from fastapi import APIRouter, Request

from services.model_update.analysis.inp_service import (
    evaluate_static_correlation,
    get_pair_node_point_result,
    get_transform_auto_info,
    match_test_nodes,
    save_transform_operation,
)

from src.l3.core.errors import AppError

from ..common import error_response, server_error, success_response
from ..models import (
    CorrelationEvaluateRequest,
    MatchNodesRequest,
    PairNodePointResultRequest,
    TransformAutoInfoRequest,
    TransformOperationRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


@router.post("/pair/node_point")
@router.post("/match/nodes")
async def match_nodes_api(request: Request, body: MatchNodesRequest):
    # Node matching bridges imported test coordinates to the FE model so later
    # modal/static correlation can reuse a stable node alignment table.
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


@router.post("/get/pair_node_point_result")
async def get_pair_node_point_result_api(request: Request, body: PairNodePointResultRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_pair_node_point_result(body.project_id)
        return success_response(result, "节点匹配结果获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/correlation/evaluate")
async def evaluate_correlation_api(request: Request, body: CorrelationEvaluateRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = evaluate_static_correlation(
            project_id=body.project_id,
            load_case_no=body.load_case_no,
            result_no=body.result_no,
            components=body.components,
            include_rotations=body.include_rotations,
        )
        return success_response(result, "一致性评价成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/transform/operation")
async def save_transform_operation_api(request: Request, body: TransformOperationRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = save_transform_operation(
            project_id=body.project_id,
            transform_type=body.type,
            matrix4=body.matrix4,
        )
        return success_response(result, "空间变换保存成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/transform/auto_info")
async def get_transform_auto_info_api(request: Request, body: TransformAutoInfoRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_transform_auto_info(
            project_id=body.project_id,
            transform_type=body.type,
        )
        return success_response(result, "空间变换获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
