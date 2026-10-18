import sqlite3
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import job_runner
from src import model_update_tasks as queue
from src.l3.infra.registry_repo import RegistryRepo


@pytest.fixture
def queued_project(tmp_path):
    db = str(tmp_path / "registry.db")
    repo = RegistryRepo(db)
    bdf = tmp_path / "input.bdf"
    op2 = tmp_path / "result.op2"
    bdf.write_bytes(b"synthetic BDF placeholder; never parsed")
    op2.write_bytes(b"synthetic OP2 placeholder; never parsed")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO projects (project_id,workspace,inp_path,source_type,geom_status,created_at,updated_at) "
            "VALUES ('18',?,?,'bdf','ready','created','created')", (str(tmp_path), str(bdf)),
        )
        conn.execute(
            "INSERT INTO result_groups (project_id,result_group,status,source_path,created_at,updated_at) "
            "VALUES ('18','A','pending',?,'created','created')", (str(op2),),
        )
    queue.ensure_schema(db)
    return db, repo, bdf, op2, tmp_path


def enqueue_bdf(data):
    db, _, bdf, _, workspace = data
    return queue.enqueue_import(db, "18", "bdf", str(workspace), {"bdf_path": str(bdf)})


def test_slow_bdf_import_does_not_block_op2_visualization_and_modal_waits(queued_project, monkeypatch):
    db, repo, bdf, op2, workspace = queued_project
    monkeypatch.setattr(job_runner, "REGISTRY_DB", db)
    monkeypatch.setattr(job_runner, "_run_streaming", lambda *args, **kwargs: (0, ""))
    # 本用例只隔离验证修正导入队列；解析/首屏准备由就绪屏障测试覆盖。
    monkeypatch.setattr(job_runner, "_prepare_registered", lambda *args, **kwargs: None)
    enqueue_bdf(queued_project)
    entered, release = threading.Event(), threading.Event()

    def slow_import(task, progress):
        progress("read_bdf", "读取BDF")
        entered.set()
        assert release.wait(5)

    worker = threading.Thread(target=lambda: queue.run_one(db, slow_import))
    worker.start()
    try:
        assert entered.wait(3)
        # The real runner wrapper returns without waiting for the BDF importer.
        assert job_runner._run_op2_result_group("18", "A", str(op2), str(workspace))
        assert repo.get_result_group("18", "A")["status"] == "ready"
        tasks = queue.list_imports(db, "18")
        assert {t["kind"]: t["status"] for t in tasks} == {"bdf": "running", "op2_modal": "pending"}
        # A second worker cannot run this project's modal import concurrently.
        assert queue.run_one(db, lambda *_: pytest.fail("concurrent writer")) is False
        assert repo.project_has_active_tasks("18")
    finally:
        release.set()
        worker.join(5)
    seen = []
    assert queue.run_one(db, lambda task, progress: seen.append(task["kind"]))
    assert seen == ["op2_modal"]
    assert not repo.project_has_active_tasks("18")


def test_enqueue_is_local_deduplicated_and_changed_source_gets_new_task(queued_project):
    first = enqueue_bdf(queued_project)
    assert enqueue_bdf(queued_project)["id"] == first["id"]
    db, _, bdf, _, _ = queued_project
    bdf.write_bytes(b"changed synthetic source")
    assert enqueue_bdf(queued_project)["id"] != first["id"]
    assert len(queue.list_imports(db, "18")) == 2


def test_failure_is_reported_without_reverting_visualization_ready(queued_project):
    db, repo, _, _, _ = queued_project
    enqueue_bdf(queued_project)

    def fail(task, progress):
        progress("write_mysql", "写入修正数据")
        raise RuntimeError("database unavailable")

    queue.run_one(db, fail)
    task = queue.list_imports(db, "18")[0]
    assert task["status"] == "failed"
    assert task["error"] == "database unavailable"
    assert repo.get_project("18")["geom_status"] == "ready"
    assert any("导入失败" in r["message"] for r in repo.get_job_logs("18"))


def test_restart_does_not_automatically_repeat_interrupted_import(queued_project, monkeypatch):
    db, _, _, _, _ = queued_project
    task = enqueue_bdf(queued_project)
    assert queue._claim(db)["id"] == task["id"]
    monkeypatch.setattr(queue, "_pid_alive", lambda pid: False)
    queue.recover_abandoned(db)
    assert queue.list_imports(db, "18")[0]["phase"] == "interrupted"
    assert enqueue_bdf(queued_project)["status"] == "failed"
    assert queue.run_one(db, lambda *_: pytest.fail("must not reparse")) is False


def test_live_owner_is_not_recovered_by_another_worker(queued_project, monkeypatch):
    db = queued_project[0]
    enqueue_bdf(queued_project)
    queue._claim(db)
    monkeypatch.setattr(queue, "_pid_alive", lambda pid: True)
    queue.recover_abandoned(db)
    assert queue.list_imports(db, "18")[0]["status"] == "running"


def test_project_replacement_does_not_run_stale_import(queued_project):
    db = queued_project[0]
    enqueue_bdf(queued_project)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE projects SET created_at='replacement' WHERE project_id='18'")
    assert queue.run_one(db, lambda *_: pytest.fail("stale import")) is False
    assert queue.list_imports(db, "18")[0]["phase"] == "invalidated"


def test_changed_source_invalidates_queued_task_without_reading_it(queued_project):
    db, _, bdf, _, _ = queued_project
    enqueue_bdf(queued_project)
    bdf.write_bytes(b"replacement source")
    assert queue.run_one(db, lambda *_: pytest.fail("stale source")) is False
    assert queue.list_imports(db, "18")[0]["phase"] == "invalidated"


def test_old_database_does_not_create_or_backfill_imports(tmp_path):
    db = str(tmp_path / "old.db")
    RegistryRepo(db)
    assert queue.list_imports(db, "18") == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (queue.TABLE,)).fetchone() is None


def test_quiet_phase_has_heartbeat_but_no_fake_percentage(queued_project):
    db, repo, _, _, _ = queued_project
    enqueue_bdf(queued_project)
    phase_entered = threading.Event()
    release = threading.Event()

    def quiet(task, progress):
        progress("node_index", "保存节点索引")
        phase_entered.set()
        assert release.wait(5)

    thread = threading.Thread(target=lambda: queue.run_one(db, quiet, heartbeat_seconds=0.02))
    thread.start()
    try:
        assert phase_entered.wait(3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            logs = repo.get_job_logs("18")
            if any("仍在执行" in row["message"] for row in logs):
                break
            time.sleep(0.02)
        else:
            pytest.fail("no heartbeat")
        assert all(row["percent"] is None for row in logs)
    finally:
        release.set()
        thread.join(5)


def test_log_api_reports_visualization_and_background_independently(queued_project, monkeypatch):
    from src.l3.api.routes import projects

    db, repo, _, _, _ = queued_project
    repo.append_job_log("18", "step", "几何进度", percent=100)
    enqueue_bdf(queued_project)
    monkeypatch.setattr(projects, "_repo", lambda: repo)
    app = FastAPI()
    app.include_router(projects.router)
    with TestClient(app) as client:
        data = client.get("/api/projects/18/logs").json()["data"]
        assert data["current_percent"] == 100
        assert data["pipeline"] == {"geometry": "ready", "results": [{"result_group": "A", "status": "pending"}]}
        assert data["model_update_tasks"][0]["status"] == "pending"
        # Incremental empty-log responses still carry live task status.
        latest = client.get(f"/api/projects/18/logs?since_id={data['next_since_id']}").json()["data"]
        assert latest["logs"] == []
        assert latest["model_update_tasks"] == data["model_update_tasks"]


def test_bdf_pipeline_queues_import_without_calling_importer(queued_project, monkeypatch):
    db, _, bdf, _, workspace = queued_project
    monkeypatch.setattr(job_runner, "REGISTRY_DB", db)
    monkeypatch.setattr(job_runner, "_run_streaming", lambda *args, **kwargs: (0, ""))
    monkeypatch.setattr(job_runner, "_prepare_registered", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner, "_log_elem_type_summary", lambda *args: None)
    assert job_runner._run_bdf_project("18", str(bdf), str(workspace))
    assert queue.list_imports(db, "18")[0]["status"] == "pending"
