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
