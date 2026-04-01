from fastapi import APIRouter, Request

from services.inp_service import create_optimization_parameter
from src.l3.core.errors import AppError, ValidationError

from ..common import server_error
from ..models import CreateOptimizationParameterRequest
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


@router.post("/optimization/parameter/create")
async def create_optimization_parameter_api(request: Request, body: CreateOptimizationParameterRequest):
    await log_request(request, model_to_dict(body))
    try:
        if not body.candidate_code or not body.set_name:
            raise ValidationError("project_id, candidate_code and set_name are required")

        result = create_optimization_parameter(
            project_id=body.project_id,
            candidate_code=body.candidate_code,
            set_name=body.set_name,
            parameter_name=body.parameter_name,
            description=body.description,
            set_type=body.set_type,
            set_scope=body.set_scope,
            instance_name=body.instance_name,
            part_name=body.part_name,
        )
        return {"ok": True, "message": "optimization parameter created", "data": result}
    except AppError:
        raise
    except Exception as exc:
        raise server_error(exc) from exc
