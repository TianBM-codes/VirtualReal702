from __future__ import annotations

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


def _ensure_task_store_ready() -> None:
    conn = _task_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS background_tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                submitted_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                request_json TEXT,
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
        "status": str(task["status"]),
        "submitted_at": task.get("submitted_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "request": _jsonable(task.get("request")),
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
                    task_id, task_type, status, submitted_at, started_at, finished_at,
                    request_json, result_json, error_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    task_type = excluded.task_type,
                    status = excluded.status,
                    submitted_at = excluded.submitted_at,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    request_json = excluded.request_json,
                    result_json = excluded.result_json,
                    error_json = excluded.error_json
                """,
                (
                    payload["task_id"],
                    payload["task_type"],
                    payload["status"],
                    payload.get("submitted_at"),
                    payload.get("started_at"),
                    payload.get("finished_at"),
                    json.dumps(payload.get("request"), ensure_ascii=False),
                    json.dumps(payload.get("result"), ensure_ascii=False),
                    json.dumps(payload.get("error"), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return


def _load_task(task_id: str) -> Optional[dict]:
    try:
        conn = _task_conn()
        try:
            row = conn.execute(
                """
                SELECT task_id, task_type, status, submitted_at, started_at, finished_at,
                       request_json, result_json, error_json
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

    return {
        "task_id": str(row["task_id"]),
        "task_type": str(row["task_type"]),
        "status": str(row["status"]),
        "submitted_at": row["submitted_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "request": json.loads(row["request_json"]) if row["request_json"] else {},
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": json.loads(row["error_json"]) if row["error_json"] else None,
    }


def _mark_inflight_tasks_aborted() -> None:
    try:
        _ensure_task_store_ready()
    except sqlite3.Error:
        return
    finished_at = _utc_now_iso()
    error = json.dumps(
        {
            "code": "TASK_ABORTED",
            "message": "task state recovered after process restart",
            "status_code": 500,
            "details": {},
        },
        ensure_ascii=False,
    )
    try:
        conn = _task_conn()
        try:
            conn.execute(
                """
                UPDATE background_tasks
                SET status = 'aborted',
                    finished_at = COALESCE(finished_at, ?),
                    error_json = CASE
                        WHEN error_json IS NULL OR error_json = '' THEN ?
                        ELSE error_json
                    END
                WHERE status IN ('submitted', 'running')
                """,
                (finished_at, error),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return


def _cache_and_snapshot(task: dict) -> dict:
    _TASKS[str(task["task_id"])] = dict(task)
    _persist_task(task)
    return _snapshot(task)


def _run_task(task_id: str, fn: Callable[..., Any], kwargs: dict) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        task["status"] = "running"
        task["started_at"] = _utc_now_iso()
        _persist_task(task)

    try:
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
                _persist_task(task)
        return
    except Exception as exc:  # pragma: no cover - defensive fallback
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
                _persist_task(task)
        return

    with _LOCK:
        task = _TASKS.get(task_id)
        if task is not None:
            task["status"] = "succeeded"
            task["finished_at"] = _utc_now_iso()
            task["result"] = _jsonable(result)
            _persist_task(task)


def submit_background_task(
    *,
    task_type: str,
    fn: Callable[..., Any],
    kwargs: dict,
    request_payload: Optional[dict] = None,
) -> dict:
    task_id = str(uuid.uuid4())
    task = {
        "task_id": task_id,
        "task_type": str(task_type),
        "status": "submitted",
        "submitted_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "request": _jsonable(request_payload or {}),
        "result": None,
        "error": None,
    }
    with _LOCK:
        snapshot = _cache_and_snapshot(task)
    _EXECUTOR.submit(_run_task, task_id, fn, dict(kwargs))
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


_mark_inflight_tasks_aborted()
