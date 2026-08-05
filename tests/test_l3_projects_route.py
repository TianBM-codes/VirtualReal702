import os
import sqlite3
from pathlib import Path

import pytest


def _make_projects_app(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
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
    pytest.importorskip("httpx")
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
            """
            SELECT inp_path, source_file, original_inp_path, original_source_file, source_type, geom_status
            FROM projects
            WHERE project_id=?
            """,
            ("proj_json_001",),
        ).fetchone()

    assert row == (
        str(source_file.resolve()),
        "door.inp",
        str(source_file),
        "door.inp",
        "inp",
        "pending",
    )


def test_create_project_renames_local_non_ascii_source_in_place(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app, _data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    original_file = tmp_path / "车门模型.odb"
    original_file.write_text("odb", encoding="utf-8")

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "project_id": "proj_cn_001",
            "source_path": str(original_file),
        },
    )

    assert response.status_code == 201
    with sqlite3.connect(registry_db) as conn:
        row = conn.execute(
            """
            SELECT inp_path, source_file, original_inp_path, original_source_file, source_type
            FROM projects
            WHERE project_id=?
            """,
            ("proj_cn_001",),
        ).fetchone()

    runtime_path = Path(row[0])
    assert row[1].startswith("project_odb_")
    assert row[1].endswith(".odb")
    assert row[2] == str(original_file)
    assert row[3] == "车门模型.odb"
    assert row[4] == "odb"
    assert not original_file.exists()
    assert runtime_path.exists()
    assert runtime_path.parent == tmp_path
    assert runtime_path.name == row[1]
    assert response.json()["data"]["project_id"] == "proj_cn_001"


def test_create_project_accepts_http_source_path(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
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
            """
            SELECT inp_path, source_file, original_inp_path, original_source_file, source_type, geom_status
            FROM projects
            WHERE project_id=?
            """,
            ("proj_url_001",),
        ).fetchone()

    assert row == (
        source_url,
        "local_model.odb",
        source_url,
        "local_model.odb",
        "odb",
        "pending",
    )


def test_create_project_http_non_ascii_name_keeps_original_url_and_stores_runtime_name(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app, _data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    client = TestClient(app)
    source_url = "https://example.com/files/%E8%BD%A6%E9%97%A8%E6%A8%A1%E5%9E%8B.odb?token=abc"

    response = client.post(
        "/api/projects",
        json={
            "project_id": "proj_url_cn_001",
            "source_path": source_url,
        },
    )

    assert response.status_code == 201
    with sqlite3.connect(registry_db) as conn:
        row = conn.execute(
            """
            SELECT inp_path, source_file, original_inp_path, original_source_file, source_type
            FROM projects
            WHERE project_id=?
            """,
            ("proj_url_cn_001",),
        ).fetchone()

    assert row[0] == source_url
    assert row[1].startswith("project_odb_")
    assert row[1].endswith(".odb")
    assert row[2] == source_url
    assert row[3] == "车门模型.odb"
    assert row[4] == "odb"


def test_add_result_group_renames_local_non_ascii_source_in_place(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    project_id = "proj_rg_001"
    workspace = data_root / project_id
    workspace.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(registry_db) as conn:
        conn.execute(
            """
            INSERT INTO projects(project_id, workspace, inp_path, source_file, original_inp_path,
                                 original_source_file, source_type, geom_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                project_id,
                project_id,
                str(tmp_path / "door.inp"),
                "door.inp",
                str(tmp_path / "door.inp"),
                "door.inp",
                "inp",
                "ready",
            ),
        )

    result_file = tmp_path / "结果工况.odb"
    result_file.write_text("odb", encoding="utf-8")

    client = TestClient(app)
    response = client.post(
        f"/api/projects/{project_id}/results",
        json={
            "source_path": str(result_file),
            "result_group": "case01",
        },
    )

    assert response.status_code == 201
    with sqlite3.connect(registry_db) as conn:
        row = conn.execute(
            """
            SELECT source_path, source_file, original_source_path, original_source_file, status
            FROM result_groups
            WHERE project_id=? AND result_group=?
            """,
            (project_id, "case01"),
        ).fetchone()

    runtime_path = Path(row[0])
    assert row[1].startswith("result_case01_")
    assert row[1].endswith(".odb")
    assert row[2] == str(result_file)
    assert row[3] == "结果工况.odb"
    assert row[4] == "pending"
    assert not result_file.exists()
    assert runtime_path.exists()
    assert runtime_path.name == row[1]


def test_project_result_catalog_returns_result_group_names_and_manifest_metadata(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    project_id = "proj_catalog_001"
    workspace = data_root / project_id
    workspace.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(registry_db) as conn:
        conn.execute(
            """
            INSERT INTO projects(project_id, workspace, inp_path, source_file, original_inp_path,
                                 original_source_file, source_type, geom_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                project_id,
                project_id,
                "D:/runtime/project_odb_abcd1234.odb",
                "project_odb_abcd1234.odb",
                "D:/demo/车门模型.odb",
                "车门模型.odb",
                "inp",
                "ready",
            ),
        )
        conn.execute(
            """
            INSERT INTO result_groups(project_id, result_group, display_name, source_path, source_file,
                                      original_source_path, original_source_file,
                                      status, parse_options, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', NULL, datetime('now'), datetime('now'))
            """,
            (
                project_id,
                "sol200_all_elements_e",
                "SOL200 sensitivity",
                "D:/runtime/result_sol200_efgh5678.op2",
                "result_sol200_efgh5678.op2",
                "D:/demo/结果模型.op2",
                "结果模型.op2",
            ),
        )

    manifest_db = workspace / "manifest.db"
    with sqlite3.connect(manifest_db) as conn:
        conn.execute(
            """
            CREATE TABLE instances (
                instance_name TEXT PRIMARY KEY,
                part_name TEXT,
                geom_path TEXT,
                highorder_path TEXT,
                node_count INTEGER,
                elem_count INTEGER,
                bbox_min TEXT,
                bbox_max TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE steps (
                result_group TEXT,
                step_name TEXT,
                step_number INTEGER,
                procedure TEXT,
                num_frames INTEGER,
                description TEXT,
                nlgeom INTEGER,
                PRIMARY KEY (result_group, step_name)
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
            CREATE TABLE result_blocks (
                result_group TEXT,
                step_name TEXT,
                field_name TEXT,
                instance_name TEXT,
                position TEXT,
                elem_type TEXT,
                h5_path TEXT,
                label_path TEXT,
                n_entities INTEGER,
                n_ip INTEGER,
                n_sp INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO instances(instance_name, part_name, geom_path, highorder_path, node_count, elem_count, bbox_min, bbox_max)
            VALUES ('PART-1-1', 'PART-1', 'l1/geometry/PART-1-1.h5', NULL, 10, 5, NULL, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO steps(result_group, step_name, step_number, procedure, num_frames, description, nlgeom)
            VALUES ('sol200_all_elements_e', 'Sensitivity', 0, 'STATIC', 2, 'Sensitivity Step', 0)
            """
        )
        conn.execute(
            """
            INSERT INTO result_files(result_group, step_name, field_name, file_path, components, invariants,
                                     positions, has_section, val_min, val_max, source)
            VALUES ('sol200_all_elements_e', 'Sensitivity', 'SENSITIVITY_CLOUD',
                    'l1/results/sol200_all_elements_e/external__Sensitivity__SENSITIVITY_CLOUD.h5',
                    '[\"SENSITIVITY\"]', '[]', '[\"ELEMENT_NODAL\"]', 0, NULL, NULL, 'external')
            """
        )
        conn.execute(
            """
            INSERT INTO result_blocks(result_group, step_name, field_name, instance_name, position, elem_type,
                                      h5_path, label_path, n_entities, n_ip, n_sp)
            VALUES ('sol200_all_elements_e', 'Sensitivity', 'SENSITIVITY_CLOUD',
                    'PART-1-1', 'ELEMENT_NODAL', 'CQUAD4', '/ELEMENT_NODAL/PART-1-1/CQUAD4', NULL, 5, 1, 1)
            """
        )

    client = TestClient(app)
    response = client.get(f"/api/projects/{project_id}/result-catalog")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["project_id"] == project_id
    assert data["geom_status"] == "ready"
    assert len(data["result_groups"]) == 1
    result_group = data["result_groups"][0]
    assert result_group["result_group"] == "sol200_all_elements_e"
    assert result_group["display_name"] == "SOL200 sensitivity"
    assert result_group["source_file"] == "结果模型.op2"
    assert result_group["source_path"] == "D:/demo/结果模型.op2"
    assert result_group["instances"] == [{"instance_name": "PART-1-1", "part_name": "PART-1"}]
    assert result_group["steps"][0]["step_name"] == "Sensitivity"
    assert result_group["steps"][0]["fields"][0]["field_name"] == "SENSITIVITY_CLOUD"
    assert result_group["fields"][0]["instances"] == ["PART-1-1"]


def test_delete_project_purges_mysql_and_workspace(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    from src.l3.api.routes import projects

    project_id = "254"
    workspace = data_root / project_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "manifest.db").write_text("", encoding="utf-8")

    with sqlite3.connect(registry_db) as conn:
        conn.execute(
            """
            INSERT INTO projects(project_id, workspace, inp_path, source_type, geom_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (project_id, project_id, str(tmp_path / "model.inp"), "inp", "ready"),
        )

    purge_calls = []

    def _fake_purge(pid: str):
        purge_calls.append(pid)
        return {
            "attempted": True,
            "project_id": pid,
            "deleted_rows": 12,
            "numeric_project_id": int(pid),
        }

    monkeypatch.setattr(projects, "_purge_project_mysql_records", _fake_purge)

    client = TestClient(app)
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert purge_calls == [project_id]
    payload = response.json()["data"]
    assert payload["project_id"] == project_id
    assert payload["deleted"] is True
    assert payload["orphan_workspace_deleted"] is False
    assert payload["mysql_purged"]["deleted_rows"] == 12
    assert not workspace.exists()

    with sqlite3.connect(registry_db) as conn:
        row = conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?",
            (project_id,),
        ).fetchone()

    assert row is None


def test_delete_project_rejects_active_project(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    from src.l3.api.routes import projects

    project_id = "255"
    workspace = data_root / project_id
    workspace.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(registry_db) as conn:
        conn.execute(
            """
            INSERT INTO projects(project_id, workspace, inp_path, source_type, geom_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (project_id, project_id, str(tmp_path / "active.inp"), "inp", "pending"),
        )

    purge_calls = []

    def _fake_purge(pid: str):
        purge_calls.append(pid)
        return {"attempted": True, "project_id": pid, "deleted_rows": 0, "numeric_project_id": int(pid)}

    monkeypatch.setattr(projects, "_purge_project_mysql_records", _fake_purge)

    client = TestClient(app)
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert purge_calls == []
    assert workspace.exists()


def test_purge_project_mysql_records_deletes_frf_tables(monkeypatch):
    from src.l3.api.routes import projects

    executed = []

    class FakeCursor:
        def __init__(self):
            self.rowcount = 1

        def execute(self, sql, params=None):
            executed.append((" ".join(str(sql).split()), params))
            self.rowcount = 1

        def close(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.committed = False
            self.rolled_back = False

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            return None

    fake_conn = FakeConnection()

    monkeypatch.setattr(projects, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(projects, "clear_unv_tables", lambda cursor, pid: executed.append(("clear_unv_tables", pid)))
    monkeypatch.setattr(projects, "clear_fem_tables", lambda cursor, pid: executed.append(("clear_fem_tables", pid)))

    result = projects._purge_project_mysql_records("254")

    delete_statements = [sql for sql, _params in executed if isinstance(sql, str) and sql.startswith("DELETE FROM")]
    assert any("DELETE FROM t_mt_py_test_frf_point WHERE pid = %s" == sql for sql in delete_statements)
    assert any("DELETE FROM t_mt_py_test_frf_curve WHERE pid = %s" == sql for sql in delete_statements)
    assert delete_statements.index("DELETE FROM t_mt_py_test_frf_point WHERE pid = %s") < delete_statements.index(
        "DELETE FROM t_mt_py_test_frf_curve WHERE pid = %s"
    )
    assert result["attempted"] is True
    assert result["numeric_project_id"] == 254
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
