"""
GET /api/odb/{odb_id}/results/frame-colors
  → L3BE binary: color_per_vertex [Rf*3, 4] uint8 + legend_range [2] float32

GET /api/odb/{odb_id}/results/frame-scalars
  → L3BE binary: u_per_vertex [Nv] float32 + legend_range [2] float32
  → Frontend applies any colormap

Pick 单面精确值复用已有 /query/pick 接口，无需重复实现。
"""
import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...core.errors import NotFoundError as CoreNotFoundError
from ...core.state import registry
from ...infra.manifest_repo import ManifestRepo
from ...infra.l3be import build as l3be_build
from ..response import ok
from ...services.result_service import (
    compute_scalar_range,
    frame_colors,
    frame_scalars,
    frame_deformed_positions,
    frame_vertex_displacements,
    suggest_deform_scale,
    modal_shape_displacement,
    modal_animation_frames,
)

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["results"])

Component = Literal["U1", "U2", "U3"]
_FRAME_ALIAS_RE = re.compile(r"^(?P<field>.+)__FRAME_(?P<frame>\d+)$")


def _normalize_step_token(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _source_field_name(field: str) -> str:
    match = _FRAME_ALIAS_RE.match(str(field or ""))
    if not match:
        return str(field or "")
    return str(match.group("field"))


def _resolve_result_targets(
    *,
    odb_id: str,
    step: str,
    field: str,
    requested_result_groups: Optional[List[Optional[str]]] = None,
) -> List[tuple[Optional[str], str]]:
    idx = registry.get(odb_id)
    if idx is None:
        raise CoreNotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    manifest = ManifestRepo(idx.workspace)
    requested_groups = list(requested_result_groups or [])
    source_field = _source_field_name(field)
    field_names = [str(field)]
    if source_field != str(field):
        field_names.append(source_field)
    normalized_step = _normalize_step_token(step)

    with manifest._get_conn() as conn:
        sql = [
            "SELECT DISTINCT result_group, step_name",
            "FROM result_files",
            f"WHERE field_name IN ({', '.join(['?'] * len(field_names))})",
        ]
        params: List[object] = list(field_names)
        if requested_groups:
            null_requested = any(group is None for group in requested_groups)
            explicit_groups = [str(group) for group in requested_groups if group is not None]
            clauses: List[str] = []
            if explicit_groups:
                clauses.append(f"result_group IN ({', '.join(['?'] * len(explicit_groups))})")
                params.extend(explicit_groups)
            if null_requested:
                clauses.append("result_group IS NULL")
            if clauses:
                sql.append(f"AND ({' OR '.join(clauses)})")
        rows = conn.execute("\n".join(sql), params).fetchall()

    if not rows:
        fallback_groups = requested_groups or [None]
        return [(group, str(step)) for group in fallback_groups]

    candidates = [(row["result_group"], str(row["step_name"] or "")) for row in rows]
    exact = [item for item in candidates if item[1] == str(step)]
    if exact:
        return exact

    normalized = [item for item in candidates if _normalize_step_token(item[1]) == normalized_step]
    if normalized:
        return normalized

    unique_steps = {item[1] for item in candidates if item[1]}
    if len(unique_steps) == 1:
        only_step = next(iter(unique_steps))
        fallback_groups = requested_groups or [item[0] for item in candidates]
        return [(group, only_step) for group in fallback_groups]

    fallback_groups = requested_groups or [item[0] for item in candidates]
    return [(group, str(step)) for group in fallback_groups]


def _resolve_single_result_target(
    *,
    odb_id: str,
    step: str,
    field: str,
    result_group: Optional[str],
) -> tuple[Optional[str], str]:
    return _resolve_result_targets(
        odb_id=odb_id,
        step=step,
        field=field,
        requested_result_groups=[result_group],
    )[0]


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
    resolved_result_group, resolved_step = _resolve_single_result_target(
        odb_id=odb_id,
        step=step,
        field=field,
        result_group=result_group,
    )
    colors, legend = frame_colors(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=resolved_step,
        field=field,
        frame_idx=frame,
        component=component,
        render_mode=mode,
        result_group=resolved_result_group,
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
    resolved_result_group, resolved_step = _resolve_single_result_target(
        odb_id=odb_id,
        step=step,
        field=field,
        result_group=result_group,
    )
    u, legend, result_position = frame_scalars(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=resolved_step,
        field=field,
        frame_idx=frame,
        component_idx=component_idx,
        render_mode=mode,
        result_group=resolved_result_group,
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
    result_group: Optional[List[str]] = Query(None),
    feature_angle: Optional[float] = Query(default=20.0),
    average_threshold: float = Query(default=0.75, ge=0.0, le=1.0),
    use_geometry_split: bool = Query(default=True),
):
    """
    Compute the union min/max across the given instances for use as a global
    normalization range.  Call this before frame-scalars when displaying multiple
    instances with a shared colormap.

    instances: comma-separated list of instance names.
    result_group: one or more result group names (repeat the param). Each is tried
                  in order; the first one that contains data for the given field wins.
    Returns JSON { global_min, global_max, instance_ranges: { name: [min, max] } }.
    """
    from ...core.errors import NotFoundError as _NFE

    inst_list = [s.strip() for s in instances.split(",") if s.strip()]
    instance_ranges: dict = {}
    global_min = float("inf")
    global_max = float("-inf")

    for inst in inst_list:
        rng = None
        targets = _resolve_result_targets(
            odb_id=odb_id,
            step=step,
            field=field,
            requested_result_groups=result_group,
        )
        for rg, resolved_step in targets:
            try:
                rng = compute_scalar_range(
                    registry=registry,
                    odb_id=odb_id,
                    instance=inst,
                    step=resolved_step,
                    field=field,
                    frame_idx=frame,
                    component_idx=component_idx,
                    render_mode=mode,
                    result_group=rg,
                    feature_angle=feature_angle,
                    average_threshold=average_threshold,
                    use_geometry_split=use_geometry_split,
                )
                if rng is not None:
                    break
            except _NFE:
                continue

        if rng is not None:
            v_min, v_max = rng
            instance_ranges[inst] = [v_min, v_max]
            if v_min < global_min:
                global_min = v_min
            if v_max > global_max:
                global_max = v_max

    if not instance_ranges:
        return ok({"global_min": None, "global_max": None, "instance_ranges": {}})

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


@router.get("/results/vertex-displacements")
async def get_vertex_displacements(
    odb_id: str,
    instance: str,
    step: str,
    frame: int = 0,
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
):
    """
    Return raw U displacement per render vertex [Nv, 3] float32 as L3BE binary.

    No scale applied, no position offset — pure displacement values from the U NODAL field.
    Useful when the frontend wants to apply its own scale or compute displacement magnitude.
    Requires indexed geometry.
    """
    displacements = frame_vertex_displacements(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        frame_idx=frame,
        result_group=result_group,
    )

    payload = l3be_build([
        ("displacements", displacements),   # [Nv, 3] float32
    ])
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Vertex-Count": str(len(displacements)),
            "X-Frame":        str(frame),
        },
    )


@router.get("/results/deformed-normals")
async def get_deformed_normals(
    odb_id: str,
    instance: str,
    step: str,
    frame: int = 0,
    scale: float = Query(1.0, description="Deformation scale factor"),
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
):
    """
    Return per-vertex normals [Nv, 3] float32 for the deformed geometry as L3BE binary.

    Normals are recomputed from the deformed mesh (original_positions + scale * U).
    Useful when the frontend already has positions and only needs updated normals for lighting.
    Requires indexed geometry.
    """
    _, normals = frame_deformed_positions(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        frame_idx=frame,
        scale=scale,
        result_group=result_group,
    )

    payload = l3be_build([
        ("normals", normals),   # [Nv, 3] float32
    ])
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Vertex-Count": str(len(normals)),
            "X-Frame":        str(frame),
            "X-Scale":        str(scale),
        },
    )


@router.get("/results/modal-shape")
async def get_modal_shape(
    odb_id: str,
    instance: str,
    step: str,
    frame: int = 0,
    result_group: Optional[str] = Query(None),
):
    """
    返回指定模态阶次的顶点位移向量 [Nv, 3] float32（L3BE binary）。
    供前端 GPU shader 模式：上传为 vertex attribute，由着色器做 sin 动画。
    """
    disp = modal_shape_displacement(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        frame_idx=frame,
        result_group=result_group,
    )
    payload = l3be_build([("displacement", disp)])
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"X-Vertex-Count": str(len(disp)), "X-Frame": str(frame)},
    )


@router.get("/results/modal-animation")
async def get_modal_animation(
    odb_id: str,
    instance: str,
    step: str,
    frame: int = 0,
    scale: float = Query(1.0),
    n_frames: int = Query(20, ge=4, le=120),
    result_group: Optional[str] = Query(None),
):
    """
    预计算 n_frames 帧谐波动画坐标，一次性返回。

    二进制格式：[n_frames uint32][n_verts uint32][n_frames × n_verts × 3 × float32]
    供前端预计算模式：收到后缓存，播放时只做 buffer 切换。
    """
    data = modal_animation_frames(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        step=step,
        frame_idx=frame,
        scale=scale,
        n_frames=n_frames,
        result_group=result_group,
    )
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-N-Frames": str(n_frames),
            "X-Frame":    str(frame),
            "X-Scale":    str(scale),
        },
    )


@router.get("/results/deform-suggest-scale")
async def get_deform_suggest_scale(
    odb_id: str,
    step: str,
    frame: int = 0,
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
):
    """
    Return a suggested deformation scale factor for the given step/frame.

    Computed globally across ALL instances (independent of which instances are displayed):
      maxBboxEdge of assembly / 10 / maxAbsDisplacement across all instances.
    Returns 0 when displacement is zero or U field is unavailable.
    """
    scale = suggest_deform_scale(
        registry=registry,
        odb_id=odb_id,
        step=step,
        frame_idx=frame,
        result_group=result_group,
    )
    return ok({"scale": scale})
