"""
GET /api/odb/{odb_id}/geometry/{instance}/render-buffers
  → L3BE binary: positions [Rf*3, 3] float32

Returns the Triangle Soup vertex positions for a given instance, ready to feed
directly into a Three.js BufferGeometry position attribute.
"""
import os

import h5py
import numpy as np
from fastapi import APIRouter
from fastapi.responses import Response

from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.l3be import build as l3be_build

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["geometry"])


@router.get("/geometry/{instance}/render-buffers")
async def get_render_buffers(odb_id: str, instance: str):
    """
    Return the render vertex buffer for one instance as L3BE binary.

    Section layout:
      - "positions": [Rf*3, 3] float32  — triangle soup vertex XYZ
      - "normals":   [Rf*3, 3] float32  — per-vertex smooth normals (if available)

    Rf = number of surface triangles for this instance.
    Each face has exactly 3 consecutive vertices (no index buffer).
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
        positions_raw = f["render/positions"][:]   # [Rf, 3, 3] float32
        normals_raw = f["render/normals"][:] if "render/normals" in f else None

    Rf = positions_raw.shape[0]
    positions_flat = np.ascontiguousarray(positions_raw.reshape(Rf * 3, 3))

    sections = [("positions", positions_flat)]
    if normals_raw is not None:
        normals_flat = np.ascontiguousarray(normals_raw.reshape(Rf * 3, 3))
        sections.append(("normals", normals_flat))

    payload = l3be_build(sections)
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Face-Count": str(Rf),
        },
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
