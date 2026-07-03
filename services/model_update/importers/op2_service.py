import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import meshio
import numpy as np
from pyNastran.bdf.bdf import BDF
from pyNastran.op2.op2 import read_op2
from pyNastran.op2.op2_geom import read_op2_geom

from BDFParserPyNastran import BDFParser
from src.l3.core.errors import NotFoundError, ValidationError


def _abs_file(path: str, field_name: str) -> str:
    resolved = os.path.abspath(str(path))
    if not os.path.exists(resolved):
        raise NotFoundError(f"{field_name} not found", {field_name: resolved})
    if not os.path.isfile(resolved):
        raise ValidationError(f"{field_name} must be a file", {field_name: resolved})
    return resolved


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _load_bdf_model(bdf_path: str) -> BDF:
    model = BDF(debug=False)
    try:
        model.read_bdf(bdf_path, xref=True)
    except Exception:
        # Modal result import only needs topology/nodes for mapping and can
        # tolerate incomplete load/excitation references in generated decks.
        model = BDF(debug=False)
        model.read_bdf(bdf_path, xref=False)
    return model


def _resolve_modal_identity(
    instance_name: Optional[str],
    part_name: Optional[str],
    bdf_path: Optional[str],
) -> Tuple[str, str]:
    # BDF import currently stores nodes under a synthetic single-instance model.
    # Keep modal imports aligned with that convention unless the caller provides
    # a more specific instance/part identity explicitly.
    if instance_name:
        resolved_instance = str(instance_name)
    elif bdf_path:
        resolved_instance = "BDF_MODEL"
    else:
        resolved_instance = ""

    if part_name:
        resolved_part = str(part_name)
    else:
        resolved_part = resolved_instance
    return resolved_instance, resolved_part


def _resolve_sidecar_metadata_info(metadata_json: Optional[str], bdf_path: Optional[str]) -> Tuple[dict, Optional[str]]:
    candidate = metadata_json
    if not candidate and bdf_path:
        path = Path(str(bdf_path))
        sidecar = path.with_suffix(path.suffix + ".sol200.json")
        if sidecar.exists():
            candidate = str(sidecar)
    if not candidate:
        return {}, None
    resolved = _abs_file(candidate, "metadata_json")
    with open(resolved, "r", encoding="utf-8") as fp:
        return json.load(fp) or {}, resolved


def _load_sidecar_metadata(metadata_json: Optional[str], bdf_path: Optional[str]) -> dict:
    return _resolve_sidecar_metadata_info(metadata_json, bdf_path)[0]


def _normalize_named_metadata_items(
    raw_items: Sequence[Any],
    *,
    name_field: str,
    output_name_field: str,
) -> List[dict]:
    items = list(raw_items or [])
    normalized: List[dict] = []
    for item in items:
        if isinstance(item, dict):
            copied = dict(item)
            name = copied.get(name_field) or copied.get(output_name_field)
            if name:
                copied[output_name_field] = str(name)
                normalized.append(copied)
            continue
        text = str(item or "").strip()
        if text:
            normalized.append({output_name_field: text})
    return normalized


def _select_named_metadata_items(
    available_items: Sequence[dict],
    requested_names: Optional[Sequence[str]],
    *,
    output_name_field: str,
) -> List[dict]:
    available = [dict(item) for item in (available_items or []) if item.get(output_name_field)]
    if not requested_names:
        return available
    by_name = {str(item[output_name_field]): dict(item) for item in available}
    selected: List[dict] = []
    for name in requested_names:
        text = str(name or "").strip()
        if not text:
            continue
        selected.append(dict(by_name.get(text) or {output_name_field: text}))
    return selected


def _read_op2(op2_path: str):
    try:
        return read_op2_geom(op2_path, debug=False)
    except Exception:
        # Some production OP2 files carry enough modal result data for
        # sensitivity/import workflows, but their embedded geometry cannot be
        # fully cross-referenced by pyNastran. Fall back to plain OP2 parsing
        # so modal frequencies/vectors can still be imported.
        return read_op2(op2_path, debug=False)


def _resolve_sensitivity_result_source(*, op2_path: Optional[str], matrix_path: Optional[str]) -> Tuple[str, str]:
    if matrix_path:
        resolved = _abs_file(matrix_path, "matrix_path")
        if Path(resolved).suffix.lower() == ".op2":
            return resolved, "op2"
        return resolved, "matrix_file"
    if op2_path:
        return _abs_file(op2_path, "op2_path"), "op2"
    raise ValidationError(
        "either op2_path or matrix_path is required for sensitivity import",
        {"op2_path": op2_path, "matrix_path": matrix_path},
    )


def _available_subcases(op2) -> List[int]:
    return sorted(int(key) for key in (getattr(op2, "eigenvectors", {}) or {}).keys())


def _resolve_subcases(op2, requested: Optional[int], *, all_subcases: bool = False) -> List[int]:
    subcases = _available_subcases(op2)
    if not subcases:
        raise NotFoundError("modal result blocks not found", {"op2_path": getattr(op2, "filename", None)})
    if requested is None:
        if all_subcases:
            return [int(subcase_id) for subcase_id in subcases]
        return [int(subcases[0])]
    if int(requested) not in subcases:
        raise NotFoundError("modal subcase not found", {"subcase_id": int(requested), "available_subcases": subcases})
    return [int(requested)]


def _resolve_subcase(op2, requested: Optional[int]) -> int:
    return _resolve_subcases(op2, requested, all_subcases=False)[0]


def _extract_mode_frequency(eigen_data, mode_index: int) -> Tuple[Optional[float], Optional[float], List[dict]]:
    warnings: List[dict] = []
    eigenvalue = None
    frequency = None
    if hasattr(eigen_data, "eigns"):
        eigns = getattr(eigen_data, "eigns", None)
        if eigns is not None and len(eigns) > mode_index:
            eigenvalue = _safe_float(eigns[mode_index])
    if hasattr(eigen_data, "mode_cycles"):
        cycles = getattr(eigen_data, "mode_cycles", None)
        if cycles is not None and len(cycles) > mode_index:
            frequency = _safe_float(cycles[mode_index])
    if frequency is None and eigenvalue is not None and eigenvalue > 0.0:
        frequency = float(math.sqrt(eigenvalue) / (2.0 * math.pi))
    if frequency is None:
        warnings.append({
            "code": "MODE_FREQUENCY_MISSING",
            "message": f"mode index {mode_index} frequency missing; no direct frequency or eigenvalue fallback available",
        })
    return frequency, eigenvalue, warnings


def _extract_requested_mode_indices(modes_array: np.ndarray, requested_mode_numbers: Optional[Sequence[int]]) -> List[int]:
    if not requested_mode_numbers:
        return list(range(len(modes_array)))
    indices: List[int] = []
    for mode_no in requested_mode_numbers:
        found = np.where(modes_array == int(mode_no))[0]
        if len(found) == 0:
            raise NotFoundError("mode number not found", {"mode_number": int(mode_no)})
        indices.append(int(found[0]))
    return indices


def _build_modal_modes(
    *,
    op2_path: str,
    bdf_path: Optional[str],
    subcase_id: Optional[int],
    mode_numbers: Optional[Sequence[int]],
    preview_node_limit: Optional[int] = None,
    instance_name: Optional[str] = None,
    part_name: Optional[str] = None,
    all_subcases: bool = False,
) -> Dict[str, Any]:
    resolved_op2 = _abs_file(op2_path, "op2_path")
    resolved_bdf = _abs_file(bdf_path, "bdf_path") if bdf_path else None
    op2 = _read_op2(resolved_op2)
    selected_subcases = _resolve_subcases(op2, subcase_id, all_subcases=all_subcases)

    if resolved_bdf:
        bdf_model = _load_bdf_model(resolved_bdf)
        ordered_node_ids = sorted(int(node_id) for node_id in bdf_model.nodes.keys())
    else:
        ordered_node_ids = None

    subcase_payloads: List[dict] = []
    all_warnings: List[dict] = []
    for chosen_subcase in selected_subcases:
        eigen_data = op2.eigenvectors[chosen_subcase]
        modes_array = np.asarray(getattr(eigen_data, "modes", []), dtype=np.int64)
        if modes_array.size == 0:
            raise NotFoundError("mode numbers not found in OP2", {"op2_path": resolved_op2, "subcase_id": chosen_subcase})

        mode_indices = _extract_requested_mode_indices(modes_array, mode_numbers)
        result_node_ids = np.asarray(eigen_data.node_gridtype[:, 0], dtype=np.int64)
        result_node_index = {int(node_id): idx for idx, node_id in enumerate(result_node_ids.tolist())}
        active_ordered_node_ids = ordered_node_ids or result_node_ids.tolist()

        subcase_payload = {"subcase_id": int(chosen_subcase), "modes": []}
        for mode_index in mode_indices:
            mode_no = int(modes_array[mode_index])
            mode_data = np.asarray(eigen_data.data[mode_index], dtype=np.float64)
            frequency, eigenvalue, warnings = _extract_mode_frequency(eigen_data, mode_index)
            all_warnings.extend(warnings)
            nodes: List[dict] = []
            missing_count = 0
            for node_id in active_ordered_node_ids:
                result_idx = result_node_index.get(int(node_id))
                if result_idx is None:
                    missing_count += 1
                    continue
                values = mode_data[result_idx]
                node_payload = {
                    "node_id": int(node_id),
                    "u1": float(values[0]) if len(values) > 0 else 0.0,
                    "u2": float(values[1]) if len(values) > 1 else 0.0,
                    "u3": float(values[2]) if len(values) > 2 else 0.0,
                    "ur1": float(values[3]) if len(values) > 3 else 0.0,
                    "ur2": float(values[4]) if len(values) > 4 else 0.0,
                    "ur3": float(values[5]) if len(values) > 5 else 0.0,
                }
                if preview_node_limit is not None:
                    if len(nodes) < int(preview_node_limit):
                        nodes.append(node_payload)
                else:
                    nodes.append(node_payload)
            if missing_count:
                all_warnings.append({
                    "code": "MODE_NODE_MAPPING_INCOMPLETE",
                    "message": f"subcase {chosen_subcase} mode {mode_no} has {missing_count} missing nodes after node-id alignment",
                })
            subcase_payload["modes"].append({
                "mode_no": mode_no,
                "frequency": frequency,
                "eigenvalue": eigenvalue,
                "node_count": int(len(active_ordered_node_ids) - missing_count),
                "nodes": nodes,
                "instance_name": instance_name,
                "part_name": part_name,
            })
        subcase_payloads.append(subcase_payload)

    return {
        "workflow": "op2_modal",
        "source": {
            "op2_path": resolved_op2,
            "bdf_path": resolved_bdf,
            "mode": "bdf_plus_op2" if resolved_bdf else "op2_only",
        },
        "subcases": subcase_payloads,
        "warnings": all_warnings,
    }


def preview_op2_modal(
    *,
    op2_path: str,
    bdf_path: Optional[str] = None,
    subcase_id: Optional[int] = None,
    mode_numbers: Optional[Sequence[int]] = None,
    preview_node_limit: int = 5,
) -> dict:
    payload = _build_modal_modes(
        op2_path=op2_path,
        bdf_path=bdf_path,
        subcase_id=subcase_id,
        mode_numbers=mode_numbers,
        preview_node_limit=preview_node_limit,
    )
    for subcase in payload["subcases"]:
        for mode in subcase["modes"]:
            mode["nodes_preview"] = mode.pop("nodes", [])
    payload["workflow"] = "op2_modal_preview"
    return payload


def build_modal_import_payload(
    *,
    op2_path: str,
    bdf_path: Optional[str] = None,
    subcase_id: Optional[int] = None,
    mode_numbers: Optional[Sequence[int]] = None,
    instance_name: Optional[str] = None,
    part_name: Optional[str] = None,
    all_subcases: bool = False,
) -> dict:
    resolved_instance_name, resolved_part_name = _resolve_modal_identity(instance_name, part_name, bdf_path)
    payload = _build_modal_modes(
        op2_path=op2_path,
        bdf_path=bdf_path,
        subcase_id=subcase_id,
        mode_numbers=mode_numbers,
        preview_node_limit=None,
        instance_name=resolved_instance_name,
        part_name=resolved_part_name,
        all_subcases=all_subcases,
    )
    mode_refs: List[Tuple[int, dict]] = []
    for subcase in payload["subcases"]:
        for mode in subcase["modes"]:
            mode_refs.append((int(subcase["subcase_id"]), mode))

    original_mode_nos = [int(mode["mode_no"]) for _, mode in mode_refs]
    renumber_modes = len(original_mode_nos) != len(set(original_mode_nos))
    warnings = list(payload.get("warnings") or [])
    if renumber_modes:
        warnings.append({
            "code": "MODE_NO_RENUMBERED",
            "message": "duplicate mode numbers were found across selected subcases; imported mode_no values were renumbered sequentially",
        })

    modes: List[dict] = []
    for import_mode_no, (source_subcase_id, mode) in enumerate(mode_refs, start=1):
        original_mode_no = int(mode["mode_no"])
        nodes = []
        for node in mode.pop("nodes", []):
            nodes.append({
                "instance_name": resolved_instance_name,
                "part_name": resolved_part_name,
                "fem_node_label": int(node["node_id"]),
                "u1": node["u1"],
                "u2": node["u2"],
                "u3": node["u3"],
                "extra_json": {
                    "node_id": int(node["node_id"]),
                    "ur1": node["ur1"],
                    "ur2": node["ur2"],
                    "ur3": node["ur3"],
                    "subcase_id": int(source_subcase_id),
                    "source_mode_no": original_mode_no,
                },
            })
        modes.append({
            "mode_no": int(import_mode_no) if renumber_modes else original_mode_no,
            "frequency": mode.get("frequency"),
            "nodes": nodes,
        })
    return {
        "workflow": "op2_modal_import_payload",
        "source": payload["source"],
        "modes": modes,
        "warnings": warnings,
    }


def _build_mesh_from_bdf(bdf_path: str) -> Tuple[np.ndarray, List[Tuple[str, np.ndarray]], List[np.ndarray], Dict[int, int], BDF]:
    parser = BDFParser(bdf_path)
    parser.parse()
    coords = np.asarray([node.coord for node in parser.nodes], dtype=np.float64)
    node_id_to_index = {int(node_id): int(index) for index, node_id in enumerate(parser.node_ids)}
    bdf_model = parser.bdf

    cell_blocks: List[Tuple[str, np.ndarray]] = []
    cell_element_ids: List[np.ndarray] = []
    grouped: Dict[str, List[List[int]]] = {}
    grouped_ids: Dict[str, List[int]] = {}
    for eid, element in sorted(bdf_model.elements.items()):
        elem_type = str(element.type or "").upper()
        node_ids = [int(node_id) for node_id in (element.node_ids or [])]
        if elem_type == "CBAR" or elem_type == "CBEAM":
            cell_type = "line"
        elif elem_type == "CTRIA3":
            cell_type = "triangle"
        elif elem_type == "CQUAD4":
            cell_type = "quad"
        elif elem_type == "CTETRA":
            cell_type = "tetra"
        elif elem_type == "CPENTA":
            cell_type = "wedge"
        elif elem_type == "CHEXA":
            cell_type = "hexahedron"
        else:
            continue
        try:
            connectivity = [node_id_to_index[int(node_id)] for node_id in node_ids]
        except KeyError:
            continue
        grouped.setdefault(cell_type, []).append(connectivity)
        grouped_ids.setdefault(cell_type, []).append(int(eid))

    for cell_type, rows in grouped.items():
        cell_blocks.append((cell_type, np.asarray(rows, dtype=np.int32)))
        cell_element_ids.append(np.asarray(grouped_ids[cell_type], dtype=np.int64))
    return coords, cell_blocks, cell_element_ids, node_id_to_index, bdf_model


def export_modal_to_vtu(
    *,
    op2_path: str,
    bdf_path: str,
    output_vtu: str,
    mode_number: int,
    subcase_id: Optional[int] = None,
    displacement_scale: float = 1.0,
) -> dict:
    resolved_bdf = _abs_file(bdf_path, "bdf_path")
    modal_payload = build_modal_import_payload(
        op2_path=op2_path,
        bdf_path=resolved_bdf,
        subcase_id=subcase_id,
        mode_numbers=[int(mode_number)],
    )
    if not modal_payload["modes"]:
        raise NotFoundError("requested modal payload is empty", {"mode_number": int(mode_number)})
    mode = modal_payload["modes"][0]
    coords, cell_blocks, _, node_id_to_index, _ = _build_mesh_from_bdf(resolved_bdf)
    disp = np.zeros((len(coords), 3), dtype=np.float64)
    rot = np.zeros((len(coords), 3), dtype=np.float64)
    magnitude = np.zeros(len(coords), dtype=np.float64)
    for node in mode["nodes"]:
        idx = node_id_to_index.get(int(node["fem_node_label"]))
        if idx is None:
            continue
        disp[idx, 0] = float(node.get("u1") or 0.0)
        disp[idx, 1] = float(node.get("u2") or 0.0)
        disp[idx, 2] = float(node.get("u3") or 0.0)
        extra = node.get("extra_json") or {}
        rot[idx, 0] = float(extra.get("ur1") or 0.0)
        rot[idx, 1] = float(extra.get("ur2") or 0.0)
        rot[idx, 2] = float(extra.get("ur3") or 0.0)
    magnitude[:] = np.linalg.norm(disp, axis=1)
    mesh = meshio.Mesh(
        points=coords + disp * float(displacement_scale),
        cells=cell_blocks,
        point_data={
            "modal_displacement": disp,
            "modal_rotation": rot,
            "modal_magnitude": magnitude,
        },
    )
    resolved_output = os.path.abspath(output_vtu)
    Path(resolved_output).parent.mkdir(parents=True, exist_ok=True)
    meshio.write(resolved_output, mesh)
    return {
        "workflow": "op2_modal_vtu_export",
        "output_vtu": resolved_output,
        "mode_no": int(mode["mode_no"]),
        "frequency": mode.get("frequency"),
        "point_count": int(len(coords)),
        "cell_block_count": len(cell_blocks),
        "warnings": modal_payload.get("warnings") or [],
    }


def _matrix_candidate_labels(owner: Any, attr_name: str, axis_len: int, axis: str) -> Optional[List[str]]:
    label_names = [
        f"{axis}_labels",
        f"{axis}_names",
        f"{axis}s",
        "labels",
        "names",
    ]
    for name in label_names:
        if not hasattr(owner, name):
            continue
        value = getattr(owner, name)
        if isinstance(value, np.ndarray):
            if value.ndim == 1 and len(value) == axis_len:
                return [str(item) for item in value.tolist()]
        elif isinstance(value, (list, tuple)) and len(value) == axis_len:
            return [str(item) for item in value]
    return None


def _collect_matrix_candidates(obj: Any, path: str = "op2", depth: int = 0, seen: Optional[set] = None) -> List[dict]:
    if seen is None:
        seen = set()
    if id(obj) in seen or depth > 4:
        return []
    seen.add(id(obj))
    candidates: List[dict] = []
    if isinstance(obj, np.ndarray):
        if np.issubdtype(obj.dtype, np.number) and obj.ndim == 2 and obj.size > 0:
            candidates.append({
                "path": path,
                "matrix": np.asarray(obj, dtype=np.float64),
                "row_labels": None,
                "column_labels": None,
            })
        return candidates

    iterable_items: Iterable[Tuple[str, Any]]
    if isinstance(obj, dict):
        iterable_items = list(obj.items())
    else:
        data = getattr(obj, "__dict__", None)
        if not data:
            return candidates
        iterable_items = [(key, value) for key, value in data.items() if not key.startswith("_")]

    for key, value in iterable_items:
        if callable(value):
            continue
        child_path = f"{path}.{key}"
        if isinstance(value, np.ndarray) and np.issubdtype(value.dtype, np.number) and value.ndim == 2 and value.size > 0:
            candidates.append({
                "path": child_path,
                "matrix": np.asarray(value, dtype=np.float64),
                "row_labels": _matrix_candidate_labels(obj, key, int(value.shape[0]), "row"),
                "column_labels": _matrix_candidate_labels(obj, key, int(value.shape[1]), "column"),
            })
            continue
        if isinstance(value, (dict, list, tuple)) or hasattr(value, "__dict__"):
            if isinstance(value, (list, tuple)):
                for idx, item in enumerate(value):
                    candidates.extend(_collect_matrix_candidates(item, f"{child_path}[{idx}]", depth + 1, seen))
            else:
                candidates.extend(_collect_matrix_candidates(value, child_path, depth + 1, seen))
    return candidates


def _choose_matrix_candidate(candidates: List[dict], response_names: List[str], parameter_names: List[str]) -> dict:
    if not candidates:
        raise NotFoundError("SENSITIVITY_BLOCK_NOT_FOUND", {"candidate_count": 0})
    if response_names and parameter_names:
        expected = (len(response_names), len(parameter_names))
        for item in candidates:
            matrix = item["matrix"]
            if tuple(matrix.shape) == expected:
                item["transposed"] = False
                return item
            if tuple(matrix.shape) == (expected[1], expected[0]):
                chosen = dict(item)
                chosen["matrix"] = matrix.T
                chosen["transposed"] = True
                return chosen
    priority_tokens = ("dscm2", "dscm", "sensitivity", "sens")
    prioritized = [
        item for item in candidates
        if any(token in str(item.get("path", "")).lower() for token in priority_tokens)
    ]
    pool = prioritized or candidates
    chosen = max(pool, key=lambda item: int(item["matrix"].size))
    chosen = dict(chosen)
    chosen["transposed"] = False
    return chosen


_FLOAT_TOKEN_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+)?")


def _split_csv_like_line(line: str) -> List[str]:
    return [item.strip() for item in str(line).split(",")]


def _looks_like_numeric_csv_line(line: str) -> bool:
    parts = [item.strip() for item in _split_csv_like_line(line) if item.strip()]
    if not parts:
        return False
    try:
        for item in parts:
            float(item.replace("D", "E").replace("d", "e"))
        return True
    except Exception:
        return False


def _parse_formatted_sensitivity_csv(
    result_path: str,
    *,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
    response_rows: Optional[Sequence[dict]] = None,
) -> Optional[dict]:
    resolved = _abs_file(result_path, "matrix_path")
    if Path(resolved).suffix.lower() not in {".csv", ".txt", ".dat"}:
        return None
    try:
        text = Path(resolved).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = Path(resolved).read_text(encoding="latin-1")
    if "Local Sensitivity Results File" not in text:
        return None

    lines = text.splitlines()
    dv_names: List[str] = []
    response_blocks: List[dict] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if line.startswith("DV ID,Label,Current Value,Lower Limit,Upper Limit"):
            idx += 1
            while idx < len(lines):
                row = lines[idx].rstrip()
                if not row.strip():
                    break
                parts = _split_csv_like_line(row)
                if len(parts) >= 2 and parts[0].strip().isdigit():
                    dv_names.append(parts[1].strip())
                    idx += 1
                    continue
                break
        if line.startswith("Design Response ID,"):
            if idx + 1 >= len(lines):
                break
            desc_parts = _split_csv_like_line(lines[idx + 1])
            if len(desc_parts) < 8:
                idx += 1
                continue
            label = desc_parts[1].strip()
            response_type = desc_parts[2].strip().upper()
            response_labels: List[str] = []
            response_values: List[float] = []
            next_idx = idx + 2
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            if next_idx < len(lines):
                labels_line = lines[next_idx].rstrip()
                values_idx = next_idx + 1
                while values_idx < len(lines) and not lines[values_idx].strip():
                    values_idx += 1
                value_line = lines[values_idx].rstrip() if values_idx < len(lines) else ""
                if (
                    labels_line
                    and not labels_line.strip().startswith("Design Response ID,")
                    and value_line
                    and not value_line.strip().startswith("Design Response ID,")
                    and _looks_like_numeric_csv_line(value_line)
                ):
                    response_labels = [item.strip() for item in _split_csv_like_line(labels_line) if item.strip()]
                    response_values = [
                        float(item.replace("D", "E").replace("d", "e"))
                        for item in _split_csv_like_line(value_line)
                        if item.strip()
                    ]
            response_blocks.append({
                "response_id": int(desc_parts[0]) if str(desc_parts[0]).strip().isdigit() else None,
                "label": label,
                "response_type": response_type,
                "reference_id": int(desc_parts[3]) if str(desc_parts[3]).strip().isdigit() else None,
                "parameter_labels": response_labels,
                "values": response_values,
            })
            idx = next_idx + 1 if response_values else idx + 2
            continue
        idx += 1

    supported_blocks = [
        item for item in response_blocks
        if item.get("response_type") in {"EIGN", "FREQ", "DISP"} and item.get("values")
    ]
    if not supported_blocks:
        return None

    available_parameter_names = list(parameter_names or dv_names or supported_blocks[0].get("parameter_labels") or [])
    if not available_parameter_names:
        available_parameter_names = [f"PARAM_{ii + 1}" for ii in range(len(supported_blocks[0]["values"]))]
    requested_response_names = list(response_names or [])
    if requested_response_names:
        ordered_blocks = []
        block_by_name = {str(item.get("label")): item for item in supported_blocks}
        requested_rows = list(response_rows or [])
        type_order_counters: Dict[str, int] = {}
        for idx, name in enumerate(requested_response_names):
            block = block_by_name.get(str(name))
            response_row = dict(requested_rows[idx]) if idx < len(requested_rows) and isinstance(requested_rows[idx], dict) else {}
            requested_type = str(response_row.get("response_type") or response_row.get("type") or "").strip().upper()
            if requested_type in {"FREQ", "MODAL_FREQUENCY"}:
                csv_types = {"EIGN", "FREQ"}
                requested_type_key = "FREQ"
            elif requested_type in {"DISP", "MODAL_DISPLACEMENT", "NODAL_DISPLACEMENT"}:
                csv_types = {"DISP"}
                requested_type_key = "DISP"
            else:
                csv_types = {"EIGN", "FREQ", "DISP"}
                requested_type_key = "ANY"
            if block is not None and block.get("response_type") not in csv_types:
                block = None
            if block is None and response_row.get("mode_number") is not None and csv_types & {"EIGN", "FREQ"}:
                mode_number = int(response_row["mode_number"])
                mode_matches = [
                    item for item in supported_blocks
                    if item.get("response_type") in {"EIGN", "FREQ"} and item.get("reference_id") == mode_number
                ]
                if len(mode_matches) == 1:
                    block = mode_matches[0]
            if block is None:
                matched_type_blocks = [
                    item for item in supported_blocks
                    if item.get("response_type") in csv_types
                ]
                ordinal = int(type_order_counters.get(requested_type_key, 0))
                if ordinal < len(matched_type_blocks):
                    block = matched_type_blocks[ordinal]
            if block is None:
                raise NotFoundError(
                    "requested sensitivity response not found in formatted CSV",
                    {
                        "response_name": str(name),
                        "mode_number": response_row.get("mode_number"),
                        "available_response_names": [item.get("label") for item in supported_blocks],
                        "available_reference_ids": [item.get("reference_id") for item in supported_blocks],
                        "available_response_types": [item.get("response_type") for item in supported_blocks],
                    },
                )
            ordered_blocks.append(block)
            type_order_counters[requested_type_key] = int(type_order_counters.get(requested_type_key, 0)) + 1
    else:
        ordered_blocks = supported_blocks

    matrix_rows: List[List[float]] = []
    for block in ordered_blocks:
        labels = list(block.get("parameter_labels") or available_parameter_names)
        values = list(block.get("values") or [])
        if len(labels) != len(values):
            raise ValidationError(
                "formatted sensitivity csv label/value length mismatch",
                {
                    "response_name": block.get("label"),
                    "label_count": len(labels),
                    "value_count": len(values),
                    "matrix_path": resolved,
                },
            )
        label_tokens = [str(label) for label in labels]
        label_has_duplicates = len(set(label_tokens)) != len(label_tokens)
        value_map = {label: float(value) for label, value in zip(label_tokens, values)}
        can_match_by_name = (
            not label_has_duplicates
            and all(str(param_name) in value_map for param_name in available_parameter_names)
        )
        if can_match_by_name:
            row = [float(value_map[str(param_name)]) for param_name in available_parameter_names]
        elif len(values) == len(available_parameter_names):
            # MSC Nastran commonly truncates DESVAR labels to 8 chars in the
            # formatted sensitivity CSV. When that happens, multiple design
            # variables can collapse to the same visible token (for example
            # `T@PROPERTY_181362` -> `T@PROPER`). In that case name-based lookup
            # becomes impossible, so preserve the original DESVAR order instead.
            row = [float(value) for value in values]
        else:
            missing_name = next(
                (str(param_name) for param_name in available_parameter_names if str(param_name) not in value_map),
                str(available_parameter_names[0]) if available_parameter_names else None,
            )
            raise NotFoundError(
                "requested parameter not found in formatted sensitivity csv",
                {
                    "parameter_name": missing_name,
                    "available_parameter_names": labels,
                    "requested_parameter_names": [str(item) for item in available_parameter_names],
                },
            )
        matrix_rows.append(row)

    return {
        "path": "formatted_csv",
        "matrix": np.asarray(matrix_rows, dtype=np.float64),
        "row_labels": [str(item.get("label")) for item in ordered_blocks],
        "column_labels": [str(item) for item in available_parameter_names],
        "response_types": [str(item.get("response_type")) for item in ordered_blocks],
    }


def _parse_text_matrix_file(result_path: str, expected_shape: Optional[Tuple[int, int]] = None) -> dict:
    resolved = _abs_file(result_path, "matrix_path")
    try:
        text = Path(resolved).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = Path(resolved).read_text(encoding="latin-1")

    blocks: List[np.ndarray] = []
    current_rows: List[List[float]] = []
    current_width: Optional[int] = None
    flat_values: List[float] = []

    for raw_line in text.splitlines():
        normalized = raw_line.replace("D", "E").replace("d", "e")
        tokens = _FLOAT_TOKEN_RE.findall(normalized)
        if not tokens:
            if current_rows:
                blocks.append(np.asarray(current_rows, dtype=np.float64))
                current_rows = []
                current_width = None
            continue
        row = [float(token.replace("D", "E").replace("d", "e")) for token in tokens]
        flat_values.extend(row)
        if current_width is None or len(row) == current_width:
            current_rows.append(row)
            current_width = len(row)
            continue
        if current_rows:
            blocks.append(np.asarray(current_rows, dtype=np.float64))
        current_rows = [row]
        current_width = len(row)

    if current_rows:
        blocks.append(np.asarray(current_rows, dtype=np.float64))

    candidates = [block for block in blocks if block.ndim == 2 and block.size > 0]
    if expected_shape and flat_values:
        rows, cols = expected_shape
        if rows > 0 and cols > 0 and len(flat_values) == rows * cols:
            candidates.insert(0, np.asarray(flat_values, dtype=np.float64).reshape(rows, cols))

    if not candidates:
        raise NotFoundError(
            "SENSITIVITY_MATRIX_TEXT_NOT_FOUND",
            {"matrix_path": resolved, "expected_shape": expected_shape},
        )

    chosen = None
    if expected_shape:
        rows, cols = expected_shape
        for candidate in candidates:
            if tuple(candidate.shape) == (rows, cols):
                chosen = candidate
                break
            if tuple(candidate.shape) == (cols, rows):
                chosen = candidate.T
                break
    if chosen is None:
        chosen = max(candidates, key=lambda item: int(item.size))

    return {
        "path": "matrix_file",
        "matrix": np.asarray(chosen, dtype=np.float64),
        "row_labels": None,
        "column_labels": None,
    }


def preview_op2_sensitivity(
    *,
    project_id: Optional[int] = None,
    batch_no: str = "1",
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
) -> dict:
    result_path, source_kind = _resolve_sensitivity_result_source(op2_path=op2_path, matrix_path=matrix_path)
    resolved_bdf = _abs_file(bdf_path, "bdf_path") if bdf_path else None
    metadata, resolved_metadata_path = _resolve_sidecar_metadata_info(metadata_json, resolved_bdf)

    if (not metadata) and project_id is not None:
        try:
            from services.model_update.analysis import sensitivity_service as _sens

            stored = _sens._load_stored_sensitivity_run(project_id=int(project_id), batch_no=str(batch_no))
        except Exception:
            stored = None
        if stored:
            metadata = {
                "parameters": [
                    {"name": item.get("param_name") or item.get("parameter_name"), **dict(item)}
                    for item in (stored.get("parameter_columns") or [])
                ],
                "responses": [
                    {"name": item.get("response_name"), **dict(item)}
                    for item in (stored.get("response_rows") or [])
                ],
            }

    available_parameter_columns = _normalize_named_metadata_items(
        metadata.get("parameters") or [],
        name_field="name",
        output_name_field="parameter_name",
    )
    available_response_rows = _normalize_named_metadata_items(
        metadata.get("responses") or [],
        name_field="name",
        output_name_field="response_name",
    )
    selected_parameter_columns = _select_named_metadata_items(
        available_parameter_columns,
        parameter_names,
        output_name_field="parameter_name",
    )
    selected_response_rows = _select_named_metadata_items(
        available_response_rows,
        response_names,
        output_name_field="response_name",
    )
    parameter_names = [str(item["parameter_name"]) for item in selected_parameter_columns]
    response_names = [str(item["response_name"]) for item in selected_response_rows]

    expected_shape = None
    if response_names and parameter_names:
        expected_shape = (len(response_names), len(parameter_names))

    warnings: List[dict] = []
    if source_kind == "op2":
        op2 = _read_op2(result_path)
        candidates = _collect_matrix_candidates(op2)
        chosen = _choose_matrix_candidate(candidates, response_names, parameter_names)
    else:
        chosen = _parse_formatted_sensitivity_csv(
            result_path,
            parameter_names=parameter_names,
            response_names=response_names,
            response_rows=selected_response_rows,
        )
        if chosen is not None:
            warnings.append({
                "code": "SENSITIVITY_FORMATTED_CSV_PARSED",
                "message": "formatted Nastran sensitivity csv was parsed successfully; supported response blocks include EIGN/FREQ and DISP",
            })
        else:
            chosen = _parse_text_matrix_file(result_path, expected_shape=expected_shape)
            warnings.append({
                "code": "SENSITIVITY_TEXT_MATRIX_HEURISTIC",
                "message": "matrix file was parsed heuristically from plain numeric text; verify row/column order against the solver output",
            })
    matrix = np.asarray(chosen["matrix"], dtype=np.float64)
    if not response_names:
        response_names = chosen.get("row_labels") or [f"RESP_{idx + 1}" for idx in range(matrix.shape[0])]
        selected_response_rows = [{"response_name": str(name)} for name in response_names]
        warnings.append({
            "code": "SENSITIVITY_LABELS_INCOMPLETE",
            "message": "response names were inferred heuristically",
        })
    else:
        selected_response_rows = _select_named_metadata_items(
            available_response_rows,
            response_names,
            output_name_field="response_name",
        )
    if not parameter_names:
        parameter_names = chosen.get("column_labels") or [f"PARAM_{idx + 1}" for idx in range(matrix.shape[1])]
        selected_parameter_columns = [{"parameter_name": str(name)} for name in parameter_names]
        warnings.append({
            "code": "SENSITIVITY_LABELS_INCOMPLETE",
            "message": "parameter names were inferred heuristically",
        })
    else:
        selected_parameter_columns = _select_named_metadata_items(
            available_parameter_columns,
            parameter_names,
            output_name_field="parameter_name",
        )

    value_mode = "unknown"
    warnings.append({
        "code": "SENSITIVITY_VALUE_MODE_UNKNOWN",
        "message": "Cannot confirm whether the extracted values are raw or normalized",
    })
    return {
        "workflow": "op2_sensitivity_preview",
        "source": {
            "op2_path": result_path if source_kind == "op2" else None,
            "matrix_path": result_path if source_kind != "op2" else None,
            "bdf_path": resolved_bdf,
            "metadata_path": resolved_metadata_path,
            "source_kind": source_kind,
            "candidate_path": chosen.get("path"),
        },
        "matrix_kind": "raw_sensitivity",
        "value_mode": value_mode,
        "row_labels": list(response_names),
        "column_labels": list(parameter_names),
        "response_rows": selected_response_rows,
        "parameter_columns": selected_parameter_columns,
        "matrix_preview": matrix.tolist(),
        "warnings": warnings,
    }


def store_op2_sensitivity(
    *,
    project_id: int,
    batch_no: str,
    case_name: str,
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
) -> dict:
    preview = preview_op2_sensitivity(
        project_id=int(project_id),
        batch_no=str(batch_no),
        op2_path=op2_path,
        matrix_path=matrix_path,
        bdf_path=bdf_path,
        metadata_json=metadata_json,
        parameter_names=parameter_names,
        response_names=response_names,
    )
    from services.model_update.analysis import sensitivity_service as _sens

    matrix_payload = {
        "response_rows": [dict(item) for item in (preview.get("response_rows") or [])],
        "parameter_columns": [dict(item) for item in (preview.get("parameter_columns") or [])],
        "matrix": preview["matrix_preview"],
        "source": dict(preview.get("source") or {}),
    }
    stored = _sens._persist_sensitivity_matrix(
        project_id=int(project_id),
        batch_no=str(batch_no),
        case_name=str(case_name),
        matrix_payload=matrix_payload,
    )
    return {
        "workflow": "op2_sensitivity_store",
        **stored,
        "warnings": preview.get("warnings") or [],
    }


def _resolve_cloud_target_instances(workspace: str, column_meta: dict) -> List[str]:
    from src.l3.infra.manifest_repo import ManifestRepo

    instance_name = str(column_meta.get("instance_name") or "").strip()
    if instance_name:
        return [instance_name]

    manifest_path = os.path.join(os.path.abspath(workspace), "manifest.db")
    # Nastran-only modal/BDF projects can legitimately skip the L3 workspace
    # packaging step, so manifest.db may not exist. In that case we still want
    # Bayesian history artifacts to record a usable single-instance mapping.
    if not os.path.exists(manifest_path):
        return ["BDF_MODEL"]

    repo = ManifestRepo(workspace)
    part_name = str(column_meta.get("part_name") or "").strip()
    if part_name:
        matched = [str(item) for item in (repo.get_instances_by_part_name(part_name) or []) if str(item).strip()]
        if matched:
            return matched

    rows = [dict(item) for item in (repo.list_instances() or [])]
    if len(rows) == 1:
        only_name = str(rows[0].get("instance_name") or "").strip()
        if only_name:
            return [only_name]

    raise ValidationError(
        "cannot resolve target instance for Nastran sensitivity cloud export",
        {
            "workspace": os.path.abspath(workspace),
            "parameter_name": column_meta.get("parameter_name"),
            "instance_name": column_meta.get("instance_name"),
            "part_name": column_meta.get("part_name"),
            "available_instances": [str(item.get("instance_name") or "") for item in rows],
        },
    )


def _build_op2_parameter_columns_with_mappings(
    *,
    workspace: str,
    bdf_path: str,
    parameter_columns: Sequence[dict],
) -> List[dict]:
    model = _load_bdf_model(_abs_file(bdf_path, "bdf_path"))
    all_element_ids = sorted(int(eid) for eid in model.elements.keys())
    resolved_columns: List[dict] = []

    for raw_column in parameter_columns:
        column_meta = dict(raw_column or {})
        parameter_name = str(column_meta.get("parameter_name") or column_meta.get("param_name") or "").strip()
        parameter_type = str(column_meta.get("param_type") or column_meta.get("type") or "").strip().upper()
        if not parameter_name:
            raise ValidationError("parameter_name is required for cloud export", {"column": column_meta})

        targets: List[int] = []
        element_id = column_meta.get("element_id")
        if element_id is not None:
            targets = [int(element_id)]
        elif parameter_type == "H":
            property_id = column_meta.get("property_id")
            if property_id is None:
                raise ValidationError(
                    "H parameter requires property_id for cloud export",
                    {"parameter_name": parameter_name, "column": column_meta},
                )
            target_pid = int(property_id)
            for eid in all_element_ids:
                element = model.elements.get(int(eid))
                if element is None:
                    continue
                if _element_property_id(element) == target_pid:
                    targets.append(int(eid))
        elif parameter_type in {"E", "RHO"}:
            material_id = column_meta.get("material_id")
            if material_id is None:
                raise ValidationError(
                    f"{parameter_type} parameter requires material_id for cloud export",
                    {"parameter_name": parameter_name, "column": column_meta},
                )
            target_mid = int(material_id)
            for eid in all_element_ids:
                element = model.elements.get(int(eid))
                if element is None:
                    continue
                prop = getattr(element, "pid_ref", None)
                if prop is None:
                    pid = _element_property_id(element)
                    prop = model.properties.get(pid) if pid is not None else None
                if prop is not None and target_mid in _property_material_ids(prop):
                    targets.append(int(eid))
        else:
            raise ValidationError(
                "unsupported Nastran sensitivity parameter type for cloud export",
                {
                    "parameter_name": parameter_name,
                    "parameter_type": parameter_type,
                    "allowed": ["E", "RHO", "H"],
                },
            )

        if not targets:
            raise ValidationError(
                "no target elements were resolved for Nastran sensitivity parameter",
                {
                    "parameter_name": parameter_name,
                    "parameter_type": parameter_type,
                    "column": column_meta,
                },
            )

        targets_by_scope = {
            instance_name: list(sorted({int(label) for label in targets}))
            for instance_name in _resolve_cloud_target_instances(workspace, column_meta)
        }
        enriched = dict(column_meta)
        enriched["parameter_name"] = parameter_name
        enriched["element_mapping"] = {
            "target_kind": "cell",
            "targets_by_scope": targets_by_scope,
        }
        resolved_columns.append(enriched)
    return resolved_columns


def _write_element_cloud_result_to_workspace(
    *,
    workspace: str,
    batch_no: str,
    matrix_payload: dict,
    result_group: Optional[str] = None,
    step_name: str = "Sensitivity",
    field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    from services.model_update.analysis import sensitivity_service as _sens
    from src.l3.services.external_result_writer import ExternalResultWriter

    request_payloads = _sens._build_sensitivity_cloud_requests(
        batch_no=str(batch_no),
        matrix_payload=matrix_payload,
        result_group=result_group,
        step_name=step_name,
    )
    written_results = []
    for payload in request_payloads:
        request_body = dict(payload.get("request_body") or {})
        metadata = dict(payload.get("metadata") or {})
        writer = ExternalResultWriter(os.path.abspath(workspace), metadata["result_group"])
        total_frames = 0
        for instance_entry in list(request_body.get("instances") or []):
            total_frames = max(
                total_frames,
                writer.write_element(
                    instance=str(instance_entry["instance"]),
                    step=str(request_body["step_name"]),
                    field=str(request_body["field_name"]),
                    components=list(request_body.get("components") or []),
                    frames=list(instance_entry.get("frames") or []),
                ),
            )

        item = dict(metadata)
        item.update(
            {
                "workspace": os.path.abspath(workspace),
                "write_response": {
                    "field_name": request_body["field_name"],
                    "step_name": request_body["step_name"],
                    "instances_written": len(list(request_body.get("instances") or [])),
                    "frames_written": int(total_frames),
                    "source": "external",
                },
                "query_hint": {
                    "result_group": metadata["result_group"],
                    "step": metadata["step"],
                    "field": metadata["field"],
                    "frame": 0,
                    "component_idx": 0,
                },
            }
        )
        written_results.append(item)

    first = dict(written_results[0])
    first["written_results"] = written_results
    first["result_groups"] = sorted({str(item["result_group"]) for item in written_results})
    first["fields"] = sorted({str(item["field"]) for item in written_results})
    first["field_name"] = first.get("field")
    return first


def _register_external_sensitivity_result_group(
    *,
    project_id: int,
    preview_source: dict,
    cloud_result: dict,
) -> None:
    from src.l3.core.config import settings
    from src.l3.infra.registry_repo import RegistryRepo

    result_group = str(cloud_result.get("result_group") or "").strip()
    if not result_group:
        return

    source_path = str(
        preview_source.get("op2_path")
        or preview_source.get("matrix_path")
        or preview_source.get("bdf_path")
        or ""
    ).strip()
    source_file = os.path.basename(source_path) if source_path else None

    RegistryRepo(settings.registry_db_path).adopt_default_result_group(
        str(int(project_id)),
        result_group,
        result_group,
        source_path,
        source_file,
    )


def store_op2_sensitivity_cloud(
    *,
    project_id: int,
    batch_no: str,
    case_name: str,
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
    cloud_result_group: Optional[str] = None,
    cloud_step_name: str = "Sensitivity",
    cloud_field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    preview = preview_op2_sensitivity(
        project_id=int(project_id),
        batch_no=str(batch_no),
        op2_path=op2_path,
        matrix_path=matrix_path,
        bdf_path=bdf_path,
        metadata_json=metadata_json,
        parameter_names=parameter_names,
        response_names=response_names,
    )
    from services.model_update.analysis import sensitivity_service as _sens
    from services.model_update.analysis.project_path_service import resolve_project_workspace

    matrix_payload = {
        "response_rows": [dict(item) for item in (preview.get("response_rows") or [])],
        "parameter_columns": [dict(item) for item in (preview.get("parameter_columns") or [])],
        "matrix": preview["matrix_preview"],
        "source": dict(preview.get("source") or {}),
    }
    stored = _sens._persist_sensitivity_matrix(
        project_id=int(project_id),
        batch_no=str(batch_no),
        case_name=str(case_name),
        matrix_payload=matrix_payload,
    )

    workspace = _sens._workspace_path(resolve_project_workspace(int(project_id)))
    resolved_bdf_path = str(preview.get("source", {}).get("bdf_path") or bdf_path or "").strip()
    if not resolved_bdf_path:
        raise ValidationError(
            "bdf_path is required for Nastran sensitivity cloud export",
            {"project_id": int(project_id), "batch_no": str(batch_no)},
        )

    matrix_payload["workspace"] = workspace
    matrix_payload["parameter_columns"] = _build_op2_parameter_columns_with_mappings(
        workspace=workspace,
        bdf_path=resolved_bdf_path,
        parameter_columns=matrix_payload["parameter_columns"],
    )
    cloud_result = _write_element_cloud_result_to_workspace(
        workspace=workspace,
        batch_no=str(batch_no),
        matrix_payload=matrix_payload,
        result_group=cloud_result_group,
        step_name=cloud_step_name,
        field_name=cloud_field_name,
    )
    _register_external_sensitivity_result_group(
        project_id=int(project_id),
        preview_source=dict(preview.get("source") or {}),
        cloud_result=cloud_result,
    )
    return {
        "workflow": "op2_sensitivity_store_cloud",
        **stored,
        "workspace": workspace,
        "cloud_result": cloud_result,
        "warnings": preview.get("warnings") or [],
    }


def _element_property_id(element: Any) -> Optional[int]:
    try:
        pid = element.Pid()
        return int(pid) if pid is not None else None
    except Exception:
        pid = getattr(element, "pid", None)
        return int(pid) if pid is not None else None


def _property_material_ids(prop: Any) -> List[int]:
    material_ids: List[int] = []
    for name in ("mid", "mid1", "mid2", "mid3", "mid4"):
        value = getattr(prop, name, None)
        if hasattr(value, "mid"):
            value = value.mid
        if value in (None, ""):
            continue
        try:
            material_id = int(value)
        except Exception:
            continue
        if material_id not in material_ids:
            material_ids.append(material_id)
    return material_ids


def _element_material_id(element: Any, bdf_model: BDF) -> Optional[int]:
    try:
        prop = getattr(element, "pid_ref", None)
        if prop is None:
            pid = _element_property_id(element)
            if pid is None:
                return None
            prop = bdf_model.properties.get(pid)
        material_ids = _property_material_ids(prop)
        if material_ids:
            return int(material_ids[0])
    except Exception:
        return None
    return None


def export_sensitivity_to_vtu(
    *,
    project_id: int,
    batch_no: str,
    input_bdf: str,
    output_vtu: str,
    response_name: str,
    metadata_json: Optional[str] = None,
) -> dict:
    from services.model_update.analysis import sensitivity_service as _sens

    stored = _sens._load_stored_sensitivity_run(project_id=int(project_id), batch_no=str(batch_no))
    row_names = [str(name) for name in stored.get("row_names") or []]
    col_names = [str(name) for name in stored.get("col_names") or []]
    matrix = np.asarray(stored.get("matrix") or [], dtype=np.float64)
    try:
        row_index = row_names.index(str(response_name))
    except ValueError as exc:
        raise NotFoundError("response name not found in stored sensitivity matrix", {
            "response_name": str(response_name),
            "available_response_names": row_names,
        }) from exc

    stored_parameter_columns = [dict(item) for item in (stored.get("parameter_columns") or [])]
    parameter_by_name = {
        str(item.get("param_name") or item.get("parameter_name") or ""): dict(item)
        for item in stored_parameter_columns
        if str(item.get("param_name") or item.get("parameter_name") or "").strip()
    }
    if not parameter_by_name:
        metadata = _load_sidecar_metadata(metadata_json, input_bdf)
        parameters = list(metadata.get("parameters") or [])
        parameter_by_name = {
            str(item.get("name")): dict(item)
            for item in parameters
            if isinstance(item, dict) and item.get("name")
        }
    if not parameter_by_name:
        raise ValidationError(
            "stored sensitivity parameter metadata is required for VTU export",
            {"project_id": int(project_id), "batch_no": str(batch_no), "metadata_json": metadata_json},
        )

    coords, cell_blocks, cell_element_ids, _, bdf_model = _build_mesh_from_bdf(_abs_file(input_bdf, "input_bdf"))
    cell_data_blocks: List[np.ndarray] = []
    for block_ids in cell_element_ids:
        values = np.full(len(block_ids), np.nan, dtype=np.float64)
        for cell_idx, element_id in enumerate(block_ids.tolist()):
            element = bdf_model.elements.get(int(element_id))
            if element is None:
                continue
            pid = _element_property_id(element)
            prop = getattr(element, "pid_ref", None)
            if prop is None and pid is not None:
                prop = bdf_model.properties.get(pid)
            mids = _property_material_ids(prop) if prop is not None else []
            candidates: List[float] = []
            for col_idx, param_name in enumerate(col_names):
                meta = parameter_by_name.get(str(param_name))
                if not meta:
                    continue
                ptype = str(meta.get("param_type") or meta.get("type") or "").upper()
                if meta.get("element_id") is not None and int(meta["element_id"]) == int(element_id):
                    candidates.append(float(matrix[row_index, col_idx]))
                elif ptype == "H" and meta.get("property_id") is not None and pid == int(meta["property_id"]):
                    candidates.append(float(matrix[row_index, col_idx]))
                elif ptype in {"E", "RHO"} and meta.get("material_id") is not None and int(meta["material_id"]) in mids:
                    candidates.append(float(matrix[row_index, col_idx]))
            if candidates:
                values[cell_idx] = float(max(candidates, key=lambda item: abs(item)))
        cell_data_blocks.append(values)

    mesh = meshio.Mesh(
        points=coords,
        cells=cell_blocks,
        cell_data={"sensitivity": cell_data_blocks},
    )
    resolved_output = os.path.abspath(output_vtu)
    Path(resolved_output).parent.mkdir(parents=True, exist_ok=True)
    meshio.write(resolved_output, mesh)
    return {
        "workflow": "op2_sensitivity_vtu_export",
        "output_vtu": resolved_output,
        "project_id": int(project_id),
        "batch_no": str(batch_no),
        "response_name": str(response_name),
        "cell_block_count": len(cell_blocks),
        "warnings": [],
    }
