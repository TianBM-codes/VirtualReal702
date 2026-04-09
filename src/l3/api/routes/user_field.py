"""
User-defined field endpoints.

POST   /api/odb/{odb_id}/results/user-field
    Upload a scalar value for a named element set (one Txx result).

GET    /api/odb/{odb_id}/results/user-field-colors
    Render the user field as a cloud map (L3BE binary).

GET    /api/odb/{odb_id}/results/user-fields
    List all stored user fields for this ODB.

DELETE /api/odb/{odb_id}/results/user-field
    Remove a named user field.
"""
from typing import List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services.user_field_service import (
    delete_user_field,
    get_user_field_colors as svc_get_user_field_colors,
    list_user_fields,
    save_user_field,
)

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["user-field"])


# ── request body ──────────────────────────────────────────────────────────────

class UserFieldBody(BaseModel):
    name: str
    instance: str
    value: float
    element_labels: List[int]


# ── POST: create / update ─────────────────────────────────────────────────────

@router.post("/results/user-field", status_code=201)
async def post_user_field(odb_id: str, body: UserFieldBody):
    """
    Store a scalar value for all elements in the given set.
    Upserts: calling again with the same (name, instance) overwrites the previous value.
    """
    result = save_user_field(
        registry=registry,
        odb_id=odb_id,
        name=body.name,
        instance=body.instance,
        value=body.value,
        element_labels=body.element_labels,
    )
    return JSONResponse(status_code=201, content=result)


# ── GET: list ─────────────────────────────────────────────────────────────────

@router.get("/results/user-fields")
async def get_user_fields(
    odb_id: str,
    instance: Optional[str] = None,
):
    """List all user fields stored for this ODB, optionally filtered by instance."""
    fields = list_user_fields(registry=registry, odb_id=odb_id, instance=instance)
    return {"odb_id": odb_id, "fields": fields}


# ── GET: cloud-map colors ─────────────────────────────────────────────────────

@router.get("/results/user-field-colors")
async def get_user_field_colors(
    odb_id: str,
    name: str,
    instance: str,
    val_min: Optional[float] = Query(None),
    val_max: Optional[float] = Query(None),
):
    """
    Render a named user field as a per-vertex RGBA cloud map (L3BE binary).

    Elements in the set → jet color based on stored value.
    Elements outside    → neutral gray.

    val_min / val_max: normalization range for colormap.
    Omit both to auto-range (all same-value elements show jet midpoint).
    """
    colors, legend = svc_get_user_field_colors(
        registry=registry,
        odb_id=odb_id,
        name=name,
        instance=instance,
        val_min=val_min,
        val_max=val_max,
    )

    payload = l3be_build([
        ("color_per_vertex", colors),  # [Rf*3, 4] uint8
        ("legend_range",     legend),  # [2] float32
    ])

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Payload-Type":   "user_field_colors_v1",
            "X-Layout-Version": "1",
            "X-Val-Min":        str(float(legend[0])),
            "X-Val-Max":        str(float(legend[1])),
            "X-Field-Name":     name,
        },
    )


# ── DELETE ────────────────────────────────────────────────────────────────────

@router.delete("/results/user-field")
async def delete_user_field_endpoint(
    odb_id: str,
    name: str,
    instance: str,
):
    """Delete a named user field."""
    deleted = delete_user_field(
        registry=registry,
        odb_id=odb_id,
        name=name,
        instance=instance,
    )
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"detail": f"User field '{name}' not found for instance '{instance}'"},
        )
    return {"deleted": True, "name": name, "instance": instance}
