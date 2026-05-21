from __future__ import annotations

from typing import Optional

from src.l3.infra.registry_repo import RegistryRepo
from src.l3.core.config import settings


def append_project_log(
    project_id: int,
    level: str,
    message: str,
    *,
    stage: Optional[str] = None,
    percent: Optional[int] = None,
) -> None:
    try:
        resolved_percent = None if percent is None else max(0, min(int(percent), 100))
        repo = RegistryRepo(settings.registry_db_path)
        repo.append_job_log(
            str(int(project_id)),
            str(level or "info"),
            str(message or ""),
            stage=str(stage) if stage is not None else None,
            percent=resolved_percent,
        )
    except Exception:
        return None


def log_project_step(project_id: int, message: str, *, stage: Optional[str] = None, percent: Optional[int] = None) -> None:
    append_project_log(project_id, "step", message, stage=stage, percent=percent)


def log_project_info(project_id: int, message: str, *, stage: Optional[str] = None, percent: Optional[int] = None) -> None:
    append_project_log(project_id, "info", message, stage=stage, percent=percent)


def log_project_warn(project_id: int, message: str, *, stage: Optional[str] = None, percent: Optional[int] = None) -> None:
    append_project_log(project_id, "warn", message, stage=stage, percent=percent)


def log_project_error(project_id: int, message: str, *, stage: Optional[str] = None, percent: Optional[int] = None) -> None:
    append_project_log(project_id, "error", message, stage=stage, percent=percent)
