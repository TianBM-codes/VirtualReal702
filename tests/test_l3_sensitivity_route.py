import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_sensitivity_app(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI

    from src.l3.api.routes import sensitivity

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
                ("sensitivity_batch_5_demo_123",),
                ("sensitivity_5_U",),
                ("sol200_all_elements_e",),
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
                ("sensitivity_batch_5_demo_123", "Step-1", "d_4_U1_T", "external"),
                ("sensitivity_5_U", "Step-1", "d_4_U1_T", "external"),
                ("sol200_all_elements_e", "Sensitivity", "SENSITIVITY_CLOUD", "external"),
                ("plain_result", "Step-1", "U", "odb"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO frames(result_group, step_name, frame_idx, frame_value, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("sol200_all_elements_e", "Sensitivity", 0, 1.0, "FREQ1"),
                ("sol200_all_elements_e", "Sensitivity", 1, 2.0, "FREQ2"),
            ],
        )

    monkeypatch.setattr(
        sensitivity.registry,
        "get",
        lambda odb_id: SimpleNamespace(workspace=str(workspace)),
    )

    app = FastAPI()
    app.include_router(sensitivity.router)
    return app


def test_sensitivity_result_groups_include_external_cloud_groups(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = _make_sensitivity_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/odb/demo/sensitivity/result_groups")

    assert response.status_code == 200
    assert response.json()["data"]["result_groups"] == [
        "sensitivity_5_U",
        "sol200_all_elements_e",
    ]


def test_sensitivity_result_groups_merge_only_false_keeps_raw_groups(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = _make_sensitivity_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/odb/demo/sensitivity/result_groups?merge_only=false")

    assert response.status_code == 200
    assert response.json()["data"]["result_groups"] == [
        "sensitivity_batch_5_demo_123",
        "sensitivity_5_U",
        "sol200_all_elements_e",
    ]


def test_sensitivity_fields_expand_external_cloud_frames(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app = _make_sensitivity_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get(
        "/api/odb/demo/sensitivity/fields",
        params={"result_group": "sol200_all_elements_e", "step": "Sensitivity"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["fields"] == [
        {
            "field_name": "SENSITIVITY_CLOUD__FRAME_0000",
            "source_field_name": "SENSITIVITY_CLOUD",
            "step": "Sensitivity",
            "frame_idx": 0,
            "frame_value": 1.0,
            "frame_description": "FREQ1",
            "response_node_label": 1,
            "component": "FREQ1",
        },
        {
            "field_name": "SENSITIVITY_CLOUD__FRAME_0001",
            "source_field_name": "SENSITIVITY_CLOUD",
            "step": "Sensitivity",
            "frame_idx": 1,
            "frame_value": 2.0,
            "frame_description": "FREQ2",
            "response_node_label": 2,
            "component": "FREQ2",
        },
    ]
