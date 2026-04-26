"""
POST /api/odb/{odb_id}/results/external-field

Write custom nodal or element results into the ODB workspace HDF5 store.
Supports multiple instances in a single request. The field appears in overview
immediately and is readable by frame-colors/frame-scalars without any changes.

Request body (JSON):
{
  "step_name":    "opt_step",
  "field_name":   "RHO",
  "components":   ["RHO"],
  "result_group": "run1",
  "type":         "nodal" | "element",
  "instances": [
    {
      "instance": "PART-1-1",
      "frames": [
        {"frame_idx": 0, "frame_value": 0.0,
         "data": [{"label": 1, "values": [0.8]}, ...]}
      ]
    },
    {
      "instance": "PART-2-1",
      "frames": [...]
    }
  ]
}

Missing node/element labels receive NaN → rendered as 0 by nan_to_num.
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import List, Literal, Optional

from ...core.errors import NotFoundError, ValidationError
from ...core.state import registry
from ...services.external_result_writer import ExternalResultWriter
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["external-results"])


class FrameEntry(BaseModel):
    label: int
    values: List[float]


class FrameData(BaseModel):
    frame_idx: int
    frame_value: float = 0.0
    description: Optional[str] = None
    data: List[FrameEntry]


class InstanceData(BaseModel):
    instance: str
    frames: List[FrameData]


class ExternalFieldRequest(BaseModel):
    step_name: str
    field_name: str
    components: List[str]
    result_group: str
    type: Literal["nodal", "element"]
    instances: List[InstanceData]


@router.post("/results/external-field")
async def write_external_field(odb_id: str, body: ExternalFieldRequest):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    writer = ExternalResultWriter(idx.workspace, body.result_group)
    total_frames = 0

    try:
        for inst_data in body.instances:
            frames_raw = [
                {
                    "frame_idx":   f.frame_idx,
                    "frame_value": f.frame_value,
                    "description": f.description,
                    "data":        [{"label": e.label, "values": e.values} for e in f.data],
                }
                for f in inst_data.frames
            ]
            if body.type == "nodal":
                n = writer.write_nodal(
                    instance=inst_data.instance,
                    step=body.step_name,
                    field=body.field_name,
                    components=body.components,
                    frames=frames_raw,
                )
            else:
                n = writer.write_element(
                    instance=inst_data.instance,
                    step=body.step_name,
                    field=body.field_name,
                    components=body.components,
                    frames=frames_raw,
                )
            total_frames = max(total_frames, n)
    except FileNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc

    return ok({
        "field_name":        body.field_name,
        "step_name":         body.step_name,
        "instances_written": len(body.instances),
        "frames_written":    total_frames,
        "source":            "external",
    })
