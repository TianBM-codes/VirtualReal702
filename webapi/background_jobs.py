from __future__ import annotations

import importlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from db import get_connection
from src.l3.core.errors import AppError

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="model-update-bg")
_TASKS: Dict[str, dict] = {}
_LOCK = threading.Lock()
_MAX_EXECUTE_COUNT = 3
_TASK_TABLE = "t_mt_py_background_task"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(_jsonable(value), ensure_ascii=False)


def _json_loads(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


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


def _row_to_task(row: dict) -> dict:
    project_id = row.get("project_id")
    return {
        "task_id": str(row["task_id"]),
        "task_type": str(row["task_type"]),
        "interface_code": str(row.get("interface_code") or row["task_type"]),
        "task_kind": str(row.get("task_kind") or "internal"),
        "project_id": None if project_id is None else int(project_id),
        "status": str(row["status"]),
        "execute_count": int(row.get("execute_count") or 0),
        "max_execute_count": int(row.get("max_execute_count") or _MAX_EXECUTE_COUNT),
        "handler_module": row.get("handler_module"),
        "handler_name": row.get("handler_name"),
        "pass_task_id": bool(row.get("pass_task_id")),
        "submitted_at": row.get("submitted_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "request": _json_loads(row.get("request_json")) or {},
        "kwargs": _json_loads(row.get("kwargs_json")) or {},
        "progress": _json_loads(row.get("progress_json")),
        "result": _json_loads(row.get("result_json")),
        "error": _json_loads(row.get("error_json")),
    }


def _persist_task(task: dict) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            INSERT INTO {_TASK_TABLE} (
                task_id, task_type, interface_code, task_kind, project_id, status,
                execute_count, max_execute_count, handler_module, handler_name, pass_task_id,
                submitted_at, started_at, finished_at, request_json, kwargs_json,
                progress_json, result_json, error_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                task_type = VALUES(task_type),
                interface_code = VALUES(interface_code),
                task_kind = VALUES(task_kind),
                project_id = VALUES(project_id),
                status = VALUES(status),
                execute_count = VALUES(execute_count),
                max_execute_count = VALUES(max_execute_count),
                handler_module = VALUES(handler_module),
                handler_name = VALUES(handler_name),
                pass_task_id = VALUES(pass_task_id),
                submitted_at = VALUES(submitted_at),
                started_at = VALUES(started_at),
                finished_at = VALUES(finished_at),
                request_json = VALUES(request_json),
                kwargs_json = VALUES(kwargs_json),
                progress_json = VALUES(progress_json),
                result_json = VALUES(result_json),
                error_json = VALUES(error_json)
            """,
            (
                str(task["task_id"]),
                str(task["task_type"]),
                str(task.get("interface_code") or task["task_type"]),
                str(task.get("task_kind") or "internal"),
                task.get("project_id"),
                str(task["status"]),
                int(task.get("execute_count") or 0),
                int(task.get("max_execute_count") or _MAX_EXECUTE_COUNT),
                task.get("handler_module"),
                task.get("handler_name"),
                1 if task.get("pass_task_id") else 0,
                task.get("submitted_at"),
                task.get("started_at"),
                task.get("finished_at"),
                _json_dumps(task.get("request") or {}),
                _json_dumps(task.get("kwargs") or {}),
                _json_dumps(task.get("progress")),
                _json_dumps(task.get("result")),
                _json_dumps(task.get("error")),
            ),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _load_task(task_id: str) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT task_id, task_type, interface_code, task_kind, project_id, status,
                   execute_count, max_execute_count, handler_module, handler_name, pass_task_id,
                   submitted_at, started_at, finished_at, request_json, kwargs_json,
                   progress_json, result_json, error_json
            FROM {_TASK_TABLE}
            WHERE task_id = %s
            LIMIT 1
            """,
            (str(task_id),),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    if row is None:
        return None
    return _row_to_task(row)


def _load_latest_task_by_project_and_interface(project_id: Any, interface_code: str) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT task_id, task_type, interface_code, task_kind, project_id, status,
                   execute_count, max_execute_count, handler_module, handler_name, pass_task_id,
                   submitted_at, started_at, finished_at, request_json, kwargs_json,
                   progress_json, result_json, error_json
            FROM {_TASK_TABLE}
            WHERE project_id = %s AND interface_code = %s
            ORDER BY submitted_at DESC, task_id DESC
            LIMIT 1
            """,
            (int(project_id), str(interface_code)),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
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
        "project_id": None if resolved_project_id is None else int(resolved_project_id),
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
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT task_id, task_type, interface_code, task_kind, project_id, status,
                   execute_count, max_execute_count, handler_module, handler_name, pass_task_id,
                   submitted_at, started_at, finished_at, request_json, kwargs_json,
                   progress_json, result_json, error_json
            FROM {_TASK_TABLE}
            WHERE status IN ('submitted', 'running')
            ORDER BY submitted_at ASC, task_id ASC
            """
        )
        rows = cursor.fetchall() or []
    finally:
        cursor.close()
        conn.close()

    requeued = 0
    failed = 0
    for row in rows:
        task = _row_to_task(row)
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
