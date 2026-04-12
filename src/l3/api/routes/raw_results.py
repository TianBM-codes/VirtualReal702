"""
GET /api/odb/{odb_id}/results/raw-values

Returns raw numerical result values for one frame.
Intended for downstream calculation workflows (fatigue, post-processing, etc.)
that need the actual numbers, not render colours.

Supports three result positions:
  NODAL              — one value per node
  ELEMENT_NODAL      — one value per element corner node (per element)
  INTEGRATION_POINT  — one value per integration point (per element)

Query parameters
----------------
instance  str   required  Instance name (e.g. "PART-1-1")
step      str   required  Step name (e.g. "Step-1")
field     str   required  Field name (e.g. "S", "U", "LE")
frame     int   optional  Frame index, default 0
position  str   required  NODAL | ELEMENT_NODAL | INTEGRATION_POINT
format    str   optional  json | l3be, default json

Response headers
----------------
X-Payload-Type   raw_values_v1
X-Position       NODAL | ELEMENT_NODAL | INTEGRATION_POINT
X-Frame          <frame index>
X-Components     JSON array of component names, e.g. ["S11","S22","S33","S12","S13","S23"]
X-Etype-Groups   JSON array of element type strings (empty for NODAL)
                 Names match the etype suffix used in the L3BE section names.

L3BE section layout
-------------------
NODAL:
  "node_labels"   [N]             int32    ODB node labels
  "values"        [N, ncomp]      float32  result values per node

ELEMENT_NODAL / INTEGRATION_POINT (one group per element type present):
  "el_{etype}"    [M]             int32    ODB element labels
  "v_{etype}"     [M, n_ip, ncomp] float32  result values
               or [M, ncomp]               if the HDF5 dataset has no IP/node sub-dim

  {etype} is the element type string sanitised to alphanumeric+underscore, e.g.
  "C3D8R" → section names "el_C3D8R" and "v_C3D8R".
  Section names are truncated to 32 chars (the L3BE limit).
"""
import json
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from fastapi.responses import Response

from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services.raw_result_service import get_raw_values, raw_values_to_json_payload

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["results"])

Position = Literal["NODAL", "ELEMENT_NODAL", "INTEGRATION_POINT"]
RawValuesFormat = Literal["json", "l3be"]


@router.get("/results/raw-values")
async def get_raw_result_values(
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame: int = Query(0, ge=0, description="Frame index (0-based)"),
    position: Position = Query(..., description="NODAL | ELEMENT_NODAL | INTEGRATION_POINT"),
    format: RawValuesFormat = Query("json", description="Response format: json | l3be"),
):
    sections, components, etype_groups = get_raw_values(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        field=field,
        frame_idx=frame,
        position=position,
    )

    if format == "json":
        return JSONResponse(
            content=raw_values_to_json_payload(
                sections=sections,
                components=components,
                etype_groups=etype_groups,
                position=position,
                odb_id=odb_id,
                instance=instance,
                step=step,
                field=field,
                frame_idx=frame,
            ),
            headers={
                "X-Payload-Type":  "raw_values_json_v1",
                "X-Layout-Version": "1",
                "X-Position":      position,
                "X-Frame":         str(frame),
                "X-Components":    json.dumps(components),
                "X-Etype-Groups":  json.dumps(etype_groups),
            },
        )

    payload = l3be_build(sections)

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Payload-Type":  "raw_values_v1",
            "X-Layout-Version": "1",
            "X-Position":      position,
            "X-Frame":         str(frame),
            "X-Components":    json.dumps(components),
            "X-Etype-Groups":  json.dumps(etype_groups),
        },
    )
