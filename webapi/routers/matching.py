from fastapi import APIRouter, Request

from services.model_update.analysis.inp_service import match_test_nodes

from ..common import server_error
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
        return {"ok": True, "message": "node match success", "data": result}
    except Exception as exc:
        raise server_error(exc) from exc
