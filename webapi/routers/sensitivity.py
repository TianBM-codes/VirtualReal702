from fastapi import APIRouter, Request

from services.model_update.analysis.sensitivity_service import (
    build_sensitivity_table,
    build_workspace_from_odb,
    export_adjoint_sensitivity_vtu,
    export_dsa_sensitivity_vtu,
    export_odb_sensitivity_vtu,
    generate_sensitivity_inp_and_store,
    get_stored_sensitivity_matrix_payload,
    get_stored_sensitivity_table_points,
    get_sensitivity_overview,
    merge_dsa_sensitivity_fields,
    run_sensitivity_inp_and_store,
    store_dsa_sensitivity_results,
)
from src.l3.core.errors import AppError

from ..background_jobs import get_background_task, submit_background_task
from ..common import error_response, server_error, success_response
from ..models import (
    SensitivityBuildWorkspaceRequest,
    SensitivityExportAdjointVtuRequest,
    SensitivityExportDsaVtuRequest,
    SensitivityExportVtuRequest,
    SensitivityGenerateRunAndStoreRequest,
    SensitivityMergeFieldsRequest,
    SensitivityOverviewRequest,
    SensitivityRunAndStoreRequest,
    SensitivityStoredQueryRequest,
    SensitivityStoreDsaRequest,
    SensitivityTableRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["sensitivity"])


def _store_dsa_kwargs(body: SensitivityStoreDsaRequest) -> dict:
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "input_inp": body.input_inp,
        "output_dir": body.output_dir,
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
        "response_elset": body.response_elset,
        "response_nset": body.response_nset,
        "response_frequency": body.response_frequency,
        "node_vars": body.node_vars,
        "element_vars": body.element_vars,
        "abaqus": body.abaqus,
        "python3": body.python3,
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
    }


def _run_and_store_kwargs(body: SensitivityRunAndStoreRequest) -> dict:
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "input_inp": body.input_inp,
        "output_dir": body.output_dir,
        "step": body.step,
        "instances": body.instances,
        "field_prefix": body.field_prefix,
        "response_component": body.response_component,
        "position": body.position,
        "aggregation": body.aggregation,
        "frame": body.frame,
        "abaqus": body.abaqus,
        "python3": body.python3,
        "base_url": body.base_url,
        "keep_raw": body.keep_raw,
        "timeout": body.timeout,
        "job_name": body.job_name,
        "cpus": body.cpus,
        "interactive": body.interactive,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
        "cleanup_process_files": body.cleanup_process_files,
        "parse_via_project_results": body.parse_via_project_results,
        "project_result_group": body.project_result_group,
        "project_result_display_name": body.project_result_display_name,
        "project_result_wait_timeout_sec": body.project_result_wait_timeout_sec,
        "project_result_poll_interval_sec": body.project_result_poll_interval_sec,
        "write_cloud_result": body.write_cloud_result,
        "cloud_result_group": body.cloud_result_group,
        "cloud_step_name": body.cloud_step_name,
        "cloud_field_name": body.cloud_field_name,
        "merge_fields": body.merge_fields,
        "merge_result_group": body.merge_result_group,
    }


def _generate_run_and_store_kwargs(body: SensitivityGenerateRunAndStoreRequest) -> dict:
    return {
        "project_id": body.project_id,
        "batch_no": body.batch_no,
        "input_inp": body.input_inp,
        "output_dir": body.output_dir,
        "step": body.step,
        "instances": body.instances,
        "field_prefix": body.field_prefix,
        "response_component": body.response_component,
        "position": body.position,
        "aggregation": body.aggregation,
        "frame": body.frame,
        "abaqus": body.abaqus,
        "python3": body.python3,
        "base_url": body.base_url,
        "keep_raw": body.keep_raw,
        "timeout": body.timeout,
        "job_name": body.job_name,
        "cpus": body.cpus,
        "interactive": body.interactive,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
        "cleanup_process_files": body.cleanup_process_files,
        "parse_via_project_results": body.parse_via_project_results,
        "project_result_group": body.project_result_group,
        "project_result_display_name": body.project_result_display_name,
        "project_result_wait_timeout_sec": body.project_result_wait_timeout_sec,
        "project_result_poll_interval_sec": body.project_result_poll_interval_sec,
    }


@router.post("/sensitivity/calculate_and_store")
async def sensitivity_store_dsa(request: Request, body: SensitivityStoreDsaRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _store_dsa_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="sensitivity.calculate_and_store",
                fn=store_dsa_sensitivity_results,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "sensitivity results task submitted")
        data = store_dsa_sensitivity_results(**kwargs)
        return success_response(data, "sensitivity results stored")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/run_and_store")
async def sensitivity_run_and_store(request: Request, body: SensitivityRunAndStoreRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _run_and_store_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="sensitivity.run_and_store",
                fn=run_sensitivity_inp_and_store,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "sensitivity inp run and store task submitted")
        data = run_sensitivity_inp_and_store(**kwargs)
        return success_response(data, "sensitivity inp run and store completed")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/generate_run_and_store")
async def sensitivity_generate_run_and_store(request: Request, body: SensitivityGenerateRunAndStoreRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _generate_run_and_store_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="sensitivity.generate_run_and_store",
                fn=generate_sensitivity_inp_and_store,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "sensitivity inp generate-run-store task submitted")
        data = generate_sensitivity_inp_and_store(**kwargs)
        return success_response(data, "sensitivity inp generated, run, and store completed")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.get("/sensitivity/tasks/{task_id}")
async def sensitivity_task_status(task_id: str):
    try:
        data = get_background_task(task_id)
        if data is None:
            return error_response(
                404,
                f"sensitivity task '{task_id}' not found",
                error_code="NOT_FOUND",
                details={"task_id": str(task_id)},
            )
        return success_response(data, "sensitivity task status loaded")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/stored/table")
async def sensitivity_stored_table(request: Request, body: SensitivityStoredQueryRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = get_stored_sensitivity_table_points(
            project_id=body.project_id,
            batch_no=body.batch_no,
        )
        return success_response(data, "stored sensitivity table loaded")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/stored/matrix")
async def sensitivity_stored_matrix(request: Request, body: SensitivityStoredQueryRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = get_stored_sensitivity_matrix_payload(
            project_id=body.project_id,
            batch_no=body.batch_no,
        )
        return success_response(data, "stored sensitivity matrix loaded")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/workspace/build")
async def build_sensitivity_workspace(request: Request, body: SensitivityBuildWorkspaceRequest):
    # Build the SQLite/HDF5 workspace once from an ODB so later endpoints can
    # query sensitivities repeatedly without reopening the raw solver result.
    await log_request(request, model_to_dict(body))
    try:
        data = build_workspace_from_odb(
            odb_path=body.odb_path,
            workspace=body.workspace,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
        )
        return success_response(data, "灵敏度工作区构建成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/overview")
async def sensitivity_overview(request: Request, body: SensitivityOverviewRequest):
    # Surface the available steps, frames, instances, and fields stored in an
    # existing workspace before the caller asks for a specific sensitivity view.
    await log_request(request, model_to_dict(body))
    try:
        data = get_sensitivity_overview(body.workspace)
        return success_response(data, "灵敏度概览获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/table")
async def sensitivity_table(request: Request, body: SensitivityTableRequest):
    # Return a tabular slice from the workspace after step/field/position
    # selection and the requested aggregation rules have been applied.
    await log_request(request, model_to_dict(body))
    try:
        data = build_sensitivity_table(
            workspace=body.workspace,
            step=body.step,
            field=body.field,
            instance=body.instance,
            position=body.position,
            components=body.components,
            frame_indices=body.frame_indices,
            entity_labels=body.entity_labels,
            aggregation=body.aggregation,
        )
        return success_response(data, "灵敏度表格获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/export/vtu")
async def sensitivity_export_vtu(request: Request, body: SensitivityExportVtuRequest):
    # Export DSA-style ODB sensitivity fields to VTU for visual inspection.
    await log_request(request, model_to_dict(body))
    try:
        data = export_odb_sensitivity_vtu(
            project_id=body.project_id,
            odb_id=body.odb_id,
            output_vtu=body.output_vtu,
            base_url=body.base_url,
            inp_path=body.inp_path,
            workspace=body.workspace,
            odb_path=body.odb_path,
            step=body.step,
            instances=body.instances,
            field_prefix=body.field_prefix,
            response_component=body.response_component,
            position=body.position,
            aggregation=body.aggregation,
            frame=body.frame,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
            timeout=body.timeout,
        )
        return success_response(data, "灵敏度 VTU 导出成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/export/vtu/dsa")
async def sensitivity_export_dsa_vtu(request: Request, body: SensitivityExportDsaVtuRequest):
    # Explicit DSA export path kept separate from the generic endpoint for
    # clients that already know they need the design-sensitivity convention.
    await log_request(request, model_to_dict(body))
    try:
        data = export_dsa_sensitivity_vtu(
            project_id=body.project_id,
            odb_id=body.odb_id,
            output_vtu=body.output_vtu,
            base_url=body.base_url,
            inp_path=body.inp_path,
            workspace=body.workspace,
            odb_path=body.odb_path,
            step=body.step,
            instances=body.instances,
            field_prefix=body.field_prefix,
            response_component=body.response_component,
            position=body.position,
            aggregation=body.aggregation,
            frame=body.frame,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
            timeout=body.timeout,
        )
        return success_response(data, "DSA 灵敏度 VTU 导出成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/sensitivity/export/vtu/adjoint")
async def sensitivity_export_adjoint_vtu(request: Request, body: SensitivityExportAdjointVtuRequest):
    # Adjoint sensitivity fields use a different naming convention, so the
    # route delegates to a dedicated exporter instead of the DSA helper.
    await log_request(request, model_to_dict(body))
    try:
        data = export_adjoint_sensitivity_vtu(
            project_id=body.project_id,
            odb_id=body.odb_id,
            output_vtu=body.output_vtu,
            base_url=body.base_url,
            inp_path=body.inp_path,
            workspace=body.workspace,
            odb_path=body.odb_path,
            step=body.step,
            instances=body.instances,
            field_name=body.field_name,
            position=body.position,
            aggregation=body.aggregation,
            frame=body.frame,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
            timeout=body.timeout,
        )
        return success_response(data, "伴随灵敏度 VTU 导出成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


def _merge_fields_kwargs(body: SensitivityMergeFieldsRequest) -> dict:
    return {
        "project_id": body.project_id,
        "workspace": body.workspace,
        "step": body.step,
        "frame": body.frame,
        "field_prefix": body.field_prefix,
        "instances": body.instances,
        "result_group": body.result_group,
        "source_result_group": body.source_result_group,
    }


@router.post("/sensitivity/merge_fields")
async def sensitivity_merge_fields(request: Request, body: SensitivityMergeFieldsRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _merge_fields_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="sensitivity.merge_fields",
                fn=merge_dsa_sensitivity_fields,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "DSA 灵敏度场合并任务已提交")
        data = merge_dsa_sensitivity_fields(**kwargs)
        return success_response(data, "DSA 灵敏度场合并完成")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
