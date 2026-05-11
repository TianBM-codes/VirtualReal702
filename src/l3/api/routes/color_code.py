"""
GET /api/odb/{odb_id}/color-code/{instance}/schemes
  → JSON  {schemes: [...], elsets: [...]}

GET /api/odb/{odb_id}/color-code/{instance}?scheme=etype|material|section_type|elset
                                            [&set_names=Set-1,Set-2,...]
  → L3BE binary: color_per_vertex [Rf*3, 3] float32
  → X-Color-Legend: JSON  [{id, name, r, g, b}, ...]
  → X-Face-Count: Rf
"""
import json
import os
from typing import Dict, List

import h5py
import numpy as np

from fastapi import APIRouter, Body, Query
from fastapi.responses import Response

from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...infra.manifest_repo import ManifestRepo
from ...services import color_service
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["color_code"])


@router.get("/color-code/{instance}/display-names")
async def get_display_names(
    odb_id: str,
    instance: str,
    scheme: str = Query(None, description="etype | material | section_type | section | elset; 省略时返回所有 scheme"),
):
    """Return user-defined display names.

    With scheme: {legend_key: display_name}
    Without scheme: {scheme: {legend_key: display_name}, ...}
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    repo = ManifestRepo(idx.workspace)
    if scheme:
        return ok(repo.get_display_names(instance, scheme))
    # Return all schemes
    try:
        with repo._get_conn() as conn:
            rows = conn.execute(
                "SELECT scheme, legend_key, display_name FROM display_names WHERE instance=?",
                (instance,),
            ).fetchall()
        result: Dict[str, Dict[str, str]] = {}
        for r in rows:
            result.setdefault(r["scheme"], {})[r["legend_key"]] = r["display_name"]
        return ok(result)
    except Exception:
        return ok({})


@router.get("/color-code/legend-entries")
async def get_all_legend_entries(
    odb_id: str,
    scheme: str = Query(..., description="etype | material | section_type | section | elset"),
    set_names: str = Query("", description="Comma-separated set names (scheme=elset only)"),
):
    """Return legend entries for all instances.

    Same fields as the per-instance endpoint, plus an 'instance' field on each entry.
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    parsed_sets = [s.strip() for s in set_names.split(",") if s.strip()]
    entries = color_service.get_all_legend_entries(idx, scheme, parsed_sets or None)
    return ok({"entries": entries})


@router.get("/color-code/{instance}/legend-entries")
async def get_legend_entries(
    odb_id: str,
    instance: str,
    scheme: str = Query(..., description="etype | material | section_type | section | elset"),
    set_names: str = Query("", description="Comma-separated set names (scheme=elset only)"),
):
    """Return legend entries for the given scheme with face counts and user overrides.

    Each entry: {legend_key, default_title, display_name, color_r/g/b,
                 user_color, user_name, face_count}
    Used by the LegendEditor floating panel.
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    parsed_sets = [s.strip() for s in set_names.split(",") if s.strip()]
    entries = color_service.get_legend_entries(idx, instance, scheme, parsed_sets or None)
    return ok({"entries": entries})


@router.post("/color-code/{instance}/legend-entries")
async def post_legend_entries(
    odb_id: str,
    instance: str,
    scheme: str = Query(..., description="etype | material | section_type | section | elset"),
    body: List[dict] = Body(..., description="[{legend_key, display_name?, color_r/g/b?}, ...]"),
):
    """Upsert display name and color overrides for legend entries.

    Each item: {legend_key (required), display_name (str|null), color_r/g/b (float|null)}.
    Pass null to clear an override.
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    ManifestRepo(idx.workspace).set_legend_overrides(instance, scheme, body)
    return ok({})


@router.get("/color-code/{instance}/legend")
async def get_legend(
    odb_id: str,
    instance: str,
    scheme: str = Query(..., description="etype | material | section_type | section | elset"),
    set_names: str = Query("", description="Comma-separated set names (scheme=elset only)"),
):
    """Return only the color legend without building vertex colors.

    Cheaper than GET /color-code/{instance} when you only need the color mapping
    (e.g. populating a legend panel or color picker).

    Response: {legend: [{id, legend_key, name, r, g, b}, ...]}
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    parsed_sets = [s.strip() for s in set_names.split(",") if s.strip()]
    legend = color_service.get_legend(idx, instance, scheme, parsed_sets or None)
    return ok({"legend": legend})


@router.post("/color-code/{instance}/display-names")
async def post_display_names(
    odb_id: str,
    instance: str,
    scheme: str = Query(..., description="etype | material | section_type | section | elset"),
    body: Dict[str, str] = Body(..., description="{legend_key: display_name, ...}"),
):
    """Upsert user-defined display names for legend items."""
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    ManifestRepo(idx.workspace).set_display_names(instance, scheme, body)
    return ok({})


@router.get("/color-code/{instance}/schemes")
async def get_color_schemes(odb_id: str, instance: str):
    """List available coloring schemes and element set names for this instance."""
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    return ok(color_service.get_schemes(idx, instance))


@router.get("/color-code/{instance}")
async def get_color_code(
    odb_id: str,
    instance: str,
    scheme: str = Query(..., description="etype | material | section_type | elset"),
    set_names: str = Query("", description="Comma-separated set names (scheme=elset)"),
):
    """
    Return per-vertex RGB colors for the requested coloring scheme.

    For scheme=elset, pass set_names as a comma-separated list.
    Each named set gets a distinct palette color; unlisted elements are grey.

    Response body: L3BE binary  'color_per_vertex' [Rf*3, 3] float32.
    Header X-Color-Legend: JSON [{id, name, r, g, b}].
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    parsed_sets: List[str] = [s.strip() for s in set_names.split(",") if s.strip()]
    colors, legend = color_service.get_color_code(
        idx, instance, scheme, parsed_sets or None
    )

    etype_arr = idx.source_elem_etype.get(instance)
    Rf = len(etype_arr) if etype_arr is not None else 0
    legend_bytes = np.frombuffer(json.dumps(legend).encode("utf-8"), dtype=np.uint8)
    payload = l3be_build([("color_per_vertex", colors), ("legend", legend_bytes)])

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Face-Count": str(Rf),
        },
    )


@router.get("/color-code/{instance}/region-mesh-edges")
async def get_region_mesh_edges(
    odb_id: str,
    instance: str,
    scheme: str = Query(..., description="section | etype"),
    region: str = Query(..., description="Region label, e.g. 'Region 1'"),
):
    """
    Return element mesh edges for one averaging region as L3BE binary.

    Edges whose both endpoints belong to the region's render vertices are kept.
    This gives a true element-boundary wireframe (quads shown as quads, not split
    triangles) for the selected region only.

    Section layout:
      - "edge_positions": [E*2, 3] float32

    Header:
      X-Edge-Count: number of edge pairs E
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    def _empty():
        return Response(
            content=l3be_build([("edge_positions", np.zeros((0, 3), dtype=np.float32))]),
            media_type="application/octet-stream",
            headers={"X-Edge-Count": "0"},
        )

    face_mask = color_service.region_face_mask(idx, instance, scheme, region)
    if face_mask is None or not np.any(face_mask):
        return _empty()

    render_indices = idx.render_indices.get(instance)
    vtx_node_row   = idx.vtx_node_row.get(instance)
    coords_global  = idx.coords_global.get(instance)
    if render_indices is None or vtx_node_row is None or coords_global is None:
        return _empty()

    region_vert_rows = vtx_node_row[np.unique(render_indices[face_mask].ravel())]
    node_in_region = np.zeros(len(coords_global), dtype=bool)
    node_in_region[region_vert_rows] = True

    surface_h5 = os.path.join(idx.workspace, "l2", "geometry", f"{instance}_surface.h5")
    if not os.path.exists(surface_h5):
        return _empty()

    with h5py.File(surface_h5, "r") as f:
        if "element_mesh_edges/edge_nodes" not in f:
            return _empty()
        edge_nodes = f["element_mesh_edges/edge_nodes"][:]   # [E, 2] int32

    edge_mask = node_in_region[edge_nodes[:, 0]] & node_in_region[edge_nodes[:, 1]]
    filtered = np.unique(np.sort(edge_nodes[edge_mask], axis=1), axis=0)

    E = len(filtered)
    edge_positions = np.ascontiguousarray(
        coords_global[filtered.ravel()].reshape(E * 2, 3).astype(np.float32)
    )
    return Response(
        content=l3be_build([("edge_positions", edge_positions)]),
        media_type="application/octet-stream",
        headers={"X-Edge-Count": str(E)},
    )


@router.get("/color-code/{instance}/region-outline")
async def get_region_outline(
    odb_id: str,
    instance: str,
    scheme: str = Query(..., description="section | etype"),
    region: str = Query(..., description="Region label, e.g. 'Region 1'"),
):
    """
    Return the outer boundary of one averaging region as L3BE binary.

    Computes in-memory: edges shared by exactly one region triangle are boundary
    edges. Triangulation diagonals are excluded automatically because both
    sub-triangles of any quad belong to the same region (diagonal appears twice).

    Section layout:
      - "edge_positions": [E*2, 3] float32

    Header:
      X-Edge-Count: number of boundary edge pairs E
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    def _empty():
        return Response(
            content=l3be_build([("edge_positions", np.zeros((0, 3), dtype=np.float32))]),
            media_type="application/octet-stream",
            headers={"X-Edge-Count": "0"},
        )

    face_mask = color_service.region_face_mask(idx, instance, scheme, region)
    if face_mask is None or not np.any(face_mask):
        return _empty()

    render_indices = idx.render_indices.get(instance)
    vtx_node_row   = idx.vtx_node_row.get(instance)
    coords_global  = idx.coords_global.get(instance)
    if render_indices is None or vtx_node_row is None or coords_global is None:
        return _empty()

    region_tris = render_indices[face_mask]          # [Rf_reg, 3] vertex indices
    v0, v1, v2 = region_tris[:, 0], region_tris[:, 1], region_tris[:, 2]

    # Convert to node rows: vertices are NOT shared across element boundaries,
    # but same-position vertices of adjacent elements have the same vtx_node_row.
    # Deduplicating edges by node-row pairs correctly collapses interior edges
    # (which appear in two adjacent triangles) and retains only boundary edges.
    n0 = vtx_node_row[v0]
    n1 = vtx_node_row[v1]
    n2 = vtx_node_row[v2]

    edges = np.concatenate([
        np.stack([np.minimum(n0, n1), np.maximum(n0, n1)], axis=1),
        np.stack([np.minimum(n1, n2), np.maximum(n1, n2)], axis=1),
        np.stack([np.minimum(n0, n2), np.maximum(n0, n2)], axis=1),
    ])   # [3*Rf_reg, 2]  — node row pairs

    N_nodes = len(coords_global)
    packed = edges[:, 0].astype(np.int64) * N_nodes + edges[:, 1].astype(np.int64)
    unique_packed, counts = np.unique(packed, return_counts=True)
    boundary_packed = unique_packed[counts == 1]

    if len(boundary_packed) == 0:
        return _empty()

    boundary_edges = np.stack([
        (boundary_packed // N_nodes).astype(np.int32),
        (boundary_packed %  N_nodes).astype(np.int32),
    ], axis=1)   # [E_boundary, 2]  — node row pairs

    E = len(boundary_edges)
    edge_positions = np.ascontiguousarray(
        coords_global[boundary_edges.ravel()].reshape(E * 2, 3).astype(np.float32)
    )
    return Response(
        content=l3be_build([("edge_positions", edge_positions)]),
        media_type="application/octet-stream",
        headers={"X-Edge-Count": str(E)},
    )
