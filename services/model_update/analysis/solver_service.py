import os
import re
import shutil
import subprocess
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.l3.core.errors import NotFoundError, ValidationError

from .model_update_meta_service import resolve_abaqus_command, resolve_nastran_command
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


def _abs_file(path: str, field_name: str) -> Path:
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise NotFoundError(f"未找到 {field_name}: {file_path}", {field_name: str(file_path)})
    if not file_path.is_file():
        raise ValidationError(f"{field_name} 必须是文件", {field_name: str(file_path)})
    return file_path


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
    if artifact_suffixes is _NASTRAN_ARTIFACT_SUFFIXES:
        payload["artifacts"] = _collect_nastran_extra_artifacts(workdir, payload["artifacts"])
        payload["artifacts_summary"] = _summarize_nastran_artifacts(payload["artifacts"])
    return payload


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
    if run_solver:
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
        payload["solver"] = _run_local_solver(
            command=command,
            workdir=target_dir,
            artifact_stem=resolved_job_name,
            artifact_suffixes=_ABAQUS_ARTIFACT_SUFFIXES,
            timeout_sec=timeout_sec,
        )
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
