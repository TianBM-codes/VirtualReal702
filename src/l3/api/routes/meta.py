from typing import Optional
from fastapi import APIRouter, Query
from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.manifest_repo import ManifestRepo
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["meta"])


_EMPTY_OVERVIEW = {"instances": [], "steps": [], "fields": []}


@router.get("/meta/overview")
async def overview(odb_id: str, result_group: Optional[str] = Query(default=None)):
    idx = registry.get(odb_id)
    if idx is None:
        return ok(_EMPTY_OVERVIEW)
    manifest = ManifestRepo(idx.workspace)
    data = manifest.get_overview(result_group=result_group)
    return ok(data)


@router.get("/steps")
async def list_steps(odb_id: str):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)
    return ok(manifest.list_steps())


@router.get("/steps/{step_name}/frames")
async def list_frames(odb_id: str, step_name: str):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)
    return ok(manifest.list_frames(step_name))


@router.get("/steps/{step_name}/frames/{frame_idx}")
async def get_frame(odb_id: str, step_name: str, frame_idx: int):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)
    frame = manifest.get_frame(step_name, frame_idx)
    if frame is None:
        raise NotFoundError(
            f"Frame {frame_idx} not found in step '{step_name}'",
            {"step_name": step_name, "frame_idx": frame_idx},
        )
    return ok(frame)
