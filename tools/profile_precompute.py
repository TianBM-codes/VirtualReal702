"""
全量预计算耗时剖析（只读，不连服务、不写缓存）。

用途：在开发者本机直接对一个已 ready 的工作区测量
  1. 单项计算的时间花在哪（读 HDF5 / 排序分组 / reduceat 求平均 / 其他），
  2. 同一帧多个分量“各读一次”与“一次读完”的差别，
  3. 多进程并行的加速比、每个进程的加载耗时与内存峰值，
为 docs/l3/L3-Full-Precompute-Cache-Proposal.md 的优化顺序提供实测依据。

用法（在仓库根目录执行）：
    python tools/profile_precompute.py --workspace E:\\...\\model_v2\\202610211132
    可选：--odb-id ID  --instance NAME  --en-field S  --nodal-field U  --frame N
          --procs 1,2,4,8  --tasks 16  --skip-parallel  --out profile_report.txt

注意：
  - 关闭全部结果缓存（内存 + 磁盘），不会往 <workspace>/l3_cache 写任何东西；
  - 会占满所测的 CPU 核，解析任务进行中时并行数据会偏低，最好在空闲时跑；
  - 报告打印到屏幕并写入 --out（默认当前目录 profile_report.txt），把它整个发给 Claude。
"""
import argparse
import cProfile
import io
import multiprocessing as mp
import os
import platform
import pstats
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import h5py  # noqa: E402
import numpy as np  # noqa: E402

try:
    import psutil  # noqa: E402
except ImportError:  # 没装 psutil 时只是不报内存
    psutil = None


# ── 公共：加载模型、关闭缓存 ────────────────────────────────────────────────

def _disable_caches():
    from src.l3.services import cache_store
    cache_store.settings.scalar_cache_mb = 0       # 同时关闭内存层与磁盘层
    cache_store.clear_memory()


def _load(odb_id, workspace):
    from src.l3.core.state import ModelIndex, OdbRegistry
    t0 = time.perf_counter()
    idx = ModelIndex(odb_id, workspace)
    idx.load_l2_render_data()
    reg = OdbRegistry(max_loaded=1)
    reg.loaded[odb_id] = idx
    return reg, idx, time.perf_counter() - t0


def _rss_mb():
    if psutil is None:
        return None
    mi = psutil.Process().memory_info()
    peak = getattr(mi, "peak_wset", None) or getattr(mi, "rss", 0)   # Windows 有 peak_wset
    return round(peak / 2**20)


def _run_task(reg, odb_id, task):
    from src.l3.services.result_service import compute_scalar_range, frame_colors, frame_scalars
    kind, rg, inst, step, field, frame, comp, mode = task
    if kind == "scalars":
        frame_scalars(reg, odb_id, inst, step, field, frame, component_idx=comp,
                      render_mode=mode, result_group=rg)
    elif kind == "range":
        compute_scalar_range(reg, odb_id, inst, step, field, frame, component_idx=comp,
                             result_group=rg)
    elif kind == "colors":
        frame_colors(reg, odb_id, inst, step, field, frame, component=comp,
                     render_mode=mode, result_group=rg)


# ── 多进程 worker（spawn 需要模块级函数） ───────────────────────────────────

def _worker(args):
    odb_id, workspace, tasks = args
    _disable_caches()
    reg, _idx, load_s = _load(odb_id, workspace)
    t0 = time.perf_counter()
    for task in tasks:
        _run_task(reg, odb_id, task)
    return load_s, time.perf_counter() - t0, _rss_mb()


# ── 报告 ───────────────────────────────────────────────────────────────────

class Report:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        print(text, flush=True)
        self.lines.append(text)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")


def _categorize(stats: pstats.Stats):
    cats = {"读 HDF5 (h5py)": 0.0, "排序 argsort/sort/unique": 0.0,
            "reduceat 分组统计": 0.0, "其他": 0.0}
    for (filename, _line, name), (_cc, _nc, tt, _ct, _callers) in stats.stats.items():
        text = f"{filename} {name}"
        if "h5py" in text:
            cats["读 HDF5 (h5py)"] += tt
        elif "argsort" in name or "'sort'" in name or "unique" in name or "lexsort" in name:
            cats["排序 argsort/sort/unique"] += tt
        elif "reduceat" in name:
            cats["reduceat 分组统计"] += tt
        else:
            cats["其他"] += tt
    return cats


def _timed(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def _en_datasets(h5_path, instance):
    """该实例 ELEMENT_NODAL 各单元类型块的 dataset 路径（实体 data / 壳 sp1）。"""
    out = []
    with h5py.File(h5_path, "r") as f:
        root = f.get(f"/ELEMENT_NODAL/{instance}")
        if root is None:
            return out
        for etype, grp in root.items():
            if "data" in grp:
                out.append(f"/ELEMENT_NODAL/{instance}/{etype}/data")
            else:
                sp = sorted(k for k in grp.keys() if k.startswith("sp"))
                if sp:
                    key = "sp1" if "sp1" in grp else sp[0]
                    out.append(f"/ELEMENT_NODAL/{instance}/{etype}/{key}/data")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--odb-id", default=None, help="默认取工作区目录名")
    ap.add_argument("--instance", default=None, help="默认取表面三角形最多的实例")
    ap.add_argument("--en-field", default="S")
    ap.add_argument("--nodal-field", default="U")
    ap.add_argument("--frame", type=int, default=None, help="默认取该 step 的中间帧")
    ap.add_argument("--procs", default="1,2,4,8")
    ap.add_argument("--tasks", type=int, default=16, help="并行测试的任务数（单元量 smooth）")
    ap.add_argument("--skip-parallel", action="store_true")
    ap.add_argument("--out", default="profile_report.txt")
    args = ap.parse_args()

    workspace = os.path.abspath(args.workspace)
    odb_id = args.odb_id or os.path.basename(workspace.rstrip("\\/"))
    rep = Report()

    from src.l3.infra.manifest_repo import ManifestRepo
    from src.l3.services.precompute_plan import list_targets

    rep("# 全量预计算耗时剖析")
    rep(f"时间 {time.strftime('%Y-%m-%d %H:%M:%S')}  工作区 {workspace}")
    rep(f"Python {platform.python_version()}  numpy {np.__version__}  h5py {h5py.__version__}  "
        f"系统 {platform.platform()}")
    rep(f"CPU 逻辑核 {os.cpu_count()}" + (
        f"  物理核 {psutil.cpu_count(logical=False)}  内存 {psutil.virtual_memory().total / 2**30:.1f} GB"
        f"  可用 {psutil.virtual_memory().available / 2**30:.1f} GB"
        f"  启动时 CPU 占用 {psutil.cpu_percent(interval=1.0):.0f}%" if psutil else "  （未安装 psutil，不报内存）"))
    rep()

    # ── 0. 加载 ──
    _disable_caches()
    reg, idx, load_s = _load(odb_id, workspace)
    rep(f"## 0. 加载模型索引：{load_s:.1f} s，进程内存峰值 {_rss_mb()} MB")

    rows = getattr(idx, "render_source_elem_row", {}) or {}
    inst = args.instance or max(rows, key=lambda k: len(rows[k]))
    rep(f"实例 {inst}：表面三角形 {len(rows.get(inst, ()))}，渲染顶点 {len(idx.vtx_node_row.get(inst, ())) if hasattr(idx, 'vtx_node_row') else '?'}")

    targets = list_targets(ManifestRepo(workspace))
    by_field = {}
    for t in targets:
        if inst in t.instances:
            by_field.setdefault(t.field, t)
    missing = [f for f in (args.en_field, args.nodal_field) if f not in by_field]
    if missing:
        rep(f"找不到字段 {missing}（该实例可用：{sorted(by_field)}），请用 --en-field/--nodal-field 指定")
        rep.save(args.out)
        return
    en_t, nd_t = by_field[args.en_field], by_field[args.nodal_field]
    frame = args.frame if args.frame is not None else en_t.frames[len(en_t.frames) // 2]
    rep(f"单元量 {en_t.field}（{en_t.step}，{en_t.n_components} 个分量），节点量 {nd_t.field}，第 {frame} 帧"
        f"，结果组 {en_t.result_group}")
    rep()

    # ── 1. 单项耗时 ──
    rep("## 1. 单项耗时（缓存关闭，每项都是现算）")

    def run(kind, t, comp, mode):
        return _timed(lambda: _run_task(reg, odb_id, (kind, t.result_group, inst, t.step, t.field,
                                                       frame, comp, mode)))

    cold = run("scalars", en_t, None, "smooth")
    rep(f"首次单元量 smooth（含一次性的平均区域计算）：{cold:.2f} s")

    rows_out = []
    prof = cProfile.Profile()
    en_smooth_total = 0.0
    for t, label in ((en_t, "单元量"), (nd_t, "节点量")):
        for comp in t.component_options:
            for mode in ("smooth", "flat"):
                if t is en_t and mode == "smooth":
                    prof.enable()
                    dt = run("scalars", t, comp, mode)
                    prof.disable()
                    en_smooth_total += dt
                else:
                    dt = run("scalars", t, comp, mode)
                rows_out.append((label, "frame-scalars", comp, mode, dt))
            rows_out.append((label, "frame-scalar-range", comp, "-", run("range", t, comp, "smooth")))
        for comp in ("U1", "U2", "U3"):
            for mode in ("smooth", "flat"):
                rows_out.append((label, "frame-colors", comp, mode, run("colors", t, comp, mode)))

    rep("| 类型 | 接口 | 分量 | 模式 | 耗时 s |")
    rep("|---|---|---|---|---|")
    for label, api, comp, mode, dt in rows_out:
        rep(f"| {label} | {api} | {'默认' if comp is None else comp} | {mode} | {dt:.2f} |")
    sums = {}
    for label, api, _c, mode, dt in rows_out:
        sums[(label, api, mode)] = sums.get((label, api, mode), 0.0) + dt
    rep()
    rep("按类型汇总（这一帧）：")
    for (label, api, mode), dt in sorted(sums.items()):
        rep(f"- {label} {api} {mode}: {dt:.2f} s")
    rep()

    # ── 2. 单元量 smooth 的时间分布 ──
    stats = pstats.Stats(prof)
    cats = _categorize(stats)
    rep(f"## 2. 单元量 smooth 时间分布（{len(en_t.component_options)} 次调用，合计 {en_smooth_total:.2f} s）")
    for name, tt in cats.items():
        share = tt / en_smooth_total * 100 if en_smooth_total else 0
        rep(f"- {name}: {tt:.2f} s（{share:.0f}%）")
    buf = io.StringIO()
    pstats.Stats(prof, stream=buf).sort_stats("tottime").print_stats(25)
    rep()
    rep("耗时最多的 25 个函数（tottime = 函数自身耗时）：")
    rep("```")
    rep(buf.getvalue().strip())
    rep("```")
    rep()

    # ── 3. 读取方式对比 ──
    rep("## 3. 读取方式对比（单元量结果文件）")
    path = en_t.file_path if os.path.isabs(en_t.file_path or "") else os.path.join(workspace, en_t.file_path or "")
    ds_paths = _en_datasets(path, inst)
    if not ds_paths:
        rep("该实例没有 ELEMENT_NODAL 块，跳过")
    else:
        n_opt = len(en_t.component_options)
        with h5py.File(path, "r") as f:
            nbytes_frame = sum(f[p][frame].nbytes for p in ds_paths)
            nbytes_all = sum(f[p].size * f[p].dtype.itemsize for p in ds_paths)
            once = _timed(lambda: [f[p][frame] for p in ds_paths])
            per = _timed(lambda: [[f[p][frame] for p in ds_paths] for _ in range(n_opt)])
            rep(f"- 单帧数据 {nbytes_frame / 2**20:.0f} MB；读 1 次 {once:.2f} s，按分量读 {n_opt} 次 {per:.2f} s")
            if nbytes_all <= 1.5 * 2**30:
                allf = _timed(lambda: [f[p][:] for p in ds_paths])
                nf = f[ds_paths[0]].shape[0]
                rep(f"- 全部 {nf} 帧 {nbytes_all / 2**20:.0f} MB 一次读完 {allf:.2f} s（逐帧读约 {once * nf:.2f} s）")
            else:
                rep(f"- 全部帧 {nbytes_all / 2**30:.1f} GB，超过 1.5 GB 不做整读测试")
    rep()

    # ── 4. 多进程 ──
    if args.skip_parallel:
        rep("## 4. 多进程：已跳过（--skip-parallel）")
    else:
        procs = [int(p) for p in args.procs.split(",") if p.strip()]
        procs = [p for p in procs if p <= (os.cpu_count() or 1)]
        tasks = []
        for fr in en_t.frames:
            for comp in en_t.component_options:
                tasks.append(("scalars", en_t.result_group, inst, en_t.step, en_t.field, fr, comp, "smooth"))
        tasks = tasks[:args.tasks]
        rep(f"## 4. 多进程（{len(tasks)} 个单元量 smooth 任务，每个进程自己加载索引）")
        rep("| 进程数 | 总耗时 s | 计算阶段 s（最慢进程） | 加载 s（最慢） | 吞吐 项/s | 单进程内存峰值 MB |")
        rep("|---|---|---|---|---|---|")
        ctx = mp.get_context("spawn")
        base = None
        for n in procs:
            chunks = [tasks[i::n] for i in range(n)]
            t0 = time.perf_counter()
            with ctx.Pool(n) as pool:
                res = pool.map(_worker, [(odb_id, workspace, c) for c in chunks])
            wall = time.perf_counter() - t0
            comp_s = max(r[1] for r in res)
            load_max = max(r[0] for r in res)
            mem = max((r[2] or 0) for r in res) or "?"
            thr = len(tasks) / comp_s if comp_s else 0
            base = base or thr
            rep(f"| {n} | {wall:.1f} | {comp_s:.1f} | {load_max:.1f} | {thr:.2f}（×{thr / base:.1f}） | {mem} |")
    rep()
    rep.save(args.out)
    print(f"\n报告已写入 {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
