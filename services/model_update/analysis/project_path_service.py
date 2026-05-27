from __future__ import annotations

import os
from typing import Optional

from src.l3.core.config import settings
from src.l3.infra.registry_repo import RegistryRepo


def resolve_project_workspace(project_id: int) -> str:
    repo = RegistryRepo(settings.registry_db_path)
    row = repo.get_project(str(int(project_id)))
    if row is not None:
        stored_workspace = str(row["workspace"] or "").strip()
        if stored_workspace:
            return os.path.abspath(repo.resolve_workspace(stored_workspace, settings.data_root))
    return os.path.abspath(os.path.join(settings.data_root, str(int(project_id))))


def resolve_project_cal_dir(project_id: int) -> str:
    path = os.path.join(resolve_project_workspace(int(project_id)), "cal")
    os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)


def resolve_project_cal_subdir(project_id: int, *parts: Optional[str]) -> str:
    path = resolve_project_cal_dir(int(project_id))
    cleaned = [str(part).strip() for part in parts if str(part or "").strip()]
    if cleaned:
        path = os.path.join(path, *cleaned)
        os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)
