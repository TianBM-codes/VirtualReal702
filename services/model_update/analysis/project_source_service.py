from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse

from db import get_connection
from src.l3.core.config import settings
from src.l3.core.errors import NotFoundError
from src.l3.infra.registry_repo import RegistryRepo


def _is_http_url(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def _normalize_local_path(path_value) -> Optional[str]:
    if path_value is None:
        return None
    text = str(path_value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if not text or _is_http_url(text):
        return None
    return os.path.abspath(text)


def _downloaded_project_inp_candidate(project_id: int, stored_inp_path: str) -> Optional[str]:
    raw = str(stored_inp_path or "").strip()
    if not raw:
        return None

    repo = RegistryRepo(settings.registry_db_path)
    proj = repo.get_project(str(project_id))
    if proj is None:
        return None

    workspace = repo.resolve_workspace(proj["workspace"], settings.data_root)
    parsed_path = urlparse(raw).path if _is_http_url(raw) else raw
    filename = os.path.basename(parsed_path)
    if not filename:
        return None
    return os.path.abspath(os.path.join(workspace, filename))


def resolve_project_source_inp_path(project_id: int) -> str:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT source_file_path
            FROM t_mt_py_fem_node_octree_cache
            WHERE pid = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (int(project_id),),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    source_file_path = _normalize_local_path(row.get("source_file_path")) if row else None
    if source_file_path and os.path.exists(source_file_path):
        return source_file_path

    repo = RegistryRepo(settings.registry_db_path)
    proj = repo.get_project(str(project_id))
    if proj is None:
        raise NotFoundError(
            f"注册表中未找到项目 '{project_id}'",
            {"project_id": int(project_id)},
        )

    stored_inp_path = str(proj["inp_path"] or "").strip()
    local_path = _normalize_local_path(stored_inp_path)
    if local_path and os.path.exists(local_path):
        return local_path

    candidate = _downloaded_project_inp_candidate(int(project_id), stored_inp_path)
    if candidate and os.path.exists(candidate):
        return candidate

    raise NotFoundError(
        f"未找到 project_id={project_id} 对应的源 inp 文件",
        {
            "project_id": int(project_id),
            "stored_inp_path": stored_inp_path or None,
            "candidate_path": candidate,
        },
    )
