#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest.py — Layer 2: Preprocessing (Pure Python / NumPy)

Reads L1 HDF5 output and generates L2 rendering-ready buffers.

Key operations:
  1. Global coordinates  : local_coords × instance transform matrix
  2. Surface extraction  : faces owned by exactly one element (vectorized)
  3. Triangulation       : tri/quad fully vectorized; higher polygons via loop
  4. Indexed geometry    : positions/normals [Nv,3] + indices [Nt,3]; shared within face
  5. Feature edges       : boundary (count=1) + fold (angle ≥ 30°), vectorized
  6. Octree              : max_depth=8, leaf ≤ 1000 faces, stored as flat arrays

Usage:
    python src/l2/ingest.py --workspace /path/to/workspace
"""

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

import h5py
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def safe(name):
    return name.replace('/', '__').replace('\\', '__').replace(' ', '_')


FOLD_ANGLE_DEG = 30.0
OCTREE_MAX_DEPTH = 8
OCTREE_LEAF_THRESHOLD = 1000

SHELL_ELEM_CODES = frozenset({0, 1, 8})   # S3/S4R/S4/STRI65
LINE_ELEM_CODES  = frozenset({9, 10})
POINT_ELEM_CODES = frozenset({11})

# Keep in sync with src/l1/abaqus_dump.py ELEM_TYPE_CODE.
ELEM_TYPE_CODE = {
    # shells / membranes
    'S3': 0,  'S3R': 0,  'S6': 0,   'STRI3': 0,
    'S4': 1,  'S4R': 1,  'S4R5': 1, 'S8R': 1, 'S8R5': 1,
    # solids
    'C3D4': 2,  'C3D4H': 2,
    'C3D6': 3,  'C3D6H': 3,
    'C3D8': 4,  'C3D8R': 4,  'C3D8I': 4,  'C3D8H': 4,  'C3D8RH': 4,
    'C3D10': 5, 'C3D10M': 5, 'C3D10H': 5, 'C3D10MH': 5,
    'C3D15': 6, 'C3D15H': 6,
    'C3D20': 7, 'C3D20R': 7, 'C3D20H': 7, 'C3D20RH': 7,
    # high-order curved triangle shell
    'STRI65': 8,
    # line elements (truss / beam) — no face data, silently ignored in collect_faces
    'T3D2':  9,  'B31':  9,  'B31OS': 9,  'PIPE31': 9,
    'T3D3': 10,  'B32': 10,  'B32OS':10,  'PIPE32':10,
    # 2-node connector / spring / dashpot elements — rendered as line segments
    'CONN3D2': 9, 'SPRING2': 9, 'SPRINGA': 9, 'DASHPOT2': 9, 'DASHPOTA': 9,
    # point elements — concentrated mass / rotary inertia / grounded spring/dashpot
    'MASS': 11,  'ROTARYI': 11,  'SPRING1': 11,  'DASHPOT1': 11,
    # plane / axisymmetric (treated as shell faces)
    'CPS3': 0, 'CPS4': 1, 'CPS4R': 1,
    'CPE3': 0, 'CPE4': 1, 'CPE4R': 1,
    'CAX3': 0, 'CAX4': 1, 'CAX4R': 1,
    # membrane 3D (M3D* handled separately via startswith check below)
    'M3D3': 0, 'M3D4': 1, 'M3D4R': 1,
}

# Abaqus variant suffixes ordered longest-first (see also src/l1/abaqus_dump.py).
_ABAQUS_VARIANT_SUFFIXES = ('OS', 'RH', 'MH', 'R5', 'R', 'H', 'I', 'M', 'T', '5')


def _resolve_elem_code(etype_str):
    """Return ELEM_TYPE_CODE for etype_str, stripping variant suffixes if needed."""
    s = etype_str.upper()
    while True:
        code = ELEM_TYPE_CODE.get(s)
        if code is not None:
            return code
        for suf in _ABAQUS_VARIANT_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf):
                s = s[:-len(suf)]
                break
        else:
            return -1


def parse_args():
    p = argparse.ArgumentParser(description="L2 Preprocessing")
    p.add_argument("--workspace", required=True)
    p.add_argument(
        "--phase", choices=("all", "core", "advanced"), default="all",
        help="core writes display geometry; advanced writes optional indexes",
    )
    return p.parse_args()


def mkdirs(path):
    os.makedirs(path, exist_ok=True)


# ─── Coordinates ──────────────────────────────────────────────────────────────

def get_instance_transform(asm_h5, inst_name):
    path = "instances/{}/transform".format(inst_name)
    return asm_h5[path][:] if path in asm_h5 else np.eye(4, dtype=np.float64)


def compute_global_coords(local_coords, transform):
    """[N, 3] local → [N, 3] float32 global via 4×4 homogeneous matrix."""
    N = local_coords.shape[0]
    hom = np.ones((N, 4), dtype=np.float64)
    hom[:, :3] = local_coords
    return (transform @ hom.T).T[:, :3].astype(np.float32)


# ─── Face collection + surface detection (vectorized) ─────────────────────────

def collect_faces(geom_h5):
    """
    Collect all faces from all element types in one pass.
    Returns:
        fnc          [Tf, max_fn] int32   face node rows (-1 = unused slot)
        elem_rows    [Tf]         int32   which element (row in labels array)
        face_seqs    [Tf]         uint8   face number within element (1-based)
        etype_codes  [Tf]         uint8   element type code
        etype_strs   [Tf]         S8      element type string
        is_surface   [Tf]         bool    True if face owned by exactly one element
    """
    keys_list  = []   # sorted node rows → canonical face key
    fnc_list   = []   # original face_node_conn rows
    er_list    = []   # elem_row per face
    fs_list    = []   # face_seq per face
    ec_list    = []   # etype_code per face
    es_list    = []   # etype_str per face
    max_fn     = 0

    for etype_str in geom_h5.get("elements", {}):
        grp = geom_h5["elements/{}".format(etype_str)]
        if "face_node_conn" not in grp:
            continue
        etype_code = _resolve_elem_code(etype_str)
        if etype_code < 0:
            logger.warning("collect_faces: unrecognized element type '%s' — skipped", etype_str)
            continue

        fnc = grp["face_node_conn"][:]   # [Mf, w]
        # Defensive: some producers (older bdf_pack) wrote an empty 1-D
        # face_node_conn for zero-face line/point types instead of omitting it.
        # Such groups have no faces to collect — skip rather than crash on the
        # `Mf, w = fnc.shape` unpack.
        if fnc.ndim != 2 or fnc.shape[0] == 0:
            continue
        fei = grp["face_elem_idx"][:]    # [Mf]
        fsq = grp["face_seq"][:]         # [Mf]
        Mf, w = fnc.shape

        # Sort rows (replace -1 with INT32_MAX so they sort to the end)
        SENT = np.iinfo(np.int32).max
        k = fnc.copy()
        k[k == -1] = SENT
        k.sort(axis=1)

        keys_list.append(k)
        fnc_list.append(fnc)
        er_list.append(fei)
        fs_list.append(fsq)
        ec_list.append(np.full(Mf, etype_code, dtype=np.uint8))
        es_list.append(np.array([etype_str.encode("ascii")] * Mf, dtype="S8"))
        max_fn = max(max_fn, w)

    if not keys_list:
        empty = np.zeros(0, dtype=np.int32)
        return (np.zeros((0, 1), dtype=np.int32), empty, empty,
                np.zeros(0, dtype=np.uint8), np.zeros(0, dtype="S8"),
                np.zeros(0, dtype=bool))

    SENT = np.iinfo(np.int32).max

    def pad(arr, width, fill):
        if arr.shape[1] < width:
            p = np.full((arr.shape[0], width - arr.shape[1]), fill, dtype=arr.dtype)
            return np.concatenate([arr, p], axis=1)
        return arr

    keys  = np.vstack([pad(k, max_fn, SENT) for k in keys_list])
    fnc   = np.vstack([pad(f, max_fn, -1)   for f in fnc_list])
    er    = np.concatenate(er_list)
    fs    = np.concatenate(fs_list)
    ec    = np.concatenate(ec_list)
    es    = np.concatenate(es_list)

    # Each face's canonical key = its sorted node row vector
    # Encode as bytes for np.unique
    keys_bytes = np.ascontiguousarray(keys).view(
        np.dtype((np.void, keys.dtype.itemsize * max_fn))
    ).ravel()
    _, inv, counts = np.unique(keys_bytes, return_inverse=True, return_counts=True)
    # count=1 → genuinely unpaired face (solid exterior, or standalone shell)
    # count=2 + shell face → shell is bonded to a solid surface face; show the
    #   shell and let the coincident solid face stay hidden (count=2 → False)
    # count≥3 + shell face → shell is an internal rib between multiple solid
    #   elements; keep it hidden (same as before)
    is_shell_face = np.isin(ec, list(SHELL_ELEM_CODES))
    is_surface = (counts[inv] == 1) | (is_shell_face & (counts[inv] == 2))

    return fnc, er, fs, ec, es, is_surface


# ─── Line element collection ──────────────────────────────────────────────────

def collect_lines(geom_h5, coords_global):
    """
    Collect beam/truss line elements and return endpoint positions.

    Returns:
        positions   [N, 2, 3] float32  — N segments, 2 endpoints, xyz
        elem_labels [N]       int32    — Abaqus element label per segment
        node_rows   [N, 2]    int32    — geometry node row per endpoint
            (row into nodes/labels / coords_global; lets L3 look up per-node
            displacement so the beams follow the deformed shape)
    """
    pos_list   = []
    label_list = []
    row_list   = []

    for etype_str in geom_h5.get("elements", {}):
        etype_code = _resolve_elem_code(etype_str)
        if etype_code not in LINE_ELEM_CODES:
            continue
        grp = geom_h5["elements/{}".format(etype_str)]
        if "conn" not in grp or "labels" not in grp:
            continue

        conn   = grp["conn"][:]    # [N, n_nodes] int32 — node rows
        labels = grp["labels"][:]  # [N] int32

        # Only the 2 corner endpoint nodes regardless of element order
        endpoint_rows = conn[:, :2]                 # [N, 2] node rows
        pts = coords_global[endpoint_rows]          # [N, 2, 3]
        pos_list.append(pts)
        label_list.append(labels)
        row_list.append(endpoint_rows)

    if not pos_list:
        return (np.zeros((0, 2, 3), dtype=np.float32),
                np.zeros(0, dtype=np.int32),
                np.zeros((0, 2), dtype=np.int32))

    return (np.vstack(pos_list).astype(np.float32),
            np.concatenate(label_list).astype(np.int32),
            np.vstack(row_list).astype(np.int32))


def collect_points(geom_h5, coords_global):
    """
    Collect MASS/ROTARYI point elements.

    Returns:
        positions   [N, 3] float32  — node position per point element
        elem_labels [N]    int32    — Abaqus element label
        node_rows   [N]    int32    — geometry node row per point (for deform)
    """
    pos_list   = []
    label_list = []
    row_list   = []

    for etype_str in geom_h5.get("elements", {}):
        etype_code = _resolve_elem_code(etype_str)
        if etype_code not in POINT_ELEM_CODES:
            continue
        grp = geom_h5["elements/{}".format(etype_str)]
        if "conn" not in grp or "labels" not in grp:
            continue
        conn   = grp["conn"][:]    # [N, 1] int32 — node row
        labels = grp["labels"][:]  # [N] int32
        rows   = conn[:, 0]        # [N] node rows
        pts = coords_global[rows]  # [N, 3]
        pos_list.append(pts)
        label_list.append(labels)
        row_list.append(rows)

    if not pos_list:
        return (np.zeros((0, 3), dtype=np.float32),
                np.zeros(0, dtype=np.int32),
                np.zeros(0, dtype=np.int32))

    return (np.vstack(pos_list).astype(np.float32),
            np.concatenate(label_list).astype(np.int32),
            np.concatenate(row_list).astype(np.int32))


def collect_couplings(geom_h5):
    """
    Pass through coupling (RBE2/KINEMATIC) line segments written by the INP
    exporter (INP path) or l1_pack (ODB path).

    Returns:
        positions [N*2, 3] float32 — interleaved (ref, slave) pairs
        node_rows [N*2]    int32   — geometry node row per endpoint (for deform),
            or empty when the source predates the node_rows change
    """
    if "couplings/positions" not in geom_h5:
        return (np.zeros((0, 3), dtype=np.float32),
                np.zeros(0, dtype=np.int32))
    positions = geom_h5["couplings/positions"][:]  # already [N*2, 3] float32
    if "couplings/node_rows" in geom_h5:
        node_rows = geom_h5["couplings/node_rows"][:].astype(np.int32)
    else:
        node_rows = np.zeros(0, dtype=np.int32)
    return positions, node_rows


# ─── Triangulation (vectorized for tri + quad, loop for higher) ───────────────

def triangulate(fnc, elem_rows, face_seqs, etype_codes, etype_strs, is_surface):
    """
    Returns:
        tri_nodes   [T, 3] int32
        tri_er      [T]    int32
        tri_fs      [T]    int32
        tri_ec      [T]    uint8
        tri_es      [T]    S8
        tri_is_surf [T]    bool
    """
    valid = fnc != -1
    n_nodes = valid.sum(axis=1)   # [Tf]

    groups = []   # each entry: (nodes_3col, er, fs, ec, es, is_s)

    # --- Triangular faces: 1 tri each ---
    m3 = n_nodes == 3
    if m3.any():
        groups.append((fnc[m3][:, :3], elem_rows[m3], face_seqs[m3],
                        etype_codes[m3], etype_strs[m3], is_surface[m3]))

    # --- Quad faces: 2 tris each (vectorized) ---
    m4 = n_nodes == 4
    if m4.any():
        q   = fnc[m4][:, :4]
        n1  = np.column_stack([q[:, 0], q[:, 1], q[:, 2]])
        n2  = np.column_stack([q[:, 0], q[:, 2], q[:, 3]])
        nds = np.vstack([n1, n2])
        rep = np.tile
        groups.append((nds,
                        rep(elem_rows[m4], 2), rep(face_seqs[m4], 2),
                        rep(etype_codes[m4], 2), rep(etype_strs[m4], 2),
                        rep(is_surface[m4], 2)))

    # --- Higher polygons: fan triangulation (rare, small loop) ---
    for n in range(5, fnc.shape[1] + 1):
        mn = n_nodes == n
        if not mn.any():
            continue
        poly = fnc[mn][:, :n]
        fan_nodes = np.vstack([
            np.column_stack([poly[:, 0], poly[:, i], poly[:, i + 1]])
            for i in range(1, n - 1)
        ])
        reps = n - 2
        groups.append((fan_nodes,
                        np.tile(elem_rows[mn], reps),
                        np.tile(face_seqs[mn], reps),
                        np.tile(etype_codes[mn], reps),
                        np.tile(etype_strs[mn], reps),
                        np.tile(is_surface[mn], reps)))

    if not groups:
        return (np.zeros((0, 3), dtype=np.int32),
                *[np.zeros(0, dtype=t) for t in
                  [np.int32, np.int32, np.uint8, "S8", bool]])

    return (
        np.vstack([g[0] for g in groups]),
        np.concatenate([g[1] for g in groups]),
        np.concatenate([g[2] for g in groups]),
        np.concatenate([g[3] for g in groups]),
        np.concatenate([g[4] for g in groups]),
        np.concatenate([g[5] for g in groups]),
    )


# ─── Indexed geometry (vectorized by face size) ───────────────────────────────

def build_indexed_geometry(coords_global, surf_fnc,
                            surf_elem_rows, surf_face_seqs,
                            surf_etype_codes, surf_etype_strs):
    """
    Build indexed render buffer.  Vertices are shared within an element face
    but NOT across different faces (element boundaries remain hard edges).
    Normals are NOT stored — frontend calls computeVertexNormals() instead.

    surf_fnc:           [Sf, W]   int32   surface face node rows (-1 = pad)
    surf_elem_rows:     [Sf]      int32
    surf_face_seqs:     [Sf]      uint8
    surf_etype_codes:   [Sf]      uint8
    surf_etype_strs:    [Sf]      S8

    Returns:
        positions:    [Nv, 3]  float32
        indices:      [Nt, 3]  int32
        vtx_node_row: [Nv]     int32    FEM node row for each vertex
        vtx_tri_idx:  [Nv]     int32    first triangle index for each vertex
        tri_elem_row: [Nt]     int32
        tri_face_seq: [Nt]     uint8
        tri_etype_code:[Nt]    uint8
        tri_etype_str: [Nt]    S8
    """
    Sf = len(surf_fnc)
    _empty = lambda d: np.zeros(0, dtype=d)
    if Sf == 0:
        return (np.zeros((0, 3), np.float32),    # positions
                np.zeros((0, 3), np.int32),      # indices
                _empty(np.int32),                # vtx_node_row
                _empty(np.int32),                # vtx_tri_idx
                _empty(np.int32),                # tri_elem_row
                _empty(np.uint8),                # tri_face_seq
                _empty(np.uint8),                # tri_etype_code
                _empty("S8"))                    # tri_etype_str

    valid_mask   = surf_fnc != -1
    n_per_face   = valid_mask.sum(axis=1).astype(np.int32)   # [Sf]
    n_tris_pf    = (n_per_face - 2).astype(np.int32)          # fan tri count per face

    vtx_starts = np.zeros(Sf + 1, dtype=np.int64)
    vtx_starts[1:] = np.cumsum(n_per_face)
    tri_starts = np.zeros(Sf + 1, dtype=np.int64)
    tri_starts[1:] = np.cumsum(n_tris_pf)

    Nv = int(vtx_starts[-1])
    Nt = int(tri_starts[-1])

    positions     = np.empty((Nv, 3), dtype=np.float32)
    indices       = np.empty((Nt, 3), dtype=np.int32)
    vtx_node_row  = np.empty(Nv, dtype=np.int32)
    vtx_tri_idx   = np.empty(Nv, dtype=np.int32)
    tri_elem_row  = np.empty(Nt, dtype=np.int32)
    tri_face_seq  = np.empty(Nt, dtype=np.uint8)
    tri_etype_code= np.empty(Nt, dtype=np.uint8)
    tri_etype_str = np.empty(Nt, dtype="S8")

    for n in np.unique(n_per_face):
        n = int(n)
        mask     = n_per_face == n
        face_idx = np.where(mask)[0]   # [K]
        K        = len(face_idx)
        n_tris   = n - 2

        fnc_n  = surf_fnc[face_idx][:, :n]         # [K, n] node rows

        vs = vtx_starts[face_idx]                   # [K] vertex buffer starts
        ts = tri_starts[face_idx]                   # [K] triangle buffer starts

        # ── Fill vertex buffer ───────────────────────────────────────────────
        flat_vtx = (vs[:, None] + np.arange(n, dtype=np.int64)[None, :]).ravel()
        node_rows = fnc_n.ravel().astype(np.int32)

        positions[flat_vtx]    = coords_global[node_rows]
        vtx_node_row[flat_vtx] = node_rows
        # All verts of face f get the first triangle of f as their tri reference
        vtx_tri_idx[flat_vtx]  = np.repeat(ts.astype(np.int32), n)

        # ── Fill index buffer (fan triangulation) ────────────────────────────
        if n_tris > 0:
            local = np.zeros((n_tris, 3), dtype=np.int32)
            local[:, 1] = np.arange(1, n_tris + 1, dtype=np.int32)
            local[:, 2] = np.arange(2, n_tris + 2, dtype=np.int32)

            flat_tri  = (ts[:, None] + np.arange(n_tris, dtype=np.int64)[None, :]).ravel()
            tiled     = np.tile(local, (K, 1))                      # [K*n_tris, 3]
            vs_rep    = np.repeat(vs.astype(np.int32), n_tris)[:, None]
            indices[flat_tri] = tiled + vs_rep

            # Per-triangle metadata (same for all tris of a face)
            tri_elem_row[flat_tri]   = np.repeat(surf_elem_rows[face_idx],   n_tris)
            tri_face_seq[flat_tri]   = np.repeat(surf_face_seqs[face_idx],   n_tris)
            tri_etype_code[flat_tri] = np.repeat(surf_etype_codes[face_idx], n_tris)
            tri_etype_str[flat_tri]  = np.repeat(surf_etype_strs[face_idx],  n_tris)

    return (positions, indices,
            vtx_node_row, vtx_tri_idx,
            tri_elem_row, tri_face_seq, tri_etype_code, tri_etype_str)


# ─── High-order (mid-node) face subdivision ──────────────────────────────────
#
# Quadratic elements (C3D20/C3D10/C3D15, quadratic shells) carry edge mid-nodes
# that often hold the field extrema.  The corner-only face pipeline above never
# samples them, so the cloud map and legend disagree with Abaqus.  Here each
# high-order *surface* face is subdivided into smaller triangles whose vertices
# include the edge mid-nodes (A2: boundary nodes only, no synthesized face
# centre).  See docs/l2/HighOrder-Midside-Subdivision-Design.md.
#
# Boundary column sequences index columns of the FULL connectivity (conn_full;
# corner nodes first, then edge mid-nodes — Abaqus node ordering), laid out as
# [corner0, mid01, corner1, mid12, ...] around the face perimeter.

# Solid high-order faces, keyed by ELEM_TYPE_CODE, indexed by face_seq-1
# (face_seq matches the order in src/l1/abaqus_dump.py FACE_DEFS).
FACE_DEFS_FULL = {
    # C3D10 tet: 4 six-node triangular faces
    5: [
        [0, 4, 1, 5, 2, 6],
        [0, 7, 3, 8, 1, 4],
        [1, 8, 3, 9, 2, 5],
        [2, 9, 3, 7, 0, 6],
    ],
    # C3D15 wedge: 2 six-node tri faces + 3 eight-node quad faces
    6: [
        [0, 6, 1, 7, 2, 8],
        [3, 11, 5, 10, 4, 9],
        [0, 6, 1, 13, 4, 9, 3, 12],
        [1, 7, 2, 14, 5, 10, 4, 13],
        [2, 8, 0, 12, 3, 11, 5, 14],
    ],
    # C3D20 hex: 6 eight-node quad faces
    7: [
        [0, 8, 1, 9, 2, 10, 3, 11],
        [4, 12, 5, 13, 6, 14, 7, 15],
        [0, 8, 1, 17, 5, 12, 4, 16],
        [1, 9, 2, 18, 6, 13, 5, 17],
        [2, 10, 3, 19, 7, 14, 6, 18],
        [3, 11, 0, 16, 4, 15, 7, 19],
    ],
}

# Quadratic shells have a single face = the element itself.  Keyed by n_full
# (full node count) because shell ELEM_TYPE_CODE (0=tri, 1=quad, 8=STRI65) is
# shared with linear shells; only quadratic shells own a conn_full.
SHELL_FULL_FACE_BY_NFULL = {
    6: [0, 3, 1, 4, 2, 5],        # S6 / STRI65: 3 corners + 3 edge mids
    8: [0, 4, 1, 5, 2, 6, 3, 7],  # S8R: 4 corners + 4 edge mids
}


def _full_boundary_cols(etype_code, face_seq, n_full):
    """conn_full boundary column sequence for one high-order face, or None if
    this (code, face_seq) is not a recognized high-order face."""
    defs = FACE_DEFS_FULL.get(int(etype_code))
    if defs is not None:
        if 1 <= face_seq <= len(defs):
            return defs[face_seq - 1]
        return None
    return SHELL_FULL_FACE_BY_NFULL.get(int(n_full))


def _subdivide_boundary_cols(boundary):
    """Subdivide a quadratic face boundary [c0,m0,c1,m1,...] into mid-node
    triangles (boundary nodes only).  Returns a list of (a,b,c) column-index
    triples, or None if the corner count is unsupported."""
    nc = len(boundary) // 2
    if nc == 3:
        c0, m0, c1, m1, c2, m2 = boundary
        return [(c0, m0, m2), (m0, c1, m1), (m2, m1, c2), (m0, m1, m2)]
    if nc == 4:
        c0, m0, c1, m1, c2, m2, c3, m3 = boundary
        return [(c0, m0, m3), (m0, c1, m1), (m1, c2, m2),
                (m2, c3, m3), (m0, m1, m3), (m1, m2, m3)]
    return None


def build_render_faces(surf_fnc, surf_elem_rows, surf_face_seqs,
                       surf_etype_codes, surf_etype_strs,
                       conn_full_rows_by_etype):
    """Expand high-order surface faces into mid-node subdivided triangles.

    Linear faces pass through unchanged (corner node rows).  Each high-order
    face is replaced by several 3-node triangles whose vertices are real FEM
    nodes including edge mid-nodes, so mid-node extrema reach the cloud map.

    conn_full_rows_by_etype maps etype_str -> [N_elem, n_full] node rows; an
    etype absent from it is treated as linear.

    Returns render_fnc [Rf, W] int32 (-1 pad) plus parallel render_elem_rows,
    render_face_seqs, render_etype_codes, render_etype_strs.
    """
    if len(surf_fnc) == 0 or not conn_full_rows_by_etype:
        return (surf_fnc, surf_elem_rows, surf_face_seqs,
                surf_etype_codes, surf_etype_strs)

    W = surf_fnc.shape[1]
    fnc_parts, er_parts, fs_parts, ec_parts, es_parts = [], [], [], [], []

    # Which faces are high-order (etype owns a full connectivity)?
    is_ho = np.zeros(len(surf_fnc), dtype=bool)
    for etype_bytes in np.unique(surf_etype_strs):
        etype_str = etype_bytes.decode("ascii").rstrip("\x00")
        if etype_str in conn_full_rows_by_etype:
            is_ho |= (surf_etype_strs == etype_bytes)

    # ── Linear faces: pass through unchanged ──────────────────────────────
    lin = ~is_ho
    if lin.any():
        fnc_parts.append(surf_fnc[lin])
        er_parts.append(surf_elem_rows[lin])
        fs_parts.append(surf_face_seqs[lin])
        ec_parts.append(surf_etype_codes[lin])
        es_parts.append(surf_etype_strs[lin])

    # ── High-order faces: subdivide per (etype, face_seq) group ───────────
    ho_etypes = np.unique(surf_etype_strs[is_ho]) if is_ho.any() else []
    for etype_bytes in ho_etypes:
        etype_str = etype_bytes.decode("ascii").rstrip("\x00")
        cfr    = conn_full_rows_by_etype[etype_str]   # [N_elem, n_full]
        n_full = cfr.shape[1]
        et_mask = is_ho & (surf_etype_strs == etype_bytes)
        codes_here = surf_etype_codes[et_mask]
        code = int(codes_here[0]) if len(codes_here) else -1

        for fs in np.unique(surf_face_seqs[et_mask]):
            grp      = et_mask & (surf_face_seqs == fs)
            erows    = surf_elem_rows[grp]                  # [G]
            boundary = _full_boundary_cols(code, int(fs), n_full)
            subtris  = _subdivide_boundary_cols(boundary) if boundary else None
            if subtris is None:
                # Unknown high-order face → keep corner face as-is.
                fnc_parts.append(surf_fnc[grp])
                er_parts.append(erows)
                fs_parts.append(surf_face_seqs[grp])
                ec_parts.append(surf_etype_codes[grp])
                es_parts.append(surf_etype_strs[grp])
                continue
            rows = cfr[erows]                              # [G, n_full] node rows
            for (a, b, c) in subtris:
                tri = np.full((len(erows), W), -1, dtype=np.int32)
                tri[:, 0] = rows[:, a]
                tri[:, 1] = rows[:, b]
                tri[:, 2] = rows[:, c]
                fnc_parts.append(tri)
                er_parts.append(erows)
                fs_parts.append(np.full(len(erows), fs, dtype=surf_face_seqs.dtype))
                ec_parts.append(np.full(len(erows), code, dtype=surf_etype_codes.dtype))
                es_parts.append(np.full(len(erows), etype_bytes, dtype=surf_etype_strs.dtype))

    render_fnc = np.vstack(fnc_parts) if fnc_parts else surf_fnc[:0]
    render_er  = np.concatenate(er_parts) if er_parts else surf_elem_rows[:0]
    render_fs  = np.concatenate(fs_parts) if fs_parts else surf_face_seqs[:0]
    render_ec  = np.concatenate(ec_parts) if ec_parts else surf_etype_codes[:0]
    render_es  = np.concatenate(es_parts) if es_parts else surf_etype_strs[:0]
    return render_fnc, render_er, render_fs, render_ec, render_es


# ─── Averaging domain helpers ────────────────────────────────────────────────

# elem_kind codes
ELEM_KIND_SOLID    = np.uint8(0)
ELEM_KIND_SHELL    = np.uint8(1)
ELEM_KIND_MEMBRANE = np.uint8(2)

# etype prefix → elem_kind
def classify_elem_kind(etype_str):
    """Return ELEM_KIND_* for a given etype string."""
    if etype_str.startswith(("S3", "S4", "S6", "S8", "STRI", "SC")):
        return ELEM_KIND_SHELL
    if etype_str.startswith(("M3D", "MCL")):
        return ELEM_KIND_MEMBRANE
    return ELEM_KIND_SOLID


def compute_section_ids(geom_h5, surf_global_elem_idx,
                        surf_unique_etypes, surf_unique_erows,
                        etype_labels_cache):
    """
    Assign a section_id (1-based int32) to each unique surface element.

    surf_unique_etypes [E]  S8    etype string per unique surface element
    surf_unique_erows  [E]  int32 elem_row per unique surface element
    etype_labels_cache       dict  etype_str -> labels [N_elem] int32

    Returns:
        section_id   [E]  int32     0 = unassigned
        section_names     list[str] index+1 = section_id (1-based)
    """
    E = len(surf_unique_erows)
    section_id = np.zeros(E, dtype=np.int32)

    if "sections" not in geom_h5:
        return section_id, []

    # Build label → section_id map across all sections
    section_names = []          # 0-based; section_id = index + 1
    label_to_sid  = {}

    for sec_name in geom_h5["sections"]:
        grp   = geom_h5["sections/{}".format(sec_name)]
        eset  = grp.attrs.get("element_set", "")
        key   = "instance_sets/element_sets/{}".format(safe(eset))
        if not eset or key not in geom_h5:
            continue
        sid = len(section_names) + 1       # 1-based
        section_names.append(sec_name)
        for lbl in geom_h5[key][:]:
            label_to_sid[int(lbl)] = sid

    if not label_to_sid:
        return section_id, section_names

    # Map each unique surface element to its label, then look up section_id
    for ei in range(E):
        etype_bytes = surf_unique_etypes[ei]
        etype_str   = etype_bytes.decode("ascii").rstrip("\x00")
        labels      = etype_labels_cache.get(etype_str)
        if labels is None:
            continue
        erow = int(surf_unique_erows[ei])
        if erow < len(labels):
            lbl = int(labels[erow])
            section_id[ei] = label_to_sid.get(lbl, 0)

    return section_id, section_names


def build_domain_ids(section_id, elem_kind, adj_src, adj_dst, adj_angle_deg,
                     feature_angle_deg=20.0):
    """
    Compute averaging domain IDs from a SciPy sparse adjacency graph.

    Step 1: Each (section_id group) starts as seed.
    Step 2: For shell/membrane elements within the same section,
            union adjacent elements whose face-normal angle <= feature_angle_deg.
            Solid elements are not further split by angle.

    Returns:
        domain_id [E] int32   globally unique domain IDs (0-based)
    """
    E = len(section_id)
    if E == 0:
        return np.zeros(0, dtype=np.int32)

    src = np.asarray(adj_src, dtype=np.int64)
    dst = np.asarray(adj_dst, dtype=np.int64)
    angle = np.asarray(adj_angle_deg)
    valid = ((src >= 0) & (src < E) & (dst >= 0) & (dst < E))

    # Index only valid rows before looking up element metadata. This also keeps
    # malformed external adjacency data from indexing past the arrays.
    valid_rows = np.flatnonzero(valid)
    if len(valid_rows):
        va = src[valid_rows]
        vb = dst[valid_rows]
        same_section = section_id[va] == section_id[vb]
        ka = elem_kind[va]
        kb = elem_kind[vb]
        solid_pair = ((ka == ELEM_KIND_SOLID) &
                      (kb == ELEM_KIND_SOLID))
        shell_like_a = ((ka == ELEM_KIND_SHELL) |
                        (ka == ELEM_KIND_MEMBRANE))
        shell_like_b = ((kb == ELEM_KIND_SHELL) |
                        (kb == ELEM_KIND_MEMBRANE))
        shell_pair = (shell_like_a & shell_like_b &
                      (angle[valid_rows] <= feature_angle_deg))
        keep = same_section & (solid_pair | shell_pair)
        src_keep = va[keep]
        dst_keep = vb[keep]
    else:
        src_keep = np.zeros(0, dtype=np.int64)
        dst_keep = np.zeros(0, dtype=np.int64)

    rows = np.concatenate((src_keep, dst_keep))
    cols = np.concatenate((dst_keep, src_keep))
    graph = coo_matrix(
        (np.ones(len(rows), dtype=np.uint8), (rows, cols)),
        shape=(E, E),
    ).tocsr()
    _, labels = connected_components(
        graph, directed=False, return_labels=True)

    # SciPy component labels are implementation details. Relabel by each
    # component's first element so IDs remain deterministic and compatible
    # with the previous Union-Find output convention.
    _, first_indices = np.unique(labels, return_index=True)
    first_indices.sort()
    label_to_domain = np.empty(E, dtype=np.int32)
    label_to_domain[labels[first_indices]] = np.arange(
        len(first_indices), dtype=np.int32)
    return label_to_domain[labels]


def build_surface_edge_topology(surf_tri_nodes, coords_global=None):
    """Build one reusable edge table for adjacency and both edge overlays."""
    face_count = len(surf_tri_nodes)
    if face_count == 0:
        return {
            "edge_nodes": np.zeros((0, 2), dtype=np.int32),
            "counts": np.zeros(0, dtype=np.int64),
            "starts": np.zeros(0, dtype=np.int64),
            "sorted_faces": np.zeros(0, dtype=np.int32),
            "face_normals": np.zeros((0, 3), dtype=np.float64),
        }

    edge_nodes = np.vstack((
        np.sort(surf_tri_nodes[:, [0, 1]], axis=1),
        np.sort(surf_tri_nodes[:, [1, 2]], axis=1),
        np.sort(surf_tri_nodes[:, [2, 0]], axis=1),
    )).astype(np.int32, copy=False)
    edge_faces = np.tile(np.arange(face_count, dtype=np.int32), 3)
    order = np.lexsort((edge_nodes[:, 1], edge_nodes[:, 0]))
    sorted_edges = edge_nodes[order]
    sorted_faces = edge_faces[order]
    starts = np.flatnonzero(np.r_[
        True,
        (sorted_edges[1:, 0] != sorted_edges[:-1, 0]) |
        (sorted_edges[1:, 1] != sorted_edges[:-1, 1]),
    ])
    counts = np.diff(np.r_[starts, len(sorted_edges)])

    if coords_global is None:
        face_normals = np.zeros((face_count, 3), dtype=np.float64)
    else:
        p0 = coords_global[surf_tri_nodes[:, 0]].astype(np.float64)
        p1 = coords_global[surf_tri_nodes[:, 1]].astype(np.float64)
        p2 = coords_global[surf_tri_nodes[:, 2]].astype(np.float64)
        face_normals = np.cross(p1 - p0, p2 - p0)
        norms = np.linalg.norm(face_normals, axis=1, keepdims=True)
        face_normals /= np.where(norms > 1e-12, norms, 1.0)

    return {
        "edge_nodes": sorted_edges[starts],
        "counts": counts,
        "starts": starts,
        "sorted_faces": sorted_faces,
        "face_normals": face_normals,
    }


def _shared_edge_face_pairs(topology):
    """Return face pairs and their unique-edge group in stable edge order."""
    counts = topology["counts"]
    starts = topology["starts"]
    sorted_faces = topology["sorted_faces"]
    shared_groups = np.flatnonzero(counts >= 2)
    if len(shared_groups) == 0:
        empty = np.zeros(0, dtype=np.int32)
        return empty, empty, empty

    face_a_parts = []
    face_b_parts = []
    group_parts = []
    pair_groups = shared_groups[counts[shared_groups] == 2]
    if len(pair_groups):
        pair_starts = starts[pair_groups]
        face_a_parts.append(sorted_faces[pair_starts])
        face_b_parts.append(sorted_faces[pair_starts + 1])
        group_parts.append(pair_groups.astype(np.int32, copy=False))

    for group_idx in shared_groups[counts[shared_groups] > 2]:
        start = starts[group_idx]
        count = counts[group_idx]
        left, right = np.triu_indices(count, k=1)
        face_a_parts.append(sorted_faces[start + left])
        face_b_parts.append(sorted_faces[start + right])
        group_parts.append(np.full(len(left), group_idx, dtype=np.int32))

    face_a = np.concatenate(face_a_parts)
    face_b = np.concatenate(face_b_parts)
    groups = np.concatenate(group_parts)
    pair_order = np.argsort(groups, kind="stable")
    return face_a[pair_order], face_b[pair_order], groups[pair_order]


def compute_elem_adjacency_from_topology(topology, tri_global_elem_idx):
    """Build element adjacency from a shared triangle-edge topology."""
    face_a, face_b, _ = _shared_edge_face_pairs(topology)
    if len(face_a) == 0:
        return (np.zeros(0, np.int32), np.zeros(0, np.int32),
                np.zeros(0, np.float32))

    adj_src = tri_global_elem_idx[face_a].astype(np.int32, copy=False)
    adj_dst = tri_global_elem_idx[face_b].astype(np.int32, copy=False)
    different_elements = adj_src != adj_dst
    adj_src = adj_src[different_elements]
    adj_dst = adj_dst[different_elements]
    face_a = face_a[different_elements]
    face_b = face_b[different_elements]
    if len(adj_src) == 0:
        return (np.zeros(0, np.int32), np.zeros(0, np.int32),
                np.zeros(0, np.float32))

    normals = topology["face_normals"]
    dots = np.einsum("ij,ij->i", normals[face_a], normals[face_b])
    angles = np.degrees(np.arccos(np.clip(dots, -1.0, 1.0)))
    return adj_src, adj_dst, angles.astype(np.float32)


def compute_elem_adjacency(surf_fnc, surf_global_elem_idx, coords_global):
    """
    Build surface element adjacency graph with dihedral angles.

    For each pair of surface elements sharing a face edge:
      - record (global_elem_idx_a, global_elem_idx_b)
      - record the angle in degrees between their face normals

    surf_fnc             [Sf, W]  int32  face node rows (-1 = pad)
    surf_global_elem_idx [Sf]     int32  global element index per surface face
    coords_global        [N, 3]   float32

    Returns:
        adj_src       [A]  int32    global_elem_idx of element a
        adj_dst       [A]  int32    global_elem_idx of element b
        adj_angle_deg [A]  float32  angle between face normals (degrees)
    """
    face_count = len(surf_fnc)
    if face_count == 0:
        return (np.zeros(0, np.int32), np.zeros(0, np.int32),
                np.zeros(0, np.float32))

    tri_parts = []
    owner_parts = []
    for face_idx, row in enumerate(surf_fnc):
        width = int(np.count_nonzero(row != -1))
        for column in range(1, width - 1):
            tri_parts.append(row[[0, column, column + 1]][None, :])
            owner_parts.append(np.array([face_idx], dtype=np.int32))
    tri_nodes = np.vstack(tri_parts).astype(np.int32, copy=False)
    tri_owner = np.concatenate(owner_parts)
    topology = build_surface_edge_topology(tri_nodes, coords_global)
    return compute_elem_adjacency_from_topology(
        topology, np.asarray(surf_global_elem_idx)[tri_owner])


# ─── source_local_node_idx ────────────────────────────────────────────────────

def compute_source_local_node_idx(tri_etype_str, tri_elem_row,
                                   vtx_node_row, render_indices,
                                   conn_by_etype):
    """
    For each render triangle and each of its 3 corners, find the local node
    index of that corner within the source element's connectivity array.

    conn_by_etype: dict  etype_str -> [N_elem, n_corner] int32

    Returns:
        source_local_node_idx [Nt, 3] int16
    """
    Nt = len(tri_elem_row)
    out = np.zeros((Nt, 3), dtype=np.int16)
    if Nt == 0:
        return out

    # node rows for every triangle corner: [Nt, 3]
    tri_node_rows = vtx_node_row[render_indices]   # [Nt, 3]

    for etype_bytes in np.unique(tri_etype_str):
        etype_str = etype_bytes.decode("ascii").rstrip("\x00")
        conn = conn_by_etype.get(etype_str)
        if conn is None:
            continue

        mask  = tri_etype_str == etype_bytes        # [Nt] bool
        t_idx = np.where(mask)[0]                   # triangle rows for this etype
        if len(t_idx) == 0:
            continue

        e_rows = tri_elem_row[t_idx]                # [T_e]
        n_rows = tri_node_rows[t_idx]               # [T_e, 3]
        e_conn = conn[e_rows]                        # [T_e, n_corner]

        # [T_e, 3, n_corner] broadcast equality → argmax gives first matching col
        match  = (n_rows[:, :, None] == e_conn[:, None, :])   # [T_e, 3, n_corner]
        out[t_idx] = np.argmax(match, axis=2).astype(np.int16)

    return out


# ─── Feature edges (vectorized) ───────────────────────────────────────────────

def compute_feature_edges(surf_tri_nodes, coords_global,
                          fold_angle_deg=FOLD_ANGLE_DEG, topology=None):
    """
    Returns:
        edge_nodes [E, 2] int32    sorted node row pairs
        edge_types [E]    uint8    1=boundary, 2=fold
    """
    if len(surf_tri_nodes) == 0:
        return np.zeros((0, 2), dtype=np.int32), np.zeros(0, dtype=np.uint8)
    topology = topology or build_surface_edge_topology(
        surf_tri_nodes, coords_global)
    counts = topology["counts"]
    unique_edge_nodes = topology["edge_nodes"]

    edge_types = np.zeros(len(counts), dtype=np.uint8)

    # Boundary edges
    is_boundary = counts == 1
    edge_types[is_boundary] = 1

    # Fold edges (shared by exactly 2 triangles)
    is_shared = counts == 2
    if is_shared.any():
        cos_thresh = np.cos(np.radians(fold_angle_deg))
        shared_idx = np.where(is_shared)[0]             # unique edge indices
        starts = topology["starts"]
        sorted_faces = topology["sorted_faces"]
        face_a = sorted_faces[starts[shared_idx]]
        face_b = sorted_faces[starts[shared_idx] + 1]

        # Vectorized angle check
        na = topology["face_normals"][face_a]
        nb = topology["face_normals"][face_b]
        cos_angle = (na * nb).sum(axis=1)
        edge_types[shared_idx[cos_angle < cos_thresh]] = 2

    # Edges shared by 3+ triangles sit at geometric junctions (e.g. two bonded
    # shells meeting at a solid side face) — always feature edges, same root
    # cause as the count>=2 fix in compute_all_surface_edges.
    edge_types[counts >= 3] = 1

    # Return only boundary + fold edges (skip interior smooth edges)
    keep = edge_types > 0
    return unique_edge_nodes[keep], edge_types[keep]


# ─── Element mesh edges (vectorized) ─────────────────────────────────────────

def compute_all_surface_edges(surf_tri_nodes, surf_tri_er, surf_tri_fs,
                              surf_tri_ec, topology=None):
    """
    Compute element_mesh_edges: every real surface unit boundary edge.

    Unlike feature_edges, no angle filter is applied — all shared edges between
    different element faces are retained, including co-planar ones.

    A shared edge (count == 2) is a triangulation diagonal if both its triangles
    belong to the SAME original element face: same (etype_code, elem_row, face_seq).
    Quads are split into 2 triangles sharing one diagonal — that diagonal must be
    excluded, or the wireframe would show phantom lines cutting through quad faces.

    elem_row is 0-based per element type, so etype_code must be included in the
    diagonal key to avoid false matches between different element types.

    Returns:
        edge_nodes [E, 2] int32   sorted node row pairs
    """
    if len(surf_tri_nodes) == 0:
        return np.zeros((0, 2), dtype=np.int32)
    topology = topology or build_surface_edge_topology(surf_tri_nodes)
    counts = topology["counts"]
    starts = topology["starts"]
    sorted_faces = topology["sorted_faces"]
    unique_edge_nodes = topology["edge_nodes"]

    # Boundary edges are always real
    keep = counts == 1

    # Shared edges (count >= 2): keep unless it is a triangulation diagonal.
    # A diagonal comes from splitting a quad face into 2 triangles: both triangles
    # belong to the SAME face → same (etype_code, elem_row, face_seq).
    # This can only produce count==2 from that face (one tri on each side of diagonal).
    # If count > 2, at least one extra triangle comes from a DIFFERENT face → real edge.
    #
    # Example where count==3 occurs: two bonded shell elements (Shell_A, Shell_B)
    # sharing an edge that is also shared by an adjacent solid side face.
    # The edge appears once per shell element face + once from the solid side face = 3.
    # Old code (counts==2 only) would silently drop these → interior shell grid lines missing.
    is_shared = counts >= 2
    if is_shared.any():
        shared_idx   = np.where(is_shared)[0]
        face_a = sorted_faces[starts[shared_idx]]
        face_b = sorted_faces[starts[shared_idx] + 1]

        # Diagonal check only applies when count==2; count>2 can never be pure diagonal.
        is_count2    = counts[shared_idx] == 2
        is_diagonal  = is_count2 & (
            (surf_tri_ec[face_a] == surf_tri_ec[face_b]) &
            (surf_tri_er[face_a] == surf_tri_er[face_b]) &
            (surf_tri_fs[face_a] == surf_tri_fs[face_b])
        )
        keep[shared_idx[~is_diagonal]] = True

    return unique_edge_nodes[keep]


# ─── Octree ───────────────────────────────────────────────────────────────────

def build_octree(tri_positions, max_depth=OCTREE_MAX_DEPTH,
                  leaf_threshold=OCTREE_LEAF_THRESHOLD):
    """
    Build octree over [Rf, 3, 3] triangle positions.
    Stores as flat arrays for HDF5:
        node_bbox     [num_nodes, 6]  float32  xmin,ymin,zmin,xmax,ymax,zmax
        node_children [num_nodes, 8]  int32    child node indices (-1=none)
        node_is_leaf  [num_nodes]     uint8
        face_indices  [total]         int32    face rows (leaves only, concatenated)
        leaf_offsets  [num_nodes+1]   int32    start index into face_indices per node
    """
    Rf = len(tri_positions)
    if Rf == 0:
        return {
            "node_bbox":     np.zeros((1, 6), dtype=np.float32),
            "node_children": np.full((1, 8), -1, dtype=np.int32),
            "node_is_leaf":  np.ones(1, dtype=np.uint8),
            "face_indices":  np.zeros(0, dtype=np.int32),
            "leaf_offsets":  np.array([0, 0], dtype=np.int32),
        }

    centroids = tri_positions.mean(axis=1)   # [Rf, 3]

    node_bboxes    = []
    node_children  = []
    node_is_leaf   = []
    leaf_faces_all = []
    leaf_offsets   = [0]

    def _bbox(face_idx):
        pts = tri_positions[face_idx].reshape(-1, 3)
        return np.concatenate([pts.min(axis=0), pts.max(axis=0)])

    def _build(face_idx, depth):
        nid = len(node_bboxes)
        bbox = _bbox(face_idx).astype(np.float32)
        node_bboxes.append(bbox)
        node_children.append(np.full(8, -1, dtype=np.int32))

        if len(face_idx) <= leaf_threshold or depth >= max_depth:
            node_is_leaf.append(np.uint8(1))
            leaf_faces_all.append(face_idx.astype(np.int32))
            leaf_offsets.append(leaf_offsets[-1] + len(face_idx))
        else:
            node_is_leaf.append(np.uint8(0))
            leaf_offsets.append(leaf_offsets[-1])   # internal: no faces
            mid = (bbox[:3] + bbox[3:]) / 2.0
            ctr = centroids[face_idx]
            for ci in range(8):
                xbit = (ci >> 0) & 1
                ybit = (ci >> 1) & 1
                zbit = (ci >> 2) & 1
                mask = (
                    ((ctr[:, 0] >= mid[0]) == bool(xbit)) &
                    ((ctr[:, 1] >= mid[1]) == bool(ybit)) &
                    ((ctr[:, 2] >= mid[2]) == bool(zbit))
                )
                child_faces = face_idx[mask]
                if len(child_faces) > 0:
                    child_id = _build(child_faces, depth + 1)
                    node_children[nid][ci] = child_id

        return nid

    _build(np.arange(Rf, dtype=np.int32), 0)

    return {
        "node_bbox":     np.vstack(node_bboxes),
        "node_children": np.vstack(node_children),
        "node_is_leaf":  np.array(node_is_leaf, dtype=np.uint8),
        "face_indices":  np.concatenate(leaf_faces_all) if leaf_faces_all
                         else np.zeros(0, dtype=np.int32),
        "leaf_offsets":  np.array(leaf_offsets, dtype=np.int32),
    }


# ─── Model-level orientation processing ──────────────────────────────────────

def _compute_orientation_axes(origin, point_a, point_b):
    """
    Compute orthonormal 3×3 axes from origin + two reference points.

    Convention (matches Abaqus *ORIENTATION RECTANGULAR):
      e1 = normalize(point_a - origin)          — local 1-axis
      e3 = normalize(cross(e1, point_b - origin)) — normal to the 1-2 plane
      e2 = cross(e3, e1)                         — local 2-axis
    """
    e1 = point_a - origin
    n1 = np.linalg.norm(e1)
    if n1 < 1e-12:
        return np.eye(3, dtype=np.float64)
    e1 = e1 / n1

    v = point_b - origin
    n = np.cross(e1, v)
    nn = np.linalg.norm(n)
    if nn < 1e-12:
        perp = np.array([1., 0., 0.]) if abs(e1[0]) < 0.9 else np.array([0., 1., 0.])
        n = np.cross(e1, perp)
        nn = np.linalg.norm(n)
    e3 = n / nn
    e2 = np.cross(e3, e1)
    return np.stack([e1, e2, e3])   # [3, 3] float64


def process_orientations(workspace, asm_h5):
    """Read raw orientations from assembly.h5, compute orthonormal axes, write l2/geometry/orientations.h5."""
    if "orientations" not in asm_h5:
        return

    names_out, systems_out, origins_out, axes_out = [], [], [], []
    for key in asm_h5["orientations"]:
        grp    = asm_h5["orientations/{}".format(key)]
        origin  = grp["origin"][:]
        point_a = grp["point_a"][:]
        point_b = grp["point_b"][:]
        system  = grp.attrs.get("system", "RECTANGULAR")
        name    = grp.attrs.get("original_name", key)
        axes    = _compute_orientation_axes(origin, point_a, point_b)
        names_out.append(name)
        systems_out.append(system)
        origins_out.append(origin.astype(np.float32))
        axes_out.append(axes.astype(np.float32))

    if not names_out:
        return

    l2_geom_dir = os.path.join(workspace, "l2", "geometry")
    mkdirs(l2_geom_dir)
    out_path = os.path.join(l2_geom_dir, "orientations.h5")

    N = len(names_out)
    origins_arr = np.stack(origins_out)   # [N, 3]
    axes_arr    = np.stack(axes_out)      # [N, 3, 3]

    with h5py.File(out_path, "w") as f:
        # Fixed-length byte strings — avoids h5py vlen_str version differences
        names_arr   = np.array([n.encode("utf-8")[:127] for n in names_out],   dtype="S128")
        systems_arr = np.array([s.encode("utf-8")[:31]  for s in systems_out], dtype="S32")
        f.create_dataset("names",   data=names_arr)
        f.create_dataset("systems", data=systems_arr)
        f.create_dataset("origins", data=origins_arr)
        f.create_dataset("axes",    data=axes_arr)

    logger.info("Orientations: {} written to {}".format(N, out_path))


# ─── Per-instance processing ──────────────────────────────────────────────────

def process_instance_core(workspace, db_conn, asm_h5, inst_name):
    logger.info("Processing core geometry for instance: {} ...".format(inst_name))
    t0 = time.time()

    row = db_conn.execute(
        "SELECT geom_path FROM instances WHERE instance_name=?", (inst_name,)
    ).fetchone()
    # db_conn has no row_factory, fetchone() returns a plain tuple → row[0]
    geom_path = (os.path.join(workspace, row[0]) if row and row[0]
                 else os.path.join(workspace, "l1", "geometry", "{}.h5".format(inst_name)))
    if not os.path.exists(geom_path):
        logger.warning("  Not found: {}".format(geom_path))
        return

    l2_geom_dir   = os.path.join(workspace, "l2", "geometry")
    l2_render_dir = os.path.join(workspace, "l2", "render")
    mkdirs(l2_geom_dir)
    mkdirs(l2_render_dir)

    surface_h5_path = os.path.join(l2_geom_dir, "{}_surface.h5".format(inst_name))
    render_h5_path  = os.path.join(l2_render_dir, "{}_render.h5".format(inst_name))
    for stale_path in (
        os.path.join(l2_geom_dir, "{}_edges.h5".format(inst_name)),
        os.path.join(l2_render_dir, "{}_advanced.h5".format(inst_name)),
    ):
        if os.path.isfile(stale_path):
            os.remove(stale_path)

    with h5py.File(geom_path, "r") as f_in:
        local_coords = f_in["nodes/coords"][:]
        labels       = f_in["nodes/labels"][:]

        # 1. Global coordinates
        transform    = get_instance_transform(asm_h5, inst_name)
        coords_global = compute_global_coords(local_coords, transform)

        # 2. Face collection + surface detection (vectorized)
        fnc, elem_rows, face_seqs, etype_codes, etype_strs, is_surface = \
            collect_faces(f_in)

        # 2b. Line element collection (beam / truss)
        line_positions, line_elem_labels, line_node_rows = collect_lines(f_in, coords_global)

        # 2c. Point element collection (MASS / ROTARYI)
        point_positions, point_elem_labels, point_node_rows = collect_points(f_in, coords_global)

        # 2d. Coupling lines (RBE2 / KINEMATIC) — written by INP exporter / l1_pack
        coupling_positions, coupling_node_rows = collect_couplings(f_in)

    # Load full (mid-node) connectivity for high-order elements → node rows.
    # conn_full in <inst>_highorder.h5 stores node LABELS; map to rows via the
    # sorted node label array (same convention as L1 label_to_row).
    conn_full_rows_by_etype = {}
    ho_path = (geom_path[:-3] + "_highorder.h5"
               if geom_path.endswith(".h5") else None)
    if ho_path and os.path.exists(ho_path):
        with h5py.File(ho_path, "r") as f_ho:
            for et in f_ho.get("elements", {}):
                g = f_ho["elements/{}".format(et)]
                if "conn_full" not in g:
                    continue
                cf_lbl = g["conn_full"][:]                       # [N_elem, n_full] labels
                conn_full_rows_by_etype[et] = \
                    np.searchsorted(labels, cf_lbl).astype(np.int32)

    # 3. Filter to surface faces (face-level, before triangulation)
    surf_fnc       = fnc[is_surface]
    surf_elem_rows = elem_rows[is_surface]
    surf_face_seqs    = face_seqs[is_surface]
    surf_etype_codes  = etype_codes[is_surface]
    surf_etype_strs   = etype_strs[is_surface]
    Sf_faces = len(surf_fnc)
    logger.info("  {} surface faces".format(Sf_faces))

    # 3b. Expand high-order surface faces into mid-node subdivided triangles.
    #     Linear faces pass through unchanged. Averaging/adjacency below keep
    #     using the corner surf_* arrays (per-element), unaffected by this.
    (render_fnc, render_face_elem_rows, render_face_seqs,
     render_face_etype_codes, render_face_etype_strs) = build_render_faces(
        surf_fnc, surf_elem_rows, surf_face_seqs,
        surf_etype_codes, surf_etype_strs, conn_full_rows_by_etype)

    # 4. Indexed geometry (positions [Nv,3] + indices [Nt,3]; normals computed by frontend)
    (render_positions, render_indices,
     vtx_node_row, vtx_tri_idx,
     tri_elem_row, tri_face_seq, tri_etype_code, tri_etype_str) = \
        build_indexed_geometry(coords_global, render_fnc,
                               render_face_elem_rows, render_face_seqs,
                               render_face_etype_codes, render_face_etype_strs)

    Nv = len(render_positions)
    Nt = len(render_indices)
    logger.info("  {} vertices, {} triangles (indexed)".format(Nv, Nt))

    # surf_tri_nodes [Nt, 3]: node rows per triangle, needed for edge detection
    surf_tri_nodes = vtx_node_row[render_indices] if Nt > 0 else np.zeros((0, 3), np.int32)

    render_face_idx = np.arange(Nt, dtype=np.int32)

    N_lines     = len(line_positions)
    N_points    = len(point_positions)
    N_couplings = len(coupling_positions) // 2  # pairs
    logger.info("  {} line elements (beam/truss)".format(N_lines))
    logger.info("  {} point elements (MASS/ROTARYI)".format(N_points))
    logger.info("  {} coupling segments (RBE2)".format(N_couplings))

    # ── Write l2/geometry/<inst>_surface.h5 ──
    with h5py.File(surface_h5_path, "w") as f:
        ng = f.create_group("nodes")
        ng.create_dataset("labels",        data=labels)
        ng.create_dataset("coords_global", data=coords_global)

        lg = f.create_group("lines")
        if N_lines > 0:
            lg.create_dataset("positions",   data=line_positions,
                              chunks=(min(N_lines, 4096), 2, 3), compression="lzf")
            lg.create_dataset("elem_labels", data=line_elem_labels)
            # [N, 2] geometry node rows per endpoint — for deform (U lookup)
            lg.create_dataset("node_rows",   data=line_node_rows)

        pg = f.create_group("points")
        if N_points > 0:
            pg.create_dataset("positions",   data=point_positions)
            pg.create_dataset("elem_labels", data=point_elem_labels)
            # [N] geometry node row per point — for deform (U lookup)
            pg.create_dataset("node_rows",   data=point_node_rows)

        cg = f.create_group("couplings")
        if N_couplings > 0:
            cg.create_dataset("positions", data=np.ascontiguousarray(coupling_positions),
                              chunks=(min(len(coupling_positions), 4096), 3),
                              compression="lzf")
            # [N*2] geometry node rows per endpoint — for deform (U lookup).
            # Only written when the upstream source supplied them (INP exporter
            # or l1_pack ODB path); empty for legacy geometry → no deform overlay.
            if coupling_node_rows.shape[0] == coupling_positions.shape[0]:
                cg.create_dataset("node_rows", data=coupling_node_rows)

    # ── Write l2/render/<inst>_render.h5 ──
    with h5py.File(render_h5_path, "w") as f:
        rg = f.create_group("render")
        chunk_v = (min(Nv, 4096), 3) if Nv > 0 else True
        chunk_t = (min(Nt, 4096), 3) if Nt > 0 else True
        rg.create_dataset("positions",     data=render_positions,
                          chunks=chunk_v, compression="lzf")
        rg.create_dataset("indices",       data=render_indices,
                          chunks=chunk_t, compression="lzf")
        rg.create_dataset("vtx_node_row",  data=vtx_node_row)
        rg.create_dataset("vtx_tri_idx",   data=vtx_tri_idx)
        rg.create_dataset("render_face_idx", data=render_face_idx)
        if Nt > 0:
            rg.create_dataset("source_elem_row",       data=tri_elem_row)
            rg.create_dataset("source_node_rows",      data=surf_tri_nodes)   # [Nt,3] backward compat
            rg.create_dataset("source_face_seq",       data=tri_face_seq)
            rg.create_dataset("source_etype_code",     data=tri_etype_code)
            rg.create_dataset("source_etype_str",      data=tri_etype_str)

    # ── Update manifest.db ──
    db_conn.execute("""
        CREATE TABLE IF NOT EXISTS l2_instances (
            instance_name   TEXT PRIMARY KEY,
            surface_path    TEXT,
            render_path     TEXT,
            surface_face_count  INTEGER,
            render_face_count   INTEGER,
            edge_count          INTEGER,
            partition_count     INTEGER,
            line_count          INTEGER
        )
    """)
    # Migration: add line_count to tables created before this column existed.
    try:
        db_conn.execute("ALTER TABLE l2_instances ADD COLUMN line_count INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
    db_conn.execute("""
        INSERT OR REPLACE INTO l2_instances
        (instance_name, surface_path, render_path,
         surface_face_count, render_face_count, edge_count, partition_count,
         line_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (inst_name,
          "l2/geometry/{}_surface.h5".format(inst_name),
          "l2/render/{}_render.h5".format(inst_name),
          Sf_faces, Nt, 0, 1, N_lines))
    db_conn.commit()

    logger.info("  Done in {:.2f}s".format(time.time() - t0))


def _stable_unique_element_map(etype_arr, elem_row_arr):
    """Return stable unique (etype, row) arrays and item-to-unique mapping."""
    pairs = np.stack([
        etype_arr.view(np.uint64),
        elem_row_arr.astype(np.int64).view(np.uint64),
    ], axis=1)
    pair_bytes = pairs.view(
        np.dtype((np.void, pairs.dtype.itemsize * 2))).ravel()
    _, unique_indices = np.unique(pair_bytes, return_index=True)
    unique_indices.sort()
    unique_bytes = pair_bytes[unique_indices]
    sort_order = np.argsort(unique_bytes)
    positions = np.searchsorted(unique_bytes[sort_order], pair_bytes)
    item_to_unique = sort_order[positions].astype(np.int32)
    return (etype_arr[unique_indices], elem_row_arr[unique_indices],
            item_to_unique)


def _all_element_domains(geom_path, unique_etypes, unique_rows,
                         elem_kind, domain_id):
    """Merge L2 shell domains into full-element section arrays for EN ranges."""
    arrays = {}
    with h5py.File(geom_path, "r") as geom_h5:
        max_section = -1
        for etype in geom_h5.get("elements", {}):
            group = geom_h5["elements/{}".format(etype)]
            if "section_id" not in group:
                continue
            values = group["section_id"][:]
            valid = values[values >= 0]
            if len(valid):
                max_section = max(max_section, int(valid.max()))
        shell_mask = ((elem_kind == ELEM_KIND_SHELL) |
                      (elem_kind == ELEM_KIND_MEMBRANE))
        shell_domains = np.unique(domain_id[shell_mask])
        domain_to_full = {
            int(domain): max_section + 1 + offset
            for offset, domain in enumerate(shell_domains.tolist())
        }
        for index in np.flatnonzero(shell_mask):
            etype = unique_etypes[index].decode("ascii").rstrip("\x00")
            if etype not in arrays:
                group = geom_h5.get("elements/{}".format(etype))
                if group is None or "section_id" not in group:
                    continue
                arrays[etype] = group["section_id"][:].astype(
                    np.int32, copy=True)
            row = int(unique_rows[index])
            values = arrays[etype]
            if 0 <= row < len(values):
                values[row] = domain_to_full[int(domain_id[index])]
    return arrays


def _atomic_h5_path(final_path):
    return final_path + ".tmp-{}".format(os.getpid())


def process_instance_advanced(workspace, db_conn, inst_name):
    logger.info("Processing advanced indexes for instance: {} ...".format(inst_name))
    started = time.time()
    row = db_conn.execute(
        "SELECT geom_path FROM instances WHERE instance_name=?", (inst_name,)
    ).fetchone()
    geom_path = (os.path.join(workspace, row[0]) if row and row[0]
                 else os.path.join(workspace, "l1", "geometry", "{}.h5".format(inst_name)))
    surface_path = os.path.join(
        workspace, "l2", "geometry", "{}_surface.h5".format(inst_name))
    render_path = os.path.join(
        workspace, "l2", "render", "{}_render.h5".format(inst_name))
    if not all(os.path.exists(path) for path in (geom_path, surface_path, render_path)):
        raise RuntimeError("Core L2 files are missing for {}".format(inst_name))

    with h5py.File(surface_path, "r") as surface_h5:
        coords_global = surface_h5["nodes/coords_global"][:]
    with h5py.File(render_path, "r") as render_h5:
        positions = render_h5["render/positions"][:]
        indices = render_h5["render/indices"][:]
        vtx_node_row = render_h5["render/vtx_node_row"][:]
        tri_elem_row = render_h5["render/source_elem_row"][:]
        tri_node_rows = render_h5["render/source_node_rows"][:]
        tri_face_seq = render_h5["render/source_face_seq"][:] \
            if "render/source_face_seq" in render_h5 else np.ones(len(indices), np.uint8)
        tri_etype_code = render_h5["render/source_etype_code"][:]
        tri_etype_str = render_h5["render/source_etype_str"][:]

    conn_by_etype = {}
    labels = None
    etype_labels_cache = {}
    with h5py.File(geom_path, "r") as geom_h5:
        labels = geom_h5["nodes/labels"][:]
        for etype in geom_h5.get("elements", {}):
            group = geom_h5["elements/{}".format(etype)]
            if "conn" in group:
                conn_by_etype[etype] = group["conn"][:]
            if "labels" in group:
                etype_labels_cache[etype] = group["labels"][:]

    highorder_path = geom_path[:-3] + "_highorder.h5" if geom_path.endswith(".h5") else None
    if highorder_path and os.path.exists(highorder_path):
        with h5py.File(highorder_path, "r") as highorder_h5:
            for etype in highorder_h5.get("elements", {}):
                group = highorder_h5["elements/{}".format(etype)]
                if "conn_full" in group:
                    conn_by_etype[etype] = np.searchsorted(
                        labels, group["conn_full"][:]).astype(np.int32)

    source_local_node_idx = compute_source_local_node_idx(
        tri_etype_str, tri_elem_row, vtx_node_row, indices, conn_by_etype)
    unique_etypes, unique_rows, tri_global_elem_idx = _stable_unique_element_map(
        tri_etype_str, tri_elem_row)
    elem_kind = np.array([
        classify_elem_kind(value.decode("ascii").rstrip("\x00"))
        for value in unique_etypes
    ], dtype=np.uint8)
    with h5py.File(geom_path, "r") as geom_h5:
        section_id, section_names = compute_section_ids(
            geom_h5, tri_global_elem_idx, unique_etypes, unique_rows,
            etype_labels_cache)

    topology = build_surface_edge_topology(tri_node_rows, coords_global)
    adj_src, adj_dst, adj_angle_deg = compute_elem_adjacency_from_topology(
        topology, tri_global_elem_idx)
    default_angle = 20.0
    domain_id = build_domain_ids(
        section_id, elem_kind, adj_src, adj_dst, adj_angle_deg,
        feature_angle_deg=default_angle)
    all_elem_domains = _all_element_domains(
        geom_path, unique_etypes, unique_rows, elem_kind, domain_id)
    logger.info("  {} surface elements, {} domains, {} adjacency edges".format(
        len(unique_rows), int(domain_id.max()) + 1 if len(domain_id) else 0,
        len(adj_src)))

    edge_nodes, edge_types = compute_feature_edges(
        tri_node_rows, coords_global, topology=topology)
    mesh_edge_nodes = compute_all_surface_edges(
        tri_node_rows, tri_elem_row, tri_face_seq, tri_etype_code,
        topology=topology)
    logger.info("  {} feature edges, {} element mesh edges".format(
        len(edge_nodes), len(mesh_edge_nodes)))

    tri_positions = positions[indices] if len(indices) else np.zeros(
        (0, 3, 3), dtype=np.float32)
    octree = build_octree(tri_positions)
    logger.info("  Octree: {} nodes".format(len(octree["node_bbox"])))

    advanced_path = os.path.join(
        workspace, "l2", "render", "{}_advanced.h5".format(inst_name))
    advanced_tmp = _atomic_h5_path(advanced_path)
    with h5py.File(advanced_tmp, "w") as advanced_h5:
        advanced_h5.create_dataset(
            "render/source_local_node_idx", data=source_local_node_idx)
        averaging = advanced_h5.create_group("averaging")
        averaging.create_dataset("elem_etype", data=unique_etypes)
        averaging.create_dataset("elem_row", data=unique_rows)
        averaging.create_dataset("elem_section_id", data=section_id)
        averaging.create_dataset("elem_kind", data=elem_kind)
        averaging.create_dataset("default_domain_id", data=domain_id)
        averaging.attrs["default_feature_angle_deg"] = default_angle
        if section_names:
            averaging.create_dataset(
                "section_names",
                data=np.array([s.encode("utf-8") for s in section_names], dtype="S128"))
        if len(adj_src):
            averaging.create_dataset("adj_src", data=adj_src)
            averaging.create_dataset("adj_dst", data=adj_dst)
            averaging.create_dataset("adj_angle_deg", data=adj_angle_deg)
        full_group = averaging.create_group("all_element_domain")
        for etype, values in all_elem_domains.items():
            full_group.create_dataset(etype, data=values)
        octree_group = advanced_h5.create_group("octree")
        for name, values in octree.items():
            octree_group.create_dataset(name, data=values)
    os.replace(advanced_tmp, advanced_path)

    edges_path = os.path.join(
        workspace, "l2", "geometry", "{}_edges.h5".format(inst_name))
    edges_tmp = _atomic_h5_path(edges_path)
    with h5py.File(edges_tmp, "w") as edges_h5:
        feature_group = edges_h5.create_group("feature_edges")
        if len(edge_nodes):
            feature_group.create_dataset("edge_nodes", data=edge_nodes)
            feature_group.create_dataset("edge_types", data=edge_types)
        mesh_group = edges_h5.create_group("element_mesh_edges")
        if len(mesh_edge_nodes):
            mesh_group.create_dataset("edge_nodes", data=mesh_edge_nodes)
    os.replace(edges_tmp, edges_path)

    db_conn.execute(
        "UPDATE l2_instances SET edge_count=? WHERE instance_name=?",
        (len(edge_nodes), inst_name),
    )
    db_conn.commit()
    logger.info("  Advanced indexes done in {:.2f}s".format(time.time() - started))


def _write_advanced_status(workspace, status, error=None):
    l2_dir = os.path.join(workspace, "l2")
    mkdirs(l2_dir)
    final_path = os.path.join(l2_dir, "advanced_status.json")
    tmp_path = final_path + ".tmp-{}".format(os.getpid())
    payload = {
        "status": status,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if error:
        payload["error"] = str(error)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True)
    os.replace(tmp_path, final_path)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args      = parse_args()
    workspace = args.workspace
    phase = args.phase

    if not os.path.exists(workspace):
        logger.error("Workspace not found: {}".format(workspace))
        return

    db_path  = os.path.join(workspace, "manifest.db")
    asm_path = os.path.join(workspace, "l1", "assembly.h5")

    if not os.path.exists(db_path) or not os.path.exists(asm_path):
        logger.error("L1 outputs not found — run L1 first.")
        return

    logger.info("=== Layer 2 Preprocessing (%s) ===", phase)
    logger.info("  Workspace: {}".format(workspace))

    # 渲染几何即将重建：L3 的结果缓存（<workspace>/l3_cache/）全部依赖它，
    # 整体删除以立即回收磁盘（缓存 key 也带 render.h5 签名，不删也不会读到旧值）。
    l3_cache = os.path.join(workspace, "l3_cache")
    if phase in ("all", "core") and os.path.isdir(l3_cache):
        shutil.rmtree(l3_cache, ignore_errors=True)
        logger.info("  Cleared L3 cache: {}".format(l3_cache))

    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")

    instances = conn.execute("SELECT instance_name FROM instances").fetchall()

    failed = False
    try:
        if phase in ("all", "core"):
            _write_advanced_status(workspace, "pending")
            with h5py.File(asm_path, "r") as asm_h5:
                for (inst_name,) in instances:
                    process_instance_core(workspace, conn, asm_h5, inst_name)
                process_orientations(workspace, asm_h5)
        if phase in ("all", "advanced"):
            _write_advanced_status(workspace, "running")
            for (inst_name,) in instances:
                process_instance_advanced(workspace, conn, inst_name)
            _write_advanced_status(workspace, "ready")
    except Exception as e:
        failed = True
        if phase in ("all", "advanced"):
            try:
                _write_advanced_status(workspace, "error", e)
            except Exception:
                pass
        logger.error("Layer 2 failed: {}".format(e), exc_info=True)
    finally:
        conn.close()

    if failed:
        # 非零退出码，让 job_runner 把任务判为失败而不是"解析成功"
        logger.error("=== Layer 2 FAILED ===")
        sys.exit(1)

    logger.info("=== Layer 2 %s Complete ===", phase)


if __name__ == "__main__":
    main()
