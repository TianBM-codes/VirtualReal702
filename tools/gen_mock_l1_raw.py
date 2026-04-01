#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_mock_l1_raw.py — 生成与 abaqus_dump.py 输出完全一致的 mock l1_raw 目录。

用途：在没有 Abaqus license / ODB 文件的情况下测试 l1_pack.py → L2 → L3 全流程。

生成规格：
  - 11 个 Instance（PART-1-1 ~ PART-11-11），每个是 10×10 节点网格
  - 元素类型：S4R（shell 四边形，9×9 = 81 个单元）
  - 1 个 Step（Step-1），4 帧
  - 1 个位移场 U（NODAL，3 分量 U1/U2/U3），USUM 作为额外 magnitude 字段

Usage:
    python tools/gen_mock_l1_raw.py --workspace /tmp/mock_odb
    python tools/gen_mock_l1_raw.py --workspace /tmp/mock_odb --instances 3 --nodes 50 --frames 4
"""

import argparse
import json
import os

import numpy as np

# ─── Constants (mirrors abaqus_dump.py) ───────────────────────────────────────

# S4R: code=1, 4 corner nodes, 1 face [0,1,2,3]
S4R_CODE     = 1
S4R_N_CORNER = 4
S4R_FACE_DEF = [0, 1, 2, 3]   # single face


# ─── Helpers ──────────────────────────────────────────────────────────────────

def mkdirs(path):
    os.makedirs(path, exist_ok=True)


def jdump(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def npsave(path, arr):
    np.save(path, arr)


def safe(name):
    """Must match l1_pack.py safe() exactly."""
    return name.replace("/", "__").replace("\\", "__").replace(" ", "_")


def make_grid(n_side, x_offset, y_offset):
    """
    Build a flat n_side×n_side node grid in the XY plane.

    Returns
    -------
    node_labels : [N] int32   — 1-based, sorted ascending
    node_coords : [N, 3] float64
    elem_labels : [M] int32   — 1-based, sorted ascending
    conn_rows   : [M, 4] int32  — row indices into node_labels (0-based)
    """
    N = n_side * n_side
    xs = np.linspace(0.0, 1.0, n_side) + x_offset
    ys = np.linspace(0.0, 1.0, n_side) + y_offset

    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    coords = np.zeros((N, 3), dtype=np.float64)
    coords[:, 0] = xx.ravel()
    coords[:, 1] = yy.ravel()
    # small z-bump so normals are well-defined later
    coords[:, 2] = np.sin(coords[:, 0]) * 0.05

    node_labels = np.arange(1, N + 1, dtype=np.int32)

    # Quads: (i,j), (i+1,j), (i+1,j+1), (i,j+1)  — S4R connectivity order
    quads = []
    for i in range(n_side - 1):
        for j in range(n_side - 1):
            n00 = i * n_side + j        # row index
            n10 = (i + 1) * n_side + j
            n11 = (i + 1) * n_side + j + 1
            n01 = i * n_side + j + 1
            quads.append([n00, n10, n11, n01])

    M = len(quads)
    conn_rows   = np.array(quads, dtype=np.int32)          # [M, 4] row indices
    elem_labels = np.arange(1, M + 1, dtype=np.int32)

    return node_labels, coords, elem_labels, conn_rows


def make_face_data(conn_rows, node_coords):
    """
    Compute S4R face arrays (one face per element).

    Returns
    -------
    face_elem_idx  : [M] int32
    face_seq       : [M] uint8   (always 1 for S4R)
    face_node_conn : [M, 4] int32
    face_normals   : [M, 3] float32
    """
    M = conn_rows.shape[0]
    face_elem_idx  = np.arange(M, dtype=np.int32)
    face_seq       = np.ones(M, dtype=np.uint8)
    face_node_conn = conn_rows.copy()   # [M, 4]

    # Normal = cross(v01, v03), Newell would also work; cross is fine for planar quads
    p0 = node_coords[conn_rows[:, 0]]   # [M, 3]
    p1 = node_coords[conn_rows[:, 1]]
    p3 = node_coords[conn_rows[:, 3]]
    v01 = (p1 - p0).astype(np.float64)
    v03 = (p3 - p0).astype(np.float64)
    normals = np.cross(v01, v03)
    norms   = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = (normals / np.where(norms > 1e-12, norms, 1.0)).astype(np.float32)

    return face_elem_idx, face_seq, face_node_conn, normals


def make_displacement(node_coords, frame_idx, inst_idx, n_frames):
    """
    Generate plausible sinusoidal displacements [N, 3] float32.
    Amplitude grows with frame; direction varies by instance.
    """
    N = node_coords.shape[0]
    phase   = 2 * np.pi * frame_idx / n_frames
    amp     = 0.05 * (frame_idx + 1)
    angle   = 2 * np.pi * inst_idx / 11

    x = node_coords[:, 0].astype(np.float32)
    y = node_coords[:, 1].astype(np.float32)

    u1 = amp * np.sin(phase + x * np.pi) * np.cos(angle)
    u2 = amp * np.sin(phase + y * np.pi) * np.sin(angle)
    u3 = amp * 0.5 * np.cos(phase + (x + y) * np.pi)

    return np.stack([u1, u2, u3], axis=1).astype(np.float32)


# ─── Main ─────────────────────────────────────────────────────────────────────

def generate(workspace, n_instances, n_side, n_frames):
    raw_dir = os.path.join(workspace, "l1_raw")
    mkdirs(raw_dir)

    N_NODES = n_side * n_side
    N_ELEMS = (n_side - 1) ** 2

    meta = {"instances": {}, "geom": {}, "steps": {}}

    # ── Instance names ─────────────────────────────────────────────────────────
    inst_names = ["PART-{}-1".format(i + 1) for i in range(n_instances)]

    # ── Assembly ───────────────────────────────────────────────────────────────
    asm_dir = os.path.join(raw_dir, "assembly")
    for i, inst_name in enumerate(inst_names):
        s = safe(inst_name)   # safe name
        d = os.path.join(asm_dir, "instances", s)
        mkdirs(d)
        npsave(os.path.join(d, "transform.npy"), np.eye(4, dtype=np.float64))
        with open(os.path.join(d, "part_name.txt"), "w") as f:
            f.write("PART-{}".format(i + 1))
        meta["instances"][inst_name] = {"part_name": "PART-{}".format(i + 1)}

    # ── Geometry ───────────────────────────────────────────────────────────────
    geom_dir = os.path.join(raw_dir, "geom")
    instance_geom = {}   # inst_name → (node_labels, node_coords, elem_labels, conn_rows)

    for i, inst_name in enumerate(inst_names):
        s = safe(inst_name)
        d = os.path.join(geom_dir, s)
        mkdirs(d)

        # Offset instances in XZ so they don't overlap
        x_off = (i % 4) * 1.5
        y_off = (i // 4) * 1.5
        node_labels, node_coords, elem_labels, conn_rows = make_grid(
            n_side, x_off, y_off
        )
        instance_geom[inst_name] = (node_labels, node_coords, elem_labels, conn_rows)

        npsave(os.path.join(d, "node_labels.npy"), node_labels)
        npsave(os.path.join(d, "node_coords.npy"), node_coords)

        # Elements (S4R)
        td = os.path.join(d, "elems", "S4R")
        mkdirs(td)
        npsave(os.path.join(td, "labels.npy"), elem_labels)
        npsave(os.path.join(td, "conn.npy"),   conn_rows)

        fei, fseq, fnc, fnrm = make_face_data(conn_rows, node_coords)
        npsave(os.path.join(td, "face_elem_idx.npy"),  fei)
        npsave(os.path.join(td, "face_seq.npy"),       fseq)
        npsave(os.path.join(td, "face_node_conn.npy"), fnc)
        npsave(os.path.join(td, "face_normals.npy"),   fnrm)

        # Empty sets / sections / materials
        mkdirs(os.path.join(d, "isets", "node_sets"))
        mkdirs(os.path.join(d, "isets", "elem_sets"))
        jdump(os.path.join(d, "sections.json"),  {})
        jdump(os.path.join(d, "materials.json"), {})

        bbox_min = node_coords.min(axis=0).tolist()
        bbox_max = node_coords.max(axis=0).tolist()

        meta["geom"][inst_name] = {
            "safe_name":     s,
            "part_name":     "PART-{}".format(i + 1),
            "node_count":    N_NODES,
            "elem_count":    N_ELEMS,
            "has_highorder": False,
            "bbox_min":      bbox_min,
            "bbox_max":      bbox_max,
            "elem_types": {
                "S4R": {
                    "count":          N_ELEMS,
                    "has_midnodes":   0,
                    "n_corner_nodes": S4R_N_CORNER,
                    "n_faces":        1,
                    "etype_code":     S4R_CODE,
                }
            },
            "isets_node": {},
            "isets_elem": {},
        }

        print("  [geom] {} — {} nodes, {} elems".format(inst_name, N_NODES, N_ELEMS))

    # ── Results ────────────────────────────────────────────────────────────────
    step_name  = "Step-1"
    field_name = "U"
    components = ["U1", "U2", "U3"]
    frames_meta = [
        {"frame_idx": fi, "frame_value": float(fi), "description": "Frame {}".format(fi)}
        for fi in range(n_frames)
    ]

    results_dir = os.path.join(raw_dir, "results")
    field_dir   = os.path.join(results_dir, "{}_{}".format(step_name, field_name))
    mkdirs(field_dir)

    blocks_meta = []

    for inst_name in inst_names:
        node_labels, node_coords, _, _ = instance_geom[inst_name]
        bd = os.path.join(field_dir, safe(inst_name), "NODAL")
        mkdirs(bd)

        # Canonical labels file
        npsave(os.path.join(bd, "labels.npy"), node_labels)

        # Per-frame data [N, 3] float32
        for fi in range(n_frames):
            inst_idx = inst_names.index(inst_name)
            data     = make_displacement(node_coords, fi, inst_idx, n_frames)
            npsave(os.path.join(bd, "f{:04d}.npy".format(fi)), data)

        blocks_meta.append({
            "inst_name":  inst_name,
            "position":   "NODAL",
            "elem_type":  None,
            "ncomp":      3,
            "n_entities": len(node_labels),
        })

    jdump(os.path.join(field_dir, "meta.json"), {
        "step_name":   step_name,
        "field_name":  field_name,
        "components":  components,
        "invariants":  ["MAGNITUDE"],
        "has_section": 0,
        "blocks":      blocks_meta,
    })

    meta["steps"] = {
        step_name: {
            "step_number": 0,
            "procedure":   "STATIC",
            "num_frames":  n_frames,
            "frames":      frames_meta,
        }
    }

    print("  [results] {}/{} — {} instances × {} frames × {} components".format(
        step_name, field_name, n_instances, n_frames, len(components)
    ))

    # ── dump_meta.json ─────────────────────────────────────────────────────────
    jdump(os.path.join(raw_dir, "dump_meta.json"), meta)
    print("\nDone. l1_raw written to: {}".format(raw_dir))
    print("Next: python src/l1/l1_pack.py --workspace {}".format(workspace))


def parse_args():
    p = argparse.ArgumentParser(description="Generate mock l1_raw for pipeline testing")
    p.add_argument("--workspace",  required=True, help="Output workspace directory")
    p.add_argument("--instances",  type=int, default=11,  help="Number of instances (default 11)")
    p.add_argument("--nodes-side", type=int, default=10,  help="Grid side length; total nodes = side² (default 10 → 100 nodes)")
    p.add_argument("--frames",     type=int, default=4,   help="Number of frames (default 4)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print("=== gen_mock_l1_raw ===")
    print("  workspace : {}".format(args.workspace))
    print("  instances : {}".format(args.instances))
    print("  nodes     : {}² = {}".format(args.nodes_side, args.nodes_side ** 2))
    print("  frames    : {}".format(args.frames))
    print()
    generate(args.workspace, args.instances, args.nodes_side, args.frames)
