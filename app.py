from config import APP_CONFIG
from fastapi import Request
from src.l3.main import app

from webapi.routes import router as model_update_router
from services.model_update.analysis.inp_service import (
    build_fe_response_catalog,
    compute_modal_correlation,
    get_dof_matches,
    get_fe_modal_results,
    get_fe_response_catalog,
    get_fe_static_results,
    get_modal_correlation,
    import_fe_modal_results,
    import_fe_static_results,
    match_test_dofs,
)

app.include_router(model_update_router)


@app.post("/match/dofs")
async def match_dofs_api(request: Request):
    body = await request.json()
    result = match_test_dofs(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
    )
    return {"ok": True, "message": "dof match success", "data": result}


@app.post("/match/dofs/query")
async def get_dofs_api(request: Request):
    body = await request.json()
    result = get_dof_matches(int(body["project_id"]))
    return {"ok": True, "message": "dof match query success", "data": result}


@app.post("/catalog/response/build")
async def build_response_catalog_api(request: Request):
    body = await request.json()
    result = build_fe_response_catalog(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
        include_test_modes=bool(body.get("include_test_modes", True)),
        include_node_dofs=bool(body.get("include_node_dofs", True)),
    )
    return {"ok": True, "message": "response catalog build success", "data": result}


@app.post("/catalog/response")
async def get_response_catalog_api(request: Request):
    body = await request.json()
    result = get_fe_response_catalog(int(body["project_id"]))
    return {"ok": True, "message": "response catalog query success", "data": result}


@app.post("/import/fem/modal")
async def import_fem_modal_api(request: Request):
    body = await request.json()
    result = import_fe_modal_results(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
        file_path=body.get("file_path"),
        modes=body.get("modes"),
    )
    return {"ok": True, "message": "fem modal import success", "data": result}


@app.post("/import/fem/modal/query")
async def get_fem_modal_api(request: Request):
    body = await request.json()
    result = get_fe_modal_results(int(body["project_id"]))
    return {"ok": True, "message": "fem modal query success", "data": result}


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
    return {"ok": True, "message": "fem static import success", "data": result}


@app.post("/import/fem/static/query")
async def get_fem_static_api(request: Request):
    body = await request.json()
    result = get_fe_static_results(
        int(body["project_id"]),
        load_case_no=body.get("load_case_no"),
    )
    return {"ok": True, "message": "fem static query success", "data": result}


@app.post("/correlation/modal/compute")
async def compute_modal_correlation_api(request: Request):
    body = await request.json()
    result = compute_modal_correlation(
        project_id=int(body["project_id"]),
        overwrite=bool(body.get("overwrite", True)),
    )
    return {"ok": True, "message": "modal correlation success", "data": result}


@app.post("/correlation/modal")
async def get_modal_correlation_api(request: Request):
    body = await request.json()
    result = get_modal_correlation(int(body["project_id"]))
    return {"ok": True, "message": "modal correlation query success", "data": result}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=APP_CONFIG["host"],
        port=APP_CONFIG["port"],
        reload=False,
    )
