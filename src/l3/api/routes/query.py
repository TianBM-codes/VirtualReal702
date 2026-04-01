from typing import Literal, Optional
from fastapi import APIRouter, Query
from ...core.state import registry
from ...schemas.query import BBoxRequest, PickResponse, BBoxResponse, RenderFacesRequest, RenderFacesResponse
from ...services import query_service

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["query"])


@router.get("/query/pick", response_model=PickResponse)
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
    )
    return result


@router.post("/query/bbox", response_model=BBoxResponse)
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
    return result


@router.post("/query/render-faces", response_model=RenderFacesResponse)
async def render_faces_query(odb_id: str, body: RenderFacesRequest):
    """
    Resolve a list of render face indices (from a screen-space bbox selection)
    into element or node data.

    element mode: expands faces to full parent elements, returns elem_labels +
                  elem_face_indices (all triangles of selected elements).
    node mode:    collects unique nodes from face vertices, returns node_labels +
                  node_positions.
    """
    return query_service.resolve_render_faces(
        registry=registry,
        odb_id=odb_id,
        instance=body.instance,
        render_face_indices=body.render_face_indices,
        mode=body.mode,
    )
