from __future__ import annotations

import importlib
import json
import os
import sqlite3
import threading
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from src.l3.core.config import settings
from src.l3.core.errors import AppError

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sensitivity-bg")
_TASKS: Dict[str, dict] = {}
_LOCK = threading.Lock()
_MAX_EXECUTE_COUNT = 3


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_db_path() -> str:
    return os.path.join(str(settings.data_root), "background_tasks.db")


def _fallback_task_db_path() -> str:
    return os.path.join(tempfile.gettempdir(), "virtualreal702_background_tasks.db")


def _task_conn() -> sqlite3.Connection:
    candidates = [_task_db_path(), _fallback_task_db_path()]
    last_error = None
    for candidate in candidates:
        db_path = os.path.abspath(candidate)
        try:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            conn = sqlite3.connect(db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise sqlite3.OperationalError("unable to open background task database")


def _ensure_column(conn: sqlite3.Connection, columns: set[str], name: str, sql: str) -> None:
    if name in columns:
        return
    conn.execute(f"ALTER TABLE background_tasks ADD COLUMN {name} {sql}")
    columns.add(name)


def _ensure_task_store_ready() -> None:
    conn = _task_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS background_tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                interface_code TEXT,
                task_kind TEXT NOT NULL DEFAULT 'internal',
                project_id TEXT,
                status TEXT NOT NULL,
                execute_count INTEGER NOT NULL DEFAULT 0,
                max_execute_count INTEGER NOT NULL DEFAULT 3,
                handler_module TEXT,
                handler_name TEXT,
                pass_task_id INTEGER NOT NULL DEFAULT 0,
                submitted_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                request_json TEXT,
                kwargs_json TEXT,
                progress_json TEXT,
                result_json TEXT,
                error_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_background_tasks_status
            ON background_tasks(status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_background_tasks_project_interface
            ON background_tasks(project_id, interface_code)
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(background_tasks)").fetchall()
        }
        _ensure_column(conn, columns, "interface_code", "TEXT")
        _ensure_column(conn, columns, "task_kind", "TEXT NOT NULL DEFAULT 'internal'")
        _ensure_column(conn, columns, "project_id", "TEXT")
        _ensure_column(conn, columns, "execute_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, columns, "max_execute_count", f"INTEGER NOT NULL DEFAULT {_MAX_EXECUTE_COUNT}")
        _ensure_column(conn, columns, "handler_module", "TEXT")
        _ensure_column(conn, columns, "handler_name", "TEXT")
        _ensure_column(conn, columns, "pass_task_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, columns, "kwargs_json", "TEXT")
        _ensure_column(conn, columns, "progress_json", "TEXT")
        conn.commit()
    finally:
        conn.close()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _snapshot(task: dict) -> dict:
    return {
        "task_id": str(task["task_id"]),
        "task_type": str(task["task_type"]),
        "interface_code": str(task.get("interface_code") or task["task_type"]),
        "task_kind": str(task.get("task_kind") or "internal"),
        "project_id": task.get("project_id"),
        "status": str(task["status"]),
        "execute_count": int(task.get("execute_count") or 0),
        "max_execute_count": int(task.get("max_execute_count") or _MAX_EXECUTE_COUNT),
        "submitted_at": task.get("submitted_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "request": _jsonable(task.get("request")),
        "progress": _jsonable(task.get("progress")),
        "result": _jsonable(task.get("result")),
        "error": _jsonable(task.get("error")),
    }


def _persist_task(task: dict) -> None:
    payload = _snapshot(task)
    try:
        conn = _task_conn()
        try:
            conn.execute(
                """
                INSERT INTO background_tasks (
                    task_id, task_type, interface_code, task_kind, project_id, status,
                    execute_count, max_execute_count, handler_module, handler_name, pass_task_id,
                    submitted_at, started_at, finished_at, request_json, kwargs_json,
                    progress_json, result_json, error_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    task_type = excluded.task_type,
                    interface_code = excluded.interface_code,
                    task_kind = excluded.task_kind,
                    project_id = excluded.project_id,
                    status = excluded.status,
                    execute_count = excluded.execute_count,
                    max_execute_count = excluded.max_execute_count,
                    handler_module = excluded.handler_module,
                    handler_name = excluded.handler_name,
                    pass_task_id = excluded.pass_task_id,
                    submitted_at = excluded.submitted_at,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    request_json = excluded.request_json,
                    kwargs_json = excluded.kwargs_json,
                    progress_json = excluded.progress_json,
                    result_json = excluded.result_json,
                    error_json = excluded.error_json
                """,
                (
                    payload["task_id"],
                    payload["task_type"],
                    payload["interface_code"],
                    payload["task_kind"],
                    None if payload.get("project_id") is None else str(payload["project_id"]),
                    payload["status"],
                    payload["execute_count"],
                    payload["max_execute_count"],
                    task.get("handler_module"),
                    task.get("handler_name"),
                    1 if task.get("pass_task_id") else 0,
                    payload.get("submitted_at"),
                    payload.get("started_at"),
                    payload.get("finished_at"),
                    json.dumps(payload.get("request"), ensure_ascii=False),
                    json.dumps(_jsonable(task.get("kwargs") or {}), ensure_ascii=False),
                    json.dumps(payload.get("progress"), ensure_ascii=False),
                    json.dumps(payload.get("result"), ensure_ascii=False),
                    json.dumps(payload.get("error"), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return


def _row_to_task(row: sqlite3.Row) -> dict:
    return {
        "task_id": str(row["task_id"]),
        "task_type": str(row["task_type"]),
        "interface_code": str(row["interface_code"] or row["task_type"]),
        "task_kind": str(row["task_kind"] or "internal"),
        "project_id": row["project_id"],
        "status": str(row["status"]),
        "execute_count": int(row["execute_count"] or 0),
        "max_execute_count": int(row["max_execute_count"] or _MAX_EXECUTE_COUNT),
        "handler_module": row["handler_module"],
        "handler_name": row["handler_name"],
        "pass_task_id": bool(row["pass_task_id"]),
        "submitted_at": row["submitted_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "request": json.loads(row["request_json"]) if row["request_json"] else {},
        "kwargs": json.loads(row["kwargs_json"]) if row["kwargs_json"] else {},
        "progress": json.loads(row["progress_json"]) if row["progress_json"] else None,
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": json.loads(row["error_json"]) if row["error_json"] else None,
    }


def _load_task(task_id: str) -> Optional[dict]:
    try:
        conn = _task_conn()
        try:
            row = conn.execute(
                """
                SELECT task_id, task_type, interface_code, task_kind, project_id, status,
                       execute_count, max_execute_count, handler_module, handler_name, pass_task_id,
                       submitted_at, started_at, finished_at, request_json, kwargs_json,
                       progress_json, result_json, error_json
                FROM background_tasks
                WHERE task_id = ?
                """,
                (str(task_id),),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    return _row_to_task(row)


def _load_latest_task_by_project_and_interface(project_id: Any, interface_code: str) -> Optional[dict]:
    try:
        conn = _task_conn()
        try:
            row = conn.execute(
                """
                SELECT task_id, task_type, interface_code, task_kind, project_id, status,
                       execute_count, max_execute_count, handler_module, handler_name, pass_task_id,
                       submitted_at, started_at, finished_at, request_json, kwargs_json,
                       progress_json, result_json, error_json
                FROM background_tasks
                WHERE project_id = ? AND interface_code = ?
                ORDER BY submitted_at DESC, task_id DESC
                LIMIT 1
                """,
                (str(project_id), str(interface_code)),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    return _row_to_task(row)


def _cache_and_snapshot(task: dict) -> dict:
    _TASKS[str(task["task_id"])] = dict(task)
    _persist_task(task)
    return _snapshot(task)


def _run_task(task_id: str, fn: Callable[..., Any], kwargs: dict, *, pass_task_id: bool = False) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        task["status"] = "running"
        task["started_at"] = _utc_now_iso()
        task["progress"] = {"phase": "running"}
        task["execute_count"] = int(task.get("execute_count") or 0) + 1
        _persist_task(task)

    try:
        if pass_task_id:
            result = fn(task_id=task_id, **kwargs)
        else:
            result = fn(**kwargs)
    except AppError as exc:
        error = {
            "code": str(exc.code),
            "message": str(exc.message),
            "status_code": int(exc.status_code),
            "details": _jsonable(exc.details),
        }
        with _LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                task["status"] = "failed"
                task["finished_at"] = _utc_now_iso()
                task["error"] = error
                task["progress"] = None
                _persist_task(task)
        return
    except Exception as exc:  # pragma: no cover
        error = {
            "code": "INTERNAL_ERROR",
            "message": str(exc),
            "status_code": 500,
            "details": {},
        }
        with _LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                task["status"] = "failed"
                task["finished_at"] = _utc_now_iso()
                task["error"] = error
                task["progress"] = None
                _persist_task(task)
        return

    with _LOCK:
        task = _TASKS.get(task_id)
        if task is not None:
            task["status"] = "success"
            task["finished_at"] = _utc_now_iso()
            task["progress"] = None
            task["result"] = _jsonable(result)
            _persist_task(task)


def submit_background_task(
    *,
    task_type: str,
    fn: Callable[..., Any],
    kwargs: dict,
    request_payload: Optional[dict] = None,
    pass_task_id: bool = False,
    project_id: Optional[Any] = None,
    interface_code: Optional[str] = None,
    task_kind: str = "internal",
    max_execute_count: int = _MAX_EXECUTE_COUNT,
) -> dict:
    task_id = str(uuid.uuid4())
    payload = _jsonable(request_payload or {})
    resolved_project_id = project_id
    if resolved_project_id is None and isinstance(payload, dict):
        resolved_project_id = payload.get("project_id")
    if resolved_project_id is None:
        resolved_project_id = kwargs.get("project_id")

    task = {
        "task_id": task_id,
        "task_type": str(task_type),
        "interface_code": str(interface_code or task_type),
        "task_kind": str(task_kind or "internal"),
        "project_id": None if resolved_project_id is None else str(resolved_project_id),
        "status": "submitted",
        "execute_count": 0,
        "max_execute_count": max(1, int(max_execute_count or _MAX_EXECUTE_COUNT)),
        "handler_module": str(fn.__module__),
        "handler_name": str(fn.__name__),
        "pass_task_id": bool(pass_task_id),
        "submitted_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "request": payload,
        "kwargs": _jsonable(dict(kwargs)),
        "progress": None,
        "result": None,
        "error": None,
    }
    with _LOCK:
        snapshot = _cache_and_snapshot(task)
    _EXECUTOR.submit(_run_task, task_id, fn, dict(kwargs), pass_task_id=bool(pass_task_id))
    return snapshot


def get_background_task(task_id: str) -> Optional[dict]:
    with _LOCK:
        task = _TASKS.get(str(task_id))
        if task is not None:
            return _snapshot(task)

    loaded = _load_task(str(task_id))
    if loaded is None:
        return None

    with _LOCK:
        task = _TASKS.get(str(task_id))
        if task is None:
            _TASKS[str(task_id)] = dict(loaded)
            task = loaded
        return _snapshot(task)


def get_background_task_by_project_and_interface(project_id: Any, interface_code: str) -> Optional[dict]:
    with _LOCK:
        matched = [
            dict(task)
            for task in _TASKS.values()
            if str(task.get("project_id") or "") == str(project_id)
            and str(task.get("interface_code") or task.get("task_type") or "") == str(interface_code)
        ]
        if matched:
            matched.sort(
                key=lambda item: (str(item.get("submitted_at") or ""), str(item.get("task_id") or "")),
                reverse=True,
            )
            return _snapshot(matched[0])

    loaded = _load_latest_task_by_project_and_interface(project_id, interface_code)
    if loaded is None:
        return None

    with _LOCK:
        _TASKS[str(loaded["task_id"])] = dict(loaded)
        return _snapshot(loaded)


def update_background_task(task_id: str, *, progress: Optional[dict] = None) -> Optional[dict]:
    with _LOCK:
        task = _TASKS.get(str(task_id))
        if task is None:
            loaded = _load_task(str(task_id))
            if loaded is None:
                return None
            _TASKS[str(task_id)] = dict(loaded)
            task = _TASKS[str(task_id)]
        if progress is not None:
            task["progress"] = _jsonable(progress)
        _persist_task(task)
        return _snapshot(task)


def _load_task_handler(task: dict) -> Optional[Callable[..., Any]]:
    module_name = str(task.get("handler_module") or "").strip()
    handler_name = str(task.get("handler_name") or "").strip()
    if not module_name or not handler_name:
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, handler_name, None)


def _mark_task_failed(task: dict, *, code: str, message: str) -> None:
    task["status"] = "failed"
    task["finished_at"] = _utc_now_iso()
    task["progress"] = None
    task["error"] = {
        "code": str(code),
        "message": str(message),
        "status_code": 500,
        "details": {},
    }
    _persist_task(task)


def recover_background_tasks() -> dict:
    try:
        _ensure_task_store_ready()
    except sqlite3.Error:
        return {"requeued": 0, "failed": 0}

    try:
        conn = _task_conn()
        try:
            rows = conn.execute(
                """
                SELECT task_id
                FROM background_tasks
                WHERE status IN ('submitted', 'running')
                ORDER BY submitted_at ASC, task_id ASC
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {"requeued": 0, "failed": 0}

    requeued = 0
    failed = 0
    for row in rows:
        task = _load_task(str(row["task_id"]))
        if task is None:
            continue

        with _LOCK:
            _TASKS[str(task["task_id"])] = dict(task)
            cached = _TASKS[str(task["task_id"])]

            if str(cached.get("task_kind") or "internal") != "internal":
                _mark_task_failed(
                    cached,
                    code="TASK_RESTART_REQUIRES_MANUAL_RETRY",
                    message="service restarted during external solver execution",
                )
                failed += 1
                continue

            if int(cached.get("execute_count") or 0) >= int(cached.get("max_execute_count") or _MAX_EXECUTE_COUNT):
                _mark_task_failed(
                    cached,
                    code="TASK_RETRY_LIMIT_EXCEEDED",
                    message="task exceeded maximum execution count after process restart",
                )
                failed += 1
                continue

        handler = _load_task_handler(cached)
        if handler is None:
            with _LOCK:
                _mark_task_failed(
                    cached,
                    code="TASK_HANDLER_NOT_FOUND",
                    message="task handler could not be restored after process restart",
                )
            failed += 1
            continue

        with _LOCK:
            cached["status"] = "submitted"
            cached["started_at"] = None
            cached["finished_at"] = None
            cached["progress"] = {"phase": "recovered"}
            cached["error"] = None
            cached["result"] = None
            _persist_task(cached)

        _EXECUTOR.submit(
            _run_task,
            str(cached["task_id"]),
            handler,
            dict(cached.get("kwargs") or {}),
            pass_task_id=bool(cached.get("pass_task_id")),
        )
        requeued += 1

    return {"requeued": requeued, "failed": failed}


_ensure_task_store_ready()
