from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

from src.l3.core.config import settings
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.infra.registry_repo import RegistryRepo

from .project_path_service import resolve_project_cal_subdir, resolve_project_workspace


def _clean_parts(parts: Sequence[Optional[str]]) -> list[str]:
    return [str(part).strip() for part in parts if str(part or "").strip()]


def _ensure_within_root(root: Path, candidate: Path, field_name: str, project_id: int) -> Path:
    try:
        common = os.path.commonpath([str(root), str(candidate)])
    except ValueError as exc:
        raise ValidationError(
            f"{field_name} must stay inside project root",
            {"project_id": int(project_id), field_name: str(candidate), "root": str(root)},
        ) from exc
    if common != str(root):
        raise ValidationError(
            f"{field_name} must stay inside project root",
            {"project_id": int(project_id), field_name: str(candidate), "root": str(root)},
        )
    return candidate


def resolve_project_workspace_root(project_id: int) -> Path:
    return Path(resolve_project_workspace(int(project_id))).expanduser().resolve()


def resolve_project_cal_root(project_id: int, *parts: Optional[str]) -> Path:
    return Path(resolve_project_cal_subdir(int(project_id), *_clean_parts(parts))).expanduser().resolve()


def _translate_project_source_reference(project_id: int, raw: str) -> Optional[Path]:
    text = str(raw or "").strip()
    if not text:
        return None

    try:
        repo = RegistryRepo(settings.registry_db_path)
        proj = repo.get_project(str(int(project_id)))
    except Exception:
        return None
    if proj is None:
        return None

    runtime_path = str(proj["inp_path"] or "").strip()
    runtime_file = str(proj["source_file"] or "").strip()
    original_path = str(proj["original_inp_path"] or "").strip() if "original_inp_path" in proj.keys() else ""
    original_file = str(proj["original_source_file"] or "").strip() if "original_source_file" in proj.keys() else ""
    workspace = Path(repo.resolve_workspace(proj["workspace"], settings.data_root)).expanduser().resolve()

    normalized_input = str(Path(text).expanduser())
    input_name = Path(normalized_input).name

    matches_original_path = bool(original_path) and normalized_input == original_path
    matches_original_file = bool(original_file) and input_name == original_file
    matches_runtime_file = bool(runtime_file) and input_name == runtime_file
    if not (matches_original_path or matches_original_file or matches_runtime_file):
        return None

    candidates = []
    if runtime_path:
        candidates.append(Path(runtime_path).expanduser().resolve())
    if runtime_file:
        candidates.append((workspace / runtime_file).resolve())

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def resolve_project_existing_file(project_id: int, path_or_name: str, field_name: str) -> Path:
    raw = str(path_or_name or "").strip()
    if not raw:
        raise ValidationError(
            f"{field_name} cannot be empty",
            {"project_id": int(project_id), field_name: path_or_name},
        )

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        workspace = resolve_project_workspace_root(project_id)
        resolved = _ensure_within_root(
            workspace,
            (workspace / candidate).resolve(),
            field_name,
            int(project_id),
        )

    if not resolved.exists():
        translated = _translate_project_source_reference(int(project_id), raw)
        if translated is not None:
            return translated
        raise NotFoundError(field_name, {"project_id": int(project_id), field_name: str(resolved)})
    if not resolved.is_file():
        raise ValidationError(
            f"{field_name} must be a file",
            {"project_id": int(project_id), field_name: str(resolved)},
        )
    return resolved


def resolve_project_input_file(
    project_id: int,
    *,
    explicit_path: Optional[str],
    file_name: Optional[str],
    field_name: str,
) -> Path:
    explicit_raw = str(explicit_path or "").strip()
    if explicit_raw:
        return resolve_project_existing_file(int(project_id), explicit_raw, field_name)

    file_name_raw = str(file_name or "").strip()
    if not file_name_raw:
        raise ValidationError(
            f"{field_name} cannot be empty",
            {"project_id": int(project_id), field_name: file_name},
        )

    candidate = Path(file_name_raw).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        return resolve_project_existing_file(int(project_id), file_name_raw, field_name)

    search_roots = [
        resolve_project_workspace_root(int(project_id)),
        resolve_project_cal_root(int(project_id)),
        resolve_project_cal_root(int(project_id), "sensitivity"),
        resolve_project_cal_root(int(project_id), "bayesian"),
    ]
    seen = set()
    for root in search_roots:
        normalized_root = str(root.resolve())
        if normalized_root in seen:
            continue
        seen.add(normalized_root)
        resolved = (root / candidate.name).resolve()
        try:
            resolved = _ensure_within_root(resolve_project_workspace_root(int(project_id)), resolved, field_name, int(project_id))
        except ValidationError:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved

    return resolve_project_existing_file(int(project_id), file_name_raw, field_name)


def resolve_project_output_dir(
    project_id: int,
    *,
    category_parts: Sequence[Optional[str]],
    explicit_dir: Optional[str] = None,
    field_name: str = "output_dir",
) -> Path:
    base_dir = resolve_project_cal_root(int(project_id), *_clean_parts(category_parts))
    raw = str(explicit_dir or "").strip()
    if not raw:
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = _ensure_within_root(
            base_dir,
            (base_dir / candidate).resolve(),
            field_name,
            int(project_id),
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_project_output_file(
    project_id: int,
    *,
    category_parts: Sequence[Optional[str]],
    explicit_path: Optional[str] = None,
    file_name: Optional[str] = None,
    default_name: Optional[str] = None,
    field_name: str,
) -> Path:
    base_dir = resolve_project_cal_root(int(project_id), *_clean_parts(category_parts))
    base_dir.mkdir(parents=True, exist_ok=True)

    raw = str(explicit_path or "").strip() or str(file_name or "").strip() or str(default_name or "").strip()
    if not raw:
        raise ValidationError(
            f"{field_name} cannot be empty",
            {"project_id": int(project_id), field_name: raw},
        )

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = _ensure_within_root(
            base_dir,
            (base_dir / candidate).resolve(),
            field_name,
            int(project_id),
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
