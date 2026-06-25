"""
DSA sensitivity field merge service.

Reads raw DSA fields such as ``d_U_T1`` .. ``d_U_TN`` from a workspace and
publishes visualization-ready external results back into the workspace.

Published layout:
- one response => one ``result_group``
- one parameter type => one ``field_name`` (for example ``E`` / ``RHO`` /
  ``THICKNESS``)
- one frame only (``frame_idx=0``)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Dict, List, Optional

import h5py
import numpy as np

from ..infra.manifest_repo import ManifestRepo
from .external_result_writer import ExternalResultWriter

_DSA_TOKEN_RE = re.compile(r"^([A-Z_][A-Z0-9_]*?)(\d+)$", re.IGNORECASE)
_PARAMETER_TYPE_FIELD_MAP = {
    "E": "E",
    "RHO": "RHO",
    "T": "THICKNESS",
    "H": "THICKNESS",
    "THICKNESS": "THICKNESS",
}


def _manifest_conn(workspace_abs: str) -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(workspace_abs, "manifest.db"), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_result_group_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    if not text:
        raise ValueError("result_group name cannot be empty")
    return text[:96]


def _parameter_type_field_name(param_row: dict) -> str:
    for raw in (
        param_row.get("quantity_code"),
        param_row.get("param_type"),
        param_row.get("type"),
    ):
        token = str(raw or "").strip().upper()
        if token in _PARAMETER_TYPE_FIELD_MAP:
            return _PARAMETER_TYPE_FIELD_MAP[token]

    parameter_name = str(param_row.get("parameter_name") or "").strip().upper()
    match = re.match(r"^[A-Z_]+", parameter_name)
    if match:
        token = match.group(0)
        if token in _PARAMETER_TYPE_FIELD_MAP:
            return _PARAMETER_TYPE_FIELD_MAP[token]

    return parameter_name or "SENSITIVITY"


def _response_result_group_name(base_result_group: str, node_label: int, component: str) -> str:
    return _normalize_result_group_name(f"{base_result_group}_{int(node_label)}_{str(component or '').strip()}")


def _field_param_index(field_prefix: str, field_name: str) -> Optional[int]:
    if not field_name.startswith(field_prefix):
        return None
    suffix = field_name[len(field_prefix):]
    match = _DSA_TOKEN_RE.fullmatch(suffix)
    if match:
        return int(match.group(2))
    prefix_tail = field_prefix.rstrip("_").rsplit("_", 1)[-1]
    combined = prefix_tail + suffix
    match = _DSA_TOKEN_RE.fullmatch(combined)
    if match:
        return int(match.group(2))
    return None


def _read_dsa_field(
    workspace_abs: str,
    step: str,
    field_name: str,
    frame: int,
    source_result_group: Optional[str],
):
    conn = _manifest_conn(workspace_abs)
    try:
        if source_result_group is None:
            rg_clause = "result_group IS NULL"
            rg_params: List[object] = []
        else:
            rg_clause = "result_group = ?"
            rg_params = [source_result_group]

        result_file = conn.execute(
            f"SELECT file_path, components FROM result_files WHERE step_name=? AND field_name=? AND {rg_clause}",
            [step, field_name] + rg_params,
        ).fetchone()
        if result_file is None:
            return None, []

        try:
            components = json.loads(result_file["components"]) if result_file["components"] else []
        except Exception:
            components = []

        h5_file = os.path.join(workspace_abs, str(result_file["file_path"]))
        if not os.path.exists(h5_file):
            return components, []

        block_rows = conn.execute(
            f"SELECT instance_name, h5_path FROM result_blocks WHERE step_name=? AND field_name=? AND {rg_clause} ORDER BY instance_name",
            [step, field_name] + rg_params,
        ).fetchall()

        blocks = []
        with h5py.File(h5_file, "r") as handle:
            for row in block_rows:
                h5_path = str(row["h5_path"])
                if h5_path not in handle:
                    continue
                group = handle[h5_path]
                if "labels" not in group or "data" not in group:
                    continue
                labels = group["labels"][:].astype(np.int32)
                raw = group["data"]
                if raw.ndim == 3:
                    data = raw[int(frame)].astype(np.float32)
                elif raw.ndim == 2:
                    data = raw[:].astype(np.float32)
                else:
                    continue
                blocks.append(
                    {
                        "instance_name": str(row["instance_name"]),
                        "labels": labels,
                        "data": data,
                    }
                )
        return components, blocks
    finally:
        conn.close()


def _resolve_element_labels(repo: ManifestRepo, param_row: dict) -> Dict[str, List[int]]:
    result: Dict[str, List[int]] = {}

    element_label = param_row.get("element_label")
    if element_label is not None:
        instance_name = param_row.get("instance_name") or ""
        if instance_name:
            result[str(instance_name)] = [int(element_label)]
            return result
        part_name = param_row.get("part_name")
        if part_name:
            for instance in repo.get_instances_by_part_name(part_name):
                result[str(instance)] = [int(element_label)]
        else:
            for row in repo.list_instances():
                result[str(row["instance_name"])] = [int(element_label)]
        return result

    set_name = str(param_row.get("set_name") or "")
    set_scope = str(param_row.get("set_scope") or "PART").upper()
    instance_name = param_row.get("instance_name")
    part_name = param_row.get("part_name")

    if set_scope == "ASSEMBLY" and instance_name:
        labels_arr = repo.get_element_set_labels(set_name, instance_name)
        if labels_arr is not None and len(labels_arr):
            result[str(instance_name)] = labels_arr.tolist()
        return result

    candidate_instances = (
        repo.get_instances_by_part_name(part_name)
        if part_name
        else ([str(instance_name)] if instance_name else [])
    )
    for instance in candidate_instances:
        labels_arr = repo.get_element_set_labels(set_name, instance)
        if labels_arr is not None and len(labels_arr):
            result[str(instance)] = labels_arr.tolist()
    return result


def merge_dsa_fields(
    *,
    workspace: str,
    step: str,
    frame: int,
    field_prefix: str,
    instances: List[str],
    parameter_rows: List[dict],
    result_group: str,
    source_result_group: Optional[str] = None,
) -> List[dict]:
    workspace_abs = os.path.abspath(workspace)
    repo = ManifestRepo(workspace_abs)

    conn = _manifest_conn(workspace_abs)
    try:
        if source_result_group is None:
            rg_clause = "result_group IS NULL"
            rg_params: List[object] = []
        else:
            rg_clause = "result_group = ?"
            rg_params = [source_result_group]
        field_rows = conn.execute(
            f"SELECT DISTINCT field_name FROM result_files WHERE step_name=? AND {rg_clause}",
            [step] + rg_params,
        ).fetchall()
    finally:
        conn.close()

    dsa_field_names = []
    for row in field_rows:
        field_name = str(row["field_name"])
        field_index = _field_param_index(field_prefix, field_name)
        if field_index is not None:
            dsa_field_names.append((field_index, field_name))
    if not dsa_field_names:
        return []
    dsa_field_names.sort(key=lambda item: item[0])

    field_data: Dict[str, tuple] = {}
    components: List[str] = []
    for _, field_name in dsa_field_names:
        comps, blocks = _read_dsa_field(workspace_abs, step, field_name, frame, source_result_group)
        if not blocks:
            continue
        field_data[field_name] = (comps, blocks)
        if not components and comps:
            components = comps
    if not field_data:
        return []
    if not components:
        components = ["C1", "C2", "C3"]

    param_elements: Dict[str, Dict[str, List[int]]] = {}
    field_parameter_types: Dict[str, str] = {}
    for field_index, field_name in dsa_field_names:
        if field_name not in field_data:
            continue
        row_index = field_index - 1
        if row_index < 0 or row_index >= len(parameter_rows):
            continue
        param_row = parameter_rows[row_index]
        param_elements[field_name] = _resolve_element_labels(repo, param_row)
        field_parameter_types[field_name] = _parameter_type_field_name(param_row)

    first_blocks = next(iter(field_data.values()))[1]
    response_labels: List[int] = []
    seen_labels = set()
    for block in first_blocks:
        for label in block["labels"].tolist():
            if label in seen_labels:
                continue
            seen_labels.add(label)
            response_labels.append(int(label))

    if instances:
        target_instances = [str(item) for item in instances]
    else:
        target_instances = sorted({key for item in param_elements.values() for key in item.keys()})

    parameter_type_fields = sorted({value for value in field_parameter_types.values() if value})
    written: List[dict] = []

    for node_label in response_labels:
        for component_index, component_name in enumerate(components):
            response_group = _response_result_group_name(result_group, node_label, component_name)
            writer = ExternalResultWriter(workspace_abs, response_group)

            for parameter_type in parameter_type_fields:
                writer.clear_field(step, parameter_type)
                total_elements = 0
                instance_count = 0

                for instance_name in target_instances:
                    label_value_map: Dict[int, float] = {}

                    for _, field_name in dsa_field_names:
                        if field_name not in field_data or field_name not in param_elements:
                            continue
                        if field_parameter_types.get(field_name) != parameter_type:
                            continue
                        element_labels = param_elements[field_name].get(instance_name)
                        if not element_labels:
                            continue

                        _, blocks = field_data[field_name]
                        scalar_value: Optional[float] = None
                        for block in blocks:
                            labels_arr = block["labels"]
                            position = int(np.searchsorted(labels_arr, node_label))
                            if position < len(labels_arr) and labels_arr[position] == node_label:
                                data_row = block["data"][position]
                                column_index = component_index if component_index < len(data_row) else 0
                                scalar_value = float(data_row[column_index])
                                break
                        if scalar_value is None:
                            continue

                        for element_label in element_labels:
                            label = int(element_label)
                            current_value = label_value_map.get(label)
                            if current_value is not None and not np.isclose(current_value, scalar_value):
                                raise ValueError(
                                    f"conflicting merged sensitivity values for result_group={response_group}, "
                                    f"field={parameter_type}, instance={instance_name}, label={label}"
                                )
                            label_value_map[label] = scalar_value

                    if not label_value_map:
                        continue

                    frame_data = [
                        {"label": label, "values": [value]}
                        for label, value in sorted(label_value_map.items())
                    ]
                    writer.write_element(
                        instance=instance_name,
                        step=step,
                        field=parameter_type,
                        components=["value"],
                        frames=[
                            {
                                "frame_idx": 0,
                                "frame_value": 0.0,
                                "description": f"{node_label}:{component_name}",
                                "data": frame_data,
                            }
                        ],
                    )
                    total_elements += len(label_value_map)
                    instance_count += 1

                if instance_count > 0:
                    written.append(
                        {
                            "result_group": response_group,
                            "field_name": parameter_type,
                            "response_node_label": node_label,
                            "component": component_name,
                            "component_idx": component_index,
                            "instance_count": instance_count,
                            "element_count": total_elements,
                        }
                    )

    return written
