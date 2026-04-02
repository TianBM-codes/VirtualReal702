from fastapi import APIRouter, Request

from services.model_update.importers.bdf_service import import_bdf_data
from services.model_update.analysis.inp_service import get_inp_catalog, import_inp_catalog
from services.model_update.analysis.inp_tree_service import get_inp_tree
from src.l3.core.errors import AppError

from ..common import server_error
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
        return {"ok": True, "message": "bdf import success", "data": result}
    except AppError:
        raise
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
    except AppError:
        raise
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/catalog/inp")
async def get_inp_catalog_api(request: Request, body: InpCatalogRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_inp_catalog(body.project_id)
        return {"ok": True, "message": "inp catalog query success", "data": result}
    except AppError:
        raise
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/tools/inp/tree")
async def get_inp_tree_api(request: Request, body: InpTreeRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_inp_tree(
            file_path=body.file_path,
            show_labels=body.show_labels,
            max_labels=body.max_labels,
        )
        return {"ok": True, "message": "inp tree parse success", "data": result}
    except AppError:
        raise
    except Exception as exc:
        raise server_error(exc) from exc
