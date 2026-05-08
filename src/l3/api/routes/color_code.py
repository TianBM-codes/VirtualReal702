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
from typing import Dict, List

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
    scheme: str = Query(..., description="etype | material | section_type | section | elset"),
):
    """Return user-defined display names: {legend_key: display_name}."""
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    return ok(ManifestRepo(idx.workspace).get_display_names(instance, scheme))


@router.put("/color-code/{instance}/display-names")
async def put_display_names(
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
