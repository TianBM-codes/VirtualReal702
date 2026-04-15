from fastapi import APIRouter, Request

from services.model_update.importers.unv_service import (
    dump_unv_modal_shapes_to_vtk,
    dump_unv_modal_to_json,
    get_modal_shape,
    import_unv_data,
)
from src.l3.core.errors import AppError

from ..common import error_response, server_error, success_response
from ..models import DumpJsonRequest, DumpVtkRequest, ImportUnvRequest, PlotModalShapeRequest
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


@router.post("/import/unv")
async def import_unv(request: Request, body: ImportUnvRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = import_unv_data(
            body.file_path,
            project_id=body.project_id,
            file_id=body.file_id,
            clear_before_insert=body.clear_before_insert,
        )
        return success_response(result, "UNV 导入成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/plot/modal_shape")
async def get_modal_plot_json(request: Request, body: PlotModalShapeRequest):
    await log_request(request, model_to_dict(body))
    try:
        return success_response(get_modal_shape(body.project_id), "模态振型获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/dump/vtk")
async def plot_modal_shape_to_vtk(request: Request, body: DumpVtkRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = dump_unv_modal_shapes_to_vtk(body.project_id, output_path=body.vtk_path)
        return success_response(result, "VTK 文件生成成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/dump/json")
async def export_modal_shape_to_json(request: Request, body: DumpJsonRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = dump_unv_modal_to_json(body.project_id, output_path=body.json_path)
        return success_response(result, "JSON 文件生成成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
