import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_model_update_app(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI

    from src.l3.api.routes import model_update

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "manifest.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE result_group_meta (
                result_group TEXT PRIMARY KEY,
                created_at TEXT
            )
            """
        )
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
        conn.execute(
            """
            CREATE TABLE frames (
                result_group TEXT,
                step_name TEXT,
                frame_idx INTEGER,
                frame_value REAL,
                description TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO result_group_meta(result_group, created_at) VALUES (?, datetime('now'))",
            [
                ("bayesian_sol200",),
                ("sensitivity_5_U",),
                ("plain_result",),
            ],
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
                ("sensitivity_5_U", "Step-1", "d_4_U1_T", "external"),
                ("plain_result", "Step-1", "U", "odb"),
            ],
        )
        conn.execute(
            """
            INSERT INTO frames(result_group, step_name, frame_idx, frame_value, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("bayesian_sol200", "BayesianUpdate", 0, 0.0, "Final"),
        )

    monkeypatch.setattr(
        model_update.registry,
        "get",
        lambda odb_id: SimpleNamespace(workspace=str(workspace)),
    )

    app = FastAPI()
    app.include_router(model_update.router)
    return app


def test_model_update_result_groups(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = _make_model_update_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/odb/demo/model-update/result_groups")

    assert response.status_code == 200
    assert response.json()["data"]["result_groups"] == [
        "bayesian_sol200",
    ]


def test_model_update_fields(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = _make_model_update_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get(
        "/api/odb/demo/model-update/fields",
        params={"result_group": "bayesian_sol200", "step": "BayesianUpdate"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["fields"] == [
        {
            "field_name": "PARAMETER_RELATIVE_DELTA_PERCENT",
            "source_field_name": "PARAMETER_RELATIVE_DELTA_PERCENT",
            "step": "BayesianUpdate",
            "frame_idx": 0,
            "frame_value": 0.0,
            "frame_description": "Final",
        },
    ]


def test_model_update_step_frame_returns_actual_step_value(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = _make_model_update_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/odb/demo/model-update/step_frame")

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "label": "Bayesian-Update",
            "value": "BayesianUpdate",
            "frame": 0,
        }
    ]
