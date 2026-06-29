from fastapi import APIRouter, Request

from services.model_update.analysis.test_unit_service import (
    convert_test_unit_system,
    get_test_unit_status,
)
from services.model_update.importers.unv_service import (
    dump_unv_modal_shapes_to_vtk,
    dump_unv_modal_to_json,
    get_deform_sensor_positions,
    get_frf_curve,
    get_frf_names,
    get_modal_shape,
    get_sensor_positions,
    import_unv_data,
    get_sensor_relative_error
)
from src.l3.core.config import settings
from src.l3.core.errors import AppError
from src.l3.infra.registry_repo import RegistryRepo
from src.utils.file_fetch import download_if_url

from ..common import error_response, server_error, success_response
from ..models import (
    DeformSensorPositionRequest,
    DumpJsonRequest,
    DumpVtkRequest,
    FrfCurveRequest,
    FrfNamesRequest,
    ImportUnvRequest,
    PlotModalShapeRequest,
    SensorPositionRequest,
    TestUnitConvertRequest,
    TestUnitStatusRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["model-update"])


def _resolve_unv_download_dir(project_id: int = None) -> str:
    if project_id is None:
        return settings.data_root
    try:
        repo = RegistryRepo(settings.registry_db_path)
        project_row = repo.get_project(str(int(project_id)))
        if project_row is None:
            return settings.data_root
        return repo.resolve_workspace(project_row["workspace"], settings.data_root)
    except Exception:
        return settings.data_root


@router.post("/import/unv")
async def import_unv(request: Request, body: ImportUnvRequest):
    await log_request(request, model_to_dict(body))
    try:
        resolved_file_path = download_if_url(
            body.file_path,
            dest_dir=_resolve_unv_download_dir(body.project_id),
        )
        result = import_unv_data(
            resolved_file_path,
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


@router.post("/get/sensor_position")
async def get_sensor_position_api(request: Request, body: SensorPositionRequest):
    await log_request(request, model_to_dict(body))
    try:
        return success_response(get_sensor_positions(body.project_id), "测点位置获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)

@router.post("/get/sensor_relative_error")
async def get_sensor_relative_error_api(request: Request, body: SensorPositionRequest):
    await log_request(request, model_to_dict(body))
    try:
        return success_response(get_sensor_relative_error(body.project_id), "测点位置获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/frf/names")
async def get_frf_names_api(request: Request, body: FrfNamesRequest):
    await log_request(request, model_to_dict(body))
    try:
        return success_response(get_frf_names(body.project_id), "FRF 曲线名称获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/frf/curve")
async def get_frf_curve_api(request: Request, body: FrfCurveRequest):
    await log_request(request, model_to_dict(body))
    try:
        return success_response(
            get_frf_curve(body.project_id, name=body.name, names=body.names, index=body.index),
            "FRF 曲线获取成功",
        )
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/get/deform_sensor_position")
async def get_deform_sensor_position_api(request: Request, body: DeformSensorPositionRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_deform_sensor_positions(
            project_id=body.project_id,
            scale=body.scale,
            static_result_id=body.static_result_id,
            load_case_no=body.load_case_no,
            result_no=body.result_no,
        )
        return success_response(result, "变形后测点位置获取成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/test/unit/convert")
async def convert_test_unit_api(request: Request, body: TestUnitConvertRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = convert_test_unit_system(
            project_id=body.project_id,
            from_unit=body.from_unit,
            to_unit=body.to_unit,
        )
        return success_response(result, "试验单位校正成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


@router.post("/test/unit/status")
async def get_test_unit_status_api(request: Request, body: TestUnitStatusRequest):
    await log_request(request, model_to_dict(body))
    try:
        result = get_test_unit_status(project_id=body.project_id)
        return success_response(result, "试验单位状态获取成功")
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
