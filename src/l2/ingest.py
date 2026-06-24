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
import logging
import math
import os
import sqlite3
import time

import h5py
import numpy as np

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
    # 2-node connector / spring elements — rendered as line segments
    'CONN3D2': 9, 'SPRING2': 9, 'SPRINGA': 9,
    # point elements — concentrated mass / rotary inertia / grounded spring
    'MASS': 11,  'ROTARYI': 11,  'SPRING1': 11,
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
    """
    pos_list   = []
    label_list = []

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
        pts = coords_global[conn[:, :2]]   # [N, 2, 3]
        pos_list.append(pts)
        label_list.append(labels)

    if not pos_list:
        return np.zeros((0, 2, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

    return (np.vstack(pos_list).astype(np.float32),
            np.concatenate(label_list).astype(np.int32))


def collect_points(geom_h5, coords_global):
    """
    Collect MASS/ROTARYI point elements.

    Returns:
        positions   [N, 3] float32  — node position per point element
        elem_labels [N]    int32    — Abaqus element label
    """
    pos_list   = []
    label_list = []

    for etype_str in geom_h5.get("elements", {}):
        etype_code = _resolve_elem_code(etype_str)
        if etype_code not in POINT_ELEM_CODES:
            continue
        grp = geom_h5["elements/{}".format(etype_str)]
        if "conn" not in grp or "labels" not in grp:
            continue
        conn   = grp["conn"][:]    # [N, 1] int32 — node row
        labels = grp["labels"][:]  # [N] int32
        pts = coords_global[conn[:, 0]]  # [N, 3]
        pos_list.append(pts)
        label_list.append(labels)

    if not pos_list:
        return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

    return (np.vstack(pos_list).astype(np.float32),
            np.concatenate(label_list).astype(np.int32))


def collect_couplings(geom_h5):
    """
    Pass through coupling (RBE2/KINEMATIC) line segments written by the INP exporter.

    Returns:
        positions [N*2, 3] float32 — interleaved (ref, slave) pairs
    """
    if "couplings/positions" not in geom_h5:
        return np.zeros((0, 3), dtype=np.float32)
    return geom_h5["couplings/positions"][:]  # already [N*2, 3] float32


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
        return (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32),
                np.zeros((0, 3), np.int32),
                _empty(np.int32), _empty(np.int32),
                _empty(np.int32), _empty(np.uint8), _empty(np.uint8), _empty("S8"))

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
    Compute averaging domain IDs for surface elements using Union-Find.

    Step 1: Each (section_id group) starts as seed.
    Step 2: For shell/membrane elements within the same section,
            union adjacent elements whose face-normal angle <= feature_angle_deg.
            Solid elements are not further split by angle.

    Returns:
        domain_id [E] int32   globally unique domain IDs (0-based)
    """
    E = len(section_id)
    parent = np.arange(E, dtype=np.int32)
    rank   = np.zeros(E, dtype=np.int32)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]   # path compression
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    cos_thr = math.cos(math.radians(feature_angle_deg))

    # Only connect shell/membrane elements within same section
    for i in range(len(adj_src)):
        ea, eb = int(adj_src[i]), int(adj_dst[i])
        if section_id[ea] != section_id[eb]:
            continue
        ka, kb = int(elem_kind[ea]), int(elem_kind[eb])
        # Solid elements: same section = same domain (union regardless of angle)
        if ka == ELEM_KIND_SOLID and kb == ELEM_KIND_SOLID:
            union(ea, eb)
        # Shell/membrane: union only if angle within threshold
        elif (ka in (ELEM_KIND_SHELL, ELEM_KIND_MEMBRANE) and
              kb in (ELEM_KIND_SHELL, ELEM_KIND_MEMBRANE)):
            angle = float(adj_angle_deg[i])
            if math.cos(math.radians(angle)) >= cos_thr:
                union(ea, eb)
        # Mixed solid/shell: don't connect (different structural semantics)

    # Assign global domain IDs: (section_id, root) -> domain_id
    root_to_did = {}
    domain_id   = np.empty(E, dtype=np.int32)
    did_counter = 0

    for ei in range(E):
        root = find(ei)
        # Include section_id in key: elements from different sections
        # might share the same union-find root if section_id==0 (unassigned)
        key = (int(section_id[ei]), root)
        if key not in root_to_did:
            root_to_did[key] = did_counter
            did_counter += 1
        domain_id[ei] = root_to_did[key]

    return domain_id


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
    Sf = len(surf_fnc)
    if Sf == 0:
        return (np.zeros(0, np.int32), np.zeros(0, np.int32),
                np.zeros(0, np.float32))

    # Compute face normals from first 3 nodes of each face
    n0 = coords_global[surf_fnc[:, 0]].astype(np.float64)
    n1 = coords_global[surf_fnc[:, 1]].astype(np.float64)
    n2 = coords_global[surf_fnc[:, 2]].astype(np.float64)
    face_normals = np.cross(n1 - n0, n2 - n0)           # [Sf, 3]
    nrm = np.linalg.norm(face_normals, axis=1, keepdims=True)
    face_normals /= np.where(nrm > 1e-12, nrm, 1.0)     # unit normals

    # Enumerate all face edges (sorted node pairs) with face index
    valid = surf_fnc != -1
    n_per_face = valid.sum(axis=1)                       # [Sf]
    max_n = int(n_per_face.max()) if Sf > 0 else 0

    edge_fa = []   # face index
    edge_n1 = []   # node a (smaller)
    edge_n2 = []   # node b (larger)

    for fi in range(Sf):
        n = int(n_per_face[fi])
        nodes = surf_fnc[fi, :n]
        for j in range(n):
            a = int(nodes[j])
            b = int(nodes[(j + 1) % n])
            if a > b:
                a, b = b, a
            edge_fa.append(fi)
            edge_n1.append(a)
            edge_n2.append(b)

    edge_fa = np.array(edge_fa, dtype=np.int32)
    edge_n1 = np.array(edge_n1, dtype=np.int32)
    edge_n2 = np.array(edge_n2, dtype=np.int32)

    # Sort by (n1, n2) to group shared edges
    order   = np.lexsort((edge_n2, edge_n1))
    edge_fa = edge_fa[order]
    edge_n1 = edge_n1[order]
    edge_n2 = edge_n2[order]

    adj_src_list   = []
    adj_dst_list   = []
    adj_angle_list = []

    i = 0
    total = len(edge_fa)
    while i < total:
        j = i + 1
        while (j < total and edge_n1[j] == edge_n1[i]
               and edge_n2[j] == edge_n2[i]):
            j += 1
        # faces i..j-1 share this edge
        if j - i >= 2:
            for a in range(i, j):
                fa = int(edge_fa[a])
                ea = int(surf_global_elem_idx[fa])
                for b in range(a + 1, j):
                    fb = int(edge_fa[b])
                    eb = int(surf_global_elem_idx[fb])
                    if ea == eb:
                        continue   # same element (e.g. solid with two surface tris sharing an edge)
                    dot = float(np.clip(
                        face_normals[fa] @ face_normals[fb], -1.0, 1.0))
                    angle = math.degrees(math.acos(dot))
                    adj_src_list.append(ea)
                    adj_dst_list.append(eb)
                    adj_angle_list.append(angle)
        i = j

    if not adj_src_list:
        return (np.zeros(0, np.int32), np.zeros(0, np.int32),
                np.zeros(0, np.float32))

    return (np.array(adj_src_list, dtype=np.int32),
            np.array(adj_dst_list, dtype=np.int32),
            np.array(adj_angle_list, dtype=np.float32))


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
                           fold_angle_deg=FOLD_ANGLE_DEG):
    """
    Returns:
        edge_nodes [E, 2] int32    sorted node row pairs
        edge_types [E]    uint8    1=boundary, 2=fold
    """
    Sf = len(surf_tri_nodes)
    if Sf == 0:
        return np.zeros((0, 2), dtype=np.int32), np.zeros(0, dtype=np.uint8)

    # All 3 edges per triangle, sorted node pairs
    e01 = np.sort(surf_tri_nodes[:, [0, 1]], axis=1)
    e12 = np.sort(surf_tri_nodes[:, [1, 2]], axis=1)
    e20 = np.sort(surf_tri_nodes[:, [2, 0]], axis=1)
    all_edges      = np.vstack([e01, e12, e20])          # [3*Sf, 2]
    face_of_edge   = np.tile(np.arange(Sf, dtype=np.int32), 3)

    edges_bytes = np.ascontiguousarray(all_edges).view(
        np.dtype((np.void, all_edges.dtype.itemsize * 2))
    ).ravel()
    _, inv, counts = np.unique(edges_bytes, return_inverse=True, return_counts=True)

    n_uniq = len(counts)
    # Recover unique edge node pairs
    first_occ = np.empty(n_uniq, dtype=np.int64)
    # Use argmax on one-hot: faster than loop
    order = np.argsort(inv, kind="stable")
    sorted_inv = inv[order]
    starts = np.searchsorted(sorted_inv, np.arange(n_uniq))
    first_occ = order[starts]
    unique_edge_nodes = all_edges[first_occ]             # [n_uniq, 2]

    edge_types = np.zeros(n_uniq, dtype=np.uint8)

    # Boundary edges
    is_boundary = counts == 1
    edge_types[is_boundary] = 1

    # Fold edges (shared by exactly 2 triangles)
    is_shared = counts == 2
    if is_shared.any():
        p0 = coords_global[surf_tri_nodes[:, 0]]
        p1 = coords_global[surf_tri_nodes[:, 1]]
        p2 = coords_global[surf_tri_nodes[:, 2]]
        face_normals = np.cross(p1 - p0, p2 - p0).astype(np.float32)
        nrm = np.linalg.norm(face_normals, axis=1, keepdims=True)
        face_normals /= np.where(nrm > 1e-12, nrm, 1.0)

        cos_thresh = np.cos(np.radians(fold_angle_deg))
        shared_idx = np.where(is_shared)[0]             # unique edge indices

        # For each shared edge, find its two triangle indices
        # Sorted order → consecutive entries with same inv value
        sorted_faces = face_of_edge[order]
        face_a = sorted_faces[starts[shared_idx]]
        face_b = sorted_faces[starts[shared_idx] + 1]

        # Vectorized angle check
        na = face_normals[face_a]
        nb = face_normals[face_b]
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

def compute_all_surface_edges(surf_tri_nodes, surf_tri_er, surf_tri_fs, surf_tri_ec):
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
    Sf = len(surf_tri_nodes)
    if Sf == 0:
        return np.zeros((0, 2), dtype=np.int32)

    e01 = np.sort(surf_tri_nodes[:, [0, 1]], axis=1)
    e12 = np.sort(surf_tri_nodes[:, [1, 2]], axis=1)
    e20 = np.sort(surf_tri_nodes[:, [2, 0]], axis=1)
    all_edges    = np.vstack([e01, e12, e20])              # [3*Sf, 2]
    face_of_edge = np.tile(np.arange(Sf, dtype=np.int32), 3)

    edges_bytes = np.ascontiguousarray(all_edges).view(
        np.dtype((np.void, all_edges.dtype.itemsize * 2))
    ).ravel()
    _, inv, counts = np.unique(edges_bytes, return_inverse=True, return_counts=True)

    n_uniq = len(counts)
    order       = np.argsort(inv, kind="stable")
    sorted_inv  = inv[order]
    starts      = np.searchsorted(sorted_inv, np.arange(n_uniq))
    first_occ   = order[starts]
    unique_edge_nodes = all_edges[first_occ]               # [n_uniq, 2]

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
        sorted_faces = face_of_edge[order]
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

def process_instance(workspace, db_conn, asm_h5, inst_name):
    logger.info("Processing instance: {} ...".format(inst_name))
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
        line_positions, line_elem_labels = collect_lines(f_in, coords_global)

        # 2c. Point element collection (MASS / ROTARYI)
        point_positions, point_elem_labels = collect_points(f_in, coords_global)

        # 2d. Coupling lines (RBE2 / KINEMATIC) — written by INP exporter
        coupling_positions = collect_couplings(f_in)

        # Load conn arrays for source_local_node_idx computation (task #6)
        conn_by_etype = {}
        for et in f_in.get("elements", {}):
            if "conn" in f_in["elements/{}".format(et)]:
                conn_by_etype[et] = f_in["elements/{}/conn".format(et)][:]

    # 3. Filter to surface faces (face-level, before triangulation)
    surf_fnc       = fnc[is_surface]
    surf_elem_rows = elem_rows[is_surface]
    surf_face_seqs    = face_seqs[is_surface]
    surf_etype_codes  = etype_codes[is_surface]
    surf_etype_strs   = etype_strs[is_surface]
    Sf_faces = len(surf_fnc)
    logger.info("  {} surface faces".format(Sf_faces))

    # 4. Indexed geometry (positions [Nv,3] + indices [Nt,3]; normals computed by frontend)
    (render_positions, render_indices,
     vtx_node_row, vtx_tri_idx,
     tri_elem_row, tri_face_seq, tri_etype_code, tri_etype_str) = \
        build_indexed_geometry(coords_global, surf_fnc,
                               surf_elem_rows, surf_face_seqs,
                               surf_etype_codes, surf_etype_strs)

    Nv = len(render_positions)
    Nt = len(render_indices)
    logger.info("  {} vertices, {} triangles (indexed)".format(Nv, Nt))

    # surf_tri_nodes [Nt, 3]: node rows per triangle, needed for edge detection
    surf_tri_nodes = vtx_node_row[render_indices] if Nt > 0 else np.zeros((0, 3), np.int32)

    # 4b. source_local_node_idx [Nt, 3]: local index of each triangle corner
    #     within its source element's connectivity array (for EN per-node lookup)
    source_local_node_idx = compute_source_local_node_idx(
        tri_etype_str, tri_elem_row, vtx_node_row, render_indices, conn_by_etype
    )

    # 4c. Averaging domain data ────────────────────────────────────────────────
    # Unique surface elements: deduplicate (etype, elem_row) pairs from surf_fnc
    # (a solid element can contribute multiple surface faces)
    surf_etype_elem_pairs = np.stack(
        [surf_etype_strs.view(np.uint64),
         surf_elem_rows.astype(np.int64).view(np.uint64)], axis=1
    )
    _, uniq_idx = np.unique(surf_etype_elem_pairs.view(
        np.dtype((np.void, surf_etype_elem_pairs.dtype.itemsize * 2))
    ).ravel(), return_index=True)
    uniq_idx = np.sort(uniq_idx)   # preserve order

    surf_uniq_etypes = surf_etype_strs[uniq_idx]    # [E] S8
    surf_uniq_erows  = surf_elem_rows[uniq_idx]      # [E] int32
    E = len(uniq_idx)

    # Build face → global_elem_idx map for adjacency computation
    pair_bytes = surf_etype_elem_pairs.view(
        np.dtype((np.void, surf_etype_elem_pairs.dtype.itemsize * 2))
    ).ravel()
    uniq_pairs_bytes = pair_bytes[uniq_idx]
    # searchsorted needs sorted uniq_pairs_bytes
    sort_order = np.argsort(uniq_pairs_bytes)
    sorted_uniq_bytes = uniq_pairs_bytes[sort_order]
    pos_in_sorted = np.searchsorted(sorted_uniq_bytes, pair_bytes)
    surf_global_elem_idx = sort_order[pos_in_sorted].astype(np.int32)  # [Sf]

    # elem_kind per unique surface element
    avg_elem_kind = np.array(
        [classify_elem_kind(eb.decode("ascii").rstrip("\x00"))
         for eb in surf_uniq_etypes],
        dtype=np.uint8
    )

    # section_id per unique surface element (needs labels from geom H5)
    etype_labels_cache = {}
    with h5py.File(geom_path, "r") as f_in:
        for et in f_in.get("elements", {}):
            if "labels" in f_in["elements/{}".format(et)]:
                etype_labels_cache[et] = f_in["elements/{}/labels".format(et)][:]
        avg_section_id, section_names = compute_section_ids(
            f_in, surf_global_elem_idx,
            surf_uniq_etypes, surf_uniq_erows,
            etype_labels_cache
        )
    section_names_arr = np.array(
        [s.encode("utf-8") for s in section_names], dtype="S128"
    ) if section_names else np.zeros(0, dtype="S128")

    # adjacency graph (face-level edges → element-level)
    adj_src, adj_dst, adj_angle_deg = compute_elem_adjacency(
        surf_fnc, surf_global_elem_idx, coords_global
    )

    # default domain IDs with feature_angle=20°
    DEFAULT_FEATURE_ANGLE = 20.0
    avg_domain_id = build_domain_ids(
        avg_section_id, avg_elem_kind,
        adj_src, adj_dst, adj_angle_deg,
        feature_angle_deg=DEFAULT_FEATURE_ANGLE
    )

    logger.info("  {} surface elements, {} domains, {} adjacency edges".format(
        E, int(avg_domain_id.max()) + 1 if E > 0 else 0, len(adj_src)))

    # 5. Feature edges (use triangulated surface topology)
    edge_nodes, edge_types = compute_feature_edges(surf_tri_nodes, coords_global)
    logger.info("  {} feature edges".format(len(edge_nodes)))

    # 5b. Element mesh edges
    mesh_edge_nodes = compute_all_surface_edges(surf_tri_nodes, tri_elem_row, tri_face_seq, tri_etype_code)
    logger.info("  {} element mesh edges".format(len(mesh_edge_nodes)))

    render_face_idx = np.arange(Nt, dtype=np.int32)

    # 6. Octree (operates on per-triangle positions [Nt, 3, 3])
    tri_positions = render_positions[render_indices] if Nt > 0 \
                    else np.zeros((0, 3, 3), dtype=np.float32)
    octree = build_octree(tri_positions)
    logger.info("  Octree: {} nodes".format(len(octree["node_bbox"])))

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

        eg = f.create_group("feature_edges")
        if len(edge_nodes) > 0:
            eg.create_dataset("edge_nodes", data=edge_nodes)
            eg.create_dataset("edge_types", data=edge_types)

        meg = f.create_group("element_mesh_edges")
        if len(mesh_edge_nodes) > 0:
            meg.create_dataset("edge_nodes", data=mesh_edge_nodes)

        lg = f.create_group("lines")
        if N_lines > 0:
            lg.create_dataset("positions",   data=line_positions,
                              chunks=(min(N_lines, 4096), 2, 3), compression="lzf")
            lg.create_dataset("elem_labels", data=line_elem_labels)

        pg = f.create_group("points")
        if N_points > 0:
            pg.create_dataset("positions",   data=point_positions)
            pg.create_dataset("elem_labels", data=point_elem_labels)

        cg = f.create_group("couplings")
        if N_couplings > 0:
            cg.create_dataset("positions", data=np.ascontiguousarray(coupling_positions),
                              chunks=(min(len(coupling_positions), 4096), 3),
                              compression="lzf")

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
            rg.create_dataset("source_etype_code",     data=tri_etype_code)
            rg.create_dataset("source_etype_str",      data=tri_etype_str)
            rg.create_dataset("source_local_node_idx", data=source_local_node_idx)

        # averaging domain data
        ag = f.create_group("averaging")
        ag.create_dataset("elem_etype",      data=surf_uniq_etypes)
        ag.create_dataset("elem_row",        data=surf_uniq_erows)
        ag.create_dataset("elem_section_id", data=avg_section_id)
        ag.create_dataset("elem_kind",       data=avg_elem_kind)
        ag.create_dataset("default_domain_id", data=avg_domain_id)
        ag.attrs["default_feature_angle_deg"] = DEFAULT_FEATURE_ANGLE
        if len(section_names_arr) > 0:
            ag.create_dataset("section_names", data=section_names_arr)
        if len(adj_src) > 0:
            ag.create_dataset("adj_src",       data=adj_src)
            ag.create_dataset("adj_dst",       data=adj_dst)
            ag.create_dataset("adj_angle_deg", data=adj_angle_deg)

        og = f.create_group("octree")
        og.create_dataset("node_bbox",     data=octree["node_bbox"])
        og.create_dataset("node_children", data=octree["node_children"])
        og.create_dataset("node_is_leaf",  data=octree["node_is_leaf"])
        og.create_dataset("face_indices",  data=octree["face_indices"])
        og.create_dataset("leaf_offsets",  data=octree["leaf_offsets"])

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
          Sf_faces, Nt, len(edge_nodes), 1, N_lines))
    db_conn.commit()

    logger.info("  Done in {:.2f}s".format(time.time() - t0))


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args      = parse_args()
    workspace = args.workspace

    if not os.path.exists(workspace):
        logger.error("Workspace not found: {}".format(workspace))
        return

    db_path  = os.path.join(workspace, "manifest.db")
    asm_path = os.path.join(workspace, "l1", "assembly.h5")

    if not os.path.exists(db_path) or not os.path.exists(asm_path):
        logger.error("L1 outputs not found — run L1 first.")
        return

    logger.info("=== Layer 2 Preprocessing ===")
    logger.info("  Workspace: {}".format(workspace))

    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")

    instances = conn.execute("SELECT instance_name FROM instances").fetchall()

    try:
        with h5py.File(asm_path, "r") as asm_h5:
            for (inst_name,) in instances:
                process_instance(workspace, conn, asm_h5, inst_name)
            process_orientations(workspace, asm_h5)
    except Exception as e:
        logger.error("Layer 2 failed: {}".format(e), exc_info=True)
    finally:
        conn.close()

    logger.info("=== Layer 2 Complete ===")


if __name__ == "__main__":
    main()
