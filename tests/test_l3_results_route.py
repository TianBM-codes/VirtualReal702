import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_results_app(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI

    from src.l3.api.routes import results

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "manifest.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE result_files (
                result_group TEXT,
                step_name TEXT,
                field_name TEXT,
                file_path TEXT,
                components TEXT,
                invariants TEXT,
                positions TEXT,
                has_section INTEGER,
                val_min REAL,
                val_max REAL,
                source TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO result_files(
                result_group, step_name, field_name, file_path,
                components, invariants, positions, has_section,
                val_min, val_max, source
            )
            VALUES (?, ?, ?, '', '[]', '[]', '[]', 0, NULL, NULL, ?)
            """,
            [
                ("bayesian_sol200", "BayesianUpdate", "PARAMETER_RELATIVE_DELTA_PERCENT", "external"),
                ("sol200_all_elements_e", "Sensitivity", "SENSITIVITY_CLOUD", "external"),
            ],
        )

    monkeypatch.setattr(
        results.registry,
        "get",
        lambda odb_id: SimpleNamespace(workspace=str(workspace), is_render_ready=True),
    )

    captured = {}

    def fake_compute_scalar_range(**kwargs):
        captured["kwargs"] = kwargs
        return (1.0, 2.0)

    monkeypatch.setattr(results, "compute_scalar_range", fake_compute_scalar_range)

    app = FastAPI()
    app.include_router(results.router)
    return app, captured


def test_frame_scalar_range_resolves_model_update_step_alias(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app, captured = _make_results_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get(
        "/api/odb/demo/results/frame-scalar-range",
        params={
            "instances": "PART-1-1",
            "step": "Bayesian-Update",
            "field": "PARAMETER_RELATIVE_DELTA_PERCENT",
            "result_group": "bayesian_sol200",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["global_min"] == 1.0
    assert captured["kwargs"]["step"] == "BayesianUpdate"
    assert captured["kwargs"]["result_group"] == "bayesian_sol200"


def test_frame_scalar_range_discovers_result_group_from_frame_alias(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app, captured = _make_results_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get(
        "/api/odb/demo/results/frame-scalar-range",
        params={
            "instances": "PART-1-1",
            "step": "Sensitivity",
            "field": "SENSITIVITY_CLOUD__FRAME_0000",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["global_max"] == 2.0
    assert captured["kwargs"]["step"] == "Sensitivity"
    assert captured["kwargs"]["result_group"] == "sol200_all_elements_e"
    assert captured["kwargs"]["field"] == "SENSITIVITY_CLOUD__FRAME_0000"
