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
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from ...core.config import settings
from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...infra.manifest_repo import ManifestRepo
from ...infra.registry_repo import RegistryRepo
from ...services.user_field_service import get_face_mask_for_elem_labels

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["geometry"])


async def _l2_ready(odb_id: str) -> None:
    """Raise 503 if this project's L2 preprocessing is pending or running."""
    repo = RegistryRepo(settings.registry_db_path)
    proj = repo.get_project(odb_id)
    if proj and proj["geom_status"] in ("l2_pending", "l2_running"):
        raise HTTPException(
            status_code=503,
            detail="L2 preprocessing in progress — geometry data is being rebuilt, try again shortly",
        )


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


# ── Chunked render-buffers ────────────────────────────────────────────────
# Chunk size targets WebGL1 uint16 index limit (65535 verts/chunk).
# Each face adds at most 3 new vertices, so FACES_PER_CHUNK * 3 must be ≤ 65535
# in the worst case. 20000 faces → ≤ 60000 verts safely; in practice (faces
# share vertices) actual unique-vertex count per chunk is much smaller.
CHUNK_VERTEX_LIMIT = 60000
FACES_PER_CHUNK    = CHUNK_VERTEX_LIMIT // 3   # = 20000


def _build_render_chunks(positions: np.ndarray, indices: np.ndarray):
    """
    Split (positions, indices) into face_idx-contiguous chunks satisfying
    the WebGL1 uint16 limit. Returns list of dicts:
        { 'positions':       float32 [Nv_k, 3]   chunk-local vertex coords
          'indices':         int32   [Nt_k, 3]   chunk-local triangle indices
          'vertex_global_id': int32  [Nv_k]      chunk-local → global vertex
          'face_idx_base':    int                global face_idx of local face 0 }

    Per-chunk vertex count is upper-bounded by FACES_PER_CHUNK * 3 (≤ 60000).
    Faces stay in their original render order, so global_face_idx =
    face_idx_base + local_face_idx is exact.
    """
    Nt = len(indices)
    chunks = []
    for start in range(0, Nt, FACES_PER_CHUNK):
        end = min(start + FACES_PER_CHUNK, Nt)
        sub_indices = indices[start:end]                                # [Nt_k, 3]
        unique_verts, remapped = np.unique(
            sub_indices.ravel(), return_inverse=True
        )
        chunks.append({
            "positions":        np.ascontiguousarray(positions[unique_verts]),
            "indices":          remapped.reshape(-1, 3).astype(np.int32),
            "vertex_global_id": unique_verts.astype(np.int32),
            "face_idx_base":    start,
        })
    return chunks


@router.get("/geometry/{instance}/render-buffers", dependencies=[Depends(_l2_ready)])
async def get_render_buffers(
    odb_id: str,
    instance: str,
    set_name: Optional[str] = Query(default=None, alias="set", description="User set name to filter geometry"),
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
    if set_name is not None and indices is not None:
        manifest = ManifestRepo(idx.workspace)
        render_rows = manifest.get_user_set_render_rows(set_name, instance)
        if render_rows is None:
            # Fallback: try INP/ODB element_sets by name
            elem_labels = manifest.get_element_set_labels(set_name, instance)
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


@router.get("/geometry/{instance}/render-buffers-chunked", dependencies=[Depends(_l2_ready)])
async def get_render_buffers_chunked(
    odb_id: str,
    instance: str,
    set_name: Optional[str] = Query(default=None, alias="set", description="User set name to filter geometry"),
):
    """
    Return chunked render buffers for one instance as L3BE binary.

    Each chunk contains ≤ CHUNK_VERTEX_LIMIT (60000) unique vertices so that
    indices fit in uint16 (WebGL1 fallback for GPUs lacking
    OES_element_index_uint).

    Section layout (see docs/l3/Binary-Payload-Spec.md §18):
      - "chunk_count":       int32   [1]
      - "positions_concat":  float32 [ΣNv_k, 3]
      - "positions_offsets": int32   [K+1]   row-offsets into positions_concat
      - "indices_concat":    int32   [ΣNt_k, 3]
      - "indices_offsets":   int32   [K+1]   row-offsets into indices_concat
      - "face_idx_base":     int32   [K]     global face_idx of each chunk's first face
      - "vertex_global_id":  int32   [ΣNv_k] chunk-local → global vertex map

    Optional ?set=<name>: filter to triangles in the named user set before chunking.

    Headers:
      X-Chunk-Count: K
      X-Face-Count : total triangle count
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
        positions = np.ascontiguousarray(f["render/positions"][:])
        indices   = np.ascontiguousarray(f["render/indices"][:]) \
                    if "render/indices" in f else None

    if indices is None:
        sections, K, Nt = _build_chunked_payload(
            np.zeros((0, 3), dtype=np.float32),
            np.zeros((0, 3), dtype=np.int32),
        )
        return Response(
            content=l3be_build(sections),
            media_type="application/octet-stream",
            headers={"X-Chunk-Count": str(K), "X-Face-Count": str(Nt)},
        )

    # Optional set filter — same logic as render-buffers
    if set_name is not None:
        manifest = ManifestRepo(idx.workspace)
        render_rows = manifest.get_user_set_render_rows(set_name, instance)
        if render_rows is None:
            elem_labels = manifest.get_element_set_labels(set_name, instance)
            if elem_labels is not None and len(elem_labels) > 0:
                face_mask = get_face_mask_for_elem_labels(
                    idx, instance, set(elem_labels.tolist())
                )
                render_rows = np.where(face_mask)[0].astype(np.int32)
        if render_rows is not None and len(render_rows) > 0:
            positions, indices = _compact_by_render_rows(
                positions, indices, render_rows
            )

    sections, K, Nt = _build_chunked_payload(positions, indices)
    return Response(
        content=l3be_build(sections),
        media_type="application/octet-stream",
        headers={"X-Chunk-Count": str(K), "X-Face-Count": str(Nt)},
    )


@router.get("/geometry/{instance}/element-mesh-edges", dependencies=[Depends(_l2_ready)])
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


@router.get("/geometry/{instance}/feature-edges", dependencies=[Depends(_l2_ready)])
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


@router.get("/geometry/{instance}/lines", dependencies=[Depends(_l2_ready)])
async def get_line_elements(odb_id: str, instance: str):
    """
    Return beam/truss line element segments for one instance as L3BE binary.

    Line elements (B31, B32, T3D2, T3D3, PIPE31, PIPE32, …) have no surface
    faces and are not included in the triangle render buffers.  This endpoint
    returns their endpoint positions so the frontend can render them as
    THREE.LineSegments overlaid on the surface mesh.

    Section layout:
      - "line_positions": [N*2, 3] float32
            Interleaved endpoint pairs — row 2i and 2i+1 are the two endpoints
            of segment i.  Ready to feed into a Three.js LineSegments geometry.
      - "elem_labels":    [N]   int32
            Abaqus element label for each segment (for picking).

    Header:
      X-Line-Count: number of line segments N (not rows)
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
        if "lines/positions" not in f:
            empty_pos = np.zeros((0, 3), dtype=np.float32)
            empty_lbl = np.zeros(0, dtype=np.int32)
            return Response(
                content=l3be_build([("line_positions", empty_pos),
                                    ("elem_labels", empty_lbl)]),
                media_type="application/octet-stream",
            )

        positions   = f["lines/positions"][:]    # [N, 2, 3] float32
        elem_labels = f["lines/elem_labels"][:]  # [N] int32

    N = len(positions)
    line_positions = np.ascontiguousarray(positions.reshape(N * 2, 3))

    return Response(
        content=l3be_build([("line_positions", line_positions),
                             ("elem_labels", elem_labels)]),
        media_type="application/octet-stream",
    )


@router.post("/geometry/{instance}/render-buffers-subset", dependencies=[Depends(_l2_ready)])
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


def _build_chunked_payload(positions: np.ndarray, indices: np.ndarray):
    """
    Helper shared by the GET and POST chunked endpoints. Returns
    (sections list, chunk_count, total_face_count) ready for l3be_build.
    """
    chunks = _build_render_chunks(positions, indices)
    K = len(chunks)
    if K == 0:
        positions_concat  = np.zeros((0, 3), dtype=np.float32)
        positions_offsets = np.zeros(1, dtype=np.int32)
        indices_concat    = np.zeros((0, 3), dtype=np.int32)
        indices_offsets   = np.zeros(1, dtype=np.int32)
        face_idx_base     = np.zeros(0, dtype=np.int32)
        vertex_global_id  = np.zeros(0, dtype=np.int32)
    else:
        positions_concat = np.concatenate([c["positions"] for c in chunks], axis=0).astype(np.float32, copy=False)
        indices_concat   = np.concatenate([c["indices"]   for c in chunks], axis=0).astype(np.int32,   copy=False)
        vertex_global_id = np.concatenate([c["vertex_global_id"] for c in chunks]).astype(np.int32, copy=False)
        pos_lens = np.array([len(c["positions"]) for c in chunks], dtype=np.int32)
        idx_lens = np.array([len(c["indices"])   for c in chunks], dtype=np.int32)
        positions_offsets = np.concatenate(([0], np.cumsum(pos_lens))).astype(np.int32)
        indices_offsets   = np.concatenate(([0], np.cumsum(idx_lens))).astype(np.int32)
        face_idx_base     = np.array([c["face_idx_base"] for c in chunks], dtype=np.int32)

    sections = [
        ("chunk_count",       np.array([K], dtype=np.int32)),
        ("positions_concat",  positions_concat),
        ("positions_offsets", positions_offsets),
        ("indices_concat",    indices_concat),
        ("indices_offsets",   indices_offsets),
        ("face_idx_base",     face_idx_base),
        ("vertex_global_id",  vertex_global_id),
    ]
    Nt = int(indices_offsets[-1]) if K > 0 else 0
    return sections, K, Nt


@router.post("/geometry/{instance}/render-buffers-subset-chunked", dependencies=[Depends(_l2_ready)])
async def get_render_buffers_subset_chunked(
    odb_id: str,
    instance: str,
    body: ElemSubsetRequest,
):
    """
    Chunked variant of POST /render-buffers-subset.

    Same body/semantics; output format mirrors GET /render-buffers-chunked
    (see docs/l3/Binary-Payload-Spec.md §18).
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

    labels_set = set(body.elem_labels)
    face_mask = get_face_mask_for_elem_labels(idx, instance, labels_set)
    render_rows = np.where(face_mask)[0].astype(np.int32)

    with h5py.File(render_h5, "r") as f:
        positions = np.ascontiguousarray(f["render/positions"][:])
        indices   = np.ascontiguousarray(f["render/indices"][:]) \
                    if "render/indices" in f else None

    if len(render_rows) == 0 or indices is None:
        sections, K, Nt = _build_chunked_payload(
            np.zeros((0, 3), dtype=np.float32),
            np.zeros((0, 3), dtype=np.int32),
        )
    else:
        positions, indices = _compact_by_render_rows(positions, indices, render_rows)
        sections, K, Nt = _build_chunked_payload(positions, indices)

    return Response(
        content=l3be_build(sections),
        media_type="application/octet-stream",
        headers={"X-Chunk-Count": str(K), "X-Face-Count": str(Nt)},
    )
