import json
import os
import re
import sqlite3
import subprocess
import sys
from typing import Dict, Iterable, List, Optional

import h5py
import numpy as np

from db import get_connection
from src.inp import parse_inp
from src.l3.core.state import registry
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services.node_table_service import get_instance_fields
from tools.odb_client import ODBClient


_POSITION_PRIORITY = ("NODAL", "ELEMENT_NODAL", "INTEGRATION_POINT")
_AGGREGATIONS = {"max_abs", "mean_abs", "max", "min", "mean"}
_VTU_POSITION_PRIORITY = ("WHOLE_ELEMENT", "INTEGRATION_POINT", "ELEMENT_NODAL", "NODAL")
_DSA_PARAMETER_TOKEN_RE = re.compile(r"^T(\d+)$", re.IGNORECASE)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _workspace_path(workspace: str) -> str:
    path = os.path.abspath(workspace)
    manifest = os.path.join(path, "manifest.db")
    if not os.path.exists(manifest):
        raise NotFoundError(f"manifest.db not found under workspace '{path}'", {"workspace": path})
    return path


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
            step_no = 10**9
        return (step_no, str(row.get("step_name") or ""))

    ordered = sorted(step_rows, key=_sort_key)
    step_name = ordered[0].get("step_name")
    return str(step_name) if step_name is not None else None


def build_workspace_from_odb(
    odb_path: str,
    workspace: str,
    abaqus: str = "abaqus",
    python3: Optional[str] = None,
    keep_raw: bool = False,
) -> dict:
    odb_abs = os.path.abspath(odb_path)
    if not os.path.exists(odb_abs):
        raise NotFoundError(f"odb file not found: {odb_abs}", {"odb_path": odb_abs})

    workspace_abs = os.path.abspath(workspace)
    cmd = [
        python3 or sys.executable,
        os.path.join(_repo_root(), "tools", "run_l1.py"),
        "--odb",
        odb_abs,
        "--out",
        workspace_abs,
        "--abaqus",
        abaqus,
    ]
    if keep_raw:
        cmd.append("--keep-raw")

    result = subprocess.run(
        cmd,
        cwd=_repo_root(),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValidationError(
            "failed to build workspace from odb",
            {
                "odb_path": odb_abs,
                "workspace": workspace_abs,
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            },
        )

    manifest = os.path.join(workspace_abs, "manifest.db")
    return {
        "odb_path": odb_abs,
        "workspace": workspace_abs,
        "manifest_db": manifest if os.path.exists(manifest) else None,
        "stdout_tail": result.stdout[-4000:],
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


def _resolve_inp_path_from_project(project_id: int) -> str:
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
            (project_id,),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if not row or not row.get("source_file_path"):
        raise NotFoundError(
            f"no imported inp metadata found for project_id={project_id}",
            {"project_id": project_id},
        )

    inp_path = os.path.abspath(str(row["source_file_path"]))
    if not os.path.exists(inp_path):
        raise NotFoundError(
            f"inp file not found on disk: {inp_path}",
            {"project_id": project_id, "inp_path": inp_path},
        )
    return inp_path


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
            if selector["kind"] == "prefix":
                rows = conn.execute(
                    """
                    SELECT rf.field_name, rb.position
                    FROM result_files rf
                    JOIN result_blocks rb
                      ON rb.step_name = rf.step_name
                     AND rb.field_name = rf.field_name
                    WHERE rf.step_name = ?
                      AND rb.instance_name = ?
                      AND rf.field_name LIKE ?
                    ORDER BY rf.field_name, rb.position
                    """,
                    (chosen_step, instance_name, f"{selector['field_prefix']}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT rf.field_name, rb.position
                    FROM result_files rf
                    JOIN result_blocks rb
                      ON rb.step_name = rf.step_name
                     AND rb.field_name = rf.field_name
                    WHERE rf.step_name = ?
                      AND rb.instance_name = ?
                      AND rf.field_name = ?
                    ORDER BY rf.field_name, rb.position
                    """,
                    (chosen_step, instance_name, selector["field_name"]),
                ).fetchall()

            fields_map: Dict[str, List[str]] = {}
            for row in rows:
                fields_map.setdefault(str(row["field_name"]), []).append(str(row["position"]))

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
            SELECT id, parameter_name, candidate_code, set_name, set_type, set_scope, instance_name, part_name
            FROM t_mt_py_fem_optimization_parameter
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
    source_name = str(source_field_name)
    if not source_name.startswith(field_prefix):
        raise ValidationError(
            "DSA field does not match the requested prefix",
            {"field_prefix": field_prefix, "field": source_name},
        )

    suffix = source_name[len(field_prefix):] or source_name
    match = _DSA_PARAMETER_TOKEN_RE.fullmatch(suffix)
    if not match:
        raise ValidationError(
            "DSA field name does not contain a T-index suffix",
            {"field_prefix": field_prefix, "field": source_name, "suffix": suffix},
        )
    return int(match.group(1))


def _build_dsa_parameter_row_map(parameter_rows: List[dict], field_prefix: str, source_field_names: List[str]) -> Dict[str, dict]:
    if not parameter_rows:
        raise ValidationError("no optimization parameters found for DSA VTU export", {"field_prefix": field_prefix})

    ordered_fields = sorted(
        {str(name) for name in source_field_names},
        key=lambda name: (_extract_dsa_field_index(field_prefix, name), str(name)),
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
    all_indices = [_extract_dsa_field_index(field_prefix, name) for name in ordered_fields]
    direct_indices_valid = all(1 <= idx <= len(parameter_rows) for idx in all_indices)
    direct_indices_unique = len(set(all_indices)) == len(all_indices)

    if direct_indices_valid and direct_indices_unique:
        for field_name, parameter_index in zip(ordered_fields, all_indices):
            field_map[field_name] = parameter_rows[parameter_index - 1]
        return field_map

    for offset, field_name in enumerate(ordered_fields):
        field_map[field_name] = parameter_rows[offset]
    return field_map


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


def _workspace_result_label_map(
    workspace: str,
    *,
    step: str,
    field: str,
    instance: str,
    position: str,
    frame: int,
    aggregation: str,
) -> Dict[str, object]:
    workspace_abs = _workspace_path(workspace)
    conn = _manifest_conn(workspace_abs)
    try:
        result_file = conn.execute(
            "SELECT file_path FROM result_files WHERE step_name = ? AND field_name = ?",
            (step, field),
        ).fetchone()
        if not result_file:
            raise NotFoundError(
                f"result file not found for step='{step}' field='{field}'",
                {"step": step, "field": field},
            )

        block_rows = conn.execute(
            """
            SELECT elem_type, h5_path
            FROM result_blocks
            WHERE step_name = ? AND field_name = ? AND instance_name = ? AND position = ?
            ORDER BY elem_type
            """,
            (step, field, instance, position),
        ).fetchall()
        if not block_rows:
            raise NotFoundError(
                f"result block not found for field '{field}'",
                {"step": step, "field": field, "instance": instance, "position": position},
            )

        frame_exists = conn.execute(
            "SELECT 1 FROM frames WHERE step_name = ? AND frame_idx = ?",
            (step, int(frame)),
        ).fetchone()
        if not frame_exists:
            raise ValidationError(
                f"frame {frame} not found under step '{step}'",
                {"step": step, "frame": frame},
            )

        h5_path = os.path.join(workspace_abs, str(result_file["file_path"]))
        if not os.path.exists(h5_path):
            raise NotFoundError(f"result h5 file not found: {h5_path}", {"file_path": h5_path})

        label_map: Dict[str, object] = {}
        with h5py.File(h5_path, "r") as h5:
            for row in block_rows:
                group = h5[str(row["h5_path"])]
                labels = np.asarray(group["labels"][:], dtype=np.int64)
                data = _reduce_frame_values(group["data"][int(frame)], aggregation=aggregation)
                for label, value in zip(labels.tolist(), data.tolist()):
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
    position: Optional[str] = None,
    aggregation: str = "max_abs",
    frame: int = 0,
    abaqus: str = "abaqus",
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
    resolved_base_url = base_url or "http://127.0.0.1:18765"
    workspace_built = False

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
    dsa_parameter_map = (
        _build_dsa_parameter_row_map(dsa_parameter_rows, selector["field_prefix"], discovery["field_names"])
        if selector["kind"] == "prefix"
        else {}
    )
    dsa_model = parse_inp(resolved_inp_path) if selector["kind"] == "prefix" else None
    export_field_name = selector["field_prefix"] if selector["kind"] == "prefix" else None
    for instance_name in discovery["instances"]:
        for field_meta in discovery["per_instance"][instance_name]:
            source_field_name = field_meta["field"]
            current_export_field = export_field_name or source_field_name
            selected_position = field_meta["position"]
            if source_mode in {"workspace", "registry"}:
                label_map = _workspace_result_label_map(
                    resolved_workspace,
                    step=discovery["step"],
                    field=source_field_name,
                    instance=instance_name,
                    position=selected_position,
                    frame=frame,
                    aggregation=aggregation,
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
                    scoped=True,
                )
            label_map = _normalize_vtu_label_map(label_map)
            if selector["kind"] == "prefix":
                parameter_row = _resolve_dsa_parameter_row(dsa_parameter_map, source_field_name)
                dsa_value = _extract_single_dsa_result_value(label_map, source_field=source_field_name)
                target, composed_map = _compose_dsa_label_map(dsa_model, parameter_row, dsa_value)
                if target == "point":
                    _merge_vtu_result_map(
                        node_results.setdefault(current_export_field, {}),
                        composed_map,
                        details={
                            "export_field": current_export_field,
                            "source_field": source_field_name,
                            "parameter_name": parameter_row.get("parameter_name"),
                            "set_name": parameter_row.get("set_name"),
                        },
                    )
                else:
                    _merge_vtu_result_map(
                        cell_results.setdefault(current_export_field, {}),
                        composed_map,
                        details={
                            "export_field": current_export_field,
                            "source_field": source_field_name,
                            "parameter_name": parameter_row.get("parameter_name"),
                            "set_name": parameter_row.get("set_name"),
                        },
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
                    "parameter_name": parameter_row.get("parameter_name") if selector["kind"] == "prefix" else None,
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
    field_prefix: str = "d_UR_",
    position: Optional[str] = None,
    aggregation: str = "max_abs",
    frame: int = 0,
    abaqus: str = "abaqus",
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
    abaqus: str = "abaqus",
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
    field_prefix: str = "d_UR_",
    position: Optional[str] = None,
    aggregation: str = "max_abs",
    frame: int = 0,
    abaqus: str = "abaqus",
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
        position=position,
        aggregation=aggregation,
        frame=frame,
        abaqus=abaqus,
        python3=python3,
        keep_raw=keep_raw,
        timeout=timeout,
    )
