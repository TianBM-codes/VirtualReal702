from typing import Optional
from fastapi import APIRouter, Query
from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.manifest_repo import ManifestRepo
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["meta"])


@router.get("/meta/overview")
async def overview(odb_id: str, result_group: Optional[str] = Query(default=None)):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)
    data = manifest.get_overview(result_group=result_group)
    return ok(data)
