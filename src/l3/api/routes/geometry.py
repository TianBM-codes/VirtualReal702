"""
GET  /api/odb/{odb_id}/geometry/{instance}/render-buffers
POST /api/odb/{odb_id}/geometry/{instance}/render-buffers-subset
  → L3BE binary: indexed geometry buffers

Section layout (indexed format):
  - "positions": [Nv, 3] float32  — unique vertex XYZ
  - "indices":   [Nt, 3] int32    — triangle index buffer

Nv = unique vertex count; Nt = triangle count.
Vertices are shared within an element face but not across element boundaries.
Normals are NOT stored — frontend calls geometry.computeVertexNormals() after load.
"""
import os
from typing import List, Optional  # Optional kept for Query defaults

import h5py
import numpy as np
from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel

from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...infra.manifest_repo import ManifestRepo
from ...services.user_field_service import get_face_mask_for_elem_labels

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["geometry"])


class ElemSubsetRequest(BaseModel):
    elem_labels: List[int]


def _compact_by_render_rows(
    positions: np.ndarray,
    indices: np.ndarray,
    render_rows: np.ndarray,
):
    """
    Filter an indexed geometry to a subset of triangles and compact the vertex buffer.

    render_rows: int32 array of triangle indices to keep.
    Returns (positions, indices) with only the used vertices.
    """
    sub_indices = indices[render_rows]                                  # [Nt_sub, 3]
    unique_verts, remapped = np.unique(sub_indices.ravel(), return_inverse=True)
    pos_out = np.ascontiguousarray(positions[unique_verts])
    idx_out = remapped.reshape(-1, 3).astype(np.int32)
    return pos_out, idx_out


@router.get("/geometry/{instance}/render-buffers")
async def get_render_buffers(
    odb_id: str,
    instance: str,
    set: Optional[str] = Query(default=None, description="User set name to filter geometry"),
):
    """
    Return indexed render buffers for one instance as L3BE binary.

    Section layout:
      - "positions": [Nv, 3] float32  — unique vertex XYZ
      - "normals":   [Nv, 3] float32  — per-vertex normals (if available)
      - "indices":   [Nt, 3] int32    — triangle index buffer

    Optional ?set=<name>: filter to only triangles belonging to the named user set.
    Vertex buffer is compacted to only include vertices used by the subset.
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    render_h5 = os.path.join(idx.workspace, "l2", "render", f"{instance}_render.h5")
    if not os.path.exists(render_h5):
        raise NotFoundError(
            f"Render data not found for instance '{instance}'",
            {"instance": instance},
        )

    with h5py.File(render_h5, "r") as f:
        positions = np.ascontiguousarray(f["render/positions"][:])   # [Nv, 3]
        indices   = np.ascontiguousarray(f["render/indices"][:]) \
                    if "render/indices" in f else None

    # ── Set filtering ──────────────────────────────────────────────────────
    if set is not None and indices is not None:
        manifest = ManifestRepo(idx.workspace)
        render_rows = manifest.get_user_set_render_rows(set, instance)
        if render_rows is None:
            # Fallback: try INP/ODB element_sets by name
            elem_labels = manifest.get_element_set_labels(set, instance)
            if elem_labels is not None and len(elem_labels) > 0:
                face_mask = get_face_mask_for_elem_labels(
                    idx, instance, set(elem_labels.tolist())
                )
                render_rows = np.where(face_mask)[0].astype(np.int32)
        if render_rows is not None and len(render_rows) > 0:
            positions, indices = _compact_by_render_rows(
                positions, indices, render_rows
            )

    Nt = len(indices) if indices is not None else 0

    sections = [("positions", positions)]
    if indices is not None:
        sections.append(("indices", indices))

    payload = l3be_build(sections)
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"X-Face-Count": str(Nt)},
    )


@router.get("/geometry/{instance}/element-mesh-edges")
async def get_element_mesh_edges(odb_id: str, instance: str):
    """
    Return element mesh edges for one instance as L3BE binary.

    Element mesh edges are ALL real surface unit boundary edges: boundary edges
    (belong to one surface triangle) plus every shared edge between different
    element faces. Triangulation diagonals introduced when quads are split into
    two triangles are excluded, so the result matches true finite element unit
    boundaries including co-planar element interfaces.

    This is the correct data source for a "show all element borders" wireframe.
    It is NOT the same as feature_edges (which filters out co-planar interfaces).

    Section layout:
      - "edge_positions": [E*2, 3] float32
            Each consecutive pair of rows is one edge: [start_xyz, end_xyz].
            Ready to feed into a Three.js LineSegments BufferGeometry.

    Header:
      X-Edge-Count: number of edges E (pairs, not rows)
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    surface_h5 = os.path.join(idx.workspace, "l2", "geometry", f"{instance}_surface.h5")
    if not os.path.exists(surface_h5):
        raise NotFoundError(
            f"Surface geometry not found for instance '{instance}'",
            {"instance": instance},
        )

    with h5py.File(surface_h5, "r") as f:
        if "element_mesh_edges/edge_nodes" not in f:
            empty = np.zeros((0, 3), dtype=np.float32)
            payload = l3be_build([("edge_positions", empty)])
            return Response(
                content=payload,
                media_type="application/octet-stream",
                headers={"X-Edge-Count": "0"},
            )

        edge_nodes = f["element_mesh_edges/edge_nodes"][:]   # [E, 2] int32
        coords = f["nodes/coords_global"][:]                 # [N, 3] float32

    # Deduplicate: sort each pair so (a,b)==(b,a), then unique rows.
    # Adjacent elements share edges; L2 may emit each shared edge twice.
    edge_nodes = np.unique(np.sort(edge_nodes, axis=1), axis=0)

    E = len(edge_nodes)
    edge_positions = np.ascontiguousarray(
        coords[edge_nodes.ravel()].reshape(E * 2, 3)
    )

    payload = l3be_build([("edge_positions", edge_positions)])
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"X-Edge-Count": str(E)},
    )


@router.get("/geometry/{instance}/feature-edges")
async def get_feature_edges(odb_id: str, instance: str):
    """
    Return feature edges (boundary + fold) for one instance as L3BE binary.

    Feature edges are a subset of surface edges: boundary edges (belong to only
    one surface triangle) and fold edges (dihedral angle >= 30 degrees).
    They highlight shape outlines and sharp creases, but do NOT represent every
    element boundary (co-planar element interfaces are omitted).

    Section layout:
      - "edge_positions": [E*2, 3] float32
            Each consecutive pair of rows is one edge: [start_xyz, end_xyz].
            Ready to feed into a Three.js LineSegments BufferGeometry.

    Header:
      X-Edge-Count: number of edges E (pairs, not rows)
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    surface_h5 = os.path.join(idx.workspace, "l2", "geometry", f"{instance}_surface.h5")
    if not os.path.exists(surface_h5):
        raise NotFoundError(
            f"Surface geometry not found for instance '{instance}'",
            {"instance": instance},
        )

    with h5py.File(surface_h5, "r") as f:
        if "feature_edges/edge_nodes" not in f:
            # Instance has no feature edges (e.g. all-interior, or zero surface)
            empty = np.zeros((0, 3), dtype=np.float32)
            payload = l3be_build([("edge_positions", empty)])
            return Response(
                content=payload,
                media_type="application/octet-stream",
                headers={"X-Edge-Count": "0"},
            )

        edge_nodes = f["feature_edges/edge_nodes"][:]   # [E, 2] int32 — node row indices
        coords = f["nodes/coords_global"][:]            # [N, 3] float32

    # Convert (row_a, row_b) pairs → interleaved position pairs [E*2, 3]
    E = len(edge_nodes)
    edge_positions = np.ascontiguousarray(
        coords[edge_nodes.ravel()].reshape(E * 2, 3)
    )

    payload = l3be_build([("edge_positions", edge_positions)])
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"X-Edge-Count": str(E)},
    )


@router.post("/geometry/{instance}/render-buffers-subset")
async def get_render_buffers_subset(
    odb_id: str,
    instance: str,
    body: ElemSubsetRequest,
):
    """
    Return indexed render buffers for an arbitrary subset of elements.

    Body: {"elem_labels": [1, 2, 3, ...]}

    The caller is free to compute elem_labels by any means (value threshold,
    element-type filter, spatial query, etc.).  The backend converts labels to
    triangle indices and returns the same compacted indexed geometry as the
    ?set= filter.

    Section layout: same as GET render-buffers.
    Header: X-Face-Count — number of triangles in the subset.
    Returns 200 with empty geometry if no labels match.
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    render_h5 = os.path.join(idx.workspace, "l2", "render", f"{instance}_render.h5")
    if not os.path.exists(render_h5):
        raise NotFoundError(
            f"Render data not found for instance '{instance}'",
            {"instance": instance},
        )

    # ── label → face mask via registry source arrays ──────────────────────
    labels_set = set(body.elem_labels)
    face_mask = get_face_mask_for_elem_labels(idx, instance, labels_set)
    render_rows = np.where(face_mask)[0].astype(np.int32)

    with h5py.File(render_h5, "r") as f:
        positions = np.ascontiguousarray(f["render/positions"][:])
        indices   = np.ascontiguousarray(f["render/indices"][:]) \
                    if "render/indices" in f else None

    if len(render_rows) == 0 or indices is None:
        # No matching triangles — return empty geometry
        empty_pos = np.zeros((0, 3), dtype=np.float32)
        empty_idx = np.zeros((0, 3), dtype=np.int32)
        payload = l3be_build([("positions", empty_pos), ("indices", empty_idx)])
        return Response(
            content=payload,
            media_type="application/octet-stream",
            headers={"X-Face-Count": "0"},
        )

    positions, indices = _compact_by_render_rows(
        positions, indices, render_rows
    )

    Nt = len(indices)
    sections = [("positions", positions), ("indices", indices)]

    payload = l3be_build(sections)
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"X-Face-Count": str(Nt)},
    )
