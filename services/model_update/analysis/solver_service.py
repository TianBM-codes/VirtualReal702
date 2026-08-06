import os
import re
import shutil
import subprocess
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.l3.core.config import settings
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.infra.registry_repo import RegistryRepo

from ..importers.op2_service import build_modal_import_payload
from .model_update_meta_service import resolve_abaqus_command, resolve_nastran_command
from .l3_local_bridge_service import (
    project_workspace_path as _local_project_workspace_path,
    submit_project_result_group_and_wait as _submit_local_project_result_group_and_wait,
)
from .project_log_service import (
    log_project_error,
    log_project_info,
    log_project_step,
)
from .project_status_service import update_work_condition_project_status
from .project_file_service import (
    resolve_project_input_file as _resolve_shared_project_input_file,
    resolve_project_output_dir,
    resolve_project_output_file,
)
from ..solver_prep.abaqus_adjoint import generate_adjoint_shell_thickness_inp
from ..solver_prep.nastran_sol103 import (
    build_sol103_controls,
    convert_to_sol103,
    filter_bulk_lines,
    read_lines,
    split_bdf,
)
from ..solver_prep.nastran_sol200 import convert_to_sol200, build_sol200_lines
from ..solver_prep.abaqus_sensitivity import generate_sensitivity_inp

# This module stays at the "local solver orchestration" layer:
# it validates file paths, generates derived analysis decks when needed,
# launches the external solver process, and collects the generated artifacts.
_ABAQUS_ARTIFACT_SUFFIXES = (
    ".inp",
    ".odb",
    ".dat",
    ".msg",
    ".sta",
    ".com",
    ".prt",
    ".sim",
    ".log",
)

_ABAQUS_PROCESS_FILE_SUFFIXES = (
    ".com",
    ".prt",
    ".pmg",
    ".pes",
    ".par",
    ".msg",
    ".sta",
    ".dat",
    ".lck",
)

_NASTRAN_ARTIFACT_SUFFIXES = (
    ".bdf",
    ".f06",
    ".op2",
    ".xdb",
    ".log",
    ".out",
    ".plt",
    ".h5",
    ".csv",
)

_NASTRAN_EXTRA_GLOB_PATTERNS = (
    "*.op2",
    "*.f06",
    "*.xdb",
    "*.h5",
    "*.csv",
    "*.pch",
    "*.plt",
    "*.out",
    "*.log",
    "*.bin",
    "*.u??",
    "*.f??",
)

_NASTRAN_BINARY_SUFFIXES = {
    ".op2",
    ".xdb",
    ".h5",
    ".pch",
    ".plt",
    ".bin",
}

_DSA_RESPONSE_TOKEN_RE = re.compile(r"^d_([A-Z0-9_]+?)(?:_[A-Z]+)?_?$", re.IGNORECASE)
_ABAQUS_ERROR_MARKERS = (
    "abaqus error:",
    "abaqus/analysis exited with error",
    "abaqus/analysis exited with errors",
    "analysis input file processor exited with an error",
)


def _normalize_result_group_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    if not text:
        raise ValidationError("result_group name cannot be empty", {"value": value})
    return text[:96]


def _default_solver_project_result_group(job_name: str) -> str:
    return _normalize_result_group_name(
        f"solver_result_{job_name}_{int(time.time())}"
    )


def _field_prefix_response_token(field_prefix: Optional[str]) -> Optional[str]:
    prefix = str(field_prefix or "").strip()
    if not prefix:
        return None
    match = _DSA_RESPONSE_TOKEN_RE.fullmatch(prefix)
    if not match:
        return None
    return str(match.group(1)).upper()


def _build_project_result_parse_options(
        *,
        step: Optional[str],
        frame: Optional[int],
        field_prefix: Optional[str],
) -> dict:
    parse_options = {
        "consistency_check": "count-only",
        "steps": [str(step)] if step else None,
        "frames": [int(frame)] if frame is not None else "all",
        "invariants": "none",
    }

    # DSA uploads need both the sensitivity fields (for example d_U_T1) and
    # their base response field (for example U). Filtering by the DSA prefix
    # here strips the response field from the extracted workspace and breaks
    # later normalization/merge logic.
    if field_prefix and _field_prefix_response_token(field_prefix) is None:
        parse_options["field_prefix"] = str(field_prefix)
    return {key: value for key, value in parse_options.items() if value is not None}


def _submit_generic_project_result_group_and_wait(
        *,
        project_id: int,
        source_path: str,
        job_name: str,
        result_group: Optional[str],
        display_name: Optional[str],
        base_url: Optional[str],
        parse_options: Optional[dict],
        timeout: int,
        wait_timeout_sec: int,
        poll_interval_sec: float,
) -> dict:
    resolved_source_path = os.path.abspath(str(source_path))
    if not os.path.exists(resolved_source_path):
        raise NotFoundError("result file not found", {"source_path": resolved_source_path})

    resolved_result_group = _normalize_result_group_name(
        result_group or _default_solver_project_result_group(job_name)
    )
    result = _submit_local_project_result_group_and_wait(
        project_id=int(project_id),
        source_path=resolved_source_path,
        result_group=resolved_result_group,
        display_name=display_name or resolved_result_group,
        parse_options=parse_options,
    )
    result.update(
        {
            "base_url": str(base_url or "").strip().rstrip("/"),
            "wait_timeout_sec": max(int(wait_timeout_sec or 0), 1),
            "poll_interval_sec": max(float(poll_interval_sec or 0), 0.1),
            "timeout": max(int(timeout or 0), 60),
        }
    )
    return result


def _submit_project_result_group_and_wait(
        *,
        project_id: int,
        odb_path: str,
        job_name: str,
        result_group: Optional[str],
        display_name: Optional[str],
        base_url: Optional[str],
        step: Optional[str],
        frame: Optional[int],
        field_prefix: Optional[str],
        timeout: int,
        wait_timeout_sec: int,
        poll_interval_sec: float,
) -> dict:
    parse_options = _build_project_result_parse_options(
        step=step,
        frame=frame,
        field_prefix=field_prefix,
    )
    return _submit_generic_project_result_group_and_wait(
        project_id=project_id,
        source_path=odb_path,
        job_name=job_name,
        result_group=result_group,
        display_name=display_name,
        base_url=base_url,
        parse_options=parse_options,
        timeout=timeout,
        wait_timeout_sec=wait_timeout_sec,
        poll_interval_sec=poll_interval_sec,
    )


def _abs_file(path: str, field_name: str) -> Path:
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise NotFoundError(f"未找到 {field_name}: {file_path}", {field_name: str(file_path)})
    if not file_path.is_file():
        raise ValidationError(f"{field_name} 必须是文件", {field_name: str(file_path)})
    return file_path


def _project_workspace(project_id: int) -> Path:
    return Path(_local_project_workspace_path(int(project_id))).expanduser().resolve()


def _update_simulation_result_status(project_id: Optional[int], status: int) -> None:
    if project_id is None:
        return
    update_work_condition_project_status(
        int(project_id),
        simulation_result_status=int(status),
    )


def _detect_project_source_type(input_path: Path) -> str:
    suffix = str(input_path.suffix or "").strip().lower()
    if suffix == ".inp":
        return "inp"
    if suffix == ".bdf":
        return "bdf"
    raise ValidationError(
        "unsupported input file type; only .inp and .bdf are allowed",
        {"input_file": str(input_path)},
    )


def _ensure_project_ready(
    *,
    project_id: int,
    source_path: str,
    source_type: str,
    wait_timeout_sec: int,
    poll_interval_sec: float,
) -> dict:
    repo = RegistryRepo(settings.registry_db_path)
    project_key = str(int(project_id))
    workspace = _project_workspace(int(project_id))
    source_abs = os.path.abspath(str(source_path))
    started_at = time.monotonic()
    poll_interval = max(float(poll_interval_sec or 0), 0.1)
    wait_timeout = max(int(wait_timeout_sec or 0), 1)

    row = repo.get_project(project_key)
    if row is None:
        workspace.mkdir(parents=True, exist_ok=True)
        repo.create_project(
            project_id=project_key,
            workspace=project_key,
            inp_path=source_abs,
            source_type=source_type,
        )
    else:
        existing_source_type = str(row["source_type"] or "").strip().lower()
        if existing_source_type and existing_source_type != source_type:
            raise ValidationError(
                "existing project source_type does not match input file type",
                {
                    "project_id": int(project_id),
                    "existing_source_type": existing_source_type,
                    "requested_source_type": source_type,
                    "input_file": source_abs,
                },
            )

    while True:
        row = repo.get_project(project_key)
        if row is None:
            raise NotFoundError(
                "project disappeared while waiting for geometry ready",
                {"project_id": int(project_id)},
            )

        geom_status = str(row["geom_status"] or "").strip().lower()
        if geom_status == "ready":
            return {
                "project_id": int(project_id),
                "workspace": str(workspace),
                "geom_status": geom_status,
                "source_type": source_type,
            }
        if geom_status == "error":
            raise ValidationError(
                "project geometry parsing failed",
                {
                    "project_id": int(project_id),
                    "geom_status": geom_status,
                    "source_type": source_type,
                },
            )

        if time.monotonic() - started_at >= wait_timeout:
            raise ValidationError(
                "waiting project geometry ready timed out",
                {
                    "project_id": int(project_id),
                    "geom_status": geom_status,
                    "wait_timeout_sec": wait_timeout,
                    "source_type": source_type,
                },
            )
        time.sleep(poll_interval)


def _resolve_project_file(project_id: int, path: str, field_name: str) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise ValidationError(
            f"field name: {field_name}",
            {"project_id": int(project_id), field_name: path},
        )
    return _resolve_shared_project_input_file(
        int(project_id),
        explicit_path=raw,
        file_name=None,
        field_name=field_name,
    )

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return _abs_file(str(candidate), field_name)

    workspace = _project_workspace(project_id)
    resolved = (workspace / candidate).resolve()
    try:
        common = os.path.commonpath([str(workspace), str(resolved)])
    except ValueError as exc:
        raise ValidationError(
            f"{field_name} 当前project workspace field name",
            {"project_id": int(project_id), field_name: raw, "workspace": str(workspace)},
        ) from exc
    if common != str(workspace):
        raise ValidationError(
            f"{field_name} 当前project workspace field_name",
            {"project_id": int(project_id), field_name: raw, "workspace": str(workspace)},
        )
    return _abs_file(str(resolved), field_name)


def _abs_dir(path: Optional[str], fallback: Path) -> Path:
    directory = Path(path).expanduser().resolve() if path else fallback.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sanitize_job_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", (name or "").strip())
    value = value.strip("._-")
    return value or "job"


def _tail_text(text: str, limit: int = 4000) -> str:
    if not text:
        return ""
    return text[-limit:]


def _normalize_extra_args(extra_args: Optional[List[str]]) -> List[str]:
    return [str(item) for item in (extra_args or []) if str(item).strip()]


def _collect_artifacts(workdir: Path, stem: str, suffixes) -> Dict[str, str]:
    # Solver outputs are discovered by stem so the API can return whatever
    # was actually produced on disk without hard-coding every workflow result.
    artifacts = {}
    for suffix in suffixes:
        path = workdir / f"{stem}{suffix}"
        if path.exists():
            artifacts[suffix.lstrip(".")] = str(path.resolve())
    return artifacts


def _collect_nastran_extra_artifacts(workdir: Path, artifacts: Dict[str, str]) -> Dict[str, str]:
    resolved = dict(artifacts)
    for pattern in _NASTRAN_EXTRA_GLOB_PATTERNS:
        for path in workdir.glob(pattern):
            if not path.is_file():
                continue
            key = path.name
            if key not in resolved:
                resolved[key] = str(path.resolve())
    return resolved


def _materialize_requested_sensitivity_csv(
    *,
    workdir: Path,
    generated_files: Dict[str, str],
    solver_payload: Dict[str, Any],
) -> None:
    target = generated_files.get("sensitivity_csv")
    assign_name = generated_files.get("sensitivity_csv_assign_name")
    if not target or not assign_name:
        return
    internal_path = (workdir / assign_name).resolve()
    if not internal_path.exists() or not internal_path.is_file():
        return
    target_path = Path(target).expanduser().resolve()
    if internal_path != target_path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(internal_path), str(target_path))
    artifacts = solver_payload.get("artifacts") or {}
    artifacts.setdefault(target_path.name, str(target_path))
    if internal_path != target_path:
        artifacts.setdefault(internal_path.name, str(internal_path))
    solver_payload["artifacts"] = artifacts


def _cleanup_sol200_intermediate_artifacts(
    *,
    workdir: Path,
    generated_files: Dict[str, str],
    solver_payload: Dict[str, Any],
) -> List[str]:
    deleted: List[str] = []
    candidates: List[Path] = []
    assign_name = str(generated_files.get("sensitivity_csv_assign_name") or "").strip()
    target_csv = str(generated_files.get("sensitivity_csv") or "").strip()
    target_csv_path = Path(target_csv).expanduser().resolve() if target_csv else None

    if assign_name:
        candidates.append((workdir / assign_name).resolve())
    for extra_name in ("fort.11", "fort.51", "fort.91", "fort.92"):
        candidates.append((workdir / extra_name).resolve())

    seen: set[str] = set()
    for candidate in candidates:
        candidate_text = str(candidate)
        if candidate_text in seen:
            continue
        seen.add(candidate_text)
        if target_csv_path is not None and candidate == target_csv_path:
            continue
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            candidate.unlink()
            deleted.append(str(candidate))
        except OSError:
            continue

    if deleted:
        artifacts = dict(solver_payload.get("artifacts") or {})
        deleted_set = set(deleted)
        artifacts = {
            key: value
            for key, value in artifacts.items()
            if str(value) not in deleted_set
        }
        solver_payload["artifacts"] = artifacts
        solver_payload["artifacts_summary"] = _summarize_nastran_artifacts(artifacts)
    return deleted


def _ensure_sol200_include_dependencies(*, workdir: Path, generated_files: Dict[str, str]) -> List[str]:
    copied: List[str] = []
    design_bdf = str(generated_files.get("design_model_bdf") or "").strip()
    if not design_bdf:
        return copied

    source_path = Path(design_bdf).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise ValidationError(
            "SOL200 include mode requires design_model.bdf, but the generated source file was not found",
            {"design_model_bdf": design_bdf, "workdir": str(workdir)},
        )

    target_path = (workdir / "design_model.bdf").resolve()
    if source_path != target_path:
        shutil.copy2(str(source_path), str(target_path))
        copied.append(str(target_path))
    elif not target_path.exists():
        shutil.copy2(str(source_path), str(target_path))
        copied.append(str(target_path))
    return copied


def _summarize_nastran_artifacts(artifacts: Dict[str, str]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "has_f06": False,
        "has_op2": False,
        "f06_files": [],
        "op2_files": [],
        "unit11_candidates": [],
        "binary_result_files": [],
        "all_files": sorted(str(path) for path in artifacts.values()),
    }
    for path_text in sorted(set(str(path) for path in artifacts.values())):
        path = Path(path_text)
        suffix = path.suffix.lower()
        name = path.name.lower()
        if suffix == ".f06":
            summary["has_f06"] = True
            summary["f06_files"].append(str(path))
        if suffix == ".op2":
            summary["has_op2"] = True
            summary["op2_files"].append(str(path))
        if suffix in _NASTRAN_BINARY_SUFFIXES:
            summary["binary_result_files"].append(str(path))
        if not suffix and ("unit11" in name or name.endswith("11")):
            summary["unit11_candidates"].append(str(path))
    return summary


def delete_abaqus_process_files(workdir: Path, job_name: str) -> List[str]:
    deleted: List[str] = []
    for suffix in _ABAQUS_PROCESS_FILE_SUFFIXES:
        path = (workdir / f"{job_name}{suffix}").resolve()
        if not path.exists() or not path.is_file():
            continue
        path.unlink()
        deleted.append(str(path))
    return deleted


def _cleanup_stale_abaqus_job_files(workdir: Path, job_name: str) -> List[str]:
    """
    Remove stale Abaqus process-side artifacts before launching a fresh job.

    In repeated local debugging runs, Abaqus may leave behind a `.lck` after a
    forced stop, which blocks the next submission even though no solver process
    is still writing to the ODB.
    """
    return delete_abaqus_process_files(workdir, job_name)


def _run_local_solver(
    command: List[str],
    workdir: Path,
    artifact_stem: str,
    artifact_suffixes,
    timeout_sec: Optional[int] = None,
) -> dict:
    # All solver wrappers eventually funnel through this helper so timeout,
    # stdout/stderr capture, and artifact collection behave consistently.
    if timeout_sec is not None and int(timeout_sec) <= 0:
        raise ValidationError("timeout_sec 必须大于 0", {"timeout_sec": timeout_sec})

    print(command)
    try:
        result = subprocess.run(
            command,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=int(timeout_sec) if timeout_sec is not None else None,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValidationError(
            f"未找到求解器可执行文件: {command[0]}",
            {"command": command, "workdir": str(workdir)},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(
            "求解器执行超时",
            {
                "command": command,
                "workdir": str(workdir),
                "timeout_sec": timeout_sec,
                "stdout_tail": _tail_text(exc.stdout or ""),
                "stderr_tail": _tail_text(exc.stderr or ""),
            },
        ) from exc

    payload = {
        "ok": result.returncode == 0,
        "returncode": int(result.returncode),
        "command": command,
        "workdir": str(workdir),
        "stdout_tail": _tail_text(result.stdout),
        "stderr_tail": _tail_text(result.stderr),
        "artifacts": _collect_artifacts(workdir, artifact_stem, artifact_suffixes),
    }
    if _looks_like_abaqus_command(command) and _abaqus_output_has_error_markers(
        result.stdout,
        result.stderr,
    ):
        payload["ok"] = False
    if artifact_suffixes is _NASTRAN_ARTIFACT_SUFFIXES:
        payload["artifacts"] = _collect_nastran_extra_artifacts(workdir, payload["artifacts"])
        payload["artifacts_summary"] = _summarize_nastran_artifacts(payload["artifacts"])
    return payload


def _looks_like_abaqus_command(command: List[str]) -> bool:
    if not command:
        return False
    token = Path(str(command[0])).name.strip().lower()
    return "abaqus" in token or token.startswith("abq")


def _abaqus_output_has_error_markers(stdout_text: Optional[str], stderr_text: Optional[str]) -> bool:
    haystack = f"{stdout_text or ''}\n{stderr_text or ''}".lower()
    return any(marker in haystack for marker in _ABAQUS_ERROR_MARKERS)


def _copy_missing_relative_includes(*, source_inp: Path, generated_inp: Path, target_dir: Path) -> List[str]:
    include_pattern = re.compile(r"^\*include\s*,\s*input\s*=\s*(.+?)\s*$", re.IGNORECASE)
    copied: List[str] = []
    text = generated_inp.read_text(encoding="utf-8", errors="ignore").splitlines()
    for raw_line in text:
        match = include_pattern.match(raw_line.strip())
        if not match:
            continue
        include_ref = match.group(1).strip().strip("\"'")
        include_path = Path(include_ref)
        if include_path.is_absolute():
            continue
        source_path = (source_inp.parent / include_path).resolve()
        if not source_path.exists() or not source_path.is_file():
            continue
        target_path = (target_dir / include_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not target_path.exists():
            shutil.copy2(source_path, target_path)
            copied.append(str(target_path))
    return copied


def _build_abaqus_command(
    abaqus: Optional[str],
    inp_path: Path,
    job_name: str,
    cpus: Optional[int] = None,
    interactive: bool = True,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    # Abaqus is invoked in the directory that contains the generated input deck,
    # so the command only needs the file name, not the full absolute path.
    command = [resolve_abaqus_command(abaqus), f"job={job_name}", f"input={inp_path.name}"]
    if interactive:
        command.append("interactive")
    if cpus is not None:
        if int(cpus) <= 0:
            raise ValidationError("cpus 必须大于 0", {"cpus": cpus})
        command.append(f"cpus={int(cpus)}")
    command.extend(_normalize_extra_args(extra_args))
    return command


def _build_nastran_command(
    nastran: Optional[str],
    bdf_path: Path,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    command = [resolve_nastran_command(nastran), bdf_path.name]
    command.extend(_normalize_extra_args(extra_args))
    return command


def preview_nastran_sol103_job(
    input_bdf: str,
    settings: Optional[Dict[str, Any]] = None,
) -> dict:
    input_path = _abs_file(input_bdf, "input_bdf")
    lines = read_lines(str(input_path))
    _, bulk_lines = split_bdf(lines)
    controls, has_bailout = build_sol103_controls(dict(settings or {}))
    filtered_bulk = filter_bulk_lines(bulk_lines, has_bailout)

    return {
        "workflow": "nastran_sol103_preview",
        "input_bdf": str(input_path),
        "control_lines_preview": controls,
        "result_target": str((settings or {}).get("result.target", "OP2")).upper(),
        "filtered_cards": {
            "original_bulk_line_count": len(bulk_lines),
            "filtered_bulk_line_count": len(filtered_bulk),
            "removed_bulk_line_count": max(0, len(bulk_lines) - len(filtered_bulk)),
            "has_bailout": bool(has_bailout),
        },
        "warnings": [],
    }


def generate_nastran_sol103_job(
    input_bdf: str,
    output_bdf: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> dict:
    input_path = _abs_file(input_bdf, "input_bdf")
    output_path = Path(output_bdf).expanduser().resolve() if output_bdf else input_path.with_name(
        f"{input_path.stem}_sol103.bdf"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    convert_to_sol103(
        input_bdf=str(input_path),
        output_bdf=str(output_path),
        settings=dict(settings or {}),
    )

    return {
        "workflow": "nastran_sol103_generate",
        "input_bdf": str(input_path),
        "output_bdf": str(output_path),
        "result_target": str((settings or {}).get("result.target", "OP2")).upper(),
        "generated_files": {
            "analysis_bdf": str(output_path),
        },
        "warnings": [],
    }


def preview_nastran_sol200_job(
    input_bdf: str,
    parameters: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> dict:
    input_path = _abs_file(input_bdf, "input_bdf")
    payload = build_sol200_lines(
        input_bdf=str(input_path),
        parameters=list(parameters or []),
        responses=list(responses or []),
        settings=dict(settings or {}),
        output_bdf=str(input_path.with_name(f"{input_path.stem}_sol200.bdf")),
        output_design_bdf=str(input_path.with_name("design_model.bdf")),
    )
    return {
        "workflow": "nastran_sol200_preview",
        "input_bdf": str(input_path),
        "result_target": str((settings or {}).get("result.target", "OP2")).upper(),
        "deck_mode": payload.get("deck_mode", "inline"),
        "sensitivity_csv_path": payload.get("sensitivity_csv_path"),
        "control_lines_preview": payload["control_lines"],
        "desvar_preview": payload["desvar_lines"],
        "relation_preview": payload["relation_lines"],
        "response_preview": payload["response_lines"],
        "design_model_preview": payload.get("design_lines") or [],
        "warnings": [],
    }


def generate_nastran_sol200_job(
    input_bdf: str,
    output_bdf: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> dict:
    input_path = _abs_file(input_bdf, "input_bdf")
    output_path = Path(output_bdf).expanduser().resolve() if output_bdf else input_path.with_name(
        f"{input_path.stem}_sol200.bdf"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    deck_mode = str((settings or {}).get("sol200.deck_mode", "inline")).strip().lower()
    design_output_path = output_path.with_name("design_model.bdf") if deck_mode == "include" else None

    payload = convert_to_sol200(
        input_bdf=str(input_path),
        output_bdf=str(output_path),
        output_design_bdf=str(design_output_path) if design_output_path else None,
        parameters=list(parameters or []),
        responses=list(responses or []),
        settings=dict(settings or {}),
    )
    metadata_path = output_path.with_suffix(output_path.suffix + ".sol200.json")
    metadata = {
        "input_bdf": str(input_path),
        "output_bdf": str(output_path),
        "parameters": list(parameters or []),
        "responses": list(responses or []),
        "settings": dict(settings or {}),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "workflow": "nastran_sol200_generate",
        "input_bdf": str(input_path),
        "output_bdf": str(output_path),
        "result_target": str((settings or {}).get("result.target", "OP2")).upper(),
        "deck_mode": payload.get("deck_mode", deck_mode),
        "parameter_count": len(list(parameters or [])),
        "response_count": len(list(responses or [])),
        "generated_files": {
            "analysis_bdf": str(output_path),
            "metadata_json": str(metadata_path),
        },
        "preview": {
            "control_lines_preview": payload["control_lines"],
            "desvar_preview": payload["desvar_lines"],
            "relation_preview": payload["relation_lines"],
            "response_preview": payload["response_lines"],
            "design_model_preview": payload.get("design_lines") or [],
        },
        "warnings": [],
    }
    if payload.get("sensitivity_csv_path"):
        result["generated_files"]["sensitivity_csv"] = str(payload["sensitivity_csv_path"])
    if payload.get("sensitivity_csv_assign_name"):
        result["generated_files"]["sensitivity_csv_assign_name"] = str(payload["sensitivity_csv_assign_name"])
    if payload.get("output_design_bdf"):
        result["generated_files"]["design_model_bdf"] = str(payload["output_design_bdf"])
    if payload.get("deck_mode") == "include":
        result["generated_files"]["include_bdf"] = str(output_path)
    return result


def run_nastran_sol200_job(
    input_bdf: str,
    output_bdf: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> dict:
    generated = generate_nastran_sol200_job(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=parameters,
        responses=responses,
        settings=settings,
    )
    output_path = Path(generated["output_bdf"]).resolve()
    command = _build_nastran_command(
        nastran=nastran,
        bdf_path=output_path,
        extra_args=extra_args,
    )
    payload = {
        "workflow": "nastran_sol200_run",
        "input_bdf": generated["input_bdf"],
        "output_bdf": generated["output_bdf"],
        "generated_files": generated.get("generated_files") or {},
        "parameter_count": generated.get("parameter_count"),
        "response_count": generated.get("response_count"),
        "command_preview": command,
        "solver": None,
        "warnings": [],
    }
    if run_solver:
        copied_include_files = _ensure_sol200_include_dependencies(
            workdir=output_path.parent,
            generated_files=payload.get("generated_files") or {},
        )
        if copied_include_files:
            payload["generated_files"]["copied_include_files"] = copied_include_files
        payload["solver"] = _run_local_solver(
            command=command,
            workdir=output_path.parent,
            artifact_stem=output_path.stem,
            artifact_suffixes=_NASTRAN_ARTIFACT_SUFFIXES,
            timeout_sec=timeout_sec,
        )
        _materialize_requested_sensitivity_csv(
            workdir=output_path.parent,
            generated_files=payload.get("generated_files") or {},
            solver_payload=payload["solver"],
        )
        payload["solver"]["cleanup_deleted"] = _cleanup_sol200_intermediate_artifacts(
            workdir=output_path.parent,
            generated_files=payload.get("generated_files") or {},
            solver_payload=payload["solver"],
        )
        summary = payload["solver"].get("artifacts_summary") or {}
        if not summary.get("has_op2"):
            payload["warnings"].append({
                "code": "NASTRAN_OP2_NOT_FOUND",
                "message": "solve completed without an OP2 file; inspect result.target/post settings and produced binary files",
            })
    return payload


def run_abaqus_sensitivity_job(
    input_inp: str,
    output_dir: Optional[str] = None,
    response_elset: Optional[str] = None,
    response_nset: Optional[str] = None,
    response_frequency: int = 1,
    node_vars: Optional[List[str]] = None,
    element_vars: Optional[List[str]] = None,
    abaqus: Optional[str] = None,
    job_name: Optional[str] = None,
    cpus: Optional[int] = None,
    interactive: bool = True,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> dict:
    # This workflow has two phases:
    # 1. generate the sensitivity-ready INP files
    # 2. optionally execute Abaqus on the generated analysis input
    input_path = _abs_file(input_inp, "input_inp")
    target_dir = _abs_dir(output_dir, input_path.parent)

    design_param_path, sensitivity_inp_path = generate_sensitivity_inp(
        input_inp=str(input_path),
        output_dir=str(target_dir),
        response_elset=response_elset,
        response_nset=response_nset,
        response_frequency=int(response_frequency),
        node_vars=node_vars,
        element_vars=element_vars,
    )
    generated_inp = Path(sensitivity_inp_path).resolve()
    copied_include_files = _copy_missing_relative_includes(
        source_inp=input_path,
        generated_inp=generated_inp,
        target_dir=target_dir,
    )
    resolved_job_name = _sanitize_job_name(job_name or generated_inp.stem)
    command = _build_abaqus_command(
        abaqus=abaqus,
        inp_path=generated_inp,
        job_name=resolved_job_name,
        cpus=cpus,
        interactive=interactive,
        extra_args=extra_args,
    )

    payload = {
        "workflow": "abaqus_sensitivity",
        "input_inp": str(input_path),
        "output_dir": str(target_dir),
        "job_name": resolved_job_name,
        "generated_files": {
            "design_parameter_inp": str(Path(design_param_path).resolve()),
            "analysis_inp": str(generated_inp),
        },
        "command_preview": command,
        "solver": None,
    }
    if copied_include_files:
        payload["generated_files"]["copied_include_files"] = copied_include_files
    if run_solver:
        payload["stale_process_files_deleted"] = _cleanup_stale_abaqus_job_files(
            target_dir,
            resolved_job_name,
        )
        # Returning both the command preview and the execution result makes it
        # easier to debug solver startup issues separately from deck generation.
        payload["solver"] = _run_local_solver(
            command=command,
            workdir=target_dir,
            artifact_stem=resolved_job_name,
            artifact_suffixes=_ABAQUS_ARTIFACT_SUFFIXES,
            timeout_sec=timeout_sec,
        )
    return payload


def run_abaqus_job(
    input_inp: str,
    output_dir: Optional[str] = None,
    abaqus: Optional[str] = None,
    job_name: Optional[str] = None,
    project_id: Optional[int] = None,
    cpus: Optional[int] = None,
    interactive: bool = True,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> dict:
    input_path = _abs_file(input_inp, "input_inp")
    target_dir = _abs_dir(output_dir, input_path.parent)

    staged_inp = (target_dir / input_path.name).resolve()
    copied_input = False
    if input_path.resolve() != staged_inp:
        shutil.copyfile(str(input_path), str(staged_inp))
        copied_input = True

    resolved_job_name = _sanitize_job_name(job_name or staged_inp.stem)
    command = _build_abaqus_command(
        abaqus=abaqus,
        inp_path=staged_inp,
        job_name=resolved_job_name,
        cpus=cpus,
        interactive=interactive,
        extra_args=extra_args,
    )

    payload = {
        "workflow": "abaqus_inp",
        "input_inp": str(input_path),
        "output_dir": str(target_dir),
        "job_name": resolved_job_name,
        "generated_files": {
            "analysis_inp": str(staged_inp),
        },
        "copied_input_inp": copied_input,
        "command_preview": command,
        "solver": None,
    }
    if run_solver:
        payload["stale_process_files_deleted"] = _cleanup_stale_abaqus_job_files(
            target_dir,
            resolved_job_name,
        )
        payload["solver"] = _run_local_solver(
            command=command,
            workdir=target_dir,
            artifact_stem=resolved_job_name,
            artifact_suffixes=_ABAQUS_ARTIFACT_SUFFIXES,
            timeout_sec=timeout_sec,
        )
        if not bool((payload.get("solver") or {}).get("ok")):
            _update_simulation_result_status(project_id, 2)
    return payload


def run_abaqus_adjoint_job(
    input_inp: str,
    output_inp: Optional[str] = None,
    response_nset: Optional[str] = None,
    abaqus: Optional[str] = None,
    job_name: Optional[str] = None,
    cpus: Optional[int] = None,
    interactive: bool = True,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> dict:
    # The adjoint workflow rewrites the source INP into a dedicated job deck.
    # The caller can stop after generation, or continue directly into solve.
    input_path = _abs_file(input_inp, "input_inp")
    output_path = Path(output_inp).expanduser().resolve() if output_inp else input_path.with_name(
        f"{input_path.stem}_adjoint_thickness.inp"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generated_path = Path(
        generate_adjoint_shell_thickness_inp(
            input_inp=str(input_path),
            output_inp=str(output_path),
            response_nset=response_nset,
        )
        or output_path
    ).resolve()

    resolved_job_name = _sanitize_job_name(job_name or generated_path.stem)
    command = _build_abaqus_command(
        abaqus=abaqus,
        inp_path=generated_path,
        job_name=resolved_job_name,
        cpus=cpus,
        interactive=interactive,
        extra_args=extra_args,
    )

    payload = {
        "workflow": "abaqus_adjoint_shell",
        "input_inp": str(input_path),
        "output_inp": str(generated_path),
        "job_name": resolved_job_name,
        "generated_files": {
            "analysis_inp": str(generated_path),
        },
        "command_preview": command,
        "solver": None,
    }
    if run_solver:
        payload["solver"] = _run_local_solver(
            command=command,
            workdir=generated_path.parent,
            artifact_stem=resolved_job_name,
            artifact_suffixes=_ABAQUS_ARTIFACT_SUFFIXES,
            timeout_sec=timeout_sec,
        )
    return payload


def run_nastran_sol103_job(
    input_bdf: str,
    output_bdf: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> dict:
    # Nastran differs from the Abaqus helpers: the preprocessing step converts
    # an existing BDF into a SOL103 deck before the optional solve.
    input_path = _abs_file(input_bdf, "input_bdf")
    output_path = Path(output_bdf).expanduser().resolve() if output_bdf else input_path.with_name(
        f"{input_path.stem}_sol103.bdf"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    convert_to_sol103(
        input_bdf=str(input_path),
        output_bdf=str(output_path),
        settings=dict(settings or {}),
    )

    command = _build_nastran_command(
        nastran=nastran,
        bdf_path=output_path,
        extra_args=extra_args,
    )

    payload = {
        "workflow": "nastran_sol103_run",
        "input_bdf": str(input_path),
        "output_bdf": str(output_path),
        "command_preview": command,
        "solver": None,
        "warnings": [],
    }
    if run_solver:
        payload["solver"] = _run_local_solver(
            command=command,
            workdir=output_path.parent,
            artifact_stem=output_path.stem,
            artifact_suffixes=_NASTRAN_ARTIFACT_SUFFIXES,
            timeout_sec=timeout_sec,
        )
        summary = payload["solver"].get("artifacts_summary") or {}
        if not summary.get("has_op2"):
            payload["warnings"].append({
                "code": "NASTRAN_OP2_NOT_FOUND",
                "message": "solve completed without an OP2 file; inspect result.target/post settings and produced binary files",
            })
    return payload


def run_nastran_sol103_and_store_modal_results(
    *,
    project_id: int,
    input_bdf: str,
    output_bdf: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    overwrite: bool = True,
    subcase_id: Optional[int] = None,
    mode_numbers: Optional[List[int]] = None,
    instance_name: Optional[str] = None,
    part_name: Optional[str] = None,
) -> dict:
    # Delay this import to avoid the startup cycle:
    # inp_service -> sensitivity_service -> solver_service -> inp_service.
    from .inp_service import import_fe_modal_results

    solver_payload = run_nastran_sol103_job(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        settings=settings,
        nastran=nastran,
        run_solver=True,
        timeout_sec=timeout_sec,
        extra_args=extra_args,
    )
    solver = solver_payload.get("solver") or {}
    if not solver.get("ok", False):
        raise ValidationError(
            "nastran SOL103 solve failed; modal results were not stored",
            {
                "project_id": int(project_id),
                "solver": solver,
            },
        )

    summary = solver.get("artifacts_summary") or {}
    op2_files = list(summary.get("op2_files") or [])
    if not op2_files:
        raise NotFoundError(
            "op2 file not found after SOL103 solve",
            {
                "project_id": int(project_id),
                "artifacts_summary": summary,
            },
        )
    resolved_op2_path = os.path.abspath(str(op2_files[0]))
    payload = build_modal_import_payload(
        op2_path=resolved_op2_path,
        bdf_path=solver_payload.get("output_bdf") or input_bdf,
        subcase_id=subcase_id,
        mode_numbers=mode_numbers,
        instance_name=instance_name,
        part_name=part_name,
        all_subcases=subcase_id is None,
    )
    stored = import_fe_modal_results(
        project_id=project_id,
        overwrite=overwrite,
        modes=payload["modes"],
    )
    stored["warnings"] = payload.get("warnings") or []
    return {
        "workflow": "nastran_sol103_run_and_store_modal",
        "project_id": int(project_id),
        "input_bdf": solver_payload.get("input_bdf"),
        "output_bdf": solver_payload.get("output_bdf"),
        "op2_path": resolved_op2_path,
        "solver": solver,
        "store": stored,
    }


def run_solver_and_parse_project_result(
    *,
    project_id: int,
    input_file: Optional[str] = None,
    input_file_name: Optional[str] = None,
    job_name: Optional[str] = None,
    result_group: Optional[str] = None,
    display_name: Optional[str] = None,
    base_url: Optional[str] = None,
    output_dir: Optional[str] = None,
    output_dir_name: Optional[str] = None,
    output_bdf: Optional[str] = None,
    output_bdf_name: Optional[str] = None,
    abaqus: Optional[str] = None,
    nastran: Optional[str] = None,
    cpus: Optional[int] = None,
    interactive: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    settings: Optional[Dict[str, Any]] = None,
    step: Optional[str] = None,
    frame: Optional[int] = None,
    field_prefix: Optional[str] = None,
    upload_timeout: int = 60,
    wait_timeout_sec: int = 3600,
    poll_interval_sec: float = 2.0,
) -> dict:
    log_project_step(int(project_id), "统一计算并解析开始", stage="solver_run_and_parse", percent=0)
    source_type: Optional[str] = None
    try:
        resolved_input = str(input_file or "").strip() or str(input_file_name or "").strip()
        resolved_field_name = "input_file" if str(input_file or "").strip() else "input_file_name"
        if not resolved_input:
            raise ValidationError(
                "input_file or input_file_name is required",
                {
                    "project_id": int(project_id),
                    "input_file": input_file,
                    "input_file_name": input_file_name,
                },
            )
        input_path = _resolve_project_file(project_id, resolved_input, resolved_field_name)
        source_type = _detect_project_source_type(input_path)
        resolved_output_dir = str(output_dir or "").strip() or str(output_dir_name or "").strip()
        if source_type == "inp":
            output_dir = str(
                resolve_project_output_dir(
                    int(project_id),
                    category_parts=("solver", "abaqus"),
                    explicit_dir=resolved_output_dir or None,
                )
            )
        elif resolved_output_dir:
            output_dir = resolved_output_dir
        resolved_output_bdf = str(output_bdf or "").strip() or str(output_bdf_name or "").strip()
        if source_type == "bdf":
            output_bdf = str(
                resolve_project_output_file(
                    int(project_id),
                    category_parts=("solver", "nastran_sol103"),
                    explicit_path=resolved_output_bdf or None,
                    default_name=f"{input_path.stem}_sol103.bdf",
                    field_name="output_bdf",
                )
            )
        elif resolved_output_bdf:
            output_bdf = resolved_output_bdf
        log_project_info(
            int(project_id),
            f"已解析输入文件路径: {input_path.name}",
            stage="path_resolved",
            percent=5,
        )

        log_project_step(
            int(project_id),
            f"工程项目id: {project_id} 输入计算文件格式后缀: ({source_type})",
            stage="project_prepare",
            percent=10,
        )
        project_info = _ensure_project_ready(
            project_id=int(project_id),
            source_path=str(input_path),
            source_type=source_type,
            wait_timeout_sec=wait_timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )
        log_project_step(
            int(project_id),
            f"workspace-name {Path(project_info['workspace']).name}",
            stage="project_ready",
            percent=12,
        )

        if source_type == "inp":
            log_project_step(int(project_id), "开始执行 Abaqus 求解", stage="solver_started", percent=15)
            solver_payload = run_abaqus_job(
                input_inp=str(input_path),
                output_dir=output_dir,
                abaqus=abaqus,
                job_name=job_name,
                project_id=int(project_id),
                cpus=cpus,
                interactive=interactive,
                run_solver=True,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
            )
            solver = solver_payload.get("solver") or {}
            if not solver.get("ok", False):
                raise ValidationError(
                    "abaqus solve failed; project result upload was not started",
                    {"project_id": int(project_id), "solver": solver},
                )
            result_file = solver.get("artifacts", {}).get("odb")
            if not result_file:
                raise NotFoundError(
                    "odb file not found after Abaqus solve",
                    {
                        "project_id": int(project_id),
                        "artifacts": solver.get("artifacts") or {},
                    },
                )
            resolved_job_name = str(solver_payload.get("job_name") or job_name or input_path.stem)
            log_project_step(
                int(project_id),
                f"Abaqus 求解完成，开始上传 ODB 结果组: {Path(result_file).name}",
                stage="result_upload_started",
                percent=70,
            )
            upload = _submit_project_result_group_and_wait(
                project_id=project_id,
                odb_path=result_file,
                job_name=resolved_job_name,
                result_group=result_group,
                display_name=display_name,
                base_url=base_url,
                step=step,
                frame=frame,
                field_prefix=field_prefix,
                timeout=upload_timeout,
                wait_timeout_sec=wait_timeout_sec,
                poll_interval_sec=poll_interval_sec,
            )
            log_project_step(
                int(project_id),
                f"结果组解析完成: {upload['result_group']}",
                stage="result_upload_finished",
                percent=100,
            )
            return {
                "workflow": "run_and_parse",
                "project_id": int(project_id),
                "source_type": "inp",
                "solver_type": "abaqus",
                "project_status": "not_checked",
                "job_name": resolved_job_name,
                "result_group": upload["result_group"],
                "result_group_status": upload["status"],
                "artifacts": {
                    "input_file": str(input_path),
                    "result_file": os.path.abspath(str(result_file)),
                },
                "solver": solver,
                "upload": upload,
            }

        if source_type == "bdf":
            log_project_step(int(project_id), "开始执行 Nastran SOL103 求解", stage="solver_started", percent=15)
            solver_payload = run_nastran_sol103_job(
                input_bdf=str(input_path),
                output_bdf=output_bdf,
                settings=dict(settings or {}),
                nastran=nastran,
                run_solver=True,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
            )
            solver = solver_payload.get("solver") or {}
            if not solver.get("ok", False):
                raise ValidationError(
                    "nastran SOL103 solve failed; project result upload was not started",
                    {"project_id": int(project_id), "solver": solver},
                )
            summary = solver.get("artifacts_summary") or {}
            op2_files = list(summary.get("op2_files") or [])
            if not op2_files:
                raise NotFoundError(
                    "op2 file not found after SOL103 solve",
                    {
                        "project_id": int(project_id),
                        "artifacts_summary": summary,
                    },
                )
            result_file = os.path.abspath(str(op2_files[0]))
            resolved_job_name = str(job_name or Path(solver_payload.get("output_bdf") or input_path).stem)
            log_project_step(
                int(project_id),
                f"Nastran 求解完成，开始上传 OP2 结果组: {Path(result_file).name}",
                stage="result_upload_started",
                percent=70,
            )
            upload = _submit_generic_project_result_group_and_wait(
                project_id=project_id,
                source_path=result_file,
                job_name=resolved_job_name,
                result_group=result_group,
                display_name=display_name,
                base_url=base_url,
                parse_options=None,
                timeout=upload_timeout,
                wait_timeout_sec=wait_timeout_sec,
                poll_interval_sec=poll_interval_sec,
            )
            log_project_step(
                int(project_id),
                f"结果组解析完成: {upload['result_group']}",
                stage="result_upload_finished",
                percent=100,
            )
            return {
                "workflow": "run_and_parse",
                "project_id": int(project_id),
                "source_type": "bdf",
                "solver_type": "nastran",
                "project_status": "not_checked",
                "job_name": resolved_job_name,
                "result_group": upload["result_group"],
                "result_group_status": upload["status"],
                "artifacts": {
                    "input_file": str(input_path),
                    "result_file": result_file,
                    "analysis_bdf": str(solver_payload.get("output_bdf")),
                },
                "solver": solver,
                "upload": upload,
            }

        raise ValidationError(
            "unsupported input file type; only .inp and .bdf are allowed",
            {"input_file": str(input_path)},
        )
    except Exception as exc:
        if source_type == "inp":
            try:
                _update_simulation_result_status(project_id, 2)
            except Exception:
                pass
        log_project_error(int(project_id), f"统一计算并解析失败: {exc}", stage="failed")
        raise
