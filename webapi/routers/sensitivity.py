from fastapi import APIRouter, Request

from services.model_update.analysis.sensitivity_service import (
    build_sensitivity_table,
    build_workspace_from_odb,
    export_adjoint_sensitivity_vtu,
    export_dsa_sensitivity_vtu,
    export_odb_sensitivity_vtu,
    get_sensitivity_overview,
)
from src.l3.core.errors import AppError

from ..common import error_response, server_error, success_response
from ..models import (
    SensitivityBuildWorkspaceRequest,
    SensitivityExportAdjointVtuRequest,
    SensitivityExportDsaVtuRequest,
    SensitivityExportVtuRequest,
    SensitivityOverviewRequest,
    SensitivityTableRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["sensitivity"])


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
