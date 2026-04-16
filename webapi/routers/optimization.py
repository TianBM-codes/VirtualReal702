from fastapi import APIRouter, Request

from services.model_update.analysis.bayesian_service import (
    run_bayesian_update_from_text,
    run_bayesian_update_workflow,
)
from services.model_update.analysis.inp_service import create_optimization_parameter
from src.l3.core.errors import AppError, ValidationError

from ..common import error_response, server_error, success_response
from ..models import BayesianModelUpdateRequest, BayesianTextCheckRequest, CreateOptimizationParameterRequest
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


def _compact_bayesian_run_response(payload: dict) -> dict:
    iteration_results = list(payload.get("iteration_results") or [])
    iteration_dirs = [
        item.get("saved_artifacts", {}).get("iteration_dir")
        for item in iteration_results
        if item.get("saved_artifacts", {}).get("iteration_dir")
    ]
    return {
        "project_id": payload.get("project_id"),
        "input_inp": payload.get("input_inp"),
        "output_dir": payload.get("output_dir"),
        "iterations": payload.get("iterations"),
        "final_updated_inp": payload.get("final_updated_inp"),
        # Detailed matrices, mappings, and per-iteration summaries stay on disk
        # under output_dir. The API only returns the root paths needed to find them.
        "iteration_dirs": iteration_dirs,
    }


@router.post("/optimization/parameter/create")
async def create_optimization_parameter_api(request: Request, body: CreateOptimizationParameterRequest):
    # Create a persistent optimization-parameter record by binding one candidate
    # type to one resolved INP set entry in the imported catalog.
    await log_request(request, model_to_dict(body))
    try:
        if not body.candidate_code or not body.set_name:
            raise ValidationError("project_id, candidate_code and set_name are required")

        result = create_optimization_parameter(
            project_id=body.project_id,
            candidate_code=body.candidate_code,
            set_name=body.set_name,
            parameter_name=body.parameter_name,
            scatter=body.scatter,
            description=body.description,
            set_type=body.set_type,
            set_scope=body.set_scope,
            instance_name=body.instance_name,
            part_name=body.part_name,
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
        data = run_bayesian_update_workflow(
            project_id=body.project_id,
            input_inp=body.input_inp,
            target_responses=body.target_responses,
            parameter_scatter=body.parameter_scatter,
            response_scatter=body.response_scatter,
            output_dir=body.output_dir,
            odb_id=body.odb_id,
            base_url=body.base_url,
            workspace=body.workspace,
            odb_path=body.odb_path,
            step=body.step,
            instances=body.instances,
            field_prefix=body.field_prefix,
            response_component=body.response_component,
            position=body.position,
            aggregation=body.aggregation,
            frame=body.frame,
            iterations=body.iterations,
            damping=body.damping,
            step_scale=body.step_scale,
            lower_bound=body.lower_bound,
            upper_bound=body.upper_bound,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
            timeout=body.timeout,
            job_name=body.job_name,
            cpus=body.cpus,
            interactive=body.interactive,
            run_solver=body.run_solver,
            timeout_sec=body.timeout_sec,
            extra_args=body.extra_args,
        )
        return success_response(_compact_bayesian_run_response(data), "Bayesian模型修正执行成功")
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
