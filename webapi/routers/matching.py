from fastapi import APIRouter, Request

from services.model_update.analysis.inp_service import match_test_nodes

from src.l3.core.errors import AppError

from ..common import error_response, server_error, success_response
from ..models import MatchNodesRequest
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


@router.post("/match/nodes")
async def match_nodes_api(request: Request, body: MatchNodesRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = match_test_nodes(
            project_id=body.project_id,
            max_distance=body.max_distance,
            overwrite=body.overwrite,
            auto_translate=body.auto_translate,
            translation=body.translation,
            rotation=model_to_dict(body.rotation) if body.rotation else None,
        )
        return success_response(result, "节点匹配成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
