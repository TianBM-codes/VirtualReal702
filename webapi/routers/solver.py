import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from services.model_update.analysis.solver_service import (
    generate_nastran_sol103_job,
    preview_nastran_sol103_job,
    run_abaqus_adjoint_job,
    run_abaqus_sensitivity_job,
    run_nastran_sol103_job,
    run_nastran_sol103_and_store_modal_results,
    run_solver_and_parse_project_result,
)
from services.model_update.analysis.pbs_service import (
    get_pbs_job_status,
    run_pbs_solver_job,
)
from services.model_update.analysis.nastran_sol200_service import (
    export_sol200_sensitivity_vtu,
    generate_sol200_workflow,
    preview_sol200_sensitivity,
    preview_sol200_workflow,
    run_sol200_workflow,
    store_sol200_sensitivity_cloud,
    store_sol200_sensitivity,
)
from services.model_update.analysis.project_file_service import (
    resolve_project_cal_root,
    resolve_project_input_file,
    resolve_project_output_dir,
    resolve_project_output_file,
)
from services.model_update.importers.op2_service import (
    build_modal_import_payload,
    export_modal_to_vtu,
    preview_op2_modal,
)
from services.model_update.analysis.inp_service import import_fe_modal_results
from src.l3.core.config import settings
from src.l3.core.errors import AppError, ValidationError

from ..background_jobs import get_background_task, submit_background_task
from ..common import error_response, server_error, success_response
from ..models import (
    AbaqusAdjointRunRequest,
    AbaqusSensitivityRunRequest,
    NastranResponseRequest,
    NastranSol200GenerateRequest,
    NastranSol200PreviewRequest,
    NastranSol200RunRequest,
    NastranSol103GenerateRequest,
    NastranSol103PreviewRequest,
    NastranSol103RunAndStoreModalRequest,
    NastranSol103RunRequest,
    Op2ModalPreviewRequest,
    Op2ModalStoreRequest,
    Op2ModalVtuExportRequest,
    Op2SensitivityPreviewRequest,
    Op2SensitivityStoreCloudRequest,
    Op2SensitivityStoreRequest,
    Op2SensitivityVtuExportRequest,
    PBSJobStatusRequest,
    PBSSolverRunRequest,
    SolverRunAndParseRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["solver"])


def _resolve_project_local_input(
    *,
    project_id: Optional[int],
    explicit_path: Optional[str],
    file_name: Optional[str],
    field_name: str,
) -> str:
    raw = str(explicit_path or "").strip() or str(file_name or "").strip()
    if project_id is not None:
        return str(
            resolve_project_input_file(
                int(project_id),
                explicit_path=explicit_path,
                file_name=file_name,
                field_name=field_name,
            )
        )
    if not raw:
        raise ValidationError(
            f"{field_name} is required",
            {"project_id": project_id, field_name: explicit_path, f"{field_name}_name": file_name},
        )
    resolved = Path(raw).expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise ValidationError(
            f"{field_name} not found",
            {"project_id": project_id, field_name: str(resolved)},
        )
    return str(resolved)


def _resolve_project_local_input_with_fallback(
    *,
    project_id: Optional[int],
    explicit_path: Optional[str],
    file_name: Optional[str],
    field_name: str,
    fallback_category_parts: tuple[str, ...] = (),
) -> str:
    raw = str(explicit_path or "").strip() or str(file_name or "").strip()
    try:
        return _resolve_project_local_input(
            project_id=project_id,
            explicit_path=explicit_path,
            file_name=file_name,
            field_name=field_name,
        )
    except AppError as exc:
        if project_id is None or not raw or Path(raw).expanduser().is_absolute() or not fallback_category_parts:
            raise exc
        fallback_root = resolve_project_cal_root(int(project_id), *fallback_category_parts)
        candidate = (Path(fallback_root) / Path(raw)).expanduser().resolve()
        try:
            common = os.path.commonpath([str(Path(fallback_root).resolve()), str(candidate)])
        except ValueError:
            raise exc
        if common != str(Path(fallback_root).resolve()):
            raise exc
        if candidate.exists() and candidate.is_file():
            return str(candidate)
        raise exc


def _resolve_project_local_output_dir(
    *,
    project_id: Optional[int],
    explicit_dir: Optional[str],
    dir_name: Optional[str],
    category_parts: tuple[str, ...],
) -> Optional[str]:
    raw = str(explicit_dir or "").strip() or str(dir_name or "").strip()
    if project_id is None:
        return raw or explicit_dir
    return str(
        resolve_project_output_dir(
            int(project_id),
            category_parts=category_parts,
            explicit_dir=raw or None,
        )
    )


def _resolve_project_local_output_file(
    *,
    project_id: Optional[int],
    explicit_path: Optional[str],
    file_name: Optional[str],
    category_parts: tuple[str, ...],
    field_name: str,
    default_name: Optional[str] = None,
) -> Optional[str]:
    raw = str(explicit_path or "").strip() or str(file_name or "").strip()
    if project_id is None:
        return raw or explicit_path
    return str(
        resolve_project_output_file(
            int(project_id),
            category_parts=category_parts,
            explicit_path=raw or None,
            default_name=default_name,
            field_name=field_name,
        )
    )


def _resolve_modal_op2_path(op2_path: Optional[str], project_id: Optional[int]) -> str:
    text = str(op2_path or "").strip()
    if text:
        return text
    if project_id is None:
        raise ValidationError("未提供 project_id 时，op2_path 不能为空")
    return os.path.abspath(
        os.path.join(settings.data_root, str(int(project_id)), "default_result_source.op2")
    )


def _resolve_sol200_input_bdf(
    *,
    project_id: Optional[int],
    explicit_path: Optional[str],
    file_name: Optional[str],
) -> str:
    return _resolve_project_local_input_with_fallback(
        project_id=project_id,
        explicit_path=explicit_path,
        file_name=file_name,
        field_name="input_bdf",
        fallback_category_parts=("solver", "nastran_sol103"),
    )

def _sol103_run_kwargs(body: NastranSol103RunRequest) -> dict:
    input_bdf = _resolve_project_local_input(
        project_id=body.project_id,
        explicit_path=body.input_bdf,
        file_name=body.input_bdf_name,
        field_name="input_bdf",
    )
    output_bdf = _resolve_project_local_output_file(
        project_id=body.project_id,
        explicit_path=body.output_bdf,
        file_name=body.output_bdf_name,
        category_parts=("solver", "nastran_sol103"),
        field_name="output_bdf",
        default_name=f"{Path(input_bdf).stem}_sol103.bdf" if body.project_id is not None else None,
    )
    return {
        "input_bdf": input_bdf,
        "output_bdf": output_bdf,
        "settings": body.settings,
        "nastran": body.nastran,
        "run_solver": body.run_solver,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
    }


def _sol103_run_and_store_modal_kwargs(body: NastranSol103RunAndStoreModalRequest) -> dict:
    input_bdf = _resolve_project_local_input(
        project_id=body.project_id,
        explicit_path=body.input_bdf,
        file_name=body.input_bdf_name,
        field_name="input_bdf",
    )
    output_bdf = _resolve_project_local_output_file(
        project_id=body.project_id,
        explicit_path=body.output_bdf,
        file_name=body.output_bdf_name,
        category_parts=("solver", "nastran_sol103"),
        field_name="output_bdf",
        default_name=f"{Path(input_bdf).stem}_sol103.bdf",
    )
    return {
        "project_id": body.project_id,
        "input_bdf": input_bdf,
        "output_bdf": output_bdf,
        "settings": body.settings,
        "nastran": body.nastran,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
        "overwrite": body.overwrite,
        "subcase_id": body.subcase_id,
        "mode_numbers": body.mode_numbers,
        "instance_name": body.instance_name,
        "part_name": body.part_name,
    }

def _solver_run_and_parse_kwargs(body: SolverRunAndParseRequest) -> dict:
    return {
        "project_id": body.project_id,
        "input_file": body.input_file,
        "input_file_name": body.input_file_name,
        "job_name": body.job_name,
        "result_group": body.result_group,
        "display_name": body.display_name,
        "base_url": body.base_url,
        "output_dir": body.output_dir,
        "output_dir_name": body.output_dir_name,
        "output_bdf": body.output_bdf,
        "output_bdf_name": body.output_bdf_name,
        "abaqus": body.abaqus,
        "nastran": body.nastran,
        "cpus": body.cpus,
        "interactive": body.interactive,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
        "settings": body.settings,
        "step": body.step,
        "frame": body.frame,
        "field_prefix": body.field_prefix,
        "upload_timeout": body.upload_timeout,
        "wait_timeout_sec": body.wait_timeout_sec,
        "poll_interval_sec": body.poll_interval_sec,
    }


def _sol200_run_kwargs(body: NastranSol200RunRequest) -> dict:
    input_bdf = _resolve_sol200_input_bdf(
        project_id=body.project_id,
        explicit_path=body.input_bdf,
        file_name=body.input_bdf_name,
    )
    output_bdf = _resolve_project_local_output_file(
        project_id=body.project_id,
        explicit_path=body.output_bdf,
        file_name=body.output_bdf_name,
        category_parts=("solver", "nastran_sol200"),
        field_name="output_bdf",
        default_name=f"{Path(input_bdf).stem}_sol200.bdf" if body.project_id is not None else None,
    )
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "case_name": body.case_name,
        "input_bdf": input_bdf,
        "output_bdf": output_bdf,
        "parameters": [model_to_dict(item) for item in body.parameters],
        "parameter_preset": model_to_dict(body.parameter_preset) if body.parameter_preset else None,
        "responses": [model_to_dict(item) for item in body.responses],
        "settings": body.settings,
        "nastran": body.nastran,
        "run_solver": body.run_solver,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
    }


def _pbs_solver_run_kwargs(body: PBSSolverRunRequest, application: str) -> dict:
    return {
        "project_id": body.project_id,
        "application": application,
        "input_file": body.input_file,
        "input_file_name": body.input_file_name,
        "env": body.env,
        "job_name": body.job_name,
        "output_dir": body.output_dir,
        "output_dir_name": body.output_dir_name,
        "result_group": body.result_group,
        "display_name": body.display_name,
        "base_url": body.base_url,
        "step": body.step,
        "frame": body.frame,
        "field_prefix": body.field_prefix,
        "upload_timeout": body.upload_timeout,
        "wait": body.wait,
        "download_results": body.download_results,
        "poll_interval_sec": body.poll_interval_sec,
        "wait_timeout_sec": body.wait_timeout_sec,
        "timeout_sec": body.timeout_sec,
        "submit_overrides": body.submit_overrides,
    }


@router.post("/solver/abaqus/sensitivity")
async def run_abaqus_sensitivity_api(request: Request, body: AbaqusSensitivityRunRequest):
    # Generate the Abaqus sensitivity deck and optionally launch the solver in
    # one request so callers can use the same endpoint for prep-only or full run.
    await log_request(request, model_to_dict(body))
    try:
        input_inp = _resolve_project_local_input(
            project_id=body.project_id,
            explicit_path=body.input_inp,
            file_name=body.input_inp_name,
            field_name="input_inp",
        )
        output_dir = _resolve_project_local_output_dir(
            project_id=body.project_id,
            explicit_dir=body.output_dir,
            dir_name=body.output_dir_name,
            category_parts=("sensitivity",),
        )
        data = run_abaqus_sensitivity_job(
            input_inp=input_inp,
            output_dir=output_dir,
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


@router.post("/solver/pbs/abaqus/run")
async def run_pbs_abaqus_api(request: Request, body: PBSSolverRunRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _pbs_solver_run_kwargs(body, "Abaqus")
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.pbs.abaqus.run",
                fn=run_pbs_solver_job,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                task_kind="external_solver",
            )
            return success_response(data, "PBS Abaqus 任务已提交")
        data = run_pbs_solver_job(**kwargs)
        return success_response(data, "PBS Abaqus 求解成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/pbs/nastran/run")
async def run_pbs_nastran_api(request: Request, body: PBSSolverRunRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _pbs_solver_run_kwargs(body, "Nastran")
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.pbs.nastran.run",
                fn=run_pbs_solver_job,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                task_kind="external_solver",
            )
            return success_response(data, "PBS Nastran 任务已提交")
        data = run_pbs_solver_job(**kwargs)
        return success_response(data, "PBS Nastran 求解成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/pbs/job/status")
async def get_pbs_job_status_api(request: Request, body: PBSJobStatusRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = get_pbs_job_status(
            project_id=body.project_id,
            env=body.env,
            job_id=body.job_id,
            timeout_sec=body.timeout_sec,
        )
        return success_response(data, "PBS 任务状态获取成功")
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
        input_inp = _resolve_project_local_input(
            project_id=body.project_id,
            explicit_path=body.input_inp,
            file_name=body.input_inp_name,
            field_name="input_inp",
        )
        default_output_name = (
            f"{Path(input_inp).stem}_adjoint_thickness.inp"
            if body.project_id is not None
            else None
        )
        output_inp = _resolve_project_local_output_file(
            project_id=body.project_id,
            explicit_path=body.output_inp,
            file_name=body.output_inp_name,
            category_parts=("solver", "abaqus_adjoint"),
            field_name="output_inp",
            default_name=default_output_name,
        )
        data = run_abaqus_adjoint_job(
            input_inp=input_inp,
            output_inp=output_inp,
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
                task_kind="external_solver",
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
        input_bdf = _resolve_project_local_input(
            project_id=body.project_id,
            explicit_path=body.input_bdf,
            file_name=body.input_bdf_name,
            field_name="input_bdf",
        )
        data = preview_nastran_sol103_job(
            input_bdf=input_bdf,
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
        input_bdf = _resolve_project_local_input(
            project_id=body.project_id,
            explicit_path=body.input_bdf,
            file_name=body.input_bdf_name,
            field_name="input_bdf",
        )
        output_bdf = _resolve_project_local_output_file(
            project_id=body.project_id,
            explicit_path=body.output_bdf,
            file_name=body.output_bdf_name,
            category_parts=("solver", "nastran_sol103"),
            field_name="output_bdf",
            default_name=f"{Path(input_bdf).stem}_sol103.bdf" if body.project_id is not None else None,
        )
        data = generate_nastran_sol103_job(
            input_bdf=input_bdf,
            output_bdf=output_bdf,
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
                task_kind="external_solver",
            )
            return success_response(data, "Nastran SOL103 求解任务已提交")
        data = run_nastran_sol103_job(**kwargs)
        return success_response(data, "Nastran SOL103 求解成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/nastran/sol103/run_and_store_modal")
async def run_nastran_sol103_run_and_store_modal_api(request: Request, body: NastranSol103RunAndStoreModalRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _sol103_run_and_store_modal_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.nastran.sol103.run_and_store_modal",
                fn=run_nastran_sol103_and_store_modal_results,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                task_kind="external_solver",
            )
            return success_response(data, "Nastran SOL103 求解并导入模态结果任务已提交")
        data = run_nastran_sol103_and_store_modal_results(**kwargs)
        return success_response(data, "Nastran SOL103 求解并导入模态结果成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/run_and_parse")
async def run_solver_and_parse_api(request: Request, body: SolverRunAndParseRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _solver_run_and_parse_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.run_and_parse",
                fn=run_solver_and_parse_project_result,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                task_kind="external_solver",
            )
            return success_response(data, "统一计算并解析任务已提交")
        data = await run_in_threadpool(run_solver_and_parse_project_result, **kwargs)
        return success_response(data, "统一计算并解析成功")
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


@router.post("/solver/nastran/sol200/preview")
async def preview_nastran_sol200_api(request: Request, body: NastranSol200PreviewRequest):
    await log_request(request, model_to_dict(body))
    try:
        input_bdf = _resolve_sol200_input_bdf(
            project_id=body.project_id,
            explicit_path=body.input_bdf,
            file_name=body.input_bdf_name,
        )
        data = preview_sol200_workflow(
            project_id=body.project_id,
            input_bdf=input_bdf,
            parameters=[model_to_dict(item) for item in body.parameters],
            parameter_preset=model_to_dict(body.parameter_preset) if body.parameter_preset else None,
            responses=[model_to_dict(item) for item in body.responses],
            settings=body.settings,
        )
        return success_response(data, "Nastran SOL200 预览成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/nastran/sol200/generate")
async def generate_nastran_sol200_api(request: Request, body: NastranSol200GenerateRequest):
    await log_request(request, model_to_dict(body))
    try:
        input_bdf = _resolve_sol200_input_bdf(
            project_id=body.project_id,
            explicit_path=body.input_bdf,
            file_name=body.input_bdf_name,
        )
        output_bdf = _resolve_project_local_output_file(
            project_id=body.project_id,
            explicit_path=body.output_bdf,
            file_name=body.output_bdf_name,
            category_parts=("solver", "nastran_sol200"),
            field_name="output_bdf",
            default_name=f"{Path(input_bdf).stem}_sol200.bdf" if body.project_id is not None else None,
        )
        data = generate_sol200_workflow(
            project_id=body.project_id,
            batch_no=body.batch_no,
            case_name=body.case_name,
            input_bdf=input_bdf,
            output_bdf=output_bdf,
            parameters=[model_to_dict(item) for item in body.parameters],
            parameter_preset=model_to_dict(body.parameter_preset) if body.parameter_preset else None,
            responses=[model_to_dict(item) for item in body.responses],
            settings=body.settings,
        )
        return success_response(data, "Nastran SOL200 生成成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/solver/nastran/sol200/run")
async def run_nastran_sol200_api(request: Request, body: NastranSol200RunRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _sol200_run_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.nastran.sol200.run",
                fn=run_sol200_workflow,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
                task_kind="external_solver",
            )
            return success_response(data, "Nastran SOL200 求解任务已提交")
        data = run_sol200_workflow(**kwargs)
        return success_response(data, "Nastran SOL200 求解成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/import/op2/modal/preview")
async def preview_op2_modal_api(request: Request, body: Op2ModalPreviewRequest):
    await log_request(request, model_to_dict(body))
    try:
        resolved_op2_path = _resolve_modal_op2_path(body.op2_path, body.project_id)
        if body.project_id is not None and (str(body.op2_path or "").strip() or str(body.op2_file_name or "").strip()):
            resolved_op2_path = _resolve_project_local_input_with_fallback(
                project_id=body.project_id,
                explicit_path=body.op2_path,
                file_name=body.op2_file_name,
                field_name="op2_path",
                fallback_category_parts=("solver", "nastran_sol103"),
            )
        resolved_bdf_path = (
            _resolve_project_local_input_with_fallback(
                project_id=int(body.project_id),
                explicit_path=body.bdf_path,
                file_name=body.bdf_file_name,
                field_name="bdf_path",
                fallback_category_parts=("solver", "nastran_sol103"),
            )
            if body.project_id is not None and (str(body.bdf_path or "").strip() or str(body.bdf_file_name or "").strip())
            else body.bdf_path
        )
        data = preview_op2_modal(
            op2_path=resolved_op2_path,
            bdf_path=resolved_bdf_path,
            subcase_id=body.subcase_id,
            mode_numbers=body.mode_numbers,
            preview_node_limit=body.preview_node_limit,
        )
        if body.project_id is not None:
            store_kwargs = {
                "project_id": body.project_id,
                "op2_path": resolved_op2_path,
                "bdf_path": resolved_bdf_path,
                "subcase_id": body.subcase_id,
                "mode_numbers": body.mode_numbers,
                "overwrite": body.overwrite,
                "instance_name": body.instance_name,
                "part_name": body.part_name,
            }
            if body.async_submit:
                data["store"] = submit_background_task(
                    task_type="import.op2.modal.store",
                    fn=_store_op2_modal_job,
                    kwargs=store_kwargs,
                    request_payload=model_to_dict(body),
                )
            else:
                data["store"] = _store_op2_modal_job(**store_kwargs)
        return success_response(data, "OP2 模态预览成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


def _store_op2_modal_job(
    *,
    project_id: int,
    op2_path: str,
    bdf_path: Optional[str],
    subcase_id: Optional[int],
    mode_numbers: Optional[list],
    overwrite: bool,
    instance_name: Optional[str],
    part_name: Optional[str],
):
    payload = build_modal_import_payload(
        op2_path=op2_path,
        bdf_path=bdf_path,
        subcase_id=subcase_id,
        mode_numbers=mode_numbers,
        instance_name=instance_name,
        part_name=part_name,
        all_subcases=subcase_id is None,
    )
    stored = import_fe_modal_results(
        project_id=project_id,
        overwrite=overwrite,
        modes=payload["modes"],
    )
    stored["warnings"] = payload.get("warnings") or []
    return stored


@router.post("/import/op2/modal/store")
async def store_op2_modal_api(request: Request, body: Op2ModalStoreRequest):
    await log_request(request, model_to_dict(body))
    try:
        if str(body.op2_path or "").strip() or str(body.op2_file_name or "").strip():
            resolved_op2_path = _resolve_project_local_input_with_fallback(
                project_id=body.project_id,
                explicit_path=body.op2_path,
                file_name=body.op2_file_name,
                field_name="op2_path",
                fallback_category_parts=("solver", "nastran_sol103"),
            )
        else:
            resolved_op2_path = _resolve_modal_op2_path(body.op2_path, body.project_id)
        resolved_bdf_path = (
            _resolve_project_local_input_with_fallback(
                project_id=body.project_id,
                explicit_path=body.bdf_path,
                file_name=body.bdf_file_name,
                field_name="bdf_path",
                fallback_category_parts=("solver", "nastran_sol103"),
            )
            if str(body.bdf_path or "").strip() or str(body.bdf_file_name or "").strip()
            else None
        )
        kwargs = {
            "project_id": body.project_id,
            "op2_path": resolved_op2_path,
            "bdf_path": resolved_bdf_path,
            "subcase_id": body.subcase_id,
            "mode_numbers": body.mode_numbers,
            "overwrite": body.overwrite,
            "instance_name": body.instance_name,
            "part_name": body.part_name,
        }
        if body.async_submit:
            data = submit_background_task(
                task_type="import.op2.modal.store",
                fn=_store_op2_modal_job,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "OP2 模态导入任务已提交")
        data = _store_op2_modal_job(**kwargs)
        return success_response(data, "OP2 模态导入成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/export/op2/modal/vtu")
async def export_op2_modal_vtu_api(request: Request, body: Op2ModalVtuExportRequest):
    await log_request(request, model_to_dict(body))
    try:
        op2_path = _resolve_project_local_input_with_fallback(
            project_id=body.project_id,
            explicit_path=body.op2_path,
            file_name=body.op2_file_name,
            field_name="op2_path",
            fallback_category_parts=("solver", "nastran_sol103"),
        )
        bdf_path = _resolve_project_local_input_with_fallback(
            project_id=body.project_id,
            explicit_path=body.bdf_path,
            file_name=body.bdf_file_name,
            field_name="bdf_path",
            fallback_category_parts=("solver", "nastran_sol103"),
        )
        output_vtu = _resolve_project_local_output_file(
            project_id=body.project_id,
            explicit_path=body.output_vtu,
            file_name=body.output_vtu_name,
            category_parts=("modal", "exports"),
            field_name="output_vtu",
        )
        data = export_modal_to_vtu(
            op2_path=op2_path,
            bdf_path=bdf_path,
            output_vtu=output_vtu,
            mode_number=body.mode_number,
            subcase_id=body.subcase_id,
            displacement_scale=body.displacement_scale,
        )
        return success_response(data, "OP2 模态 VTU 导出成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/import/op2/sensitivity/preview")
async def preview_op2_sensitivity_api(request: Request, body: Op2SensitivityPreviewRequest):
    await log_request(request, model_to_dict(body))
    try:
        op2_path = (
            _resolve_project_local_input(
                project_id=int(body.project_id),
                explicit_path=body.op2_path,
                file_name=body.op2_file_name,
                field_name="op2_path",
            )
            if body.project_id is not None and (str(body.op2_path or "").strip() or str(body.op2_file_name or "").strip())
            else body.op2_path
        )
        matrix_path = (
            _resolve_project_local_input(
                project_id=int(body.project_id),
                explicit_path=body.matrix_path,
                file_name=body.matrix_file_name,
                field_name="matrix_path",
            )
            if body.project_id is not None and (str(body.matrix_path or "").strip() or str(body.matrix_file_name or "").strip())
            else body.matrix_path
        )
        bdf_path = (
            _resolve_project_local_input(
                project_id=int(body.project_id),
                explicit_path=body.bdf_path,
                file_name=body.bdf_file_name,
                field_name="bdf_path",
            )
            if body.project_id is not None and (str(body.bdf_path or "").strip() or str(body.bdf_file_name or "").strip())
            else body.bdf_path
        )
        metadata_json = (
            _resolve_project_local_input(
                project_id=int(body.project_id),
                explicit_path=body.metadata_json,
                file_name=body.metadata_json_name,
                field_name="metadata_json",
            )
            if body.project_id is not None and (str(body.metadata_json or "").strip() or str(body.metadata_json_name or "").strip())
            else body.metadata_json
        )
        data = preview_sol200_sensitivity(
            project_id=body.project_id,
            batch_no=body.batch_no,
            op2_path=op2_path,
            matrix_path=matrix_path,
            bdf_path=bdf_path,
            metadata_json=metadata_json,
            parameter_names=body.parameter_names,
            response_names=body.response_names,
        )
        return success_response(data, "OP2 灵敏度预览成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/import/op2/sensitivity/store")
async def store_op2_sensitivity_api(request: Request, body: Op2SensitivityStoreRequest):
    await log_request(request, model_to_dict(body))
    try:
        op2_path = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.op2_path,
                file_name=body.op2_file_name,
                field_name="op2_path",
            )
            if str(body.op2_path or "").strip() or str(body.op2_file_name or "").strip()
            else None
        )
        matrix_path = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.matrix_path,
                file_name=body.matrix_file_name,
                field_name="matrix_path",
            )
            if str(body.matrix_path or "").strip() or str(body.matrix_file_name or "").strip()
            else None
        )
        bdf_path = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.bdf_path,
                file_name=body.bdf_file_name,
                field_name="bdf_path",
            )
            if str(body.bdf_path or "").strip() or str(body.bdf_file_name or "").strip()
            else None
        )
        metadata_json = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.metadata_json,
                file_name=body.metadata_json_name,
                field_name="metadata_json",
            )
            if str(body.metadata_json or "").strip() or str(body.metadata_json_name or "").strip()
            else None
        )
        kwargs = {
            "project_id": body.project_id,
            "batch_no": body.batch_no,
            "case_name": body.case_name,
            "op2_path": op2_path,
            "matrix_path": matrix_path,
            "bdf_path": bdf_path,
            "metadata_json": metadata_json,
            "parameter_names": body.parameter_names,
            "response_names": body.response_names,
        }
        if body.async_submit:
            data = submit_background_task(
                task_type="import.op2.sensitivity.store",
                fn=store_sol200_sensitivity,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "OP2 灵敏度导入任务已提交")
        data = store_sol200_sensitivity(**kwargs)
        return success_response(data, "OP2 灵敏度导入成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/import/op2/sensitivity/store_cloud")
async def store_op2_sensitivity_cloud_api(request: Request, body: Op2SensitivityStoreCloudRequest):
    await log_request(request, model_to_dict(body))
    try:
        op2_path = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.op2_path,
                file_name=body.op2_file_name,
                field_name="op2_path",
            )
            if str(body.op2_path or "").strip() or str(body.op2_file_name or "").strip()
            else None
        )
        matrix_path = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.matrix_path,
                file_name=body.matrix_file_name,
                field_name="matrix_path",
            )
            if str(body.matrix_path or "").strip() or str(body.matrix_file_name or "").strip()
            else None
        )
        bdf_path = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.bdf_path,
                file_name=body.bdf_file_name,
                field_name="bdf_path",
            )
            if str(body.bdf_path or "").strip() or str(body.bdf_file_name or "").strip()
            else None
        )
        metadata_json = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.metadata_json,
                file_name=body.metadata_json_name,
                field_name="metadata_json",
            )
            if str(body.metadata_json or "").strip() or str(body.metadata_json_name or "").strip()
            else None
        )
        kwargs = {
            "project_id": body.project_id,
            "batch_no": body.batch_no,
            "case_name": body.case_name,
            "op2_path": op2_path,
            "matrix_path": matrix_path,
            "bdf_path": bdf_path,
            "metadata_json": metadata_json,
            "parameter_names": body.parameter_names,
            "response_names": body.response_names,
            "cloud_result_group": body.cloud_result_group,
            "cloud_step_name": body.cloud_step_name,
            "cloud_field_name": body.cloud_field_name,
        }
        if body.async_submit:
            data = submit_background_task(
                task_type="import.op2.sensitivity.store_cloud",
                fn=store_sol200_sensitivity_cloud,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "OP2 灵敏度入库并生成云图结果任务已提交")
        data = store_sol200_sensitivity_cloud(**kwargs)
        return success_response(data, "OP2 灵敏度入库并生成云图结果成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/export/op2/sensitivity/vtu")
async def export_op2_sensitivity_vtu_api(request: Request, body: Op2SensitivityVtuExportRequest):
    await log_request(request, model_to_dict(body))
    try:
        input_bdf = _resolve_project_local_input(
            project_id=body.project_id,
            explicit_path=body.input_bdf,
            file_name=body.input_bdf_name,
            field_name="input_bdf",
        )
        output_vtu = _resolve_project_local_output_file(
            project_id=body.project_id,
            explicit_path=body.output_vtu,
            file_name=body.output_vtu_name,
            category_parts=("sol200", "exports"),
            field_name="output_vtu",
        )
        metadata_json = (
            _resolve_project_local_input(
                project_id=body.project_id,
                explicit_path=body.metadata_json,
                file_name=body.metadata_json_name,
                field_name="metadata_json",
            )
            if str(body.metadata_json or "").strip() or str(body.metadata_json_name or "").strip()
            else None
        )
        data = export_sol200_sensitivity_vtu(
            project_id=body.project_id,
            batch_no=body.batch_no,
            input_bdf=input_bdf,
            output_vtu=output_vtu,
            response_name=body.response_name,
            metadata_json=metadata_json,
        )
        return success_response(data, "OP2 灵敏度 VTU 导出成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
