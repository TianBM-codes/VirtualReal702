from fastapi import APIRouter, Request

from services.unv_service import (
    dump_unv_modal_shapes_to_vtk,
    dump_unv_modal_to_json,
    get_modal_shape,
    import_unv_data,
)

from ..common import server_error
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
        return {"ok": True, "message": "unv import success", "data": result}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/plot/modal_shape")
async def get_modal_plot_json(request: Request, body: PlotModalShapeRequest):
    await log_request(request, model_to_dict(body))
    try:
        return {"ok": True, "message": "unv import success", "data": get_modal_shape(body.project_id)}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/dump/vtk")
async def plot_modal_shape_to_vtk(request: Request, body: DumpVtkRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = dump_unv_modal_shapes_to_vtk(body.project_id, output_path=body.vtk_path)
        return {"ok": True, "message": "generate vtk success", "data": result}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/dump/json")
async def export_modal_shape_to_json(request: Request, body: DumpJsonRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = dump_unv_modal_to_json(body.project_id, output_path=body.json_path)
        return {"ok": True, "message": "generate json file success", "data": result}
    except Exception as exc:
        raise server_error(exc) from exc
