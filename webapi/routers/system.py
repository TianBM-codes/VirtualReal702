import logging

from fastapi import APIRouter, Query, Request

from db import ensure_tables_exist
from services.model_update.analysis.project_config_service import (
    get_project_config,
    upsert_project_config,
)

from ..background_jobs import get_background_task, get_background_task_by_project_and_interface
from ..common import error_response, server_error, success_response
from ..models import ProjectConfigRequest, ProjectConfigUpsertRequest
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])
logger = logging.getLogger(__name__)


@router.post("/init")
async def init_db():
    try:
        logger.info("开始初始化数据库表")
        logger.info("如果删除表, 需要重新启动服务")
        ensure_tables_exist()
        logger.info("数据库表初始化成功")
        return success_response(None, "数据库表初始化成功")
    except Exception as exc:
        logger.exception("数据库表初始化失败")
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


@router.get("/calc/tasks/{task_id}")
async def query_calc_task_by_id(task_id: str):
    try:
        data = get_background_task(task_id)
        if data is None:
            return error_response(
                404,
                f"未找到任务 '{task_id}'",
                error_code="NOT_FOUND",
                details={"task_id": str(task_id)},
            )
        return success_response(data, "任务状态获取成功")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(
            app_exc.status_code,
            app_exc.message,
            error_code=app_exc.code,
            details=app_exc.details,
        )


@router.get("/calc/task/status")
async def query_calc_task_status(
    project_id: int = Query(..., description="项目 ID"),
    interface_code: str = Query(..., description="接口编码"),
):
    try:
        data = get_background_task_by_project_and_interface(project_id, interface_code)
        if data is None:
            return error_response(
                404,
                "未找到对应任务记录",
                error_code="NOT_FOUND",
                details={
                    "project_id": int(project_id),
                    "interface_code": str(interface_code),
                },
            )
        return success_response(data, "任务状态获取成功")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(
            app_exc.status_code,
            app_exc.message,
            error_code=app_exc.code,
            details=app_exc.details,
        )
