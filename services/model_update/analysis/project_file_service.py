from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

from src.l3.core.errors import NotFoundError, ValidationError

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
    raw = str(explicit_path or "").strip() or str(file_name or "").strip()
    return resolve_project_existing_file(int(project_id), raw, field_name)


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
