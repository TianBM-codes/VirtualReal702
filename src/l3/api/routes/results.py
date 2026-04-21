"""
GET /api/odb/{odb_id}/results/frame-colors
  → L3BE binary: color_per_vertex [Rf*3, 4] uint8 + legend_range [2] float32

GET /api/odb/{odb_id}/results/frame-scalars
  → L3BE binary: u_per_vertex [Nv] float32 + legend_range [2] float32
  → Frontend applies any colormap

Pick 单面精确值复用已有 /query/pick 接口，无需重复实现。
"""
from typing import Literal, Optional

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services.result_service import frame_colors, frame_scalars, frame_deformed_positions

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
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
    set: Optional[str] = Query(None, description="User set name to filter triangles"),
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
        result_group=result_group,
        set_name=set,
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


@router.get("/results/frame-scalars")
async def get_frame_scalars(
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame: int = 0,
    component_idx: Optional[int] = Query(default=None, ge=0),
    mode: str = Query("smooth", pattern="^(smooth|flat)$"),
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
    set: Optional[str] = Query(None, description="User set name to filter triangles"),
    feature_angle: Optional[float] = Query(
        default=20.0,
        description="Shell/membrane geometric split angle (degrees). Omit to disable."
    ),
    average_threshold: float = Query(
        default=0.75,
        ge=0.0, le=1.0,
        description="75% threshold for conditional node averaging.",
    ),
    use_geometry_split: bool = Query(
        default=True,
        description="False = section-only domains, ignore feature_angle.",
    ),
):
    """
    Return per-vertex normalized scalar t ∈ [0,1] + legend range.
    Frontend applies any colormap to produce colors.

    component_idx: 0-based index into the result component axis.
                   Omit (or null) to get the L2 magnitude.
    mode: smooth (per-vertex NODAL interpolation) | flat (per-element average).
    set: optional user set name; returns only vertices for triangles in that set.
    feature_angle: angle threshold for shell/membrane domain splitting (default 20°).
    average_threshold: 0.75 = Abaqus default 75% threshold for conditional averaging.
    use_geometry_split: set to false to split only by section, ignoring geometry.
    """
    u, legend, result_position = frame_scalars(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        field=field,
        frame_idx=frame,
        component_idx=component_idx,
        render_mode=mode,
        result_group=result_group,
        set_name=set,
        feature_angle=feature_angle,
        average_threshold=average_threshold,
        use_geometry_split=use_geometry_split,
    )

    payload = l3be_build([
        ("u_per_vertex", u),       # [Nv] float32
        ("legend_range", legend),  # [2]  float32
    ])

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Payload-Type":         "frame_scalars_v1",
            "X-Result-Position":      result_position,
            "X-Normalization-Scope":  "instance",
            "X-Val-Min":              str(float(legend[0])),
            "X-Val-Max":              str(float(legend[1])),
            "X-Component-Idx":        str(component_idx) if component_idx is not None else "mag",
            "X-Frame":                str(frame),
            "X-Feature-Angle":        str(feature_angle) if feature_angle is not None else "none",
            "X-Average-Threshold":    str(average_threshold),
            "X-Use-Geometry-Split":   str(use_geometry_split).lower(),
        },
    )


@router.get("/results/deformed-positions")
async def get_deformed_positions(
    odb_id: str,
    instance: str,
    step: str,
    frame: int = 0,
    scale: float = Query(1.0, description="Deformation scale factor"),
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
):
    """
    Return deformed vertex positions [Nv, 3] float32 as L3BE binary.

    Computes: original_positions + scale * U_displacement_per_vertex.
    Requires U NODAL field in the given step.  Only supports indexed geometry.
    """
    positions, normals = frame_deformed_positions(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        frame_idx=frame,
        scale=scale,
        result_group=result_group,
    )

    payload = l3be_build([
        ("positions", positions),   # [Nv, 3] float32
        ("normals",   normals),     # [Nv, 3] float32  — pre-computed, skip JS computeVertexNormals
    ])
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Vertex-Count": str(len(positions)),
            "X-Frame":        str(frame),
            "X-Scale":        str(scale),
        },
    )
