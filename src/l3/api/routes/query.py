from typing import List, Literal, Optional
from fastapi import APIRouter, Query
from ...core.state import registry
from ...schemas.query import BBoxRequest, PickResponse, BBoxResponse, RenderFacesRequest, RenderFacesResponse, NearestFaceResponse, SurfacePatchRequest, SurfacePatchResponse, RayPickRequest
from ...services import query_service
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["query"])


@router.get("/query/pick")
async def pick(
    odb_id: str,
    instance: str,
    render_face_idx: int,
    pick_mode: Literal["element", "node"] = Query("element"),
    step: Optional[str] = Query(None),
    field: Optional[str] = Query(None),
    frame_idx: Optional[int] = Query(None),
    component: Optional[str] = Query(None),
    # Explicit column index — required for non-displacement fields (S11→0, S22→1, …).
    # Takes priority over component name resolution when provided.
    component_idx: Optional[int] = Query(None, ge=0),
    # node pick only: which of the 3 candidate nodes to select (0, 1, or 2).
    # The frontend determines the nearest node via screen-space raycasting.
    node_idx: Optional[int] = Query(None, ge=0, le=2),
    # when True: return orig_coords and def_coords in odb info (node mode only)
    include_coords: bool = Query(False),
    # deformation scale factor for def_coords = orig + U * deform_scale
    deform_scale: float = Query(1.0, ge=0.0),
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
):
    result = query_service.pick(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        render_face_idx=render_face_idx,
        pick_mode=pick_mode,
        step=step,
        field=field,
        frame_idx=frame_idx,
        component=component,
        component_idx=component_idx,
        node_idx=node_idx,
        include_coords=include_coords,
        deform_scale=deform_scale,
        result_group=result_group,
    )
    return ok(result.model_dump())


@router.post("/query/ray-pick")
async def ray_pick(odb_id: str, body: RayPickRequest):
    """
    Ray-cast pick: frontend sends camera state + screen pixel coordinates,
    backend reconstructs the world-space ray and finds the nearest triangle.

    view_projection_matrix is the combined projection × view matrix.
    In Three.js:

        const vp = new THREE.Matrix4().multiplyMatrices(
            camera.projectionMatrix,
            camera.matrixWorldInverse
        );
        body.view_projection_matrix = [...vp.elements];   // 16 floats, column-major

    screen_x / screen_y are pixel coordinates with (0, 0) at the top-left corner,
    matching the DOM / canvas coordinate system.

    Returns the same PickResponse as GET /query/pick.
    """
    result = query_service.ray_pick(
        registry=registry,
        odb_id=odb_id,
        instance=body.instance,
        screen_x=body.screen_x,
        screen_y=body.screen_y,
        viewport_width=body.viewport_width,
        viewport_height=body.viewport_height,
        vp_matrix_col_major=body.view_projection_matrix,
        pick_mode=body.pick_mode,
        node_idx=body.node_idx,
        step=body.step,
        field=body.field,
        frame_idx=body.frame_idx,
        component_idx=body.component_idx,
        include_coords=body.include_coords,
        deform_scale=body.deform_scale,
    )
    return ok(result.model_dump())


@router.post("/query/bbox")
async def bbox_query(odb_id: str, body: BBoxRequest):
    result = query_service.bbox(
        registry=registry,
        odb_id=odb_id,
        instance=body.instance,
        bbox_min=body.bbox_min,
        bbox_max=body.bbox_max,
        mode=body.mode,
        set_name=body.set_name,
    )
    return ok(result.model_dump())


@router.post("/query/render-faces")
async def render_faces_query(odb_id: str, body: RenderFacesRequest):
    """
    Resolve a list of render face indices (from a screen-space bbox selection)
    into element or node data.

    element mode: expands faces to full parent elements, returns elem_labels +
                  elem_face_indices (all triangles of selected elements).
    node mode:    collects unique nodes from face vertices, returns node_labels +
                  node_positions.
    """
    result = query_service.resolve_render_faces(
        registry=registry,
        odb_id=odb_id,
        instance=body.instance,
        render_face_indices=body.render_face_indices,
        mode=body.mode,
    )
    return ok(result.model_dump())


@router.post("/query/surface-patch")
async def surface_patch_query(odb_id: str, body: SurfacePatchRequest):
    """
    Select all surface faces whose triangle overlaps an oriented rectangle (including edge-touching).

    The rectangle is defined by a center point, a normal vector (which becomes
    the plane normal), and width/height dimensions.  An optional up_hint vector
    orients the rectangle within the plane (default: global Y).

    Typical workflow:
      1. Call GET /query/nearest-face to get the center point and face normal.
      2. Call this endpoint with the returned normal and desired dimensions.

    Returns all matching render_face_indices (for frontend highlighting),
    elem_labels, node_labels, and node_positions.
    """
    result = query_service.surface_patch(
        registry=registry,
        odb_id=odb_id,
        req=body,
    )
    return ok(result.model_dump())


@router.get("/query/nearest-face")
async def nearest_face_query(
    odb_id: str,
    instance: str,
    x: float = Query(..., description="X coordinate of query point"),
    y: float = Query(..., description="Y coordinate of query point"),
    z: float = Query(..., description="Z coordinate of query point"),
):
    """
    Find the surface triangle face closest to an arbitrary point in 3-D space.

    The point does not need to coincide with a mesh node or element centroid.
    Uses the L2 octree for efficient search, then verifies the global optimum
    via a second-pass expansion query.

    Returns the closest face's unit normal vector, the closest point ON the face,
    and the Euclidean distance from the query point to that face.
    """
    result = query_service.nearest_face(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        point=[x, y, z],
    )
    return ok(result.model_dump())
