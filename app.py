import json
import time
from datetime import datetime

from config import APP_CONFIG
from db import initialize_database_runtime
from fastapi import Request
from src.l3.main import app

from webapi.routes import router as model_update_router
from webapi.common import success_response
from src.modal_service.routes import router as modal_router
from services.model_update.analysis.inp_service import (
    build_fe_response_catalog,
    compute_modal_correlation,
    evaluate_static_correlation,
    get_dof_matches,
    get_fe_modal_results,
    get_fe_response_catalog,
    get_fe_static_results,
    get_modal_correlation,
    get_modal_correlation_matrix_payload,
    get_modal_correlation_table_payload,
    import_fe_modal_results,
    import_fe_static_results,
    match_modal_modes,
    match_test_dofs,
    preview_modal_match,
)

app.include_router(model_update_router)
app.include_router(modal_router)


@app.on_event("startup")
async def initialize_model_update_runtime() -> None:
    # Warm the DB pool before the first frontend request arrives. This moves
    # the one-time handshake cost out of endpoints such as /get/sensor_position.
    initialize_database_runtime(ensure_tables=True, warm_connection=True)


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


@app.post("/match/dofs")
async def match_dofs_api(request: Request):
    body = await request.json()
    result = match_test_dofs(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
        min_match_score=body.get("min_match_score"),
    )
    return success_response(result, "dof match success")


@app.post("/match/dofs/query")
async def get_dofs_api(request: Request):
    body = await request.json()
    result = get_dof_matches(int(body["project_id"]))
    return success_response(result, "dof match query success")


@app.post("/catalog/response/build")
async def build_response_catalog_api(request: Request):
    body = await request.json()
    result = build_fe_response_catalog(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
        include_test_modes=bool(body.get("include_test_modes", True)),
        include_node_dofs=bool(body.get("include_node_dofs", True)),
    )
    return success_response(result, "response catalog build success")


@app.post("/catalog/response")
async def get_response_catalog_api(request: Request):
    body = await request.json()
    result = get_fe_response_catalog(int(body["project_id"]))
    return success_response(result, "response catalog query success")


@app.post("/import/fem/modal")
async def import_fem_modal_api(request: Request):
    body = await request.json()
    result = import_fe_modal_results(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
        file_path=body.get("file_path"),
        modes=body.get("modes"),
    )
    return success_response(result, "fem modal import success")


@app.post("/import/fem/modal/query")
async def get_fem_modal_api(request: Request):
    body = await request.json()
    result = get_fe_modal_results(int(body["project_id"]))
    return success_response(result, "fem modal query success")


@app.post("/import/fem/static")
async def import_fem_static_api(request: Request):
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
    return success_response(result, "fem static import success")


@app.post("/import/fem/static/query")
async def get_fem_static_api(request: Request):
    body = await request.json()
    result = get_fe_static_results(
        int(body["project_id"]),
        load_case_no=body.get("load_case_no"),
    )
    return success_response(result, "fem static query success")


@app.post("/correlation/modal/compute")
async def compute_modal_correlation_api(request: Request):
    body = await request.json()
    result = compute_modal_correlation(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
    )
    return success_response(result, "modal correlation success")


@app.post("/correlation/modal")
async def get_modal_correlation_api(request: Request):
    body = await request.json()
    result = get_modal_correlation_matrix_payload(int(body["project_id"]))
    return success_response(result, "modal correlation matrix query success")


@app.post("/correlation/modal/matrix")
async def get_modal_correlation_matrix_api(request: Request):
    body = await request.json()
    result = get_modal_correlation_matrix_payload(int(body["project_id"]))
    return success_response(result, "modal correlation matrix query success")


@app.post("/correlation/modal/table")
async def get_modal_correlation_table_api(request: Request):
    body = await request.json()
    result = get_modal_correlation_table_payload(int(body["project_id"]))
    return success_response(result, "modal correlation table query success")


@app.post("/correlation/modal/match/preview")
async def preview_modal_match_api(request: Request):
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
    return {"ok": True, "message": "modal match preview success", "data": result}


@app.post("/correlation/modal/match")
async def match_modal_api(request: Request):
    body = await request.json()
    result = match_modal_modes(
        int(body["project_id"]),
        mac_threshold=float(body.get("mac_threshold", 0.7)),
        max_freq_error_ratio=(
            None if body.get("max_freq_error_ratio") is None
            else float(body.get("max_freq_error_ratio"))
        ),
        method=str(body.get("method", "greedy")),
    )
    return {"ok": True, "message": "modal match success", "data": result}


@app.post("/correlation/static/compute")
async def compute_static_correlation_api(request: Request):
    body = await request.json()
    result = evaluate_static_correlation(
        project_id=int(body["project_id"]),
        load_case_no=body.get("load_case_no"),
        result_no=body.get("result_no"),
        components=body.get("components"),
        include_rotations=bool(body.get("include_rotations", False)),
    )
    return success_response(result, "static correlation success")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=APP_CONFIG["host"],
        port=APP_CONFIG["port"],
        reload=False,
    )
