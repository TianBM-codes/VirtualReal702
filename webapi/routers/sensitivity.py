from fastapi import APIRouter, Request

from services.model_update.analysis.sensitivity_service import (
    build_sensitivity_table,
    build_workspace_from_odb,
    get_sensitivity_overview,
)

from ..common import server_error
from ..models import (
    SensitivityBuildWorkspaceRequest,
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
