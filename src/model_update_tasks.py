"""Local queue for automatic model-update imports; never runs on the parsing loop.

This stores scheduling metadata only. The existing importers still own all
MySQL/HDF5 data writes. One worker serializes imports so BDF and modal imports
cannot clear/write the same project's model-update data concurrently.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
TABLE = "model_update_import_tasks"
_WORKERS = {}
_WORKER_LOCK = threading.Lock()
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    project_created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    result_group TEXT,
    workspace TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    phase TEXT NOT NULL DEFAULT 'queued',
    message TEXT NOT NULL DEFAULT '等待模型修正后台线程',
    error TEXT,
    owner_pid INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mu_import_project ON {TABLE}(project_id, id);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(db_path):
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _log(conn, task, level, message):
    label = "BDF修正数据" if task["kind"] == "bdf" else f"OP2修正数据[{task['result_group']}]"
    text = f"[模型修正后台][{label}] {message}"
    conn.execute(
        "INSERT INTO job_logs (odb_id, ts, level, stage, message, percent) VALUES (?,?,?,?,?,NULL)",
        (task["project_id"], datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
         level, "mu_background_" + task["kind"], html.escape(text)),
    )
    logger.info("[%s] %s", task["project_id"], text)


def _fingerprint(path):
    if not path:
        return None
    info = os.stat(path)
    return [os.path.abspath(path), info.st_size, info.st_mtime_ns]


def enqueue_import(db_path, project_id, kind, workspace, payload, *, result_group=None):
    if kind not in {"bdf", "op2_modal"}:
        raise ValueError("unknown model-update import kind")
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        project = conn.execute("SELECT created_at FROM projects WHERE project_id=?", (str(project_id),)).fetchone()
        if project is None:
            raise ValueError("project no longer exists")
        # A same-file notification or service restart must not repeat an import.
        sources = {key: _fingerprint(payload.get(key)) for key in ("bdf_path", "op2_path")}
        signature = [str(project_id), project["created_at"], kind, result_group, payload, sources]
        key = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        now = _now()
        cur = conn.execute(
            f"INSERT OR IGNORE INTO {TABLE} "
            "(dedupe_key,project_id,project_created_at,kind,result_group,workspace,payload_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (key, str(project_id), project["created_at"], kind, result_group, os.path.abspath(workspace),
             json.dumps({**payload, "_source_versions": sources}, ensure_ascii=False), now, now),
        )
        row = conn.execute(f"SELECT * FROM {TABLE} WHERE dedupe_key=?", (key,)).fetchone()
        if cur.rowcount:
            _log(conn, row, "step", "已排队；可视化结果解析主队列可继续执行")
        return dict(row)


def list_imports(db_path, project_id, limit=20):
    with _connect(db_path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return []  # Existing projects have no automatic backfill/reparse.
        rows = conn.execute(
            f"SELECT id,kind,result_group,status,phase,message,error,created_at,updated_at,started_at,finished_at "
            f"FROM {TABLE} WHERE project_id=? "
            "ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, id DESC LIMIT ?", (str(project_id), limit),
        ).fetchall()
        return [dict(row) for row in rows]


def _claim(db_path):
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        # At most one import per project, even if two runner processes exist.
        task = conn.execute(
            f"SELECT t.* FROM {TABLE} t WHERE t.status='pending' "
            f"AND NOT EXISTS (SELECT 1 FROM {TABLE} r WHERE r.project_id=t.project_id AND r.status='running') "
            "ORDER BY t.id LIMIT 1"
        ).fetchone()
        if task is None:
            return None
        project = conn.execute("SELECT created_at,geom_status FROM projects WHERE project_id=?", (task["project_id"],)).fetchone()
        if project is None or project["created_at"] != task["project_created_at"] or project["geom_status"] != "ready":
            conn.execute(f"UPDATE {TABLE} SET status='failed',phase='invalidated',message=?,error=?,finished_at=?,updated_at=? WHERE id=?",
                         ("项目已删除、替换或重新解析，未执行修正导入", "project changed", _now(), _now(), task["id"]))
            _log(conn, task, "warn", "项目已删除、替换或重新解析，跳过过期修正任务")
            return None
        payload = json.loads(task["payload_json"])
        try:
            unchanged = all(_fingerprint(payload.get(key)) == version
                            for key, version in payload.get("_source_versions", {}).items())
        except OSError:
            unchanged = False
        if not unchanged:
            message = "源文件已变化或不存在，未执行过期修正任务"
            conn.execute(f"UPDATE {TABLE} SET status='failed',phase='invalidated',message=?,error=?,finished_at=?,updated_at=? WHERE id=?",
                         (message, message, _now(), _now(), task["id"]))
            _log(conn, task, "warn", message)
            return None
        now = _now()
        conn.execute(f"UPDATE {TABLE} SET status='running',owner_pid=?,started_at=?,updated_at=?,phase='starting',message='开始执行' WHERE id=?",
                     (os.getpid(), now, now, task["id"]))
        _log(conn, task, "step", "开始执行（不占用可视化解析主队列）")
        return dict(task)


def _execute_import(task, progress):
    payload = json.loads(task["payload_json"])
    project_id = int(task["project_id"])
    if task["kind"] == "bdf":
        from services.model_update.importers.bdf_service import import_bdf_data
        import_bdf_data(payload["bdf_path"], project_id, clear_before_insert=True, progress=progress)
    else:
        from services.model_update.importers.op2_service import build_modal_import_payload
        from services.model_update.analysis.inp_service import import_fe_modal_results
        cfg = payload["config"]
        progress("read_modal", "读取OP2并对齐配套BDF节点（沿用现有修正导入逻辑）")
        result = build_modal_import_payload(
            op2_path=payload["op2_path"], bdf_path=payload.get("bdf_path"),
            subcase_id=cfg.get("subcase_id"), mode_numbers=cfg.get("mode_numbers"),
            instance_name=cfg.get("instance_name"), part_name=cfg.get("part_name"),
        )
        progress("store_modal", f"写入修正模态数据：{len(result['modes'])}阶（现有存储格式）")
        import_fe_modal_results(project_id=project_id, overwrite=cfg.get("overwrite", True), modes=result["modes"])


def run_one(db_path, execute=None, *, heartbeat_seconds=30):
    task = _claim(db_path)
    if task is None:
        return False
    done = threading.Event()
    phase_start = [time.monotonic()]

    def progress(phase, message):
        phase_start[0] = time.monotonic()
        try:
            with _connect(db_path) as conn:
                conn.execute(f"UPDATE {TABLE} SET phase=?,message=?,updated_at=? WHERE id=?", (phase, message, _now(), task["id"]))
                _log(conn, task, "step", message)
        except sqlite3.Error:
            # Observability must not roll back an otherwise valid data import.
            logger.exception("Model-update phase logging failed: task=%s", task["id"])

    def heartbeat():
        while not done.wait(heartbeat_seconds):
            try:
                with _connect(db_path) as conn:
                    row = conn.execute(f"SELECT message FROM {TABLE} WHERE id=? AND status='running'", (task["id"],)).fetchone()
                    if row:
                        conn.execute(f"UPDATE {TABLE} SET updated_at=? WHERE id=?", (_now(), task["id"]))
                        _log(conn, task, "info", f"仍在执行：{row['message']}；当前阶段已用{int(time.monotonic()-phase_start[0])}秒（非完成百分比）")
            except Exception:
                logger.exception("Model-update import heartbeat failed")

    monitor = threading.Thread(target=heartbeat, daemon=True, name="mu-import-heartbeat")
    monitor.start()
    try:
        (execute or _execute_import)(task, progress)
    except Exception as exc:
        done.set(); monitor.join(timeout=1)
        with _connect(db_path) as conn:
            conn.execute(f"UPDATE {TABLE} SET status='failed',phase='failed',message='导入失败',error=?,updated_at=?,finished_at=? WHERE id=?",
                         (str(exc), _now(), _now(), task["id"]))
            _log(conn, task, "warn", f"导入失败：{exc}；可视化几何和结果的就绪状态不受影响")
        logger.exception("Model-update import failed: task=%s", task["id"])
    else:
        done.set(); monitor.join(timeout=1)
        with _connect(db_path) as conn:
            conn.execute(f"UPDATE {TABLE} SET status='ready',phase='done',message='导入完成',updated_at=?,finished_at=? WHERE id=?",
                         (_now(), _now(), task["id"]))
            _log(conn, task, "step", "导入完成")
    return True


def _pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ctypes.get_last_error() == 5  # No permission is not proof of death.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def recover_abandoned(db_path):
    with _connect(db_path) as conn:
        for task in conn.execute(f"SELECT * FROM {TABLE} WHERE status='running'").fetchall():
            if not _pid_alive(task["owner_pid"]):
                message = "上次服务中断，修正导入未完成；未自动重跑"
                conn.execute(f"UPDATE {TABLE} SET status='failed',phase='interrupted',message=?,error=?,finished_at=?,updated_at=? WHERE id=?",
                             (message, message, _now(), _now(), task["id"]))
                _log(conn, task, "warn", message)


def start_worker(db_path):
    db_path = str(Path(db_path).resolve())
    with _WORKER_LOCK:
        if db_path in _WORKERS and _WORKERS[db_path].is_alive():
            return
        ensure_schema(db_path)
        recover_abandoned(db_path)

        def run():
            while True:
                try:
                    worked = run_one(db_path)
                except Exception:
                    logger.exception("Model-update import worker error")
                    worked = False
                if not worked:
                    time.sleep(1)

        thread = threading.Thread(target=run, daemon=True, name="model-update-imports")
        _WORKERS[db_path] = thread
        thread.start()
