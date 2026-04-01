import json
import os
import sqlite3
import subprocess
import sys
from typing import Iterable, List, Optional

import h5py
import numpy as np

from src.l3.core.errors import NotFoundError, ValidationError


_POSITION_PRIORITY = ("NODAL", "ELEMENT_NODAL", "INTEGRATION_POINT")
_AGGREGATIONS = {"max_abs", "mean_abs", "max", "min", "mean"}


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
