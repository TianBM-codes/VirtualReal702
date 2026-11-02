"""Verify the retained public workflow and the reduced delivery surface."""
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def test_retained_workflows_and_removed_integrations():
    from app import app

    paths = {route.path for route in app.routes}
    assert {
        "/solver/run_and_parse",
        "/sensitivity/run_and_store",
        "/pair/node_point",
        "/correlation/modal/match",
        "/api/odb/{odb_id}/results/deformed-positions",
    } <= paths
    assert any(path.startswith("/optimization/") for path in paths)
    assert not any("/solver/pbs/" in path for path in paths)
    assert "/applications/3dlite/api/v1/query" not in paths
    assert "/get/simright_test_load_level_comparison" not in paths
    assert "/dump/json" not in paths
    assert "/frf/curve" not in paths


def test_timing_middleware_preserves_body_without_logging_it(capsys):
    from app import log_request_timing

    probe = FastAPI()
    probe.middleware("http")(log_request_timing)

    @probe.post("/echo")
    async def echo(request: Request):
        return await request.json()

    response = TestClient(probe).post(
        "/echo?private_query=private-value", json={"private_model": "private-value"}
    )
    assert response.status_code == 200
    assert response.json() == {"private_model": "private-value"}
    assert "X-Elapsed-Time-Ms" in response.headers
    output = capsys.readouterr().out
    assert "private-value" not in output
    assert "private_model" not in output


def test_route_request_logger_does_not_copy_payload(capsys):
    from webapi.utils import log_request

    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/matching"))
    asyncio.run(log_request(request, {"private_input": "private-value"}))
    assert "private-value" not in capsys.readouterr().out


def test_static_mount_does_not_expose_runtime_sources():
    from app import app
    from pathlib import Path

    mount = next(route for route in app.routes if route.path == "/static")
    assert Path(mount.app.directory).name == "static"
    assert not (Path(mount.app.directory) / "odb_client.py").exists()
