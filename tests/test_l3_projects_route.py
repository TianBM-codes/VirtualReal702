import sqlite3
from pathlib import Path

import pytest


def _make_projects_app(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    from src.l3.api.routes import projects
    from src.l3.core.errors import AppError
    from src.l3.core.exception_handlers import (
        app_error_handler,
        request_validation_error_handler,
        unhandled_error_handler,
    )

    data_root = tmp_path / "model"
    registry_db = data_root / "registry.db"
    data_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(projects.settings, "data_root", str(data_root))
    monkeypatch.setattr(projects.settings, "registry_db_path", str(registry_db))

    app = FastAPI()
    app.include_router(projects.router)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    return app, data_root, registry_db


def test_create_project_accepts_json_local_path(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    source_file = tmp_path / "door.inp"
    source_file.write_text("*Heading\n", encoding="utf-8")

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "project_id": "proj_json_001",
            "local_path": str(source_file),
        },
    )

    assert response.status_code == 201
    assert response.json()["data"] == {
        "project_id": "proj_json_001",
        "source_type": "inp",
        "geom_status": "pending",
    }
    assert (data_root / "proj_json_001").is_dir()

    with sqlite3.connect(registry_db) as conn:
        row = conn.execute(
            "SELECT inp_path, source_type, geom_status FROM projects WHERE project_id=?",
            ("proj_json_001",),
        ).fetchone()

    assert row == (str(source_file), "inp", "pending")


def test_create_project_accepts_http_source_path(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    client = TestClient(app)
    source_url = "http://example.com/files/local_model.odb"

    response = client.post(
        "/api/projects",
        json={
            "project_id": "proj_url_001",
            "source_path": source_url,
        },
    )

    assert response.status_code == 201
    assert response.json()["data"] == {
        "project_id": "proj_url_001",
        "source_type": "odb",
        "geom_status": "pending",
    }
    assert (data_root / "proj_url_001").is_dir()

    with sqlite3.connect(registry_db) as conn:
        row = conn.execute(
            "SELECT inp_path, source_type, geom_status FROM projects WHERE project_id=?",
            ("proj_url_001",),
        ).fetchone()

    assert row == (source_url, "odb", "pending")
