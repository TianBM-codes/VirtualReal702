from fastapi import APIRouter, Request

from services.model_update.analysis.inp_service import (
    evaluate_static_correlation,
    get_modal_correlation_all_scatter_payload,
    get_modal_scale_factor_table_payload,
    get_dof_matches,
    get_modal_frequency_consistency_payload,
    get_modal_match_frequency_scatter_payload,
    get_pair_node_point_result,
    match_test_dofs,
    get_transform_auto_info,
    match_test_nodes,
    save_transform_operation,
)
from services.model_update.analysis.project_config_service import get_node_match_parameter_context

from src.l3.core.errors import AppError

from ..common import error_response, server_error, success_response
from ..models import (
    CorrelationEvaluateRequest,
    DofMatchResultRequest,
    MatchDofsRequest,
    MatchNodeParametersRequest,
    MatchNodesRequest,
    ModalCorrelationScatterRequest,
    ModalMatchScatterRequest,
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
            auto_rotate=body.auto_rotate,
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


@router.post("/match/dofs")
async def match_dofs_api(request: Request, body: MatchDofsRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = match_test_dofs(
            project_id=body.project_id,
            overwrite=body.overwrite,
            min_match_score=body.min_match_score,
        )
        return success_response(result, "自由度匹配成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/get/dof_match_result")
async def get_dof_match_result_api(request: Request, body: DofMatchResultRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_dof_matches(body.project_id)
        return success_response(result, "自由度匹配结果获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/match/nodes/params")
async def get_match_node_parameters_api(request: Request, body: MatchNodeParametersRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_node_match_parameter_context(body.project_id)
        return success_response(result, "节点匹配参数获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/correlation/evaluate")
async def evaluate_correlation_api(request: Request, body: CorrelationEvaluateRequest):
    await log_request(request, model_to_dict(body))
    try:
        project_type = str(body.project_type or "JLXZ").strip().upper()
        if project_type == "JLXZ":
            result = evaluate_static_correlation(
                project_id=body.project_id,
                load_case_no=body.load_case_no,
                result_no=body.result_no,
                components=body.components,
                include_rotations=body.include_rotations,
            )
        elif project_type == "MTXZ":
            result = get_modal_frequency_consistency_payload(body.project_id)
        else:
            raise AppError(
                message="unsupported project_type",
                status_code=400,
                code="VALIDATION_ERROR",
                details={"project_type": body.project_type, "allowed": ["JLXZ", "MTXZ"]},
            )
        return success_response(result, "一致性评价成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/correlation/modal/match/frequency_scatter")
async def modal_match_frequency_scatter_api(request: Request, body: ModalMatchScatterRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_modal_match_frequency_scatter_payload(
            project_id=body.project_id,
            mac_threshold=body.mac_threshold,
            max_freq_error_ratio=body.max_freq_error_ratio,
            method=body.method,
        )
        return success_response(result, "模态频率匹配散点图获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/correlation/modal/all_scatter")
async def modal_correlation_all_scatter_api(request: Request, body: ModalCorrelationScatterRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_modal_correlation_all_scatter_payload(
            project_id=body.project_id,
            mac_threshold=body.mac_threshold,
            max_freq_error_ratio=body.max_freq_error_ratio,
            method=body.method,
        )
        return success_response(result, "频率相关性散点图计算成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/correlation/modal/msf/table")
async def modal_scale_factor_table_api(request: Request, body: ModalCorrelationScatterRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_modal_scale_factor_table_payload(project_id=body.project_id)
        return success_response(result, "MSF结果返回成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/correlation/modal/frequency_consistency")
async def modal_frequency_consistency_api(request: Request, body: ModalCorrelationScatterRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_modal_frequency_consistency_payload(project_id=body.project_id)
        return success_response(result, "频率一致性计算成功")
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
            matrix4_fem=body.matrix4_fem,
            matrix4_test=body.matrix4_test,
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
        )
        return success_response(result, "空间变换获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
