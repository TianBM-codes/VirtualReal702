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
import os
import sqlite3
import time

import h5py
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FOLD_ANGLE_DEG = 30.0
OCTREE_MAX_DEPTH = 8
OCTREE_LEAF_THRESHOLD = 1000

ELEM_TYPE_CODE = {
    'S3': 0, 'S3R': 0, 'S6': 0,
    'S4': 1, 'S4R': 1, 'S4R5': 1, 'S8R': 1, 'S8R5': 1,
    'C3D4': 2, 'C3D4H': 2,
    'C3D6': 3, 'C3D6H': 3,
    'C3D8': 4, 'C3D8R': 4, 'C3D8H': 4, 'C3D8RH': 4,
    'C3D10': 5, 'C3D10M': 5, 'C3D10H': 5,
    'C3D15': 6, 'C3D15H': 6,
    'C3D20': 7, 'C3D20R': 7, 'C3D20H': 7, 'C3D20RH': 7,
}


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
        face_normals [Tf, 3]      float32 outward face normals from L1 (via Newell+flip)
    """
    keys_list  = []   # sorted node rows → canonical face key
    fnc_list   = []   # original face_node_conn rows
    er_list    = []   # elem_row per face
    fs_list    = []   # face_seq per face
    ec_list    = []   # etype_code per face
    es_list    = []   # etype_str per face
    nrm_list   = []   # face normals from L1
    max_fn     = 0

    for etype_str in geom_h5.get("elements", {}):
        grp = geom_h5["elements/{}".format(etype_str)]
        if "face_node_conn" not in grp:
            continue
        etype_code = ELEM_TYPE_CODE.get(etype_str, -1)
        if etype_code < 0:
            continue

        fnc = grp["face_node_conn"][:]   # [Mf, w]
        fei = grp["face_elem_idx"][:]    # [Mf]
        fsq = grp["face_seq"][:]         # [Mf]
        Mf, w = fnc.shape

        # L1 face normals (outward, Newell+flip corrected)
        if "face_normals" in grp:
            nrm = grp["face_normals"][:].astype(np.float32)  # [Mf, 3]
        else:
            nrm = np.zeros((Mf, 3), dtype=np.float32)

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
        nrm_list.append(nrm)
        max_fn = max(max_fn, w)

    if not keys_list:
        empty = np.zeros(0, dtype=np.int32)
        return (np.zeros((0, 1), dtype=np.int32), empty, empty,
                np.zeros(0, dtype=np.uint8), np.zeros(0, dtype="S8"),
                np.zeros(0, dtype=bool), np.zeros((0, 3), dtype=np.float32))

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
    nrm   = np.vstack(nrm_list)

    # Each face's canonical key = its sorted node row vector
    # Encode as bytes for np.unique
    keys_bytes = np.ascontiguousarray(keys).view(
        np.dtype((np.void, keys.dtype.itemsize * max_fn))
    ).ravel()
    _, inv, counts = np.unique(keys_bytes, return_inverse=True, return_counts=True)
    is_surface = counts[inv] == 1

    return fnc, er, fs, ec, es, is_surface, nrm


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

def build_indexed_geometry(coords_global, surf_fnc, surf_face_normals,
                            surf_elem_rows, surf_face_seqs,
                            surf_etype_codes, surf_etype_strs):
    """
    Build indexed render buffer.  Vertices are shared within an element face
    but NOT across different faces (element boundaries remain hard edges).

    surf_fnc:           [Sf, W]   int32   surface face node rows (-1 = pad)
    surf_face_normals:  [Sf, 3]   float32 outward face normals from L1
    surf_elem_rows:     [Sf]      int32
    surf_face_seqs:     [Sf]      uint8
    surf_etype_codes:   [Sf]      uint8
    surf_etype_strs:    [Sf]      S8

    Returns:
        positions:    [Nv, 3]  float32
        normals:      [Nv, 3]  float32  (all verts of a face share the face normal)
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
    normals       = np.empty((Nv, 3), dtype=np.float32)
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
        nrm_n  = surf_face_normals[face_idx]        # [K, 3]

        vs = vtx_starts[face_idx]                   # [K] vertex buffer starts
        ts = tri_starts[face_idx]                   # [K] triangle buffer starts

        # ── Fill vertex buffer ───────────────────────────────────────────────
        flat_vtx = (vs[:, None] + np.arange(n, dtype=np.int64)[None, :]).ravel()
        node_rows = fnc_n.ravel().astype(np.int32)

        positions[flat_vtx]    = coords_global[node_rows]
        normals[flat_vtx]      = np.repeat(nrm_n, n, axis=0)
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

    return (positions, normals, indices,
            vtx_node_row, vtx_tri_idx,
            tri_elem_row, tri_face_seq, tri_etype_code, tri_etype_str)


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

    # Return only boundary + fold edges (skip interior smooth edges)
    keep = edge_types > 0
    return unique_edge_nodes[keep], edge_types[keep]


# ─── Element mesh edges (vectorized) ─────────────────────────────────────────

def compute_all_surface_edges(surf_tri_nodes, surf_tri_er, surf_tri_fs):
    """
    Compute element_mesh_edges: every real surface unit boundary edge.

    Unlike feature_edges, no angle filter is applied — all shared edges between
    different element faces are retained, including co-planar ones.

    A shared edge (count == 2) is a triangulation diagonal if both its triangles
    belong to the SAME original element face: same (tri_er, tri_fs) pair.
    Quads are split into 2 triangles sharing one diagonal — that diagonal must be
    excluded, or the wireframe would show phantom lines cutting through quad faces.

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

    # Shared edges: keep unless they are a triangulation diagonal.
    # Diagonal = shared by 2 triangles from the same (elem_row, face_seq).
    is_shared = counts == 2
    if is_shared.any():
        sorted_faces = face_of_edge[order]
        shared_idx   = np.where(is_shared)[0]
        face_a = sorted_faces[starts[shared_idx]]
        face_b = sorted_faces[starts[shared_idx] + 1]

        is_diagonal = (
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


# ─── Per-instance processing ──────────────────────────────────────────────────

def process_instance(workspace, db_conn, asm_h5, inst_name):
    logger.info("Processing instance: {} ...".format(inst_name))
    t0 = time.time()

    geom_path = os.path.join(workspace, "l1", "geometry", "{}.h5".format(inst_name))
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
        fnc, elem_rows, face_seqs, etype_codes, etype_strs, is_surface, face_normals = \
            collect_faces(f_in)

    # 3. Filter to surface faces (face-level, before triangulation)
    surf_fnc          = fnc[is_surface]
    surf_face_normals = face_normals[is_surface]
    surf_elem_rows    = elem_rows[is_surface]
    surf_face_seqs    = face_seqs[is_surface]
    surf_etype_codes  = etype_codes[is_surface]
    surf_etype_strs   = etype_strs[is_surface]
    Sf_faces = len(surf_fnc)
    logger.info("  {} surface faces".format(Sf_faces))

    # 4. Indexed geometry (positions/normals [Nv,3] + indices [Nt,3])
    (render_positions, render_normals, render_indices,
     vtx_node_row, vtx_tri_idx,
     tri_elem_row, tri_face_seq, tri_etype_code, tri_etype_str) = \
        build_indexed_geometry(coords_global, surf_fnc, surf_face_normals,
                               surf_elem_rows, surf_face_seqs,
                               surf_etype_codes, surf_etype_strs)

    Nv = len(render_positions)
    Nt = len(render_indices)
    logger.info("  {} vertices, {} triangles (indexed)".format(Nv, Nt))

    # surf_tri_nodes [Nt, 3]: node rows per triangle, needed for edge detection
    surf_tri_nodes = vtx_node_row[render_indices] if Nt > 0 else np.zeros((0, 3), np.int32)

    # 5. Feature edges (use triangulated surface topology)
    edge_nodes, edge_types = compute_feature_edges(surf_tri_nodes, coords_global)
    logger.info("  {} feature edges".format(len(edge_nodes)))

    # 5b. Element mesh edges
    mesh_edge_nodes = compute_all_surface_edges(surf_tri_nodes, tri_elem_row, tri_face_seq)
    logger.info("  {} element mesh edges".format(len(mesh_edge_nodes)))

    render_face_idx = np.arange(Nt, dtype=np.int32)

    # 6. Octree (operates on per-triangle positions [Nt, 3, 3])
    tri_positions = render_positions[render_indices] if Nt > 0 \
                    else np.zeros((0, 3, 3), dtype=np.float32)
    octree = build_octree(tri_positions)
    logger.info("  Octree: {} nodes".format(len(octree["node_bbox"])))

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

    # ── Write l2/render/<inst>_render.h5 ──
    with h5py.File(render_h5_path, "w") as f:
        rg = f.create_group("render")
        chunk_v = (min(Nv, 4096), 3) if Nv > 0 else True
        chunk_t = (min(Nt, 4096), 3) if Nt > 0 else True
        rg.create_dataset("positions",     data=render_positions,
                          chunks=chunk_v, compression="lzf")
        rg.create_dataset("normals",       data=render_normals,
                          chunks=chunk_v, compression="lzf")
        rg.create_dataset("indices",       data=render_indices,
                          chunks=chunk_t, compression="lzf")
        rg.create_dataset("vtx_node_row",  data=vtx_node_row)
        rg.create_dataset("vtx_tri_idx",   data=vtx_tri_idx)
        rg.create_dataset("render_face_idx", data=render_face_idx)
        if Nt > 0:
            rg.create_dataset("source_elem_row",  data=tri_elem_row)
            rg.create_dataset("source_node_rows", data=surf_tri_nodes)   # [Nt,3] backward compat
            rg.create_dataset("source_etype_code",data=tri_etype_code)
            rg.create_dataset("source_etype_str", data=tri_etype_str)

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
            partition_count     INTEGER
        )
    """)
    db_conn.execute("""
        INSERT OR REPLACE INTO l2_instances
        (instance_name, surface_path, render_path,
         surface_face_count, render_face_count, edge_count, partition_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (inst_name,
          "l2/geometry/{}_surface.h5".format(inst_name),
          "l2/render/{}_render.h5".format(inst_name),
          Sf_faces, Nt, len(edge_nodes), 1))
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
    except Exception as e:
        logger.error("Layer 2 failed: {}".format(e), exc_info=True)
    finally:
        conn.close()

    logger.info("=== Layer 2 Complete ===")


if __name__ == "__main__":
    main()
