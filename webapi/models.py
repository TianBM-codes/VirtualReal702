from typing import Any, Dict, List, Optional, Union

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


class SensorPositionRequest(BaseModel):
    project_id: int


class DeformSensorPositionRequest(BaseModel):
    project_id: int
    scale: float = 1.0
    static_result_id: Optional[int] = None
    load_case_no: Optional[int] = None
    result_no: Optional[int] = None


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


class ImportProjectStaticResultRequest(BaseModel):
    project_id: int
    result_group: str
    load_case_no: int = Field(default=1, ge=1)
    step: Optional[str] = None
    frame: Optional[int] = None
    instances: Optional[List[str]] = None
    overwrite: bool = True


class CreateOptimizationParameterRequest(BaseModel):
    project_id: int
    set_name: Union[str, List[str]]
    quantity_code: Optional[str] = None
    candidate_code: Optional[str] = None
    lower: float
    upper: float
    prob_id: int = 0
    selection_mode: Optional[str] = None
    parameter_name: Optional[str] = None
    scatter: Optional[float] = None
    description: str = ""
    set_type: Optional[str] = None
    set_scope: Optional[str] = None
    instance_name: Optional[str] = None
    part_name: Optional[str] = None


class AddResponseRequest(BaseModel):
    project_id: int
    type: str
    scatter: float
    dof: str
    step: Optional[str] = None


class BayesianModelUpdateRequest(BaseModel):
    project_id: int
    batch_no: int = Field(default=1, ge=1)
    input_inp: str
    target_responses: Any
    parameter_scatter: Any = None
    response_scatter: Any = None
    output_dir: Optional[str] = None
    save_results: bool = True
    odb_id: Optional[str] = None
    base_url: Optional[str] = None
    workspace: Optional[str] = None
    odb_path: Optional[str] = None
    step: Optional[str] = None
    instances: Optional[List[str]] = None
    field_prefix: str = "d_U_"
    response_component: Optional[str] = None
    position: Optional[str] = None
    aggregation: str = "max_abs"
    frame: int = 0
    iterations: int = Field(default=1, ge=1)
    exit_diff_percent: Optional[float] = Field(default=None, ge=0)
    damping: float = 1e-8
    step_scale: float = 1.0
    lower_bound: Optional[Any] = None
    upper_bound: Optional[Any] = None
    abaqus: Optional[str] = None
    python3: Optional[str] = None
    keep_raw: bool = False
    timeout: int = 60
    job_name: Optional[str] = None
    cpus: Optional[int] = None
    interactive: bool = True
    run_solver: bool = False
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)
    write_cloud_result: bool = False
    cloud_result_group: Optional[str] = None
    cloud_step_name: str = "BayesianUpdate"
    cloud_field_name: str = "PARAMETER_CLOUD"
    cloud_value_mode: str = "updated_value"
    async_submit: bool = False


class TextRowReadRequest(BaseModel):
    file_path: str
    row: int = Field(ge=1)
    col_start: int = Field(default=1, ge=1)


class TextMatrixReadRequest(BaseModel):
    file_path: str
    row_start: int = Field(ge=1)
    row_count: int = Field(ge=1)
    col_start: int = Field(default=1, ge=1)


class BayesianTextCheckRequest(BaseModel):
    sensitivity_matrix: TextMatrixReadRequest
    model_response: TextRowReadRequest
    target_response: TextRowReadRequest
    parameter_names: List[str]
    parameter_scatter: Any = None
    response_scatter: Any = None
    input_inp: Optional[str] = None
    parameter_values: Optional[Any] = None
    damping: float = 1e-8
    step_scale: float = 1.0
    lower_bound: Optional[Any] = None
    upper_bound: Optional[Any] = None
    output_dir: Optional[str] = None
    case_name: str = "bayesian_text_check"


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


class PairNodePointResultRequest(BaseModel):
    project_id: int


class CorrelationEvaluateRequest(BaseModel):
    project_id: int
    load_case_no: Optional[int] = None
    result_no: Optional[int] = None
    components: Optional[List[str]] = None
    include_rotations: bool = False


class TransformOperationRequest(BaseModel):
    project_id: int
    type: str
    matrix4: List[List[float]]


class TransformAutoInfoRequest(BaseModel):
    project_id: int
    type: Optional[str] = None


class SensitivityBuildWorkspaceRequest(BaseModel):
    odb_path: str
    workspace: str
    abaqus: Optional[str] = None
    python3: Optional[str] = None
    keep_raw: bool = False


class SensitivityOverviewRequest(BaseModel):
    workspace: str


class SensitivityDsaConfigPreviewRequest(BaseModel):
    project_id: int
    value_mode: str = "inherit"


class SensitivityDsaInpGenerateRequest(BaseModel):
    project_id: int
    input_inp: str
    output_dir: Optional[str] = None
    value_mode: str = "inherit"
    output_inp: Optional[str] = None
    include_file: Optional[str] = None
    config_file: Optional[str] = None


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


class SensitivityExportVtuBaseRequest(BaseModel):
    project_id: int
    odb_id: Optional[str] = None
    output_vtu: str
    base_url: Optional[str] = None
    inp_path: Optional[str] = None
    workspace: Optional[str] = None
    odb_path: Optional[str] = None
    step: Optional[str] = None
    instances: Optional[List[str]] = None
    field_prefix: str = "d_U_"
    response_component: Optional[str] = None
    position: Optional[str] = None
    aggregation: str = "max_abs"
    frame: int = 0
    abaqus: Optional[str] = None
    python3: Optional[str] = None
    keep_raw: bool = False
    timeout: int = 60


class SensitivityExportDsaVtuRequest(SensitivityExportVtuBaseRequest):
    field_prefix: str = "d_U_"


class SensitivityExportAdjointVtuRequest(SensitivityExportVtuBaseRequest):
    field_name: str


class SensitivityExportVtuRequest(SensitivityExportDsaVtuRequest):
    pass


class SensitivityStoreDsaRequest(BaseModel):
    project_id: int
    batch_no: Optional[str] = "1"
    input_inp: str
    output_dir: Optional[str] = None
    odb_id: Optional[str] = None
    base_url: Optional[str] = None
    workspace: Optional[str] = None
    odb_path: Optional[str] = None
    step: Optional[str] = None
    instances: Optional[List[str]] = None
    field_prefix: str = "d_U_"
    response_component: Optional[str] = None
    position: Optional[str] = None
    aggregation: str = "max_abs"
    frame: int = 0
    response_elset: Optional[str] = None
    response_nset: Optional[str] = None
    response_frequency: int = 1
    node_vars: Optional[List[str]] = None
    element_vars: Optional[List[str]] = None
    abaqus: Optional[str] = None
    python3: Optional[str] = None
    keep_raw: bool = False
    timeout: int = 60
    job_name: Optional[str] = None
    cpus: Optional[int] = None
    interactive: bool = True
    run_solver: bool = True
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)
    write_cloud_result: bool = False
    cloud_result_group: Optional[str] = None
    cloud_step_name: str = "Sensitivity"
    cloud_field_name: str = "SENSITIVITY_CLOUD"
    async_submit: bool = False


class SensitivityRunAndStoreRequest(BaseModel):
    project_id: int
    batch_no: Optional[str] = "1"
    input_inp: str
    output_dir: str
    step: str
    instances: List[str]
    field_prefix: str
    response_component: str
    position: str
    aggregation: str = "max_abs"
    frame: int = 0
    abaqus: Optional[str] = None
    python3: Optional[str] = None
    base_url: Optional[str] = None
    keep_raw: bool = False
    timeout: int = 60
    job_name: Optional[str] = None
    cpus: Optional[int] = None
    interactive: bool = True
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)
    cleanup_process_files: bool = True
    parse_via_project_results: bool = True
    project_result_group: Optional[str] = None
    project_result_display_name: Optional[str] = None
    project_result_wait_timeout_sec: int = 3600
    project_result_poll_interval_sec: float = 2.0
    write_cloud_result: bool = False
    cloud_result_group: Optional[str] = None
    cloud_step_name: str = "Sensitivity"
    cloud_field_name: str = "SENSITIVITY_CLOUD"
    merge_fields: bool = True
    merge_result_group: Optional[str] = None
    async_submit: bool = False


class SensitivityGenerateRunAndStoreRequest(BaseModel):
    project_id: int
    batch_no: Optional[str] = "1"
    input_inp: str
    output_dir: str
    step: str
    instances: List[str]
    field_prefix: str
    response_component: str
    position: str
    aggregation: str = "max_abs"
    frame: int = 0
    abaqus: Optional[str] = None
    python3: Optional[str] = None
    base_url: Optional[str] = None
    keep_raw: bool = False
    timeout: int = 60
    job_name: Optional[str] = None
    cpus: Optional[int] = None
    interactive: bool = True
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)
    cleanup_process_files: bool = True
    parse_via_project_results: bool = True
    project_result_group: Optional[str] = None
    project_result_display_name: Optional[str] = None
    project_result_wait_timeout_sec: int = 3600
    project_result_poll_interval_sec: float = 2.0
    async_submit: bool = False


class SensitivityStoredQueryRequest(BaseModel):
    project_id: int
    batch_no: Optional[str] = "1"


class SensitivityMergeFieldsRequest(BaseModel):
    project_id: int
    workspace: str
    step: str
    frame: int = 0
    field_prefix: str
    instances: List[str] = Field(default_factory=list)
    result_group: str = "merged_dsa"
    source_result_group: Optional[str] = None
    async_submit: bool = False


class AbaqusSensitivityRunRequest(BaseModel):
    input_inp: str
    output_dir: Optional[str] = None
    response_elset: Optional[str] = None
    response_nset: Optional[str] = None
    response_frequency: int = 1
    node_vars: Optional[List[str]] = None
    element_vars: Optional[List[str]] = None
    abaqus: Optional[str] = None
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
    abaqus: Optional[str] = None
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
