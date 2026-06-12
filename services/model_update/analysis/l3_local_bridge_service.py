import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from src import job_runner as local_job_runner
from src.l3.core.config import settings
from src.l3.core.errors import ConflictError, NotFoundError, ValidationError
from src.l3.core.state import registry
from src.l3.infra.registry_repo import RegistryRepo
from src.l3.services.external_result_writer import ExternalResultWriter


def project_workspace_path(project_id: int) -> str:
    project_key = str(int(project_id))
    repo = RegistryRepo(settings.registry_db_path)
    row = repo.get_project(project_key)
    if row is not None:
        stored_workspace = str(row["workspace"] or "").strip()
        if stored_workspace:
            return str(
                Path(
                    repo.resolve_workspace(stored_workspace, settings.data_root)
                ).expanduser().resolve()
            )
    return str((Path(settings.data_root).expanduser().resolve() / project_key).resolve())


def submit_project_result_group_and_wait(
        *,
        project_id: int,
        source_path: str,
        result_group: str,
        display_name: Optional[str],
        parse_options: Optional[dict],
) -> dict:
    resolved_source_path = os.path.abspath(str(source_path))
    if not os.path.exists(resolved_source_path):
        raise NotFoundError("result file not found", {"source_path": resolved_source_path})

    project_key = str(int(project_id))
    repo = RegistryRepo(settings.registry_db_path)
    project_row = repo.get_project(project_key)
    if project_row is None:
        raise NotFoundError("project", {"project_id": int(project_id)})

    geom_status = str(project_row["geom_status"] or "").strip().lower()
    if geom_status != "ready":
        raise ValidationError(
            "project geometry must be ready before importing a local result group",
            {
                "project_id": int(project_id),
                "geom_status": geom_status,
            },
        )

    resolved_display_name = str(
        display_name or Path(resolved_source_path).stem or str(result_group)
    ).strip() or str(result_group)
    resolved_workspace = project_workspace_path(int(project_id))

    parse_options_payload = dict(parse_options or {})
    parse_options_payload["display_name"] = resolved_display_name
    parse_options_json = json.dumps(parse_options_payload) if parse_options_payload else None
    source_file = os.path.basename(resolved_source_path)

    existing = repo.get_result_group(project_key, str(result_group))
    if existing is not None:
        if str(existing["status"] or "").strip().lower() == "running":
            raise ConflictError(
                f"result_group '{result_group}' is currently being processed",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                },
            )
        repo.reset_result_group_for_resubmit(
            project_key,
            str(result_group),
            resolved_source_path,
            source_file,
            resolved_display_name,
            parse_options_json,
        )
    else:
        repo.create_result_group(
            project_id=project_key,
            result_group=str(result_group),
            display_name=resolved_display_name,
            source_path=resolved_source_path,
            source_file=source_file,
            parse_options=parse_options_json,
        )

    local_job_runner._cleanup_result_group(resolved_workspace, str(result_group))
    local_job_runner._update_result_group_status(project_key, str(result_group), "running")
    ok = local_job_runner._run_result_group(
        project_key,
        str(result_group),
        resolved_source_path,
        parse_options_json,
        resolved_workspace,
    )
    group_row = repo.get_result_group(project_key, str(result_group))
    group_payload = dict(group_row) if group_row is not None else None
    status = str((group_payload or {}).get("status") or ("ready" if ok else "error"))
    if not ok or status != "ready":
        raise ValidationError(
            "local project result-group parsing failed",
            {
                "project_id": int(project_id),
                "result_group": str(result_group),
                "status": status,
                "source_path": resolved_source_path,
                "project_result_group": group_payload,
            },
        )

    return {
        "result_group": str(result_group),
        "display_name": resolved_display_name,
        "status": status,
        "project_id": int(project_id),
        "source_path": resolved_source_path,
        "parse_options": parse_options_payload,
        "workspace": resolved_workspace,
        "project_result_group": group_payload,
    }


def write_external_field_local(odb_id: str, body: Dict[str, Any]) -> dict:
    resolved_odb_id = str(odb_id or "").strip()
    if not resolved_odb_id:
        raise ValidationError("odb_id is required for local cloud export")

    registry_entry = registry.get(resolved_odb_id)
    if registry_entry is None:
        raise NotFoundError(
            "cloud export target odb was not found in the local registry",
            {"odb_id": resolved_odb_id},
        )

    writer = ExternalResultWriter(registry_entry.workspace, str(body.get("result_group") or ""))
    total_frames = 0
    instances_written = 0
    field_type = str(body.get("type") or "").strip().lower()

    for inst_data in list(body.get("instances") or []):
        frames_raw = []
        for frame in list(inst_data.get("frames") or []):
            frames_raw.append(
                {
                    "frame_idx": int(frame["frame_idx"]),
                    "frame_value": float(frame.get("frame_value", 0.0)),
                    "description": frame.get("description"),
                    "data": [
                        {"label": int(entry["label"]), "values": list(entry.get("values") or [])}
                        for entry in list(frame.get("data") or [])
                    ],
                }
            )

        if field_type == "nodal":
            written = writer.write_nodal(
                instance=str(inst_data.get("instance") or ""),
                step=str(body.get("step_name") or ""),
                field=str(body.get("field_name") or ""),
                components=list(body.get("components") or []),
                frames=frames_raw,
            )
        elif field_type == "element":
            written = writer.write_element(
                instance=str(inst_data.get("instance") or ""),
                step=str(body.get("step_name") or ""),
                field=str(body.get("field_name") or ""),
                components=list(body.get("components") or []),
                frames=frames_raw,
            )
        else:
            raise ValidationError("external field type must be 'nodal' or 'element'", {"type": field_type})
        total_frames = max(total_frames, int(written))
        instances_written += 1

    return {
        "field_name": str(body.get("field_name") or ""),
        "step_name": str(body.get("step_name") or ""),
        "instances_written": int(instances_written),
        "frames_written": int(total_frames),
        "source": "external_local",
    }
