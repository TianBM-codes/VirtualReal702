from fastapi import APIRouter, Request

from db import ensure_tables_exist
from services.model_update.analysis.project_config_service import (
    get_project_config,
    upsert_project_config,
)

from ..common import error_response, server_error, success_response
from ..models import ProjectConfigRequest, ProjectConfigUpsertRequest
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


@router.post("/init")
async def init_db():
    try:
        ensure_tables_exist()
        return success_response(None, "database tables are ready")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(
            app_exc.status_code,
            app_exc.message,
            error_code=app_exc.code,
            details=app_exc.details,
        )


@router.post("/project/config")
async def save_project_config(request: Request, body: ProjectConfigUpsertRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = upsert_project_config(
            project_id=body.project_id,
            test_model_dims=model_to_dict(body.test_model_dims) if body.test_model_dims else None,
            fem_model_dims=model_to_dict(body.fem_model_dims) if body.fem_model_dims else None,
            coefficients=body.coefficients,
            extra_json=body.extra_json,
        )
        return success_response(data, "项目配置保存成功")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(
            app_exc.status_code,
            app_exc.message,
            error_code=app_exc.code,
            details=app_exc.details,
        )


@router.post("/project/config/query")
async def query_project_config(request: Request, body: ProjectConfigRequest):
    await log_request(request, model_to_dict(body))
    try:
        return success_response(get_project_config(body.project_id), "项目配置获取成功")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(
            app_exc.status_code,
            app_exc.message,
            error_code=app_exc.code,
            details=app_exc.details,
        )
