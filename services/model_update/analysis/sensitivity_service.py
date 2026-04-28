import json
import os
import re
import sqlite3
import subprocess
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import h5py
import numpy as np

from config import get_local_service_base_url
from db import ensure_tables_exist, get_connection
from src.inp import parse_inp
from src.inp.parameter_mapping import build_parameter_target_map
from src.l3.core.config import settings
from src.l3.core.state import registry
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services.node_table_service import get_instance_fields
from tools.odb_client import ODBClient, ODBClientError, _select_component_values

from .abaqusDSAInpGenerator import (
    COMMENT_EMPTY_SECTION,
    DEFAULT_INCLUDE_FILE,
    UPDATED_MAIN_INP,
    analyze_element_set_tasks,
    build_include_text,
    parse_main_inp,
    patch_include_line,
    patch_main_sections,
    patch_static_step_to_dsa,
    remove_blank_lines,
)
from .model_update_meta_service import resolve_abaqus_command
from .project_source_service import resolve_project_source_inp_path
from .solver_service import delete_abaqus_process_files, run_abaqus_job, run_abaqus_sensitivity_job

_POSITION_PRIORITY = ("NODAL", "ELEMENT_NODAL", "INTEGRATION_POINT")
_AGGREGATIONS = {"max_abs", "mean_abs", "max", "min", "mean"}
_VTU_POSITION_PRIORITY = ("WHOLE_ELEMENT", "INTEGRATION_POINT", "ELEMENT_NODAL", "NODAL")
_DSA_PARAMETER_TOKEN_RE = re.compile(r"^([A-Z_][A-Z0-9_]*?)(\d+)$", re.IGNORECASE)
_RESPONSE_TOKEN_RE = re.compile(r"^d_([A-Z0-9_]+?)(?:_[A-Z]+)?_?$", re.IGNORECASE)
_VECTOR_DIRECTION_ALIASES = {
    "UX": ("U", "U1"),
    "UY": ("U", "U2"),
    "UZ": ("U", "U3"),
    "RX": ("UR", "UR1"),
    "RY": ("UR", "UR2"),
    "RZ": ("UR", "UR3"),
}
_DEFAULT_PARAMETER_SCATTER = 0.25
_DEFAULT_RESPONSE_SCATTER = 0.01
_SENSITIVITY_STATUS_PENDING = -1
_SENSITIVITY_STATUS_RUNNING = 0
_SENSITIVITY_STATUS_DONE = 1
_SENSITIVITY_STATUS_LOCAL = threading.local()


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _active_sensitivity_projects() -> set:
    active = getattr(_SENSITIVITY_STATUS_LOCAL, "active_projects", None)
    if active is None:
        active = set()
        _SENSITIVITY_STATUS_LOCAL.active_projects = active
    return active


def _update_project_sensitivity_status(project_id: int, status: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE t_mt_work_condition_project
            SET sensitivity_status = %s
            WHERE project_id = %s
            """,
            (int(status), int(project_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _run_with_project_sensitivity_status(project_id: int, fn):
    active = _active_sensitivity_projects()
    if int(project_id) in active:
        return fn()

    active.add(int(project_id))
    _update_project_sensitivity_status(project_id, _SENSITIVITY_STATUS_RUNNING)
    try:
        result = fn()
    except Exception:
        _update_project_sensitivity_status(project_id, _SENSITIVITY_STATUS_PENDING)
        raise
    else:
        _update_project_sensitivity_status(project_id, _SENSITIVITY_STATUS_DONE)
        return result
    finally:
        active.discard(int(project_id))


def _workspace_path(workspace: str) -> str:
    path = os.path.abspath(workspace)
    manifest = os.path.join(path, "manifest.db")
    if not os.path.exists(manifest):
        raise NotFoundError(f"manifest.db not found under workspace '{path}'", {"workspace": path})
    return path


def _assert_safe_workspace_rebuild_path(workspace: str) -> None:
    normalized = os.path.normpath(os.path.abspath(workspace))
    drive, tail = os.path.splitdrive(normalized)
    if normalized in {drive + os.sep, os.sep} or not os.path.basename(normalized):
        raise ValidationError(
            "workspace path is too broad to rebuild safely",
            {"workspace": normalized},
        )


def _manifest_conn(workspace: str) -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(workspace, "manifest.db"), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _load_json_list(value: Optional[str]) -> list:
    if not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []


def _default_components(data_shape: tuple) -> list:
    ncomp = int(data_shape[-1]) if data_shape else 0
    return [f"C{i + 1}" for i in range(ncomp)]


def _coerce_components(raw_components: list, data_shape: tuple) -> list:
    return [str(x) for x in raw_components] if raw_components else _default_components(data_shape)


def _aggregate(values: np.ndarray, axis, mode: str) -> np.ndarray:
    if mode == "max_abs":
        return np.nanmax(np.abs(values), axis=axis)
    if mode == "mean_abs":
        return np.nanmean(np.abs(values), axis=axis)
    if mode == "max":
        return np.nanmax(values, axis=axis)
    if mode == "min":
        return np.nanmin(values, axis=axis)
    if mode == "mean":
        return np.nanmean(values, axis=axis)
    raise ValidationError(f"unsupported aggregation '{mode}'", {"aggregation": mode})


def _reduce_block(raw: np.ndarray, aggregation: str) -> np.ndarray:
    if raw.ndim < 3:
        raise ValidationError("unexpected result block rank", {"rank": raw.ndim})
    if raw.ndim == 3:
        return raw.astype(np.float64, copy=False)
    reduce_axes = tuple(range(2, raw.ndim - 1))
    reduced = _aggregate(raw.astype(np.float64, copy=False), axis=reduce_axes, mode=aggregation)
    return reduced.astype(np.float64, copy=False)


def _select_position(blocks: list, requested: Optional[str]) -> str:
    positions = {str(row["position"]) for row in blocks}
    if requested:
        if requested not in positions:
            raise NotFoundError(
                f"position '{requested}' not found for the selected result",
                {"position": requested, "available_positions": sorted(positions)},
            )
        return requested
    for candidate in _POSITION_PRIORITY:
        if candidate in positions:
            return candidate
    raise NotFoundError("no usable result blocks found", {"available_positions": sorted(positions)})


def _resolve_single(name: str, requested: Optional[str], values: Iterable[str]) -> str:
    items = sorted({str(v) for v in values})
    if requested:
        if requested not in items:
            raise NotFoundError(f"{name} '{requested}' not found", {name: requested, "available": items})
        return requested
    if len(items) == 1:
        return items[0]
    raise ValidationError(f"{name} is required because multiple values are available", {"available": items})


def _default_step_from_rows(step_rows: List[dict]) -> Optional[str]:
    if not step_rows:
        return None

    def _sort_key(row: dict):
        step_no = row.get("step_number")
        try:
            step_no = int(step_no)
        except Exception:
            step_no = 10 ** 9
        return (step_no, str(row.get("step_name") or ""))

    ordered = sorted(step_rows, key=_sort_key)
    step_name = ordered[0].get("step_name")
    return str(step_name) if step_name is not None else None


def _infer_temp_quantity_code_from_target(target_row: dict) -> str:
    component_name = str(target_row.get("component_name") or "").upper()
    source_keyword = str(target_row.get("source_keyword") or "").upper()
    if component_name == "THICKNESS" or component_name.startswith("DIM") or "SECTION" in source_keyword:
        return "T"
    return "DSA"


def _rebuild_selected_parameters_from_inp(*, project_id: int, inp_path: str) -> dict:
    # Temporary compatibility path for /sensitivity/run_and_store:
    # derive optimization-parameter rows from the current INP in memory so
    # DSA field discovery can proceed even when the project has no formal
    # user-selected optimization parameters yet.

    resolved_inp_path = os.path.abspath(inp_path)
    if not os.path.exists(resolved_inp_path):
        raise NotFoundError("input_inp not found", {"input_inp": resolved_inp_path})

    model = parse_inp(resolved_inp_path)
    parameter_definitions = getattr(model, "parameters", {}) or {}
    target_map = build_parameter_target_map(model)

    design_parameters = sorted(
        list(getattr(model, "design_parameters", []) or []),
        key=lambda item: (int(getattr(item, "order", 0) or 0), str(getattr(item, "name", "") or "")),
    )
    ordered_parameter_names = []
    seen_parameter_names = set()
    for item in design_parameters:
        name = str(getattr(item, "name", "") or "").strip()
        if not name or name in seen_parameter_names:
            continue
        seen_parameter_names.add(name)
        ordered_parameter_names.append(name)
    for name in sorted(parameter_definitions.keys()):
        text = str(name or "").strip()
        if not text or text in seen_parameter_names:
            continue
        seen_parameter_names.add(text)
        ordered_parameter_names.append(text)

    created_rows = []
    optimization_parameter_rows = []
    for offset, parameter_name in enumerate(ordered_parameter_names, start=1):
        definition = parameter_definitions.get(parameter_name)
        scalar_value = getattr(definition, "scalar_value", None) if definition is not None else None
        target_rows = [dict(row) for row in (target_map.get(parameter_name) or [])]
        primary_target = dict(target_rows[0]) if target_rows else {}
        quantity_code = _infer_temp_quantity_code_from_target(primary_target) if primary_target else "DSA"

        set_name = str(primary_target.get("set_name") or f"TMP_PARAM_{offset}")
        set_type = str(primary_target.get("set_type") or "ELSET")
        set_scope = str(primary_target.get("set_scope") or "PART")
        instance_name = primary_target.get("instance_name")
        part_name = primary_target.get("part_name")

        extra_json = {
            "source": "run_and_store_temp",
            "scalar_value": scalar_value,
            "target_rows": target_rows,
        }
        optimization_parameter_rows.append(
            {
                "id": offset,
                "parameter_group_name": str(parameter_name),
                "parameter_name": str(parameter_name),
                "quantity_code": quantity_code,
                "selection_mode": "GLOBAL",
                "set_name": set_name,
                "set_type": set_type,
                "set_scope": set_scope,
                "instance_name": instance_name,
                "part_name": part_name,
                "element_label": None,
                "lower": float(scalar_value) if scalar_value is not None else 0.0,
                "upper": float(scalar_value) if scalar_value is not None else 0.0,
                "prob_id": 0,
                "scatter": float(_DEFAULT_PARAMETER_SCATTER),
                "scalar_value": scalar_value,
                "extra_json": extra_json,
            }
        )
        created_rows.append(
            {
                "parameter_name": str(parameter_name),
                "quantity_code": quantity_code,
                "set_name": set_name,
                "set_type": set_type,
                "set_scope": set_scope,
                "instance_name": instance_name,
                "part_name": part_name,
                "scalar_value": scalar_value,
                "target_row_count": len(target_rows),
            }
        )

    return {
        "project_id": int(project_id),
        "inp_path": resolved_inp_path,
        "selected_parameter_count": len(created_rows),
        "selected_parameters_preview": created_rows[:20],
        "optimization_parameter_rows": optimization_parameter_rows,
    }


def build_workspace_from_odb(
        odb_path: str,
        workspace: str,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
) -> dict:
    # Bayesian/sensitivity workflows query results through the generated workspace,
    # not directly from the raw ODB file.
    odb_abs = os.path.abspath(odb_path)
    if not os.path.exists(odb_abs):
        raise NotFoundError(f"odb file not found: {odb_abs}", {"odb_path": odb_abs})

    workspace_abs = os.path.abspath(workspace)
    _assert_safe_workspace_rebuild_path(workspace_abs)
    if os.path.isdir(workspace_abs):
        # Rebuild the workspace from scratch so stale manifest.db schemas from
        # previous runs do not leak into the current ODB conversion.
        shutil.rmtree(workspace_abs, ignore_errors=True)
    os.makedirs(os.path.dirname(workspace_abs) or workspace_abs, exist_ok=True)
    cmd = [
        python3 or sys.executable,
        os.path.join(_repo_root(), "tools", "run_l1.py"),
        "--odb",
        odb_abs,
        "--out",
        workspace_abs,
        "--abaqus",
        resolve_abaqus_command(abaqus),
    ]
    if keep_raw:
        cmd.append("--keep-raw")

    result = subprocess.run(
        cmd,
        cwd=_repo_root(),
        capture_output=True
    )

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    if result.returncode != 0:
        raise ValidationError(
            "failed to build workspace from odb",
            {
                "odb_path": odb_abs,
                "workspace": workspace_abs,
                "returncode": result.returncode,
                "stdout": stdout[-4000:],
                "stderr": stderr[-4000:],
            },
        )

    manifest = os.path.join(workspace_abs, "manifest.db")
    return {
        "odb_path": odb_abs,
        "workspace": workspace_abs,
        "manifest_db": manifest if os.path.exists(manifest) else None,
        "stdout_tail": stdout[-4000:],
    }


def get_sensitivity_overview(workspace: str) -> dict:
    workspace_abs = _workspace_path(workspace)
    conn = _manifest_conn(workspace_abs)
    try:
        steps = [dict(row) for row in conn.execute("SELECT * FROM steps ORDER BY step_name").fetchall()]
        frames = [
            dict(row)
            for row in conn.execute(
                "SELECT step_name, frame_idx, frame_value, description FROM frames ORDER BY step_name, frame_idx"
            ).fetchall()
        ]
        instances = [
            dict(row)
            for row in conn.execute(
                "SELECT instance_name, part_name, node_count, elem_count, bbox_min, bbox_max "
                "FROM instances ORDER BY instance_name"
            ).fetchall()
        ]
        result_files = []
        for row in conn.execute(
                "SELECT step_name, field_name, file_path, components, positions, val_min, val_max "
                "FROM result_files ORDER BY step_name, field_name"
        ).fetchall():
            item = dict(row)
            item["components"] = _load_json_list(item.get("components"))
            item["positions"] = _load_json_list(item.get("positions"))
            result_files.append(item)

        return {
            "workspace": workspace_abs,
            "steps": steps,
            "frames": frames,
            "instances": instances,
            "result_files": result_files,
        }
    finally:
        conn.close()


def build_sensitivity_table(
        workspace: str,
        step: Optional[str],
        field: Optional[str],
        instance: Optional[str],
        position: Optional[str] = None,
        components: Optional[List[str]] = None,
        frame_indices: Optional[List[int]] = None,
        entity_labels: Optional[List[int]] = None,
        aggregation: str = "max_abs",
) -> dict:
    if aggregation not in _AGGREGATIONS:
        raise ValidationError(
            f"unsupported aggregation '{aggregation}'",
            {"aggregation": aggregation, "allowed": sorted(_AGGREGATIONS)},
        )

    workspace_abs = _workspace_path(workspace)
    conn = _manifest_conn(workspace_abs)
    try:
        step_name = _resolve_single("step", step, [row["step_name"] for row in conn.execute("SELECT step_name FROM steps")])
        field_name = _resolve_single(
            "field",
            field,
            [
                row["field_name"]
                for row in conn.execute("SELECT field_name FROM result_files WHERE step_name = ?", (step_name,))
            ],
        )
        instance_name = _resolve_single(
            "instance",
            instance,
            [
                row["instance_name"]
                for row in conn.execute(
                "SELECT DISTINCT instance_name FROM result_blocks WHERE step_name = ? AND field_name = ?",
                (step_name, field_name),
            )
            ],
        )

        result_file = conn.execute(
            "SELECT file_path, components FROM result_files WHERE step_name = ? AND field_name = ?",
            (step_name, field_name),
        ).fetchone()
        if not result_file:
            raise NotFoundError(
                f"result file not found for step='{step_name}' field='{field_name}'",
                {"step": step_name, "field": field_name},
            )

        blocks = conn.execute(
            "SELECT position, elem_type, h5_path FROM result_blocks "
            "WHERE step_name = ? AND field_name = ? AND instance_name = ? "
            "ORDER BY position, elem_type",
            (step_name, field_name, instance_name),
        ).fetchall()
        if not blocks:
            raise NotFoundError(
                f"no result blocks for instance '{instance_name}'",
                {"step": step_name, "field": field_name, "instance": instance_name},
            )

        chosen_position = _select_position(blocks, position)
        position_blocks = [row for row in blocks if str(row["position"]) == chosen_position]

        frame_rows = conn.execute(
            "SELECT frame_idx, frame_value, description FROM frames WHERE step_name = ? ORDER BY frame_idx",
            (step_name,),
        ).fetchall()
        all_frame_indices = [int(row["frame_idx"]) for row in frame_rows]
        if not all_frame_indices:
            raise NotFoundError(f"no frames found for step '{step_name}'", {"step": step_name})

        if frame_indices:
            requested_frames = [int(i) for i in frame_indices]
            invalid = [idx for idx in requested_frames if idx not in all_frame_indices]
            if invalid:
                raise ValidationError(
                    "frame_indices contains values outside the available range",
                    {"invalid": invalid, "available": all_frame_indices},
                )
            selected_frame_indices = requested_frames
        else:
            selected_frame_indices = all_frame_indices

        frame_lookup = {int(row["frame_idx"]): row for row in frame_rows}
        h5_path = os.path.join(workspace_abs, str(result_file["file_path"]))
        if not os.path.exists(h5_path):
            raise NotFoundError(f"result h5 file not found: {h5_path}", {"file_path": h5_path})

        per_block = []
        with h5py.File(h5_path, "r") as h5:
            for row in position_blocks:
                group = h5[str(row["h5_path"])]
                labels = group["labels"][:].astype(np.int64)
                data = group["data"][selected_frame_indices]
                reduced = _reduce_block(data, aggregation=aggregation)
                block_components = _coerce_components(_load_json_list(result_file["components"]), reduced.shape)
                per_block.append(
                    {
                        "elem_type": row["elem_type"],
                        "labels": labels,
                        "values": reduced,
                        "components": block_components,
                    }
                )

        if not per_block:
            raise NotFoundError("no readable result blocks found", {"position": chosen_position})

        available_components = per_block[0]["components"]
        if components:
            unknown = [comp for comp in components if comp not in available_components]
            if unknown:
                raise ValidationError(
                    "components contains unknown names",
                    {"unknown": unknown, "available": available_components},
                )
            component_names = list(components)
        else:
            component_names = list(available_components)
        component_indices = [available_components.index(name) for name in component_names]

        row_meta = []
        rows = []
        columns = []
        matrix = []

        if entity_labels:
            ordered_labels = [int(label) for label in entity_labels]
            columns = [
                {
                    "key": f"L{label}_{comp}",
                    "title": f"{label}:{comp}",
                    "type": "number",
                    "entity_label": label,
                    "component": comp,
                }
                for label in ordered_labels
                for comp in component_names
            ]

            label_values = {}
            for block in per_block:
                for local_idx, label in enumerate(block["labels"].tolist()):
                    if int(label) not in label_values:
                        label_values[int(label)] = block["values"][:, local_idx, :]

            missing_labels = [label for label in ordered_labels if label not in label_values]
            for frame_pos, frame_idx in enumerate(selected_frame_indices):
                frame_info = frame_lookup[frame_idx]
                row_meta.append(
                    {
                        "step": step_name,
                        "frame_idx": frame_idx,
                        "frame_value": frame_info["frame_value"],
                        "description": frame_info["description"],
                    }
                )
                row = {
                    "row_id": f"{step_name}:{frame_idx}",
                    "frame_idx": frame_idx,
                    "frame_value": frame_info["frame_value"],
                }
                row_values = []
                for label in ordered_labels:
                    label_array = label_values.get(label)
                    for comp_name, comp_idx in zip(component_names, component_indices):
                        key = f"L{label}_{comp_name}"
                        value = float(label_array[frame_pos, comp_idx]) if label_array is not None else None
                        row[key] = value
                        row_values.append(np.nan if value is None else value)
                rows.append(row)
                matrix.append(row_values)
        else:
            columns = [{"key": comp, "title": comp, "type": "number", "component": comp} for comp in component_names]
            combined = np.concatenate([block["values"] for block in per_block], axis=1)
            reduced_per_frame = _aggregate(combined[:, :, component_indices], axis=1, mode=aggregation)
            missing_labels = []

            for frame_pos, frame_idx in enumerate(selected_frame_indices):
                frame_info = frame_lookup[frame_idx]
                row_meta.append(
                    {
                        "step": step_name,
                        "frame_idx": frame_idx,
                        "frame_value": frame_info["frame_value"],
                        "description": frame_info["description"],
                    }
                )
                row = {
                    "row_id": f"{step_name}:{frame_idx}",
                    "frame_idx": frame_idx,
                    "frame_value": frame_info["frame_value"],
                }
                row_values = []
                for col_pos, comp_name in enumerate(component_names):
                    value = float(reduced_per_frame[frame_pos, col_pos])
                    row[comp_name] = value
                    row_values.append(value)
                rows.append(row)
                matrix.append(row_values)

        matrix_array = np.asarray(matrix, dtype=np.float64) if matrix else np.zeros((0, 0), dtype=np.float64)
        finite = matrix_array[np.isfinite(matrix_array)]
        abs_max = float(np.max(np.abs(finite))) if finite.size else 0.0

        return {
            "workspace": workspace_abs,
            "step": step_name,
            "field": field_name,
            "instance": instance_name,
            "position": chosen_position,
            "aggregation": aggregation,
            "components": component_names,
            "entity_labels": [int(x) for x in entity_labels] if entity_labels else None,
            "columns": columns,
            "rows": rows,
            "matrix": matrix_array.tolist(),
            "row_meta": row_meta,
            "summary": {
                "row_count": len(rows),
                "column_count": len(columns),
                "value_abs_max": abs_max,
                "missing_labels": missing_labels,
            },
        }
    finally:
        conn.close()


def _normalize_batch_no(batch_no: Optional[str]) -> str:
    value = str(batch_no or "1").strip()
    return value or "1"


def _normalize_response_display_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    parts = text.split("|")
    if len(parts) != 5:
        return text
    instance_name = str(parts[0]).strip()
    response_label = str(parts[4]).strip()
    prefix = f"{instance_name}::"
    if instance_name and response_label.startswith(prefix):
        parts[4] = response_label[len(prefix):]
    return "|".join(parts)


def _response_display_name(row_meta: dict) -> str:
    row_key = str(row_meta.get("row_key") or "").strip()
    if row_key:
        return _normalize_response_display_name(row_key)
    parts = [
        str(row_meta.get("instance") or "").strip(),
        str(row_meta.get("response_field") or "").strip(),
        str(row_meta.get("response_component") or "").strip(),
        str(row_meta.get("response_position") or "").strip(),
        str(row_meta.get("response_label") or "").strip(),
    ]
    return _normalize_response_display_name("|".join(parts))


def _ensure_finite_float(value, *, context: str, details: Optional[dict] = None) -> float:
    arr = np.asarray(value, dtype=np.float64)
    if arr.size != 1:
        raise ValidationError(
            f"{context} must resolve to a scalar float",
            {**(details or {}), "value": arr.tolist()},
        )
    scalar = float(arr.reshape(-1)[0])
    if not np.isfinite(scalar):
        raise ValidationError(
            f"{context} contains a non-finite float",
            {**(details or {}), "value": scalar},
        )
    return scalar


def _parameter_display_names(parameter_columns: List[dict]) -> List[str]:
    base_names = [
        str(item.get("parameter_name") or item.get("field") or f"parameter_{index + 1}")
        for index, item in enumerate(parameter_columns)
    ]
    totals = Counter(base_names)
    seen: Dict[str, int] = {}
    display_names: List[str] = []
    for index, item in enumerate(parameter_columns):
        base_name = base_names[index]
        seen[base_name] = seen.get(base_name, 0) + 1
        if totals[base_name] <= 1:
            display_names.append(base_name)
            continue
        field_name = str(item.get("field") or "").strip()
        suffix = field_name or str(seen[base_name])
        display_names.append(f"{base_name}|{suffix}")
    return display_names


def _load_dsa_normalized_sensitivity_matrix(**kwargs) -> dict:
    from .bayesian_service import build_dsa_normalized_sensitivity_matrix

    return build_dsa_normalized_sensitivity_matrix(**kwargs)


def _load_project_design_responses(project_id: int) -> List[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT response_no, request_no, step_name, frequency, region_type, set_name, variables_json, extra_json
            FROM t_mt_py_fem_design_response_catalog
            WHERE pid = %s
            ORDER BY response_no ASC, request_no ASC
            """,
            (int(project_id),),
        )
        rows = []
        for row in cursor.fetchall() or []:
            item = dict(row)
            variables_json = item.get("variables_json")
            extra_json = item.get("extra_json")
            item["variables"] = json.loads(variables_json) if variables_json else []
            item["extra_json"] = json.loads(extra_json) if isinstance(extra_json, str) and extra_json else (
                extra_json if isinstance(extra_json, dict) else {}
            )
            rows.append(item)
        return rows
    finally:
        cursor.close()
        conn.close()


def _load_project_thickness_parameters(project_id: int) -> List[dict]:
    return [
        row
        for row in _load_project_optimization_parameters(project_id)
        if str(row.get("quantity_code") or "").upper() in {"T", "H"}
    ]


def _capability_key(row: dict) -> tuple:
    return (
        str(row.get("set_name") or "").strip(),
        str(row.get("set_type") or "").strip(),
        str(row.get("set_scope") or "").strip(),
        str(row.get("instance_name") or "").strip(),
        str(row.get("part_name") or "").strip(),
    )


def _load_project_thickness_capabilities(project_id: int) -> Dict[tuple, dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, extra_json
            FROM t_mt_py_fem_quantity_set_capability
            WHERE pid = %s AND quantity_code IN ('T', 'H')
            """,
            (int(project_id),),
        )
        rows = [dict(row) for row in (cursor.fetchall() or [])]
        return {_capability_key(row): row for row in rows}
    finally:
        cursor.close()
        conn.close()


def _parse_optional_json_object(raw_value) -> dict:
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if not raw_value:
        return {}
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_id_list(raw_value) -> List[int]:
    if raw_value is None or raw_value == "":
        return []
    if isinstance(raw_value, (list, tuple, set)):
        values = raw_value
    else:
        values = [raw_value]

    labels = []
    for item in values:
        if item is None or item == "":
            continue
        if isinstance(item, str) and "," in item:
            labels.extend(_parse_id_list([part.strip() for part in item.split(",")]))
            continue
        labels.append(int(item))
    return labels


def _safe_dsa_set_name(parameter_name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", str(parameter_name or "").strip())
    base = base.strip("_") or "PARAM"
    raw_name = f"DSA_{base}"
    if raw_name[0].isdigit():
        raw_name = f"DSA_{raw_name}"
    return raw_name


def _unique_name(preferred: str, used_names: set) -> str:
    name = preferred
    suffix = 2
    while name in used_names:
        name = f"{preferred}_{suffix}"
        suffix += 1
    used_names.add(name)
    return name


def _jsonable_scalar(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    try:
        import decimal

        if isinstance(value, decimal.Decimal):
            return float(value)
    except Exception:
        pass
    return value


def build_project_dsa_config_preview(*, project_id: int, value_mode: str = "inherit") -> dict:
    resolved_value_mode = str(value_mode or "").strip().lower()
    if resolved_value_mode not in {"inherit", "explicit"}:
        raise ValidationError(
            "value_mode must be either 'inherit' or 'explicit'",
            {"value_mode": value_mode, "allowed": ["inherit", "explicit"]},
        )

    parameter_rows = _load_project_thickness_parameters(project_id)
    capability_rows = _load_project_thickness_capabilities(project_id)
    response_rows = _load_project_design_responses(project_id)
    warnings: List[dict] = []

    used_set_names = set()
    source_set_names = {
        str(row.get("set_name") or "").strip()
        for row in list(parameter_rows) + list(response_rows)
        if str(row.get("set_name") or "").strip()
    }
    element_sets = []

    for idx, row in enumerate(parameter_rows, start=1):
        parameter_name = str(row.get("parameter_name") or "").strip()
        if not parameter_name:
            parameter_name = f"T{idx}"
            warnings.append(
                {
                    "code": "PARAMETER_NAME_EMPTY",
                    "message": "参数名为空，预览中使用临时参数名",
                    "parameter_index": idx,
                    "fallback_parameter": parameter_name,
                }
            )

        preferred_set_name = _safe_dsa_set_name(parameter_name)
        set_name = _unique_name(preferred_set_name, used_set_names)
        if set_name != preferred_set_name:
            warnings.append(
                {
                    "code": "PARAMETER_SET_NAME_RENAMED",
                    "message": "自动生成的 DSA 集合名重复，已追加后缀避免冲突",
                    "parameter": parameter_name,
                    "preferred_set_name": preferred_set_name,
                    "set_name": set_name,
                }
            )
        if set_name in source_set_names:
            warnings.append(
                {
                    "code": "PARAMETER_SET_NAME_MAY_COLLIDE",
                    "message": "自动生成的 DSA 集合名与数据库中的已有集合名相同，请人工确认原始 inp 中没有同名集合",
                    "parameter": parameter_name,
                    "set_name": set_name,
                }
            )

        extra_json = _parse_optional_json_object(row.get("extra_json"))
        elements = _parse_id_list(extra_json.get("element_labels"))
        if not elements:
            elements = _parse_id_list(row.get("element_label"))
        if not elements:
            capability_row = capability_rows.get(_capability_key(row))
            capability_extra = _parse_optional_json_object(
                capability_row.get("extra_json") if capability_row else None
            )
            elements = _parse_id_list(capability_extra.get("element_labels"))
            if elements:
                warnings.append(
                    {
                        "code": "PARAMETER_ELEMENTS_FROM_CAPABILITY",
                        "message": "参数记录没有 element_labels，已从 quantity_set_capability 中同名壳厚集合补齐",
                        "parameter": parameter_name,
                        "source_set_name": row.get("set_name"),
                        "element_count": len(elements),
                    }
                )
        if not elements:
            warnings.append(
                {
                    "code": "PARAMETER_ELEMENTS_EMPTY",
                    "message": "参数没有 element_labels，也没有 element_label，生成器后续无法为它创建 ELSET",
                    "parameter": parameter_name,
                    "source_set_name": row.get("set_name"),
                }
            )

        item = {
            "set_name": set_name,
            "parameter": parameter_name,
            "elements": elements,
        }
        if resolved_value_mode == "explicit":
            current_value = _jsonable_scalar(row.get("scalar_value"))
            item["value"] = current_value
            if current_value is None:
                warnings.append(
                    {
                        "code": "PARAMETER_VALUE_EMPTY",
                        "message": "explicit 模式要求写入 value，但参数 current_value 为空",
                        "parameter": parameter_name,
                        "set_name": set_name,
                    }
                )

        element_sets.append(item)

    if resolved_value_mode == "inherit":
        warnings.append(
            {
                "code": "INHERIT_MODE_REQUIRES_UNIQUE_ORIGINAL_THICKNESS",
                "message": "inherit 模式依赖原始 inp 中这些单元能继承到唯一壳厚；如果一个参数覆盖多个原始 section 且厚度不同，后续生成 inp 会报错",
            }
        )

    responses = []
    for idx, row in enumerate(response_rows, start=1):
        region_type = str(row.get("region_type") or "").strip().upper()
        if region_type == "NODE":
            response_type = "node"
        elif region_type == "ELEMENT":
            response_type = "element"
        else:
            response_type = ""
            warnings.append(
                {
                    "code": "RESPONSE_REGION_TYPE_UNSUPPORTED",
                    "message": "响应 region_type 不是 NODE 或 ELEMENT，预览中保留该记录但 type 为空",
                    "response_no": row.get("response_no"),
                    "request_no": row.get("request_no"),
                    "region_type": row.get("region_type"),
                }
            )

        set_name = str(row.get("set_name") or "").strip()
        if not set_name:
            warnings.append(
                {
                    "code": "RESPONSE_SET_NAME_EMPTY",
                    "message": "响应 set_name 为空，生成器后续无法引用响应集合",
                    "response_no": row.get("response_no"),
                    "request_no": row.get("request_no"),
                }
            )

        variables = row.get("variables")
        if variables is None:
            variables_json = row.get("variables_json")
            try:
                variables = json.loads(variables_json) if variables_json else []
            except json.JSONDecodeError:
                variables = []
        variables = [str(item).strip() for item in (variables or []) if str(item).strip()]
        if not variables:
            warnings.append(
                {
                    "code": "RESPONSE_VARIABLES_EMPTY",
                    "message": "响应 variables_json 为空或不是非空列表，生成器后续无法创建 DESIGN RESPONSE",
                    "response_no": row.get("response_no"),
                    "request_no": row.get("request_no"),
                    "set_name": set_name,
                }
            )

        response_item = {
            "type": response_type,
            "set": set_name,
            "variables": variables,
        }
        responses.append(response_item)

    config_json = {
        "include_file": "include.inp",
        "main_output": "model_dsa.inp",
        "element_sets": element_sets,
        "node_sets": [],
        "responses": responses,
    }

    return {
        "project_id": int(project_id),
        "value_mode": resolved_value_mode,
        "config_json": config_json,
        "parameter_count": len(element_sets),
        "response_count": len(responses),
        "warnings": warnings,
    }


_DSA_CONFIG_BLOCKING_WARNING_CODES = {
    "PARAMETER_ELEMENTS_EMPTY",
    "PARAMETER_VALUE_EMPTY",
    "RESPONSE_REGION_TYPE_UNSUPPORTED",
    "RESPONSE_SET_NAME_EMPTY",
    "RESPONSE_VARIABLES_EMPTY",
}


def _default_dsa_output_name(input_inp: str) -> str:
    stem = Path(input_inp).stem
    return f"{stem}_dsa.inp" if stem else UPDATED_MAIN_INP


def _resolve_output_file(path_value: Optional[str], *, output_dir: str, default_name: str) -> str:
    if path_value:
        candidate = Path(path_value).expanduser()
        if not candidate.is_absolute():
            candidate = Path(output_dir) / candidate
    else:
        candidate = Path(output_dir) / default_name
    return str(candidate.resolve())


def generate_project_dsa_inp_from_db(
        *,
        project_id: int,
        input_inp: str,
        output_dir: Optional[str] = None,
        value_mode: str = "inherit",
        output_inp: Optional[str] = None,
        include_file: Optional[str] = None,
        config_file: Optional[str] = None,
) -> dict:
    ensure_tables_exist()

    input_inp_abs = os.path.abspath(input_inp)
    if not os.path.exists(input_inp_abs):
        raise NotFoundError("input_inp not found", {"input_inp": input_inp_abs})

    output_dir_abs = os.path.abspath(output_dir or os.path.dirname(input_inp_abs))
    os.makedirs(output_dir_abs, exist_ok=True)

    include_name = str(include_file or DEFAULT_INCLUDE_FILE).strip() or DEFAULT_INCLUDE_FILE
    include_ref = os.path.basename(include_name)
    include_path = _resolve_output_file(include_ref, output_dir=output_dir_abs, default_name=DEFAULT_INCLUDE_FILE)
    output_inp_path = _resolve_output_file(
        output_inp,
        output_dir=output_dir_abs,
        default_name=_default_dsa_output_name(input_inp_abs),
    )
    config_path = _resolve_output_file(
        config_file,
        output_dir=output_dir_abs,
        default_name="dsa_config.json",
    )

    if os.path.abspath(output_inp_path) == input_inp_abs:
        raise ValidationError(
            "output_inp must not overwrite input_inp",
            {"input_inp": input_inp_abs, "output_inp": output_inp_path},
        )

    preview = build_project_dsa_config_preview(project_id=project_id, value_mode=value_mode)
    blocking_warnings = [
        warning for warning in preview["warnings"]
        if warning.get("code") in _DSA_CONFIG_BLOCKING_WARNING_CODES
    ]
    if blocking_warnings:
        raise ValidationError(
            "DSA config is not complete enough to generate inp",
            {"project_id": int(project_id), "warnings": blocking_warnings},
        )

    config = json.loads(json.dumps(preview["config_json"], ensure_ascii=False))
    config["include_file"] = include_ref
    config["main_output"] = output_inp_path

    main_lines = Path(input_inp_abs).read_text(encoding="utf-8", errors="ignore").splitlines()
    parsed = parse_main_inp(main_lines)

    try:
        enriched_tasks, mother_set_to_remainder_name, generator_warnings = analyze_element_set_tasks(
            config["element_sets"],
            parsed,
        )
        include_text = build_include_text(
            config,
            enriched_tasks,
            parsed,
            mother_set_to_remainder_name,
        )
        patched_main_lines = patch_main_sections(
            main_lines,
            parsed,
            mother_set_to_remainder_name,
            comment_empty_section=COMMENT_EMPTY_SECTION,
        )
        patched_main_lines = patch_include_line(patched_main_lines, include_ref)
        patched_main_lines = patch_static_step_to_dsa(patched_main_lines, config)
        patched_main_lines = remove_blank_lines(patched_main_lines)
    except ValueError as exc:
        raise ValidationError(
            "failed to generate DSA inp from database config",
            {"project_id": int(project_id), "message": str(exc)},
        )

    Path(include_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_inp_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config_path).parent.mkdir(parents=True, exist_ok=True)
    Path(include_path).write_text(include_text, encoding="utf-8")
    Path(output_inp_path).write_text("\n".join(patched_main_lines) + "\n", encoding="utf-8")
    Path(config_path).write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    warnings = list(preview["warnings"])
    warnings.extend(
        {
            "code": "DSA_GENERATOR_WARNING",
            "message": str(message),
        }
        for message in generator_warnings
    )

    return {
        "project_id": int(project_id),
        "value_mode": preview["value_mode"],
        "input_inp": input_inp_abs,
        "analysis_inp": output_inp_path,
        "include_file": include_path,
        "config_file": config_path,
        "config_json": config,
        "parameter_count": preview["parameter_count"],
        "response_count": preview["response_count"],
        "warnings": warnings,
        "mother_set_remainders": mother_set_to_remainder_name,
    }


def _generate_sensitivity_inp_from_project_db(
        *,
        project_id: int,
        input_inp: str,
        output_dir: str,
        parameter_rows: List[dict],
        design_response_rows: List[dict],
) -> dict:
    raise ValidationError(
        "project-driven sensitivity inp generator is not implemented yet",
        {
            "project_id": int(project_id),
            "input_inp": os.path.abspath(input_inp),
            "output_dir": os.path.abspath(output_dir),
            "optimization_parameter_count": len(parameter_rows),
            "design_response_count": len(design_response_rows),
        },
    )


def _default_solver_workspace(output_dir: str, job_name: str) -> str:
    return str((Path(output_dir).expanduser().resolve() / f"{job_name}_workspace").resolve())


def _auto_merge_result_group(batch_no: str, field_prefix: str) -> str:
    response_field = _field_prefix_response_token(field_prefix) or "sensitivity"
    raw = f"sensitivity_{batch_no}_{response_field}"
    return _normalize_result_group_name(raw)


def _normalize_result_group_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    if not text:
        raise ValidationError("result_group name cannot be empty", {"value": value})
    return text[:96]


def _default_project_result_group(batch_no: str, job_name: str) -> str:
    return _normalize_result_group_name(
        f"sensitivity_batch_{batch_no}_{job_name}_{int(time.time())}"
    )


def _project_workspace_path(project_id: int) -> str:
    return os.path.abspath(os.path.join(settings.data_root, str(project_id)))


def _build_project_result_parse_options(
        *,
        step: Optional[str],
        frame: Optional[int],
        field_prefix: Optional[str],
) -> dict:
    parse_options = {
        "consistency_check": "count-only",
        "steps": [str(step)] if step else None,
        "frames": [int(frame)] if frame is not None else "all",
        "invariants": "none",
    }

    # DSA normalization needs both the sensitivity fields (for example d_U_T1)
    # and the base response field they are normalized against (for example U).
    # Passing a DSA field_prefix into the project-result extraction filters that
    # response field out of the workspace, so only preserve the prefix filter
    # for non-DSA prefixes that do not map back to a response token.
    if field_prefix and _field_prefix_response_token(field_prefix) is None:
        parse_options["field_prefix"] = str(field_prefix)

    return {key: value for key, value in parse_options.items() if value is not None}


def _resolved_workspace_frame(
        *,
        requested_frame: int,
        parse_via_project_results: bool,
) -> int:
    if not parse_via_project_results:
        return int(requested_frame)

    # Project result-group extraction is usually scoped to a single requested
    # frame. The extracted workspace then stores that one physical frame as
    # frame_idx=0, so later workspace lookups must use the remapped index.
    return 0


def _submit_project_result_group_and_wait(
        *,
        project_id: int,
        odb_path: str,
        batch_no: str,
        job_name: str,
        step: Optional[str],
        frame: Optional[int],
        field_prefix: Optional[str],
        base_url: Optional[str],
        timeout: int,
        result_group: Optional[str] = None,
        display_name: Optional[str] = None,
        wait_timeout_sec: int = 3600,
        poll_interval_sec: float = 2.0,
) -> dict:
    resolved_odb_path = os.path.abspath(str(odb_path))
    if not os.path.exists(resolved_odb_path):
        raise NotFoundError("odb file not found", {"odb_path": resolved_odb_path})

    resolved_result_group = _normalize_result_group_name(
        result_group or _default_project_result_group(batch_no, job_name)
    )
    resolved_base_url = str(base_url or get_local_service_base_url()).strip().rstrip("/")
    resolved_timeout = max(int(timeout or 0), 60)
    resolved_wait_timeout_sec = max(int(wait_timeout_sec or 0), 1)
    resolved_poll_interval = max(float(poll_interval_sec or 0), 0.1)

    parse_options = _build_project_result_parse_options(
        step=step,
        frame=frame,
        field_prefix=field_prefix,
    )

    client = ODBClient(base_url=resolved_base_url, timeout=resolved_timeout)
    try:
        submit_response = client.add_project_result_group(
            str(project_id),
            source_path=resolved_odb_path,
            result_group=resolved_result_group,
            display_name=display_name or resolved_result_group,
            parse_options=parse_options,
        )
    except ODBClientError as exc:
        details = {
            "project_id": int(project_id),
            "result_group": resolved_result_group,
            "base_url": resolved_base_url,
            "status_code": exc.status_code,
            "detail": exc.detail,
            "odb_path": resolved_odb_path,
        }
        if exc.status_code == 404:
            raise NotFoundError("project result-group api target project was not found", details) from exc
        raise ValidationError("project result-group api request failed", details) from exc

    started_at = time.monotonic()
    last_status = str(submit_response.get("status") or "pending")
    last_error_message = None
    while True:
        try:
            project_payload = client.get_project(str(project_id))
        except ODBClientError as exc:
            details = {
                "project_id": int(project_id),
                "result_group": resolved_result_group,
                "base_url": resolved_base_url,
                "status_code": exc.status_code,
                "detail": exc.detail,
            }
            if exc.status_code == 404:
                raise NotFoundError("project was not found while polling result-group status", details) from exc
            raise ValidationError("failed to poll project result-group status", details) from exc

        matched_group = None
        for row in list(project_payload.get("result_groups") or []):
            if str(row.get("result_group") or "") == resolved_result_group:
                matched_group = dict(row)
                break

        if matched_group is not None:
            last_status = str(matched_group.get("status") or last_status or "pending")
            last_error_message = matched_group.get("error_message")
            if last_status == "ready":
                workspace = _workspace_path(_project_workspace_path(project_id))
                return {
                    "project_id": int(project_id),
                    "result_group": resolved_result_group,
                    "display_name": display_name or resolved_result_group,
                    "status": last_status,
                    "workspace": workspace,
                    "base_url": resolved_base_url,
                    "parse_options": parse_options,
                    "submit_response": submit_response,
                    "waited_seconds": round(time.monotonic() - started_at, 3),
                }
            if last_status == "error":
                raise ValidationError(
                    "project result-group extraction failed",
                    {
                        "project_id": int(project_id),
                        "result_group": resolved_result_group,
                        "status": last_status,
                        "error_message": last_error_message,
                        "base_url": resolved_base_url,
                        "parse_options": parse_options,
                    },
                )

        if (time.monotonic() - started_at) >= resolved_wait_timeout_sec:
            raise ValidationError(
                "timed out waiting for project result-group to become ready",
                {
                    "project_id": int(project_id),
                    "result_group": resolved_result_group,
                    "status": last_status,
                    "error_message": last_error_message,
                    "base_url": resolved_base_url,
                    "wait_timeout_sec": resolved_wait_timeout_sec,
                    "poll_interval_sec": resolved_poll_interval,
                    "parse_options": parse_options,
                },
            )

        time.sleep(resolved_poll_interval)


def _finalize_sensitivity_store_result(
        *,
        project_id: int,
        batch_no: str,
        source_input_inp: str,
        analysis_inp_path: str,
        resolved_odb_path: Optional[str],
        matrix_payload: dict,
        solver_payload: Optional[dict] = None,
        generated_files: Optional[dict] = None,
        write_cloud_result: bool = False,
        odb_id: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 60,
        cloud_result_group: Optional[str] = None,
        cloud_step_name: str = "Sensitivity",
        cloud_field_name: str = "SENSITIVITY_CLOUD",
        deleted_process_files: Optional[List[str]] = None,
        extra_payload: Optional[dict] = None,
) -> dict:
    persisted = _persist_sensitivity_matrix(
        project_id=project_id,
        batch_no=batch_no,
        case_name=Path(source_input_inp).stem,
        matrix_payload=matrix_payload,
    )

    cloud_result = None
    if write_cloud_result:
        resolved_cloud_odb_id = str(project_id or "").strip() or _resolve_loaded_odb_id_for_workspace(matrix_payload.get("workspace"))
        if not resolved_cloud_odb_id:
            raise ValidationError(
                "cloud export via external-field api requires odb_id or a workspace already loaded in the L3 registry",
                {
                    "project_id": int(project_id),
                    "batch_no": batch_no,
                    "odb_id": project_id,
                    "workspace": matrix_payload.get("workspace"),
                },
            )
        cloud_result = _write_sensitivity_cloud_result(
            odb_id=resolved_cloud_odb_id,
            base_url=base_url or matrix_payload.get("base_url"),
            batch_no=batch_no,
            matrix_payload=matrix_payload,
            result_group=cloud_result_group,
            step_name=cloud_step_name,
            field_name=cloud_field_name,
            timeout=timeout,
        )

    result = {
        **persisted,
        "input_inp": os.path.abspath(source_input_inp),
        "analysis_inp": os.path.abspath(analysis_inp_path),
        "odb_path": os.path.abspath(resolved_odb_path) if resolved_odb_path else None,
        "workspace": matrix_payload.get("workspace"),
        "step": matrix_payload.get("step"),
        "instances": matrix_payload.get("instances"),
        "aggregation": matrix_payload.get("aggregation"),
        "frame": matrix_payload.get("frame"),
        "field_prefix": matrix_payload.get("field_prefix"),
        "solver": solver_payload.get("solver") if solver_payload else None,
        "generated_files": generated_files or (solver_payload.get("generated_files") if solver_payload else None),
        "cloud_result": cloud_result,
        "deleted_process_files": [str(path) for path in (deleted_process_files or [])],
    }
    if extra_payload:
        result.update(extra_payload)
    return result


def _resolve_loaded_odb_id_for_workspace(workspace: Optional[str]) -> Optional[str]:
    if not workspace:
        return None
    try:
        normalized_workspace = os.path.normcase(os.path.abspath(workspace))
    except Exception:
        return None

    loaded = getattr(registry, "loaded", {})
    for loaded_odb_id, model_index in dict(loaded).items():
        candidate_workspace = getattr(model_index, "workspace", None)
        if not candidate_workspace:
            continue
        if os.path.normcase(os.path.abspath(candidate_workspace)) == normalized_workspace:
            return str(loaded_odb_id)
    return None


def _response_frame_name(row_meta: dict) -> str:
    response_label = str(row_meta.get("response_label") or "").strip() or "response"
    response_field = str(row_meta.get("response_field") or "").strip() or "field"
    return f"sensitivity_{response_label}_{response_field}"


def _workspace_instance_element_labels(workspace: str, instance: str) -> List[int]:
    geom_path = ManifestRepo(workspace).get_geom_path(instance) or os.path.join(
        workspace,
        "l1",
        "geometry",
        f"{instance}.h5",
    )
    if not os.path.exists(geom_path):
        raise NotFoundError(
            "geometry HDF5 not found while assembling sensitivity cloud result",
            {"workspace": workspace, "instance": instance, "geom_path": geom_path},
        )

    labels: List[int] = []
    with h5py.File(geom_path, "r") as geom_h5:
        if "elements" not in geom_h5:
            raise ValidationError(
                "geometry HDF5 has no /elements group",
                {"workspace": workspace, "instance": instance, "geom_path": geom_path},
            )
        for etype in geom_h5["elements"]:
            labels.extend(int(item) for item in np.asarray(geom_h5[f"elements/{etype}/labels"][:], dtype=np.int64).tolist())
    return sorted(set(labels))


def _build_sensitivity_cloud_request(
        *,
        batch_no: str,
        matrix_payload: dict,
        result_group: Optional[str] = None,
        step_name: str = "Sensitivity",
        field_name: str = "SENSITIVITY_CLOUD",
) -> tuple[dict, dict]:
    parameter_columns = list(matrix_payload.get("parameter_columns") or [])
    response_rows = list(matrix_payload.get("response_rows") or [])
    matrix = np.asarray(matrix_payload.get("matrix") or [], dtype=np.float64)

    if not parameter_columns:
        raise ValidationError("parameter_columns are required for sensitivity cloud export")
    if not response_rows:
        raise ValidationError("response_rows are required for sensitivity cloud export")
    if matrix.ndim != 2:
        raise ValidationError(
            "sensitivity matrix must be a 2D array for cloud export",
            {"shape": list(matrix.shape)},
        )
    if matrix.shape != (len(response_rows), len(parameter_columns)):
        raise ValidationError(
            "sensitivity matrix dimensions do not match row/column metadata for cloud export",
            {
                "matrix_shape": list(matrix.shape),
                "response_count": len(response_rows),
                "parameter_count": len(parameter_columns),
            },
        )

    resolved_result_group = str(result_group or f"sensitivity_batch_{batch_no}")
    resolved_step_name = str(step_name or "Sensitivity").strip() or "Sensitivity"
    resolved_field_name = str(field_name or "SENSITIVITY_CLOUD").strip() or "SENSITIVITY_CLOUD"
    resolved_workspace = os.path.abspath(str(matrix_payload.get("workspace"))) if matrix_payload.get("workspace") else None
    components = ["SENSITIVITY"]

    instance_frames: Dict[str, List[dict]] = {}
    instance_counts: Dict[str, List[int]] = {}
    instance_label_cache: Dict[str, List[int]] = {}

    for frame_index, row_meta in enumerate(response_rows):
        per_instance_labels: Dict[str, Dict[int, float]] = {}
        for column_index, column_meta in enumerate(parameter_columns):
            mapping = dict(column_meta.get("element_mapping") or {})
            target_kind = str(mapping.get("target_kind") or "").lower()
            if target_kind not in {"cell", ""}:
                raise ValidationError(
                    "sensitivity cloud export currently supports element targets only",
                    {
                        "target_kind": mapping.get("target_kind"),
                        "parameter_name": column_meta.get("parameter_name"),
                        "field": column_meta.get("field"),
                    },
                )

            scalar_value = _ensure_finite_float(
                matrix[frame_index, column_index],
                context="sensitivity cloud matrix value",
                details={
                    "frame_index": int(frame_index),
                    "component_index": int(column_index),
                    "parameter_name": column_meta.get("parameter_name"),
                    "field": column_meta.get("field"),
                },
            )
            for scope_name, labels in dict(mapping.get("targets_by_scope") or {}).items():
                instance_name = str(scope_name or "").strip()
                if not instance_name:
                    continue
                if instance_name not in per_instance_labels:
                    if resolved_workspace:
                        instance_label_cache.setdefault(
                            instance_name,
                            _workspace_instance_element_labels(resolved_workspace, instance_name),
                        )
                        per_instance_labels[instance_name] = {
                            int(label): 0.0 for label in instance_label_cache[instance_name]
                        }
                    else:
                        per_instance_labels[instance_name] = {}
                label_map = per_instance_labels[instance_name]
                for label in labels or []:
                    element_label = int(label)
                    current_value = label_map.get(element_label)
                    if current_value is not None and not np.isclose(current_value, 0.0) and not np.isclose(current_value, scalar_value):
                        raise ValidationError(
                            "multiple sensitivity parameters map to the same element in assembled cloud export",
                            {
                                "frame_index": int(frame_index),
                                "response": dict(row_meta),
                                "instance": instance_name,
                                "element_label": element_label,
                                "existing_value": float(current_value),
                                "incoming_value": float(scalar_value),
                                "parameter_name": column_meta.get("parameter_name"),
                                "field": column_meta.get("field"),
                            },
                        )
                    label_map[element_label] = float(scalar_value)

        for instance_name, label_map in sorted(per_instance_labels.items()):
            instance_frame_entry = {
                "frame_idx": frame_index,
                "frame_value": float(frame_index + 1),
                "description": _response_frame_name(dict(row_meta)),
                "data": [
                    {"label": int(label), "values": [float(value)]}
                    for label, value in sorted(label_map.items())
                ],
            }
            instance_frames.setdefault(instance_name, []).append(instance_frame_entry)
            instance_counts.setdefault(instance_name, []).append(len(instance_frame_entry["data"]))

    if not instance_frames:
        raise ValidationError("no element targets were resolved for sensitivity cloud export")

    instances_payload = [
        {"instance": instance_name, "frames": frames}
        for instance_name, frames in sorted(instance_frames.items())
    ]
    response_frames = [
        {
            "frame_idx": index,
            "frame_value": float(index + 1),
            "description": _response_frame_name(dict(row_meta)),
            "response": dict(row_meta),
        }
        for index, row_meta in enumerate(response_rows)
    ]

    request_body = {
        "step_name": resolved_step_name,
        "field_name": resolved_field_name,
        "components": components,
        "result_group": resolved_result_group,
        "type": "element",
        "instances": instances_payload,
    }
    metadata = {
        "result_group": resolved_result_group,
        "step": resolved_step_name,
        "field": resolved_field_name,
        "position": "ELEMENT_NODAL",
        "frame_count": len(response_rows),
        "frames": response_frames,
        "components": components,
        "instances": [str(item["instance"]) for item in instances_payload],
        "instance_element_counts": {
            instance_name: [int(count) for count in counts]
            for instance_name, counts in sorted(instance_counts.items())
        },
    }
    return request_body, metadata


def _write_sensitivity_cloud_result(
        *,
        odb_id: str,
        base_url: Optional[str],
        batch_no: str,
        matrix_payload: dict,
        result_group: Optional[str] = None,
        step_name: str = "Sensitivity",
        field_name: str = "SENSITIVITY_CLOUD",
        timeout: int = 60,
) -> dict:
    resolved_odb_id = str(odb_id or "").strip()
    if not resolved_odb_id:
        raise ValidationError("odb_id is required for cloud export via external-field api")

    request_body, metadata = _build_sensitivity_cloud_request(
        batch_no=batch_no,
        matrix_payload=matrix_payload,
        result_group=result_group,
        step_name=step_name,
        field_name=field_name,
    )
    resolved_base_url = str(base_url or get_local_service_base_url()).strip().rstrip("/")
    client = ODBClient(base_url=resolved_base_url, timeout=timeout)
    try:
        write_response = client.post_external_field(resolved_odb_id, request_body)
    except ODBClientError as exc:
        details = {
            "odb_id": resolved_odb_id,
            "base_url": resolved_base_url,
            "status_code": exc.status_code,
        }
        if exc.status_code == 404:
            raise NotFoundError("external-field api target odb was not found", details) from exc
        raise ValidationError(
            "external-field api request failed",
            {**details, "detail": exc.detail},
        ) from exc

    result = dict(metadata)
    result.update(
        {
            "odb_id": resolved_odb_id,
            "base_url": resolved_base_url,
            "write_response": write_response,
            "query_hint": {
                "endpoint": "/api/odb/{odb_id}/results/frame-scalars",
                "odb_id": resolved_odb_id,
                "result_group": metadata["result_group"],
                "step": metadata["step"],
                "field": metadata["field"],
                "frame": 0,
                "component_idx": 0,
            },
        }
    )
    return result


def _load_existing_analysis_run_ids(cursor, *, project_id: int, batch_no: str) -> List[int]:
    cursor.execute(
        """
        SELECT id
        FROM t_mt_py_fem_analysis_run
        WHERE project_id = %s AND run_no = %s
        ORDER BY id DESC
        """,
        (int(project_id), str(batch_no)),
    )
    return [int(row["id"]) for row in (cursor.fetchall() or [])]


def _delete_sensitivity_children(cursor, analysis_run_ids: List[int]) -> None:
    if not analysis_run_ids:
        return
    placeholders = ", ".join(["%s"] * len(analysis_run_ids))
    params = tuple(int(item) for item in analysis_run_ids)
    cursor.execute(
        f"DELETE FROM t_mt_py_fem_sensitivity_result WHERE analysis_run_id IN ({placeholders})",
        params,
    )
    cursor.execute(
        f"DELETE FROM t_mt_py_fem_response_def WHERE analysis_run_id IN ({placeholders})",
        params,
    )
    cursor.execute(
        f"DELETE FROM t_mt_py_fem_parameter_def WHERE analysis_run_id IN ({placeholders})",
        params,
    )


def _upsert_analysis_run(
        cursor,
        *,
        project_id: int,
        batch_no: str,
        case_name: str,
) -> int:
    analysis_run_ids = _load_existing_analysis_run_ids(
        cursor,
        project_id=project_id,
        batch_no=batch_no,
    )
    if analysis_run_ids:
        keep_id = int(analysis_run_ids[0])
        _delete_sensitivity_children(cursor, analysis_run_ids)
        if len(analysis_run_ids) > 1:
            placeholders = ", ".join(["%s"] * (len(analysis_run_ids) - 1))
            cursor.execute(
                f"DELETE FROM t_mt_py_fem_analysis_run WHERE id IN ({placeholders})",
                tuple(int(item) for item in analysis_run_ids[1:]),
            )
        cursor.execute(
            """
            UPDATE t_mt_py_fem_analysis_run
            SET case_name = %s
            WHERE id = %s
            """,
            (str(case_name), keep_id),
        )
        return keep_id

    cursor.execute(
        """
        INSERT INTO t_mt_py_fem_analysis_run (project_id, case_name, run_no)
        VALUES (%s, %s, %s)
        """,
        (int(project_id), str(case_name), str(batch_no)),
    )
    return int(cursor.lastrowid)


def _persist_sensitivity_matrix(
        *,
        project_id: int,
        batch_no: str,
        case_name: str,
        matrix_payload: dict,
) -> dict:
    response_rows = list(matrix_payload.get("response_rows") or [])
    parameter_columns = list(matrix_payload.get("parameter_columns") or [])
    matrix = np.asarray(matrix_payload.get("matrix") or [], dtype=np.float64)

    if matrix.shape != (len(response_rows), len(parameter_columns)):
        raise ValidationError(
            "normalized sensitivity matrix dimensions do not match row/column metadata",
            {
                "matrix_shape": list(matrix.shape),
                "response_count": len(response_rows),
                "parameter_count": len(parameter_columns),
            },
        )

    response_names = [_response_display_name(item) for item in response_rows]
    parameter_names = _parameter_display_names(parameter_columns)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        analysis_run_id = _upsert_analysis_run(
            cursor,
            project_id=project_id,
            batch_no=batch_no,
            case_name=case_name,
        )

        response_ids: List[int] = []
        for index, response_name in enumerate(response_names, start=1):
            cursor.execute(
                """
                INSERT INTO t_mt_py_fem_response_def (
                    analysis_run_id, response_code, response_name, unit, seq_no, project_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (analysis_run_id, f"R{index:04d}", str(response_name), None, index, project_id),
            )
            response_ids.append(int(cursor.lastrowid))

        parameter_ids: List[int] = []
        for index, parameter_name in enumerate(parameter_names, start=1):
            cursor.execute(
                """
                INSERT INTO t_mt_py_fem_parameter_def (
                    analysis_run_id, param_code, param_name, unit, seq_no
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (analysis_run_id, f"P{index:04d}", str(parameter_name), None, index),
            )
            parameter_ids.append(int(cursor.lastrowid))

        for row_index, response_id in enumerate(response_ids):
            for col_index, parameter_id in enumerate(parameter_ids):
                cursor.execute(
                    """
                    INSERT INTO t_mt_py_fem_sensitivity_result (
                        analysis_run_id, parameter_id, response_id, sensitivity_value
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        analysis_run_id,
                        parameter_id,
                        response_id,
                        float(matrix[row_index, col_index]),
                    ),
                )

        conn.commit()
        return {
            "analysis_run_id": analysis_run_id,
            "project_id": int(project_id),
            "batch_no": str(batch_no),
            "case_name": str(case_name),
            "response_names": response_names,
            "parameter_names": parameter_names,
            "response_count": len(response_names),
            "parameter_count": len(parameter_names),
            "point_count": int(matrix.size),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _load_stored_sensitivity_run(*, project_id: int, batch_no: Optional[str]) -> dict:
    ensure_tables_exist()
    normalized_batch_no = _normalize_batch_no(batch_no)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, project_id, case_name, run_no, created_at
            FROM t_mt_py_fem_analysis_run
            WHERE project_id = %s AND run_no = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(project_id), normalized_batch_no),
        )
        analysis_run = cursor.fetchone()
        if not analysis_run:
            raise NotFoundError(
                "stored sensitivity result not found",
                {"project_id": int(project_id), "batch_no": normalized_batch_no},
            )

        analysis_run_id = int(analysis_run["id"])
        cursor.execute(
            """
            SELECT id, response_code, response_name, seq_no
            FROM t_mt_py_fem_response_def
            WHERE analysis_run_id = %s
            ORDER BY seq_no ASC, id ASC
            """,
            (analysis_run_id,),
        )
        response_rows = [dict(row) for row in (cursor.fetchall() or [])]

        cursor.execute(
            """
            SELECT id, param_code, param_name, seq_no
            FROM t_mt_py_fem_parameter_def
            WHERE analysis_run_id = %s
            ORDER BY seq_no ASC, id ASC
            """,
            (analysis_run_id,),
        )
        parameter_rows = [dict(row) for row in (cursor.fetchall() or [])]

        cursor.execute(
            """
            SELECT parameter_id, response_id, sensitivity_value
            FROM t_mt_py_fem_sensitivity_result
            WHERE analysis_run_id = %s
            """,
            (analysis_run_id,),
        )
        result_rows = [dict(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()

    response_index = {int(row["id"]): idx for idx, row in enumerate(response_rows)}
    parameter_index = {int(row["id"]): idx for idx, row in enumerate(parameter_rows)}
    matrix: List[List[Optional[float]]] = [
        [None for _ in parameter_rows]
        for _ in response_rows
    ]
    for row in result_rows:
        row_idx = response_index.get(int(row["response_id"]))
        col_idx = parameter_index.get(int(row["parameter_id"]))
        if row_idx is None or col_idx is None:
            continue
        matrix[row_idx][col_idx] = float(row["sensitivity_value"])

    row_names = [_normalize_response_display_name(row["response_name"]) for row in response_rows]
    col_names = [str(row["param_name"]) for row in parameter_rows]
    return {
        "analysis_run_id": analysis_run_id,
        "project_id": int(analysis_run["project_id"]),
        "batch_no": str(analysis_run["run_no"]),
        "case_name": analysis_run.get("case_name"),
        "created_at": analysis_run.get("created_at").isoformat() if analysis_run.get("created_at") else None,
        "row_names": row_names,
        "col_names": col_names,
        "matrix": matrix,
    }


def store_dsa_sensitivity_results(
        *,
        project_id: int,
        batch_no: Optional[str] = None,
        input_inp: str,
        output_dir: Optional[str] = None,
        odb_id: Optional[str] = None,
        base_url: Optional[str] = None,
        workspace: Optional[str] = None,
        odb_path: Optional[str] = None,
        step: Optional[str] = None,
        instances: Optional[List[str]] = None,
        field_prefix: str = "d_U_",
        response_component: Optional[str] = None,
        position: Optional[str] = None,
        aggregation: str = "max_abs",
        frame: int = 0,
        response_elset: Optional[str] = None,
        response_nset: Optional[str] = None,
        response_frequency: int = 1,
        node_vars: Optional[List[str]] = None,
        element_vars: Optional[List[str]] = None,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
        job_name: Optional[str] = None,
        cpus: Optional[int] = None,
        interactive: bool = True,
        run_solver: bool = True,
        timeout_sec: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
        write_cloud_result: bool = False,
        cloud_result_group: Optional[str] = None,
        cloud_step_name: str = "Sensitivity",
        cloud_field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    ensure_tables_exist()

    input_inp_abs = os.path.abspath(input_inp)
    if not os.path.exists(input_inp_abs):
        raise NotFoundError("input_inp not found", {"input_inp": input_inp_abs})

    normalized_batch_no = _normalize_batch_no(batch_no)

    def _run():
        resolved_workspace = os.path.abspath(workspace) if workspace else None
        resolved_odb_path = os.path.abspath(odb_path) if odb_path else None
        solver_payload = None
        analysis_inp_path = input_inp_abs

        if run_solver or (not resolved_workspace and not resolved_odb_path):
            solver_payload = run_abaqus_sensitivity_job(
                input_inp=input_inp_abs,
                output_dir=output_dir,
                response_elset=response_elset,
                response_nset=response_nset,
                response_frequency=int(response_frequency),
                node_vars=node_vars,
                element_vars=element_vars,
                abaqus=abaqus,
                job_name=job_name,
                cpus=cpus,
                interactive=interactive,
                run_solver=run_solver,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
            )
            generated_files = solver_payload.get("generated_files") or {}
            generated_analysis_inp = generated_files.get("analysis_inp")
            if generated_analysis_inp:
                analysis_inp_path = os.path.abspath(str(generated_analysis_inp))
            if run_solver:
                solver_result = solver_payload.get("solver") or {}
                if not solver_result.get("ok"):
                    raise ValidationError(
                        "abaqus sensitivity solver run failed",
                        {
                            "project_id": int(project_id),
                            "batch_no": normalized_batch_no,
                            "returncode": solver_result.get("returncode"),
                            "stdout_tail": solver_result.get("stdout_tail"),
                            "stderr_tail": solver_result.get("stderr_tail"),
                        },
                    )
                resolved_odb_path = (solver_result.get("artifacts") or {}).get("odb")
                if not resolved_odb_path:
                    raise NotFoundError(
                        "odb artifact not found after abaqus sensitivity run",
                        {"project_id": int(project_id), "batch_no": normalized_batch_no},
                    )

        if not run_solver and not resolved_workspace and not resolved_odb_path:
            raise ValidationError(
                "workspace or odb_path is required when run_solver is disabled",
                {"workspace": workspace, "odb_path": odb_path, "run_solver": run_solver},
            )

        matrix_payload = _load_dsa_normalized_sensitivity_matrix(
            project_id=project_id,
            odb_id=odb_id,
            base_url=base_url,
            inp_path=analysis_inp_path,
            workspace=resolved_workspace,
            odb_path=resolved_odb_path,
            workspace_root=output_dir,
            step=step,
            instances=instances,
            field_prefix=field_prefix,
            response_component=response_component,
            position=position,
            aggregation=aggregation,
            frame=frame,
            abaqus=abaqus,
            python3=python3,
            keep_raw=keep_raw,
            timeout=timeout,
        )
        return _finalize_sensitivity_store_result(
            project_id=project_id,
            batch_no=normalized_batch_no,
            source_input_inp=input_inp_abs,
            analysis_inp_path=analysis_inp_path,
            resolved_odb_path=resolved_odb_path,
            matrix_payload=matrix_payload,
            solver_payload=solver_payload,
            write_cloud_result=write_cloud_result,
            odb_id=odb_id,
            base_url=base_url,
            timeout=timeout,
            cloud_result_group=cloud_result_group,
            cloud_step_name=cloud_step_name,
            cloud_field_name=cloud_field_name,
        )

    return _run_with_project_sensitivity_status(project_id, _run)


def run_sensitivity_inp_and_store(
        *,
        project_id: int,
        batch_no: Optional[str] = None,
        input_inp: str,
        output_dir: str,
        step: str,
        instances: List[str],
        field_prefix: str,
        response_component: str,
        position: str,
        aggregation: str = "max_abs",
        frame: int = 0,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        base_url: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
        job_name: Optional[str] = None,
        cpus: Optional[int] = None,
        interactive: bool = True,
        timeout_sec: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
        cleanup_process_files: bool = True,
        parse_via_project_results: bool = True,
        project_result_group: Optional[str] = None,
        project_result_display_name: Optional[str] = None,
        project_result_wait_timeout_sec: int = 3600,
        project_result_poll_interval_sec: float = 2.0,
        write_cloud_result: bool = False,
        cloud_result_group: Optional[str] = None,
        cloud_step_name: str = "Sensitivity",
        cloud_field_name: str = "SENSITIVITY_CLOUD",
        merge_fields: bool = True,
        merge_result_group: Optional[str] = None,
) -> dict:
    ensure_tables_exist()

    input_inp_abs = os.path.abspath(input_inp)
    if not os.path.exists(input_inp_abs):
        raise NotFoundError("input_inp not found", {"input_inp": input_inp_abs})

    output_dir_abs = os.path.abspath(output_dir)
    os.makedirs(output_dir_abs, exist_ok=True)
    normalized_batch_no = _normalize_batch_no(batch_no)
    resolved_merge_result_group = merge_result_group or _auto_merge_result_group(
        batch_no=normalized_batch_no, field_prefix=field_prefix
    )
    workspace_frame = _resolved_workspace_frame(
        requested_frame=frame,
        parse_via_project_results=parse_via_project_results,
    )

    def _run():
        solver_payload = run_abaqus_job(
            input_inp=input_inp_abs,
            output_dir=output_dir_abs,
            abaqus=abaqus,
            job_name=job_name,
            cpus=cpus,
            interactive=interactive,
            run_solver=True,
            timeout_sec=timeout_sec,
            extra_args=extra_args,
        )
        solver_result = solver_payload.get("solver") or {}
        if not solver_result.get("ok"):
            raise ValidationError(
                "abaqus sensitivity solver run failed",
                {
                    "project_id": int(project_id),
                    "batch_no": normalized_batch_no,
                    "returncode": solver_result.get("returncode"),
                    "stdout_tail": solver_result.get("stdout_tail"),
                    "stderr_tail": solver_result.get("stderr_tail"),
                },
            )

        generated_files = solver_payload.get("generated_files") or {}
        analysis_inp_path = os.path.abspath(str(generated_files.get("analysis_inp") or input_inp_abs))
        resolved_job_name = str(solver_payload.get("job_name") or Path(analysis_inp_path).stem)
        resolved_odb_path = (solver_result.get("artifacts") or {}).get("odb")
        if not resolved_odb_path:
            raise NotFoundError(
                "odb artifact not found after abaqus sensitivity run",
                {"project_id": int(project_id), "batch_no": normalized_batch_no},
            )

        project_result_parse = None
        if parse_via_project_results:
            project_result_parse = _submit_project_result_group_and_wait(
                project_id=project_id,
                odb_path=str(resolved_odb_path),
                batch_no=normalized_batch_no,
                job_name=resolved_job_name,
                step=step,
                frame=frame,
                field_prefix=field_prefix,
                base_url=base_url,
                timeout=timeout,
                result_group=project_result_group,
                display_name=project_result_display_name,
                wait_timeout_sec=project_result_wait_timeout_sec,
                poll_interval_sec=project_result_poll_interval_sec,
            )
            resolved_workspace = str(project_result_parse["workspace"])
        else:
            resolved_workspace = _default_solver_workspace(output_dir_abs, resolved_job_name)
            build_workspace_from_odb(
                odb_path=str(resolved_odb_path),
                workspace=resolved_workspace,
                abaqus=abaqus,
                python3=python3,
                keep_raw=keep_raw,
            )
        deleted_process_files = (
            delete_abaqus_process_files(Path(output_dir_abs), resolved_job_name)
            if cleanup_process_files
            else []
        )
        temp_selected_parameters = _rebuild_selected_parameters_from_inp(
            project_id=project_id,
            inp_path=analysis_inp_path,
        )

        matrix_payload = _load_dsa_normalized_sensitivity_matrix(
            project_id=project_id,
            inp_path=analysis_inp_path,
            workspace=resolved_workspace,
            step=step,
            instances=instances,
            field_prefix=field_prefix,
            response_component=response_component,
            position=position,
            aggregation=aggregation,
            frame=workspace_frame,
            abaqus=abaqus,
            python3=python3,
            keep_raw=keep_raw,
            timeout=timeout,
            result_group=project_result_parse["result_group"] if project_result_parse else None,
            optimization_parameter_rows=list(temp_selected_parameters.get("optimization_parameter_rows") or []),
        )

        result = _finalize_sensitivity_store_result(
            project_id=project_id,
            batch_no=normalized_batch_no,
            source_input_inp=input_inp_abs,
            analysis_inp_path=analysis_inp_path,
            resolved_odb_path=str(resolved_odb_path),
            matrix_payload=matrix_payload,
            solver_payload=solver_payload,
            generated_files=generated_files,
            base_url=base_url,
            timeout=timeout,
            deleted_process_files=deleted_process_files,
            write_cloud_result=write_cloud_result,
            cloud_result_group=cloud_result_group,
            cloud_step_name=cloud_step_name,
            cloud_field_name=cloud_field_name,
            extra_payload={
                "temp_selected_parameters": temp_selected_parameters,
                "project_result_parse": project_result_parse,
                "workspace_frame": int(workspace_frame),
            },
        )

        if merge_fields:
            source_rg = (project_result_parse or {}).get("result_group") if parse_via_project_results else None
            try:
                merge_result = merge_dsa_sensitivity_fields(
                    project_id=project_id,
                    workspace=str(result["workspace"]),
                    step=step,
                    frame=int(workspace_frame),
                    field_prefix=field_prefix,
                    instances=list(instances) if instances else [],
                    result_group=resolved_merge_result_group,
                    source_result_group=source_rg,
                )
                result["merge_result"] = merge_result
                result["merge_result_group"] = resolved_merge_result_group
            except Exception as exc:
                result["merge_result"] = {"error": str(exc)}
                result["merge_result_group"] = None

        return result

    return _run_with_project_sensitivity_status(project_id, _run)


def generate_sensitivity_inp_and_store(
        *,
        project_id: int,
        batch_no: Optional[str] = None,
        input_inp: str,
        output_dir: str,
        step: str,
        instances: List[str],
        field_prefix: str,
        response_component: str,
        position: str,
        aggregation: str = "max_abs",
        frame: int = 0,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        base_url: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
        job_name: Optional[str] = None,
        cpus: Optional[int] = None,
        interactive: bool = True,
        timeout_sec: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
        cleanup_process_files: bool = True,
        parse_via_project_results: bool = True,
        project_result_group: Optional[str] = None,
        project_result_display_name: Optional[str] = None,
        project_result_wait_timeout_sec: int = 3600,
        project_result_poll_interval_sec: float = 2.0,
) -> dict:
    ensure_tables_exist()

    input_inp_abs = os.path.abspath(input_inp)
    if not os.path.exists(input_inp_abs):
        raise NotFoundError("input_inp not found", {"input_inp": input_inp_abs})

    output_dir_abs = os.path.abspath(output_dir)
    os.makedirs(output_dir_abs, exist_ok=True)

    def _run():
        parameter_rows = _load_project_optimization_parameters(project_id)
        if not parameter_rows:
            raise ValidationError(
                "no optimization parameters found for project-driven sensitivity generation",
                {"project_id": int(project_id)},
            )
        design_response_rows = _load_project_design_responses(project_id)
        if not design_response_rows:
            raise ValidationError(
                "no design responses found for project-driven sensitivity generation",
                {"project_id": int(project_id)},
            )

        generation_payload = _generate_sensitivity_inp_from_project_db(
            project_id=project_id,
            input_inp=input_inp_abs,
            output_dir=output_dir_abs,
            parameter_rows=parameter_rows,
            design_response_rows=design_response_rows,
        )
        generated_analysis_inp = generation_payload.get("analysis_inp")
        if not generated_analysis_inp:
            raise ValidationError(
                "project-driven sensitivity inp generator did not return analysis_inp",
                {"project_id": int(project_id), "generation_payload": generation_payload},
            )

        result = run_sensitivity_inp_and_store(
            project_id=project_id,
            batch_no=batch_no,
            input_inp=str(generated_analysis_inp),
            output_dir=output_dir_abs,
            step=step,
            instances=instances,
            field_prefix=field_prefix,
            response_component=response_component,
            position=position,
            aggregation=aggregation,
            frame=frame,
            abaqus=abaqus,
            python3=python3,
            base_url=base_url,
            keep_raw=keep_raw,
            timeout=timeout,
            job_name=job_name,
            cpus=cpus,
            interactive=interactive,
            timeout_sec=timeout_sec,
            extra_args=extra_args,
            cleanup_process_files=cleanup_process_files,
            parse_via_project_results=parse_via_project_results,
            project_result_group=project_result_group,
            project_result_display_name=project_result_display_name,
            project_result_wait_timeout_sec=project_result_wait_timeout_sec,
            project_result_poll_interval_sec=project_result_poll_interval_sec,
        )
        result["input_inp"] = input_inp_abs
        result["generation_source"] = "project_db"
        result["loaded_parameter_count"] = len(parameter_rows)
        result["loaded_design_response_count"] = len(design_response_rows)
        result["generation_summary"] = {
            "optimization_parameter_count": len(parameter_rows),
            "design_response_count": len(design_response_rows),
        }
        generated_files = dict(result.get("generated_files") or {})
        generation_files = dict(generation_payload.get("generated_files") or {})
        if generation_files:
            generated_files.update(generation_files)
        generated_files["analysis_inp"] = os.path.abspath(str(generated_analysis_inp))
        result["generated_files"] = generated_files
        return result

    return _run_with_project_sensitivity_status(project_id, _run)


def get_stored_sensitivity_table_points(*, project_id: int, batch_no: Optional[str] = None) -> dict:
    try:
        payload = _load_stored_sensitivity_run(project_id=project_id, batch_no=batch_no)
    except NotFoundError:
        return {
            "analysis_run_id": None,
            "project_id": int(project_id),
            "batch_no": _normalize_batch_no(batch_no),
            "case_name": None,
            "created_at": None,
            "rows": [],
            "column": [],
            "data": [],
            "summary": {
                "response_count": 0,
                "parameter_count": 0,
                "point_count": 0,
            },
        }
    response_names = [_normalize_response_display_name(item) for item in (payload.get("row_names") or [])]
    parameter_names = list(payload.get("col_names") or [])
    matrix = [list(row) for row in (payload.get("matrix") or [])]

    transpose_matrix: List[List[Optional[float]]] = []
    for col_index in range(len(parameter_names)):
        transpose_row = []
        for row_index in range(len(response_names)):
            current_row = matrix[row_index] if row_index < len(matrix) else []
            transpose_row.append(current_row[col_index] if col_index < len(current_row) else None)
        transpose_matrix.append(transpose_row)

    points = []
    for row_index, _parameter_name in enumerate(parameter_names):
        current_row = transpose_matrix[row_index] if row_index < len(transpose_matrix) else []
        columns_value = {}
        for col_index, col_name in enumerate(response_names):
            value = current_row[col_index] if col_index < len(current_row) else None
            columns_value[col_name] = value

        points.append(columns_value)

    return {
        "analysis_run_id": payload.get("analysis_run_id"),
        "project_id": payload.get("project_id"),
        "batch_no": payload.get("batch_no"),
        "case_name": payload.get("case_name"),
        "created_at": payload.get("created_at"),
        "rows": parameter_names,
        "column": response_names,
        "data": points,
        "summary": {
            "response_count": len(response_names),
            "parameter_count": len(parameter_names),
            "point_count": len(points),
        },
    }


def _build_empty_stored_sensitivity_matrix_payload(*, project_id: int, batch_no: Optional[str] = None) -> dict:
    return {
        "analysis_run_id": None,
        "project_id": int(project_id),
        "batch_no": _normalize_batch_no(batch_no),
        "case_name": None,
        "created_at": None,
        "data": {
            "column": [],
            "rows": [],
            "data": [],
        },
        "summary": {
            "response_count": 0,
            "parameter_count": 0,
        },
    }


def get_stored_sensitivity_matrix_payload(*, project_id: int, batch_no: Optional[str] = None) -> dict:
    try:
        payload = _load_stored_sensitivity_run(project_id=project_id, batch_no=batch_no)
    except NotFoundError:
        return _build_empty_stored_sensitivity_matrix_payload(project_id=project_id, batch_no=batch_no)
    row_names = [_normalize_response_display_name(item) for item in (payload.get("row_names") or [])]
    col_names = list(payload.get("col_names") or [])
    matrix = list(payload.get("matrix") or [])

    column_count = len(col_names)
    row_count = len(row_names)
    data = []

    for ii in range(row_count):
        for jj in range(column_count):
            current_row = matrix[ii] if ii < len(matrix) else []
            data.append([ii, jj, current_row[jj] if jj < len(current_row) else None])

    sensitivity = {"column": col_names,
                   "rows": row_names,
                   "data": data}

    return {
        "analysis_run_id": payload.get("analysis_run_id"),
        "project_id": payload.get("project_id"),
        "batch_no": payload.get("batch_no"),
        "case_name": payload.get("case_name"),
        "created_at": payload.get("created_at"),
        "data": sensitivity,
        "summary": {
            "response_count": len(row_names),
            "parameter_count": len(col_names),
        },
    }


def _build_curve_series(*, labels: List[str], xaxis: List[str], matrix: List[List[Optional[float]]]) -> List[dict]:
    curves = []
    for row_index, label in enumerate(labels):
        current_row = matrix[row_index] if row_index < len(matrix) else []
        points = []
        for col_index, x_value in enumerate(xaxis):
            y_value = current_row[col_index] if col_index < len(current_row) else None
            points.append([str(x_value), y_value])
        curves.append(
            {
                "data": points,
                "label": str(label),
                "xaxis": [str(item) for item in xaxis],
            }
        )
    return curves


def _build_empty_stored_sensitivity_curve_payload(
        *,
        project_id: int,
        batch_no: Optional[str] = None,
        curve_type: str,
) -> dict:
    return {
        "analysis_run_id": None,
        "project_id": int(project_id),
        "batch_no": _normalize_batch_no(batch_no),
        "case_name": None,
        "created_at": None,
        "curve_type": str(curve_type),
        "data": [],
        "summary": {
            "response_count": 0,
            "parameter_count": 0,
            "curve_count": 0,
        },
    }


def get_stored_sensitivity_parameter_curves(*, project_id: int, batch_no: Optional[str] = None) -> dict:
    try:
        payload = _load_stored_sensitivity_run(project_id=project_id, batch_no=batch_no)
    except NotFoundError:
        return _build_empty_stored_sensitivity_curve_payload(
            project_id=project_id,
            batch_no=batch_no,
            curve_type="parameter",
        )
    response_names = [_normalize_response_display_name(item) for item in (payload.get("row_names") or [])]
    parameter_names = [str(item) for item in (payload.get("col_names") or [])]
    matrix = [list(row) for row in (payload.get("matrix") or [])]

    curves = _build_curve_series(
        labels=response_names,
        xaxis=parameter_names,
        matrix=matrix,
    )

    return {
        "analysis_run_id": payload.get("analysis_run_id"),
        "project_id": payload.get("project_id"),
        "batch_no": payload.get("batch_no"),
        "case_name": payload.get("case_name"),
        "created_at": payload.get("created_at"),
        "curve_type": "parameter",
        "data": curves,
        "summary": {
            "response_count": len(response_names),
            "parameter_count": len(parameter_names),
            "curve_count": len(curves),
        },
    }


def get_stored_sensitivity_response_curves(*, project_id: int, batch_no: Optional[str] = None) -> dict:
    try:
        payload = _load_stored_sensitivity_run(project_id=project_id, batch_no=batch_no)
    except NotFoundError:
        return _build_empty_stored_sensitivity_curve_payload(
            project_id=project_id,
            batch_no=batch_no,
            curve_type="response",
        )
    response_names = [_normalize_response_display_name(item) for item in (payload.get("row_names") or [])]
    parameter_names = [str(item) for item in (payload.get("col_names") or [])]
    matrix = [list(row) for row in (payload.get("matrix") or [])]

    transpose_matrix: List[List[Optional[float]]] = []
    for col_index in range(len(parameter_names)):
        transpose_row = []
        for row_index in range(len(response_names)):
            current_row = matrix[row_index] if row_index < len(matrix) else []
            transpose_row.append(current_row[col_index] if col_index < len(current_row) else None)
        transpose_matrix.append(transpose_row)

    curves = _build_curve_series(
        labels=parameter_names,
        xaxis=response_names,
        matrix=transpose_matrix,
    )

    return {
        "analysis_run_id": payload.get("analysis_run_id"),
        "project_id": payload.get("project_id"),
        "batch_no": payload.get("batch_no"),
        "case_name": payload.get("case_name"),
        "created_at": payload.get("created_at"),
        "curve_type": "response",
        "data": curves,
        "summary": {
            "response_count": len(response_names),
            "parameter_count": len(parameter_names),
            "curve_count": len(curves),
        },
    }


def _resolve_inp_path_from_project(project_id: int) -> str:
    return resolve_project_source_inp_path(int(project_id))


def _pick_export_position(field_meta: dict, requested_position: Optional[str]) -> str:
    positions = [str(x) for x in (field_meta.get("positions") or [])]
    if requested_position:
        if requested_position not in positions:
            raise NotFoundError(
                f"position '{requested_position}' not available for field '{field_meta.get('field')}'",
                {
                    "field": field_meta.get("field"),
                    "requested_position": requested_position,
                    "available_positions": positions,
                },
            )
        return requested_position

    for candidate in _VTU_POSITION_PRIORITY:
        if candidate in positions:
            return candidate

    raise NotFoundError(
        f"no exportable position found for field '{field_meta.get('field')}'",
        {"field": field_meta.get("field"), "available_positions": positions},
    )


def _build_field_selector(*, field_prefix: Optional[str] = None, field_name: Optional[str] = None) -> dict:
    has_prefix = bool(field_prefix)
    has_name = bool(field_name)
    if has_prefix == has_name:
        raise ValidationError(
            "exactly one of field_prefix or field_name must be provided",
            {"field_prefix": field_prefix, "field_name": field_name},
        )

    if has_prefix:
        return {
            "kind": "prefix",
            "field_prefix": str(field_prefix),
            "display": f"prefix '{field_prefix}'",
            "match": lambda candidate: str(candidate).startswith(str(field_prefix)),
        }

    return {
        "kind": "exact",
        "field_name": str(field_name),
        "display": f"field '{field_name}'",
        "match": lambda candidate: str(candidate) == str(field_name),
    }


def _raise_no_sensitivity_fields(selector: dict, *, step: str, instances: List[str]) -> None:
    details = {"step": step, "instances": instances}
    if selector["kind"] == "prefix":
        details["field_prefix"] = selector["field_prefix"]
    else:
        details["field_name"] = selector["field_name"]
    raise NotFoundError(
        f"no sensitivity fields found for {selector['display']}",
        details,
    )


def _discover_sensitivity_fields(
        client: ODBClient,
        odb_id: str,
        *,
        step: Optional[str],
        instances: Optional[List[str]],
        selector: dict,
        position: Optional[str],
) -> dict:
    overview = client.get_overview(odb_id)
    chosen_step = step or overview.get("default_step")
    if not chosen_step:
        steps = list(overview.get("steps") or [])
        chosen_step = _resolve_single("step", step, steps)

    overview_instances = [str(x) for x in (overview.get("instances") or [])]
    if instances:
        chosen_instances = [str(x) for x in instances]
        missing = [name for name in chosen_instances if name not in overview_instances]
        if missing:
            raise NotFoundError(
                "some instances are not available in the selected odb",
                {"missing_instances": missing, "available_instances": overview_instances},
            )
    else:
        chosen_instances = overview_instances

    per_instance: Dict[str, List[dict]] = {}
    matched_field_names = set()
    for instance_name in chosen_instances:
        fields = client.get_fields(odb_id, instance_name, chosen_step)
        selected = []
        for field_meta in fields:
            field_name = str(field_meta.get("field") or "")
            if not selector["match"](field_name):
                continue
            selected.append(
                {
                    "field": field_name,
                    "position": _pick_export_position(field_meta, position),
                    "components": list(field_meta.get("components") or []),
                }
            )
            matched_field_names.add(field_name)
        per_instance[instance_name] = selected

    if not matched_field_names:
        _raise_no_sensitivity_fields(selector, step=chosen_step, instances=chosen_instances)

    return {
        "step": chosen_step,
        "instances": chosen_instances,
        "per_instance": per_instance,
        "field_names": sorted(matched_field_names),
    }


def _discover_sensitivity_fields_from_workspace(
        workspace: str,
        *,
        step: Optional[str],
        instances: Optional[List[str]],
        selector: dict,
        position: Optional[str],
) -> dict:
    workspace_abs = _workspace_path(workspace)
    conn = _manifest_conn(workspace_abs)
    try:
        step_rows = [dict(row) for row in conn.execute(
            "SELECT step_name, step_number FROM steps ORDER BY step_number, step_name"
        ).fetchall()]
        all_steps = [str(row["step_name"]) for row in step_rows]
        chosen_step = step or _default_step_from_rows(step_rows)
        if not chosen_step:
            chosen_step = _resolve_single("step", step, all_steps)

        if instances:
            chosen_instances = [str(x) for x in instances]
        else:
            chosen_instances = [
                str(row["instance_name"])
                for row in conn.execute(
                    "SELECT DISTINCT instance_name FROM result_blocks WHERE step_name = ? ORDER BY instance_name",
                    (chosen_step,),
                ).fetchall()
            ]

        matched_field_names = set()
        per_instance: Dict[str, List[dict]] = {}
        for instance_name in chosen_instances:
            rows = conn.execute(
                """
                SELECT rf.field_name, rb.position
                FROM result_files rf
                JOIN result_blocks rb
                  ON rb.step_name = rf.step_name
                 AND rb.field_name = rf.field_name
                WHERE rf.step_name = ?
                  AND rb.instance_name = ?
                ORDER BY rf.field_name, rb.position
                """,
                (chosen_step, instance_name),
            ).fetchall()

            fields_map: Dict[str, List[str]] = {}
            for row in rows:
                field_name = str(row["field_name"])
                if not selector["match"](field_name):
                    continue
                fields_map.setdefault(field_name, []).append(str(row["position"]))

            selected = []
            for field_name, positions in fields_map.items():
                field_meta = {"field": field_name, "positions": sorted(set(positions))}
                selected.append(
                    {
                        "field": field_name,
                        "position": _pick_export_position(field_meta, position),
                        "components": [],
                    }
                )
                matched_field_names.add(field_name)
            per_instance[instance_name] = selected

        if not matched_field_names:
            _raise_no_sensitivity_fields(selector, step=chosen_step, instances=chosen_instances)

        return {
            "workspace": workspace_abs,
            "step": chosen_step,
            "instances": chosen_instances,
            "per_instance": per_instance,
            "field_names": sorted(matched_field_names),
        }
    finally:
        conn.close()


def _discover_sensitivity_fields_from_registry(
        odb_id: str,
        *,
        step: Optional[str],
        instances: Optional[List[str]],
        selector: dict,
        position: Optional[str],
) -> dict:
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found in loaded registry", {"odb_id": odb_id})

    manifest = ManifestRepo(idx.workspace)
    overview = manifest.get_overview()
    step_rows = overview.get("steps") or []
    step_names = [str(row.get("step_name")) for row in step_rows if row.get("step_name") is not None]
    chosen_step = step or _default_step_from_rows(step_rows)
    if not chosen_step:
        raise ValidationError("step is required because multiple steps are available", {"available": step_names})

    instance_rows = overview.get("instances") or []
    overview_instances = [str(row.get("instance_name")) for row in instance_rows if row.get("instance_name") is not None]
    if instances:
        chosen_instances = [str(x) for x in instances]
        missing = [name for name in chosen_instances if name not in overview_instances]
        if missing:
            raise NotFoundError(
                "some instances are not available in the selected odb",
                {"missing_instances": missing, "available_instances": overview_instances},
            )
    else:
        chosen_instances = overview_instances

    per_instance: Dict[str, List[dict]] = {}
    matched_field_names = set()
    for instance_name in chosen_instances:
        fields = get_instance_fields(registry, odb_id, instance_name, chosen_step)
        selected = []
        for field_meta in fields:
            field_name = str(field_meta.get("name") or field_meta.get("field") or "")
            if not selector["match"](field_name):
                continue
            selected.append(
                {
                    "field": field_name,
                    "position": _pick_export_position(field_meta, position),
                    "components": list(field_meta.get("components") or []),
                }
            )
            matched_field_names.add(field_name)
        per_instance[instance_name] = selected

    if not matched_field_names:
        _raise_no_sensitivity_fields(selector, step=chosen_step, instances=chosen_instances)

    return {
        "workspace": idx.workspace,
        "step": chosen_step,
        "instances": chosen_instances,
        "per_instance": per_instance,
        "field_names": sorted(matched_field_names),
    }


def _reduce_frame_values(raw: np.ndarray, aggregation: str) -> np.ndarray:
    arr = np.asarray(raw, dtype=np.float64)
    if arr.ndim == 0:
        return arr.reshape(1, 1)
    if arr.ndim == 1:
        return arr[:, np.newaxis]
    if arr.ndim == 2:
        return arr
    reduce_axes = tuple(range(1, arr.ndim - 1))
    reduced = _aggregate(arr, axis=reduce_axes, mode=aggregation)
    if reduced.ndim == 1:
        return reduced[:, np.newaxis]
    return reduced


def _normalize_vtu_result_value(value):
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return value.item()
        if value.ndim == 1 and value.size == 1:
            return value.reshape(-1)[0].item()
        return value.tolist()
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return _normalize_vtu_result_value(value[0])
        return list(value)
    return value


def _normalize_vtu_label_map(label_map: Dict[str, object]) -> Dict[str, object]:
    return {key: _normalize_vtu_result_value(value) for key, value in label_map.items()}


def _vtu_result_values_equal(left, right) -> bool:
    left_arr = np.asarray(left)
    right_arr = np.asarray(right)
    if left_arr.shape != right_arr.shape:
        return False
    return bool(np.array_equal(left_arr, right_arr, equal_nan=True))


def _merge_vtu_result_map(target: Dict[object, object], incoming: Dict[object, object], *, details: dict) -> None:
    for label, value in incoming.items():
        if label in target and not _vtu_result_values_equal(target[label], value):
            raise ValidationError(
                "conflicting sensitivity values found while composing VTU result",
                {**details, "label": label, "existing_value": target[label], "incoming_value": value},
            )
        target[label] = value


def _load_project_optimization_parameters(project_id: int) -> List[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, parameter_group_name, parameter_name, quantity_code, selection_mode,
                   set_name, set_type, set_scope, instance_name, part_name,
                   element_label, lower, upper, prob_id, scatter, current_value AS scalar_value, extra_json
            FROM t_mt_py_fem_selected_parameter
            WHERE pid = %s
            ORDER BY created_at ASC, id ASC
            """,
            (project_id,),
        )
        return [dict(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def _extract_dsa_field_index(field_prefix: str, source_field_name: str) -> int:
    parameter_token = _extract_dsa_field_token(field_prefix, source_field_name)
    match = _DSA_PARAMETER_TOKEN_RE.fullmatch(parameter_token)
    if not match:
        raise ValidationError(
            "DSA field name does not contain a parameter-index suffix",
            {"field_prefix": field_prefix, "field": str(source_field_name), "parameter_token": parameter_token},
        )
    return int(match.group(2))


def _extract_dsa_field_token(field_prefix: str, source_field_name: str) -> str:
    source_name = str(source_field_name)
    prefix = str(field_prefix)
    if not source_name.startswith(prefix):
        raise ValidationError(
            "DSA field does not match the requested prefix",
            {"field_prefix": prefix, "field": source_name},
        )

    suffix = source_name[len(prefix):]
    if suffix:
        direct_match = _DSA_PARAMETER_TOKEN_RE.fullmatch(str(suffix))
        if direct_match:
            return str(suffix)

        prefix_tail = prefix.rsplit("_", 1)[-1] if "_" in prefix else prefix
        if prefix_tail and re.fullmatch(r"[A-Z][A-Z0-9]*", prefix_tail, re.IGNORECASE) and str(suffix).isdigit():
            return f"{prefix_tail}{suffix}"

    return str(suffix or source_name)


def _build_dsa_parameter_row_map(parameter_rows: List[dict], field_prefix: str, source_field_names: List[str]) -> Dict[str, dict]:
    if not parameter_rows:
        raise ValidationError("no optimization parameters found for DSA VTU export", {"field_prefix": field_prefix})

    field_tokens = {
        str(name): _extract_dsa_field_token(field_prefix, str(name))
        for name in {str(item) for item in source_field_names}
    }
    field_indices: Dict[str, Optional[int]] = {}
    for field_name, parameter_token in field_tokens.items():
        match = _DSA_PARAMETER_TOKEN_RE.fullmatch(parameter_token)
        field_indices[field_name] = int(match.group(2)) if match else None

    ordered_fields = sorted(
        field_tokens.keys(),
        key=lambda name: (
            field_indices[name] is None,
            field_indices[name] if field_indices[name] is not None else 10 ** 9,
            field_tokens[name],
            name,
        ),
    )
    if len(ordered_fields) > len(parameter_rows):
        raise ValidationError(
            "number of DSA fields exceeds available optimization parameters",
            {
                "field_prefix": field_prefix,
                "field_count": len(ordered_fields),
                "available_parameter_count": len(parameter_rows),
            },
        )

    field_map: Dict[str, dict] = {}
    all_indices = [field_indices[name] for name in ordered_fields]
    direct_indices = [idx for idx in all_indices if idx is not None]
    direct_indices_valid = (
            len(direct_indices) == len(ordered_fields)
            and all(1 <= idx <= len(parameter_rows) for idx in direct_indices)
    )
    direct_indices_unique = len(set(direct_indices)) == len(direct_indices)

    if direct_indices_valid and direct_indices_unique:
        for field_name, parameter_index in zip(ordered_fields, direct_indices):
            field_map[field_name] = parameter_rows[int(parameter_index) - 1]
        return field_map

    for offset, field_name in enumerate(ordered_fields):
        field_map[field_name] = parameter_rows[offset]
    return field_map


def _build_dsa_design_parameter_name_map(model) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    design_parameters = sorted(
        list(getattr(model, "design_parameters", []) or []),
        key=lambda item: int(getattr(item, "order", 0) or 0),
    )
    for offset, item in enumerate(design_parameters, start=1):
        mapping[offset] = str(item.name)
        order_value = int(getattr(item, "order", offset) or offset)
        mapping[order_value] = str(item.name)
    return mapping


def _flatten_design_response_requests(model, *, step_name: Optional[str]) -> List[dict]:
    rows: List[dict] = []
    for response in list(getattr(model, "design_responses", []) or []):
        response_step = getattr(response, "step_name", None)
        if step_name and response_step not in (None, step_name):
            continue
        for request in list(getattr(response, "requests", []) or []):
            rows.append(
                {
                    "step_name": response_step,
                    "frequency": int(getattr(response, "frequency", 1) or 1),
                    "region_type": str(getattr(request, "region_type", "") or "").upper(),
                    "set_name": str(getattr(request, "set_name", "") or ""),
                    "variables": [str(item).upper() for item in (getattr(request, "variables", []) or []) if str(item).strip()],
                }
            )
    return rows


def _parse_design_response_variable(variable: str) -> dict:
    token = str(variable or "").strip().upper()
    if not token:
        raise ValidationError("design response variable is empty")

    alias = _VECTOR_DIRECTION_ALIASES.get(token)
    if alias:
        field_name, component_name = alias
        component_index = int(component_name[-1]) - 1
        return {
            "variable": token,
            "field_name": field_name,
            "component": component_name,
            "component_index": component_index,
        }

    direct_component = re.fullmatch(r"([A-Z]+)(\d+)", token)
    if direct_component:
        field_name = direct_component.group(1)
        component_name = token
        component_index = int(direct_component.group(2)) - 1
        return {
            "variable": token,
            "field_name": field_name,
            "component": component_name,
            "component_index": component_index,
        }

    return {
        "variable": token,
        "field_name": token,
        "component": None,
        "component_index": None,
    }


def _parse_explicit_response_component(response_component: Optional[str]) -> Optional[dict]:
    if response_component is None:
        return None

    parsed = _parse_design_response_variable(str(response_component))
    if parsed.get("component") is None:
        raise ValidationError(
            "response_component must describe a concrete direction",
            {
                "response_component": response_component,
                "allowed_examples": ["U1", "U2", "U3", "UX", "UY", "UZ", "UR1", "UR2", "UR3", "RX", "RY", "RZ"],
            },
        )
    return parsed


def _resolve_dsa_response_spec(model, *, step_name: Optional[str]) -> Optional[dict]:
    requests = _flatten_design_response_requests(model, step_name=step_name)
    specs = []
    for request in requests:
        variables = list(request["variables"] or [])
        if len(variables) != 1:
            continue
        parsed = _parse_design_response_variable(variables[0])
        specs.append(
            {
                **request,
                **parsed,
                "preferred_position": "NODAL" if request["region_type"] == "NODE" else None,
            }
        )
    return specs


def _select_dsa_response_specs(
        specs: List[dict],
        *,
        field_prefix: str,
        response_component: Optional[str],
) -> Tuple[List[dict], Optional[dict]]:
    response_token = _field_prefix_response_token(field_prefix)
    candidate_specs = [
                          spec for spec in specs if _design_response_matches_token(spec, response_token)
                      ] or list(specs)

    explicit_response = _parse_explicit_response_component(response_component)
    if explicit_response is None:
        return candidate_specs, None

    filtered_specs = []
    explicit_field_name = str(explicit_response["field_name"]).upper()
    explicit_component = str(explicit_response["component"]).upper()
    for spec in candidate_specs:
        spec_field_name = str(spec.get("field_name") or "").upper()
        spec_component = spec.get("component")
        if spec_field_name != explicit_field_name:
            continue
        if spec_component is not None and str(spec_component).upper() != explicit_component:
            continue
        filtered_specs.append(spec)

    if filtered_specs:
        return filtered_specs, explicit_response

    raise ValidationError(
        "response_component does not match any design response defined in the inp file",
        {
            "field_prefix": field_prefix,
            "response_component": response_component,
            "available_design_responses": [
                {
                    "field_name": str(spec.get("field_name") or ""),
                    "component": spec.get("component"),
                    "set_name": spec.get("set_name"),
                    "region_type": spec.get("region_type"),
                }
                for spec in candidate_specs[:20]
            ],
        },
    )


def _resolve_dsa_parameter_row(parameter_map: Dict[str, dict], source_field_name: str) -> dict:
    if source_field_name not in parameter_map:
        raise ValidationError("DSA field has no mapped optimization parameter", {"field": source_field_name})
    return parameter_map[source_field_name]


def _extract_single_dsa_result_value(label_map: Dict[str, object], *, source_field: str) -> object:
    if not label_map:
        raise ValidationError("DSA field has no values", {"field": source_field})
    if len(label_map) != 1:
        raise ValidationError(
            "DSA VTU export currently requires each source field to contain exactly one response location",
            {"field": source_field, "value_count": len(label_map), "labels": list(label_map.keys())[:10]},
        )
    return next(iter(label_map.values()))


def _scoped_labels(scope_name: Optional[str], labels: Iterable[int]) -> List[object]:
    if scope_name:
        return [f"{scope_name}::{int(label)}" for label in labels]
    return [int(label) for label in labels]


def _parameter_target_labels(model, parameter_row: dict) -> tuple[str, List[object]]:
    set_name = str(parameter_row.get("set_name") or "")
    set_type = str(parameter_row.get("set_type") or "").upper()
    set_scope = str(parameter_row.get("set_scope") or "").upper()
    instance_name = parameter_row.get("instance_name")
    part_name = parameter_row.get("part_name")
    element_label = parameter_row.get("element_label")

    if element_label is not None:
        resolved_label = int(element_label)
        if set_scope == "PART":
            if model.assembly and model.assembly.instances and part_name:
                matched_instances = [
                    inst_name
                    for inst_name, inst in model.assembly.instances.items()
                    if str(inst.part_name) == str(part_name)
                ]
                if matched_instances:
                    scoped = []
                    for inst_name in matched_instances:
                        scoped.extend(_scoped_labels(inst_name, [resolved_label]))
                    return "cell", scoped
            return "cell", _scoped_labels(str(part_name) if part_name else None, [resolved_label])

        if set_scope == "ASSEMBLY":
            return "cell", _scoped_labels(str(instance_name) if instance_name else None, [resolved_label])

    if set_type == "NSET":
        target_kind = "point"
    elif set_type == "ELSET":
        target_kind = "cell"
    else:
        raise ValidationError("unsupported optimization parameter set_type", {"set_type": set_type, "set_name": set_name})

    if set_scope == "PART":
        if not part_name or part_name not in model.parts:
            raise ValidationError(
                "optimization parameter part set not found in inp model",
                {"set_name": set_name, "part_name": part_name, "set_scope": set_scope},
            )
        part = model.parts[str(part_name)]
        if set_type == "NSET":
            source_set = part.nsets.get(set_name)
            labels = list(source_set.node_labels) if source_set else []
        else:
            source_set = part.elsets.get(set_name)
            labels = list(source_set.elem_labels) if source_set else []
        if not labels:
            raise ValidationError(
                "optimization parameter set has no members in inp model",
                {"set_name": set_name, "part_name": part_name, "set_scope": set_scope, "set_type": set_type},
            )

        if model.assembly and model.assembly.instances:
            matched_instances = [
                inst_name
                for inst_name, inst in model.assembly.instances.items()
                if str(inst.part_name) == str(part_name)
            ]
            if matched_instances:
                scoped = []
                for inst_name in matched_instances:
                    scoped.extend(_scoped_labels(inst_name, labels))
                return target_kind, scoped

        return target_kind, _scoped_labels(str(part_name) if part_name else None, labels)

    if set_scope == "ASSEMBLY":
        if not model.assembly:
            raise ValidationError(
                "optimization parameter references an assembly set but inp model has no assembly",
                {"set_name": set_name, "set_scope": set_scope},
            )
        if set_type == "NSET":
            source_set = model.assembly.nsets.get(set_name)
            labels = list(source_set.node_labels) if source_set else []
        else:
            source_set = model.assembly.elsets.get(set_name)
            labels = list(source_set.elem_labels) if source_set else []
        if not labels:
            raise ValidationError(
                "optimization parameter assembly set has no members in inp model",
                {"set_name": set_name, "set_scope": set_scope, "set_type": set_type},
            )
        return target_kind, _scoped_labels(str(instance_name) if instance_name else None, labels)

    raise ValidationError("unsupported optimization parameter set_scope", {"set_scope": set_scope, "set_name": set_name})


def _compose_dsa_label_map(model, parameter_row: dict, value: object) -> tuple[str, Dict[object, object]]:
    target_kind, targets = _parameter_target_labels(model, parameter_row)
    return target_kind, {target: value for target in targets}


def _resolve_dsa_parameter_scalar_value(model, *, parameter_name: Optional[str], target_rows: List[dict]) -> float:
    if parameter_name:
        parameter_def = getattr(model, "parameters", {}).get(str(parameter_name))
        scalar_value = getattr(parameter_def, "scalar_value", None) if parameter_def is not None else None
        if scalar_value is not None:
            return float(scalar_value)

    for row in target_rows:
        scalar_value = row.get("scalar_value")
        if scalar_value is not None:
            return float(scalar_value)
        extra_json = row.get("extra_json") or {}
        if isinstance(extra_json, dict) and extra_json.get("scalar_value") is not None:
            return float(extra_json["scalar_value"])

    raise ValidationError(
        "unable to resolve the current parameter value for DSA normalization",
        {"parameter_name": parameter_name, "target_rows": target_rows[:3]},
    )


def _normalize_dsa_sensitivity_value(
        sensitivity_value,
        *,
        parameter_value: float,
        response_value,
        source_field: str,
        response_field: str,
):
    sens_arr = np.asarray(sensitivity_value, dtype=np.float64)
    resp_arr = np.asarray(response_value, dtype=np.float64)

    if np.any(~np.isfinite(sens_arr)):
        raise ValidationError(
            "sensitivity value contains non-finite entries for DSA normalization",
            {"field": source_field, "response_field": response_field, "sensitivity_value": sensitivity_value},
        )

    if resp_arr.size == 0:
        raise ValidationError(
            "response value is empty for DSA normalization",
            {"field": source_field, "response_field": response_field},
        )

    if np.any(~np.isfinite(resp_arr)):
        raise ValidationError(
            "response value contains non-finite entries for DSA normalization",
            {"field": source_field, "response_field": response_field, "response_value": response_value},
        )

    if np.any(np.isclose(resp_arr, 0.0, atol=1e-18)):
        raise ValidationError(
            "response value is zero and cannot be used for DSA normalization",
            {"field": source_field, "response_field": response_field, "response_value": response_value},
        )

    normalized = (sens_arr * float(parameter_value)) / resp_arr
    if np.any(~np.isfinite(normalized)):
        raise ValidationError(
            "normalized sensitivity contains non-finite entries",
            {
                "field": source_field,
                "response_field": response_field,
                "parameter_value": float(parameter_value),
            },
        )
    if normalized.ndim == 0:
        return float(normalized.item())
    values = normalized.tolist()
    return values[0] if isinstance(values, list) and len(values) == 1 else values


def _field_prefix_response_token(field_prefix: str) -> Optional[str]:
    prefix = str(field_prefix or "").strip()
    if not prefix:
        return None
    match = _RESPONSE_TOKEN_RE.fullmatch(prefix)
    if not match:
        return None
    return str(match.group(1)).upper()


def _design_response_token_candidates(spec: dict) -> List[str]:
    candidates = set()
    variable = str(spec.get("variable") or "").upper()
    field_name = str(spec.get("field_name") or "").upper()
    component = spec.get("component")
    if variable:
        candidates.add(variable)
    if field_name:
        candidates.add(field_name)
    if component:
        candidates.add(str(component).upper())

    if field_name == "U":
        candidates.update({"UR"})
    if field_name == "UR":
        candidates.update({"UR"})

    return sorted(candidates)


def _design_response_matches_token(spec: dict, response_token: Optional[str]) -> bool:
    if not response_token:
        return True
    return str(response_token).upper() in _design_response_token_candidates(spec)


def _safe_vtu_field_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("_") or "field"


def _dsa_response_export_field(base_name: str, response_label: object, component: Optional[str]) -> str:
    suffix = str(response_label).replace("::", "_")
    if component:
        suffix = f"{suffix}_{component}"
    base = str(base_name)
    joiner = "" if base.endswith("_") else "_"
    return _safe_vtu_field_name(f"{base}{joiner}{suffix}")


def _workspace_field_meta(workspace: str, *, step: str, instance: str, field: str) -> dict:
    workspace_abs = _workspace_path(workspace)
    conn = _manifest_conn(workspace_abs)
    try:
        result_file = conn.execute(
            "SELECT components FROM result_files WHERE step_name = ? AND field_name = ?",
            (step, field),
        ).fetchone()
        if not result_file:
            raise NotFoundError(
                f"result file not found for step='{step}' field='{field}'",
                {"workspace": workspace_abs, "step": step, "field": field},
            )

        rows = conn.execute(
            """
            SELECT DISTINCT position
            FROM result_blocks
            WHERE step_name = ? AND field_name = ? AND instance_name = ?
            ORDER BY position
            """,
            (step, field, instance),
        ).fetchall()
        positions = [str(row["position"]) for row in rows]
        if not positions:
            raise NotFoundError(
                f"result blocks not found for field '{field}'",
                {"workspace": workspace_abs, "step": step, "field": field, "instance": instance},
            )

        return {
            "field": field,
            "components": _load_json_list(result_file["components"]),
            "positions": positions,
        }
    finally:
        conn.close()


def _registry_field_meta(odb_id: str, *, step: str, instance: str, field: str) -> dict:
    fields = get_instance_fields(registry, odb_id, instance, step)
    for item in fields:
        field_name = str(item.get("name") or item.get("field") or "")
        if field_name == str(field):
            return {
                "field": field_name,
                "components": list(item.get("components") or []),
                "positions": list(item.get("positions") or []),
            }
    raise NotFoundError(
        f"field '{field}' not found",
        {"odb_id": odb_id, "step": step, "instance": instance, "field": field},
    )


def _api_field_meta(client: ODBClient, odb_id: str, *, step: str, instance: str, field: str) -> dict:
    fields = client.get_fields(odb_id, instance, step)
    for item in fields:
        field_name = str(item.get("field") or "")
        if field_name == str(field):
            return {
                "field": field_name,
                "components": list(item.get("components") or []),
                "positions": list(item.get("positions") or []),
            }
    raise NotFoundError(
        f"field '{field}' not found",
        {"odb_id": odb_id, "step": step, "instance": instance, "field": field},
    )


def _resolve_response_field_meta(
        *,
        source_mode: str,
        workspace: Optional[str],
        client: Optional[ODBClient],
        odb_id: Optional[str],
        step: str,
        instance: str,
        field: str,
) -> dict:
    if source_mode in {"workspace", "registry"}:
        return _workspace_field_meta(str(workspace), step=step, instance=instance, field=field)
    if source_mode == "l3_api":
        return _api_field_meta(client, str(odb_id), step=step, instance=instance, field=field)
    raise ValidationError("unsupported source_mode for response field resolution", {"source_mode": source_mode})


def _pick_response_position(field_meta: dict, preferred: Optional[str]) -> str:
    if preferred:
        positions = [str(x) for x in (field_meta.get("positions") or [])]
        if preferred in positions:
            return preferred
    return _pick_export_position(field_meta, None)


def _component_name_for_index(field_meta: dict, field_name: str, component_index: int) -> str:
    components = [str(item) for item in (field_meta.get("components") or []) if str(item).strip()]
    if 0 <= int(component_index) < len(components):
        return components[int(component_index)]
    return f"{str(field_name)}{int(component_index) + 1}"


def _resolve_vector_design_response_component(
        sensitivity_label_map: Dict[str, object],
        response_label_map: Dict[str, object],
        *,
        response_field_meta: dict,
        response_field_name: str,
        source_field_name: str,
        instance_name: str,
) -> Tuple[Dict[str, object], Dict[str, object], Optional[str], Optional[int]]:
    overlap = sorted(set(sensitivity_label_map.keys()) & set(response_label_map.keys()))
    if not overlap:
        return sensitivity_label_map, response_label_map, None, None

    response_vectors = []
    sensitivity_vectors = []
    for label in overlap:
        response_arr = np.asarray(response_label_map[label], dtype=np.float64).reshape(-1)
        sensitivity_arr = np.asarray(sensitivity_label_map[label], dtype=np.float64).reshape(-1)
        if response_arr.size == 1 and sensitivity_arr.size == 1:
            continue
        response_vectors.append((label, response_arr))
        sensitivity_vectors.append((label, sensitivity_arr))

    if not response_vectors:
        return sensitivity_label_map, response_label_map, None, None

    component_count = response_vectors[0][1].size
    if component_count <= 1:
        return sensitivity_label_map, response_label_map, None, None

    if any(arr.size != component_count for _, arr in response_vectors):
        raise ValidationError(
            "response values have inconsistent vector dimensions",
            {"response_field": response_field_name, "source_field": source_field_name, "instance": instance_name},
        )
    if any(arr.size != component_count for _, arr in sensitivity_vectors):
        raise ValidationError(
            "DSA sensitivity values have inconsistent vector dimensions",
            {"response_field": response_field_name, "source_field": source_field_name, "instance": instance_name},
        )

    active_indices = []
    for idx in range(component_count):
        has_signal = any(not np.isclose(arr[idx], 0.0, atol=1e-18) for _, arr in response_vectors)
        if has_signal:
            active_indices.append(idx)

    if len(active_indices) != 1:
        raise ValidationError(
            "design response component is ambiguous; update the inp design response to use an explicit component",
            {
                "response_field": response_field_name,
                "source_field": source_field_name,
                "instance": instance_name,
                "active_component_indices": active_indices,
                "available_components": [str(item) for item in (response_field_meta.get("components") or [])],
                "hint": "use U1/U2/U3, UX/UY/UZ, UR1/UR2/UR3, or RX/RY/RZ in *NODE RESPONSE",
            },
        )

    chosen_index = int(active_indices[0])
    chosen_component = _component_name_for_index(response_field_meta, response_field_name, chosen_index)
    scalar_sensitivity = dict(sensitivity_label_map)
    scalar_response = dict(response_label_map)
    for label, response_arr in response_vectors:
        scalar_response[label] = float(response_arr[chosen_index])
    for label, sensitivity_arr in sensitivity_vectors:
        scalar_sensitivity[label] = float(sensitivity_arr[chosen_index])
    return scalar_sensitivity, scalar_response, chosen_component, chosen_index


def _workspace_result_label_map(
        workspace: str,
        *,
        step: str,
        field: str,
        instance: str,
        position: str,
        frame: int,
        aggregation: str,
        component: Optional[str] = None,
        component_index: Optional[int] = None,
        result_group: Optional[str] = None,
) -> Dict[str, object]:
    workspace_abs = _workspace_path(workspace)
    conn = _manifest_conn(workspace_abs)
    try:
        if result_group is None:
            rg_result_file_clause = "result_group IS NULL"
            rg_result_file_params = []
            rg_result_block_clause = "result_group IS NULL"
            rg_result_block_params = []
            rg_frame_clause = "result_group IS NULL"
            rg_frame_params = []
        else:
            rg_result_file_clause = "result_group = ?"
            rg_result_file_params = [str(result_group)]
            rg_result_block_clause = "result_group = ?"
            rg_result_block_params = [str(result_group)]
            rg_frame_clause = "result_group = ?"
            rg_frame_params = [str(result_group)]

        result_file = conn.execute(
            f"SELECT file_path, components FROM result_files WHERE step_name = ? AND field_name = ? AND {rg_result_file_clause}",
            [step, field] + rg_result_file_params,
        ).fetchone()
        if not result_file:
            raise NotFoundError(
                f"result file not found for step='{step}' field='{field}'",
                {"step": step, "field": field, "result_group": result_group},
            )

        block_rows = conn.execute(
            f"""
            SELECT elem_type, h5_path
            FROM result_blocks
            WHERE step_name = ? AND field_name = ? AND instance_name = ? AND position = ? AND {rg_result_block_clause}
            ORDER BY elem_type
            """,
            [step, field, instance, position] + rg_result_block_params,
        ).fetchall()
        if not block_rows:
            raise NotFoundError(
                f"result block not found for field '{field}'",
                {
                    "step": step,
                    "field": field,
                    "instance": instance,
                    "position": position,
                    "result_group": result_group,
                },
            )

        frame_exists = conn.execute(
            f"SELECT 1 FROM frames WHERE step_name = ? AND frame_idx = ? AND {rg_frame_clause}",
            [step, int(frame)] + rg_frame_params,
        ).fetchone()
        if not frame_exists:
            raise ValidationError(
                f"frame {frame} not found under step '{step}'",
                {"step": step, "frame": frame, "result_group": result_group},
            )

        h5_path = os.path.join(workspace_abs, str(result_file["file_path"]))
        if not os.path.exists(h5_path):
            raise NotFoundError(f"result h5 file not found: {h5_path}", {"file_path": h5_path})

        label_map: Dict[str, object] = {}
        components = _load_json_list(result_file["components"])
        with h5py.File(h5_path, "r") as h5:
            for row in block_rows:
                group = h5[str(row["h5_path"])]
                labels = np.asarray(group["labels"][:], dtype=np.int64)
                data = _reduce_frame_values(group["data"][int(frame)], aggregation=aggregation)
                selected, _ = _select_component_values(
                    np.asarray(data),
                    components,
                    component,
                    component_index,
                )
                for label, value in zip(labels.tolist(), np.asarray(selected).tolist()):
                    if isinstance(value, list) and len(value) == 1:
                        value = value[0]
                    label_map[f"{instance}::{int(label)}"] = value
        return label_map
    finally:
        conn.close()


def _export_sensitivity_vtu(
        *,
        project_id: int,
        odb_id: Optional[str],
        output_vtu: str,
        base_url: Optional[str] = None,
        inp_path: Optional[str] = None,
        workspace: Optional[str] = None,
        odb_path: Optional[str] = None,
        step: Optional[str] = None,
        instances: Optional[List[str]] = None,
        field_prefix: Optional[str] = None,
        field_name: Optional[str] = None,
        response_component: Optional[str] = None,
        position: Optional[str] = None,
        aggregation: str = "max_abs",
        frame: int = 0,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
) -> dict:
    if aggregation not in _AGGREGATIONS and aggregation != "first":
        raise ValidationError(
            f"unsupported aggregation '{aggregation}'",
            {"aggregation": aggregation, "allowed": sorted(_AGGREGATIONS | {'first'})},
        )

    selector = _build_field_selector(field_prefix=field_prefix, field_name=field_name)
    resolved_inp_path = os.path.abspath(inp_path) if inp_path else _resolve_inp_path_from_project(project_id)
    if not os.path.exists(resolved_inp_path):
        raise NotFoundError(f"inp file not found: {resolved_inp_path}", {"inp_path": resolved_inp_path})

    node_results: Dict[str, Dict] = {}
    cell_results: Dict[str, Dict] = {}
    export_items = []
    resolved_base_url = str(base_url or get_local_service_base_url()).strip().rstrip("/")
    workspace_built = False
    client: Optional[ODBClient] = None

    if odb_path:
        if not workspace:
            raise ValidationError(
                "workspace is required when odb_path is provided",
                {"odb_path": odb_path},
            )
        build_workspace_from_odb(
            odb_path=odb_path,
            workspace=workspace,
            abaqus=abaqus,
            python3=python3,
            keep_raw=keep_raw,
        )
        resolved_workspace = _workspace_path(workspace)
        workspace_built = True
    elif workspace:
        resolved_workspace = _workspace_path(workspace)
    else:
        resolved_workspace = None

    if resolved_workspace:
        discovery = _discover_sensitivity_fields_from_workspace(
            resolved_workspace,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        source_mode = "workspace"
    elif odb_id and not base_url:
        discovery = _discover_sensitivity_fields_from_registry(
            odb_id,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        resolved_workspace = discovery["workspace"]
        source_mode = "registry"
    else:
        if not odb_id:
            raise ValidationError(
                "odb_id is required when workspace/odb_path are not provided",
                {"odb_id": odb_id},
            )
        client = ODBClient(base_url=resolved_base_url, timeout=timeout)
        discovery = _discover_sensitivity_fields(
            client,
            odb_id,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        source_mode = "l3_api"

    dsa_parameter_rows = _load_project_optimization_parameters(project_id) if selector["kind"] == "prefix" else []
    dsa_model = parse_inp(resolved_inp_path) if selector["kind"] == "prefix" else None
    dsa_direct_target_map = build_parameter_target_map(dsa_model) if dsa_model is not None else {}
    dsa_design_parameter_name_map = _build_dsa_design_parameter_name_map(dsa_model) if dsa_model is not None else {}
    dsa_response_specs = _resolve_dsa_response_spec(dsa_model, step_name=discovery["step"]) if dsa_model is not None else []
    selected_response_specs = []
    explicit_response = None
    if selector["kind"] == "prefix":
        selected_response_specs, explicit_response = _select_dsa_response_specs(
            list(dsa_response_specs or []),
            field_prefix=selector["field_prefix"],
            response_component=response_component,
        )
    dsa_parameter_map = (
        _build_dsa_parameter_row_map(dsa_parameter_rows, selector["field_prefix"], discovery["field_names"])
        if selector["kind"] == "prefix" and dsa_parameter_rows
        else {}
    )
    export_field_name = selector["field_prefix"] if selector["kind"] == "prefix" else None
    response_value_cache: Dict[Tuple[str, str, str, str, Optional[str], Optional[int], int, str], object] = {}
    for instance_name in discovery["instances"]:
        for field_meta in discovery["per_instance"][instance_name]:
            source_field_name = field_meta["field"]
            current_export_field = export_field_name or source_field_name
            selected_position = field_meta["position"]
            initial_component = (
                explicit_response["component"]
                if selector["kind"] == "prefix" and explicit_response is not None
                else None
            )
            initial_component_index = (
                explicit_response["component_index"]
                if selector["kind"] == "prefix" and explicit_response is not None
                else None
            )
            if source_mode in {"workspace", "registry"}:
                label_map = _workspace_result_label_map(
                    resolved_workspace,
                    step=discovery["step"],
                    field=source_field_name,
                    instance=instance_name,
                    position=selected_position,
                    frame=frame,
                    aggregation=aggregation,
                    component=initial_component,
                    component_index=initial_component_index,
                )
            else:
                label_map = client.get_result_label_map(
                    odb_id=odb_id,
                    instance=instance_name,
                    step=discovery["step"],
                    field=source_field_name,
                    position=selected_position,
                    frame=frame,
                    aggregation=aggregation,
                    component=initial_component,
                    component_index=initial_component_index,
                    scoped=True,
                )
            label_map = _normalize_vtu_label_map(label_map)
            if selector["kind"] == "prefix":
                parameter_token = _extract_dsa_field_token(selector["field_prefix"], source_field_name)
                direct_target_rows = list(dsa_direct_target_map.get(parameter_token, []))
                mapped_parameter_name = parameter_token
                token_match = _DSA_PARAMETER_TOKEN_RE.fullmatch(parameter_token)
                token_index = int(token_match.group(2)) if token_match else None
                if (
                        not direct_target_rows
                        and token_index is not None
                        and token_index in dsa_design_parameter_name_map
                ):
                    mapped_parameter_name = dsa_design_parameter_name_map[token_index]
                    direct_target_rows = list(dsa_direct_target_map.get(mapped_parameter_name, []))
                target_rows = direct_target_rows
                mapping_mode = "inp_parameter"
                if not target_rows:
                    parameter_row = _resolve_dsa_parameter_row(dsa_parameter_map, source_field_name)
                    target_rows = [parameter_row]
                    mapped_parameter_name = str(parameter_row.get("parameter_name") or parameter_token)
                    mapping_mode = "optimization_parameter"

                normalized = False
                response_field_name = None
                response_component = None
                response_position = None
                response_value = None
                export_value_map = None
                response_label_map = None
                chosen_response_spec = None
                if selected_response_specs:
                    candidate_specs = list(selected_response_specs)
                    best_score = -1
                    best_specs = []
                    best_response_label_map = None
                    best_position = None
                    best_response_field_meta = None

                    for spec in candidate_specs:
                        candidate_field_name = str(spec["field_name"])
                        candidate_component = (
                            explicit_response["component"]
                            if explicit_response is not None
                            else spec.get("component")
                        )
                        candidate_component_index = (
                            explicit_response["component_index"]
                            if explicit_response is not None
                            else spec.get("component_index")
                        )
                        if source_mode in {"workspace", "registry"}:
                            candidate_dsa_map = _workspace_result_label_map(
                                resolved_workspace,
                                step=discovery["step"],
                                field=source_field_name,
                                instance=instance_name,
                                position=selected_position,
                                frame=frame,
                                aggregation=aggregation,
                                component=candidate_component,
                                component_index=candidate_component_index,
                            )
                        else:
                            candidate_dsa_map = client.get_result_label_map(
                                odb_id=odb_id,
                                instance=instance_name,
                                step=discovery["step"],
                                field=source_field_name,
                                position=selected_position,
                                frame=frame,
                                aggregation=aggregation,
                                component=candidate_component,
                                component_index=candidate_component_index,
                                scoped=True,
                            )
                        candidate_dsa_map = _normalize_vtu_label_map(candidate_dsa_map)
                        if not candidate_dsa_map:
                            continue

                        response_field_meta = _resolve_response_field_meta(
                            source_mode=source_mode,
                            workspace=resolved_workspace,
                            client=client,
                            odb_id=odb_id,
                            step=discovery["step"],
                            instance=instance_name,
                            field=candidate_field_name,
                        )
                        candidate_position = _pick_response_position(
                            response_field_meta,
                            spec.get("preferred_position"),
                        )
                        response_cache_key = (
                            str(instance_name),
                            str(discovery["step"]),
                            candidate_field_name,
                            candidate_position,
                            candidate_component,
                            candidate_component_index,
                            int(frame),
                            str(aggregation),
                        )
                        if response_cache_key not in response_value_cache:
                            if source_mode in {"workspace", "registry"}:
                                cached_response_map = _workspace_result_label_map(
                                    resolved_workspace,
                                    step=discovery["step"],
                                    field=candidate_field_name,
                                    instance=instance_name,
                                    position=candidate_position,
                                    frame=frame,
                                    aggregation=aggregation,
                                    component=candidate_component,
                                    component_index=candidate_component_index,
                                )
                            else:
                                cached_response_map = client.get_result_label_map(
                                    odb_id=odb_id,
                                    instance=instance_name,
                                    step=discovery["step"],
                                    field=candidate_field_name,
                                    position=candidate_position,
                                    frame=frame,
                                    aggregation=aggregation,
                                    component=candidate_component,
                                    component_index=candidate_component_index,
                                    scoped=True,
                                )
                            response_value_cache[response_cache_key] = _normalize_vtu_label_map(cached_response_map)

                        candidate_response_label_map = response_value_cache[response_cache_key]
                        overlap = sorted(set(candidate_dsa_map.keys()) & set(candidate_response_label_map.keys()))
                        score = len(overlap)
                        if score <= 0:
                            continue
                        if score > best_score:
                            best_score = score
                            best_specs = [spec]
                            best_response_label_map = candidate_response_label_map
                            best_position = candidate_position
                            best_response_field_meta = response_field_meta
                            label_map = candidate_dsa_map
                        elif score == best_score:
                            best_specs.append(spec)

                    if len(best_specs) == 1:
                        chosen_response_spec = best_specs[0]
                        response_label_map = dict(best_response_label_map or {})
                        response_field_name = str(chosen_response_spec["field_name"])
                        response_component = (
                            explicit_response["component"]
                            if explicit_response is not None
                            else chosen_response_spec.get("component")
                        )
                        response_position = best_position
                        if (
                                explicit_response is None
                                and response_component is None
                                and best_response_field_meta is not None
                        ):
                            (
                                label_map,
                                response_label_map,
                                inferred_component,
                                _,
                            ) = _resolve_vector_design_response_component(
                                label_map,
                                response_label_map,
                                response_field_meta=best_response_field_meta,
                                response_field_name=response_field_name,
                                source_field_name=source_field_name,
                                instance_name=str(instance_name),
                            )
                            if inferred_component is not None:
                                response_component = inferred_component
                    elif len(best_specs) > 1:
                        raise ValidationError(
                            "multiple design responses match the requested DSA field",
                            {
                                "field_prefix": selector["field_prefix"],
                                "source_field": source_field_name,
                                "step": discovery["step"],
                                "instance": instance_name,
                                "matches": [
                                    {
                                        "field_name": str(spec["field_name"]),
                                        "component": spec.get("component"),
                                        "set_name": spec.get("set_name"),
                                        "region_type": spec.get("region_type"),
                                    }
                                    for spec in best_specs
                                ],
                            },
                        )

                if chosen_response_spec and response_label_map:
                    parameter_value = _resolve_dsa_parameter_scalar_value(
                        dsa_model,
                        parameter_name=mapped_parameter_name,
                        target_rows=target_rows,
                    )
                    matched_labels = sorted(set(label_map.keys()) & set(response_label_map.keys()))
                    if not matched_labels:
                        raise ValidationError(
                            "no overlapping response labels were found for DSA normalization",
                            {
                                "source_field": source_field_name,
                                "response_field": response_field_name,
                                "instance": instance_name,
                            },
                        )
                    export_value_map = {}
                    for response_label_key in matched_labels:
                        normalized_value = _normalize_dsa_sensitivity_value(
                            label_map[response_label_key],
                            parameter_value=parameter_value,
                            response_value=response_label_map[response_label_key],
                            source_field=source_field_name,
                            response_field=response_field_name,
                        )
                        export_name = (
                            current_export_field
                            if len(matched_labels) == 1
                            else _dsa_response_export_field(
                                current_export_field,
                                response_label_key,
                                response_component,
                            )
                        )
                        export_value_map[export_name] = normalized_value
                    normalized = True
                    response_value = (
                        next(iter(response_label_map.values()))
                        if len(response_label_map) == 1
                        else None
                    )

                if export_value_map is None:
                    raw_dsa_value = _extract_single_dsa_result_value(label_map, source_field=source_field_name)
                    export_value_map = {current_export_field: raw_dsa_value}

                for export_name, dsa_value in export_value_map.items():
                    for target_row in target_rows:
                        target, composed_map = _compose_dsa_label_map(dsa_model, target_row, dsa_value)
                        if target == "point":
                            _merge_vtu_result_map(
                                node_results.setdefault(export_name, {}),
                                composed_map,
                                details={
                                    "export_field": export_name,
                                    "source_field": source_field_name,
                                    "parameter_name": mapped_parameter_name,
                                    "set_name": target_row.get("set_name"),
                                },
                            )
                        else:
                            _merge_vtu_result_map(
                                cell_results.setdefault(export_name, {}),
                                composed_map,
                                details={
                                    "export_field": export_name,
                                    "source_field": source_field_name,
                                    "parameter_name": mapped_parameter_name,
                                    "set_name": target_row.get("set_name"),
                                },
                            )
                target = "mixed" if len({str(row.get("set_type")) for row in target_rows}) > 1 else (
                    "point" if target_rows and str(target_rows[0].get("set_type")).upper() == "NSET" else "cell"
                )
            else:
                if selected_position == "NODAL":
                    node_results.setdefault(current_export_field, {}).update(label_map)
                    target = "point"
                else:
                    cell_results.setdefault(current_export_field, {}).update(label_map)
                    target = "cell"
            export_items.append(
                {
                    "instance": instance_name,
                    "field": source_field_name,
                    "export_field": current_export_field,
                    "parameter_name": mapped_parameter_name if selector["kind"] == "prefix" else None,
                    "mapping_mode": mapping_mode if selector["kind"] == "prefix" else None,
                    "normalized": normalized if selector["kind"] == "prefix" else None,
                    "generated_fields": sorted(export_value_map.keys()) if selector["kind"] == "prefix" else None,
                    "response_field": response_field_name if selector["kind"] == "prefix" else None,
                    "response_component": response_component if selector["kind"] == "prefix" else None,
                    "response_position": response_position if selector["kind"] == "prefix" else None,
                    "response_value": response_value if selector["kind"] == "prefix" else None,
                    "position": selected_position,
                    "target": target,
                    "value_count": len(label_map),
                }
            )

    from tools.inp_to_vtu import write_vtu

    output_vtu_abs = os.path.abspath(output_vtu)
    write_vtu(
        resolved_inp_path,
        output_vtu_abs,
        node_results=node_results or None,
        cell_results=cell_results or None,
    )

    result = {
        "project_id": project_id,
        "odb_id": odb_id,
        "base_url": resolved_base_url if source_mode == "l3_api" else None,
        "workspace": resolved_workspace,
        "source_mode": source_mode,
        "workspace_built": workspace_built,
        "inp_path": resolved_inp_path,
        "output_vtu": output_vtu_abs,
        "step": discovery["step"],
        "instances": discovery["instances"],
        "frame": int(frame),
        "aggregation": aggregation,
        "exported_field_count": len(discovery["field_names"]),
        "exported_fields": discovery["field_names"],
        "items": export_items,
    }
    if selector["kind"] == "prefix":
        result["field_prefix"] = selector["field_prefix"]
        result["response_component"] = explicit_response["component"] if explicit_response is not None else None
    else:
        result["field_name"] = selector["field_name"]
    return result


def export_dsa_sensitivity_vtu(
        *,
        project_id: int,
        odb_id: Optional[str],
        output_vtu: str,
        base_url: Optional[str] = None,
        inp_path: Optional[str] = None,
        workspace: Optional[str] = None,
        odb_path: Optional[str] = None,
        step: Optional[str] = None,
        instances: Optional[List[str]] = None,
        field_prefix: str = "d_U_",
        response_component: Optional[str] = None,
        position: Optional[str] = None,
        aggregation: str = "max_abs",
        frame: int = 0,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
) -> dict:
    return _export_sensitivity_vtu(
        project_id=project_id,
        odb_id=odb_id,
        output_vtu=output_vtu,
        base_url=base_url,
        inp_path=inp_path,
        workspace=workspace,
        odb_path=odb_path,
        step=step,
        instances=instances,
        field_prefix=field_prefix,
        response_component=response_component,
        position=position,
        aggregation=aggregation,
        frame=frame,
        abaqus=abaqus,
        python3=python3,
        keep_raw=keep_raw,
        timeout=timeout,
    )


def export_adjoint_sensitivity_vtu(
        *,
        project_id: int,
        odb_id: Optional[str],
        output_vtu: str,
        field_name: str,
        base_url: Optional[str] = None,
        inp_path: Optional[str] = None,
        workspace: Optional[str] = None,
        odb_path: Optional[str] = None,
        step: Optional[str] = None,
        instances: Optional[List[str]] = None,
        position: Optional[str] = None,
        aggregation: str = "max_abs",
        frame: int = 0,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
) -> dict:
    return _export_sensitivity_vtu(
        project_id=project_id,
        odb_id=odb_id,
        output_vtu=output_vtu,
        base_url=base_url,
        inp_path=inp_path,
        workspace=workspace,
        odb_path=odb_path,
        step=step,
        instances=instances,
        field_name=field_name,
        response_component=None,
        position=position,
        aggregation=aggregation,
        frame=frame,
        abaqus=abaqus,
        python3=python3,
        keep_raw=keep_raw,
        timeout=timeout,
    )


def export_odb_sensitivity_vtu(
        *,
        project_id: int,
        odb_id: Optional[str],
        output_vtu: str,
        base_url: Optional[str] = None,
        inp_path: Optional[str] = None,
        workspace: Optional[str] = None,
        odb_path: Optional[str] = None,
        step: Optional[str] = None,
        instances: Optional[List[str]] = None,
        field_prefix: str = "d_U_",
        response_component: Optional[str] = None,
        position: Optional[str] = None,
        aggregation: str = "max_abs",
        frame: int = 0,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
) -> dict:
    return export_dsa_sensitivity_vtu(
        project_id=project_id,
        odb_id=odb_id,
        output_vtu=output_vtu,
        base_url=base_url,
        inp_path=inp_path,
        workspace=workspace,
        odb_path=odb_path,
        step=step,
        instances=instances,
        field_prefix=field_prefix,
        response_component=response_component,
        position=position,
        aggregation=aggregation,
        frame=frame,
        abaqus=abaqus,
        python3=python3,
        keep_raw=keep_raw,
        timeout=timeout,
    )


def merge_dsa_sensitivity_fields(
        *,
        project_id: int,
        workspace: str,
        step: str,
        frame: int = 0,
        field_prefix: str,
        instances: Optional[List[str]] = None,
        result_group: str = "merged_dsa",
        source_result_group: Optional[str] = None,
) -> List[dict]:
    """
    查询 project 的优化参数，合并 DSA 灵敏度场写入 workspace。

    返回写入的字段列表，每条含 field_name / response_node_label /
    component / instance_count / element_count。
    """
    from src.l3.services.dsa_merge_service import merge_dsa_fields

    parameter_rows = _load_project_optimization_parameters(project_id)
    if not parameter_rows:
        raise ValidationError(
            "no optimization parameters found for project",
            {"project_id": int(project_id)},
        )

    return merge_dsa_fields(
        workspace=workspace,
        step=step,
        frame=frame,
        field_prefix=field_prefix,
        instances=list(instances) if instances else [],
        parameter_rows=parameter_rows,
        result_group=result_group,
        source_result_group=source_result_group,
    )
