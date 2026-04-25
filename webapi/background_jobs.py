from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from src.l3.core.errors import AppError

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sensitivity-bg")
_TASKS: Dict[str, dict] = {}
_LOCK = threading.Lock()


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


def _run_task(task_id: str, fn: Callable[..., Any], kwargs: dict) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        task["status"] = "running"
        task["started_at"] = _utc_now_iso()

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
        return

    with _LOCK:
        task = _TASKS.get(task_id)
        if task is not None:
            task["status"] = "succeeded"
            task["finished_at"] = _utc_now_iso()
            task["result"] = _jsonable(result)


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
        _TASKS[task_id] = task
    _EXECUTOR.submit(_run_task, task_id, fn, dict(kwargs))
    return _snapshot(task)


def get_background_task(task_id: str) -> Optional[dict]:
    with _LOCK:
        task = _TASKS.get(str(task_id))
        return _snapshot(task) if task is not None else None
