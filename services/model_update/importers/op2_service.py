import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import meshio
import numpy as np
from pyNastran.bdf.bdf import BDF
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
    model.read_bdf(bdf_path, xref=True)
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


def _load_sidecar_metadata(metadata_json: Optional[str], bdf_path: Optional[str]) -> dict:
    candidate = metadata_json
    if not candidate and bdf_path:
        path = Path(str(bdf_path))
        sidecar = path.with_suffix(path.suffix + ".sol200.json")
        if sidecar.exists():
            candidate = str(sidecar)
    if not candidate:
        return {}
    resolved = _abs_file(candidate, "metadata_json")
    with open(resolved, "r", encoding="utf-8") as fp:
        return json.load(fp) or {}


def _read_op2(op2_path: str):
    return read_op2_geom(op2_path, debug=False)


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


def _resolve_subcase(op2, requested: Optional[int]) -> int:
    subcases = _available_subcases(op2)
    if not subcases:
        raise NotFoundError("modal result blocks not found", {"op2_path": getattr(op2, "filename", None)})
    if requested is None:
        return int(subcases[0])
    if int(requested) not in subcases:
        raise NotFoundError("modal subcase not found", {"subcase_id": int(requested), "available_subcases": subcases})
    return int(requested)


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
) -> Dict[str, Any]:
    resolved_op2 = _abs_file(op2_path, "op2_path")
    resolved_bdf = _abs_file(bdf_path, "bdf_path") if bdf_path else None
    op2 = _read_op2(resolved_op2)
    chosen_subcase = _resolve_subcase(op2, subcase_id)
    eigen_data = op2.eigenvectors[chosen_subcase]
    modes_array = np.asarray(getattr(eigen_data, "modes", []), dtype=np.int64)
    if modes_array.size == 0:
        raise NotFoundError("mode numbers not found in OP2", {"op2_path": resolved_op2, "subcase_id": chosen_subcase})

    mode_indices = _extract_requested_mode_indices(modes_array, mode_numbers)
    result_node_ids = np.asarray(eigen_data.node_gridtype[:, 0], dtype=np.int64)
    result_node_index = {int(node_id): idx for idx, node_id in enumerate(result_node_ids.tolist())}

    if resolved_bdf:
        bdf_model = _load_bdf_model(resolved_bdf)
        ordered_node_ids = sorted(int(node_id) for node_id in bdf_model.nodes.keys())
    else:
        bdf_model = None
        ordered_node_ids = result_node_ids.tolist()

    subcase_payload = {"subcase_id": int(chosen_subcase), "modes": []}
    all_warnings: List[dict] = []
    for mode_index in mode_indices:
        mode_no = int(modes_array[mode_index])
        mode_data = np.asarray(eigen_data.data[mode_index], dtype=np.float64)
        frequency, eigenvalue, warnings = _extract_mode_frequency(eigen_data, mode_index)
        all_warnings.extend(warnings)
        nodes: List[dict] = []
        missing_count = 0
        for node_id in ordered_node_ids:
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
                "message": f"mode {mode_no} has {missing_count} missing nodes after node-id alignment",
            })
        subcase_payload["modes"].append({
            "mode_no": mode_no,
            "frequency": frequency,
            "eigenvalue": eigenvalue,
            "node_count": int(len(ordered_node_ids) - missing_count),
            "nodes": nodes,
            "instance_name": instance_name,
            "part_name": part_name,
        })

    return {
        "workflow": "op2_modal",
        "source": {
            "op2_path": resolved_op2,
            "bdf_path": resolved_bdf,
            "mode": "bdf_plus_op2" if resolved_bdf else "op2_only",
        },
        "subcases": [subcase_payload],
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
    )
    modes: List[dict] = []
    for subcase in payload["subcases"]:
        for mode in subcase["modes"]:
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
                        "subcase_id": int(subcase["subcase_id"]),
                    },
                })
            modes.append({
                "mode_no": int(mode["mode_no"]),
                "frequency": mode.get("frequency"),
                "nodes": nodes,
            })
    return {
        "workflow": "op2_modal_import_payload",
        "source": payload["source"],
        "modes": modes,
        "warnings": payload["warnings"],
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
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
) -> dict:
    result_path, source_kind = _resolve_sensitivity_result_source(op2_path=op2_path, matrix_path=matrix_path)
    resolved_bdf = _abs_file(bdf_path, "bdf_path") if bdf_path else None
    metadata = _load_sidecar_metadata(metadata_json, resolved_bdf)

    raw_parameter_names = list(parameter_names or metadata.get("parameters") or [])
    if raw_parameter_names and isinstance(raw_parameter_names[0], dict):
        parameter_names = [str(item.get("name")) for item in raw_parameter_names if item.get("name")]
    else:
        parameter_names = [str(item) for item in raw_parameter_names]

    raw_response_names = list(response_names or metadata.get("responses") or [])
    if raw_response_names and isinstance(raw_response_names[0], dict):
        response_names = [str(item.get("name")) for item in raw_response_names if item.get("name")]
    else:
        response_names = [str(item) for item in raw_response_names]

    expected_shape = None
    if response_names and parameter_names:
        expected_shape = (len(response_names), len(parameter_names))

    warnings: List[dict] = []
    if source_kind == "op2":
        op2 = _read_op2(result_path)
        candidates = _collect_matrix_candidates(op2)
        chosen = _choose_matrix_candidate(candidates, response_names, parameter_names)
    else:
        chosen = _parse_text_matrix_file(result_path, expected_shape=expected_shape)
        warnings.append({
            "code": "SENSITIVITY_TEXT_MATRIX_HEURISTIC",
            "message": "matrix file was parsed heuristically from plain numeric text; verify row/column order against the solver output",
        })
    matrix = np.asarray(chosen["matrix"], dtype=np.float64)
    if not response_names:
        response_names = chosen.get("row_labels") or [f"RESP_{idx + 1}" for idx in range(matrix.shape[0])]
        warnings.append({
            "code": "SENSITIVITY_LABELS_INCOMPLETE",
            "message": "response names were inferred heuristically",
        })
    if not parameter_names:
        parameter_names = chosen.get("column_labels") or [f"PARAM_{idx + 1}" for idx in range(matrix.shape[1])]
        warnings.append({
            "code": "SENSITIVITY_LABELS_INCOMPLETE",
            "message": "parameter names were inferred heuristically",
        })

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
            "source_kind": source_kind,
            "candidate_path": chosen.get("path"),
        },
        "matrix_kind": "raw_sensitivity",
        "value_mode": value_mode,
        "row_labels": list(response_names),
        "column_labels": list(parameter_names),
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
        op2_path=op2_path,
        matrix_path=matrix_path,
        bdf_path=bdf_path,
        metadata_json=metadata_json,
        parameter_names=parameter_names,
        response_names=response_names,
    )
    from services.model_update.analysis import sensitivity_service as _sens

    matrix_payload = {
        "response_rows": [{"response_name": name} for name in preview["row_labels"]],
        "parameter_columns": [{"parameter_name": name} for name in preview["column_labels"]],
        "matrix": preview["matrix_preview"],
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


def _element_property_id(element: Any) -> Optional[int]:
    try:
        pid = element.Pid()
        return int(pid) if pid is not None else None
    except Exception:
        pid = getattr(element, "pid", None)
        return int(pid) if pid is not None else None


def _element_material_id(element: Any, bdf_model: BDF) -> Optional[int]:
    try:
        prop = getattr(element, "pid_ref", None)
        if prop is None:
            pid = _element_property_id(element)
            if pid is None:
                return None
            prop = bdf_model.properties.get(pid)
        mid = getattr(prop, "mid", None)
        if hasattr(mid, "mid"):
            return int(mid.mid)
        if mid is not None:
            return int(mid)
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

    metadata = _load_sidecar_metadata(metadata_json, input_bdf)
    parameters = list(metadata.get("parameters") or [])
    if not parameters:
        raise ValidationError(
            "SOL200 metadata parameters are required for VTU export",
            {"metadata_json": metadata_json, "input_bdf": input_bdf},
        )
    parameter_by_name = {
        str(item.get("name")): dict(item)
        for item in parameters
        if isinstance(item, dict) and item.get("name")
    }

    coords, cell_blocks, cell_element_ids, _, bdf_model = _build_mesh_from_bdf(_abs_file(input_bdf, "input_bdf"))
    cell_data_blocks: List[np.ndarray] = []
    for block_ids in cell_element_ids:
        values = np.full(len(block_ids), np.nan, dtype=np.float64)
        for cell_idx, element_id in enumerate(block_ids.tolist()):
            element = bdf_model.elements.get(int(element_id))
            if element is None:
                continue
            pid = _element_property_id(element)
            mid = _element_material_id(element, bdf_model)
            candidates: List[float] = []
            for col_idx, param_name in enumerate(col_names):
                meta = parameter_by_name.get(str(param_name))
                if not meta:
                    continue
                ptype = str(meta.get("type") or "").upper()
                if ptype == "H" and meta.get("property_id") is not None and pid == int(meta["property_id"]):
                    candidates.append(float(matrix[row_index, col_idx]))
                elif ptype in {"E", "RHO"} and meta.get("material_id") is not None and mid == int(meta["material_id"]):
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
