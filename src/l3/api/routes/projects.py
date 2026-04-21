"""
Project-grouping API endpoints.

POST   /api/projects                              — 创建 project（提交 INP）
POST   /api/projects/{project_id}/results         — 提交 ODB（追加结果组）
GET    /api/projects/{project_id}                 — 查询 project + result_groups 状态
PATCH  /api/projects/{project_id}/results/{rg}    — 修改 result_group 显示名
DELETE /api/projects/{project_id}                 — 删除 project（文件夹 + 注册表）
"""
import json
import os
import shutil

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from ...core.config import settings
from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...infra.registry_repo import RegistryRepo
from ...infra.manifest_repo import ManifestRepo
from ..response import ok

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _repo() -> RegistryRepo:
    return RegistryRepo(settings.registry_db_path)


def _workspace(project_id: str) -> str:
    return os.path.join(settings.data_root, project_id)


def _resolve_workspace(stored: str, project_id: str) -> str:
    """Resolve stored workspace (bare id, relative, or absolute) to absolute path."""
    import re as _re
    is_abs = os.path.isabs(stored) or bool(_re.match(r'^[A-Za-z]:[/\\]', stored))
    if is_abs:
        return stored
    has_sep = os.sep in stored or '/' in stored
    if has_sep:
        return stored  # relative path already contains data_root prefix
    return os.path.join(settings.data_root, stored)


# ── Request bodies ─────────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    project_id: str
    source_path: str


class AddResultGroupRequest(BaseModel):
    source_path: str
    result_group: str
    display_name: Optional[str] = None
    parse_options: Optional[dict] = None


class PatchResultGroupRequest(BaseModel):
    display_name: str


# ── Shared helper ──────────────────────────────────────────────────────────────

def _build_project_response(proj, repo) -> dict:
    """Build the full project dict (including result_groups) from a projects row."""
    project_id = proj["project_id"]
    workspace  = _resolve_workspace(proj["workspace"], project_id)
    rg_rows    = repo.list_result_groups(project_id)

    result_groups = []
    for rg in rg_rows:
        rg_name = rg["result_group"]
        status  = rg["status"]

        consistency_check = "count-only"
        try:
            manifest = ManifestRepo(workspace)
            meta = manifest.get_result_group_meta(rg_name)
            if meta and meta["consistency_check"]:
                consistency_check = meta["consistency_check"]
            elif rg["parse_options"]:
                opts = json.loads(rg["parse_options"])
                consistency_check = opts.get("consistency_check", "count-only")
        except Exception:
            pass

        steps = []
        if status == "ready":
            try:
                manifest = ManifestRepo(workspace)
                overview = manifest.get_overview(result_group=rg_name)
                steps = [s["step_name"] for s in overview.get("steps", [])]
            except Exception:
                pass

        result_groups.append({
            "result_group": rg_name,
            "display_name": rg["display_name"],
            "status": status,
            "consistency_check": consistency_check,
            "error_message": rg["error_message"],
            "steps": steps,
        })

    return {
        "project_id": project_id,
        "geom_status": proj["geom_status"],
        "result_groups": result_groups,
    }


# ── GET /api/projects ──────────────────────────────────────────────────────────

@router.get("")
async def list_projects():
    """列出所有 projects，含各 result_group 状态。"""
    repo = _repo()
    rows = repo.list_projects()
    return ok([_build_project_response(r, repo) for r in rows])


# ── POST /api/projects ─────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_project(body: CreateProjectRequest):
    """
    创建新 project，触发 INP 几何解析。
    调用方（Java 后端）提供 project_id（UUID），避免 L3 自己生成。
    """
    if not os.path.isfile(body.source_path):
        raise ValidationError(f"File not found on server: {body.source_path}")

    repo = _repo()
    if repo.get_project(body.project_id) is not None:
        raise ConflictError(f"Project '{body.project_id}' already exists")

    workspace = _workspace(body.project_id)
    os.makedirs(workspace, exist_ok=True)

    # Store bare project_id as workspace key (same pattern as odb_jobs).
    # resolve_workspace(project_id, data_root) → data_root/project_id at read time.
    repo.create_project(
        project_id=body.project_id,
        workspace=body.project_id,
        inp_path=body.source_path,
    )

    return ok({"project_id": body.project_id, "geom_status": "pending"})


# ── POST /api/projects/{project_id}/results ───────────────────────────────────

@router.post("/{project_id}/results", status_code=201)
async def add_result_group(project_id: str, body: AddResultGroupRequest):
    """
    向已有 project 追加一个 ODB 结果组。
    - 若 project 不存在 → 404
    - 若同名 result_group 已存在且 status=error → 重置为 pending（重试）
    - 若同名 result_group 已存在且 status=ready/running → 409
    """
    repo = _repo()

    proj = repo.get_project(project_id)
    if proj is None:
        raise NotFoundError(f"Project '{project_id}' not found")

    if not os.path.isfile(body.source_path):
        raise ValidationError(f"File not found on server: {body.source_path}")

    display_name = body.display_name or body.result_group
    parse_options_json = json.dumps(body.parse_options) if body.parse_options else None

    # Check for existing result_group
    existing = repo.get_result_group(project_id, body.result_group)
    if existing is not None:
        if existing["status"] in ("ready", "running"):
            raise ConflictError(
                f"result_group '{body.result_group}' already exists "
                f"with status='{existing['status']}'"
            )
        # status == 'error' → reset for retry
        repo.reset_result_group_for_retry(project_id, body.result_group)
    else:
        source_file = os.path.basename(body.source_path)
        repo.create_result_group(
            project_id=project_id,
            result_group=body.result_group,
            display_name=display_name,
            source_path=body.source_path,
            source_file=source_file,
            parse_options=parse_options_json,
        )

    return ok({
        "project_id": project_id,
        "result_group": body.result_group,
        "status": "pending",
    })


# ── GET /api/projects/{project_id} ────────────────────────────────────────────

@router.get("/{project_id}")
async def get_project(project_id: str):
    """查询 project 状态及所有 result_groups 的详情。"""
    repo = _repo()
    proj = repo.get_project(project_id)
    if proj is None:
        raise NotFoundError(f"Project '{project_id}' not found")
    return ok(_build_project_response(proj, repo))


# ── PATCH /api/projects/{project_id}/results/{result_group} ───────────────────

@router.patch("/{project_id}/results/{result_group}")
async def rename_result_group(project_id: str, result_group: str,
                               body: PatchResultGroupRequest):
    """
    修改 result_group 的显示名。
    同时更新 registry.db（result_groups.display_name）
    和 manifest.db（result_group_meta.display_name）。
    """
    repo = _repo()

    proj = repo.get_project(project_id)
    if proj is None:
        raise NotFoundError(f"Project '{project_id}' not found")

    rg = repo.get_result_group(project_id, result_group)
    if rg is None:
        raise NotFoundError(
            f"result_group '{result_group}' not found in project '{project_id}'"
        )

    # Update registry
    repo.update_result_group_display_name(project_id, result_group, body.display_name)

    # Update manifest (best-effort; manifest may not exist if still pending/error)
    try:
        workspace = _resolve_workspace(proj["workspace"], project_id)
        ManifestRepo(workspace).update_result_group_meta_display_name(
            result_group, body.display_name
        )
    except Exception:
        pass

    return ok({
        "project_id": project_id,
        "result_group": result_group,
        "display_name": body.display_name,
    })


# ── DELETE /api/projects/{project_id} ─────────────────────────────────────────

@router.delete("/{project_id}", status_code=200)
async def delete_project(project_id: str):
    """
    删除 project：移除 model/<project_id>/ 整个目录 + registry.db 中相关行。
    """
    repo = _repo()

    proj = repo.get_project(project_id)
    if proj is None:
        raise NotFoundError(f"Project '{project_id}' not found")

    workspace = _resolve_workspace(proj["workspace"], project_id)

    # Remove from registry first (so runner won't pick it up)
    repo.delete_project(project_id)

    # Delete the workspace directory
    if os.path.isdir(workspace):
        shutil.rmtree(workspace, ignore_errors=True)

    return ok({"project_id": project_id, "deleted": True})
