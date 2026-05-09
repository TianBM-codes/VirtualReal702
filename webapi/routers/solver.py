from fastapi import APIRouter, Request

from services.model_update.analysis.solver_service import (
    generate_nastran_sol103_job,
    preview_nastran_sol103_job,
    run_abaqus_adjoint_job,
    run_abaqus_sensitivity_job,
    run_nastran_sol103_job,
)
from src.l3.core.errors import AppError

from ..background_jobs import get_background_task, submit_background_task
from ..common import error_response, server_error, success_response
from ..models import (
    AbaqusAdjointRunRequest,
    AbaqusSensitivityRunRequest,
    NastranSol103GenerateRequest,
    NastranSol103PreviewRequest,
    NastranSol103RunRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["solver"])


def _sol103_run_kwargs(body: NastranSol103RunRequest) -> dict:
    return {
        "input_bdf": body.input_bdf,
        "output_bdf": body.output_bdf,
        "settings": body.settings,
        "nastran": body.nastran,
        "run_solver": body.run_solver,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
    }


@router.post("/solver/abaqus/sensitivity")
async def run_abaqus_sensitivity_api(request: Request, body: AbaqusSensitivityRunRequest):
    # Generate the Abaqus sensitivity deck and optionally launch the solver in
    # one request so callers can use the same endpoint for prep-only or full run.
    await log_request(request, model_to_dict(body))
    try:
        data = run_abaqus_sensitivity_job(
            input_inp=body.input_inp,
            output_dir=body.output_dir,
            response_elset=body.response_elset,
            response_nset=body.response_nset,
            response_frequency=body.response_frequency,
            node_vars=body.node_vars,
            element_vars=body.element_vars,
            abaqus=body.abaqus,
            job_name=body.job_name,
            cpus=body.cpus,
            interactive=body.interactive,
            run_solver=body.run_solver,
            timeout_sec=body.timeout_sec,
            extra_args=body.extra_args,
        )
        return success_response(data, "Abaqus 灵敏度流程执行成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/abaqus/adjoint")
async def run_abaqus_adjoint_api(request: Request, body: AbaqusAdjointRunRequest):
    # Thin API wrapper around the local adjoint workflow implemented in the
    # service layer; the router only logs, validates, and normalizes responses.
    await log_request(request, model_to_dict(body))
    try:
        data = run_abaqus_adjoint_job(
            input_inp=body.input_inp,
            output_inp=body.output_inp,
            response_nset=body.response_nset,
            abaqus=body.abaqus,
            job_name=body.job_name,
            cpus=body.cpus,
            interactive=body.interactive,
            run_solver=body.run_solver,
            timeout_sec=body.timeout_sec,
            extra_args=body.extra_args,
        )
        return success_response(data, "Abaqus 伴随流程执行成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/nastran/sol103")
async def run_nastran_sol103_api(request: Request, body: NastranSol103RunRequest):
    # Nastran preprocessing and solve are exposed together so downstream code
    # does not need to know where the converted SOL103 deck is written.
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _sol103_run_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.nastran.sol103.run",
                fn=run_nastran_sol103_job,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "Nastran SOL103 任务已提交")
        data = run_nastran_sol103_job(**kwargs)
        return success_response(data, "Nastran SOL103 流程执行成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/nastran/sol103/preview")
async def preview_nastran_sol103_api(request: Request, body: NastranSol103PreviewRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = preview_nastran_sol103_job(
            input_bdf=body.input_bdf,
            settings=body.settings,
        )
        return success_response(data, "Nastran SOL103 预览成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/nastran/sol103/generate")
async def generate_nastran_sol103_api(request: Request, body: NastranSol103GenerateRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = generate_nastran_sol103_job(
            input_bdf=body.input_bdf,
            output_bdf=body.output_bdf,
            settings=body.settings,
        )
        return success_response(data, "Nastran SOL103 生成成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/nastran/sol103/run")
async def run_nastran_sol103_run_api(request: Request, body: NastranSol103RunRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _sol103_run_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.nastran.sol103.run",
                fn=run_nastran_sol103_job,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "Nastran SOL103 求解任务已提交")
        data = run_nastran_sol103_job(**kwargs)
        return success_response(data, "Nastran SOL103 求解成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.get("/solver/nastran/tasks")
async def nastran_task_status(task_id: str):
    try:
        data = get_background_task(task_id)
        if data is None:
            return error_response(
                404,
                f"nastran task '{task_id}' not found",
                error_code="NOT_FOUND",
                details={"task_id": str(task_id)},
            )
        return success_response(data, "Nastran 任务状态获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
