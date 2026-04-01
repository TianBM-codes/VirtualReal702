#!/usr/bin/env python3
"""
生成模态分析测试 JSON 文件。

输出格式与 modal_service.py 一致：
  {
    "points":      { "ids": [...], "position": [...flat N*3...], "ItemSize": 3 },
    "elements":    { "type": 0, "index": [...flat quads node-IDs...], "ItemSize": 4 },
    "modal_shape": [
      { "order": 1, "frequency": 30.0, "unit": "Hz",
        "position": { "real": [...flat N*3...], "imag": [...flat N*3...] } },
      ...
    ]
  }

模型：简支方板（Simply-supported square plate）
  - N×N 个节点，(N-1)×(N-1) 个 QUAD4 单元（1-based 节点 ID）
  - 振型：UZ = sin(m*pi*x/L)*sin(n*pi*y/L)（实部）
  - 虚部取实部的 20%（模拟小阻尼复模态，仅供演示）

用法：
  python tools/gen_mock_modal.py
  python tools/gen_mock_modal.py --out data/my_plate.json --n 20 --modes 8
"""
import argparse
import json
import math
import os


def main():
    parser = argparse.ArgumentParser()
    default_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_modal.json")
    parser.add_argument("--out",   default=default_out, help="输出 JSON 路径")
    parser.add_argument("--n",     type=int, default=10, help="每边节点数（默认10）")
    parser.add_argument("--modes", type=int, default=6,  help="模态阶数（默认6）")
    args = parser.parse_args()

    N = args.n
    L = 1.0   # 板边长 1 m

    # ── 节点 ID（1-based）和坐标 ─────────────────────────────────
    ids      = []   # 节点 ID，1-based
    pos_flat = []   # flat [N*N * 3]

    for j in range(N):
        for i in range(N):
            node_id = j * N + i + 1           # 1-based
            x = round(i * L / (N - 1), 6)
            y = round(j * L / (N - 1), 6)
            ids.append(node_id)
            pos_flat += [x, y, 0.0]

    # ── 单元连接（flat，存节点 ID 1-based）──────────────────────
    index_flat = []
    for j in range(N - 1):
        for i in range(N - 1):
            n0 = j * N + i + 1
            n1 = j * N + i + 2
            n2 = (j + 1) * N + i + 2
            n3 = (j + 1) * N + i + 1
            index_flat += [n0, n1, n2, n3]

    # ── 振型（简支板弯曲模态）────────────────────────────────────
    patterns = [(1,1),(1,2),(2,1),(2,2),(1,3),(3,1),(2,3),(3,2),(3,3),(1,4)]
    f0 = 15.0   # 基频 Hz

    modal_shapes = []
    for k, (m, n) in enumerate(patterns[:args.modes]):
        freq = round(f0 * (m * m + n * n), 2)

        real_flat = []
        imag_flat = []
        node_id = 1
        for j in range(N):
            for i in range(N):
                x = i * L / (N - 1)
                y = j * L / (N - 1)
                # 实部：弯曲振型（UZ 为主，UX/UY 为次）
                uz_r = math.sin(m * math.pi * x / L) * math.sin(n * math.pi * y / L)
                ux_r = 0.05 * math.cos(m * math.pi * x / L) * math.sin(n * math.pi * y / L)
                uy_r = 0.05 * math.sin(m * math.pi * x / L) * math.cos(n * math.pi * y / L)
                # 虚部：取实部 20%（模拟小阻尼）
                uz_i = 0.2 * uz_r
                ux_i = 0.2 * ux_r
                uy_i = 0.2 * uy_r
                real_flat += [round(ux_r, 8), round(uy_r, 8), round(uz_r, 8)]
                imag_flat += [round(ux_i, 8), round(uy_i, 8), round(uz_i, 8)]
                node_id += 1

        modal_shapes.append({
            "order":     k + 1,
            "frequency": freq,
            "unit":      "Hz",
            "position":  {
                "real": real_flat,
                "imag": imag_flat,
            },
        })

    data = {
        "points": {
            "ids":      ids,
            "position": pos_flat,
            "ItemSize": 3,
        },
        "elements": {
            "type":     0,
            "index":    index_flat,
            "ItemSize": 4,
        },
        "modal_shape": modal_shapes,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"生成完成：{args.out}")
    print(f"  节点数  : {len(ids)}")
    print(f"  单元数  : {len(index_flat) // 4}")
    print(f"  模态阶数: {len(modal_shapes)}")
    for m in modal_shapes:
        print(f"    Mode {m['order']}: {m['frequency']} Hz")


if __name__ == "__main__":
    main()
