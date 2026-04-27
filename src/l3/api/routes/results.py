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
from ..response import ok
from ...services.result_service import (
    compute_scalar_range,
    frame_colors,
    frame_scalars,
    frame_deformed_positions,
    suggest_deform_scale,
)

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["results"])

Component = Literal["U1", "U2", "U3"]


@router.get("/results/frame-colors")
async def get_frame_colors(
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame: int = 0,
    component: Component = "U1",
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
    global_min: Optional[float] = Query(None, description="Override normalization min (global mode)."),
    global_max: Optional[float] = Query(None, description="Override normalization max (global mode)."),
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
    global_min/global_max: when both provided, skip per-instance range and normalize
                           against the supplied global range (multi-instance mode).
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
        override_min=global_min,
        override_max=global_max,
    )

    norm_scope = "global" if (global_min is not None and global_max is not None) else "instance"

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
            "X-Normalization-Scope":  norm_scope,
            "X-Val-Min":              str(float(legend[0])),
            "X-Val-Max":              str(float(legend[1])),
            "X-Component-Idx":        str(component_idx) if component_idx is not None else "mag",
            "X-Frame":                str(frame),
            "X-Feature-Angle":        str(feature_angle) if feature_angle is not None else "none",
            "X-Average-Threshold":    str(average_threshold),
            "X-Use-Geometry-Split":   str(use_geometry_split).lower(),
        },
    )


@router.get("/results/frame-scalar-range")
async def get_frame_scalar_range(
    odb_id: str,
    instances: str,
    step: str,
    field: str,
    frame: int = 0,
    component_idx: Optional[int] = Query(default=None, ge=0),
    mode: str = Query("smooth", pattern="^(smooth|flat)$"),
    result_group: Optional[str] = Query(None),
    feature_angle: Optional[float] = Query(default=20.0),
    average_threshold: float = Query(default=0.75, ge=0.0, le=1.0),
    use_geometry_split: bool = Query(default=True),
):
    """
    Compute the union min/max across the given instances for use as a global
    normalization range.  Call this before frame-scalars when displaying multiple
    instances with a shared colormap.

    instances: comma-separated list of instance names.
    Returns JSON { global_min, global_max, instance_ranges: { name: [min, max] } }.
    """
    from ...core.errors import NotFoundError as _NFE

    inst_list = [s.strip() for s in instances.split(",") if s.strip()]
    instance_ranges: dict = {}
    global_min = float("inf")
    global_max = float("-inf")

    for inst in inst_list:
        try:
            rng = compute_scalar_range(
                registry=registry,
                odb_id=odb_id,
                instance=inst,
                step=step,
                field=field,
                frame_idx=frame,
                component_idx=component_idx,
                render_mode=mode,
                result_group=result_group,
                feature_angle=feature_angle,
                average_threshold=average_threshold,
                use_geometry_split=use_geometry_split,
            )
        except _NFE:
            rng = None

        if rng is not None:
            v_min, v_max = rng
            instance_ranges[inst] = [v_min, v_max]
            if v_min < global_min:
                global_min = v_min
            if v_max > global_max:
                global_max = v_max

    if not instance_ranges:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"No result data found for field '{field}' in any of the requested instances.",
        )

    return ok({
        "global_min": global_min,
        "global_max": global_max,
        "instance_ranges": instance_ranges,
    })


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


@router.get("/results/deform-suggest-scale")
async def get_deform_suggest_scale(
    odb_id: str,
    instance: str,
    step: str,
    frame: int = 0,
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
):
    """
    Return a suggested deformation scale factor for the given step/frame.

    Computed as: maxBboxEdge / 10 / maxAbsDisplacement.
    Returns 0 when displacement is zero or U field is unavailable.
    """
    scale = suggest_deform_scale(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        frame_idx=frame,
        result_group=result_group,
    )
    return ok({"scale": scale})
