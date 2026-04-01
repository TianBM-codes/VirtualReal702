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
from typing import List

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services import color_service

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["color_code"])


@router.get("/color-code/{instance}/schemes")
async def get_color_schemes(odb_id: str, instance: str):
    """List available coloring schemes and element set names for this instance."""
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    return color_service.get_schemes(idx, instance)


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

    Rf = colors.shape[0] // 3
    payload = l3be_build([("color_per_vertex", colors)])

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Face-Count":   str(Rf),
            "X-Color-Legend": json.dumps(legend),
        },
    )
