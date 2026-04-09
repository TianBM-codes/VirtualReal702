"""
/api/jobs  —  Job management endpoints.

POST   /api/jobs                    Submit a new ODB for processing
GET    /api/jobs                    List all jobs
GET    /api/jobs/{odb_id}           Single job detail
DELETE /api/jobs/{odb_id}           Soft-delete (default) or hard-delete (?hard=true)
POST   /api/jobs/{odb_id}/retry     Re-queue a failed job (always re-runs L1+L2)
"""
import os
import shutil
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core.config import settings
from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.registry_repo import RegistryRepo

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _repo() -> RegistryRepo:
    return RegistryRepo(settings.registry_db_path)


def _row_to_summary(row) -> dict:
    d = dict(row)
    d["is_render_ready"] = (d.get("status") == "ready")
    return d


# ── Request bodies ────────────────────────────────────────────────────────────

class SubmitJobRequest(BaseModel):
    odb_path: str
    display_name: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def submit_job(body: SubmitJobRequest):
    """Submit a new ODB file for L1+L2 processing."""
    # Validate path exists on the server
    if not os.path.isfile(body.odb_path):
        raise HTTPException(status_code=400,
                            detail=f"File not found on server: {body.odb_path}")

    # Optional path-traversal guard
    if settings.raw_odb_root:
        raw_root = os.path.realpath(settings.raw_odb_root)
        req_path = os.path.realpath(body.odb_path)
        if not req_path.startswith(raw_root + os.sep):
            raise HTTPException(status_code=400,
                                detail="odb_path is outside the allowed root directory")

    odb_id    = str(uuid.uuid4())
    workspace = os.path.join(settings.data_root, odb_id)
    os.makedirs(workspace, exist_ok=True)

    size = os.path.getsize(body.odb_path)
    # Store only odb_id (portable), not the full absolute path.
    # resolve_workspace() reconstructs the absolute path from data_root at load time.
    _repo().create_job(odb_id, body.display_name, body.odb_path, odb_id, size)

    return {"odb_id": odb_id, "display_name": body.display_name, "status": "submitted"}


@router.get("")
async def list_jobs():
    """Return all jobs ordered by submission time (newest first)."""
    rows = _repo().list_jobs()
    return [_row_to_summary(r) for r in rows]


@router.get("/{odb_id}")
async def get_job(odb_id: str):
    """Return full detail for a single job."""
    row = _repo().get_job(odb_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{odb_id}' not found")
    return _row_to_summary(row)


@router.delete("/{odb_id}")
async def delete_job(
    odb_id: str,
    hard: bool = Query(False, description="If true, also delete workspace files"),
):
    """
    Remove a job.
    - soft delete (default): unload from memory + remove DB record, keep files on disk.
    - hard delete (?hard=true): same, then delete workspace directory.
    Refused while job is running (status l1_running / l2_running).
    """
    repo = _repo()
    row  = repo.get_job(odb_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{odb_id}' not found")
    if row["status"] in ("l1_running", "l2_running"):
        raise HTTPException(status_code=409,
                            detail="Cannot delete a job that is currently running")

    workspace = repo.resolve_workspace(row["workspace"], settings.data_root)

    # Order matters: unload memory first so in-flight requests see 404 immediately
    registry.unload(odb_id)
    repo.delete_job(odb_id)

    if hard and workspace and os.path.exists(workspace):
        shutil.rmtree(workspace, ignore_errors=True)

    return {"ok": True, "hard": hard}


@router.post("/{odb_id}/retry")
async def retry_job(odb_id: str):
    """
    Re-queue a failed job.  Always re-runs L1+L2 from scratch (design decision D2).
    Only allowed when status == 'error'.
    """
    repo = _repo()
    row  = repo.get_job(odb_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{odb_id}' not found")
    if row["status"] != "error":
        raise HTTPException(status_code=409,
                            detail=f"Cannot retry job with status '{row['status']}' "
                                   f"(only 'error' jobs can be retried)")

    repo.update_status(odb_id, "submitted", error_msg=None)
    return {"odb_id": odb_id, "status": "submitted"}
