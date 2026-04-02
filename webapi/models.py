from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ImportUnvRequest(BaseModel):
    file_path: str
    project_id: Optional[int] = None
    file_id: Optional[int] = None
    clear_before_insert: bool = True


class ImportBdfRequest(BaseModel):
    file_path: str
    project_id: int
    clear_before_insert: bool = True


class PlotModalShapeRequest(BaseModel):
    project_id: int


class DumpVtkRequest(BaseModel):
    vtk_path: str
    project_id: int


class DumpJsonRequest(BaseModel):
    json_path: str
    project_id: int


class ImportInpCatalogRequest(BaseModel):
    file_path: str
    project_id: int
    clear_before_insert: bool = True
    build_octree: bool = True
    force_rebuild_octree: bool = False


class InpCatalogRequest(BaseModel):
    project_id: int


class InpTreeRequest(BaseModel):
    file_path: str
    show_labels: bool = True
    max_labels: int = 8


class CreateOptimizationParameterRequest(BaseModel):
    project_id: int
    candidate_code: str
    set_name: str
    parameter_name: Optional[str] = None
    description: str = ""
    set_type: Optional[str] = None
    set_scope: Optional[str] = None
    instance_name: Optional[str] = None
    part_name: Optional[str] = None


class RotationRequest(BaseModel):
    center: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    axis: List[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0], min_length=3, max_length=3)
    angle_deg: float = 0.0


class MatchNodesRequest(BaseModel):
    project_id: int
    max_distance: Optional[float] = None
    overwrite: bool = True
    auto_translate: bool = True
    translation: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)
    rotation: Optional[RotationRequest] = None


class SensitivityBuildWorkspaceRequest(BaseModel):
    odb_path: str
    workspace: str
    abaqus: str = "abaqus"
    python3: Optional[str] = None
    keep_raw: bool = False


class SensitivityOverviewRequest(BaseModel):
    workspace: str


class SensitivityTableRequest(BaseModel):
    workspace: str
    step: Optional[str] = None
    field: Optional[str] = None
    instance: Optional[str] = None
    position: Optional[str] = None
    components: Optional[List[str]] = None
    frame_indices: Optional[List[int]] = None
    entity_labels: Optional[List[int]] = None
    aggregation: str = "max_abs"


class AbaqusSensitivityRunRequest(BaseModel):
    input_inp: str
    output_dir: Optional[str] = None
    response_elset: Optional[str] = None
    response_nset: Optional[str] = None
    response_frequency: int = 1
    node_vars: Optional[List[str]] = None
    element_vars: Optional[List[str]] = None
    abaqus: str = "abaqus"
    job_name: Optional[str] = None
    cpus: Optional[int] = None
    interactive: bool = True
    run_solver: bool = True
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)


class AbaqusAdjointRunRequest(BaseModel):
    input_inp: str
    output_inp: Optional[str] = None
    response_nset: Optional[str] = None
    abaqus: str = "abaqus"
    job_name: Optional[str] = None
    cpus: Optional[int] = None
    interactive: bool = True
    run_solver: bool = True
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)


class NastranSol103RunRequest(BaseModel):
    input_bdf: str
    output_bdf: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    nastran: str = "nastran"
    run_solver: bool = True
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)
