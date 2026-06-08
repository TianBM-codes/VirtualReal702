from fastapi import APIRouter, Request

from services.model_update.analysis.bayesian_service import (
    run_modal_frequency_bayesian_update_workflow,
    run_sol200_modal_frequency_bayesian_update_workflow,
    run_bayesian_update_from_text,
    run_bayesian_update_workflow,
)
from services.model_update.analysis.model_update_meta_service import (
    add_manual_response,
    resolve_abaqus_command,
)
from services.model_update.analysis import sensitivity_service as _sens
from services.model_update.analysis.project_file_service import resolve_project_input_file
from services.model_update.analysis.project_path_service import resolve_project_cal_subdir
from services.model_update.analysis.inp_service import (
    clear_design_response_catalog_entries,
    create_modal_frequency_response_catalog_from_fem,
    create_modal_match_response_catalog_entries,
    create_modal_frequency_response_catalog_from_match,
    create_design_response_catalog_entry,
    create_optimization_parameter,
    get_fe_response_catalog,
    get_modal_frequency_response_options,
    list_optimization_parameters,
    list_design_response_catalog_entries,
    remove_optimization_parameters_from_update,
    select_optimization_parameters_for_update,
    update_optimization_parameter_usage,
)
from services.model_update.analysis.nastran_sol200_service import (
    clear_sol200_parameter_config_entries,
    clear_sol200_response_config_entries,
    create_sol200_parameter_config_entry,
    create_sol200_response_config_entry,
    list_sol200_parameter_config_entries,
    list_sol200_response_config_entries,
    sync_sol200_config_from_catalog,
)
from src.l3.core.errors import AppError, ValidationError

from ..background_jobs import get_background_task, submit_background_task, update_background_task
from ..common import error_response, server_error, success_response
from ..models import (
    AddResponseRequest,
    AbaqusStaticResponseCatalogRequest,
    BayesianModelUpdateRequest,
    BayesianTextCheckRequest,
    CreateAbaqusStaticResponseRequest,
    CreateDesignResponseRequest,
    CreateModalFrequencyResponseCatalogRequest,
    CreateOptimizationParameterRequest,
    CreateSol200ParameterConfigRequest,
    CreateSol200ResponseConfigRequest,
    DesignResponseCatalogRequest,
    FeResponseCatalogRequest,
    ModalFrequencyResponseOptionsRequest,
    ModalFrequencyResponseFromMatchRequest,
    ModalMatchResponseSelectRequest,
    ModalFrequencyBayesianModelUpdateRequest,
    Sol200ModalFrequencyBayesianModelUpdateRequest,
    OptimizationParameterCatalogRequest,
    OptimizationParameterRemoveFromUpdateRequest,
    OptimizationParameterSelectForUpdateRequest,
    OptimizationParameterUsageUpdateRequest,
    Sol200SyncFromCatalogRequest,
    Sol200ConfigCatalogRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


def _normalize_set_names(raw_value) -> list[str]:
    if isinstance(raw_value, str):
        values = [raw_value]
    else:
        values = list(raw_value or [])
    result = []
    seen = set()
    for item in values:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _normalize_element_labels(raw_value) -> list[int]:
    values = list(raw_value or [])
    result = []
    seen = set()
    for item in values:
        label = int(item)
        if label in seen:
            continue
        seen.add(label)
        result.append(label)
    return result


def _normalize_node_labels(raw_value) -> list[int]:
    values = list(raw_value or [])
    result = []
    seen = set()
    for item in values:
        label = int(item)
        if label in seen:
            continue
        seen.add(label)
        result.append(label)
    return result


def _create_abaqus_static_response_catalog(body: CreateAbaqusStaticResponseRequest):
    node_labels = _normalize_node_labels(body.node_labels)
    element_labels = _normalize_element_labels(body.element_labels)
    return create_design_response_catalog_entry(
        project_id=body.project_id,
        region_type=body.region_type,
        variables=body.variables,
        set_name=body.set_name,
        set_scope=body.set_scope,
        instance_name=body.instance_name,
        part_name=body.part_name,
        node_labels=node_labels or None,
        element_labels=element_labels or None,
        step_name=body.step_name,
        frequency=body.frequency,
        response_name=body.response_name,
    )


def _create_modal_frequency_response_catalog(body: CreateModalFrequencyResponseCatalogRequest):
    return create_modal_frequency_response_catalog_from_fem(
        project_id=body.project_id,
        mode_numbers=body.mode_numbers,
        overwrite=body.overwrite,
        solver_scope=body.solver_scope,
        scatter=body.scatter,
        response_name_prefix=body.response_name_prefix,
    )


def _compact_bayesian_run_response(payload: dict) -> dict:
    iteration_results = list(payload.get("iteration_results") or [])
    iteration_dirs = [
        item.get("saved_artifacts", {}).get("iteration_dir")
        for item in iteration_results
        if item.get("saved_artifacts", {}).get("iteration_dir")
    ]
    return {
        "project_id": payload.get("project_id"),
        "batch_no": payload.get("batch_no"),
        "input_inp": payload.get("input_inp"),
        "output_dir": payload.get("output_dir"),
        "save_results": payload.get("save_results"),
        "iterations": payload.get("iterations"),
        "requested_iterations": payload.get("requested_iterations"),
        "stopped_early": payload.get("stopped_early"),
        "exit_diff_percent": payload.get("exit_diff_percent"),
        "final_updated_inp": payload.get("final_updated_inp"),
        "history_dir": payload.get("saved_artifacts", {}).get("history_dir"),
        "history_html": payload.get("saved_artifacts", {}).get("files", {}).get("overview_html"),
        "cloud_result": payload.get("cloud_result"),
        "final_static_output": payload.get("final_static_output"),
        # Detailed matrices, mappings, and per-iteration summaries stay on disk
        # under output_dir. The API only returns the root paths needed to find them.
        "iteration_dirs": iteration_dirs,
    }


def _compact_modal_bayesian_run_response(payload: dict) -> dict:
    iteration_results = list(payload.get("iteration_results") or [])
    iteration_dirs = [
        item.get("saved_artifacts", {}).get("iteration_dir")
        for item in iteration_results
        if item.get("saved_artifacts", {}).get("iteration_dir")
    ]
    return {
        "project_id": payload.get("project_id"),
        "batch_no": payload.get("batch_no"),
        "sensitivity_batch_no": payload.get("sensitivity_batch_no"),
        "input_bdf": payload.get("input_bdf"),
        "output_dir": payload.get("output_dir"),
        "save_results": payload.get("save_results"),
        "iterations": payload.get("iterations"),
        "requested_iterations": payload.get("requested_iterations"),
        "stopped_early": payload.get("stopped_early"),
        "exit_diff_percent": payload.get("exit_diff_percent"),
        "matched_pair_count": payload.get("matched_pair_count"),
        "final_updated_bdf": payload.get("final_updated_bdf"),
        "history_dir": payload.get("saved_artifacts", {}).get("history_dir"),
        "history_html": payload.get("saved_artifacts", {}).get("files", {}).get("overview_html"),
        "final_modal_output": payload.get("final_modal_output"),
        "cloud_result": payload.get("cloud_result"),
        "iteration_dirs": iteration_dirs,
    }


def _bayesian_run_kwargs(body: BayesianModelUpdateRequest) -> dict:
    input_inp = str(
        resolve_project_input_file(
            int(body.project_id),
            explicit_path=body.input_inp,
            file_name=body.input_inp_name,
            field_name="input_inp",
        )
    )
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "input_inp": input_inp,
        "target_responses": body.target_responses,
        "parameter_scatter": body.parameter_scatter,
        "response_scatter": body.response_scatter,
        "output_dir": resolve_project_cal_subdir(int(body.project_id), "bayesian"),
        "save_results": body.save_results,
        "odb_id": body.odb_id,
        "base_url": body.base_url,
        "workspace": body.workspace,
        "odb_path": body.odb_path,
        "step": body.step,
        "instances": body.instances,
        "field_prefix": body.field_prefix,
        "response_component": body.response_component,
        "position": body.position,
        "aggregation": body.aggregation,
        "frame": body.frame,
        "iterations": body.iterations,
        "exit_diff_percent": body.exit_diff_percent,
        "damping": body.damping,
        "step_scale": body.step_scale,
        "lower_bound": body.lower_bound,
        "upper_bound": body.upper_bound,
        "abaqus": resolve_abaqus_command(None),
        "keep_raw": body.keep_raw,
        "timeout": body.timeout,
        "job_name": body.job_name,
        "cpus": body.cpus,
        "interactive": body.interactive,
        "run_solver": body.run_solver,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
        "write_cloud_result": body.write_cloud_result,
        "cloud_result_group": body.cloud_result_group,
        "cloud_step_name": body.cloud_step_name,
        "cloud_field_name": body.cloud_field_name,
        "cloud_value_mode": body.cloud_value_mode,
    }


def _run_bayesian_update_workflow_compact(**kwargs) -> dict:
    project_id = int(kwargs["project_id"])
    return _sens._run_with_project_sensitivity_status(
        project_id,
        lambda: _compact_bayesian_run_response(run_bayesian_update_workflow(**kwargs)),
    )


def _run_bayesian_update_task(task_id: str, **kwargs) -> dict:
    def _progress_callback(progress: dict) -> None:
        update_background_task(task_id, progress=progress)

    project_id = int(kwargs["project_id"])
    return _sens._run_with_project_sensitivity_status(
        project_id,
        lambda: _compact_bayesian_run_response(
            run_bayesian_update_workflow(progress_callback=_progress_callback, **kwargs)
        ),
    )


def _modal_bayesian_run_kwargs(body: ModalFrequencyBayesianModelUpdateRequest) -> dict:
    input_bdf = None
    if str(body.input_bdf or "").strip() or str(body.input_bdf_name or "").strip():
        input_bdf = str(
            resolve_project_input_file(
                int(body.project_id),
                explicit_path=body.input_bdf,
                file_name=body.input_bdf_name,
                field_name="input_bdf",
            )
        )
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "sensitivity_batch_no": body.sensitivity_batch_no,
        "input_bdf": input_bdf,
        "parameter_scatter": body.parameter_scatter,
        "response_scatter": body.response_scatter,
        "output_dir": resolve_project_cal_subdir(int(body.project_id), "bayesian", "modal_frequency"),
        "save_results": body.save_results,
        "iterations": body.iterations,
        "exit_diff_percent": body.exit_diff_percent,
        "damping": body.damping,
        "step_scale": body.step_scale,
        "lower_bound": body.lower_bound,
        "upper_bound": body.upper_bound,
        "mac_threshold": body.mac_threshold,
        "max_freq_error_ratio": body.max_freq_error_ratio,
        "matching_method": body.matching_method,
    }


def _run_modal_bayesian_update_workflow_compact(**kwargs) -> dict:
    project_id = int(kwargs["project_id"])
    return _sens._run_with_project_sensitivity_status(
        project_id,
        lambda: _compact_modal_bayesian_run_response(run_modal_frequency_bayesian_update_workflow(**kwargs)),
    )


def _run_modal_bayesian_update_task(task_id: str, **kwargs) -> dict:
    def _progress_callback(progress: dict) -> None:
        update_background_task(task_id, progress=progress)

    project_id = int(kwargs["project_id"])
    return _sens._run_with_project_sensitivity_status(
        project_id,
        lambda: _compact_modal_bayesian_run_response(
            run_modal_frequency_bayesian_update_workflow(progress_callback=_progress_callback, **kwargs)
        ),
    )


def _sol200_modal_bayesian_run_kwargs(body: Sol200ModalFrequencyBayesianModelUpdateRequest) -> dict:
    input_bdf = None
    if str(body.input_bdf or "").strip() or str(body.input_bdf_name or "").strip():
        input_bdf = str(
            resolve_project_input_file(
                int(body.project_id),
                explicit_path=body.input_bdf,
                file_name=body.input_bdf_name,
                field_name="input_bdf",
            )
        )
    settings = dict(body.settings or {})
    settings.setdefault("sol200.deck_mode", "include")
    settings.setdefault("sol200.sensitivity_csv", True)
    settings.setdefault("result.target", "F06")
    settings.setdefault("post", -1)
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "sensitivity_batch_no": body.sensitivity_batch_no,
        "input_bdf": input_bdf,
        "parameter_scatter": body.parameter_scatter,
        "response_scatter": body.response_scatter,
        "output_dir": resolve_project_cal_subdir(int(body.project_id), "bayesian", "sol200_modal_frequency"),
        "save_results": body.save_results,
        "iterations": body.iterations,
        "exit_diff_percent": body.exit_diff_percent,
        "damping": body.damping,
        "step_scale": body.step_scale,
        "lower_bound": body.lower_bound,
        "upper_bound": body.upper_bound,
        "mac_threshold": body.mac_threshold,
        "max_freq_error_ratio": body.max_freq_error_ratio,
        "matching_method": body.matching_method,
        "settings": settings,
        "nastran": body.nastran,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
        "write_cloud_result": body.write_cloud_result,
        "cloud_result_group": body.cloud_result_group,
        "cloud_step_name": body.cloud_step_name,
        "cloud_field_name": body.cloud_field_name,
    }


def _run_sol200_modal_bayesian_update_workflow_compact(**kwargs) -> dict:
    project_id = int(kwargs["project_id"])
    return _sens._run_with_project_sensitivity_status(
        project_id,
        lambda: _compact_modal_bayesian_run_response(run_sol200_modal_frequency_bayesian_update_workflow(**kwargs)),
    )


def _run_sol200_modal_bayesian_update_task(task_id: str, **kwargs) -> dict:
    def _progress_callback(progress: dict) -> None:
        update_background_task(task_id, progress=progress)

    project_id = int(kwargs["project_id"])
    return _sens._run_with_project_sensitivity_status(
        project_id,
        lambda: _compact_modal_bayesian_run_response(
            run_sol200_modal_frequency_bayesian_update_workflow(progress_callback=_progress_callback, **kwargs)
        ),
    )


@router.post("/add/response")
async def add_response_api(request: Request, body: AddResponseRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = add_manual_response(
            project_id=body.project_id,
            response_type=body.type,
            scatter=body.scatter,
            dof=body.dof,
            step=body.step,
        )
        return success_response(data, "响应添加成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/parameter/create")
async def create_optimization_parameter_api(request: Request, body: CreateOptimizationParameterRequest):
    # Create a persistent optimization-parameter record by binding one
    # optimization quantity type to one resolved INP set entry.
    await log_request(request, model_to_dict(body))
    try:
        set_names = _normalize_set_names(body.set_name)
        element_labels = _normalize_element_labels(body.element_labels)
        all_elements_e = bool(body.all_elements_e)
        if not body.candidate_code and not body.quantity_code:
            raise ValidationError(
                "project_id、优化参数类型不能为空；"
                "请优先使用 quantity_code，candidate_code 仅作为兼容别名"
            )
        if all_elements_e and (set_names or element_labels):
            raise ValidationError(
                "all_elements_e 模式下不能同时传 set_name 或 element_labels",
                {
                    "all_elements_e": True,
                    "set_name_count": len(set_names),
                    "element_label_count": len(element_labels),
                },
            )
        if not all_elements_e and bool(set_names) == bool(element_labels):
            raise ValidationError(
                "set_name 和 element_labels 必须二选一，且不能同时传入",
                {
                    "set_name_count": len(set_names),
                    "element_label_count": len(element_labels),
                },
            )

        if all_elements_e:
            result = create_optimization_parameter(
                project_id=body.project_id,
                candidate_code=body.candidate_code,
                quantity_code=body.quantity_code,
                lower=body.lower,
                upper=body.upper,
                prob_id=body.prob_id,
                selection_mode=body.selection_mode,
                parameter_name=body.parameter_name,
                scatter=body.scatter,
                description=body.description,
                set_type=body.set_type,
                set_scope=body.set_scope,
                instance_name=body.instance_name,
                part_name=body.part_name,
                current_value=body.current_value,
                usage_scope=body.usage_scope,
                all_elements_e=True,
            )
            return success_response(result, "优化参数创建成功")

        if element_labels:
            result = create_optimization_parameter(
                project_id=body.project_id,
                candidate_code=body.candidate_code,
                quantity_code=body.quantity_code,
                lower=body.lower,
                upper=body.upper,
                prob_id=body.prob_id,
                selection_mode=body.selection_mode,
                parameter_name=body.parameter_name,
                scatter=body.scatter,
                description=body.description,
                set_type=body.set_type,
                set_scope=body.set_scope,
                instance_name=body.instance_name,
                part_name=body.part_name,
                element_labels=element_labels,
                current_value=body.current_value,
                usage_scope=body.usage_scope,
            )
            return success_response(result, "优化参数创建成功")

        results = []
        multi_set = len(set_names) > 1
        for set_name in set_names:
            resolved_parameter_name = body.parameter_name
            if multi_set and resolved_parameter_name:
                resolved_parameter_name = f"{resolved_parameter_name}@{set_name}"
            else:
                resolved_parameter_name = f"{body.quantity_code}@{set_name}"
            results.append(
                create_optimization_parameter(
                    project_id=body.project_id,
                    candidate_code=body.candidate_code,
                    quantity_code=body.quantity_code,
                    lower=body.lower,
                    upper=body.upper,
                    prob_id=body.prob_id,
                    selection_mode=body.selection_mode,
                    set_name=set_name,
                    parameter_name=resolved_parameter_name,
                    scatter=body.scatter,
                    description=body.description,
                    set_type=body.set_type,
                    set_scope=body.set_scope,
                    instance_name=body.instance_name,
                    part_name=body.part_name,
                    current_value=body.current_value,
                    usage_scope=body.usage_scope,
                )
            )
        result = (
            results[0]
            if len(results) == 1
            else {
                "project_id": body.project_id,
                "set_names": set_names,
                "created_set_count": len(results),
                "results": results,
            }
        )
        return success_response(result, "优化参数创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/abaqus/static_response/create",
    summary="创建 Abaqus 静力响应目录",
)
async def create_abaqus_static_response_api(request: Request, body: CreateAbaqusStaticResponseRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = _create_abaqus_static_response_catalog(body)
        return success_response(data, "Abaqus 静力响应目录创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/response/create",
    deprecated=True,
    summary="创建设计响应目录（旧接口，建议改用 /optimization/abaqus/static_response/create）",
)
async def create_design_response_api(request: Request, body: CreateDesignResponseRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = _create_abaqus_static_response_catalog(body)
        return success_response(data, "设计响应创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/nastran/modal_frequency_response/create",
    summary="创建 Nastran 模态频率响应目录（无需试验匹配）",
)
async def create_modal_frequency_response_api(request: Request, body: CreateModalFrequencyResponseCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = _create_modal_frequency_response_catalog(body)
        return success_response(data, "Nastran 模态频率响应目录创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/response/modal_frequency/create",
    summary="创建模态频率响应目录（无需试验匹配）",
)
async def create_modal_frequency_response_legacy_prefix_api(request: Request, body: CreateModalFrequencyResponseCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = _create_modal_frequency_response_catalog(body)
        return success_response(data, "模态频率响应目录创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/nastran/modal_frequency_response/create_from_match",
    summary="从模态匹配结果创建 Nastran 模态频率响应目录",
)
async def create_nastran_modal_frequency_response_from_match_api(request: Request, body: ModalFrequencyResponseFromMatchRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = create_modal_frequency_response_catalog_from_match(
            project_id=body.project_id,
            overwrite=body.overwrite,
            mac_threshold=body.mac_threshold,
            max_freq_error_ratio=body.max_freq_error_ratio,
            solver_scope=body.solver_scope,
            matching_method=body.matching_method,
            scatter=body.scatter,
        )
        return success_response(data, "Nastran 模态频率响应目录创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/response/modal_frequency/create_from_match",
    summary="从模态匹配结果创建模态频率响应目录",
)
async def create_modal_frequency_response_from_match_api(request: Request, body: ModalFrequencyResponseFromMatchRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = create_modal_frequency_response_catalog_from_match(
            project_id=body.project_id,
            overwrite=body.overwrite,
            mac_threshold=body.mac_threshold,
            max_freq_error_ratio=body.max_freq_error_ratio,
            solver_scope=body.solver_scope,
            matching_method=body.matching_method,
            scatter=body.scatter,
        )
        return success_response(data, "模态频率响应目录创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/parameter")
async def list_optimization_parameter_api(request: Request, body: OptimizationParameterCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = list_optimization_parameters(body.project_id)
        return success_response(data, "优化参数目录加载成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/parameter/usage/update")
async def update_optimization_parameter_usage_api(request: Request, body: OptimizationParameterUsageUpdateRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = update_optimization_parameter_usage(
            project_id=body.project_id,
            parameters=[model_to_dict(item) for item in body.parameters],
        )
        return success_response(data, "优化参数用途已更新")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/parameter/select_for_update")
async def select_optimization_parameter_for_update_api(request: Request, body: OptimizationParameterSelectForUpdateRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = select_optimization_parameters_for_update(
            project_id=body.project_id,
            parameter_names=body.parameter_names,
            replace_update_set=body.replace_update_set,
        )
        return success_response(data, "优化参数已加入模型修正集合")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/parameter/remove_from_update")
async def remove_optimization_parameter_from_update_api(request: Request, body: OptimizationParameterRemoveFromUpdateRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = remove_optimization_parameters_from_update(
            project_id=body.project_id,
            parameter_names=body.parameter_names,
        )
        return success_response(data, "优化参数已从模型修正集合移除")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/abaqus/static_response",
    summary="加载 Abaqus 静力响应目录",
)
async def list_abaqus_static_response_api(request: Request, body: AbaqusStaticResponseCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = list_design_response_catalog_entries(body.project_id)
        return success_response(data, "Abaqus 静力响应目录加载成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/response",
    deprecated=True,
    summary="加载设计响应目录（旧接口，建议改用 /optimization/abaqus/static_response）",
)
async def list_design_response_api(request: Request, body: DesignResponseCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = list_design_response_catalog_entries(body.project_id)
        return success_response(data, "设计响应目录加载成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/response/catalog")
async def list_formal_response_catalog_api(request: Request, body: FeResponseCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = get_fe_response_catalog(body.project_id)
        return success_response(data, "正式响应目录加载成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/response/modal_match/select")
async def select_modal_match_response_api(request: Request, body: ModalMatchResponseSelectRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = create_modal_match_response_catalog_entries(
            project_id=body.project_id,
            overwrite=body.overwrite,
            response_types=body.response_types,
            solver_scope=body.solver_scope,
            scatter=body.scatter,
            selected_pairs=[model_to_dict(item) for item in body.selected_pairs],
        )
        return success_response(data, "模态匹配对正式响应已保存")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/response/modal_frequency/options")
async def modal_frequency_response_options_api(request: Request, body: ModalFrequencyResponseOptionsRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = get_modal_frequency_response_options(
            project_id=body.project_id,
            response_source=body.response_source,
        )
        return success_response(data, "获取频率响应数据成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/abaqus/static_response/clear",
    summary="清空 Abaqus 静力响应目录",
)
async def clear_abaqus_static_response_api(request: Request, body: AbaqusStaticResponseCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = clear_design_response_catalog_entries(body.project_id)
        return success_response(data, "Abaqus 静力响应目录已清空")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post(
    "/optimization/response/clear",
    deprecated=True,
    summary="清空设计响应目录（旧接口，建议改用 /optimization/abaqus/static_response/clear）",
)
async def clear_design_response_api(request: Request, body: DesignResponseCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = clear_design_response_catalog_entries(body.project_id)
        return success_response(data, "设计响应已清空")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/sol200/parameter/create")
async def create_sol200_parameter_api(request: Request, body: CreateSol200ParameterConfigRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = create_sol200_parameter_config_entry(
            project_id=body.project_id,
            parameter_name=body.parameter_name,
            parameter_type=body.parameter_type,
            initial=body.initial,
            lower=body.lower,
            upper=body.upper,
            property_id=body.property_id,
            material_id=body.material_id,
            element_id=body.element_id,
            extra_json=body.extra_json,
        )
        return success_response(data, "SOL200 参数配置创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/sol200/parameter")
async def list_sol200_parameter_api(request: Request, body: Sol200ConfigCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = list_sol200_parameter_config_entries(body.project_id)
        return success_response(data, "SOL200 参数配置加载成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/sol200/parameter/clear")
async def clear_sol200_parameter_api(request: Request, body: Sol200ConfigCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = clear_sol200_parameter_config_entries(body.project_id)
        return success_response(data, "SOL200 参数配置已清空")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/sol200/response/create")
async def create_sol200_response_api(request: Request, body: CreateSol200ResponseConfigRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = create_sol200_response_config_entry(
            project_id=body.project_id,
            response_name=body.response_name,
            response_type=body.response_type,
            mode_number=body.mode_number,
            extra_json=body.extra_json,
        )
        return success_response(data, "SOL200 响应配置创建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/sol200/response")
async def list_sol200_response_api(request: Request, body: Sol200ConfigCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = list_sol200_response_config_entries(body.project_id)
        return success_response(data, "SOL200 响应配置加载成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/sol200/response/clear")
async def clear_sol200_response_api(request: Request, body: Sol200ConfigCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = clear_sol200_response_config_entries(body.project_id)
        return success_response(data, "SOL200 响应配置已清空")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/sol200/config/sync_from_catalog")
async def sync_sol200_config_from_catalog_api(request: Request, body: Sol200SyncFromCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = sync_sol200_config_from_catalog(
            project_id=body.project_id,
            overwrite=body.overwrite,
            parameter_source=body.parameter_source,
            response_source=body.response_source,
            mac_threshold=body.mac_threshold,
            max_freq_error_ratio=body.max_freq_error_ratio,
            matching_method=body.matching_method,
        )
        return success_response(data, "SOL200 配置同步成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/bayesian/run")
async def run_bayesian_update_api(request: Request, body: BayesianModelUpdateRequest):
    # Main Bayesian entrypoint used during debugging: it resolves sensitivities,
    # runs the parameter update loop, and optionally re-solves each iteration.
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _bayesian_run_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="optimization.bayesian.run",
                fn=_run_bayesian_update_task,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                pass_task_id=True,
                task_kind="external_solver" if body.run_solver else "internal",
            )
            return success_response(data, "Bayesian 模型修正任务已提交")
        data = _run_bayesian_update_workflow_compact(**kwargs)
        return success_response(data, "Bayesian模型修正执行成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/bayesian/modal_frequency/run")
async def run_modal_bayesian_update_api(request: Request, body: ModalFrequencyBayesianModelUpdateRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _modal_bayesian_run_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="optimization.bayesian.modal_frequency.run",
                fn=_run_modal_bayesian_update_task,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                pass_task_id=True,
                task_kind="internal",
            )
            return success_response(data, "模态频率 Bayesian 模型修正任务已提交")
        data = _run_modal_bayesian_update_workflow_compact(**kwargs)
        return success_response(data, "模态频率 Bayesian 模型修正执行成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/bayesian/sol200/modal_frequency/run")
async def run_sol200_modal_bayesian_update_api(request: Request, body: Sol200ModalFrequencyBayesianModelUpdateRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _sol200_modal_bayesian_run_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="optimization.bayesian.sol200.modal_frequency.run",
                fn=_run_sol200_modal_bayesian_update_task,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                pass_task_id=True,
                task_kind="external_solver",
            )
            return success_response(data, "SOL200 模态频率 Bayesian 模型修正任务已提交")
        data = _run_sol200_modal_bayesian_update_workflow_compact(**kwargs)
        return success_response(data, "SOL200 模态频率 Bayesian 模型修正执行成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.get("/optimization/bayesian/tasks")
async def bayesian_task_status(task_id: str):
    try:
        data = get_background_task(task_id)
        if data is None:
            return error_response(
                404,
                f"未找到 Bayesian 任务 '{task_id}'",
                error_code="NOT_FOUND",
                details={"task_id": str(task_id)},
            )
        return success_response(data, "Bayesian 任务状态获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/optimization/bayesian/check")
async def run_bayesian_text_check_api(request: Request, body: BayesianTextCheckRequest):
    # Lightweight text-based checker for validating the Bayesian math path
    # without depending on ODB/workspace extraction.
    await log_request(request, model_to_dict(body))
    try:
        data = run_bayesian_update_from_text(
            sensitivity_matrix_file=body.sensitivity_matrix.file_path,
            sensitivity_row_start=body.sensitivity_matrix.row_start,
            sensitivity_row_count=body.sensitivity_matrix.row_count,
            sensitivity_col_start=body.sensitivity_matrix.col_start,
            model_response_file=body.model_response.file_path,
            model_response_row=body.model_response.row,
            model_response_col_start=body.model_response.col_start,
            target_response_file=body.target_response.file_path,
            target_response_row=body.target_response.row,
            target_response_col_start=body.target_response.col_start,
            parameter_names=body.parameter_names,
            parameter_scatter=body.parameter_scatter,
            response_scatter=body.response_scatter,
            input_inp=body.input_inp,
            parameter_values=body.parameter_values,
            damping=body.damping,
            step_scale=body.step_scale,
            lower_bound=body.lower_bound,
            upper_bound=body.upper_bound,
            output_dir=body.output_dir,
            case_name=body.case_name,
        )
        return success_response(data, "Bayesian文本校核执行成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
