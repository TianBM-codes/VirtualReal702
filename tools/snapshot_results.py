r"""
结果快照与比对：确认缓存/性能优化没有改变任何计算结果（不连服务、默认不写缓存）。

推荐用法：基准代码放在独立的 git worktree 里，主目录（正在跑服务和解析）不用切换版本：

    git worktree add ..\702-baseline precompute-opt-baseline      # 只需做一次
    python tools/snapshot_results.py --code-root ..\702-baseline --workspace E:\...\<项目> --out baseline.npz --procs 4
    python tools/snapshot_results.py --workspace E:\...\<项目> --compare baseline.npz --procs 4
    或者两份同时生成、最后直接比对文件（不重算，几秒钟）：
    python tools/snapshot_results.py --workspace E:\...\<项目> --out new.npz --procs 2
    python tools/snapshot_results.py --diff baseline.npz new.npz

--code-root：从这个目录导入计算代码（src/），工具本身用当前版本。不传则用工具所在仓库的代码。
--procs：多进程并行（每个进程各自加载一份模型索引，大模型注意内存）。
--cache：memory（默认，只用进程内缓存：同一帧同一分量的范围不重复算，不写工作区）/
         none（每项都现算，最慢）/ disk（读取全量准备写好的磁盘缓存，用于核对导入时写进缓存的内容；
         缺的条目会现算并写入 l3_cache）。缓存里存的就是计算结果本身，三种模式结果应完全相同。

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
import multiprocessing as mp
import os
import subprocess
import sys
import time

_TOOL_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _TOOL_REPO)

import numpy as np  # noqa: E402

FORMAT_VERSION = 1


def _use_code_root(code_root):
    """让 `import src...` 指向 code_root（必须在任何 src 导入之前调用）。"""
    if not code_root:
        return
    root = os.path.abspath(code_root)
    if not os.path.isfile(os.path.join(root, "src", "l3", "services", "result_service.py")):
        sys.exit(f"--code-root {root} 下没有 src/l3 代码。请先在仓库目录执行：\n"
                 f"    git worktree add {root} precompute-opt-baseline\n"
                 f"然后用 git -C {root} log -1 --oneline 确认是 fccb74d。")
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _TOOL_REPO]
    sys.path.insert(0, root)


def _configure_caches(mode):
    from src.l3.services import cache_store
    if mode == "none":
        cache_store.settings.scalar_cache_mb = 0          # 内存层与磁盘层都关
    elif mode == "memory":
        cache_store.settings.scalar_disk_cache_mb = 0     # 只关磁盘层：不写工作区
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



# ── 计算（单进程与多进程共用） ────────────────────────────────────────────────

_STATE = {}


def _init(code_root, odb_id, workspace, cache_mode):
    _use_code_root(code_root)
    _configure_caches(cache_mode)
    t0 = time.perf_counter()
    print(f"  [进程 {os.getpid()}] 加载模型索引……", flush=True)
    reg, _idx = _load(odb_id, workspace)
    print(f"  [进程 {os.getpid()}] 加载完成 {time.perf_counter() - t0:.0f} s", flush=True)
    _STATE.update(reg=reg, odb_id=odb_id)


def _run_unit(unit):
    """unit = [(序号, 任务, 是否保留数组), ...]，同一字段的任务放一起以复用缓存。"""
    out = []
    for i, task, keep in unit:
        result = run_task(_STATE["reg"], _STATE["odb_id"], tuple(task))
        digest, stats = _digest(result)
        out.append((i, digest, stats, {k: np.asarray(v) for k, v in result.items()} if keep else None))
    return out


def _units(tasks, keep_arrays):
    groups = {}
    for i, task in enumerate(tasks):
        # 同一 (结果组, 实例, step, 字段, 帧) 一组：组内复用范围/原始帧，组又足够小、进度更新及时
        groups.setdefault((task[1], task[2], task[3], task[4], task[5]), []).append((i, task, i < keep_arrays))
    return list(groups.values())


def diff_files(base_path, new_path, *, atol=0.0) -> int:
    """比对两份快照：逐项比 SHA-256；不同且两边都存了数组的项给出误差。返回退出码。"""
    def _read(path):
        with np.load(path, allow_pickle=False) as z:
            return json.loads(str(z["meta"])), {k: z[k] for k in z.files if k != "meta"}

    (mb, ab), (mn, an) = _read(base_path), _read(new_path)
    print(f"基准 {base_path}：代码 {mb.get('git', '?')}，{len(mb['tasks'])} 项，生成于 {mb['created']}")
    print(f"新版 {new_path}：代码 {mn.get('git', '?')}，{len(mn['tasks'])} 项，生成于 {mn['created']}")
    if mb["tasks"] != mn["tasks"]:
        print("两份快照的抽样组合不同（工作区、--steps/--frames/--fields 或 manifest 不一致），无法逐项比对")
        return 2
    same = equiv = diff = 0
    report = []
    for i, task in enumerate(mb["tasks"]):
        rb, rn = mb["records"][i], mn["records"][i]
        if rb["sha256"] == rn["sha256"]:
            same += 1
            continue
        prefix = f"a{i}_"
        arr_b = {k[len(prefix):]: v for k, v in ab.items() if k.startswith(prefix)}
        arr_n = {k[len(prefix):]: v for k, v in an.items() if k.startswith(prefix)}
        if arr_b and arr_n:
            ok, why = _compare_arrays(arr_b, arr_n, atol)
        else:
            ok, why = False, f"统计 {rb['stats']} → {rn['stats']}"
        if ok:
            equiv += 1
        else:
            diff += 1
            report.append(f"[不一致] {_task_key(task)}：{why}")
    print(f"\n比对完成：完全一致 {same}，数值等价 {equiv}，不一致 {diff}")
    for line in report[:200]:
        print(line)
    if len(report) > 200:
        print(f"……另有 {len(report) - 200} 项不一致")
    return 1 if diff else 0


def _git_rev(root):
    try:
        return subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "?"
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default=None, help="工作区目录（--diff 时不需要）")
    ap.add_argument("--odb-id", default=None)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", help="生成基准快照到此文件（.npz）")
    g.add_argument("--compare", help="与此基准快照比对（用当前代码重算）")
    g.add_argument("--diff", nargs=2, metavar=("BASE", "NEW"),
                   help="直接比对两份已生成的快照文件，不重算（两份可以同时生成）")
    ap.add_argument("--code-root", default=None, help="从这个目录导入计算代码（例如基准 tag 的 worktree）")
    ap.add_argument("--procs", type=int, default=1)
    ap.add_argument("--cache", choices=("memory", "none", "disk"), default="memory")
    ap.add_argument("--use-disk-cache", action="store_true", help="同 --cache disk（兼容旧参数）")
    ap.add_argument("--steps", choices=("ends", "all"), default="ends")
    ap.add_argument("--frames", choices=("ends", "all"), default="ends")
    ap.add_argument("--fields", default=None, help="逗号分隔，只抽这些字段")
    ap.add_argument("--keep-arrays", type=int, default=60)
    ap.add_argument("--atol", type=float, default=0.0,
                    help="数值等价的最大绝对误差，默认 0（只允许 -0/+0 一类的字节差异）")
    args = ap.parse_args()
    cache_mode = "disk" if args.use_disk_cache else args.cache
    if args.diff:
        sys.exit(diff_files(*args.diff, atol=args.atol))
    if not args.workspace:
        ap.error("--out / --compare 需要 --workspace")

    _use_code_root(args.code_root)
    code_root = os.path.abspath(args.code_root) if args.code_root else _TOOL_REPO
    workspace = os.path.abspath(args.workspace)
    odb_id = args.odb_id or os.path.basename(workspace.rstrip("\\/"))
    print(f"计算代码 {code_root}（{_git_rev(code_root)}），缓存模式 {cache_mode}，{args.procs} 个进程")

    if args.compare:
        with np.load(args.compare, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            stored = {name: z[name] for name in z.files if name != "meta"}
        tasks = [tuple(t) for t in meta["tasks"]]
        print(f"基准 {args.compare}：{len(tasks)} 项，生成于 {meta['created']}（代码 {meta.get('git', '?')}）")
        keep_arrays = 0
    else:
        from src.l3.infra.manifest_repo import ManifestRepo
        from src.l3.services.precompute_plan import list_targets
        _configure_caches(cache_mode)
        _reg, idx = _load(odb_id, workspace)
        fields = set(args.fields.split(",")) if args.fields else None
        tasks = build_tasks(idx, list_targets(ManifestRepo(workspace)),
                            steps=args.steps, frames=args.frames, fields=fields)
        del _reg, idx
        keep_arrays = args.keep_arrays
        print(f"共 {len(tasks)} 项，开始计算……")

    units = _units(tasks, keep_arrays)
    results = [None] * len(tasks)
    t0 = time.perf_counter()
    last = [t0]
    done = [0]

    def _heartbeat(force=False):
        now = time.perf_counter()
        if not force and now - last[0] < 30:
            return
        last[0] = now
        el = now - t0
        eta = f"，预计还需 {el / done[0] * (len(tasks) - done[0]) / 60:.1f} 分钟" if done[0] else ""
        print(f"  {done[0]}/{len(tasks)}，已用 {el / 60:.1f} 分钟{eta}", flush=True)

    def _collect(chunk):
        for i, digest, stats, arrays in chunk:
            results[i] = (digest, stats, arrays)
        done[0] += len(chunk)
        _heartbeat()

    init_args = (args.code_root, odb_id, workspace, cache_mode)
    if args.procs <= 1:
        _init(*init_args)
        for unit in units:
            _collect(_run_unit(unit))
    else:
        units.sort(key=len, reverse=True)
        with mp.get_context("spawn").Pool(args.procs, initializer=_init, initargs=init_args) as pool:
            it = pool.imap_unordered(_run_unit, units)
            remaining = len(units)
            while remaining:
                try:
                    chunk = it.next(timeout=30)
                except mp.TimeoutError:
                    _heartbeat(force=True)          # 没有新结果也每 30 秒报一次，表明还在算
                    continue
                remaining -= 1
                _collect(chunk)

    if args.compare:
        same = equiv = diff = 0
        report = []
        for i, task in enumerate(tasks):
            digest, stats, _ = results[i]
            old = meta["records"][i]
            if old["sha256"] == digest:
                same += 1
                continue
            prefix = f"a{i}_"
            old_arrays = {k[len(prefix):]: v for k, v in stored.items() if k.startswith(prefix)}
            if old_arrays:
                # 需要完整数组才能算误差：单独重算这一项
                if not _STATE:
                    _init(*init_args)
                ok, why = _compare_arrays(old_arrays, run_task(_STATE["reg"], odb_id, task), args.atol)
            else:
                ok, why = False, f"统计 {old['stats']} → {stats}"
            if ok:
                equiv += 1
            else:
                diff += 1
                report.append(f"[不一致] {_task_key(task)}：{why}")
        print(f"\n比对完成（{(time.perf_counter() - t0) / 60:.1f} 分钟）：完全一致 {same}，"
              f"数值等价 {equiv}，不一致 {diff}")
        for line in report[:200]:
            print(line)
        if len(report) > 200:
            print(f"……另有 {len(report) - 200} 项不一致")
        sys.exit(1 if diff else 0)

    records = [{"sha256": d, "stats": st} for d, st, _a in results]
    arrays_out = {}
    for i, (_d, _st, arrays) in enumerate(results):
        if arrays:
            for name, a in arrays.items():
                arrays_out[f"a{i}_{name}"] = a
    meta = {"format": FORMAT_VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "workspace": workspace, "git": _git_rev(code_root), "code_root": code_root,
            "cache_mode": cache_mode, "tasks": tasks, "records": records}
    np.savez_compressed(args.out, meta=np.array(json.dumps(meta, ensure_ascii=False)), **arrays_out)
    n_err = sum(1 for r in records if "error" in r["stats"])
    print(f"\n已写入 {os.path.abspath(args.out)}：{len(tasks)} 项（其中 {n_err} 项是报错结果），"
          f"用时 {(time.perf_counter() - t0) / 60:.1f} 分钟")


if __name__ == "__main__":
    main()
