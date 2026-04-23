"""
Safety helpers for project workspace file operations.

All project-level copy/delete/restore operations must derive paths from ids
under DATA_ROOT. External callers should never provide filesystem targets.
"""
import os
import re
import shutil
import uuid
from pathlib import Path

from ..core.errors import ConflictError, ValidationError


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_workspace_id(value: str, field_name: str = "id") -> str:
    if not value or not _SAFE_ID_RE.fullmatch(value):
        raise ValidationError(
            f"Invalid {field_name}; use only letters, numbers, '_' and '-'",
            {field_name: value},
        )
    return value


def resolve_data_root(data_root: str) -> Path:
    root = Path(data_root).resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValidationError("DATA_ROOT is not a directory", {"data_root": str(root)})
    return root


def ensure_under_data_root(path: Path, data_root: Path, label: str = "path") -> Path:
    resolved = path.resolve()
    root = data_root.resolve()
    try:
        common = os.path.commonpath([str(root), str(resolved)])
    except ValueError:
        raise ValidationError(f"{label} is outside DATA_ROOT", {label: str(resolved)})
    if common != str(root):
        raise ValidationError(f"{label} is outside DATA_ROOT", {label: str(resolved)})
    return resolved


def project_workspace(data_root: str, project_id: str) -> Path:
    validate_workspace_id(project_id, "project_id")
    root = resolve_data_root(data_root)
    return ensure_under_data_root(root / project_id, root, "project_workspace")


def ops_tmp_dir(data_root: str) -> Path:
    root = resolve_data_root(data_root)
    tmp = ensure_under_data_root(root / ".ops_tmp", root, "ops_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def clone_tmp_workspace(data_root: str, new_project_id: str) -> Path:
    validate_workspace_id(new_project_id, "new_project_id")
    tmp_root = ops_tmp_dir(data_root)
    tmp = tmp_root / f"clone-{new_project_id}-{uuid.uuid4().hex}"
    return ensure_under_data_root(tmp, resolve_data_root(data_root), "clone_tmp")


def require_directory(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValidationError(f"{label} must not be a symlink", {label: str(path)})
    if not path.is_dir():
        raise ValidationError(f"{label} does not exist", {label: str(path)})


def require_target_absent(path: Path, label: str = "target") -> None:
    if path.exists() or path.is_symlink():
        raise ConflictError(f"{label} already exists", {label: str(path)})


def safe_rmtree(path: Path, data_root: str, label: str = "path") -> None:
    root = resolve_data_root(data_root)
    target = ensure_under_data_root(path, root, label)
    if target == root:
        raise ValidationError("Refusing to delete DATA_ROOT", {label: str(target)})
    if target.exists() or target.is_symlink():
        shutil.rmtree(str(target), ignore_errors=True)
