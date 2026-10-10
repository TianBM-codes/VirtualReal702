"""预热调度回归测试：解析繁忙时让路、导入完成自动排队、跨进程文件锁、完成标记。"""
import os

import pytest

from src.l3.services import warmup_service as ws


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ws, "_pending", set())
    monkeypatch.setattr(ws, "_seen_sig", {})
    monkeypatch.setattr(ws, "_first_poll_done", False)
    ws.set_busy_check(None)
    yield
    ws.set_busy_check(None)


def test_parse_busy_defers_auto_warmup_but_not_manual(monkeypatch):
    ws.set_busy_check(lambda: True)
    started = []
    monkeypatch.setattr(ws.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: started.append(1)})())
    assert ws.warm_odb_async("p1", reason="model loaded") == "deferred"
    assert "p1" in ws._pending and not started
    assert ws.warm_odb_async("p1", reason="manual", force=True) == "started"
    assert started == [1]
    ws._active.discard("p1")


def test_note_project_state_baseline_then_ready_transition():
    ws.note_project_state("old", ("ready", (("g", "ready"),)))
    ws.finish_poll_round()
    assert not ws._pending                                   # 启动前的老项目不动

    ws.note_project_state("new", ("running", ()))
    ws.note_project_state("new", ("ready", (("g", "pending"),)))
    assert not ws._pending                                   # 结果组还在解析
    ws.note_project_state("new", ("ready", (("g", "ready"),)))
    assert ws._pending == {"new"}

    ws._pending.clear()
    ws.note_project_state("new", ("ready", (("g", "ready"),)))
    assert not ws._pending                                   # 无变化不重复

    ws.note_project_state("fast", ("ready", (("g", "ready"),)))
    assert ws._pending == {"fast"}                           # 首轮后才出现的新项目


def test_warm_lock_is_exclusive(tmp_path):
    a, b = ws._WarmLock(str(tmp_path)), ws._WarmLock(str(tmp_path))
    assert a.acquire()
    assert not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_done_marker_tracks_result_signature(tmp_path):
    import sqlite3
    (tmp_path / "l1" / "results").mkdir(parents=True)
    h5 = tmp_path / "l1" / "results" / "s.h5"
    h5.write_bytes(b"x")
    con = sqlite3.connect(tmp_path / "manifest.db")
    con.execute("CREATE TABLE result_files (result_group, step_name, field_name, file_path)")
    con.execute("INSERT INTO result_files VALUES ('g','S1','U','l1/results/s.h5')")
    con.commit(); con.close()

    sig = ws._targets_signature(str(tmp_path))
    assert sig
    ws._write_done(str(tmp_path), sig)
    assert ws._read_done(str(tmp_path)) == sig
    h5.write_bytes(b"xyz")                                   # 结果文件重写 → 签名变
    assert ws._targets_signature(str(tmp_path)) != sig


def test_evict_skips_control_files(tmp_path):
    from src.l3.services import cache_store
    root = cache_store.cache_root(str(tmp_path))
    os.makedirs(root)
    for name in (".warmup.lock", ".warmup_done", "x.bin"):
        with open(os.path.join(root, name), "wb") as f:
            f.write(b"0" * 10)
    cache_store.evict(str(tmp_path), cap_bytes=0)
    assert sorted(os.listdir(root)) == [".warmup.lock", ".warmup_done"]


def _prep(workspace, make_registry):
    import sqlite3
    import numpy as np
    from tests.test_l3_disk_cache_warmup import _setup_nodal_instance
    registry, _instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0))
    conn = sqlite3.connect(workspace / "manifest.db")
    conn.execute("CREATE TABLE IF NOT EXISTS result_files "
                 "(step_name TEXT, field_name TEXT, result_group TEXT)")
    conn.execute("INSERT INTO result_files (step_name, field_name, result_group) "
                  "VALUES ('Step-1', 'U', NULL)")
    conn.commit()
    conn.close()
    return registry


def _join_warmers():
    import threading
    for t in threading.enumerate():
        if t.name.startswith("cache-warmup-"):
            t.join(timeout=60)


def test_async_warmup_writes_done_marker(workspace, make_registry):
    from src.l3.services import cache_store
    cache_store.clear_memory()
    registry = _prep(workspace, make_registry)
    assert ws.warm_odb_async("odb", reason="test", reg=registry) == "started"
    _join_warmers()
    assert ws._read_done(str(workspace)) == ws._targets_signature(str(workspace))


def test_async_warmup_skips_when_other_process_holds_lock(workspace, make_registry):
    from src.l3.services import cache_store
    cache_store.clear_memory()
    registry = _prep(workspace, make_registry)
    other = ws._WarmLock(str(workspace))
    assert other.acquire()
    try:
        assert ws.warm_odb_async("odb", reason="test", reg=registry) == "started"
        _join_warmers()
        assert ws._read_done(str(workspace)) == ""           # 没有预热
    finally:
        other.release()


def test_advanced_busy_reads_l2_status(tmp_path):
    import json
    import time
    d = tmp_path / "l2"
    d.mkdir()
    p = d / "advanced_status.json"
    assert not ws.advanced_busy(str(tmp_path))               # 旧工作区无状态文件
    for status, busy in (("pending", True), ("running", True),
                         ("ready", False), ("error", False)):
        p.write_text(json.dumps({"status": status}))
        assert ws.advanced_busy(str(tmp_path)) is busy
    p.write_text(json.dumps({"status": "running"}))
    old = time.time() - ws._ADVANCED_STALE_S - 10
    os.utime(p, (old, old))
    assert not ws.advanced_busy(str(tmp_path))               # 残留的旧状态不阻塞
