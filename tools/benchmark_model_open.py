#!/usr/bin/env python3
"""Measure the viewer's model-open HTTP chain, including lazy index loading.

Uses benchmark_versions' isolated services. Each version may point APP_DATA_ROOT
and APP_REGISTRY_DB_PATH at an existing benchmark run, or import sources.odb_path.
This measures full downloads, not JavaScript parsing, GPU upload or drawing.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

if __package__:
    from . import benchmark_versions as bench
else:
    import benchmark_versions as bench


def stage_odb(source: Path, output: Path):
    """Give the service a verified private copy: legacy upgrades replace inputs."""
    started = time.perf_counter()
    before = source.stat()
    inputs = output / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="odb-input-", dir=inputs))
    destination = directory / source.name
    shutil.copy2(source, destination)

    def digest(path):
        value = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()

    original_hash = digest(source)
    if original_hash != digest(destination):
        raise RuntimeError("Staged ODB does not match the original")
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("Source ODB changed while staging")
    source_info = {"path": str(source), "bytes": before.st_size,
                   "mtime_ns": before.st_mtime_ns, "sha256": original_hash}
    return destination, source_info, (time.perf_counter() - started) * 1000


def download(session, url, timeout):
    started = time.perf_counter()
    digest = hashlib.sha256()
    hash_seconds = 0.0
    size = 0
    header = bytearray()
    with session.get(url, stream=True, timeout=timeout,
                     headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        for chunk in response.iter_content(1024 * 1024):
            if len(header) < 40:
                header.extend(chunk[:40 - len(header)])
            hash_started = time.perf_counter()
            digest.update(chunk)
            hash_seconds += time.perf_counter() - hash_started
            size += len(chunk)
        if bytes(header[:4]) != b"L3BE" or size < 40:
            raise RuntimeError(f"Invalid geometry envelope: {url}")
        return {
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "server_ms": bench._float_or_none(response.headers.get("X-Elapsed-Time-Ms")),
            "bytes": size, "sha256": digest.hexdigest(),
            "sha256_ms": hash_seconds * 1000,
            "face_count": response.headers.get("X-Face-Count"),
            "edge_count": response.headers.get("X-Edge-Count"),
            "chunk_count": response.headers.get("X-Chunk-Count"),
        }


def measure_open(version, settings):
    base = version["base_url"]
    project = quote(str(version["project_id"]), safe="")
    timeout = float(settings.get("timeout_seconds", 1800))
    rows = []
    signatures = {}
    with requests.Session() as session:
        for run in range(1, max(1, int(settings.get("repeat", 3))) + 1):
            started = time.perf_counter()
            result = bench._request(
                bench._url(base, f"api/odb/{project}/meta/overview"),
                timeout, collect_json=True, session=session,
            )
            overview = result.pop("json")
            if run == 1:
                version["_open_overview"] = overview
            instances = [i.get("instance_name") or i.get("name")
                         for i in overview["instances"]]
            if not instances or any(not name for name in instances):
                raise RuntimeError("Overview has no valid instances; refusing an empty timing")
            common = {"version": version["name"], "run": run,
                      "temperature": "first" if run == 1 else "repeat"}
            rows.append({**common, "case": "open_metadata", **result})
            # Serial downloads isolate backend work. The current async backend
            # performs this work on one event loop; browser concurrency differs.
            for kind in ("feature-edges", "render-buffers-chunked"):
                phase_start = time.perf_counter()
                face_count = 0
                for instance in instances:
                    path = f"api/odb/{project}/geometry/{quote(instance, safe='')}/{kind}"
                    item = download(session, bench._url(base, path), timeout)
                    signature = (item["bytes"], item["sha256"], item["face_count"],
                                 item["edge_count"], item["chunk_count"])
                    key = (kind, instance)
                    if run > 1 and signatures.get(key) != signature:
                        raise RuntimeError(f"Geometry changed between requests: {key}")
                    signatures[key] = signature
                    face_count += int(item["face_count"] or 0)
                    rows.append({**common, "case": kind, "instance": instance, **item})
                    print(f"[{version['name']}] {kind} {instance} [{run}]: "
                          f"{item['elapsed_ms']:.1f} ms, {item['bytes']} bytes", flush=True)
                if kind == "render-buffers-chunked" and face_count == 0:
                    raise RuntimeError("No surface faces returned; refusing an empty-model speedup")
                rows.append({**common, "case": f"all_{kind}",
                             "elapsed_ms": (time.perf_counter() - phase_start) * 1000})
                if kind == "feature-edges":
                    rows.append({**common, "case": "open_outline_http_total",
                                 "elapsed_ms": (time.perf_counter() - started) * 1000})
            total = (time.perf_counter() - started) * 1000
            rows.append({**common, "case": "open_surface_http_total", "elapsed_ms": total})
            print(f"[{version['name']}] complete open [{run}]: {total:.1f} ms", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    settings = config["benchmark"]
    output = bench._config_path(settings["output_dir"], config_path.parent)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output / f"model_open_{stamp}.json"
    automation = config["automation"]
    automation["_run_id"] = f"model_open_{stamp}"
    report = {"config": copy.deepcopy(config), "notes": [
        "APP_PRELOAD_PROJECTS=0, APP_WARMUP=0; first overview includes lazy ModelIndex load.",
        "OS file cache is not flushed; first is a process/request cold measurement.",
        "All instances, serial HTTP downloads; excludes browser parsing, GPU upload and drawing.",
        "Downloads include streaming SHA-256 verification time, also recorded per response.",
        "Repeat opens share the same service; import time excludes model-update database work.",
        "ODB input is copied and verified per version before timing; automatic upgrade changes only the private copy.",
    ], "versions": [], "models": {}, "rows": []}

    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                               encoding="utf-8")

    for version in config["versions"]:
        if bench._is_numeric_id(version["project_id"]):
            raise ValueError("Only non-numeric benchmark project IDs are permitted")
        env = version.setdefault("env", {})
        env.update(APP_PRELOAD_PROJECTS="0", APP_WARMUP="0")
        service = None
        try:
            service = bench._start_managed_service(version, automation, config_path.parent, output)
            version_meta = {k: v for k, v in version.items() if not k.startswith("_")}
            report["versions"].append(version_meta)
            save()
            odb = config.get("sources", {}).get("odb_path")
            if odb:
                source = bench._config_path(odb, config_path.parent)
                if not source.is_file():
                    raise ValueError(f"ODB does not exist: {source}")
                staged, source_info, staging_ms = stage_odb(source, output)
                if report.get("source", source_info) != source_info:
                    raise RuntimeError("Source ODB changed between versions")
                report["source"] = source_info
                version_meta["staged_odb_path"] = str(staged)
                report["rows"].append({"version": version["name"], "case": "odb_input_staging",
                                       "elapsed_ms": staging_ms})
                save()
                project = bench._project_status(version, 30)
                if project.get("geom_status") != "not_found":
                    raise ValueError("Fresh ODB benchmark requires a new project ID/data directory")
                started = time.perf_counter()
                bench._json_request(version["base_url"], "api/projects", 60, method="POST",
                                    payload={"project_id": version["project_id"],
                                             "source_path": str(staged), "source_type": "odb"})
                project, ready = bench._wait_for_geometry(version, settings)
                if project["geom_status"] != "ready":
                    raise RuntimeError("ODB import failed; inspect runner log")
                report["rows"].append({"version": version["name"], "case": "odb_import_ready",
                                       "elapsed_ms": (ready - started) * 1000})
                bench._wait_for_advanced(version, settings)
                # Status polling materializes ModelIndex. Restart the API against
                # exactly these files before measuring the first open.
                bench._stop_managed_service(service)
                service = None
                service = bench._start_managed_service(version, automation, config_path.parent, output)
            report["rows"].extend(measure_open(version, settings))
            report["models"][version["name"]] = version["_open_overview"]
            save()
            if settings.get("include_endpoint_suite", False):
                suite = dict(settings)
                if suite.get("suite_instance") == "largest_surface":
                    surfaces = [r for r in report["rows"] if r.get("version") == version["name"]
                                and r.get("case") == "render-buffers-chunked" and r.get("run") == 1]
                    version["instance"] = max(surfaces, key=lambda r: int(r["face_count"] or 0))["instance"]
                ctx = bench._discover(version, float(suite.get("timeout_seconds", 1800)), suite)
                if suite.get("skip_nonmodal_animations", True):
                    overview = bench._json_get(
                        version["base_url"],
                        f"api/odb/{quote(str(version['project_id']), safe='')}/meta/overview", 60,
                        {"result_group": ctx["result_group"]},
                    )
                    step = next(s for s in overview["steps"] if s["step_name"] == ctx["step"])
                    if str(step.get("procedure", "")).upper() not in {"FREQUENCY", "BUCKLE"}:
                        suite["exclude_cases"] = list(dict.fromkeys([
                            *suite.get("exclude_cases", []), "modal_shape", "modal_animation",
                        ]))
                    version["measured_step"] = step
                version["excluded_cases"] = suite.get("exclude_cases", [])
                if suite.get("frame") == "last":
                    frames = bench._json_get(
                        version["base_url"],
                        f"api/odb/{quote(str(version['project_id']), safe='')}/steps/"
                        f"{quote(ctx['step'], safe='')}/frames", 60,
                        {"result_group": ctx["result_group"]},
                    )
                    suite["frame"] = max(int(f["frame_idx"]) for f in frames)
                    version["measured_frame"] = suite["frame"]
                report["rows"].extend(bench._benchmark_version(version, suite))
            version_meta.update({k: v for k, v in version.items() if not k.startswith("_")})
            save()
        except Exception as exc:
            report["error"] = {"version": version["name"], "message": repr(exc)}
            save()
            raise
        finally:
            if service:
                bench._stop_managed_service(service)
    print(f"Model-open results: {report_path}", flush=True)


if __name__ == "__main__":
    main()
