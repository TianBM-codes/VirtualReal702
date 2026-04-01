"""
GET /api/odb/{odb_id}/results/frame-colors
  → L3BE binary: color_per_vertex [Rf*3, 4] uint8 + legend_range [2] float32

Pick 单面精确值复用已有 /query/pick 接口，无需重复实现。
"""
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services.result_service import frame_colors

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["results"])

Component = Literal["U1", "U2", "U3", "USUM"]


@router.get("/results/frame-colors")
async def get_frame_colors(
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame: int = 0,
    component: Component = "USUM",
    mode: str = Query("smooth", pattern="^(smooth|flat)$"),
):
    colors, legend = frame_colors(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        field=field,
        frame_idx=frame,
        component=component,
        render_mode=mode,
    )

    payload = l3be_build([
        ("color_per_vertex", colors),   # [Rf*3, 4] uint8
        ("legend_range",     legend),   # [2] float32
    ])

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Payload-Type":    "frame_colors_v1",
            "X-Layout-Version":  "1",
            "X-Val-Min":         str(float(legend[0])),
            "X-Val-Max":         str(float(legend[1])),
            "X-Component":       component,
            "X-Frame":           str(frame),
        },
    )
