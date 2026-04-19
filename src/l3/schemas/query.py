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


class SurfacePatchRequest(BaseModel):
    instance: str
    center: List[float] = Field(..., min_length=3, max_length=3)
    normal: List[float] = Field(..., min_length=3, max_length=3)
    width: float = Field(..., gt=0)
    height: float = Field(..., gt=0)
    up_hint: List[float] = Field(default=[0.0, 1.0, 0.0], min_length=3, max_length=3)
    # Slab thickness along the normal direction.
    # Only faces whose centroid lies within ±depth/2 of the center plane are kept.
    # None (default) → auto: uses min(width, height) / 2.
    depth: Optional[float] = Field(default=None, gt=0)


class SurfacePatchResponse(BaseModel):
    face_count: int
    elem_count: int
    node_count: int
    render_face_indices: List[int]
    elem_labels: Optional[List[int]] = None    # null when elem_count > 2000
    node_labels: Optional[List[int]] = None    # null when node_count > 2000
    node_positions: Optional[List[List[float]]] = None  # null when node_count > 5000


class NearestFaceResponse(BaseModel):
    """Result of a nearest-face spatial query."""
    instance: str
    render_face_idx: int          # index into the triangle soup (stable per instance)
    elem_label: Optional[int]     # ODB element label of the owning element
    elem_type: Optional[str]      # element type string e.g. "C3D8R"
    normal: List[float]           # unit outward normal [nx, ny, nz]
    closest_point: List[float]    # closest point ON the face to the query point [x, y, z]
    distance: float               # Euclidean distance from query point to closest_point


class RayPickRequest(BaseModel):
    """
    Ray-cast pick request.  Frontend passes camera state + screen pixel coordinates;
    backend reconstructs the world-space ray and finds the nearest intersected triangle.

    view_projection_matrix
        Combined projection × view matrix from Three.js:
            const vp = new THREE.Matrix4().multiplyMatrices(
                camera.projectionMatrix, camera.matrixWorldInverse
            );
            body.view_projection_matrix = [...vp.elements];   // 16 floats, column-major
        The backend inverts this matrix to unproject screen coords to world space.
    """
    instance: str
    screen_x: float                # pixel X (0 = left edge)
    screen_y: float                # pixel Y (0 = top edge)
    viewport_width: float = Field(..., gt=0)
    viewport_height: float = Field(..., gt=0)
    view_projection_matrix: List[float] = Field(..., min_length=16, max_length=16)

    # ── same optional result-fetch params as GET /query/pick ──
    pick_mode: Literal["element", "node"] = "element"
    node_idx: Optional[int] = Field(default=None, ge=0, le=2)
    step: Optional[str] = None
    field: Optional[str] = None
    frame_idx: Optional[int] = None
    component_idx: Optional[int] = Field(default=None, ge=0)
    include_coords: bool = False
    deform_scale: float = Field(default=1.0, ge=0.0)
