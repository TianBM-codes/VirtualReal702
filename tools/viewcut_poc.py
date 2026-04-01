"""
View Cut Phase 2 — POC 性能评估脚本
====================================

测量目标：评估 L3 全量扫描体单元并与切面求交的耗时，
决定是否需要在 L2 预建体单元空间索引。

用法
----
# 仅用合成数据测试（无需 ODB）：
python tools/viewcut_poc.py

# 指定规模（元素数量）：
python tools/viewcut_poc.py --sizes 100000 500000 1000000

# 用真实 L1 workspace 数据测试（需已完成 L1 pack）：
python tools/viewcut_poc.py --workspace /data/<odb_id> --instance PART-1-1

被测量的各步骤
--------------
① 读取 / 生成节点坐标 + 单元连接表（I/O 或 alloc 耗时）
② 计算各节点到切面的有向距离          ← 向量化，应很快
③ 筛选与切面相交的单元（符号变化检测）  ← 向量化
④ 逐单元计算截面多边形（含插值求交点）  ← Python 循环，最可能成为瓶颈
⑤ 三角剖分截面多边形 + 构造渲染 buffer
⑥ 附加：体单元 Octree 粗筛的加速效果（可选，依赖 scipy）

输出
----
各步骤耗时（毫秒）、命中单元数、截面三角形数，
以及"是否建议加 L2 预处理索引"的结论。
"""

import argparse
import time
import sys
import os
import textwrap

# Ensure UTF-8 output on Windows (Chinese chars + em-dash would otherwise crash stdout)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np

try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

try:
    from scipy.spatial import KDTree  # noqa: F401 — only used for Octree demo
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# ─── 合成数据生成 ──────────────────────────────────────────────────────────────

def gen_hex_mesh(n_elems: int, seed: int = 42):
    """
    生成一个均匀结构化六面体网格（C3D8），用作合成压力测试数据。

    返回：
      coords  : [N_nodes, 3] float32 — 节点坐标，分布在 [0, 1]³ 内
      conn    : [n_elems, 8] int32   — 每行 8 个节点行索引（六面体）
    """
    rng = np.random.default_rng(seed)

    # 估算边长以凑够元素数
    side = max(2, int(round(n_elems ** (1 / 3))) + 1)
    nx, ny, nz = side, side, max(2, n_elems // (side * side) + 1)

    # 节点格点
    xs = np.linspace(0.0, 1.0, nx + 1, dtype=np.float32)
    ys = np.linspace(0.0, 1.0, ny + 1, dtype=np.float32)
    zs = np.linspace(0.0, 1.0, nz + 1, dtype=np.float32)
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing='ij')
    coords = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1).astype(np.float32)

    # 添加少量随机扰动（让切面命中率更真实）
    coords += rng.uniform(-0.4 / max(nx, ny, nz),
                           0.4 / max(nx, ny, nz),
                           coords.shape).astype(np.float32)

    # 构造连接表
    def idx(i, j, k):
        return i * (ny + 1) * (nz + 1) + j * (nz + 1) + k

    elems = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                elems.append([
                    idx(i,   j,   k),   idx(i+1, j,   k),
                    idx(i+1, j+1, k),   idx(i,   j+1, k),
                    idx(i,   j,   k+1), idx(i+1, j,   k+1),
                    idx(i+1, j+1, k+1), idx(i,   j+1, k+1),
                ])
                if len(elems) >= n_elems:
                    break
            if len(elems) >= n_elems:
                break
        if len(elems) >= n_elems:
            break

    conn = np.array(elems, dtype=np.int32)
    return coords, conn


def gen_tet_mesh(n_elems: int, seed: int = 42):
    """
    生成 C3D4 四面体网格（每个六面体拆成 5 个四面体）。
    """
    # 先生成六面体再拆
    n_hex = max(1, n_elems // 5)
    coords, hex_conn = gen_hex_mesh(n_hex, seed)

    # 每个六面体拆成 5 个四面体（固定拆法）
    tet_splits = [
        [0, 1, 3, 4],
        [1, 2, 3, 6],
        [4, 5, 6, 1],
        [3, 4, 6, 7],
        [1, 3, 4, 6],
    ]
    tets = []
    for h in hex_conn:
        for t in tet_splits:
            tets.append(h[t])
            if len(tets) >= n_elems:
                break
        if len(tets) >= n_elems:
            break

    conn = np.array(tets[:n_elems], dtype=np.int32)
    return coords, conn


# ─── 从 L1 HDF5 加载 ────────────────────────────────────────────────────────

def load_from_l1(workspace: str, instance: str):
    """
    从真实 L1 geometry HDF5 加载节点坐标和所有体单元连接表。
    返回 (coords, conn, etype_label)，conn 包含所有体单元（跳过 shell/beam）。
    """
    if not HAS_H5PY:
        raise RuntimeError("h5py 未安装，无法读取 L1 数据")

    geom_path = os.path.join(workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_path):
        raise FileNotFoundError(f"HDF5 文件不存在：{geom_path}")

    t0 = time.perf_counter()
    with h5py.File(geom_path, "r") as f:
        coords = f["nodes/coords"][:].astype(np.float32)

        all_conn = []
        etype_info = []
        if "elements" in f:
            for etype_safe in f["elements"]:
                grp = f[f"elements/{etype_safe}"]
                if "conn" not in grp:
                    continue
                conn = grp["conn"][:].astype(np.int32)
                # 只保留体单元：通过节点数粗判（去掉 shell ≤4 节点、beam ≤3 节点）
                # C3D8→8, C3D4→4, C3D10→10, C3D6→6 等
                n_corner = conn.shape[1]
                if n_corner >= 4:
                    all_conn.append((etype_safe, conn))
                    etype_info.append(etype_safe)

    io_time = (time.perf_counter() - t0) * 1000

    if not all_conn:
        raise ValueError(f"实例 '{instance}' 中未找到体单元")

    # 拼接成一个大 conn（不同 etype 节点数可能不同，截断到最小公共列数）
    min_cols = min(c.shape[1] for _, c in all_conn)
    merged = np.vstack([c[:, :min_cols] for _, c in all_conn])

    print(f"  L1 读取耗时：{io_time:.1f} ms")
    print(f"  节点数：{len(coords):,}，体单元数：{len(merged):,}")
    print(f"  单元类型：{', '.join(etype_info)}")
    return coords, merged, io_time


# ─── 截面切割算法 ──────────────────────────────────────────────────────────────

def _edge_intersect(p0: np.ndarray, p1: np.ndarray, d0: float, d1: float) -> np.ndarray:
    """线段 p0-p1 与平面的交点（d0/d1 是两端到平面的有向距离）。"""
    t = d0 / (d0 - d1)
    return p0 + t * (p1 - p0)


def cut_hex_elements(
    coords: np.ndarray,   # [N_nodes, 3] float32
    conn: np.ndarray,     # [N_elems, 8] int32  (六面体，8列)
    normal: np.ndarray,   # [3,] float32
    position: float,      # 切面偏移量（平面方程：dot(x, normal) = position）
) -> dict:
    """
    将所有六面体单元与切面求交，返回：
      polygons   : list of ndarray [K, 3]  — 每个单元的截面多边形顶点
      hit_count  : int                     — 命中单元数
      timings    : dict                    — 各步骤耗时（ms）
    """
    timings = {}

    # ── ① 计算各节点到切面的有向距离 ─────────────────────────────────────────
    t0 = time.perf_counter()
    dist = coords @ normal - position           # [N_nodes]
    timings['dist_ms'] = (time.perf_counter() - t0) * 1000

    # ── ② 筛选命中单元 ────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    elem_dist = dist[conn]                      # [N_elems, 8]
    d_min = elem_dist.min(axis=1)
    d_max = elem_dist.max(axis=1)
    hit_mask = (d_min < 0) & (d_max > 0)       # 严格穿越
    hit_indices = np.where(hit_mask)[0]
    timings['filter_ms'] = (time.perf_counter() - t0) * 1000

    # ── ③ 逐单元求交多边形 ────────────────────────────────────────────────────
    # 六面体 12 条棱的端点对（节点局部编号）
    HEX_EDGES = [
        (0,1),(1,2),(2,3),(3,0),   # 底面
        (4,5),(5,6),(6,7),(7,4),   # 顶面
        (0,4),(1,5),(2,6),(3,7),   # 侧棱
    ]

    t0 = time.perf_counter()
    polygons = []
    for ei in hit_indices:
        pts = []
        nodes = conn[ei]                 # 8 个节点行索引
        ds    = dist[nodes]              # 8 个有向距离
        verts = coords[nodes]            # [8, 3]

        for a, b in HEX_EDGES:
            da, db = ds[a], ds[b]
            if (da < 0) != (db < 0):    # 符号不同 → 此棱穿越切面
                pts.append(_edge_intersect(verts[a], verts[b], da, db))

        if len(pts) < 3:
            continue

        # 将交点投影到切面局部坐标系，按极角排序（保证多边形是凸序）
        pts = np.array(pts, dtype=np.float32)
        centroid = pts.mean(axis=0)
        # 构造切面局部坐标系
        u = pts[0] - centroid
        u /= (np.linalg.norm(u) + 1e-12)
        v = np.cross(normal, u)
        v /= (np.linalg.norm(v) + 1e-12)
        angles = np.arctan2((pts - centroid) @ v, (pts - centroid) @ u)
        pts = pts[np.argsort(angles)]
        polygons.append(pts)

    timings['intersect_ms'] = (time.perf_counter() - t0) * 1000

    # ── ④ 三角剖分（扇形剖分，适合凸多边形）────────────────────────────────────
    t0 = time.perf_counter()
    triangles = []
    for poly in polygons:
        n = len(poly)
        for i in range(1, n - 1):
            triangles.append([poly[0], poly[i], poly[i + 1]])
    if triangles:
        tri_buf = np.array(triangles, dtype=np.float32)   # [T, 3, 3]
    else:
        tri_buf = np.zeros((0, 3, 3), dtype=np.float32)
    timings['triangulate_ms'] = (time.perf_counter() - t0) * 1000

    return {
        'polygons':   polygons,
        'tri_buf':    tri_buf,
        'hit_count':  len(hit_indices),
        'timings':    timings,
    }


def cut_tet_elements(
    coords: np.ndarray,   # [N_nodes, 3] float32
    conn: np.ndarray,     # [N_elems, 4] int32  (四面体，4列)
    normal: np.ndarray,
    position: float,
) -> dict:
    """同 cut_hex_elements，但针对四面体（C3D4）。"""
    timings = {}

    t0 = time.perf_counter()
    dist = coords @ normal - position
    timings['dist_ms'] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    elem_dist = dist[conn]
    d_min = elem_dist.min(axis=1)
    d_max = elem_dist.max(axis=1)
    hit_mask = (d_min < 0) & (d_max > 0)
    hit_indices = np.where(hit_mask)[0]
    timings['filter_ms'] = (time.perf_counter() - t0) * 1000

    # 四面体 6 条棱
    TET_EDGES = [(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)]

    t0 = time.perf_counter()
    polygons = []
    for ei in hit_indices:
        pts = []
        nodes = conn[ei]
        ds    = dist[nodes]
        verts = coords[nodes]
        for a, b in TET_EDGES:
            da, db = ds[a], ds[b]
            if (da < 0) != (db < 0):
                pts.append(_edge_intersect(verts[a], verts[b], da, db))
        if len(pts) < 3:
            continue
        pts = np.array(pts, dtype=np.float32)
        centroid = pts.mean(axis=0)
        u = pts[0] - centroid
        norm_u = np.linalg.norm(u)
        if norm_u < 1e-12:
            continue
        u /= norm_u
        v = np.cross(normal, u)
        nv = np.linalg.norm(v)
        if nv < 1e-12:
            continue
        v /= nv
        angles = np.arctan2((pts - centroid) @ v, (pts - centroid) @ u)
        polygons.append(pts[np.argsort(angles)])

    timings['intersect_ms'] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    triangles = []
    for poly in polygons:
        n = len(poly)
        for i in range(1, n - 1):
            triangles.append([poly[0], poly[i], poly[i + 1]])
    if triangles:
        tri_buf = np.array(triangles, dtype=np.float32)
    else:
        tri_buf = np.zeros((0, 3, 3), dtype=np.float32)
    timings['triangulate_ms'] = (time.perf_counter() - t0) * 1000

    return {
        'polygons':   polygons,
        'tri_buf':    tri_buf,
        'hit_count':  len(hit_indices),
        'timings':    timings,
    }


# ─── 主测试循环 ────────────────────────────────────────────────────────────────

def run_benchmark(coords, conn, etype_label: str, n_label: str):
    """在给定网格上运行 3 个切面位置（25%/50%/75% 处）并汇总。"""
    normal = np.array([0.0, 0.0, 1.0], dtype=np.float32)

    # 切面在 Z 轴 25% / 50% / 75% 处
    z_min = float(coords[:, 2].min())
    z_max = float(coords[:, 2].max())

    results = []
    for frac in [0.25, 0.50, 0.75]:
        pos = z_min + frac * (z_max - z_min)
        if conn.shape[1] == 8:
            r = cut_hex_elements(coords, conn, normal, pos)
        else:
            r = cut_tet_elements(coords, conn, normal, pos)
        total = sum(r['timings'].values())
        results.append({
            'frac':    frac,
            'hit':     r['hit_count'],
            'tris':    len(r['tri_buf']),
            'total_ms': total,
            **r['timings'],
        })

    # 取 3 次均值
    avg = {k: np.mean([x[k] for x in results]) for k in results[0] if k != 'frac'}

    return {
        'label':  n_label,
        'etype':  etype_label,
        'n_elem': len(conn),
        'n_node': len(coords),
        **avg,
    }


def print_table(rows: list):
    hdrs = ['规模', '单元类型', '单元数', '①节点距离', '②筛选', '③求交（主循环）', '④三角剖分', '总耗时', '命中单元', '截面三角形']
    keys = ['label', 'etype', 'n_elem', 'dist_ms', 'filter_ms', 'intersect_ms', 'triangulate_ms', 'total_ms', 'hit', 'tris']

    col_w = [max(len(h), 12) for h in hdrs]
    sep = '  '

    hdr_line = sep.join(h.ljust(w) for h, w in zip(hdrs, col_w))
    print()
    print(hdr_line)
    print('-' * len(hdr_line))

    for row in rows:
        def fmt(k, v):
            if k in ('dist_ms', 'filter_ms', 'intersect_ms', 'triangulate_ms', 'total_ms'):
                return f'{v:.1f} ms'
            if isinstance(v, (int, np.integer)):
                return f'{v:,}'
            if isinstance(v, float):
                return f'{v:.1f}'
            return str(v)

        vals = [fmt(k, row[k]) for k in keys]
        print(sep.join(v.ljust(w) for v, w in zip(vals, col_w)))

    print()


def print_recommendation(rows: list):
    """根据实测数据给出建议。"""
    print('=' * 70)
    print('性能评估结论')
    print('=' * 70)

    THRESHOLD_RT  = 500    # ms — 可接受"实时"（拖动时更新）
    THRESHOLD_OK  = 3000   # ms — 可接受"鼠标释放后更新"
    THRESHOLD_BAD = 8000   # ms — 需要 L2 预处理

    for row in rows:
        t = row['total_ms']
        n = row['n_elem']
        label = f"{row['label']} ({n:,} {row['etype']})"

        if t < THRESHOLD_RT:
            verdict = f'✓ 实时可行 ({t:.0f} ms < {THRESHOLD_RT} ms)'
        elif t < THRESHOLD_OK:
            verdict = f'△ mouseup 触发可接受 ({t:.0f} ms < {THRESHOLD_OK} ms)'
        elif t < THRESHOLD_BAD:
            verdict = f'✗ 需 L2 体单元索引 ({t:.0f} ms，超过 {THRESHOLD_OK} ms)'
        else:
            verdict = f'✗✗ 必须 L2 预处理 ({t:.0f} ms >> {THRESHOLD_BAD} ms)'

        print(f'  {label:<40}  {verdict}')

    print()
    print('注意：')
    print('  ① 求交（主循环）步骤是 Python 级循环，数值最大。')
    print('  ② 若命中单元数大（> 50%），考虑体单元 Octree 粗筛。')
    print('  ③ 实际 HDF5 I/O 耗时需单独测量（见 --workspace 模式）。')
    print()


# ─── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='View Cut Phase 2 POC 性能评估',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            示例：
              python tools/viewcut_poc.py
              python tools/viewcut_poc.py --sizes 50000 200000 1000000
              python tools/viewcut_poc.py --workspace /data/job1 --instance PART-1-1
        """),
    )
    parser.add_argument('--sizes', type=int, nargs='+',
                        default=[10_000, 50_000, 200_000, 500_000, 1_000_000],
                        help='合成网格的单元数列表（默认：1万 5万 20万 50万 100万）')
    parser.add_argument('--etype', choices=['hex', 'tet', 'both'], default='hex',
                        help='合成网格类型：hex（六面体）/ tet（四面体）/ both（各测一次）')
    parser.add_argument('--workspace', type=str, default=None,
                        help='真实 L1 workspace 路径（替代合成数据）')
    parser.add_argument('--instance', type=str, default=None,
                        help='实例名（与 --workspace 配合使用）')
    args = parser.parse_args()

    print()
    print('View Cut Phase 2 — POC 性能评估')
    print('Python:', sys.version.split()[0], '  NumPy:', np.__version__)
    print()

    rows = []

    # ── 真实数据模式 ──────────────────────────────────────────────────────────
    if args.workspace:
        if not args.instance:
            parser.error('--workspace 需要同时指定 --instance')
        print(f'[真实数据] workspace={args.workspace}  instance={args.instance}')
        try:
            coords, conn, io_ms = load_from_l1(args.workspace, args.instance)
        except Exception as e:
            print(f'  读取失败：{e}')
            sys.exit(1)

        etype_label = f'C3D{conn.shape[1]}'
        row = run_benchmark(coords, conn, etype_label, '真实模型')
        row['io_ms'] = io_ms
        row['total_ms'] += io_ms
        rows.append(row)
        print_table(rows)
        print_recommendation(rows)
        return

    # ── 合成数据模式 ──────────────────────────────────────────────────────────
    etypes = []
    if args.etype in ('hex', 'both'):
        etypes.append(('hex', gen_hex_mesh, 'C3D8'))
    if args.etype in ('tet', 'both'):
        etypes.append(('tet', gen_tet_mesh, 'C3D4'))

    for etype_key, gen_fn, etype_label in etypes:
        print(f'[合成数据 — {etype_label}]')
        for n in args.sizes:
            label = _fmt_n(n)
            print(f'  生成 {label} {etype_label} 网格 ...')
            t0 = time.perf_counter()
            coords, conn = gen_fn(n)
            gen_ms = (time.perf_counter() - t0) * 1000
            actual_n = len(conn)
            print(f'    实际单元数：{actual_n:,}（生成耗时 {gen_ms:.0f} ms）')
            row = run_benchmark(coords, conn, etype_label, label)
            rows.append(row)

        print()

    print_table(rows)
    print_recommendation(rows)


def _fmt_n(n: int) -> str:
    if n >= 1_000_000:
        return f'{n // 1_000_000}M'
    if n >= 1_000:
        return f'{n // 1_000}K'
    return str(n)


if __name__ == '__main__':
    main()
