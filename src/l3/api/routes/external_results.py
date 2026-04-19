"""
POST /api/odb/{odb_id}/results/external-field

Write custom nodal or element results into the ODB workspace HDF5 store.
The field appears in overview immediately and is readable by frame-colors/
frame-scalars without any other changes.

Request body (JSON):
{
  "step_name":    "opt_step",
  "field_name":   "RHO",
  "components":   ["RHO"],
  "result_group": "sag-run1",
  "instance":     "PART-1-1",
  "type":         "nodal" | "element",
  "frames": [
    {
      "frame_idx":   0,
      "frame_value": 0.0,
      "data": [{"label": 1, "values": [0.8]}, ...]
    }
  ]
}

Missing node/element labels receive NaN → rendered as 0 by nan_to_num.
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import List, Literal

from ...core.errors import NotFoundError, AppError
from ...core.state import registry
from ...services.external_result_writer import ExternalResultWriter

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["external-results"])


class FrameEntry(BaseModel):
    label: int
    values: List[float]


class FrameData(BaseModel):
    frame_idx: int
    frame_value: float = 0.0
    data: List[FrameEntry]


class ExternalFieldRequest(BaseModel):
    step_name: str
    field_name: str
    components: List[str]
    result_group: str
    instance: str
    type: Literal["nodal", "element"]
    frames: List[FrameData]


@router.post("/results/external-field")
async def write_external_field(odb_id: str, body: ExternalFieldRequest):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    frames_raw = [
        {
            "frame_idx":   f.frame_idx,
            "frame_value": f.frame_value,
            "data":        [{"label": e.label, "values": e.values} for e in f.data],
        }
        for f in body.frames
    ]

    writer = ExternalResultWriter(idx.workspace, body.result_group)

    try:
        if body.type == "nodal":
            n = writer.write_nodal(
                instance=body.instance,
                step=body.step_name,
                field=body.field_name,
                components=body.components,
                frames=frames_raw,
            )
        else:
            n = writer.write_element(
                instance=body.instance,
                step=body.step_name,
                field=body.field_name,
                components=body.components,
                frames=frames_raw,
            )
    except FileNotFoundError as exc:
        raise AppError(404, str(exc), {}) from exc
    except KeyError as exc:
        raise AppError(400, str(exc), {}) from exc

    return {
        "code": 0,
        "data": {
            "field_name":     body.field_name,
            "step_name":      body.step_name,
            "instance":       body.instance,
            "frames_written": n,
            "source":         "external",
        },
    }
