import pytest


def test_pbs_abaqus_run_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import solver

    captured = {}

    def fake_run_pbs_solver_job(**kwargs):
        captured.update(kwargs)
        return {
            "workflow": "pbs_abaqus_run",
            "job_id": "12345",
            "resolved_job_state": "C",
        }

    monkeypatch.setattr(solver, "run_pbs_solver_job", fake_run_pbs_solver_job)

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/solver/pbs/abaqus/run",
        json={
            "env": "dev",
            "input_file": "D:/demo/model.inp",
            "job_name": "demo_job",
            "wait": True,
            "download_results": False,
            "submit_overrides": {"CORES": 32},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["code"] == 200
    assert payload["data"]["job_id"] == "12345"
    assert captured["application"] == "Abaqus"
    assert captured["env"] == "dev"
    assert captured["submit_overrides"] == {"CORES": 32}


def test_pbs_job_status_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import solver

    def fake_get_pbs_job_status(**kwargs):
        return {
            "env": kwargs["env"],
            "job_id": kwargs["job_id"],
            "resolved_job_state": "R",
        }

    monkeypatch.setattr(solver, "get_pbs_job_status", fake_get_pbs_job_status)

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/solver/pbs/job/status",
        json={
            "env": "prod",
            "job_id": "job-001",
            "timeout_sec": 30,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["code"] == 200
    assert payload["data"] == {
        "env": "prod",
        "job_id": "job-001",
        "resolved_job_state": "R",
    }
