import re
from typing import Optional

from fastapi import APIRouter, Query

from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.manifest_repo import ManifestRepo
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["model-update"])

_MODEL_UPDATE_GROUP_RE = re.compile(r"^bayesian_", re.IGNORECASE)
_MODEL_UPDATE_FIELD_RE = re.compile(r"^PARAMETER_.*DELTA", re.IGNORECASE)


def _list_model_update_groups(manifest: ManifestRepo) -> set[str]:
    try:
        with manifest._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT result_group, step_name, field_name, source
                  FROM result_files
                 WHERE result_group IS NOT NULL
                   AND field_name IS NOT NULL
                """
            ).fetchall()
    except Exception:
        return set()

    groups: set[str] = set()
    for row in rows:
        result_group = str(row["result_group"] or "").strip()
        step_name = str(row["step_name"] or "").strip()
        field_name = str(row["field_name"] or "").strip()
        source = str(row["source"] or "").strip().lower()
        if not result_group:
            continue
        if _MODEL_UPDATE_GROUP_RE.match(result_group):
            groups.add(result_group)
            continue
        if source == "external" and step_name == "BayesianUpdate" and _MODEL_UPDATE_FIELD_RE.match(field_name):
            groups.add(result_group)
    return groups


@router.get("/model-update/result_groups")
async def list_model_update_result_groups(odb_id: str):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)
    all_groups = manifest.list_result_groups()
    model_update_groups = _list_model_update_groups(manifest)
    groups = [str(group or "") for group in all_groups if str(group or "") in model_update_groups]
    return ok({"result_groups": groups})


@router.get("/model-update/step")
async def step():
    return ok([{"label": "Bayesian-Update", "value": "Bayesian-Update", "frame": 0}])


@router.get("/model-update/fields")
async def list_model_update_fields(
        odb_id: str,
        result_group: str = Query(..., description="bayesian_* result group name"),
        step: Optional[str] = Query(default=None, description="analysis step name"),
):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)

    fields = []
    with manifest._get_conn() as conn:
        if step:
            rows = conn.execute(
                "SELECT step_name, field_name, source FROM result_files"
                " WHERE result_group=? AND step_name=? ORDER BY field_name",
                (result_group, step),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT step_name, field_name, source FROM result_files"
                " WHERE result_group=? ORDER BY field_name",
                (result_group,),
            ).fetchall()

    for row in rows:
        field_name = str(row["field_name"] or "").strip()
        if not field_name or not _MODEL_UPDATE_FIELD_RE.match(field_name):
            continue
        step_name = str(row["step_name"] or "").strip()
        source = str(row["source"] or "").strip().lower()
        frame_info = None
        if source == "external":
            frames = manifest.get_frames(step_name, result_group=result_group) or []
            if frames:
                first = dict(frames[0])
                frame_info = {
                    "frame_idx": int(first["frame_idx"]),
                    "frame_value": float(first["frame_value"]),
                    "frame_description": str(first["description"] or "").strip() or None,
                }
        fields.append(
            {
                "field_name": field_name,
                "source_field_name": field_name,
                "step": step_name,
                "frame_idx": None if frame_info is None else frame_info["frame_idx"],
                "frame_value": None if frame_info is None else frame_info["frame_value"],
                "frame_description": None if frame_info is None else frame_info["frame_description"],
            }
        )

    return ok({"result_group": result_group, "fields": fields})
