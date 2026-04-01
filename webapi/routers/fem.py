from fastapi import APIRouter, Request

from services.bdf_service import import_bdf_data
from services.inp_service import get_inp_catalog, import_inp_catalog

from ..common import server_error
from ..models import ImportBdfRequest, ImportInpCatalogRequest, InpCatalogRequest
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
        return {"ok": True, "message": "bdf import success", "data": result}
    except Exception as exc:
        raise server_error(exc) from exc


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
        return {"ok": True, "message": "inp catalog import success", "data": result}
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/catalog/inp")
async def get_inp_catalog_api(request: Request, body: InpCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_inp_catalog(body.project_id)
        return {"ok": True, "message": "inp catalog query success", "data": result}
    except Exception as exc:
        raise server_error(exc) from exc
