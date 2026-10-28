"""POST /api/projects/{project_id}/results/{result_group}/reparse + runner 清理根目录结果文件。"""
import json
import sqlite3
from pathlib import Path

import pytest

from tests.test_l3_projects_route import _make_projects_app


def _seed(registry_db: Path, data_root: Path, tmp_path: Path, *, rg_status="ready",
          source_name="model.odb", parse_options=None, external=0):
    project_id = "proj_reparse"
    workspace = data_root / project_id
    workspace.mkdir(parents=True, exist_ok=True)
    src = tmp_path / source_name
    src.write_bytes(b"odb")
    with sqlite3.connect(registry_db) as conn:
        conn.execute(
            "INSERT INTO projects(project_id, workspace, inp_path, source_file, original_inp_path,"
            " original_source_file, source_type, geom_status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'odb', 'ready', datetime('now'), datetime('now'))",
            (project_id, project_id, str(src), source_name, str(src), source_name),
        )
        conn.execute(
            "INSERT INTO result_groups(project_id, result_group, display_name, source_path, source_file,"
            " original_source_path, original_source_file, status, parse_options, created_at, updated_at)"
            " VALUES (?, 'default_result', 'model', ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (project_id, str(src), source_name, str(src), source_name, rg_status,
             json.dumps(parse_options) if parse_options else None),
        )
    with sqlite3.connect(workspace / "manifest.db") as conn:
        conn.execute(
            "CREATE TABLE result_files (result_group TEXT, step_name TEXT, field_name TEXT,"
            " file_path TEXT, source TEXT NOT NULL DEFAULT 'odb')")
        for i in range(external):
            conn.execute(
                "INSERT INTO result_files VALUES ('default_result', 'Step-1', ?, 'x.h5', 'external')",
                ("EXT{}".format(i),))
    return project_id


def _rg_row(registry_db, project_id):
    with sqlite3.connect(registry_db) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM result_groups WHERE project_id=? AND result_group='default_result'",
            (project_id,)).fetchone()


def test_reparse_requeues_and_merges_parse_options(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    pid = _seed(registry_db, data_root, tmp_path,
                parse_options={"steps": ["Step-1"], "frames": "all", "invariants": "full"})

    client = TestClient(app)
    resp = client.post(f"/api/projects/{pid}/results/default_result/reparse",
                       json={"parse_options": {"fields": ["U", "S"], "invariants": None}})

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["status"] == "pending"
    assert data["parse_options"] == {"steps": ["Step-1"], "frames": "all", "fields": ["U", "S"]}
    row = _rg_row(registry_db, pid)
    assert row["status"] == "pending"
    assert json.loads(row["parse_options"]) == data["parse_options"]


def test_reparse_without_body(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    pid = _seed(registry_db, data_root, tmp_path)
    resp = TestClient(app).post(f"/api/projects/{pid}/results/default_result/reparse")
    assert resp.status_code == 202
    assert _rg_row(registry_db, pid)["status"] == "pending"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_reparse_rejects_busy_group(monkeypatch, tmp_path, status):
    from fastapi.testclient import TestClient
    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    pid = _seed(registry_db, data_root, tmp_path, rg_status=status)
    resp = TestClient(app).post(f"/api/projects/{pid}/results/default_result/reparse")
    assert resp.status_code == 409


def test_reparse_rejects_non_odb(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    pid = _seed(registry_db, data_root, tmp_path, source_name="model.op2")
    resp = TestClient(app).post(f"/api/projects/{pid}/results/default_result/reparse")
    assert resp.status_code == 400


def test_reparse_requires_drop_external(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    pid = _seed(registry_db, data_root, tmp_path, external=2)
    client = TestClient(app)

    resp = client.post(f"/api/projects/{pid}/results/default_result/reparse")
    assert resp.status_code == 409
    assert _rg_row(registry_db, pid)["status"] == "ready"

    resp = client.post(f"/api/projects/{pid}/results/default_result/reparse",
                       json={"drop_external": True})
    assert resp.status_code == 202
    assert resp.json()["data"]["dropped_external_fields"] == 2


def test_reparse_missing_source(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    app, data_root, registry_db = _make_projects_app(monkeypatch, tmp_path)
    pid = _seed(registry_db, data_root, tmp_path)
    (tmp_path / "model.odb").unlink()
    resp = TestClient(app).post(f"/api/projects/{pid}/results/default_result/reparse")
    assert resp.status_code == 400


def test_cleanup_removes_root_level_default_result_files(tmp_path):
    from src import job_runner

    ws = tmp_path / "ws"
    results = ws / "l1" / "results"
    (results / "other").mkdir(parents=True)
    root_file = results / "Step-1__RF.h5"
    root_file.write_bytes(b"h5")
    other_file = results / "other" / "Step-1__U.h5"
    other_file.write_bytes(b"h5")
    outside = tmp_path / "outside.h5"
    outside.write_bytes(b"h5")
    with sqlite3.connect(ws / "manifest.db") as conn:
        conn.execute("CREATE TABLE result_files (result_group TEXT, file_path TEXT)")
        conn.executemany("INSERT INTO result_files VALUES (?, ?)", [
            ("default_result", "l1\\results\\Step-1__RF.h5"),
            ("default_result", str(outside)),           # 工作区 l1/results 外的不删
            ("other", "l1/results/other/Step-1__U.h5"),  # 别的组不动
        ])
        job_runner._remove_result_group_files(conn, str(ws), "default_result")

    assert not root_file.exists()
    assert outside.exists()
    assert other_file.exists()
