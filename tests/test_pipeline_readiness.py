"""就绪屏障回归：状态提交顺序、失败反馈、跨进程缓存落盘。"""
import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from src import job_runner
from src.l3.infra import runner_thread
from src.l3.infra.registry_repo import RegistryRepo
from src.l3.services import cache_store, readiness_service as readiness


@pytest.fixture(params=[False, True], ids=["standalone", "embedded"])
def pipeline(request, tmp_path, monkeypatch):
    repo = RegistryRepo(str(tmp_path / "registry.db"))
    repo.create_project("p", str(tmp_path), source_type="bdf")
    repo.update_project_geom_status("p", "running")
    repo.create_result_group("p", "g", "g", "a.op2", "a.op2", "a.op2", "a.op2", None)
    with repo._connect() as conn:
        conn.execute("UPDATE result_groups SET status='running'")
    if request.param:
        module = runner_thread
        runner = runner_thread.EmbeddedRunner(repo.db_path, str(tmp_path))
    else:
        module = job_runner
        runner = job_runner
        monkeypatch.setattr(job_runner, "REGISTRY_DB", repo.db_path)
        monkeypatch.setattr(job_runner, "DATA_ROOT", str(tmp_path))
    return repo, module, runner


@pytest.mark.parametrize("group", [False, True])
def test_ready_is_committed_only_after_prepare_returns(pipeline, monkeypatch, group):
    repo, module, runner = pipeline
    calls = []

    def prepare(*args, **kwargs):
        assert repo.get_project("p")["geom_status"] == "running"
        assert repo.list_result_groups("p")[0]["status"] == "running"
        assert repo.has_active_tasks()  # 不因为自己仍 running 就让路或等待 ready。
        calls.append(kwargs)

    monkeypatch.setattr(module, "_prepare_registered", prepare)
    if group:
        runner._update_result_group_status("p", "g", "ready")
        assert repo.list_result_groups("p")[0]["status"] == "ready"
    else:
        runner._update_project_geom_status("p", "ready")
        assert repo.get_project("p")["geom_status"] == "ready"
        # 普通几何完成不能错误地把另一个正在解析的结果组一起发布。
        assert repo.list_result_groups("p")[0]["status"] == "running"
    assert len(calls) == 1


@pytest.mark.parametrize("group", [False, True])
def test_prepare_failure_is_persisted_for_frontend(pipeline, monkeypatch, group):
    repo, module, runner = pipeline

    def fail(*args, **kwargs):
        raise readiness.PreparationError("磁盘空间不足")

    monkeypatch.setattr(module, "_prepare_registered", fail)
    with pytest.raises(readiness.PreparationError):
        if group:
            runner._update_result_group_status("p", "g", "ready")
        else:
            runner._update_project_geom_status("p", "ready")
    target = repo.list_result_groups("p")[0] if group else repo.get_project("p")
    assert target["status" if group else "geom_status"] == "error"
    assert "磁盘空间不足" in target["error_message"]
    assert repo.get_project("p")["geom_status"] == ("running" if group else "error")


def test_recovered_default_group_waits_and_is_published_with_project(pipeline, monkeypatch):
    from pathlib import Path
    from src.l1.manifest_schema import MANIFEST_SCHEMA
    repo, module, runner = pipeline
    workspace = Path(repo.get_project("p")["workspace"])
    with repo._connect() as conn:
        conn.execute("UPDATE projects SET source_type='odb' WHERE project_id='p'")
    repo.create_result_group("p", "default_result", "default", "a.odb", "a.odb", "a.odb", "a.odb", None)
    with sqlite3.connect(workspace / "manifest.db") as conn:
        conn.executescript(MANIFEST_SCHEMA)
        conn.execute("INSERT INTO result_files (result_group,step_name,field_name,file_path) VALUES (NULL,'Step','U','u.h5')")
    adopt = getattr(runner, "_adopt_odb_default_results", None) or runner._adopt_odb_result_group
    adopt("p", "a.odb", str(workspace))
    assert repo.get_project("p")["geom_status"] == "running"
    assert repo.get_result_group("p", "default_result")["status"] == "running"

    def prepared(*args, **kwargs):
        assert repo.get_result_group("p", "default_result")["status"] == "running"

    monkeypatch.setattr(module, "_prepare_registered", prepared)
    runner._update_project_geom_status("p", "ready")
    assert repo.get_project("p")["geom_status"] == "ready"
    assert repo.get_result_group("p", "default_result")["status"] == "ready"
    assert repo.get_result_group("p", "g")["status"] == "running"


def test_embedded_advanced_failure_never_publishes_core(tmp_path, monkeypatch):
    repo = RegistryRepo(str(tmp_path / "registry.db"))
    repo.create_project("p", str(tmp_path))
    repo.update_project_geom_status("p", "running")
    runner = runner_thread.EmbeddedRunner(repo.db_path, str(tmp_path))
    phases = []

    def run(cmd, **kwargs):
        phases.append(cmd[-1])
        assert repo.get_project("p")["geom_status"] == "running"
        return SimpleNamespace(returncode=int(cmd[-1] == "advanced"), stderr="index failed")

    monkeypatch.setattr(runner_thread.subprocess, "run", run)
    monkeypatch.setattr(runner_thread, "_prepare_registered", lambda *a, **kw: pytest.fail("不应预热"))
    assert not runner._run_project_l2_staged("p", str(tmp_path))
    assert phases == ["core", "advanced"]
    assert repo.get_project("p")["geom_status"] == "error"


@pytest.fixture
def prepared_model(tmp_path, monkeypatch):
    (tmp_path / "l2").mkdir()
    (tmp_path / "l2" / "advanced_status.json").write_text(json.dumps({"status": "ready"}))
    (tmp_path / "u.h5").write_bytes(b"result file")
    with sqlite3.connect(tmp_path / "manifest.db") as conn:
        conn.executescript("""
            CREATE TABLE instances (instance_name TEXT);
            INSERT INTO instances VALUES ('A'), ('B');
            CREATE TABLE result_files (result_group TEXT, step_name TEXT, field_name TEXT, file_path TEXT);
            INSERT INTO result_files VALUES ('g', 'Step', 'U', 'u.h5');
            CREATE TABLE result_blocks (result_group TEXT, step_name TEXT, field_name TEXT, instance_name TEXT);
            INSERT INTO result_blocks VALUES ('g', 'Step', 'U', 'A');
            CREATE TABLE frames (result_group TEXT, step_name TEXT, frame_idx INT);
            INSERT INTO frames VALUES ('g', 'Step', 0), ('g', 'Step', 4);
        """)
    idx = SimpleNamespace(
        is_render_ready=True, advanced_status="ready", workspace=str(tmp_path),
        render_source_elem_row={"A": np.array([0]), "B": np.array([0])},
        averaging_data={"A": {}, "B": {}}, octree={"A": {}, "B": {}},
        load_l2_render_data=lambda: None)
    monkeypatch.setattr(readiness, "ModelIndex", lambda *a: idx)
    monkeypatch.setattr(readiness, "warm_color_codes", lambda *a, **kw: 0)
    monkeypatch.setattr(readiness, "warm_element_mesh_edges", lambda *a, **kw: 0)
    calls = []

    def calculate(*args, **kw):
        calls.append((args, kw))
        cache_store.json_put(str(tmp_path), "groups/g", ("test", str(args[2:])), {"ok": True})

    for name in ("compute_scalar_range", "frame_scalars", "frame_colors",
                 "frame_vertex_displacements", "deform_scale_stats"):
        monkeypatch.setattr(readiness, name, calculate)
    monkeypatch.setattr(cache_store.settings, "scalar_cache_mb", 10)
    monkeypatch.setattr(cache_store.settings, "scalar_disk_cache_mb", 10)
    return tmp_path, idx, calls


def test_prepare_ignores_busy_queue_and_only_warms_instances_with_data(prepared_model, monkeypatch):
    workspace, idx, calls = prepared_model
    from src.l3.services import warmup_service
    monkeypatch.setattr(warmup_service, "parse_busy", lambda: pytest.fail("不得等待解析队列空闲"))
    readiness.prepare_workspace("p", str(workspace), "g", group_only=True)
    assert len(calls) == 5
    assert all(args[2] == "A" and args[-1] == 4 for args, _kw in calls[:4])
    assert calls[4][0][2:5] == ("Step", 4, "g")
    assert list((workspace / "l3_cache" / "groups" / "g" / "json").glob("*.json"))


def test_declared_result_failure_is_not_skipped(prepared_model, monkeypatch):
    workspace, _idx, _calls = prepared_model

    def fail(*a, **kw):
        raise OSError("broken HDF5")

    monkeypatch.setattr(readiness, "frame_scalars", fail)
    with pytest.raises(readiness.PreparationError, match="g/Step/U/A.*broken HDF5"):
        readiness.prepare_workspace("p", str(workspace))


def test_instance_without_data_is_skipped_like_api(prepared_model, monkeypatch):
    """实体实例的面内主应变等全 NaN 块：接口返回空 payload 置灰，首屏也不应失败。"""
    workspace, _idx, calls = prepared_model
    from src.l3.core.errors import NotFoundError

    def no_data(*a, **kw):
        raise NotFoundError("No result data (NODAL/ELEMENT_NODAL/INTEGRATION_POINT)")

    monkeypatch.setattr(readiness, "compute_scalar_range", no_data)
    readiness.prepare_workspace("p", str(workspace), "g", group_only=True)
    # 云图三件套跳过，U 位移与变形统计照常准备
    assert len(calls) == 2


def test_missing_advanced_data_never_counts_as_success(prepared_model):
    workspace, idx, _calls = prepared_model
    del idx.averaging_data["B"]
    with pytest.raises(readiness.PreparationError, match="高级索引缺失"):
        readiness.prepare_workspace("p", str(workspace))


def test_missing_result_blocks_are_a_failure(prepared_model):
    workspace, _idx, _calls = prepared_model
    with sqlite3.connect(workspace / "manifest.db") as conn:
        conn.execute("DELETE FROM result_blocks")
    with pytest.raises(readiness.PreparationError, match="缺少结果块"):
        readiness.prepare_workspace("p", str(workspace))


def test_cache_lock_timeout_does_not_wait_forever(prepared_model, monkeypatch):
    workspace, _idx, _calls = prepared_model
    monkeypatch.setattr(readiness._WarmLock, "acquire", lambda self: False)
    times = iter([0, 301])
    monkeypatch.setattr(readiness.time, "monotonic", lambda: next(times))
    with pytest.raises(readiness.PreparationError, match="锁超时"):
        readiness.prepare_workspace("p", str(workspace))


def test_lock_creation_error_is_not_treated_as_success(prepared_model, monkeypatch):
    workspace, _idx, _calls = prepared_model

    def fail(*args, **kwargs):
        raise PermissionError("lock denied")

    monkeypatch.setattr(readiness.os, "open", fail)
    with pytest.raises(readiness.PreparationError, match="lock denied"):
        readiness.prepare_workspace("p", str(workspace))


def test_strict_cache_detects_silent_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_store.settings, "scalar_cache_mb", 10)
    monkeypatch.setattr(cache_store.settings, "scalar_disk_cache_mb", 10)
    monkeypatch.setattr(cache_store, "_atomic_write", lambda *a: False)
    with pytest.raises(RuntimeError, match="未完整保留"):
        with cache_store.require_persistent_cache():
            cache_store.payload_put(str(tmp_path), "model", ("x",), b"data")


def test_strict_cache_detects_eviction_and_does_not_trust_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_store.settings, "scalar_cache_mb", 10)
    monkeypatch.setattr(cache_store.settings, "scalar_disk_cache_mb", 1)
    cache_store.mem_put(("memory",), np.ones(1))
    with pytest.raises(RuntimeError, match="未完整保留"):
        with cache_store.require_persistent_cache():
            assert cache_store.mem_get(("memory",)) is None
            for i in range(2):
                cache_store.payload_put(str(tmp_path), "model", (i,), b"x" * 600_000)


def test_disabled_disk_cache_cannot_claim_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_store.settings, "scalar_disk_cache_mb", 0)
    with pytest.raises(RuntimeError, match="均大于 0"):
        with cache_store.require_persistent_cache():
            pytest.fail("cache disabled")


def test_project_status_endpoint_returns_failure_without_manifest(tmp_path, monkeypatch):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from src.l3.api.routes import projects
    repo = RegistryRepo(str(tmp_path / "registry.db"))
    repo.create_project("p", str(tmp_path))
    repo.update_project_geom_status("p", "error", "[首屏准备失败] 磁盘已满")
    monkeypatch.setattr(projects, "_repo", lambda: repo)
    app = FastAPI()
    app.include_router(projects.router)
    async def query():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for url in ("/api/projects/p", "/api/projects", "/api/projects/p/result-catalog"):
                response = await client.get(url)
                assert response.status_code == 200
                data = response.json()["data"]
                if isinstance(data, list):
                    data = data[0]
                assert data["geom_status"] == "error"
                assert "磁盘已满" in data["error_message"]
    asyncio.run(query())
    assert not (tmp_path / "manifest.db").exists()
