import json
import time
from contextlib import asynccontextmanager
from datetime import datetime

from pyNastran.converters.format_converter import process_ugrid

from config import APP_CONFIG, DB_CONFIG, DB_INIT_ON_STARTUP
from db import initialize_database_runtime
from fastapi import Request
from src.l3.main import app
from src.l3.api.routes.meta import list_steps as list_src_steps

from webapi.routes import router as model_update_router
from webapi.background_jobs import recover_background_tasks
from webapi.common import error_response, server_error, success_response
from src.modal_service.middleware import add_test_mesh_route_rewrite_middleware
from src.modal_service.routes import router as modal_router
from src.l3.core.errors import AppError
from services.model_update.analysis.inp_service import (
    build_fe_response_catalog,
    compute_modal_correlation,
    evaluate_static_correlation,
    get_dof_matches,
    get_fe_modal_results,
    get_fe_response_catalog,
    get_fe_static_results,
    get_modal_match_frequency_scatter_payload,
    get_modal_correlation,
    get_modal_correlation_matrix_payload,
    get_modal_correlation_table_payload,
    get_modal_scale_factor_table_payload,
    import_fe_modal_results,
    import_fe_static_results,
    match_modal_modes,
    match_test_dofs,
    preview_modal_match,
)

app.include_router(model_update_router)
app.include_router(modal_router)
add_test_mesh_route_rewrite_middleware(app)

_base_lifespan = app.router.lifespan_context


def _startup_database() -> None:
    """
    Warm the DB pool before the first frontend request arrives, moving the
    one-time handshake cost out of endpoints such as /get/sensor_position.

    A MySQL that is unreachable or slow must not keep the HTTP server from
    binding: the ODB viewer routes do not touch MySQL at all. On failure the
    pool stays uninitialised and the first request that needs it rebuilds it.
    """
    if not DB_INIT_ON_STARTUP:
        print("[startup] DB_INIT_ON_STARTUP=0 — deferring MySQL init to first request")
        return
    started = time.perf_counter()
    try:
        initialize_database_runtime(ensure_tables=True, warm_connection=True)
        recover_background_tasks()
        print(f"[startup] MySQL ready in {time.perf_counter() - started:.2f}s")
    except Exception as exc:
        print(
            f"[startup] MySQL init FAILED after {time.perf_counter() - started:.2f}s "
            f"({DB_CONFIG['host']}:{DB_CONFIG['port']}): {exc!r}\n"
            f"[startup] Serving anyway — ODB viewer routes work; "
            f"model-update routes will retry on first use."
        )


@asynccontextmanager
async def model_update_lifespan(application):
    async with _base_lifespan(application):
        _startup_database()
        yield


app.router.lifespan_context = model_update_lifespan


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    start_perf = time.perf_counter()
    start_iso = _now_iso()

    # 读取 body 并回填，让下游路由仍能正常读取
    raw_body = await request.body()

    async def _receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    request._receive = _receive

    try:
        body_text = raw_body.decode("utf-8")
    except Exception:
        body_text = f"<binary {len(raw_body)} bytes>"

    request_info = {
        "event": "request_start",
        "time": start_iso,
        "method": request.method,
        "path": request.url.path,
        "query": str(request.url.query or ""),
        "client_ip": request.client.host if request.client else None,
        "body": body_text,
    }
    print("[HTTP Timing] " + json.dumps(request_info, ensure_ascii=False, default=str))

    try:
        response = await call_next(request)
    except Exception as exc:
        end_iso = _now_iso()
        elapsed_ms = round((time.perf_counter() - start_perf) * 1000.0, 3)
        error_info = {
            "event": "request_error",
            "start_time": start_iso,
            "end_time": end_iso,
            "elapsed_ms": elapsed_ms,
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query or ""),
            "client_ip": request.client.host if request.client else None,
            "error": repr(exc),
        }
        print("[HTTP Timing] " + json.dumps(error_info, ensure_ascii=False, default=str))
        raise

    end_iso = _now_iso()
    elapsed_ms = round((time.perf_counter() - start_perf) * 1000.0, 3)
    response.headers["X-Elapsed-Time-Ms"] = str(elapsed_ms)
    response_info = {
        "event": "request_end",
        "start_time": start_iso,
        "end_time": end_iso,
        "elapsed_ms": elapsed_ms,
        "method": request.method,
        "path": request.url.path,
        "query": str(request.url.query or ""),
        "client_ip": request.client.host if request.client else None,
        "status_code": response.status_code,
    }
    print("[HTTP Timing] " + json.dumps(response_info, ensure_ascii=False, default=str))
    return response


def _legacy_error_response(exc: Exception):
    if isinstance(exc, AppError):
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    app_exc = server_error(exc)
    return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)


async def _get_step_names_from_src(project_id: int, procedure=None) -> list[str]:
    try:
        payload = await list_src_steps(str(project_id))
    except Exception:
        return []
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    if procedure is None:
        return [
            str(item.get("step_name"))
            for item in rows
            if isinstance(item, dict) and item.get("step_name") is not None
        ]
    else:
        return [
            str(item.get("step_name"))
            for item in rows
            if isinstance(item, dict) and item.get("step_name") is not None and item.get("procedure") == procedure
        ]


@app.post("/match/dofs")
async def match_dofs_api(request: Request):
    try:
        body = await request.json()
        result = match_test_dofs(
            project_id=int(body["project_id"]),
            overwrite=bool(body.get("overwrite", True)),
            min_match_score=body.get("min_match_score"),
        )
        return success_response(result, "自由度匹配成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/match/dofs/query")
async def get_dofs_api(request: Request):
    try:
        body = await request.json()
        result = get_dof_matches(int(body["project_id"]))
        return success_response(result, "自由度匹配结果查询成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/catalog/response/build")
async def build_response_catalog_api(request: Request):
    try:
        body = await request.json()
        result = build_fe_response_catalog(
            project_id=int(body["project_id"]),
            overwrite=bool(body.get("overwrite", True)),
            include_test_modes=bool(body.get("include_test_modes", True)),
            include_node_dofs=bool(body.get("include_node_dofs", True)),
        )
        return success_response(result, "响应目录构建成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/catalog/response")
async def get_response_catalog_api(request: Request):
    try:
        body = await request.json()
        result = get_fe_response_catalog(int(body["project_id"]))
        return success_response(result, "响应目录查询成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/import/fem/modal")
async def import_fem_modal_api(request: Request):
    try:
        body = await request.json()
        result = import_fe_modal_results(
            project_id=int(body["project_id"]),
            overwrite=bool(body.get("overwrite", True)),
            file_path=body.get("file_path"),
            modes=body.get("modes"),
        )
        return success_response(result, "有限元模态导入成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/import/fem/modal/query")
async def get_fem_modal_api(request: Request):
    try:
        body = await request.json()
        result = get_fe_modal_results(int(body["project_id"]))
        return success_response(result, "有限元模态查询成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/import/fem/static")
async def import_fem_static_api(request: Request):
    try:
        body = await request.json()
        result = import_fe_static_results(
            project_id=int(body["project_id"]),
            overwrite=bool(body.get("overwrite", True)),
            file_path=body.get("file_path"),
            rows=body.get("rows"),
            load_case_no=int(body.get("load_case_no", 1)),
            instance_name=body.get("instance_name"),
            part_name=body.get("part_name"),
        )
        return success_response(result, "有限元静态结果导入成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/import/fem/static/query")
async def get_fem_static_api(request: Request):
    try:
        body = await request.json()
        result = get_fe_static_results(
            int(body["project_id"]),
            load_case_no=body.get("load_case_no"),
        )
        return success_response(result, "有限元静态结果查询成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal/compute")
async def compute_modal_correlation_api(request: Request):
    try:
        body = await request.json()
        result = compute_modal_correlation(
            project_id=int(body["project_id"]),
            overwrite=bool(body.get("overwrite", True)),
            mac_threshold=body.get("mac_threshold", 70),
        )
        return success_response(result, "模态相关性计算成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal")
async def get_modal_correlation_api(request: Request):
    try:
        body = await request.json()
        result = get_modal_correlation_matrix_payload(int(body["project_id"]))
        return success_response(result, "模态相关矩阵查询成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal/matrix")
async def get_modal_correlation_matrix_api(request: Request):
    try:
        body = await request.json()
        result = get_modal_correlation_matrix_payload(int(body["project_id"]))
        return success_response(result, "模态相关矩阵查询成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal/table")
async def get_modal_correlation_table_api(request: Request):
    try:
        body = await request.json()
        result = get_modal_correlation_table_payload(int(body["project_id"]))
        return success_response(result, "模态相关表格查询成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal/msf/table")
async def get_modal_scale_factor_table_api(request: Request):
    try:
        body = await request.json()
        result = get_modal_scale_factor_table_payload(int(body["project_id"]))
        return success_response(result, "MSF琛ㄦ牸鏌ヨ鎴愬姛")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal/match/preview")
async def preview_modal_match_api(request: Request):
    try:
        body = await request.json()
        result = preview_modal_match(
            int(body["project_id"]),
            mac_threshold=float(body.get("mac_threshold", 0.7)),
            max_candidates_per_mode=int(body.get("max_candidates_per_mode", 3)),
            max_freq_error_ratio=(
                None if body.get("max_freq_error_ratio") is None
                else float(body.get("max_freq_error_ratio"))
            ),
        )
        return success_response(result, "模态匹配预览成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal/match")
async def match_modal_api(request: Request):
    try:
        body = await request.json()
        subcase_name = await _get_step_names_from_src(int(body["project_id"]), "FREQUENCY")
        result = match_modal_modes(
            int(body["project_id"]),
            mac_threshold=float(body.get("mac_threshold", 70)),
            max_freq_error_ratio=(
                None if body.get("max_freq_error_ratio") is None
                else float(body.get("max_freq_error_ratio"))
            ),
            method=str(body.get("method", "greedy")),
            subcase_name=subcase_name
        )
        return success_response(result, "模态匹配成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/modal/match/frequency_scatter")
async def modal_match_frequency_scatter_api(request: Request):
    try:
        body = await request.json()
        subcase_name = await _get_step_names_from_src(int(body["project_id"]))
        result = get_modal_match_frequency_scatter_payload(
            int(body["project_id"]),
            mac_threshold=float(body.get("mac_threshold", 0.7)),
            max_freq_error_ratio=(
                None if body.get("max_freq_error_ratio") is None
                else float(body.get("max_freq_error_ratio"))
            ),
            method=str(body.get("method", "greedy")),
            subcase_name=subcase_name,
        )
        result["step_names"] = subcase_name
        return success_response(result, "模态频率匹配散点图获取成功")
    except Exception as exc:
        return _legacy_error_response(exc)


@app.post("/correlation/static/compute")
async def compute_static_correlation_api(request: Request):
    try:
        body = await request.json()
        result = evaluate_static_correlation(
            project_id=int(body["project_id"]),
            load_case_no=body.get("load_case_no"),
            result_no=body.get("result_no"),
            components=body.get("components"),
            include_rotations=bool(body.get("include_rotations", False)),
        )
        return success_response(result, "静态相关性计算成功")
    except Exception as exc:
        return _legacy_error_response(exc)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=APP_CONFIG["host"],
        port=APP_CONFIG["port"],
        reload=False,
    )
