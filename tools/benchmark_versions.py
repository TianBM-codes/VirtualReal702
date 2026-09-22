#!/usr/bin/env python3
"""Compare two running VirtualReal702 services without changing either service.

Typical workflow:
  1. Configure old/new base_url and project_id in a JSON file.
  2. Create/import both projects with the same frontend and source files.
  3. Run: python tools/benchmark_versions.py --config benchmark_versions.json

The script streams binary responses instead of retaining them in memory. Results
are written as raw CSV plus a compact Markdown comparison report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT = 1800.0
TERMINAL_STATES = {"ready", "error", "not_found"}


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


def _request(url: str, timeout: float, *, collect_json: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    size = 0
    body = bytearray() if collect_json else None
    try:
        with urlopen(Request(url, headers={"Accept-Encoding": "identity"}), timeout=timeout) as response:
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


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_get(base_url: str, path: str, timeout: float, params=None) -> Any:
    return _request(_url(base_url, path, params), timeout, collect_json=True)["json"]


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
) -> dict[str, str]:
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
    return {
        "project_id": project_id,
        "instance": str(instance),
        "step": str(step or ""),
        "result_group": str(result_group or ""),
    }


def _cases(ctx: dict[str, str], settings: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
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
    ]
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
    return cases


def _benchmark_version(version: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
    timeout = float(settings.get("timeout_seconds", DEFAULT_TIMEOUT))
    repeat = max(1, int(settings.get("repeat", 5)))
    ctx = _discover(version, timeout, settings)
    print(
        f"[{version['name']}] project={ctx['project_id']} instance={ctx['instance']} "
        f"step={ctx['step']} result_group={ctx['result_group'] or '-'}"
    )
    rows = []
    for case_name, path, params in _cases(ctx, settings):
        for run in range(1, repeat + 1):
            result = _request(_url(version["base_url"], path, params), timeout)
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
    try:
        data = _json_get(
            version["base_url"], f"api/projects/{quote(project_id, safe='')}/logs", timeout,
            {"since_id": 0, "limit": 1000},
        )
    except HttpRequestError as exc:
        if exc.status != 404:
            raise
        print(f"[{version['name']}] project logs are not available; skipping log-derived timings.")
        return []
    grouped: dict[str, list[datetime]] = {}
    for item in data.get("logs", []):
        ts = _parse_ts(item.get("ts", ""))
        stage = str(item.get("stage") or "")
        if ts and stage:
            grouped.setdefault(stage, []).append(ts)
    rows = []
    stages = ["l1_bdf", "l2_ingest"]
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
            entry[name] = {
                "first": _median(first), "repeat": _median(repeat), "p95": _p95(repeat),
                "bytes": max((int(r["bytes"]) for r in selected), default=0),
            }
        output.append(entry)
    return output


def _write_outputs(
    rows: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    output_dir: Path,
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
        f"| Test | {names[0]} first | {names[1]} first | New/old | Improvement | "
        f"{names[0]} repeat median | {names[1]} repeat median | Payload |", 
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary:
        old, new = item[names[0]], item[names[1]]
        ratio = (new["first"] / old["first"] * 100.0) if old["first"] and new["first"] is not None else None
        improvement = (100.0 - ratio) if ratio is not None else None
        payload = max(old["bytes"], new["bytes"]) / 1024 / 1024
        lines.append(
            f"| {item['case']} | {_human_time(old['first'])} | {_human_time(new['first'])} | "
            f"{_pct(ratio)} | {_pct(improvement)} | {_human_time(old['repeat'])} | "
            f"{_human_time(new['repeat'])} | {payload:.2f} MiB |"
        )
    lines += [
        "", "Notes:", "",
        "- `first` is the first request observed by this script; restart services or clear caches for a true cold test.",
        "- `repeat median` excludes the first request. HTTP time includes downloading the complete response body.",
        "- `log_*` durations are derived from the first and last timestamps for that stage and have one-second precision.",
        "- Negative improvement means the new version was slower.", "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Raw data: {csv_path.resolve()}")
    print(f"Report:   {md_path.resolve()}")


def _human_time(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.1f} ms"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _verify_sources(config: dict[str, Any]) -> None:
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
        print(f"Source {key}: {path.name}, {path.stat().st_size / 1024 / 1024:.2f} MiB, sha256={digest.hexdigest()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare old/new VirtualReal702 API timings")
    parser.add_argument("--config", required=True, help="JSON configuration path")
    parser.add_argument(
        "--mode",
        choices=("benchmark", "watch", "all", "sequential"),
        default="benchmark",
        help=(
            "benchmark=measure ready projects; watch=only monitor imports; "
            "all=monitor two simultaneously then benchmark; "
            "sequential=old and new use the same port, one service at a time"
        ),
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    versions = config.get("versions", [])
    if len(versions) != 2:
        raise ValueError("config.versions must contain exactly two entries: old, then new")
    for index, version in enumerate(versions):
        version.setdefault("name", "old" if index == 0 else "new")
        if not version.get("base_url") or not version.get("project_id"):
            raise ValueError("each version requires base_url and project_id")
    settings = config.get("benchmark", {})
    include_op2 = bool(settings.get("include_op2", True))
    print("Test scope: " + ("BDF + OP2 + result endpoints" if include_op2 else "BDF only"))
    _verify_sources(config)
    output_dir = Path(settings.get("output_dir", "benchmark_results"))
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir

    rows: list[dict[str, Any]] = []
    if args.mode == "sequential":
        for index, version in enumerate(versions):
            print("\n" + "=" * 72)
            print(f"Sequential phase {index + 1}/2: {version['name']}")
            print(f"Expected service: {version['base_url']}, project: {version['project_id']}")
            rows.extend(_watch_imports([version], settings))
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
                rows.extend(_log_stage_rows(version, settings))
                rows.extend(_benchmark_version(version, settings))

    _write_outputs(rows, versions, output_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, TimeoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
