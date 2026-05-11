from fastapi import APIRouter, Request

from services.model_update.analysis.bayesian_service import (
    run_bayesian_update_from_text,
    run_bayesian_update_workflow,
)
from services.model_update.analysis.model_update_meta_service import (
    add_manual_response,
    resolve_abaqus_command,
    resolve_bayesian_output_dir,
    resolve_python3_command,
)
from services.model_update.analysis import sensitivity_service as _sens
from services.model_update.analysis.inp_service import create_optimization_parameter
from src.l3.core.errors import AppError, ValidationError

from ..background_jobs import get_background_task, submit_background_task, update_background_task
from ..common import error_response, server_error, success_response
from ..models import (
    AddResponseRequest,
    BayesianModelUpdateRequest,
    BayesianTextCheckRequest,
    CreateOptimizationParameterRequest,
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


def _bayesian_run_kwargs(body: BayesianModelUpdateRequest) -> dict:
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "input_inp": body.input_inp,
        "target_responses": body.target_responses,
        "parameter_scatter": body.parameter_scatter,
        "response_scatter": body.response_scatter,
        "output_dir": resolve_bayesian_output_dir(None),
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
        "python3": resolve_python3_command(None),
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
        if (not body.candidate_code and not body.quantity_code) or not set_names:
            raise ValidationError(
                "project_id、set_name 和优化参数类型不能为空；"
                "请优先使用 quantity_code，candidate_code 仅作为兼容别名"
            )

        results = []
        multi_set = len(set_names) > 1
        for set_name in set_names:
            resolved_parameter_name = body.parameter_name
            if multi_set and resolved_parameter_name:
                resolved_parameter_name = f"{resolved_parameter_name}@{set_name}"
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
            )
            return success_response(data, "Bayesian model update task submitted")
        data = _run_bayesian_update_workflow_compact(**kwargs)
        return success_response(data, "Bayesian模型修正执行成功")
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
        return success_response(data, "Bayesian task status loaded")
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
