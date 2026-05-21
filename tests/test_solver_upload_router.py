from pathlib import Path

import pytest


def test_upload_and_run_abaqus_inp_route_saves_uploaded_file(monkeypatch, tmp_path: Path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import solver

    captured = {}

    def fake_run_abaqus_job(**kwargs):
        captured.update(kwargs)
        inp_path = Path(kwargs["input_inp"])
        return {
            "workflow": "abaqus_inp",
            "input_inp": str(inp_path),
            "output_dir": str(kwargs["output_dir"]),
            "job_name": kwargs.get("job_name") or inp_path.stem,
            "generated_files": {"analysis_inp": str(inp_path)},
            "solver": {
                "ok": True,
                "artifacts": {"odb": str(inp_path.with_suffix(".odb"))},
            },
        }

    monkeypatch.setattr(solver, "run_abaqus_job", fake_run_abaqus_job)
    monkeypatch.setattr(solver.settings, "data_root", str(tmp_path))

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/solver/abaqus/inp/upload_and_run",
        params={
            "filename": "demo model.inp",
            "job_name": "remote_job",
            "interactive": "true",
            "cpus": "4",
            "extra_args_json": '["memory=8gb"]',
        },
        content=b"*Heading\n*Step\n*End Step\n",
        headers={"content-type": "application/octet-stream"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["code"] == 200
    assert payload["data"]["uploaded_inp"]["original_filename"] == "demo model.inp"
    assert payload["data"]["odb_path"].endswith(".odb")

    saved_inp = Path(captured["input_inp"])
    assert saved_inp.exists()
    assert saved_inp.read_text(encoding="utf-8") == "*Heading\n*Step\n*End Step\n"
    assert captured["job_name"] == "remote_job"
    assert captured["cpus"] == 4
    assert captured["interactive"] is True
    assert captured["extra_args"] == ["memory=8gb"]


def test_upload_and_run_abaqus_inp_route_accepts_json_server_path(monkeypatch, tmp_path: Path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import solver

    captured = {}
    source_inp = tmp_path / "1111.inp"
    source_inp.write_text("*Heading\n*Step\n*End Step\n", encoding="utf-8")

    def fake_run_abaqus_job(**kwargs):
        captured.update(kwargs)
        inp_path = Path(kwargs["input_inp"])
        return {
            "workflow": "abaqus_inp",
            "input_inp": str(inp_path),
            "output_dir": str(kwargs["output_dir"]),
            "job_name": kwargs.get("job_name") or inp_path.stem,
            "generated_files": {"analysis_inp": str(inp_path)},
            "solver": {
                "ok": True,
                "artifacts": {"odb": str((Path(kwargs["output_dir"]) / "demo_job.odb").resolve())},
            },
        }

    monkeypatch.setattr(solver, "run_abaqus_job", fake_run_abaqus_job)

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/solver/abaqus/inp/upload_and_run",
        json={
            "project_id": 1001,
            "file_name": str(source_inp),
            "output_dir": str(tmp_path / "abaqus_out"),
            "job_name": "demo_job",
            "cpus": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["code"] == 200
    assert payload["data"]["source_inp"]["mode"] == "server_path"
    assert payload["data"]["source_inp"]["path"] == str(source_inp.resolve())
    assert payload["data"]["uploaded_inp"] is None
    assert captured["input_inp"] == str(source_inp)
    assert captured["job_name"] == "demo_job"
    assert captured["cpus"] == 2


def test_solver_run_and_parse_route_for_inp(monkeypatch, tmp_path: Path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import solver

    captured = {}
    monkeypatch.setattr(
        solver,
        "run_solver_and_parse_project_result",
        lambda **kwargs: captured.update(kwargs) or {
            "workflow": "run_and_parse",
            "project_id": kwargs["project_id"],
            "source_type": "inp",
            "solver_type": "abaqus",
            "result_group": "case_a_result",
            "result_group_status": "ready",
        },
    )

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/solver/run_and_parse",
        json={
            "project_id": 1001,
            "input_file": "case_a.inp",
            "job_name": "case_a",
            "result_group": "case_a_result",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["source_type"] == "inp"
    assert captured["project_id"] == 1001
    assert captured["input_file"] == "case_a.inp"


def test_solver_run_and_parse_route_for_bdf_async(monkeypatch, tmp_path: Path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import solver

    monkeypatch.setattr(
        solver,
        "submit_background_task",
        lambda **kwargs: {
            "task_id": "task-1",
            "status": "submitted",
            "task_type": kwargs["task_type"],
            "request": kwargs["request_payload"],
        },
    )

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/solver/run_and_parse",
        json={
            "project_id": 1002,
            "input_file": "case_b.bdf",
            "async_submit": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["task_type"] == "solver.run_and_parse"
    assert payload["data"]["request"]["project_id"] == 1002
