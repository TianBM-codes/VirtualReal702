"""
Project-grouping API endpoints.

POST   /api/projects                              — 创建 project（提交 INP 或 ODB）
POST   /api/projects/{project_id}/results         — 提交 ODB（追加结果组）
GET    /api/projects/{project_id}                 — 查询 project + result_groups 状态
PATCH  /api/projects/{project_id}/results/{rg}    — 修改 result_group 显示名
DELETE /api/projects/{project_id}                 — 删除 project（文件夹 + 注册表）
"""
import json
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional

from ...core.config import settings
from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...core.state import registry
from ...infra.registry_repo import RegistryRepo
from ...infra.manifest_repo import ManifestRepo
from ...infra.workspace_safety import (
    clone_tmp_workspace,
    project_workspace,
    require_directory,
    require_target_absent,
    safe_rmtree,
    validate_workspace_id,
)
from ..response import ok


def _is_http_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")

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
    source_type: Optional[str] = None


class AddResultGroupRequest(BaseModel):
    source_path: str
    result_group: str
    display_name: Optional[str] = None
    parse_options: Optional[dict] = None


class PatchResultGroupRequest(BaseModel):
    display_name: str


class CloneProjectRequest(BaseModel):
    new_project_id: str


# ── Shared helper ──────────────────────────────────────────────────────────────

def _build_project_response(proj, repo) -> dict:
    """Build the full project dict (including result_groups) from a projects row."""
    project_id = proj["project_id"]
    workspace  = _resolve_workspace(proj["workspace"], project_id)
    rg_rows    = repo.list_result_groups(project_id)
    source_type = proj["source_type"] if "source_type" in proj.keys() else "inp"

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
        "source_type": source_type,
        "geom_status": proj["geom_status"],
        "result_groups": result_groups,
    }


def _detect_source_type(source_path: str, explicit: Optional[str] = None) -> str:
    allowed = {"inp", "odb", "bdf", "op2"}
    if explicit is not None:
        source_type = explicit.strip().lower()
        if source_type not in allowed:
            raise ValidationError(
                "source_type must be one of: inp, odb, bdf, op2",
                {"source_type": explicit},
            )
    else:
        source_type = None

    parsed_path = urlparse(source_path).path if _is_http_url(source_path) else source_path
    suffix = Path(parsed_path).suffix.lower()
    inferred = None
    if suffix == ".inp":
        inferred = "inp"
    elif suffix == ".odb":
        inferred = "odb"
    elif suffix == ".bdf":
        inferred = "bdf"
    elif suffix == ".op2":
        inferred = "op2"

    if source_type is None:
        if inferred is None:
            raise ValidationError(
                "Cannot infer source_type from source_path; please pass source_type explicitly",
                {"source_path": source_path},
            )
        return inferred

    if inferred is not None and inferred != source_type:
        raise ValidationError(
            "source_type does not match source_path extension",
            {"source_type": source_type, "source_path": source_path},
        )
    return source_type


# ── GET /api/projects ──────────────────────────────────────────────────────────

def _reject_symlink_tree(root: Path) -> None:
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise ValidationError(
                "Project workspace contains symlinks and cannot be copied",
                {"path": str(entry)},
            )


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
    创建新 project，触发 source_path 对应的解析流程。
    调用方（Java 后端）提供 project_id（UUID），避免 L3 自己生成。

    若同 project_id 已存在且 geom_status='error'，视为重试：
    清空 workspace、更新 source_path、重置为 pending。
    """
    project_id = validate_workspace_id(body.project_id, "project_id")
    if not _is_http_url(body.source_path) and not os.path.isfile(body.source_path):
        raise ValidationError(f"File not found on server: {body.source_path}")
    source_type = _detect_source_type(body.source_path, body.source_type)

    repo = _repo()
    existing = repo.get_project(project_id)
    workspace = project_workspace(settings.data_root, project_id)

    if existing is not None:
        if existing["geom_status"] != "error":
            raise ConflictError(
                f"Project '{project_id}' already exists "
                f"(geom_status='{existing['geom_status']}')"
            )
        # Retry path: project previously failed — clean workspace and resubmit.
        if workspace.exists() or workspace.is_symlink():
            safe_rmtree(workspace, settings.data_root, "project workspace")
        workspace.mkdir(parents=True, exist_ok=True)
        repo.reset_project_for_retry(project_id, body.source_path, source_type)
        return ok({
            "project_id": project_id,
            "source_type": source_type,
            "geom_status": "pending",
        })

    if workspace.exists() or workspace.is_symlink():
        raise ConflictError(
            f"Project '{project_id}' cannot be created because workspace "
            f"'{workspace}' already exists without a registry entry"
        )
    require_target_absent(workspace, "project workspace")
    workspace.mkdir(parents=True, exist_ok=False)

    # Store bare project_id as workspace key (same pattern as odb_jobs).
    # resolve_workspace(project_id, data_root) → data_root/project_id at read time.
    try:
        repo.create_project(
            project_id=project_id,
            workspace=project_id,
            inp_path=body.source_path,
            source_type=source_type,
        )
    except Exception:
        safe_rmtree(workspace, settings.data_root, "project workspace")
        raise

    return ok({
        "project_id": project_id,
        "source_type": source_type,
        "geom_status": "pending",
    })


# ── POST /api/projects/{project_id}/results ───────────────────────────────────

@router.post("/{source_project_id}/clone", status_code=201)
async def clone_project(source_project_id: str, body: CloneProjectRequest):
    source_project_id = validate_workspace_id(source_project_id, "source_project_id")
    new_project_id = validate_workspace_id(body.new_project_id, "new_project_id")
    if source_project_id == new_project_id:
        raise ConflictError("new_project_id must be different from source_project_id")

    repo = _repo()
    src = repo.get_project(source_project_id)
    if src is None:
        raise NotFoundError(f"Project '{source_project_id}' not found")
    if repo.get_project(new_project_id) is not None:
        raise ConflictError(f"Project '{new_project_id}' already exists")
    if repo.project_has_active_tasks(source_project_id):
        raise ConflictError(
            "Cannot clone a project while geometry or result parsing is pending/running"
        )

    source_workspace = project_workspace(settings.data_root, source_project_id)
    target_workspace = project_workspace(settings.data_root, new_project_id)
    tmp_workspace = clone_tmp_workspace(settings.data_root, new_project_id)

    require_directory(source_workspace, "source workspace")
    require_target_absent(target_workspace, "target workspace")
    require_target_absent(tmp_workspace, "temporary workspace")
    _reject_symlink_tree(source_workspace)

    target_created = False
    try:
        shutil.copytree(str(source_workspace), str(tmp_workspace), symlinks=False)

        if repo.project_has_active_tasks(source_project_id):
            raise ConflictError(
                "Cannot clone a project while geometry or result parsing is pending/running"
            )

        tmp_workspace.rename(target_workspace)
        target_created = True

        try:
            repo.clone_project_records(source_project_id, new_project_id)
        except sqlite3.IntegrityError as exc:
            safe_rmtree(target_workspace, settings.data_root, "target workspace")
            target_created = False
            raise ConflictError(f"Project '{new_project_id}' already exists") from exc
        except ValueError as exc:
            safe_rmtree(target_workspace, settings.data_root, "target workspace")
            target_created = False
            raise NotFoundError(f"Project '{source_project_id}' not found") from exc
        except Exception:
            safe_rmtree(target_workspace, settings.data_root, "target workspace")
            target_created = False
            raise

    except Exception:
        if not target_created:
            safe_rmtree(tmp_workspace, settings.data_root, "temporary workspace")
        raise

    cloned_groups = repo.list_result_groups(new_project_id)
    return ok({
        "source_project_id": source_project_id,
        "project_id": new_project_id,
        "geom_status": src["geom_status"],
        "result_group_count": len(cloned_groups),
    })


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

    if not _is_http_url(body.source_path) and not os.path.isfile(body.source_path):
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

@router.get("/{project_id}/logs")
async def get_project_logs(
    project_id: str,
    since_id: int = Query(0, ge=0, description="只返回 id > since_id 的行，用于增量轮询"),
    limit: int = Query(200, ge=1, le=1000),
):
    """
    返回 project 的解析进度日志，支持增量轮询。

    覆盖范围：几何管道（INP/ODB L1+L2）和所有结果组（result_group）的解析日志。
    result_group 相关行的 stage 以 'rg_' 开头，message 中包含结果组名称。

    level 取值：step / info / warn / error
    """
    repo = _repo()
    if repo.get_project(project_id) is None:
        raise NotFoundError(f"Project '{project_id}' not found")
    rows = repo.get_job_logs(project_id, since_id=since_id, limit=limit)
    items = [dict(r) for r in rows]
    next_since = items[-1]["id"] if items else since_id
    current_percent = repo.get_current_percent(project_id)
    return ok({"logs": items, "next_since_id": next_since, "current_percent": current_percent})


@router.post("/{project_id}/logs/clear")
async def clear_project_logs(project_id: str):
    """清空该 project 的全部解析日志（job_logs）。"""
    repo = _repo()
    if repo.get_project(project_id) is None:
        raise NotFoundError(f"Project '{project_id}' not found")
    deleted = repo.clear_job_logs(project_id)
    return ok({"project_id": project_id, "deleted_count": deleted})


@router.get("/{project_id}")
async def get_project(project_id: str):
    """查询 project 状态及所有 result_groups 的详情。"""
    repo = _repo()
    proj = repo.get_project(project_id)
    if proj is None:
        raise NotFoundError(f"Project '{project_id}' not found")
    # Eagerly load into registry if ready but not yet in memory.
    # Closes the 10-second poll gap: frontend sees geom_status=ready here,
    # then immediately calls /meta/overview — registry must have the entry by then.
    workspace = _resolve_workspace(proj["workspace"], project_id)
    if proj["geom_status"] == "ready":
        if registry.get(project_id) is None:
            registry.load(project_id, workspace, "ready")
        else:
            # L2 may have been re-run after initial load (sections patch in INP+ODB mode).
            # If so, reload averaging data from the updated render.h5 files.
            _reload_marker = os.path.join(workspace, "l2", "render", ".reload_needed")
            if os.path.exists(_reload_marker):
                try:
                    os.remove(_reload_marker)
                except OSError:
                    pass
                registry.upgrade(project_id)
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


# ── GET /api/projects/{project_id}/summary ────────────────────────────────────

@router.get("/{project_id}/summary")
async def get_project_summary(project_id: str):
    """
    返回模型统计摘要（来自 INP 解析）。
    geom_status=ready 后可用；尚未解析完返回 404。
    """
    repo = _repo()
    proj = repo.get_project(project_id)
    if proj is None:
        raise NotFoundError(f"Project '{project_id}' not found")

    workspace = _resolve_workspace(proj["workspace"], project_id)
    summary_path = os.path.join(workspace, "model_summary.json")
    if not os.path.isfile(summary_path):
        raise NotFoundError("Summary not yet available — geometry may still be processing")

    with open(summary_path, encoding="utf-8") as f:
        data = json.load(f)
    return ok(data)


# ── DELETE /api/projects/{project_id} ─────────────────────────────────────────

@router.post("/{project_id}/rerun-l2", status_code=202)
async def rerun_l2(project_id: str):
    """
    重新运行 L2 预处理（ingest.py），用于 L2 逻辑更新后刷新几何缓存。

    仅允许 geom_status 为 'ready' 或 'error' 时触发；job_runner 轮询到
    geom_status='l2_pending' 后自动认领并执行。
    运行期间所有几何接口返回 503。
    进度通过 GET /api/projects/{project_id}/logs 实时查询。
    """
    project_id = validate_workspace_id(project_id, "project_id")
    repo = _repo()
    proj = repo.get_project(project_id)
    if proj is None:
        raise NotFoundError(f"Project '{project_id}' not found")

    claimed = repo.claim_l2_rerun(project_id)
    if not claimed:
        current = proj["geom_status"]
        raise ConflictError(
            f"Cannot start L2 rerun: project '{project_id}' is in status '{current}' "
            "(only 'ready' or 'error' allowed)"
        )

    return ok({"project_id": project_id, "geom_status": "l2_pending"})


@router.delete("/{project_id}", status_code=200)
async def delete_project(project_id: str):
    """
    删除 project：移除 model/<project_id>/ 整个目录 + registry.db 中相关行。
    """
    project_id = validate_workspace_id(project_id, "project_id")
    repo = _repo()
    workspace = project_workspace(settings.data_root, project_id)

    proj = repo.get_project(project_id)
    if proj is None:
        if workspace.exists() or workspace.is_symlink():
            safe_rmtree(workspace, settings.data_root, "project workspace")
            return ok({
                "project_id": project_id,
                "deleted": True,
                "orphan_workspace_deleted": True,
            })
        raise NotFoundError(f"Project '{project_id}' not found")

    # Remove from registry first (so runner won't pick it up)
    repo.delete_project(project_id)

    # Delete the workspace directory
    safe_rmtree(workspace, settings.data_root, "project workspace")

    return ok({
        "project_id": project_id,
        "deleted": True,
        "orphan_workspace_deleted": False,
    })
