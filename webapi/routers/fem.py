from fastapi import APIRouter, Request

from services.model_update.importers.bdf_service import import_bdf_data
from services.model_update.analysis.inp_service import get_inp_catalog, import_inp_catalog
from services.model_update.analysis.inp_tree_service import get_inp_tree
from src.l3.core.errors import AppError

from ..common import error_response, server_error, success_response
from ..models import ImportBdfRequest, ImportInpCatalogRequest, InpCatalogRequest, InpTreeRequest
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


@router.post("/import/bdf")
async def import_bdf(request: Request, body: ImportBdfRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = import_bdf_data(
            body.file_path,
            body.project_id,
            clear_before_insert=body.clear_before_insert,
        )
        return success_response(result, "BDF 导入成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/import/inp/catalog")
async def import_inp_catalog_api(request: Request, body: ImportInpCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = import_inp_catalog(
            file_path=body.file_path,
            project_id=body.project_id,
            clear_before_insert=body.clear_before_insert,
            build_octree=body.build_octree,
            force_rebuild_octree=body.force_rebuild_octree,
        )
        return success_response(result, "INP 目录导入成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/catalog/inp")
async def get_inp_catalog_api(request: Request, body: InpCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_inp_catalog(body.project_id)
        return success_response(result, "INP 目录查询成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/tools/inp/tree")
async def get_inp_tree_api(request: Request, body: InpTreeRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_inp_tree(
            file_path=body.file_path,
            show_labels=body.show_labels,
            max_labels=body.max_labels,
        )
        return success_response(result, "INP 树解析成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
