from fastapi import APIRouter

from db import ensure_tables_exist
from services.model_update.analysis.model_update_meta_service import get_abaqus_config

from ..common import error_response, server_error, success_response

router = APIRouter(tags=["model-update"])


@router.post("/init")
async def init_db():
    try:
        ensure_tables_exist()
        return success_response(None, "数据表检查完成")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/config/abaqus")
async def get_abaqus_config_api():
    try:
        return success_response(get_abaqus_config(), "ABAQUS配置读取完成")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
