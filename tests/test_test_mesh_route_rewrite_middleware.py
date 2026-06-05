import asyncio
import json

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI, Request

from src.modal_service.middleware import (
    add_test_mesh_route_rewrite_middleware,
    rewrite_test_mesh_path,
)


def test_rewrite_test_mesh_path_maps_proxy_stripped_paths():
    assert rewrite_test_mesh_path("//model/testMesh/modelSelect") == "/api/model/testMesh/modelSelect"
    assert rewrite_test_mesh_path("/model/testMesh/modelSelect") == "/api/model/testMesh/modelSelect"
    assert rewrite_test_mesh_path("/api/model/testMesh/modelSelect") == "/api/model/testMesh/modelSelect"
    assert rewrite_test_mesh_path("/api/projects") == "/api/projects"


def test_rewrite_test_mesh_middleware_routes_double_slash_path():
    app = FastAPI()
    add_test_mesh_route_rewrite_middleware(app)

    @app.post("/api/model/testMesh/modelSelect")
    async def model_select(request: Request):
        return {"path": request.scope["path"]}

    response = asyncio.run(_asgi_json_post(app, "//model/testMesh/modelSelect", {"project_id": 18}))

    assert response["status"] == 200
    assert response["json"] == {"path": "/api/model/testMesh/modelSelect"}


async def _asgi_json_post(app, path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    messages = []
    sent_body = False

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive():
        nonlocal sent_body
        if sent_body:
            return {"type": "http.disconnect"}
        sent_body = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)

    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body_bytes = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return {"status": status, "json": json.loads(body_bytes)}
