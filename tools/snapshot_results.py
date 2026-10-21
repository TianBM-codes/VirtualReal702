"""
结果快照与比对：确认缓存/性能优化没有改变任何计算结果（只读，不连服务、不写缓存）。

先用优化前的代码生成基准快照，优化后用同一批组合重算并逐项比对：

    git checkout precompute-opt-baseline          # 优化前的代码
    python tools/snapshot_results.py --workspace E:\\...\\202610211132 --out baseline.npz
    git checkout main                             # 优化后的代码
    python tools/snapshot_results.py --workspace E:\\...\\202610211132 --compare baseline.npz

抽样范围（默认）：每个结果组、每个字段、第一个和最后一个 step、各 step 的第 0 帧 /
中间帧 / 最后一帧、所有实例，覆盖
  - frame-scalars：默认分量 + 每个实际分量 × smooth/flat（归一化后的顶点值 + 图例范围）；
  - frame-scalar-range：默认分量 + 每个实际分量；
  - frame-colors：U1/U2/U3 × smooth/flat（顶点颜色 + 图例范围）；
  - U 场：顶点位移向量与 deform-suggest-scale 统计。
可用 --steps all、--frames all 扩大，--fields S,U 缩小。

每项记录 SHA-256（逐字节一致性）和数值统计（NaN 个数、最小、最大、和）；
前 --keep-arrays 项（默认 60）另存完整数组，比对不一致时能给出最大绝对误差。
比对结果分三类：完全一致 / 数值等价（字节不同但 NaN 位置一致、最大误差 ≤ --atol）/ 不一致。
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np  # noqa: E402

FORMAT_VERSION = 1


def _disable_caches():
    from src.l3.services import cache_store
    cache_store.settings.scalar_cache_mb = 0       # 同时关闭内存层与磁盘层：每项都现算
    cache_store.clear_memory()


def _load(odb_id, workspace):
    from src.l3.core.state import ModelIndex, OdbRegistry
    idx = ModelIndex(odb_id, workspace)
    idx.load_l2_render_data()
    reg = OdbRegistry(max_loaded=1)
    reg.loaded[odb_id] = idx
    return reg, idx


def _pick(values, how):
    values = tuple(values)
    if how == "all" or len(values) <= 3:
        return values
    return tuple(dict.fromkeys((values[0], values[len(values) // 2], values[-1])))


def build_tasks(idx, targets, *, steps="ends", frames="ends", fields=None):
    rows = getattr(idx, "render_source_elem_row", {}) or {}
    step_order = []
    for t in targets:
        if t.step not in step_order:
            step_order.append(t.step)
    keep_steps = set(step_order if steps == "all" else _pick(step_order, "ends")[:1] + _pick(step_order, "ends")[-1:])
    tasks = []
    for t in targets:
        if t.step not in keep_steps or (fields and t.field not in fields):
            continue
        for inst in t.instances:
            surface = inst not in rows or len(rows[inst]) > 0
            for fr in _pick(t.frames, frames):
                base = (t.result_group, inst, t.step, t.field, int(fr))
                if surface:
                    for comp in t.component_options:
                        for mode in ("smooth", "flat"):
                            tasks.append(("scalars",) + base + (comp, mode))
                        tasks.append(("range",) + base + (comp, None))
                    for comp in ("U1", "U2", "U3"):
                        for mode in ("smooth", "flat"):
                            tasks.append(("colors",) + base + (comp, mode))
                if t.field == "U":
                    tasks.append(("displacement",) + base + (None, None))
        if t.field == "U":
            for fr in _pick(t.frames, frames):
                tasks.append(("deform_stats", t.result_group, None, t.step, "U", int(fr), None, None))
    return tasks


def run_task(reg, odb_id, task):
    """返回 {名字: ndarray} 或 {"error": 文本}。异常本身也是结果的一部分（优化后应一致）。"""
    from src.l3.services import result_service as rs
    kind, rg, inst, step, field, frame, comp, mode = task
    try:
        if kind == "scalars":
            u, legend, pos = rs.frame_scalars(reg, odb_id, inst, step, field, frame,
                                              component_idx=comp, render_mode=mode, result_group=rg)
            return {"u": np.asarray(u), "legend": np.asarray(legend), "position": np.array(str(pos))}
        if kind == "range":
            r = rs.compute_scalar_range(reg, odb_id, inst, step, field, frame,
                                        component_idx=comp, result_group=rg)
            return {"range": np.asarray(r if r is not None else [np.nan, np.nan], dtype=np.float64),
                    "is_none": np.array(r is None)}
        if kind == "colors":
            c, legend = rs.frame_colors(reg, odb_id, inst, step, field, frame,
                                        component=comp, render_mode=mode, result_group=rg)
            return {"colors": np.asarray(c), "legend": np.asarray(legend)}
        if kind == "displacement":
            return {"disp": np.asarray(rs.frame_vertex_displacements(reg, odb_id, inst, step, frame,
                                                                     result_group=rg))}
        if kind == "deform_stats":
            s = rs.deform_scale_stats(reg, odb_id, step, frame, rg)
            return {"stats": np.array(json.dumps(s, sort_keys=True, default=str))}
    except Exception as exc:
        return {"error": np.array(f"{type(exc).__name__}: {exc}")}
    raise ValueError(kind)


def _digest(arrays):
    h = hashlib.sha256()
    stats = {}
    for name in sorted(arrays):
        a = np.ascontiguousarray(arrays[name])
        h.update(name.encode())
        h.update(str(a.dtype).encode())
        h.update(str(a.shape).encode())
        h.update(a.tobytes() if a.dtype.kind != "U" else str(a).encode())
        if a.dtype.kind == "f" and a.size:
            fin = a[np.isfinite(a)]
            stats[name] = [int(a.size - fin.size),
                           float(fin.min()) if fin.size else None,
                           float(fin.max()) if fin.size else None,
                           float(fin.astype(np.float64).sum()) if fin.size else None]
        elif a.dtype.kind in "U":
            stats[name] = str(a)
        elif a.size:
            stats[name] = [int(a.size), int(a.min()), int(a.max()), int(a.astype(np.int64).sum())]
    return h.hexdigest(), stats


def _task_key(task):
    return json.dumps(task, ensure_ascii=False)


def _compare_arrays(old, new, atol):
    """字节不同的两组数组：NaN 位置一致且最大绝对误差 ≤ atol → 数值等价。"""
    worst = 0.0
    for name in old:
        if name not in new:
            return False, f"缺少 {name}"
        a, b = np.asarray(old[name]), np.asarray(new[name])
        if a.shape != b.shape:
            return False, f"{name} 形状 {a.shape} → {b.shape}"
        if a.dtype.kind in "fiu" and b.dtype.kind in "fiu":
            na, nb = np.isnan(a) if a.dtype.kind == "f" else None, np.isnan(b) if b.dtype.kind == "f" else None
            if na is not None and nb is not None and not np.array_equal(na, nb):
                return False, f"{name} NaN 位置不同（{int(na.sum())} → {int(nb.sum())}）"
            fa = a[~na] if na is not None else a
            fb = b[~nb] if nb is not None else b
            if fa.size:
                worst = max(worst, float(np.max(np.abs(fa.astype(np.float64) - fb.astype(np.float64)))))
        elif not np.array_equal(a, b):
            return False, f"{name}: {a} → {b}"
    return worst <= atol, f"最大绝对误差 {worst:.3g}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--odb-id", default=None)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", help="生成基准快照到此文件（.npz）")
    g.add_argument("--compare", help="与此基准快照比对")
    ap.add_argument("--steps", choices=("ends", "all"), default="ends")
    ap.add_argument("--frames", choices=("ends", "all"), default="ends")
    ap.add_argument("--fields", default=None, help="逗号分隔，只抽这些字段")
    ap.add_argument("--keep-arrays", type=int, default=60)
    ap.add_argument("--atol", type=float, default=0.0,
                    help="数值等价的最大绝对误差，默认 0（只允许 -0/+0 一类的字节差异）")
    args = ap.parse_args()

    workspace = os.path.abspath(args.workspace)
    odb_id = args.odb_id or os.path.basename(workspace.rstrip("\\/"))

    from src.l3.infra.manifest_repo import ManifestRepo
    from src.l3.services.precompute_plan import list_targets

    _disable_caches()
    reg, idx = _load(odb_id, workspace)

    if args.compare:
        with np.load(args.compare, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            stored = {name: z[name] for name in z.files if name != "meta"}
        tasks = [tuple(t) for t in meta["tasks"]]
        print(f"基准 {args.compare}：{len(tasks)} 项，生成于 {meta['created']}（代码 {meta.get('git', '?')}）")
    else:
        fields = set(args.fields.split(",")) if args.fields else None
        tasks = build_tasks(idx, list_targets(ManifestRepo(workspace)),
                            steps=args.steps, frames=args.frames, fields=fields)
        print(f"共 {len(tasks)} 项，开始计算……")

    records = []
    arrays_out = {}
    same = equiv = diff = 0
    report = []
    t0 = time.perf_counter()
    last = t0
    for i, task in enumerate(tasks):
        result = run_task(reg, odb_id, task)
        digest, stats = _digest(result)
        if args.compare:
            old = meta["records"][i]
            if old["sha256"] == digest:
                same += 1
            else:
                prefix = f"a{i}_"
                old_arrays = {k[len(prefix):]: v for k, v in stored.items() if k.startswith(prefix)}
                if old_arrays:
                    ok, why = _compare_arrays(old_arrays, result, args.atol)
                else:
                    ok, why = False, f"统计 {old['stats']} → {stats}"
                if ok:
                    equiv += 1
                else:
                    diff += 1
                    report.append(f"[不一致] {_task_key(task)}：{why}")
        else:
            records.append({"sha256": digest, "stats": stats})
            if i < args.keep_arrays:
                for name, a in result.items():
                    arrays_out[f"a{i}_{name}"] = a
        now = time.perf_counter()
        if now - last > 30:
            last = now
            print(f"  {i + 1}/{len(tasks)}，已用 {(now - t0) / 60:.1f} 分钟", flush=True)

    if args.compare:
        print(f"\n比对完成（{(time.perf_counter() - t0) / 60:.1f} 分钟）：完全一致 {same}，"
              f"数值等价 {equiv}，不一致 {diff}")
        for line in report[:200]:
            print(line)
        if len(report) > 200:
            print(f"……另有 {len(report) - 200} 项不一致")
        sys.exit(1 if diff else 0)

    try:
        import subprocess
        git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=os.path.dirname(__file__)).stdout.strip()
    except Exception:
        git = "?"
    meta = {"format": FORMAT_VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "workspace": workspace, "git": git, "tasks": tasks, "records": records}
    np.savez_compressed(args.out, meta=np.array(json.dumps(meta, ensure_ascii=False)), **arrays_out)
    n_err = sum(1 for r in records if "error" in r["stats"])
    print(f"\n已写入 {os.path.abspath(args.out)}：{len(tasks)} 项（其中 {n_err} 项是报错结果），"
          f"用时 {(time.perf_counter() - t0) / 60:.1f} 分钟")


if __name__ == "__main__":
    main()
