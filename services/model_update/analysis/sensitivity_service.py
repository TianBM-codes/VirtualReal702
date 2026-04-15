import json
import os
import re
import sqlite3
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import h5py
import numpy as np

from db import get_connection
from src.inp import parse_inp
from src.inp.parameter_mapping import build_parameter_target_map
from src.l3.core.state import registry
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services.node_table_service import get_instance_fields
from tools.odb_client import ODBClient, _select_component_values

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


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


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
            step_no = 10 ** 9
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
            SELECT op.id, op.parameter_name, op.candidate_code, op.set_name, op.set_type, op.set_scope,
                   op.instance_name, op.part_name, op.scatter, cand.scalar_value
            FROM t_mt_py_fem_optimization_parameter op
            LEFT JOIN t_mt_py_fem_parameter_candidate cand
              ON cand.pid = op.pid AND cand.candidate_code = op.candidate_code
            WHERE op.pid = %s
            ORDER BY op.created_at ASC, op.id ASC
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
) -> Dict[str, object]:
    workspace_abs = _workspace_path(workspace)
    conn = _manifest_conn(workspace_abs)
    try:
        result_file = conn.execute(
            "SELECT file_path, components FROM result_files WHERE step_name = ? AND field_name = ?",
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
        field_prefix: str = "d_UR_",
        response_component: Optional[str] = None,
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
        field_prefix: str = "d_UR_",
        response_component: Optional[str] = None,
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
        response_component=response_component,
        position=position,
        aggregation=aggregation,
        frame=frame,
        abaqus=abaqus,
        python3=python3,
        keep_raw=keep_raw,
        timeout=timeout,
    )
