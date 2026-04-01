"""
View Cut Phase 2 — Plane-section mesh generation.

Reads L1 volume-element geometry, intersects each element with an axis-aligned
cutting plane, and returns the cross-section as a Triangle Soup buffer.

Supported element types (by corner-node count):
  4  → C3D4 / any 4-node tet       (6 edges)
  6  → C3D6 / wedge                (9 edges)
  8  → C3D8 / C3D8R / any 8-node hex (12 edges)
  10 → C3D10 (high-order tet)      → treated as 4-node tet (corner nodes only)
  20 → C3D20 / C3D20R              → treated as 8-node hex (corner nodes only)

Shell / beam elements (etype name starts with S, B, M, STRI, …) are skipped.
"""
import logging
import os
from typing import Optional, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, NotReadyError
from ..core.state import OdbRegistry

logger = logging.getLogger(__name__)

# ── Element-edge tables (corner-node indices, 0-based) ────────────────────────
# Each tuple (a, b) means: edge connects conn[a] and conn[b].
# Order follows Abaqus standard node numbering.

_EDGES_4 = [                                        # C3D4 tet
    (0, 1), (0, 2), (0, 3),
    (1, 2), (1, 3), (2, 3),
]
_EDGES_6 = [                                        # C3D6 wedge / penta
    (0, 1), (1, 2), (2, 0),                         # bottom face
    (3, 4), (4, 5), (5, 3),                         # top face
    (0, 3), (1, 4), (2, 5),                         # vertical
]
_EDGES_8 = [                                        # C3D8 / C3D8R hex
    (0, 1), (1, 2), (2, 3), (3, 0),                 # bottom face
    (4, 5), (5, 6), (6, 7), (7, 4),                 # top face
    (0, 4), (1, 5), (2, 6), (3, 7),                 # vertical
]

# n_corner_nodes → (edge list, actual corner count used for cutting)
_ETYPE_CONFIG = {
    4:  (_EDGES_4, 4),
    6:  (_EDGES_6, 6),
    8:  (_EDGES_8, 8),
    10: (_EDGES_4, 4),   # C3D10: use first 4 corner nodes (Abaqus ordering)
    20: (_EDGES_8, 8),   # C3D20: use first 8 corner nodes
}

# Etype names that are NOT volume elements — skip them.
_SHELL_PREFIXES = ("S", "B", "M", "STRI", "SAX", "SC")


def _is_volume_etype(etype_safe: str) -> bool:
    """Return True if the element type is a volume (solid) element."""
    name = etype_safe.upper()
    for prefix in _SHELL_PREFIXES:
        if name.startswith(prefix):
            return False
    return True


def _axis_normal(axis: str) -> np.ndarray:
    a = axis.upper()
    if a == "X":
        return np.array([1.0, 0.0, 0.0], dtype=np.float32)
    if a == "Y":
        return np.array([0.0, 1.0, 0.0], dtype=np.float32)
    return np.array([0.0, 0.0, 1.0], dtype=np.float32)


def _cut_elements(
    coords: np.ndarray,    # [N_nodes, 3] float32
    conn: np.ndarray,      # [N_elems, n_col] int32
    edges: list,           # list of (a, b) corner-index pairs
    n_corner: int,         # how many columns to actually use
    normal: np.ndarray,    # [3] float32
    position: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Intersect a batch of same-type elements with the cutting plane.

    Returns:
        tri_buf  [T, 3, 3] float32 — filled cross-section triangles (fan triangulation)
        edge_buf [E, 2, 3] float32 — polygon outline edges (one edge per polygon side)
    """
    conn_c = conn[:, :n_corner]

    dist_all  = coords @ normal - position          # [N_nodes]
    elem_dist = dist_all[conn_c]                    # [Ne, n_corner]
    d_min = elem_dist.min(axis=1)
    d_max = elem_dist.max(axis=1)
    hit_idx = np.where((d_min < 0) & (d_max > 0))[0]

    if len(hit_idx) == 0:
        return (np.zeros((0, 3, 3), dtype=np.float32),
                np.zeros((0, 2, 3), dtype=np.float32))

    triangles: list = []
    poly_edges: list = []
    hit_conn  = conn_c[hit_idx]
    hit_edist = elem_dist[hit_idx]
    hit_verts = coords[hit_conn]

    for k in range(len(hit_idx)):
        ds = hit_edist[k]
        vs = hit_verts[k]
        pts = []

        for a, b in edges:
            da, db = float(ds[a]), float(ds[b])
            if (da < 0.0) != (db < 0.0):
                t = da / (da - db)
                pts.append(vs[a] + t * (vs[b] - vs[a]))

        if len(pts) < 3:
            continue

        pts_arr = np.array(pts, dtype=np.float32)

        # Sort by polar angle (convex polygon → CCW order)
        centroid = pts_arr.mean(axis=0)
        u = pts_arr[0] - centroid
        u_len = float(np.linalg.norm(u))
        if u_len < 1e-12:
            continue
        u /= u_len
        v = np.cross(normal, u).astype(np.float32)
        v_len = float(np.linalg.norm(v))
        if v_len < 1e-12:
            continue
        v /= v_len
        delta  = pts_arr - centroid
        angles = np.arctan2(delta @ v, delta @ u)
        pts_arr = pts_arr[np.argsort(angles)]
        n = len(pts_arr)

        # Fan triangulation (fill)
        for i in range(1, n - 1):
            triangles.append(np.stack([pts_arr[0], pts_arr[i], pts_arr[i + 1]]))

        # Polygon outline edges (mesh edges on cut face)
        for i in range(n):
            poly_edges.append(np.stack([pts_arr[i], pts_arr[(i + 1) % n]]))

    empty_tris  = np.zeros((0, 3, 3), dtype=np.float32)
    empty_edges = np.zeros((0, 2, 3), dtype=np.float32)

    tri_buf  = np.stack(triangles).astype(np.float32)  if triangles  else empty_tris
    edge_buf = np.stack(poly_edges).astype(np.float32) if poly_edges else empty_edges
    return tri_buf, edge_buf


def compute_section(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    axis: str,
    position: float,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """
    Compute the cross-section mesh and element outline edges.

    Returns:
        (tri_buf [T,3,3], edge_buf [E,2,3], tri_count)
        Both arrays may be empty if the plane doesn't intersect any volume element.
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' is not render-ready", {"odb_id": odb_id})

    geom_path = os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_path):
        raise NotFoundError(
            f"Geometry file not found for instance '{instance}'",
            {"instance": instance},
        )

    normal = _axis_normal(axis)
    all_tris:  list = []
    all_edges: list = []

    try:
        with h5py.File(geom_path, "r") as f:
            coords = f["nodes/coords"][:].astype(np.float32)

            if "elements" not in f:
                return (np.zeros((0, 3, 3), dtype=np.float32),
                        np.zeros((0, 2, 3), dtype=np.float32), 0)

            for etype_safe in f["elements"]:
                if not _is_volume_etype(etype_safe):
                    continue
                grp = f[f"elements/{etype_safe}"]
                if "conn" not in grp:
                    continue
                conn  = grp["conn"][:].astype(np.int32)
                n_col = conn.shape[1]
                cfg   = _ETYPE_CONFIG.get(n_col)
                if cfg is None:
                    logger.debug(
                        "section_service: skipping '%s' (n_col=%d)", etype_safe, n_col
                    )
                    continue
                elem_edges, n_corner = cfg
                tris, segs = _cut_elements(
                    coords, conn, elem_edges, n_corner, normal, position
                )
                if len(tris)  > 0: all_tris.append(tris)
                if len(segs)  > 0: all_edges.append(segs)

    except Exception:
        logger.warning("section_service: error reading geometry HDF5", exc_info=True)
        raise

    if not all_tris:
        return (np.zeros((0, 3, 3), dtype=np.float32),
                np.zeros((0, 2, 3), dtype=np.float32), 0)

    tri_buf  = np.concatenate(all_tris,  axis=0).astype(np.float32)
    edge_buf = np.concatenate(all_edges, axis=0).astype(np.float32) if all_edges \
               else np.zeros((0, 2, 3), dtype=np.float32)
    return tri_buf, edge_buf, len(tri_buf)
