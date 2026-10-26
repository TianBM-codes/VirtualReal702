#!/usr/bin/env python
r"""
接口缓存覆盖探测：对运行中的服务，把一个项目的只读 GET 接口逐个请求两遍，
按服务端耗时判断哪些已预算好、哪些首次要现算、哪些每次都现算（没进缓存）。

路由清单来自服务的 /openapi.json（新接口自动纳入），参数取值全部通过服务自己的
接口发现（结果组、step、帧、实例、字段、分量、着色方案、区域），不读工作区文件。
只发 GET；POST/DELETE/PATCH 与日志、健康检查等列为"未探测"并注明原因。
默认只探测 viewer 实际调用的 GET 接口（VIEWER_ROUTES，2026-10-22 起只保证这些接口的缓存），
加 --all-routes 探测全部项目数据接口。deformed-positions / modal-animation 与前端一样带建议 scale
（modal-animation 另带 n_frames=20，且只测 FREQUENCY 步）。

用法（在服务所在机器上跑，避免网络时间干扰；建议先重启服务，让第一遍反映磁盘缓存状态）：
    python tools/probe_endpoints.py --project 2026102201
    python tools/probe_endpoints.py --project 2026102201 --full          # 结果类接口穷举全部组合
    python tools/probe_endpoints.py --base http://127.0.0.1:5000 --project X --threshold-ms 100
    python tools/probe_endpoints.py --project X --interval-ms 200     # 放慢，减少对在用服务的影响

始终单连接串行（上一个返回才发下一个），每两个请求之间默认停 50 ms；间隔不计入耗时。

输出：控制台汇总 + <out>.md（按接口汇总、未探测清单）+ <out>.csv（每个请求明细）。
分类（服务端耗时取响应头 X-Elapsed-Time-Ms，没有时用客户端总耗时）：
    ERROR          HTTP >= 400，或 JSON 包体 code >= 400
    NOT_CACHED     第二次仍超过阈值：每次都现算
    NOT_PRECOMPUTED 第一次超过阈值、第二次在阈值内：没预算，首次请求后才缓存
    OK             两次都在阈值内
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict, defaultdict

# 不探测的路由前缀（非数据查看类，或实时状态）
SKIP_PREFIXES = (
    ("/api/health", "健康检查"),
    ("/api/jobs", "旧版任务接口"),
    ("/api/modal/", "独立模态接口（需 model_id，非项目数据）"),
    ("/api/projects/{project_id}/logs", "实时日志"),
)
# viewer（viewer/src）实际调用的 GET 接口；全量缓存只对这些负责
VIEWER_ROUTES = {
    "/api/projects/{project_id}",
    "/api/odb/{odb_id}/meta/overview",
    "/api/odb/{odb_id}/geometry/orientations",
    "/api/odb/{odb_id}/geometry/{instance}/render-buffers",
    "/api/odb/{odb_id}/geometry/{instance}/render-buffers-chunked",
    "/api/odb/{odb_id}/geometry/{instance}/element-mesh-edges",
    "/api/odb/{odb_id}/geometry/{instance}/feature-edges",
    "/api/odb/{odb_id}/geometry/{instance}/lines",
    "/api/odb/{odb_id}/geometry/{instance}/points",
    "/api/odb/{odb_id}/geometry/{instance}/couplings",
    "/api/odb/{odb_id}/results/frame-scalar-range",
    "/api/odb/{odb_id}/results/frame-scalars",
    "/api/odb/{odb_id}/results/deformed-positions",
    "/api/odb/{odb_id}/results/deform-suggest-scale",
    "/api/odb/{odb_id}/results/modal-shape",
    "/api/odb/{odb_id}/results/modal-animation",
    "/api/odb/{odb_id}/results/section-mesh",
    "/api/odb/{odb_id}/query/pick",
    "/api/odb/{odb_id}/query/nearest-face",
    "/api/odb/{odb_id}/color-code/{instance}/schemes",
    "/api/odb/{odb_id}/color-code/{instance}",
    "/api/odb/{odb_id}/color-code/{instance}/legend-entries",
    "/api/odb/{odb_id}/color-code/{instance}/legend",
    "/api/odb/{odb_id}/color-code/{instance}/region-mesh-edges",
    "/api/odb/{odb_id}/color-code/{instance}/region-outline",
    "/api/odb/{odb_id}/sensitivity/result_groups",
    "/api/odb/{odb_id}/sensitivity/fields",
}
MODAL_ANIMATION_FRAMES = 20     # 前端预计算模式的默认帧数（与 precompute_plan 一致）
# 结果维度在 sample 模式下取的字段：NODAL 优先 U，单元类优先 S，另取一个无分量标量和一个 WHOLE_ELEMENT
PREFERRED_FIELDS = ("U", "S")


def _get(base, path, params=None, timeout=600):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            status, headers = r.status, r.headers
    except urllib.error.HTTPError as e:
        body, status, headers = e.read(), e.code, e.headers
    except Exception as e:                       # 超时、连接断开
        return {"url": url, "status": 0, "bytes": 0, "ms": (time.perf_counter() - t0) * 1000,
                "srv_ms": None, "x_cache": "", "body_code": None, "error": str(e)[:200], "body": b""}
    ms = (time.perf_counter() - t0) * 1000
    srv = headers.get("X-Elapsed-Time-Ms")
    body_code = None
    if (headers.get("Content-Type") or "").startswith("application/json"):
        try:
            body_code = json.loads(body).get("code")
        except Exception:
            pass
    return {"url": url, "status": status, "bytes": len(body), "ms": ms,
            "srv_ms": float(srv) if srv else None, "x_cache": headers.get("X-Cache") or "",
            "body_code": body_code, "error": "", "body": body}


def _data(base, path, params=None):
    r = _get(base, path, params)
    if r["status"] != 200:
        return None
    try:
        return json.loads(r["body"]).get("data")
    except Exception:
        return None


# ── 发现参数取值 ──────────────────────────────────────────────────────────────

class Catalog:
    def __init__(self, base, project, full):
        self.base, self.project, self.full = base, project, full
        odb = f"/api/odb/{project}"
        proj = _data(base, f"/api/projects/{project}") or {}
        self.groups = OrderedDict()             # rg -> [steps]
        for g in proj.get("result_groups") or []:
            if g.get("status") == "ready":
                self.groups[g["result_group"]] = list(g.get("steps") or [])
        ov = _data(base, f"{odb}/meta/overview") or {}
        self.instances = [i["instance_name"] for i in ov.get("instances") or []]
        self.procedures = {}                    # step -> procedure（overview 不带 result_group 时不列 step）
        for rg in self.groups:
            ov_rg = _data(base, f"{odb}/meta/overview", {"result_group": rg}) or {}
            for s in ov_rg.get("steps") or []:
                self.procedures.setdefault(s.get("step_name"), (s.get("procedure") or "").upper())
        self.bbox_center = {}
        for i in ov.get("instances") or []:
            lo, hi = i.get("bbox_min"), i.get("bbox_max")
            if lo and hi:
                self.bbox_center[i["instance_name"]] = [(a + b) / 2 for a, b in zip(lo, hi)]
        self.frames = {}                        # step -> [frame_idx]
        self.fields = {}                        # (rg, step, inst) -> [field dict]
        for rg, steps in self.groups.items():
            for step in steps:
                if step not in self.frames:
                    fr = _data(base, f"{odb}/steps/{urllib.parse.quote(step, safe='')}/frames") or []
                    self.frames[step] = [f["frame_idx"] for f in fr] or [0]
                for inst in self.instances:
                    d = _data(base, f"{odb}/fields", {"instance": inst, "step": step, "result_group": rg})
                    self.fields[(rg, step, inst)] = (d or {}).get("fields") or []
        self._scales = {}
        self.schemes, self.elsets, self.regions = {}, {}, {}
        for inst in self.instances:
            d = _data(base, f"{odb}/color-code/{urllib.parse.quote(inst, safe='')}/schemes") or {}
            self.schemes[inst] = list(d.get("schemes") or [])
            self.elsets[inst] = list(d.get("elsets") or [])
            for sc in self.schemes[inst]:
                if sc == "elset":
                    continue
                lg = _data(base, f"{odb}/color-code/{urllib.parse.quote(inst, safe='')}/legend",
                           {"scheme": sc}) or {}
                self.regions[(inst, sc)] = [e["legend_key"] for e in lg.get("legend") or []]

    def suggest_scale(self, rg, step, frame):
        """与前端相同：deformed-positions / modal-animation 的 scale 取 deform-suggest-scale 返回值（原样回传）。"""
        key = (rg, step, frame)
        if key not in self._scales:
            d = _data(self.base, f"/api/odb/{self.project}/results/deform-suggest-scale",
                      {"step": step, "frame": frame, "result_group": rg}) or {}
            self._scales[key] = d.get("scale")
        return self._scales[key]

    # sample 模式的取样
    def _pick(self, seq, n_ends=True):
        seq = list(seq)
        if self.full or len(seq) <= 2:
            return seq
        return [seq[0], seq[-1]] if n_ends else seq[:1]

    def steps(self):
        out = []
        for rg, steps in self.groups.items():
            for s in self._pick(steps):
                out.append((rg, s))
        return out

    def frames_of(self, step):
        return self._pick(self.frames.get(step, [0]))

    def fields_of(self, rg, step, inst):
        fs = self.fields.get((rg, step, inst), [])
        if self.full:
            return fs
        chosen, seen = [], set()

        def add(f):
            if f and f["name"] not in seen:
                seen.add(f["name"])
                chosen.append(f)
        for name in PREFERRED_FIELDS:
            add(next((f for f in fs if f["name"] == name), None))
        add(next((f for f in fs if "NODAL" in f["positions"]), None))
        add(next((f for f in fs if "NODAL" not in f["positions"] and f["components"]), None))
        add(next((f for f in fs if not f["components"]), None))
        add(next((f for f in fs if "WHOLE_ELEMENT" in f["positions"]), None))
        return chosen

    def comps_of(self, field):
        n = len(field["components"])
        opts = [None] + list(range(n))
        return opts if self.full else opts[:2]

    def regions_of(self, inst, scheme):
        r = self.regions.get((inst, scheme), [])
        return r if self.full else r[:1]

    def elsets_of(self, inst):
        e = self.elsets.get(inst, [])
        return e if self.full else e[:1]


# ── 按路由生成请求 ────────────────────────────────────────────────────────────

def _routes(base):
    spec = json.loads(_get(base, "/openapi.json")["body"])
    out = []
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            params = [p for p in op.get("parameters", [])]
            out.append({
                "method": method.upper(), "path": path,
                "query": {p["name"]: bool(p.get("required")) for p in params if p["in"] == "query"},
                "path_params": [p["name"] for p in params if p["in"] == "path"],
                "body": "requestBody" in op,
            })
    return out


def _skip_reason(route, all_routes=False):
    path = route["path"]
    if not path.startswith("/api/"):
        return "非项目数据接口"
    if route["method"] != "GET":
        return f"{route['method']} 接口（只探测 GET）"
    for prefix, why in SKIP_PREFIXES:
        if path.startswith(prefix):
            return why
    if not (path.startswith("/api/odb/{odb_id}") or path.startswith("/api/projects/{project_id}")):
        return "非项目数据接口"
    if not all_routes and path not in VIEWER_ROUTES:
        return "viewer 不调用（--all-routes 时探测）"
    return None


def _requests_for(route, cat):
    """返回 [(path, params)]；无法填全必填参数时返回 (None, 原因)。"""
    q, pp = route["query"], set(route["path_params"])
    names = set(q) | pp
    uses_step = bool(names & {"step", "step_name"})
    uses_inst = bool(names & {"instance", "instances"})
    uses_field = "field" in names
    uses_frame = bool(names & {"frame", "frame_idx"})
    uses_scheme = "scheme" in names
    known = {"odb_id", "project_id", "instance", "instances", "step", "step_name", "frame", "frame_idx",
             "field", "component_idx", "component", "mode", "result_group", "scheme", "set_names",
             "region", "position", "x", "y", "z", "render_face_idx", "name", "scale", "n_frames"}
    missing = [n for n, req in q.items() if req and n not in known]
    if missing:
        return None, f"必填参数无法自动填：{missing}"

    out = []

    def emit(a):
        path = route["path"].replace("{odb_id}", cat.project).replace("{project_id}", cat.project)
        params = {}
        for n in pp:
            if n in ("odb_id", "project_id"):
                continue
            key = {"step_name": "step", "frame_idx": "frame"}.get(n, n)
            if key not in a:
                return
            path = path.replace("{" + n + "}", urllib.parse.quote(str(a[key]), safe=""))
        for n in q:
            key = {"instances": "instance", "frame_idx": "frame"}.get(n, n)
            if key in a and a[key] is not None:
                params[n] = a[key]
            elif q[n]:
                return                       # 必填缺值：组合不适用
        out.append((path, params))

    rg_steps = cat.steps() if uses_step else [(rg, None) for rg in cat.groups] or [(None, None)]
    if not uses_step and "result_group" not in names:
        rg_steps = [(None, None)]
    insts = cat.instances if uses_inst else [None]
    for rg, step in rg_steps:
        for inst in insts:
            base = {"result_group": rg, "step": step, "instance": inst}
            if uses_scheme:
                for sc in (cat.schemes.get(inst, []) if inst else
                           sorted({s for v in cat.schemes.values() for s in v})):
                    if sc == "elset":
                        for es in (cat.elsets_of(inst) if inst else []):
                            emit({**base, "scheme": sc, "set_names": es})
                        continue
                    if "region" in names:
                        for reg in cat.regions_of(inst, sc):
                            emit({**base, "scheme": sc, "region": reg})
                    else:
                        emit({**base, "scheme": sc})
                continue
            if "name" in names:                  # 用户场
                continue
            if {"x", "y", "z"} <= names:
                c = cat.bbox_center.get(inst)
                if c:
                    emit({**base, "x": c[0], "y": c[1], "z": c[2]})
                continue
            frames = cat.frames_of(step) if (uses_frame and step) else [None]
            fields = cat.fields_of(rg, step, inst) if (uses_field and step and inst) else [None]
            for fd in fields:
                if uses_field and fd is None:
                    continue
                for fr in frames:
                    a = {**base, "frame": fr}
                    if "render_face_idx" in names:
                        a["render_face_idx"] = 0
                    if fd is None:
                        if route["path"].endswith("/results/modal-animation") and fr is not None:
                            # viewer 只在 FREQUENCY 步显示模态动画（DeformCard.vue）
                            if cat.procedures.get(step, "FREQUENCY") != "FREQUENCY":
                                continue
                            sc = cat.suggest_scale(rg, step, fr)
                            if sc is not None:
                                a = {**a, "scale": sc, "n_frames": MODAL_ANIMATION_FRAMES}
                        elif route["path"].endswith("/results/deformed-positions") and fr is not None:
                            # 变形面板自动填入该帧建议 scale（DeformCard.vue autoFillScale）
                            sc = cat.suggest_scale(rg, step, fr)
                            if sc is not None:
                                a = {**a, "scale": sc}
                        emit(a)
                        continue
                    a["field"] = fd["name"]
                    if "position" in names:
                        for pos in (fd["positions"] if cat.full else fd["positions"][:1]):
                            emit({**a, "position": pos})
                        continue
                    if "component" in names:     # frame-colors：U1/U2/U3 字符串分量
                        comps = ["U1", "U2", "U3"] if fd["name"] == "U" else [None]
                        modes = ["smooth", "flat"] if "mode" in names else [None]
                        for c in (comps if cat.full else comps[:1]):
                            for m in modes:
                                emit({**a, "component": c, "mode": m})
                        continue
                    comps = cat.comps_of(fd) if "component_idx" in names else [None]
                    modes = ["smooth", "flat"] if "mode" in names else [None]
                    for c in comps:
                        for m in modes:
                            emit({**a, "component_idx": c, "mode": m})
    if not out:
        return None, "没有可用的参数组合"
    # 去重（不用某些维度的路由会重复）
    seen, uniq = set(), []
    for p, prm in out:
        k = (p, tuple(sorted(prm.items())))
        if k not in seen:
            seen.add(k)
            uniq.append((p, prm))
    return uniq, None


def _classify(r1, r2, thr):
    for r in (r1, r2):
        if r["status"] == 0 or r["status"] >= 400 or (r["body_code"] or 0) >= 400:
            return "ERROR"
    t1 = r1["srv_ms"] if r1["srv_ms"] is not None else r1["ms"]
    t2 = r2["srv_ms"] if r2["srv_ms"] is not None else r2["ms"]
    if t2 > thr:
        return "NOT_CACHED"
    if t1 > thr:
        return "NOT_PRECOMPUTED"
    return "OK"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:5000", help="服务地址")
    ap.add_argument("--project", required=True, help="项目 ID（odb_id）")
    ap.add_argument("--full", action="store_true", help="穷举全部 step/帧/字段/分量/区域（默认取样）")
    ap.add_argument("--threshold-ms", type=float, default=100.0, help="判定“需要现算”的服务端耗时阈值")
    ap.add_argument("--limit-per-route", type=int, default=0, help="每个接口最多请求多少组参数（0=不限）")
    ap.add_argument("--all-routes", action="store_true",
                    help="探测全部项目数据 GET 接口（默认只探测 viewer 调用的）")
    ap.add_argument("--only", default="", help="只探测路径里包含该子串的接口，如 results/")
    ap.add_argument("--out", default="", help="输出文件前缀，默认 probe_<项目>_<时间>")
    ap.add_argument("--interval-ms", type=float, default=50.0,
                    help="每两个请求之间停顿的毫秒数（默认 50，0=不停顿）")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    out = args.out or f"probe_{args.project}_{time.strftime('%Y%m%d_%H%M%S')}"

    print(f"发现参数取值（{base}，项目 {args.project}）…", flush=True)
    cat = Catalog(base, args.project, args.full)
    if not cat.groups:
        print("项目没有 ready 的结果组，或项目不存在", file=sys.stderr)
        return 1
    print(f"  结果组 {list(cat.groups)}，实例 {cat.instances}，"
          f"step {sum(len(s) for s in cat.groups.values())} 个", flush=True)

    plan, skipped = [], []
    for route in _routes(base):
        if args.only and args.only not in route["path"]:
            continue
        why = _skip_reason(route, args.all_routes)
        if why:
            skipped.append((route, why))
            continue
        reqs, why = _requests_for(route, cat)
        if reqs is None:
            skipped.append((route, why))
            continue
        if args.limit_per_route:
            reqs = reqs[:args.limit_per_route]
        plan.extend((route, p, prm) for p, prm in reqs)
    print(f"共 {len(plan)} 个请求 × 2 遍，未探测接口 {len(skipped)} 个；单连接串行，"
          f"请求间隔 {args.interval_ms:g} ms", flush=True)

    rows = []
    t_start, last = time.time(), time.time()
    pause = max(args.interval_ms, 0) / 1000.0
    for i, (route, path, prm) in enumerate(plan, 1):
        r1 = _get(base, path, prm)
        time.sleep(pause)
        r2 = _get(base, path, prm)
        time.sleep(pause)
        cls = _classify(r1, r2, args.threshold_ms)
        rows.append({
            "route": route["path"], "class": cls, "url": r1["url"].replace(base, ""),
            "status": f"{r1['status']}/{r2['status']}",
            "body_code": "" if r1["body_code"] in (None, 200) else r1["body_code"],
            "bytes": r1["bytes"],
            "srv_ms_1": None if r1["srv_ms"] is None else round(r1["srv_ms"], 1),
            "srv_ms_2": None if r2["srv_ms"] is None else round(r2["srv_ms"], 1),
            "total_ms_1": round(r1["ms"], 1), "total_ms_2": round(r2["ms"], 1),
            "x_cache": f"{r1['x_cache']}/{r2['x_cache']}".strip("/"),
            "error": r1["error"] or r2["error"],
        })
        if time.time() - last > 15:
            last = time.time()
            print(f"  {i}/{len(plan)}，已用 {(last - t_start) / 60:.1f} 分钟", flush=True)

    with open(out + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["route"])
        w.writeheader()
        w.writerows(rows)

    by_route = defaultdict(list)
    for r in rows:
        by_route[r["route"]].append(r)
    order = {"ERROR": 0, "NOT_CACHED": 1, "NOT_PRECOMPUTED": 2, "OK": 3}

    def t(v, fallback):
        return v if v is not None else fallback

    summary = []
    for route, rs in by_route.items():
        counts = defaultdict(int)
        for r in rs:
            counts[r["class"]] += 1
        worst = min(rs, key=lambda r: (order[r["class"]], -t(r["srv_ms_2"], r["total_ms_2"])))
        summary.append({
            "route": route, "n": len(rs), "counts": dict(counts),
            "worst_class": worst["class"],
            "max_ms_1": max(t(r["srv_ms_1"], r["total_ms_1"]) for r in rs),
            "max_ms_2": max(t(r["srv_ms_2"], r["total_ms_2"]) for r in rs),
            "example": worst["url"],
        })
    summary.sort(key=lambda s: (order[s["worst_class"]], -s["max_ms_2"]))

    lines = [f"# 接口缓存探测：项目 {args.project}", "",
             f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}，服务：{base}，模式：{'full' if args.full else 'sample'}，"
             f"阈值 {args.threshold_ms:g} ms，请求 {len(rows)} 个 × 2 遍，用时 {(time.time() - t_start) / 60:.1f} 分钟",
             "- 分类：ERROR 出错；NOT_CACHED 第二次仍慢（没进缓存）；NOT_PRECOMPUTED 第一次慢第二次快（没预算）；OK 两次都快",
             "- 耗时为服务端 X-Elapsed-Time-Ms（毫秒），第一次 / 第二次的最大值", "",
             "| 接口 | 最差分类 | 请求数 | 各类数量 | 首次最大 | 再次最大 | 例子 |",
             "|---|---|---|---|---|---|---|"]
    for s in summary:
        cnt = ", ".join(f"{k} {v}" for k, v in sorted(s["counts"].items(), key=lambda kv: order[kv[0]]))
        lines.append(f"| `{s['route']}` | {s['worst_class']} | {s['n']} | {cnt} | {s['max_ms_1']:.0f} | "
                     f"{s['max_ms_2']:.0f} | `{s['example']}` |")
    other = [r for r, why in skipped if why == "非项目数据接口"]
    lines += ["", "## 未探测的接口", "",
              f"另有 {len(other)} 个非项目数据接口（模型修正、求解、导入等，不在 /api/odb、/api/projects 下）未列出。", "",
              "| 方法 | 接口 | 原因 |", "|---|---|---|"]
    for route, why in skipped:
        if why != "非项目数据接口":
            lines.append(f"| {route['method']} | `{route['path']}` | {why} |")
    errors = [r for r in rows if r["class"] == "ERROR"]
    if errors:
        lines += ["", "## 出错的请求（前 50 个）", "", "| 状态 | 包体 code | 请求 | 错误 |", "|---|---|---|---|"]
        for r in errors[:50]:
            lines.append(f"| {r['status']} | {r['body_code']} | `{r['url']}` | {r['error']} |")
    with open(out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print()
    for s in summary:
        print(f"{s['worst_class']:<16} {s['route']:<62} n={s['n']:<5} "
              f"首次≤{s['max_ms_1']:.0f}ms 再次≤{s['max_ms_2']:.0f}ms")
    print(f"\n未探测 {len(skipped)} 个接口，详见 {os.path.abspath(out + '.md')}")
    print(f"每个请求明细：{os.path.abspath(out + '.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
