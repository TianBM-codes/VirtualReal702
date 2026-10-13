#!/usr/bin/env python3
"""Compare old/new VirtualReal702 versions and generate a Markdown report.

Fully automated workflow:
  1. Configure old/new base_url and project_id in a JSON file.
  2. Configure automation.repo_path/commit for each version.
  3. Run: python tools/benchmark_versions.py --config benchmark_versions.json --mode auto

The auto mode creates/reuses an isolated Git worktree for the old commit, starts
the API and job runner, submits BDF/OP2 imports, benchmarks the ready project,
then repeats the same process for the current version. Only benchmark project
IDs configured in the JSON file are deleted. They must not be numeric: the
managed services share the real MySQL database from service_config.json, and
the service purges/overwrites model-update rows by numeric project id. Managed
services run with APP_WARMUP=0 unless a version's env overrides it.

The script streams binary responses instead of retaining them in memory. Results
are written as raw CSV plus a compact Markdown comparison report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

import requests


DEFAULT_TIMEOUT = 1800.0
TERMINAL_STATES = {"ready", "error", "not_found"}
ADVANCED_BUSY_STATES = {"pending", "running"}
LOG_PAGE_SIZE = 1000
# Waiting time, not processing cost; the old value is mostly polling latency.
NO_RATIO_CASES = {"op2_queue_wait"}
DEFAULT_COLOR_CODE_SCHEMES = ["etype", "material", "section_type", "section", "section_assignment"]


class HttpRequestError(RuntimeError):
    def __init__(self, status: int, url: str, detail: str):
        self.status = int(status)
        self.url = url
        self.detail = detail
        super().__init__(f"HTTP {self.status} for {url}: {detail}")


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "code" in payload and "data" in payload:
        if int(payload.get("code") or 0) >= 400:
            raise RuntimeError(payload.get("message") or f"API code {payload['code']}")
        return payload["data"]
    return payload


def _url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    return url + (("?" + urlencode(clean)) if clean else "")


def _request(
    url: str,
    timeout: float,
    *,
    collect_json: bool = False,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    size = 0
    body = bytearray() if collect_json else None
    request_body = None
    headers = {"Accept-Encoding": "identity"}
    if payload is not None:
        request_body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if session is not None:
        try:
            with session.request(
                method,
                url,
                data=request_body,
                headers=headers,
                timeout=timeout,
                stream=True,
            ) as response:
                if response.status_code >= 400:
                    detail = response.content[:4096].decode("utf-8", errors="replace")
                    raise HttpRequestError(response.status_code, url, detail)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if body is not None:
                        body.extend(chunk)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                result = {
                    "status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "server_ms": _float_or_none(
                        response.headers.get("x-elapsed-time-ms")),
                    "bytes": size,
                    "cache": response.headers.get("x-cache", ""),
                }
                if body is not None:
                    result["json"] = _unwrap(json.loads(body.decode("utf-8-sig")))
                return result
        except requests.RequestException as exc:
            raise RuntimeError(f"Cannot reach {url}: {exc}") from exc
    try:
        request = Request(url, data=request_body, headers=headers, method=method)
        with urlopen(request, timeout=timeout) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if body is not None:
                    body.extend(chunk)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            headers = {k.lower(): v for k, v in response.headers.items()}
            result = {
                "status": response.status,
                "elapsed_ms": elapsed_ms,
                "server_ms": _float_or_none(headers.get("x-elapsed-time-ms")),
                "bytes": size,
                "cache": headers.get("x-cache", ""),
            }
            if body is not None:
                result["json"] = _unwrap(json.loads(body.decode("utf-8-sig")))
            return result
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise HttpRequestError(exc.code, url, detail) from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc
    except (OSError, http.client.HTTPException) as exc:
        # urllib leaves read timeouts and dropped connections unwrapped.
        raise RuntimeError(f"Cannot reach {url}: {exc!r}") from exc


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_get(base_url: str, path: str, timeout: float, params=None) -> Any:
    return _request(_url(base_url, path, params), timeout, collect_json=True)["json"]


def _json_request(
    base_url: str,
    path: str,
    timeout: float,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    return _request(
        _url(base_url, path),
        timeout,
        collect_json=True,
        method=method,
        payload=payload,
    )["json"]


def _first_name(items: list[Any], keys: tuple[str, ...]) -> str | None:
    if not items:
        return None
    item = items[0]
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in keys:
            if item.get(key):
                return str(item[key])
    return None


def _discover(
    version: dict[str, Any], timeout: float, settings: dict[str, Any]
) -> dict[str, Any]:
    base = version["base_url"]
    project_id = str(version["project_id"])
    project = _json_get(base, f"api/projects/{quote(project_id, safe='')}", timeout)
    if project.get("geom_status") != "ready":
        raise RuntimeError(
            f"{version['name']}: project {project_id} geom_status={project.get('geom_status')!r}"
        )

    include_op2 = bool(settings.get("include_op2", True))
    result_group = version.get("result_group") if include_op2 else None
    if include_op2 and not result_group:
        ready = [r for r in project.get("result_groups", []) if r.get("status") == "ready"]
        result_group = ready[0].get("result_group") if ready else None

    overview = _json_get(
        base,
        f"api/odb/{quote(project_id, safe='')}/meta/overview",
        timeout,
        {"result_group": result_group},
    )
    instance = version.get("instance") or _first_name(
        overview.get("instances", []), ("instance_name", "name")
    )
    step = version.get("step") or _first_name(
        overview.get("steps", []), ("step_name", "name")
    )
    required = [("instance", instance)]
    if include_op2:
        required.append(("step", step))
    missing = [k for k, v in required if not v]
    if missing:
        raise RuntimeError(f"{version['name']}: cannot discover {', '.join(missing)}")
    requested_schemes = settings.get("color_code_schemes", DEFAULT_COLOR_CODE_SCHEMES)
    if not isinstance(requested_schemes, list) or any(
        scheme not in DEFAULT_COLOR_CODE_SCHEMES for scheme in requested_schemes
    ):
        raise ValueError(f"color_code_schemes must be a list drawn from {DEFAULT_COLOR_CODE_SCHEMES}")
    requested_schemes = list(dict.fromkeys(requested_schemes))
    available_schemes = []
    if requested_schemes:
        scheme_info = _json_get(
            base, f"api/odb/{quote(project_id, safe='')}/color-code/{quote(str(instance), safe='')}/schemes",
            timeout,
        )
        available_schemes = scheme_info.get("schemes", [])
    color_schemes = [scheme for scheme in requested_schemes if scheme in available_schemes]
    skipped_schemes = [scheme for scheme in requested_schemes if scheme not in available_schemes]
    version["color_code_schemes"] = color_schemes
    version["skipped_color_code_schemes"] = skipped_schemes
    if skipped_schemes:
        print(f"[{version['name']}] unavailable color-code schemes skipped: {', '.join(skipped_schemes)}")
    return {
        "project_id": project_id,
        "instance": str(instance),
        "step": str(step or ""),
        "result_group": str(result_group or ""),
        "color_code_schemes": color_schemes,
    }


def _cases(ctx: dict[str, Any], settings: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    project = quote(ctx["project_id"], safe="")
    instance_path = quote(ctx["instance"], safe="")
    common = {
        "instance": ctx["instance"],
        "step": ctx["step"],
        "frame": int(settings.get("frame", 0)),
        "result_group": ctx["result_group"] or None,
    }
    cases = [
        ("metadata", f"api/odb/{project}/meta/overview", {"result_group": common["result_group"]}),
        ("model_load", f"api/odb/{project}/geometry/{instance_path}/render-buffers", {}),
        ("element_mesh_edges", f"api/odb/{project}/geometry/{instance_path}/element-mesh-edges", {}),
    ]
    for scheme in ctx.get("color_code_schemes", []):
        cases.append((f"color_code_{scheme}", f"api/odb/{project}/color-code/{instance_path}", {"scheme": scheme}))
    if not bool(settings.get("include_op2", True)):
        return cases
    cases.extend([
        ("modal_shape", f"api/odb/{project}/results/modal-shape", common),
        (
            "deformed_positions",
            f"api/odb/{project}/results/deformed-positions",
            {**common, "scale": settings.get("scale", 1.0)},
        ),
        (
            "modal_animation",
            f"api/odb/{project}/results/modal-animation",
            {
                **common,
                "scale": settings.get("scale", 1.0),
                "n_frames": int(settings.get("n_frames", 4)),
            },
        ),
    ])
    color_modes = settings.get("frame_colors_modes", ["smooth", "flat"])
    if not isinstance(color_modes, list) or any(mode not in ("smooth", "flat") for mode in color_modes):
        raise ValueError("frame_colors_modes must be a list drawn from smooth, flat")
    # Keep existing deformation cases in their original order. Cloud colors
    # have separate scalar cache keys and run after the deformation family.
    for mode in dict.fromkeys(color_modes):
        cases.append((
            f"frame_colors_{mode}", f"api/odb/{project}/results/frame-colors",
            {**common, "field": settings.get("frame_colors_field", "U"),
             "component": settings.get("frame_colors_component", "U1"), "mode": mode},
        ))
    return cases


def _benchmark_version(version: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
    timeout = float(settings.get("timeout_seconds", DEFAULT_TIMEOUT))
    repeat = max(1, int(settings.get("repeat", 5)))
    repeat_by_case = settings.get("repeat_by_case", {})
    ctx = _discover(version, timeout, settings)
    print(
        f"[{version['name']}] project={ctx['project_id']} instance={ctx['instance']} "
        f"step={ctx['step']} result_group={ctx['result_group'] or '-'}"
    )
    rows = []
    with requests.Session() as session:
        for case_name, path, params in _cases(ctx, settings):
            case_repeat = max(1, int(repeat_by_case.get(case_name, repeat)))
            for run in range(1, case_repeat + 1):
                result = _request(
                    _url(version["base_url"], path, params),
                    timeout,
                    session=session,
                )
                row = {
                    "version": version["name"],
                    "project_id": ctx["project_id"],
                    "case": case_name,
                    "run": run,
                    "temperature": "first" if run == 1 else "repeat",
                    **result,
                }
                rows.append(row)
                print(
                    f"  {case_name}[{run}]: {result['elapsed_ms']:.1f} ms, "
                    f"server={_fmt_ms(result['server_ms'])}, "
                    f"size={result['bytes'] / 1024 / 1024:.2f} MiB, cache={result['cache'] or '-'}"
                )
    return rows


def _parse_ts(value: str) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _log_stage_rows(version: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
    timeout = float(settings.get("timeout_seconds", DEFAULT_TIMEOUT))
    project_id = str(version["project_id"])
    logs: list[dict[str, Any]] = []
    since_id = 0
    try:
        # Every subprocess output line is a log row, so page through all of them;
        # the OP2 stage comes last and would be the first to be cut off.
        while True:
            data = _json_get(
                version["base_url"], f"api/projects/{quote(project_id, safe='')}/logs", timeout,
                {"since_id": since_id, "limit": LOG_PAGE_SIZE},
            )
            page = data.get("logs", [])
            logs.extend(page)
            next_since_id = int(data.get("next_since_id") or since_id)
            if len(page) < LOG_PAGE_SIZE or next_since_id <= since_id:
                break
            since_id = next_since_id
    except HttpRequestError as exc:
        if exc.status != 404:
            raise
        print(f"[{version['name']}] project logs are not available; skipping log-derived timings.")
        return []
    grouped: dict[str, list[datetime]] = {}
    for item in logs:
        ts = _parse_ts(item.get("ts", ""))
        stage = str(item.get("stage") or "")
        if ts and stage:
            grouped.setdefault(stage, []).append(ts)
    rows = []
    stages = ["l1_bdf", "l2_core", "l2_advanced", "l2_ingest"]
    if bool(settings.get("include_op2", True)):
        stages.append("rg_op2")
    for stage in stages:
        stamps = grouped.get(stage, [])
        if len(stamps) >= 2:
            elapsed_ms = (max(stamps) - min(stamps)).total_seconds() * 1000.0
            rows.append({
                "version": version["name"], "project_id": project_id,
                "case": f"log_{stage}", "run": 1, "temperature": "log",
                "status": 200, "elapsed_ms": elapsed_ms, "server_ms": None,
                "bytes": 0, "cache": "", 
            })
    return rows


def _project_status(version: dict[str, Any], timeout: float) -> dict[str, Any]:
    raw_project_id = str(version["project_id"])
    project_id = quote(raw_project_id, safe="")
    try:
        return _json_get(version["base_url"], f"api/projects/{project_id}", timeout)
    except HttpRequestError as exc:
        if exc.status != 404:
            raise
        return {
            "project_id": raw_project_id,
            "geom_status": "not_found",
            "result_groups": [],
        }


def _delete_benchmark_project(version: dict[str, Any], settings: dict[str, Any]) -> None:
    """Delete only the explicitly configured benchmark project."""
    timeout = float(settings.get("timeout_seconds", DEFAULT_TIMEOUT))
    project_id = str(version["project_id"])
    project = _project_status(version, min(timeout, 30.0))
    if project.get("geom_status") != "not_found":
        print(f"[{version['name']}] deleting previous benchmark project {project_id}")
    try:
        _json_request(
            version["base_url"],
            f"api/projects/{quote(project_id, safe='')}",
            timeout,
            method="DELETE",
        )
    except HttpRequestError as exc:
        if exc.status != 404:
            raise


def _ensure_service_alive(version: dict[str, Any]) -> None:
    for process_key, log_key, label in (
        ("_app_process", "_app_log", "API process"),
        ("_runner_process", "_runner_log", "job runner"),
    ):
        process = version.get(process_key)
        if process is None or process.poll() is None:
            continue
        log_path = Path(str(version.get(log_key) or ""))
        raise RuntimeError(
            f"{version['name']}: {label} exited with {process.returncode}\n{_tail(log_path)}"
        )


def _poll_project(version: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    """Poll the project status once; a timeout or dropped connection returns None.

    The first GET that sees geom_status=ready loads the model into memory before
    answering, which can outlast the per-poll timeout on large models.
    """
    _ensure_service_alive(version)
    timeout = min(float(settings.get("timeout_seconds", DEFAULT_TIMEOUT)), 30.0)
    try:
        return _project_status(version, timeout)
    except HttpRequestError:
        raise
    except RuntimeError as exc:
        now = time.monotonic()
        if now - float(version.get("_last_poll_warning") or 0.0) >= 10.0:
            print(f"[{version['name']}] status poll failed, retrying: {exc}", file=sys.stderr)
            version["_last_poll_warning"] = now
        return None


def _advanced_status(project: dict[str, Any]) -> str:
    # Versions without staged L2 do not report it: their geometry is complete at ready.
    return str(project.get("advanced_status") or "ready")


def _wait_for_geometry(
    version: dict[str, Any], settings: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    poll = max(0.2, float(settings.get("poll_seconds", 1.0)))
    deadline = time.monotonic() + float(settings.get("watch_timeout_seconds", 7200))
    while time.monotonic() < deadline:
        project = _poll_project(version, settings)
        if project is not None and project.get("geom_status") in {"ready", "error"}:
            return project, time.perf_counter()
        time.sleep(poll)
    raise TimeoutError(f"{version['name']}: BDF import timed out")


def _wait_for_advanced(
    version: dict[str, Any], settings: dict[str, Any]
) -> tuple[str, float]:
    """Wait for staged-L2 advanced indexes so they do not overlap the measurements."""
    poll = max(0.2, float(settings.get("poll_seconds", 1.0)))
    deadline = time.monotonic() + float(settings.get("watch_timeout_seconds", 7200))
    announced = False
    while time.monotonic() < deadline:
        project = _poll_project(version, settings)
        if project is not None:
            status = _advanced_status(project)
            if status not in ADVANCED_BUSY_STATES:
                return status, time.perf_counter()
            if not announced:
                print(f"[{version['name']}] waiting for L2 advanced indexes ({status}) before measuring")
                announced = True
        time.sleep(poll)
    raise TimeoutError(f"{version['name']}: L2 advanced indexes did not finish")


def _wait_for_result_group(
    version: dict[str, Any], settings: dict[str, Any], watch_advanced: bool
) -> dict[str, Any]:
    """Poll until the OP2 result group finishes.

    Also notes when the job runner claims it (status running) and, when asked,
    when the advanced indexes it may be queued behind have finished.
    """
    poll = max(0.2, float(settings.get("poll_seconds", 1.0)))
    deadline = time.monotonic() + float(settings.get("watch_timeout_seconds", 7200))
    target = str(version["result_group"])
    marks: dict[str, Any] = {"running": None, "advanced": None, "advanced_status": None}
    while time.monotonic() < deadline:
        project = _poll_project(version, settings)
        if project is not None:
            now = time.perf_counter()
            if watch_advanced and marks["advanced"] is None:
                advanced = _advanced_status(project)
                if advanced not in ADVANCED_BUSY_STATES:
                    marks["advanced"], marks["advanced_status"] = now, advanced
            match = next(
                (g for g in project.get("result_groups", []) if g.get("result_group") == target),
                None,
            )
            status = str(match.get("status")) if match else "not_found"
            if status == "running" and marks["running"] is None:
                marks["running"] = now
            if status in {"ready", "error"}:
                marks["status"], marks["ready"] = status, now
                return marks
        time.sleep(poll)
    raise TimeoutError(f"{version['name']}: OP2 import timed out")


def _full_ready_row(
    version: dict[str, Any], started: float, ended: float, status: str
) -> dict[str, Any]:
    row = _watch_row(version, "bdf_full_ready_total", started, ended, status)
    print(
        f"[{version['name']}] full geometry incl. L2 advanced indexes: {status}, "
        f"{row['elapsed_ms'] / 1000:.2f} s"
    )
    return row


def _submit_imports(
    version: dict[str, Any], config: dict[str, Any], settings: dict[str, Any]
) -> list[dict[str, Any]]:
    timeout = float(settings.get("timeout_seconds", DEFAULT_TIMEOUT))
    sources = config.get("sources", {})
    include_op2 = bool(settings.get("include_op2", True))
    bdf_path = str(Path(sources["bdf_path"]).resolve())
    op2_path = str(Path(sources["op2_path"]).resolve()) if include_op2 else ""
    project_id = str(version["project_id"])
    name = version["name"]

    if bool(settings.get("delete_existing_project", True)):
        _delete_benchmark_project(version, settings)
    elif _project_status(version, min(timeout, 30.0)).get("geom_status") != "not_found":
        raise RuntimeError(
            f"{name}: project {project_id} already exists; enable delete_existing_project "
            "or choose another benchmark project_id"
        )

    print(f"[{name}] submitting BDF: {bdf_path}")
    bdf_started = time.perf_counter()
    _json_request(
        version["base_url"],
        "api/projects",
        timeout,
        method="POST",
        payload={"project_id": project_id, "source_path": bdf_path, "source_type": "bdf"},
    )
    project, geom_ready = _wait_for_geometry(version, settings)
    geom_status = str(project.get("geom_status"))
    print(f"[{name}] BDF finished: {geom_status}, {geom_ready - bdf_started:.2f} s")
    if geom_status == "error":
        raise RuntimeError(f"{name}: BDF import failed; inspect the service logs")
    rows = [_watch_row(version, "bdf_import_total", bdf_started, geom_ready, geom_status)]
    advanced_status = _advanced_status(project)
    advanced_done = geom_ready if advanced_status not in ADVANCED_BUSY_STATES else None

    if not include_op2:
        if advanced_done is None:
            advanced_status, advanced_done = _wait_for_advanced(version, settings)
        rows.append(_full_ready_row(version, bdf_started, advanced_done, advanced_status))
        return rows

    # Submit OP2 as soon as the geometry is usable, like the frontend does. The
    # job runner is single-threaded, so the OP2 job queues behind any geometry
    # work still running (staged L2 advanced indexes, model-update import).
    result_group = str(
        version.get("result_group") or settings.get("result_group") or "benchmark_op2"
    )
    version["result_group"] = result_group
    print(f"[{name}] submitting OP2: {op2_path}")
    op2_started = time.perf_counter()
    payload: dict[str, Any] = {
        "source_path": op2_path,
        "result_group": result_group,
        "display_name": f"{name} benchmark",
    }
    parse_options = settings.get("op2_parse_options")
    if isinstance(parse_options, dict) and parse_options:
        payload["parse_options"] = parse_options
    _json_request(
        version["base_url"],
        f"api/projects/{quote(project_id, safe='')}/results",
        timeout,
        method="POST",
        payload=payload,
    )
    marks = _wait_for_result_group(version, settings, watch_advanced=advanced_done is None)
    op2_status, op2_ready = marks["status"], marks["ready"]
    print(f"[{name}] OP2 finished: {op2_status}, {op2_ready - op2_started:.2f} s")
    if op2_status == "error":
        raise RuntimeError(f"{name}: OP2 import failed; inspect the service logs")

    if advanced_done is None and marks["advanced"] is not None:
        advanced_status, advanced_done = marks["advanced_status"], marks["advanced"]
    if advanced_done is not None:
        rows.append(_full_ready_row(version, bdf_started, advanced_done, advanced_status))
    else:
        print(
            f"[{name}] L2 advanced indexes still {advanced_status}; bdf_full_ready_total skipped",
            file=sys.stderr,
        )
    running = marks["running"]
    if running is not None:
        print(f"[{name}] OP2 queued {running - op2_started:.2f} s, parsed {op2_ready - running:.2f} s")
        rows.append(_watch_row(version, "op2_queue_wait", op2_started, running, "running"))
        rows.append(_watch_row(version, "op2_parse", running, op2_ready, op2_status))
    else:
        print(f"[{name}] OP2 finished between two polls; queue/parse split unavailable")
    rows.append(_watch_row(version, "op2_import_total", op2_started, op2_ready, op2_status))
    rows.append(_watch_row(version, "end_to_end_total", bdf_started, op2_ready, op2_status))
    return rows


def _watch_imports(versions: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    timeout = float(settings.get("timeout_seconds", DEFAULT_TIMEOUT))
    poll = max(0.2, float(settings.get("poll_seconds", 1.0)))
    deadline = time.monotonic() + float(settings.get("watch_timeout_seconds", 7200))
    state = {
        v["name"]: {"seen": None, "geom": None, "op2": None, "last_warning": 0.0}
        for v in versions
    }
    rows: list[dict[str, Any]] = []
    labels = ", ".join(str(v["name"]) for v in versions)
    include_op2 = bool(settings.get("include_op2", True))
    import_label = "BDF/OP2" if include_op2 else "BDF"
    print(f"Waiting for frontend submission: {labels}. Start the {import_label} import now...")
    while time.monotonic() < deadline:
        all_done = True
        for version in versions:
            item = state[version["name"]]
            try:
                project = _project_status(version, min(timeout, 5.0))
            except RuntimeError as exc:
                now = time.monotonic()
                if now - item["last_warning"] >= 10.0:
                    print(f"[{version['name']}] service unavailable while polling: {exc}", file=sys.stderr)
                    item["last_warning"] = now
                all_done = False
                continue
            now = time.perf_counter()
            geom_status = project.get("geom_status")
            if geom_status != "not_found" and item["seen"] is None:
                item["seen"] = now
                print(f"[{version['name']}] project detected, geom_status={geom_status}")
            if item["seen"] is None or geom_status not in TERMINAL_STATES:
                all_done = False
            elif item["geom"] is None:
                item["geom"] = geom_status
                rows.append(_watch_row(version, "observed_bdf_import", item["seen"], now, geom_status))
                print(f"[{version['name']}] BDF finished: {geom_status}, {(now-item['seen']):.1f} s")

            if not include_op2:
                if item["geom"] not in {"ready", "error"}:
                    all_done = False
                continue

            target_rg = version.get("result_group")
            groups = project.get("result_groups", [])
            matches = [g for g in groups if not target_rg or g.get("result_group") == target_rg]
            rg = matches[0] if matches else None
            if item["geom"] == "ready" and item.get("op2_seen") is None and rg:
                item["op2_seen"] = now
                print(f"[{version['name']}] OP2 result group detected: {rg.get('result_group')}")
            if item["geom"] == "ready" and item.get("op2_seen") is None:
                all_done = False
            elif item.get("op2_seen") is not None and item["op2"] is None:
                rg_status = rg.get("status") if rg else "not_found"
                if rg_status in {"ready", "error"}:
                    item["op2"] = rg_status
                    rows.append(_watch_row(version, "observed_op2_import", item["op2_seen"], now, rg_status))
                    print(f"[{version['name']}] OP2 finished: {rg_status}, {(now-item['op2_seen']):.1f} s")
                else:
                    all_done = False
        if all_done:
            return rows
        time.sleep(poll)
    raise TimeoutError("Import monitoring timed out")


def _watch_row(version, case, started, ended, status):
    return {
        "version": version["name"], "project_id": str(version["project_id"]),
        "case": case, "run": 1, "temperature": "observed",
        "status": status, "elapsed_ms": (ended - started) * 1000.0,
        "server_ms": None, "bytes": 0, "cache": "",
    }


def _fmt_ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} ms"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]


def _summary(rows: list[dict[str, Any]], versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [v["name"] for v in versions]
    cases = list(dict.fromkeys(row["case"] for row in rows))
    output = []
    for case in cases:
        entry: dict[str, Any] = {"case": case}
        for name in names:
            selected = [r for r in rows if r["version"] == name and r["case"] == case]
            first = [float(r["elapsed_ms"]) for r in selected if r["temperature"] != "repeat"]
            repeat = [float(r["elapsed_ms"]) for r in selected if r["temperature"] == "repeat"]
            first_server = [
                float(r["server_ms"]) for r in selected
                if (r["temperature"] != "repeat" and
                    r.get("server_ms") not in (None, ""))
            ]
            repeat_server = [
                float(r["server_ms"]) for r in selected
                if (r["temperature"] == "repeat" and
                    r.get("server_ms") not in (None, ""))
            ]
            entry[name] = {
                "first": _median(first), "repeat": _median(repeat), "p95": _p95(repeat),
                "server_first": _median(first_server),
                "server_repeat": _median(repeat_server),
                "server_p95": _p95(repeat_server),
                "bytes": max((int(r["bytes"]) for r in selected), default=0),
            }
        output.append(entry)
    return output


def _write_outputs(
    rows: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    output_dir: Path,
    source_info: list[dict[str, Any]] | None = None,
    file_tag: str = "benchmark",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"{file_tag}_raw_{stamp}.csv"
    md_path = output_dir / f"{file_tag}_report_{stamp}.md"
    fields = ["version", "project_id", "case", "run", "temperature", "status", "elapsed_ms", "server_ms", "bytes", "cache"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    names = [v["name"] for v in versions]
    summary = _summary(rows, versions)
    lines = [
        "# Version benchmark", "", f"Generated: {datetime.now().isoformat(timespec='seconds')}", "",
        "## Test inputs", "",
        "| Version | Git commit | BDF parser | APP_WARMUP | Project | Service |",
        "|---|---|---|---|---|---|",
    ]
    for version in versions:
        lines.append(
            f"| {version['name']} | {version.get('resolved_commit') or version.get('commit') or '-'} | "
            f"{version.get('bdf_parser') or '-'} | {version.get('warmup_env') or '-'} | "
            f"{version['project_id']} | {version['base_url']} |"
        )
    if source_info:
        lines += ["", "| Source | Size | SHA-256 |", "|---|---:|---|"]
        for item in source_info:
            lines.append(
                f"| {item['path']} | {item['size'] / 1024 / 1024:.2f} MiB | `{item['sha256']}` |"
            )
    lines += ["", "## Coloring coverage", ""]
    for version in versions:
        if "color_code_schemes" not in version:
            lines.append(f"- {version['name']}: not measured yet.")
            continue
        measured = ", ".join(version["color_code_schemes"]) or "none"
        skipped = ", ".join(version.get("skipped_color_code_schemes", [])) or "none"
        lines.append(f"- {version['name']}: color-code measured: {measured}; unavailable/skipped: {skipped}.")
    lines += [
        "", "## Results", "",
        f"| Test | {names[0]} first | {names[1]} first | New/old | Improvement | "
        f"{names[0]} repeat median | {names[1]} repeat median | Payload |", 
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary:
        old, new = item[names[0]], item[names[1]]
        payload_bytes = max(old["bytes"], new["bytes"])
        tiny_http_sample = (
            payload_bytes < 64 * 1024 and
            old["first"] is not None and new["first"] is not None and
            max(old["first"], new["first"]) < 50.0
        )
        ratio = (
            new["first"] / old["first"] * 100.0
            if (old["first"] and new["first"] is not None and not tiny_http_sample and
                item["case"] not in NO_RATIO_CASES)
            else None
        )
        improvement = (100.0 - ratio) if ratio is not None else None
        payload = payload_bytes / 1024 / 1024
        lines.append(
            f"| {item['case']} | {_human_time(old['first'])} | {_human_time(new['first'])} | "
            f"{_pct(ratio)} | {_pct(improvement)} | {_human_time(old['repeat'])} | "
            f"{_human_time(new['repeat'])} | {payload:.2f} MiB |"
        )
    lines += [
        "", "## Backend processing time", "",
        f"| Test | {names[0]} server first | {names[1]} server first | "
        f"{names[0]} server repeat median | {names[1]} server repeat median | "
        "Repeat delta | Repeat improvement |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary:
        old, new = item[names[0]], item[names[1]]
        old_repeat = old["server_repeat"]
        new_repeat = new["server_repeat"]
        repeat_ratio = (
            new_repeat / old_repeat * 100.0
            if (old_repeat and new_repeat is not None and
                max(old_repeat, new_repeat) >= 10.0)
            else None
        )
        repeat_improvement = (
            100.0 - repeat_ratio if repeat_ratio is not None else None)
        repeat_delta = (
            new_repeat - old_repeat
            if old_repeat is not None and new_repeat is not None else None)
        lines.append(
            f"| {item['case']} | {_human_time(old['server_first'])} | "
            f"{_human_time(new['server_first'])} | "
            f"{_human_time(old_repeat)} | {_human_time(new_repeat)} | "
            f"{_human_delta(repeat_delta)} | {_pct(repeat_improvement)} |"
        )
    lines += [
        "", "Notes:", "",
        "- `first` is the first request observed by this script; restart services or clear caches for a true cold test.",
        "- `repeat median` excludes the first request. HTTP time includes downloading the complete response body and reuses one persistent connection per version.",
        "- Backend processing time comes from `X-Elapsed-Time-Ms`; it excludes client/network scheduling and is the better metric for tiny responses such as metadata.",
        "- Sub-10 ms HTTP measurements are sensitive to operating-system scheduling; compare the server repeat median instead of a single percentage.",
        "- HTTP percentages are suppressed for responses below 64 KiB when both versions finish within 50 ms; backend percentages are suppressed below 10 ms. Use the absolute times/delta instead.",
        "- `bdf_import_total` ends when the base geometry is usable; staged-L2 versions publish it before the advanced indexes. `bdf_full_ready_total` also waits for the advanced L2 indexes and equals `bdf_import_total` for single-stage versions.",
        "- OP2 is submitted as soon as the geometry is usable, like the frontend does. The job runner is single-threaded, so the OP2 job may queue behind the remaining geometry work: `op2_queue_wait` is submit to runner claim, `op2_parse` is runner claim to ready, `op2_import_total` is their sum and `end_to_end_total` is BDF submit to OP2 ready. Queue time is waiting rather than parsing cost, so it has no percentage.",
        "- Import timings come from status polling and have about `poll_seconds` precision.",
        "- Auto mode starts every managed service with `APP_WARMUP=0` unless the version's `env` overrides it, so background cache warm-up cannot overlap the measurements. Versions without warm-up ignore the variable.",
        "- `first` is a single sample. Versions with the unified L3 result cache share per-frame vertex displacements between `modal_shape`, `deformed_positions` and `modal_animation`; the cases run in that order, so the later two may reuse what `modal_shape` computed and their `first` is not fully cold.",
        "- `frame_colors_smooth` and `frame_colors_flat` use U/U1 by default. Color-code tests cover available non-elset schemes; absent material/section data is reported as skipped, not measured as an empty success. A simple model cannot represent the cost of many material/section regions.",
        "- A blank `X-Cache` value does not mean caching is disabled: frame-colors and color-code cache intermediate arrays without exposing a hit/miss response header. Compare backend repeat timings as well as HTTP timings.",
        "- `log_*` durations are derived from the first and last timestamps for that stage and have one-second precision.",
        "- Versions run sequentially to avoid CPU/disk contention; the second run may still benefit from the OS file cache.",
        "- Negative improvement means the new version was slower.", "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Raw data: {csv_path.resolve()}")
    print(f"Report:   {md_path.resolve()}")


def _human_time(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.1f} ms"


def _human_delta(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value / 1000:.2f} s" if abs(value) >= 1000 else f"{sign}{value:.1f} ms"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _verify_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    source_info: list[dict[str, Any]] = []
    for key in ("bdf_path", "op2_path"):
        raw = config.get("sources", {}).get(key)
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            print(f"Source warning: {path} does not exist on this machine", file=sys.stderr)
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        item = {
            "kind": key,
            "path": str(path.resolve()),
            "size": path.stat().st_size,
            "sha256": digest.hexdigest(),
        }
        source_info.append(item)
        print(
            f"Source {key}: {path.name}, {item['size'] / 1024 / 1024:.2f} MiB, "
            f"sha256={item['sha256']}"
        )
    return source_info


def _config_path(raw: str, config_dir: Path) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (config_dir / path).resolve()


def _command_output(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Command failed ({' '.join(args)}): {detail}")
    return completed.stdout.strip()


def _prepare_version_repo(
    version: dict[str, Any], automation: dict[str, Any], config_dir: Path
) -> Path:
    source_repo = _config_path(str(automation.get("repository_path", "..")), config_dir)
    commit = str(version.get("commit") or "").strip()
    worktree_raw = version.get("worktree_path")
    repo_raw = version.get("repo_path")

    if worktree_raw:
        if not commit:
            raise ValueError(f"{version['name']}: commit is required with worktree_path")
        repo_path = _config_path(str(worktree_raw), config_dir)
        if not repo_path.exists():
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[{version['name']}] creating isolated worktree at {repo_path}")
            _command_output(
                ["git", "worktree", "add", "--detach", str(repo_path), commit],
                cwd=source_repo,
            )
        actual = _command_output(["git", "rev-parse", "HEAD"], cwd=repo_path)
        expected = _command_output(["git", "rev-parse", commit], cwd=source_repo)
        if actual != expected:
            raise RuntimeError(
                f"{version['name']}: existing worktree is at {actual}, expected {expected}; "
                "choose a new worktree_path or update it manually"
            )
        dirty = _command_output(["git", "status", "--porcelain"], cwd=repo_path)
        if dirty:
            raise RuntimeError(f"{version['name']}: old-version worktree has local changes")
    else:
        repo_path = _config_path(str(repo_raw or automation.get("repository_path", "..")), config_dir)
        if not (repo_path / "app.py").is_file():
            raise RuntimeError(f"{version['name']}: app.py not found in {repo_path}")
        actual = _command_output(["git", "rev-parse", "HEAD"], cwd=repo_path)
        if commit:
            expected = _command_output(["git", "rev-parse", commit], cwd=repo_path)
            if actual != expected:
                raise RuntimeError(
                    f"{version['name']}: repo_path is at {actual}, expected configured commit {expected}"
                )
        if _command_output(["git", "status", "--porcelain"], cwd=repo_path):
            actual += " (dirty)"

    version["resolved_commit"] = actual
    version["resolved_repo_path"] = str(repo_path)
    return repo_path


def _is_numeric_id(value: Any) -> bool:
    # Same test the service uses before touching MySQL (int(project_id)).
    try:
        int(str(value))
    except ValueError:
        return False
    return True


def _service_port(base_url: str) -> int:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid base_url: {base_url}")
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("auto mode only starts local services (127.0.0.1 or localhost)")
    return int(parsed.port or (443 if parsed.scheme == "https" else 80))


def _tail(path: Path, limit: int = 30) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:])
    except OSError:
        return ""


def _wait_for_service(
    version: dict[str, Any], app_process: subprocess.Popen, app_log: Path, startup_timeout: float
) -> None:
    deadline = time.monotonic() + startup_timeout
    health_url = _url(version["base_url"], "api/health/live")
    while time.monotonic() < deadline:
        if app_process.poll() is not None:
            raise RuntimeError(
                f"{version['name']}: API process exited with {app_process.returncode}\n{_tail(app_log)}"
            )
        try:
            _request(health_url, 3.0)
            print(f"[{version['name']}] service is ready at {version['base_url']}")
            return
        except RuntimeError:
            time.sleep(0.5)
    raise TimeoutError(f"{version['name']}: service startup timed out; log: {app_log}")


def _start_managed_service(
    version: dict[str, Any],
    automation: dict[str, Any],
    config_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    repo_path = _prepare_version_repo(version, automation, config_dir)
    port = _service_port(str(version["base_url"]))
    runtime_root = _config_path(
        str(automation.get("runtime_root", "../benchmark_runtime")), config_dir
    )
    slug_source = f"{version['name']}_{version['project_id']}"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", slug_source).strip("_") or "version"
    run_id = str(automation.get("_run_id") or f"{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}")
    version_runtime = runtime_root / "runs" / run_id / slug
    data_root = version_runtime / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    version_runtime.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "APP_HOST": "127.0.0.1",
        "APP_PORT": str(port),
        "APP_BASE_URL": str(version["base_url"]).rstrip("/"),
        "APP_DATA_ROOT": str(data_root),
        "APP_REGISTRY_DB_PATH": str(version_runtime / "registry.db"),
        "APP_EMBEDDED_RUNNER": "0",
        "JOB_RUNNER_POLL_INTERVAL": str(automation.get("runner_poll_seconds", 1)),
        # Background cache warm-up would run during the measurements. Versions
        # without warm-up ignore this; a version's env can set it back to 1.
        "APP_WARMUP": "0",
    })
    source_repo = _config_path(str(automation.get("repository_path", "..")), config_dir)
    config_file_raw = automation.get("service_config")
    service_config = (
        _config_path(str(config_file_raw), config_dir)
        if config_file_raw
        else source_repo / "service_config.json"
    )
    if service_config.is_file():
        env["CONFIG_FILE"] = str(service_config)
    configured_env = version.get("env", {})
    if configured_env and not isinstance(configured_env, dict):
        raise ValueError(f"{version['name']}: env must be an object")
    env.update({str(k): str(v) for k, v in configured_env.items()})
    version["warmup_env"] = env["APP_WARMUP"]

    log_dir = output_dir / "service_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    app_log = log_dir / f"{slug}_app_{stamp}.log"
    runner_log = log_dir / f"{slug}_runner_{stamp}.log"
    app_handle = app_log.open("w", encoding="utf-8")
    runner_handle = runner_log.open("w", encoding="utf-8")
    python_exe = str(automation.get("python_executable") or sys.executable)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    processes: list[subprocess.Popen] = []
    try:
        print(f"[{version['name']}] starting API and job runner from {repo_path}")
        app_process = subprocess.Popen(
            [python_exe, "app.py"],
            cwd=str(repo_path),
            env=env,
            stdout=app_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        processes.append(app_process)
        # The API creates the registry schema on startup. Starting the runner
        # first lets it query a brand-new SQLite database before tables exist.
        _wait_for_service(
            version,
            app_process,
            app_log,
            float(automation.get("startup_timeout_seconds", 180)),
        )
        runner_process = subprocess.Popen(
            [python_exe, "src/job_runner.py"],
            cwd=str(repo_path),
            env=env,
            stdout=runner_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        processes.append(runner_process)
        if runner_process.poll() is not None:
            raise RuntimeError(
                f"{version['name']}: job runner exited with {runner_process.returncode}\n"
                f"{_tail(runner_log)}"
            )
        version["_app_process"] = app_process
        version["_app_log"] = str(app_log)
        version["_runner_process"] = runner_process
        version["_runner_log"] = str(runner_log)
        print(f"[{version['name']}] logs: {app_log} ; {runner_log}")
        return {
            "processes": processes,
            "handles": [app_handle, runner_handle],
            "app_log": app_log,
            "runner_log": runner_log,
        }
    except Exception:
        _stop_managed_service({"processes": processes, "handles": [app_handle, runner_handle]})
        raise


def _stop_managed_service(service: dict[str, Any]) -> None:
    processes = list(reversed(service.get("processes", [])))
    if os.name == "nt":
        for process in processes:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    else:
        for process in processes:
            if process.poll() is None:
                process.terminate()
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    for handle in service.get("handles", []):
        handle.close()


def _detect_bdf_parser(runner_log: Path) -> str:
    try:
        text = runner_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    if "Fast BDF scan done." in text:
        return "fast path"
    if "Fast BDF path: fallback to pyNastran" in text:
        return "pyNastran (fast-path fallback)"
    if "Reading BDF" in text:
        return "pyNastran"
    return "unknown"


def _run_auto_sequential(
    config: dict[str, Any],
    versions: list[dict[str, Any]],
    settings: dict[str, Any],
    config_dir: Path,
    output_dir: Path,
    source_info: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    automation = config.get("automation", {})
    automation["_run_id"] = f"{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}"
    rows: list[dict[str, Any]] = []
    for index, version in enumerate(versions):
        print("\n" + "=" * 72)
        print(f"Automatic sequential phase {index + 1}/2: {version['name']}")
        service: dict[str, Any] | None = None
        try:
            service = _start_managed_service(version, automation, config_dir, output_dir)
            rows.extend(_submit_imports(version, config, settings))
            version["bdf_parser"] = _detect_bdf_parser(service["runner_log"])
            print(f"[{version['name']}] BDF parser used: {version['bdf_parser']}")
            rows.extend(_log_stage_rows(version, settings))
            rows.extend(_benchmark_version(version, settings))
            _write_outputs(
                rows,
                versions,
                output_dir,
                source_info,
                file_tag=f"benchmark_{'old' if index == 0 else 'complete'}_checkpoint",
            )
            if bool(settings.get("delete_project_after", True)):
                _delete_benchmark_project(version, settings)
        finally:
            if service is not None:
                _stop_managed_service(service)
                print(f"[{version['name']}] managed service stopped")
        if index == 0:
            cooldown = max(0.0, float(automation.get("between_versions_seconds", 2.0)))
            if cooldown:
                time.sleep(cooldown)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare old/new VirtualReal702 API timings")
    parser.add_argument("--config", required=True, help="JSON configuration path")
    parser.add_argument(
        "--mode",
        choices=("benchmark", "watch", "all", "sequential", "auto"),
        default="benchmark",
        help=(
            "benchmark=measure ready projects; watch=only monitor imports; "
            "all=monitor two simultaneously then benchmark; "
            "sequential=manually switch services on the same port; "
            "auto=start services, submit imports, and test old/new sequentially"
        ),
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    for source_key in ("bdf_path", "op2_path"):
        source_value = config.get("sources", {}).get(source_key)
        if source_value:
            source_path = Path(source_value)
            if not source_path.is_absolute():
                config["sources"][source_key] = str((config_path.parent / source_path).resolve())
    versions = config.get("versions", [])
    if len(versions) != 2:
        raise ValueError("config.versions must contain exactly two entries: old, then new")
    for index, version in enumerate(versions):
        version.setdefault("name", "old" if index == 0 else "new")
        if not version.get("base_url") or not version.get("project_id"):
            raise ValueError("each version requires base_url and project_id")
    settings = config.get("benchmark", {})
    include_op2 = bool(settings.get("include_op2", True))
    print("Test scope: " + (
        "BDF + OP2 + geometry/color/result endpoints" if include_op2
        else "BDF + geometry/color endpoints"
    ))
    source_info = _verify_sources(config)
    if args.mode == "auto":
        numeric_ids = [str(v["project_id"]) for v in versions if _is_numeric_id(v["project_id"])]
        if numeric_ids:
            raise ValueError(
                f"auto mode requires non-numeric project_id values, got {', '.join(numeric_ids)}: "
                "the managed services use the real MySQL from service_config.json, and the "
                "service purges/overwrites model-update rows of numeric project ids on delete/import"
            )
        required_sources = {"bdf_path"} | ({"op2_path"} if include_op2 else set())
        missing_sources = [
            key for key in sorted(required_sources)
            if not config.get("sources", {}).get(key)
            or not Path(config["sources"][key]).is_file()
        ]
        if missing_sources:
            raise ValueError(f"auto mode requires existing source files: {', '.join(missing_sources)}")
    output_dir = Path(settings.get("output_dir", "benchmark_results"))
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir

    rows: list[dict[str, Any]] = []
    if args.mode == "auto":
        rows = _run_auto_sequential(
            config,
            versions,
            settings,
            config_path.parent,
            output_dir,
            source_info,
        )
    elif args.mode == "sequential":
        for index, version in enumerate(versions):
            print("\n" + "=" * 72)
            print(f"Sequential phase {index + 1}/2: {version['name']}")
            print(f"Expected service: {version['base_url']}, project: {version['project_id']}")
            rows.extend(_watch_imports([version], settings))
            _wait_for_advanced(version, settings)
            rows.extend(_log_stage_rows(version, settings))
            rows.extend(_benchmark_version(version, settings))
            if index == 0:
                _write_outputs(rows, versions, output_dir, file_tag="benchmark_old_checkpoint")
                print("\nOld-version measurements are saved.")
                print("Stop the old service, start the new service on the same port, then import the new project.")
                print("The script will keep polling the new project ID; no Enter key is required.")
    else:
        if args.mode in ("watch", "all"):
            rows.extend(_watch_imports(versions, settings))
        if args.mode in ("benchmark", "all"):
            for version in versions:
                _wait_for_advanced(version, settings)
                rows.extend(_log_stage_rows(version, settings))
                rows.extend(_benchmark_version(version, settings))

    _write_outputs(rows, versions, output_dir, source_info)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, TimeoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
