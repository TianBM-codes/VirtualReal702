"""
GET /api/odb/{odb_id}/results/section-mesh

Returns the cross-section Triangle Soup for an axis-aligned cutting plane
as an L3BE binary payload.

Query params:
  instance  str          — instance name
  axis      X | Y | Z    — cutting plane orientation
  position  float        — plane offset along the axis (world coordinates)

Response: L3BE binary with sections:
  "vertices"  [T*3, 3] float32  — flattened triangle vertices (3 verts per tri)

Response headers:
  X-Tri-Count   <int>    — number of triangles (0 if plane doesn't intersect)
  X-Axis        X|Y|Z
  X-Position    <float>
"""
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services.section_service import compute_section
import numpy as np

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["section"])


@router.get("/results/section-mesh")
async def get_section_mesh(
    odb_id: str,
    instance: str,
    axis: Literal["X", "Y", "Z"] = "Z",
    position: float = Query(0.0),
):
    tri_buf, edge_buf, tri_count = compute_section(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        axis=axis,
        position=position,
    )

    # Flatten buffers for the binary payload
    vertices   = tri_buf.reshape(-1, 3)   if tri_count > 0  else np.zeros((0, 3), dtype=np.float32)
    edge_verts = edge_buf.reshape(-1, 3)  if len(edge_buf) > 0 else np.zeros((0, 3), dtype=np.float32)
    edge_count = len(edge_buf)            # number of line segments

    payload = l3be_build([
        ("vertices",   vertices),    # [T*3, 3] float32 — triangle fill
        ("edge_verts", edge_verts),  # [E*2, 3] float32 — polygon outline segments
    ])

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Payload-Type":    "section_mesh_v1",
            "X-Layout-Version":  "1",
            "X-Tri-Count":       str(tri_count),
            "X-Edge-Count":      str(edge_count),
            "X-Axis":            axis,
            "X-Position":        str(position),
        },
    )
