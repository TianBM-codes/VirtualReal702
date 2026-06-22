import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from pyNastran.bdf.bdf import BDF

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.model_update.importers.op2_service import store_op2_sensitivity_cloud


RUN_CONFIG = {
    "nastran_command": r"C:/MSC.Software/MSC_Nastran/20180/bin/nastran.exe",
    "input_bdf": r"D:/WorkSpace/OtherProjects/VirtualReal702/run/fem15_py.bdf",
    "output_bdf": r"D:/WorkSpace/OtherProjects/VirtualReal702/run/fem15_all_elem_e_sol200.bdf",
    "project_id": 3,
    "batch_no": "sol200_all_elements_e",
    "case_name": "nastran_sol200_phase1",
    "parameter_preset": {
        "preset": "all_elements_e",
        "lower_scale": 0.01,
        "upper_scale": 1000000.0,
    },
    "responses": [
        {"name": "FREQ1", "type": "FREQ", "mode_number": 1},
        {"name": "FREQ2", "type": "FREQ", "mode_number": 2},
    ],
    "settings": {
        "sol200.deck_mode": "include",
        "dynamic.fmin": 100.0,
        "dynamic.fmax": 1000.0,
        "dynamic.vectors": 20,
        "dynamic.norm": "MASS",
        "result.target": "OP2",
        "sol200.sensitivity_csv": True,
    },
    "run_solver": True,
    "timeout_sec": 1800,
    "extra_args": [],
    "cloud_result_group": "sol200_all_elements_e",
    "cloud_step_name": "Sensitivity",
    "cloud_field_name": "SENSITIVITY_CLOUD",
}


def run_sol200_frequency_sensitivity(config: Dict[str, Any]) -> Dict[str, Any]:
    input_bdf = _abs_file(config["input_bdf"], "input_bdf")
    output_bdf = str(Path(config["output_bdf"]).expanduser().resolve())
    settings = dict(config.get("settings") or {})
    responses = [dict(item) for item in (config.get("responses") or [])]
    preset = dict(config.get("parameter_preset") or {})
    run_solver = bool(config.get("run_solver", True))
    timeout_sec = config.get("timeout_sec")
    nastran_command = str(config.get("nastran_command") or os.environ.get("NASTRAN_COMMAND") or "").strip()
    extra_args = [str(item) for item in (config.get("extra_args") or []) if str(item).strip()]

    if not responses:
        raise RuntimeError("responses are required")
    if str(preset.get("preset") or "").strip().lower() != "all_elements_e":
        raise RuntimeError("only parameter_preset.preset='all_elements_e' is supported")
    if run_solver and not nastran_command:
        raise RuntimeError("nastran_command is required when run_solver=True")

    for response in responses:
        if str(response.get("type") or "").upper() != "FREQ":
            raise RuntimeError("only FREQ responses are supported")
        if response.get("mode_number") is None:
            raise RuntimeError(f"mode_number is required for response {response.get('name')!r}")

    localized_input_bdf, parameters, preset_info = _localize_all_elements_e(
        input_bdf=input_bdf,
        localized_output=_localized_bdf_path(output_bdf),
        lower_scale=float(preset.get("lower_scale", 0.01)),
        upper_scale=float(preset.get("upper_scale", 1000000.0)),
    )
    generated = _generate_sol200_deck(
        localized_input_bdf=localized_input_bdf,
        source_input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=parameters,
        responses=responses,
        settings=settings,
        preset_info=preset_info,
    )

    solver_payload = None
    if run_solver:
        solver_payload = _run_nastran(
            nastran_command=nastran_command,
            bdf_path=generated["output_bdf"],
            timeout_sec=int(timeout_sec) if timeout_sec is not None else None,
            extra_args=extra_args,
        )
        _materialize_sensitivity_csv(
            generated["generated_files"]["sensitivity_csv_internal"],
            generated["generated_files"]["sensitivity_csv"],
        )

    sensitivity = extract_sensitivity_results(
        matrix_path=generated["generated_files"]["sensitivity_csv"],
        metadata_json=generated["generated_files"]["metadata_json"],
        response_names=[item["name"] for item in responses],
    )
    return {
        "workflow": "sol200_freq_sensitivity",
        "project_id": int(config["project_id"]),
        "batch_no": str(config["batch_no"]),
        "case_name": str(config.get("case_name") or "nastran_sol200_phase1"),
        "input_bdf": input_bdf,
        "localized_input_bdf": localized_input_bdf,
        "output_bdf": generated["output_bdf"],
        "metadata_json": generated["generated_files"]["metadata_json"],
        "sensitivity_csv": generated["generated_files"]["sensitivity_csv"],
        "parameter_count": len(parameters),
        "response_count": len(responses),
        "preset_info": preset_info,
        "solver": solver_payload,
        "sensitivity": sensitivity,
    }


def extract_sensitivity_results(
    matrix_path: str,
    metadata_json: str,
    response_names: Optional[Sequence[str]] = None,
    parameter_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    resolved_matrix = _abs_file(matrix_path, "matrix_path")
    resolved_metadata = _abs_file(metadata_json, "metadata_json")
    metadata = json.loads(Path(resolved_metadata).read_text(encoding="utf-8"))

    raw_parameters = [dict(item) for item in (metadata.get("parameters") or [])]
    raw_responses = [dict(item) for item in (metadata.get("responses") or [])]
    available_parameter_names = [str(item.get("name")) for item in raw_parameters if str(item.get("name") or "").strip()]
    available_response_names = [str(item.get("name")) for item in raw_responses if str(item.get("name") or "").strip()]

    parsed = _parse_formatted_sensitivity_csv(
        matrix_path=resolved_matrix,
        parameter_names=list(parameter_names or available_parameter_names),
        response_names=list(response_names or available_response_names),
    )

    parameter_map = {str(item["name"]): dict(item) for item in raw_parameters if item.get("name")}
    response_map = {str(item["name"]): dict(item) for item in raw_responses if item.get("name")}
    return {
        "matrix_path": resolved_matrix,
        "metadata_json": resolved_metadata,
        "row_labels": parsed["row_labels"],
        "column_labels": parsed["column_labels"],
        "response_rows": [response_map[name] for name in parsed["row_labels"]],
        "parameter_columns": [parameter_map[name] for name in parsed["column_labels"]],
        "matrix": parsed["matrix"],
    }


def store_sensitivity_to_system(
    config: Dict[str, Any],
    run_payload: Dict[str, Any],
) -> Dict[str, Any]:
    project_id = int(config["project_id"])
    batch_no = str(config["batch_no"])
    case_name = str(config.get("case_name") or "nastran_sol200_phase1")
    response_names = [str(item["name"]) for item in (config.get("responses") or []) if str(item.get("name") or "").strip()]
    cloud_result_group = str(config.get("cloud_result_group") or "").strip() or None
    cloud_step_name = str(config.get("cloud_step_name") or "Sensitivity").strip() or "Sensitivity"
    cloud_field_name = str(config.get("cloud_field_name") or "SENSITIVITY_CLOUD").strip() or "SENSITIVITY_CLOUD"

    return store_op2_sensitivity_cloud(
        project_id=project_id,
        batch_no=batch_no,
        case_name=case_name,
        matrix_path=str(run_payload["sensitivity_csv"]),
        bdf_path=str(run_payload["input_bdf"]),
        metadata_json=str(run_payload["metadata_json"]),
        response_names=response_names or None,
        cloud_result_group=cloud_result_group,
        cloud_step_name=cloud_step_name,
        cloud_field_name=cloud_field_name,
    )


def _localize_all_elements_e(
    input_bdf: str,
    localized_output: str,
    lower_scale: float,
    upper_scale: float,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    model = BDF(debug=False)
    model.read_bdf(input_bdf, xref=False)

    next_pid = (max(model.properties.keys()) if model.properties else 0) + 1
    next_mid = (max(model.materials.keys()) if model.materials else 0) + 1
    parameters: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for eid, element in sorted(model.elements.items()):
        pid = getattr(element, "pid", None)
        if pid is None:
            skipped.append({"element_id": int(eid), "reason": "missing property id"})
            continue
        prop = model.properties.get(int(pid))
        if prop is None:
            skipped.append({"element_id": int(eid), "reason": f"property {int(pid)} not found"})
            continue
        mid = _property_material_id(prop)
        if mid is None:
            skipped.append({"element_id": int(eid), "reason": f"property {int(pid)} has no MAT1"})
            continue
        material = model.materials.get(int(mid))
        if material is None or str(getattr(material, "type", "")).upper() != "MAT1":
            skipped.append({"element_id": int(eid), "reason": f"material {int(mid)} is not MAT1"})
            continue
        e_value = _material_scalar(material, "E")
        if getattr(material, "g", None) is not None:
            material.g = None
        if e_value is None:
            skipped.append({"element_id": int(eid), "reason": f"material {int(mid)} missing E"})
            continue

        new_mid = int(next_mid)
        next_mid += 1
        new_pid = int(next_pid)
        next_pid += 1

        new_material = material.__deepcopy__({})
        new_material.mid = int(new_mid)
        new_property = prop.__deepcopy__({})
        if hasattr(new_property, "pid"):
            new_property.pid = int(new_pid)
        if hasattr(new_property, "mid"):
            new_property.mid = int(new_mid)
        elif hasattr(new_property, "mid1"):
            new_property.mid1 = int(new_mid)
            if hasattr(new_property, "mid2"):
                new_property.mid2 = int(new_mid)
            if hasattr(new_property, "mid3"):
                new_property.mid3 = None
        else:
            raise RuntimeError(f"unsupported property type: {getattr(prop, 'type', '')}")

        model.materials[new_mid] = new_material
        model.properties[new_pid] = new_property
        element.pid = int(new_pid)

        parameters.append(
            {
                "name": f"E{int(eid)}",
                "type": "E",
                "element_id": int(eid),
                "property_id": int(new_pid),
                "material_id": int(new_mid),
                "source_property_id": int(pid),
                "source_material_id": int(mid),
                "initial": float(e_value),
                "lower": float(e_value * lower_scale),
                "upper": float(e_value * upper_scale),
            }
        )

    if not parameters:
        raise RuntimeError("all_elements_e did not produce any valid parameters")

    localized_path = Path(localized_output).expanduser().resolve()
    localized_path.parent.mkdir(parents=True, exist_ok=True)
    model.write_bdf(str(localized_path), interspersed=False)
    return str(localized_path), parameters, {
        "localized_input_bdf": str(localized_path),
        "localized_element_count": len(parameters),
        "parameter_count": len(parameters),
        "skipped_preview": skipped[:20],
    }


def _generate_sol200_deck(
    localized_input_bdf: str,
    source_input_bdf: str,
    output_bdf: str,
    parameters: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
    settings: Dict[str, Any],
    preset_info: Dict[str, Any],
) -> Dict[str, Any]:
    output_path = Path(output_bdf).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sensitivity_csv = _resolve_sensitivity_csv_path(output_path, settings)
    sensitivity_csv_internal = str(output_path.parent / "sens.csv") if sensitivity_csv else None
    design_model_bdf = str(output_path.with_name("design_model.bdf"))
    deck_mode = str(settings.get("sol200.deck_mode", "inline")).strip().lower()

    lines = Path(localized_input_bdf).read_text(encoding="utf-8", errors="ignore").splitlines()
    _, bulk_lines = _split_bdf(lines)
    design_lines = _build_sol200_design_lines(parameters, responses)
    control_lines = _build_sol200_control_lines(settings, sensitivity_csv_internal)
    filtered_bulk = _filter_bulk_lines_for_sol200(bulk_lines)

    if deck_mode == "include":
        output_lines = control_lines + [f"INCLUDE './{Path(design_model_bdf).name}'", ""] + filtered_bulk
        Path(design_model_bdf).write_text("\n".join(design_lines).rstrip() + "\n", encoding="utf-8")
    else:
        output_lines = control_lines + design_lines + filtered_bulk

    Path(output_bdf).write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")

    metadata_json = str(output_path.with_suffix(output_path.suffix + ".sol200.json"))
    metadata = {
        "input_bdf": source_input_bdf,
        "localized_input_bdf": localized_input_bdf,
        "output_bdf": str(output_path),
        "parameters": parameters,
        "responses": responses,
        "settings": settings,
        "parameter_preset_info": preset_info,
    }
    Path(metadata_json).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    generated_files = {
        "analysis_bdf": str(output_path),
        "metadata_json": metadata_json,
        "localized_input_bdf": localized_input_bdf,
        "sensitivity_csv": sensitivity_csv,
        "sensitivity_csv_internal": sensitivity_csv_internal,
    }
    if deck_mode == "include":
        generated_files["design_model_bdf"] = design_model_bdf
    return {
        "output_bdf": str(output_path),
        "generated_files": generated_files,
    }


def _run_nastran(
    nastran_command: str,
    bdf_path: str,
    timeout_sec: Optional[int],
    extra_args: Sequence[str],
) -> Dict[str, Any]:
    bdf = Path(bdf_path).resolve()
    completed = subprocess.run(
        [nastran_command, str(bdf)] + [str(item) for item in extra_args],
        cwd=str(bdf.parent),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    return {
        "command": [nastran_command, str(bdf)] + [str(item) for item in extra_args],
        "returncode": int(completed.returncode),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "artifacts": _collect_solver_artifacts(bdf.parent, bdf.stem),
    }


def _parse_formatted_sensitivity_csv(
    matrix_path: str,
    parameter_names: Sequence[str],
    response_names: Sequence[str],
) -> Dict[str, Any]:
    lines = Path(matrix_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    dv_names: List[str] = []
    response_blocks: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if line.startswith("DV ID,Label,Current Value,Lower Limit,Upper Limit"):
            idx += 1
            while idx < len(lines):
                row = lines[idx].rstrip()
                if not row.strip():
                    break
                parts = [item.strip() for item in row.split(",")]
                if len(parts) >= 2 and parts[0].isdigit():
                    dv_names.append(parts[1].strip())
                    idx += 1
                    continue
                break
        if line.startswith("Design Response ID,"):
            if idx + 1 >= len(lines):
                break
            desc_parts = [item.strip() for item in lines[idx + 1].split(",")]
            if len(desc_parts) < 8:
                idx += 1
                continue
            next_idx = idx + 2
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            values_idx = next_idx + 1
            while values_idx < len(lines) and not lines[values_idx].strip():
                values_idx += 1
            labels: List[str] = []
            values: List[float] = []
            labels_line = lines[next_idx].rstrip() if next_idx < len(lines) else ""
            values_line = lines[values_idx].rstrip() if values_idx < len(lines) else ""
            if (
                labels_line
                and not labels_line.strip().startswith("Design Response ID,")
                and values_line
                and not values_line.strip().startswith("Design Response ID,")
                and _looks_like_numeric_csv_line(values_line)
            ):
                labels = [item.strip() for item in labels_line.split(",") if item.strip()]
                values = [_parse_float(item) for item in values_line.split(",") if item.strip()]
            response_blocks.append(
                {
                    "label": desc_parts[1].strip(),
                    "response_type": desc_parts[2].strip().upper(),
                    "parameter_labels": labels,
                    "values": values,
                }
            )
            idx = values_idx + 1 if values else idx + 2
            continue
        idx += 1

    eign_blocks = [item for item in response_blocks if item["response_type"] == "EIGN" and item["values"]]
    if not eign_blocks:
        raise RuntimeError(f"no EIGN sensitivity blocks found in {matrix_path}")

    chosen_parameter_names = list(parameter_names or dv_names or eign_blocks[0]["parameter_labels"])
    if not chosen_parameter_names:
        raise RuntimeError("unable to resolve parameter names from sensitivity csv")

    block_by_name = {str(item["label"]): item for item in eign_blocks}
    chosen_rows = []
    for name in response_names:
        if str(name) not in block_by_name:
            raise RuntimeError(f"response {name!r} not found in sensitivity csv")
        chosen_rows.append(block_by_name[str(name)])

    matrix_rows: List[List[float]] = []
    for block in chosen_rows:
        value_map = {str(label): float(value) for label, value in zip(block["parameter_labels"], block["values"])}
        row: List[float] = []
        for parameter_name in chosen_parameter_names:
            if str(parameter_name) not in value_map:
                raise RuntimeError(f"parameter {parameter_name!r} not found in response {block['label']!r}")
            row.append(float(value_map[str(parameter_name)]))
        matrix_rows.append(row)

    return {
        "row_labels": [str(item["label"]) for item in chosen_rows],
        "column_labels": [str(item) for item in chosen_parameter_names],
        "matrix": np.asarray(matrix_rows, dtype=np.float64),
    }


def _build_mesh_from_bdf(bdf_path: str) -> Tuple[np.ndarray, List[Tuple[str, np.ndarray]], List[np.ndarray]]:
    model = BDF(debug=False)
    model.read_bdf(bdf_path, xref=True)

    ordered_node_ids = sorted(int(node_id) for node_id in model.nodes.keys())
    node_id_to_index = {node_id: idx for idx, node_id in enumerate(ordered_node_ids)}
    points = np.asarray([model.nodes[node_id].get_position() for node_id in ordered_node_ids], dtype=np.float64)

    grouped_cells: Dict[str, List[List[int]]] = {}
    grouped_ids: Dict[str, List[int]] = {}
    for eid, element in sorted(model.elements.items()):
        cell_type = _meshio_cell_type(str(element.type or "").upper())
        if not cell_type:
            continue
        node_ids = [int(node_id) for node_id in (element.node_ids or [])]
        if any(node_id not in node_id_to_index for node_id in node_ids):
            continue
        grouped_cells.setdefault(cell_type, []).append([node_id_to_index[node_id] for node_id in node_ids])
        grouped_ids.setdefault(cell_type, []).append(int(eid))

    cells = [(cell_type, np.asarray(rows, dtype=np.int32)) for cell_type, rows in grouped_cells.items()]
    cell_element_ids = [np.asarray(grouped_ids[cell_type], dtype=np.int64) for cell_type, _ in cells]
    return points, cells, cell_element_ids


def _build_sol200_control_lines(settings: Dict[str, Any], sensitivity_csv_internal: Optional[str]) -> List[str]:
    dynamic_norm = str(settings.get("dynamic.norm", "MASS")).strip().upper()
    if dynamic_norm not in {"MASS", "2"}:
        raise RuntimeError("dynamic.norm must be MASS or 2")

    fmin = _format_float_or_blank(settings.get("dynamic.fmin"))
    fmax = _format_float_or_blank(settings.get("dynamic.fmax"))
    vectors = _format_int_or_blank(settings.get("dynamic.vectors"))
    post = "-5" if str(settings.get("result.target", "OP2")).strip().upper() == "OP2" else "-1"

    lines: List[str] = []
    if sensitivity_csv_internal:
        lines.append(f"ASSIGN USERFILE='{Path(sensitivity_csv_internal).name}' FORM=FORMATTED STATUS=UNKNOWN UNIT=52")
    lines.extend(
        [
            "SOL 200",
            "CEND",
            "METHOD = 1",
            "DISPLACEMENT(PLOT)=ALL",
            "DSAPRT(FORMATTED,EXPORT,END=SENS)" if sensitivity_csv_internal else "DSAPRT(NOPRINT,EXPORT,END=SENS)",
            "",
            "SUBCASE 1",
            "  ANALYSIS = MODES",
            "  DESSUB = 1",
            "BEGIN BULK",
            f"PARAM,POST,{post}",
            "PARAM,GRDPNT,0",
            "PARAM,K6ROT,10.0",
            "PARAM,COUPMASS,-1",
            "PARAM,XYUNIT,52" if sensitivity_csv_internal else "",
            f"EIGRL,1,{fmin},{fmax},{vectors},,,,{dynamic_norm}",
        ]
    )
    return [line for line in lines if line != ""]


def _build_sol200_design_lines(parameters: Sequence[Dict[str, Any]], responses: Sequence[Dict[str, Any]]) -> List[str]:
    lines = [
        "$ -----------------------------------------------------------------------------",
        "$ Standalone SOL200 design model include",
        "$ -----------------------------------------------------------------------------",
    ]
    for index, parameter in enumerate(parameters, start=1):
        lines.append(
            ",".join(
                [
                    "DESVAR",
                    str(index),
                    str(parameter["name"]),
                    _format_free_float(parameter["initial"]),
                    _format_free_float(parameter["lower"]),
                    _format_free_float(parameter["upper"]),
                ]
            ).rstrip(",")
        )
        lines.append(f"{'DVMREL1':<8}{index:>8}{'MAT1':<8}{int(parameter['material_id']):>8}{'E':<8}")
        lines.append(f"{'':<8}{index:>8}{_format_small_float(1.0):>8}")

    for index, response in enumerate(responses, start=1):
        lines.append(f"DRESP1,{index},{response['name']},FREQ,STRUC,,{int(response['mode_number'])}")
        lines.append(f"DCONSTR,1,{index},-1.0E30,1.0E30")
        lines.append(f"DSCREEN  FREQ    -1.0E30")
    return lines


def _filter_bulk_lines_for_sol200(lines: Sequence[str]) -> List[str]:
    filtered: List[str] = []
    skipping_continuation = False
    skip_prefixes = ("EIG", "DES", "DCO", "DRE", "DVM", "DVP")
    skip_params = {"POST", "K6ROT", "GRDPNT", "COUPMASS", "XYUNIT"}
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            filtered.append(line)
            skipping_continuation = False
            continue
        if stripped.startswith("$"):
            filtered.append(line)
            continue
        if skipping_continuation and stripped[0] in {"+", "*", ","}:
            continue

        card = stripped.split(",", 1)[0].split()[0].rstrip("*").upper()
        if any(card.startswith(prefix) for prefix in skip_prefixes):
            skipping_continuation = True
            continue
        if card == "PARAM":
            parts = [part.strip().upper() for part in stripped.replace(" ", ",").split(",") if part.strip()]
            if len(parts) >= 2 and parts[1] in skip_params:
                skipping_continuation = True
                continue
        skipping_continuation = False
        filtered.append(line)
    return filtered


def _materialize_sensitivity_csv(internal_path: Optional[str], target_path: Optional[str]) -> None:
    if not internal_path or not target_path:
        return
    src = Path(internal_path).resolve()
    dst = Path(target_path).resolve()
    if not src.exists():
        return
    if src != dst:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))


def _resolve_sensitivity_csv_path(output_bdf: Path, settings: Dict[str, Any]) -> Optional[str]:
    if not bool(settings.get("sol200.sensitivity_csv", False)):
        return None
    explicit = settings.get("sol200.sensitivity_csv_path")
    if explicit:
        return str(Path(str(explicit)).expanduser().resolve())
    return str((output_bdf.parent / "sol200_sens.csv").resolve())


def _collect_solver_artifacts(workdir: Path, stem: str) -> Dict[str, str]:
    suffixes = [".f04", ".f06", ".log", ".op2", ".pch", ".xdb", ".asm", ".master"]
    artifacts: Dict[str, str] = {}
    for suffix in suffixes:
        path = workdir / f"{stem}{suffix}"
        if path.exists():
            artifacts[suffix.lstrip(".")] = str(path.resolve())
    for extra_name in ["fort.11", "fort.51", "fort.91", "fort.92", "sens.csv"]:
        path = workdir / extra_name
        if path.exists():
            artifacts[extra_name] = str(path.resolve())
    return artifacts


def _split_bdf(lines: Sequence[str]) -> Tuple[List[str], List[str]]:
    before_bulk: List[str] = []
    bulk: List[str] = []
    in_bulk = False
    for line in lines:
        if not in_bulk:
            before_bulk.append(line)
            if line.strip().upper().startswith("BEGIN BULK"):
                in_bulk = True
        else:
            bulk.append(line)
    return before_bulk, bulk


def _localized_bdf_path(output_bdf: str) -> str:
    path = Path(output_bdf).expanduser().resolve()
    return str(path.with_name(f"{path.stem}.localized_source.bdf"))


def _meshio_cell_type(element_type: str) -> Optional[str]:
    return {
        "CBAR": "line",
        "CBEAM": "line",
        "CTRIA3": "triangle",
        "CQUAD4": "quad",
        "CTETRA": "tetra",
        "CPENTA": "wedge",
        "CHEXA": "hexahedron",
    }.get(element_type)


def _property_material_id(prop: Any) -> Optional[int]:
    for name in ("mid", "mid1"):
        value = getattr(prop, name, None)
        if hasattr(value, "mid"):
            return int(value.mid)
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return None


def _material_scalar(material: Any, field: str) -> Optional[float]:
    candidate_names = {"E": ("e", "E"), "RHO": ("rho", "Rho")}.get(str(field).upper(), ())
    for name in candidate_names:
        value = getattr(material, name, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _format_small_float(value: float) -> str:
    text = f"{float(value):.7f}"
    if text.startswith("0"):
        text = text[1:]
    if len(text) > 8:
        text = f"{float(value):.6f}"
        if text.startswith("0"):
            text = text[1:]
    return text.rjust(8)[:8]


def _format_free_float(value: Any) -> str:
    text = f"{float(value):.15g}"
    if "." not in text and "E" not in text.upper():
        text = f"{text}.0"
    return text


def _format_float_or_blank(value: Any) -> str:
    if value in (None, ""):
        return ""
    return _format_small_float(float(value)).strip()


def _format_int_or_blank(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(int(value))


def _parse_float(text: str) -> float:
    token = str(text).strip().replace("D", "E").replace("d", "e")
    if "E" not in token.upper():
        for idx in range(1, len(token)):
            if token[idx] in "+-" and token[idx - 1].isdigit():
                token = token[:idx] + "E" + token[idx:]
                break
    return float(token)


def _looks_like_numeric_csv_line(line: str) -> bool:
    tokens = [item.strip() for item in str(line).split(",") if item.strip()]
    if not tokens:
        return False
    for token in tokens:
        try:
            _parse_float(token)
        except Exception:
            return False
    return True


def _abs_file(path: str, field_name: str) -> str:
    resolved = os.path.abspath(str(path))
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"{field_name} not found: {resolved}")
    if not os.path.isfile(resolved):
        raise RuntimeError(f"{field_name} must be a file: {resolved}")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone SOL200 frequency sensitivity -> system h5 workflow")
    parser.add_argument("--skip-solver", action="store_true", help="Reuse existing sensitivity csv instead of running Nastran")
    args = parser.parse_args()

    run_config = dict(RUN_CONFIG)
    if args.skip_solver:
        run_config["run_solver"] = False

    run_result = run_sol200_frequency_sensitivity(run_config)
    store_result = store_sensitivity_to_system(run_config, run_result)

    print("SOL200 workflow finished")
    print(f"  analysis_bdf : {run_result['output_bdf']}")
    print(f"  metadata_json: {run_result['metadata_json']}")
    print(f"  sensitivity  : {run_result['sensitivity_csv']}")
    print(f"  project_id   : {run_result['project_id']}")
    print(f"  batch_no     : {run_result['batch_no']}")
    print(f"  result_group : {store_result['cloud_result']['result_group']}")
    print(f"  step         : {store_result['cloud_result']['step']}")
    print(f"  field        : {store_result['cloud_result']['field']}")
    print(f"  workspace    : {store_result['workspace']}")


if __name__ == "__main__":
    main()
