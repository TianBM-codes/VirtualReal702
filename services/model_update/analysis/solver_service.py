import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.l3.core.errors import NotFoundError, ValidationError

from .model_update_meta_service import resolve_abaqus_command
from ..solver_prep.abaqus_adjoint import generate_adjoint_shell_thickness_inp
from ..solver_prep.nastran_sol103 import convert_to_sol103
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
)


def _abs_file(path: str, field_name: str) -> Path:
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise NotFoundError(f"{field_name} not found: {file_path}", {field_name: str(file_path)})
    if not file_path.is_file():
        raise ValidationError(f"{field_name} must be a file", {field_name: str(file_path)})
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
        raise ValidationError("timeout_sec must be > 0", {"timeout_sec": timeout_sec})

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
            f"solver executable not found: {command[0]}",
            {"command": command, "workdir": str(workdir)},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(
            "solver execution timed out",
            {
                "command": command,
                "workdir": str(workdir),
                "timeout_sec": timeout_sec,
                "stdout_tail": _tail_text(exc.stdout or ""),
                "stderr_tail": _tail_text(exc.stderr or ""),
            },
        ) from exc

    return {
        "ok": result.returncode == 0,
        "returncode": int(result.returncode),
        "command": command,
        "workdir": str(workdir),
        "stdout_tail": _tail_text(result.stdout),
        "stderr_tail": _tail_text(result.stderr),
        "artifacts": _collect_artifacts(workdir, artifact_stem, artifact_suffixes),
    }


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
            raise ValidationError("cpus must be > 0", {"cpus": cpus})
        command.append(f"cpus={int(cpus)}")
    command.extend(_normalize_extra_args(extra_args))
    return command


def _build_nastran_command(
    nastran: str,
    bdf_path: Path,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    command = [str(nastran), bdf_path.name]
    command.extend(_normalize_extra_args(extra_args))
    return command


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
    nastran: str = "nastran",
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
        "workflow": "nastran_sol103",
        "input_bdf": str(input_path),
        "output_bdf": str(output_path),
        "command_preview": command,
        "solver": None,
    }
    if run_solver:
        payload["solver"] = _run_local_solver(
            command=command,
            workdir=output_path.parent,
            artifact_stem=output_path.stem,
            artifact_suffixes=_NASTRAN_ARTIFACT_SUFFIXES,
            timeout_sec=timeout_sec,
        )
    return payload
