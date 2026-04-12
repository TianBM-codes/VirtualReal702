from fastapi import APIRouter, Request

from services.model_update.analysis.sensitivity_service import (
    build_sensitivity_table,
    build_workspace_from_odb,
    export_adjoint_sensitivity_vtu,
    export_dsa_sensitivity_vtu,
    export_odb_sensitivity_vtu,
    get_sensitivity_overview,
)

from ..common import server_error
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
    await log_request(request, model_to_dict(body))
    try:
        data = build_workspace_from_odb(
            odb_path=body.odb_path,
            workspace=body.workspace,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
        )
        return {"ok": True, "message": "sensitivity workspace built", "data": data}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/sensitivity/overview")
async def sensitivity_overview(request: Request, body: SensitivityOverviewRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = get_sensitivity_overview(body.workspace)
        return {"ok": True, "message": "sensitivity overview success", "data": data}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/sensitivity/table")
async def sensitivity_table(request: Request, body: SensitivityTableRequest):
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
        return {"ok": True, "message": "sensitivity table success", "data": data}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/sensitivity/export/vtu")
async def sensitivity_export_vtu(request: Request, body: SensitivityExportVtuRequest):
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
            position=body.position,
            aggregation=body.aggregation,
            frame=body.frame,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
            timeout=body.timeout,
        )
        return {"ok": True, "message": "sensitivity vtu export success", "data": data}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/sensitivity/export/vtu/dsa")
async def sensitivity_export_dsa_vtu(request: Request, body: SensitivityExportDsaVtuRequest):
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
            position=body.position,
            aggregation=body.aggregation,
            frame=body.frame,
            abaqus=body.abaqus,
            python3=body.python3,
            keep_raw=body.keep_raw,
            timeout=body.timeout,
        )
        return {"ok": True, "message": "dsa sensitivity vtu export success", "data": data}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/sensitivity/export/vtu/adjoint")
async def sensitivity_export_adjoint_vtu(request: Request, body: SensitivityExportAdjointVtuRequest):
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
        return {"ok": True, "message": "adjoint sensitivity vtu export success", "data": data}
    except Exception as exc:
        raise server_error(exc) from exc
