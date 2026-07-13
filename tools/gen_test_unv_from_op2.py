#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 BDF + OP2(SOL103) 生成模拟试验模态 UNV 文件。

用途:没有真实试验数据时,从仿真结果"造"一份试验模态数据,用于联调
试验网格 / FEM 模型同屏同步动画等功能。

做法:
  1. 读 BDF 拿节点坐标,读 OP2 拿模态振型(eigenvectors)。
  2. 用最远点采样(FPS)从全部节点中挑 n_points 个作为"测点",保留原节点号。
  3. 测点之间用最小生成树 + k 近邻连边,构成试验线框(tracelines)。
  4. 取每阶振型在测点处的平动分量,加上少量随机扰动、频率偏移,模拟
     试验与仿真的差异。
  5. 按 FemToolsUNVParser 支持的格式写 UNV:
       dataset 15  节点坐标
       dataset 82  tracelines(0 分隔的折线段)
       dataset 55  normal modes(analysis_type=2, data_type=2 实数, ndv=3)

生成的文件可直接走既有导入链路:POST /import/unv → t_mt_py_test_* 表。

用法:
  python tools/gen_test_unv_from_op2.py \
      --bdf /path/to/sol103.bdf --op2 /path/to/sol103.op2 \
      --out /path/to/sol103_test.unv \
      [--n-points 40] [--modes 8] [--noise 0.03] [--freq-shift 0.03] [--seed 702]
"""
import argparse
import math
import os
import sys

import numpy as np

# 保证从仓库根目录导入(与 tools/ 下其他脚本一致)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_bdf_nodes(bdf_path):
    """读 BDF,返回 {node_id: [x, y, z]}(basic 坐标系)。"""
    from pyNastran.bdf.bdf import BDF
    bdf = BDF(debug=False)
    bdf.read_bdf(bdf_path, xref=True)
    coords = {}
    for nid, node in bdf.nodes.items():
        coords[int(nid)] = np.asarray(node.get_position(), dtype=np.float64)
    return coords


def load_op2_modes(op2_path):
    """
    读 OP2 eigenvectors,返回 (node_ids [N], shapes [n_modes, N, 3], freqs [n_modes])。
    freqs 由特征值换算:f = sqrt(eign) / (2*pi)。
    """
    from pyNastran.op2.op2 import OP2
    op2 = OP2(debug=False)
    # post=-2 的 OP2 文件结尾会触发 pyNastran 的 FatalError,关闭该检查即可正常读完
    op2.stop_on_unclosed_file = False
    op2.read_op2(op2_path, build_dataframe=False)
    if not op2.eigenvectors:
        raise RuntimeError("OP2 中没有 eigenvectors(不是 SOL103 结果?)")
    ev = op2.eigenvectors[sorted(op2.eigenvectors.keys())[0]]
    node_ids = np.asarray(ev.node_gridtype[:, 0], dtype=np.int64)
    shapes = np.asarray(ev.data[:, :, :3], dtype=np.float64)  # [n_modes, N, 3] 平动
    freqs = np.array([math.sqrt(max(e, 0.0)) / (2.0 * math.pi) for e in ev.eigns],
                     dtype=np.float64)
    return node_ids, shapes, freqs


def farthest_point_sample(coords, n_points, seed):
    """最远点采样,返回选中的行号数组。保证测点在结构上分布均匀。"""
    n = coords.shape[0]
    n_points = min(n_points, n)
    rng = np.random.default_rng(seed)
    chosen = [int(rng.integers(n))]
    dist = np.linalg.norm(coords - coords[chosen[0]], axis=1)
    for _ in range(n_points - 1):
        idx = int(np.argmax(dist))
        chosen.append(idx)
        dist = np.minimum(dist, np.linalg.norm(coords - coords[idx], axis=1))
    return np.array(sorted(chosen), dtype=np.int64)


def build_wireframe_edges(coords, k=2):
    """
    最小生成树(保证连通)+ 每点 k 近邻(加密线框),返回去重后的边列表
    [(i, j), ...](行号,i < j)。
    """
    n = coords.shape[0]
    d2 = np.sum((coords[:, None, :] - coords[None, :, :]) ** 2, axis=2)
    np.fill_diagonal(d2, np.inf)

    # Prim 最小生成树
    edges = set()
    in_tree = np.zeros(n, dtype=bool)
    in_tree[0] = True
    best = d2[0].copy()
    best_from = np.zeros(n, dtype=np.int64)
    for _ in range(n - 1):
        j = int(np.argmin(np.where(in_tree, np.inf, best)))
        i = int(best_from[j])
        edges.add((min(i, j), max(i, j)))
        in_tree[j] = True
        closer = d2[j] < best
        best[closer] = d2[j][closer]
        best_from[closer] = j

    # k 近邻加密
    for i in range(n):
        for j in np.argsort(d2[i])[:k]:
            edges.add((min(i, int(j)), max(i, int(j))))
    return sorted(edges)


def _fmt_floats(vals):
    return " ".join(f"{v: .6e}" for v in vals)


def write_unv(out_path, node_ids, coords, edges, modes):
    """
    按 FemToolsUNVParser 可解析的格式写 UNV。

    modes: [{"mode_no", "frequency", "damping", "disp": {nid: (ux,uy,uz)}}]
    """
    lines = []

    # dataset 15: 节点
    lines += ["    -1", "    15"]
    for nid, (x, y, z) in zip(node_ids, coords):
        lines.append(f"{int(nid):10d}{0:10d}{0:10d}{11:10d} {x: .6e} {y: .6e} {z: .6e}")
    lines.append("    -1")

    # dataset 82: tracelines,每条边写成 2 节点折线,用 0 分隔
    trace_ints = []
    for i, j in edges:
        trace_ints += [int(node_ids[i]), int(node_ids[j]), 0]
    lines += ["    -1", "    82",
              f"{1:10d}{len(trace_ints):10d}{0:10d}",
              "Simulated test wireframe"]
    for k in range(0, len(trace_ints), 8):
        lines.append("".join(f"{v:10d}" for v in trace_ints[k:k + 8]))
    lines.append("    -1")

    # dataset 55: 每阶一个数据集(normal mode,实数,ndv=3)
    for m in modes:
        lines += ["    -1", "    55",
                  "NONE", "NONE", "NONE", "NONE", "NONE",
                  # model_type analysis_type data_ch spec_data_type data_type ndv
                  f"{1:10d}{2:10d}{3:10d}{8:10d}{2:10d}{3:10d}",
                  # 8I10,field3=load_case field4=mode_no
                  f"{2:10d}{4:10d}{0:10d}{m['mode_no']:10d}{0:10d}{0:10d}{0:10d}{0:10d}",
                  # freq, modal_mass, viscous_damping, hysteretic_damping
                  f" {m['frequency']: .6e} {0.0: .6e} {m['damping']: .6e} {0.0: .6e} {0.0: .6e} {0.0: .6e}"]
        for nid in node_ids:
            ux, uy, uz = m["disp"][int(nid)]
            lines.append(f"{int(nid):10d}")
            lines.append(f" {ux: .6e} {uy: .6e} {uz: .6e}")
        lines.append("    -1")

    with open(out_path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bdf", required=True)
    ap.add_argument("--op2", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-points", type=int, default=40, help="测点数(默认 40)")
    ap.add_argument("--modes", type=int, default=8, help="导出前几阶模态(默认 8)")
    ap.add_argument("--noise", type=float, default=0.03,
                    help="振型相对扰动幅度(默认 0.03 = 3%%)")
    ap.add_argument("--freq-shift", type=float, default=0.03,
                    help="频率随机偏移上限(默认 ±3%%)")
    ap.add_argument("--seed", type=int, default=702)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)

    print(f"读取 BDF: {args.bdf}")
    bdf_coords = load_bdf_nodes(args.bdf)
    print(f"  节点数: {len(bdf_coords)}")

    print(f"读取 OP2: {args.op2}")
    op2_nids, shapes, freqs = load_op2_modes(args.op2)
    n_modes = min(args.modes, shapes.shape[0])
    print(f"  模态阶数: {shapes.shape[0]}(导出前 {n_modes} 阶),节点数: {len(op2_nids)}")

    # 只在 BDF/OP2 都有的节点里选测点
    common = [i for i, nid in enumerate(op2_nids) if int(nid) in bdf_coords]
    op2_row = np.array(common, dtype=np.int64)
    nids = op2_nids[op2_row]
    coords = np.array([bdf_coords[int(n)] for n in nids])

    sel = farthest_point_sample(coords, args.n_points, args.seed)
    sel_nids = nids[sel]
    sel_coords = coords[sel]
    sel_rows = op2_row[sel]
    print(f"采样测点: {len(sel_nids)} 个")

    edges = build_wireframe_edges(sel_coords)
    print(f"线框边数: {len(edges)}")

    modes = []
    for mi in range(n_modes):
        shape = shapes[mi][sel_rows]                       # [n_pts, 3]
        ref = float(np.abs(shape).max()) or 1.0
        noisy = (shape * (1.0 + rng.normal(0.0, args.noise, shape.shape))
                 + rng.normal(0.0, args.noise * 0.1 * ref, shape.shape))
        freq = float(freqs[mi]) * (1.0 + rng.uniform(-args.freq_shift, args.freq_shift))
        modes.append({
            "mode_no": mi + 1,
            "frequency": freq,
            "damping": float(rng.uniform(0.005, 0.02)),
            "disp": {int(nid): tuple(noisy[k]) for k, nid in enumerate(sel_nids)},
        })
        print(f"  Mode {mi + 1}: FEM {freqs[mi]:.2f} Hz → 试验 {freq:.2f} Hz")

    write_unv(args.out, sel_nids, sel_coords, edges, modes)
    print(f"已写出: {args.out}")

    # 自校验:用项目自己的解析器读回
    from FemToolsUNVParser import parse_unv
    nodes, _, trace_lines, parsed_modes, _, _ = parse_unv(args.out)
    n_segs = sum(len(p) - 1 for p in trace_lines)
    print(f"回读校验: 节点 {len(nodes)},折线 {len(trace_lines)} 条(线段 {n_segs}),模态 {len(parsed_modes)} 阶")
    assert len(nodes) == len(sel_nids), "节点数不一致"
    assert len(parsed_modes) == n_modes, "模态阶数不一致"
    assert n_segs == len(edges), "线段数不一致"
    print("OK")


if __name__ == "__main__":
    main()
