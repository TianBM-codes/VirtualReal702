from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BBoxRequest(BaseModel):
    instance: str
    bbox_min: List[float] = Field(..., min_length=3, max_length=3)
    bbox_max: List[float] = Field(..., min_length=3, max_length=3)
    mode: Literal["intersect", "contained"] = "intersect"
    set_name: Optional[str] = None  # if provided, persist as user_set


class PickOdbInfo(BaseModel):
    """
    ODB topology / labelling info.
    Populated fields depend on pick_mode:
      element: elem_label, elem_type, elem_node_labels
      node:    node_label, elem_label, candidate_node_labels, orig_coords, def_coords
    """
    elem_label: Optional[int] = None
    elem_type: Optional[str] = None           # element mode: etype string e.g. "C3D8R"
    elem_node_labels: Optional[List[int]] = None
    node_label: Optional[int] = None
    candidate_node_labels: Optional[List[int]] = None
    orig_coords: Optional[List[float]] = None  # node mode: [x, y, z] undeformed
    def_coords: Optional[List[float]] = None   # node mode: orig + U * deform_scale
    attached_elem_labels: Optional[List[int]] = None  # node mode: elem labels sharing this node; None = old workspace


class PickResultInfo(BaseModel):
    """
    Result value semantics for a pick query.
    value_kind is always "odb_raw".
    source_elem_label clarifies which element the result was read from (relevant
    for node mode where S,Mises comes from the hit element, not all attached elements).
    """
    field: str
    position: str
    component: Optional[str] = None
    value_kind: str = "odb_raw"
    raw_value: Optional[float] = None
    raw_values: Optional[List[float]] = None
    display_value: Optional[float] = None
    source_elem_label: Optional[int] = None   # element the result was read from


class PickResponse(BaseModel):
    pick_mode: str                       # "element" | "node"
    instance: str
    render_face_idx: int
    render_face_indices: List[int] = []  # all face indices for the hit element
    odb: PickOdbInfo
    result: Optional[PickResultInfo] = None
    mises: Optional[float] = None        # Von Mises stress from hit element; None if S unavailable


class BBoxResponse(BaseModel):
    set_name: Optional[str]
    elem_count: int
    render_face_count: int
    elem_labels: Optional[List[int]] = None   # populated when elem_count <= 2000


class RenderFacesRequest(BaseModel):
    instance: str
    render_face_indices: List[int]
    mode: Literal["element", "node"] = "element"


class RenderFacesResponse(BaseModel):
    mode: str
    # element mode
    elem_count: int = 0
    elem_labels: Optional[List[int]] = None        # capped at 2000
    elem_face_indices: Optional[List[int]] = None  # ALL faces of selected elements
    elem_ids_per_face: Optional[List[int]] = None  # element group index per face (same length)
    # node mode
    node_count: int = 0
    node_labels: Optional[List[int]] = None        # capped at 2000
    node_positions: Optional[List[List[float]]] = None  # [[x,y,z],...] capped at 5000
