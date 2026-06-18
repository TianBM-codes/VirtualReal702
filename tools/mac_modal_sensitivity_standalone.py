#!/usr/bin/env python3
"""
Standalone helpers for MAC modal sensitivity workflows.

This tool intentionally keeps two entrypoints separate:
1. build-sol200-bdf
   Generate a SOL200 sensitivity deck from a base BDF plus parameter/response
   configuration. This reuses the repository's existing SOL200 deck builder.
2. compute-mac-sensitivity
   Compute MAC and dMAC/dp directly from external matrices. This is the
   easiest way to compare against FEMTools or any other external source.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import meshio
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.model_update.analysis.nastran_sol200_service import generate_sol200_workflow
from services.model_update.analysis.modal_mac_service import run_modal_mac_sensitivity
from services.model_update.importers.op2_service import (
    _build_mesh_from_bdf,
    _element_property_id,
    _parse_formatted_sensitivity_csv,
    _property_material_ids,
)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _load_json(path: str) -> Dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    with resolved.open("r", encoding="utf-8-sig") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise RuntimeError(f"config must be a JSON object: {resolved}")
    return payload


def _write_json(path: str, payload: Dict[str, Any]) -> str:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(_json_dumps(payload), encoding="utf-8")
    return str(resolved)


def run_mac_sensitivity_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return run_modal_mac_sensitivity(payload)


def _expand_frequency_responses(mode_numbers: Iterable[Any]) -> List[dict]:
    rows: List[dict] = []
    for mode_number in list(mode_numbers or []):
        mode_no = int(mode_number)
        rows.append(
            {
                "name": f"FREQ_MODE_{mode_no}",
                "type": "FREQ",
                "mode_number": mode_no,
            }
        )
    return rows


def _expand_modal_displacement_groups(groups: Iterable[dict]) -> List[dict]:
    rows: List[dict] = []
    for raw_group in list(groups or []):
        group = dict(raw_group or {})
        component = str(group.get("component") or "U3").strip().upper()
        mode_numbers = [int(item) for item in list(group.get("mode_numbers") or [])]
        node_ids = [int(item) for item in list(group.get("node_ids") or [])]
        if not mode_numbers:
            raise RuntimeError(f"modal_displacement_groups entry is missing mode_numbers: {group}")
        if not node_ids:
            raise RuntimeError(f"modal_displacement_groups entry is missing node_ids: {group}")
        name_template = str(group.get("name_template") or "PHI_M{mode}_N{node}_{component}")
        for mode_no in mode_numbers:
            for node_id in node_ids:
                rows.append(
                    {
                        "name": name_template.format(mode=mode_no, node=node_id, component=component),
                        "type": "DISP",
                        "mode_number": int(mode_no),
                        "node_id": int(node_id),
                        "component": component,
                    }
                )
    return rows


def _collect_build_responses(payload: Dict[str, Any]) -> List[dict]:
    responses = [dict(item) for item in list(payload.get("responses") or [])]
    responses.extend(_expand_frequency_responses(payload.get("frequency_response_modes") or []))
    responses.extend(_expand_modal_displacement_groups(payload.get("modal_displacement_groups") or []))
    if not responses:
        raise RuntimeError("build-sol200-bdf requires at least one response")
    return responses


def run_build_sol200_bdf(payload: Dict[str, Any]) -> Dict[str, Any]:
    input_bdf = str(payload.get("input_bdf") or "").strip()
    output_bdf = str(payload.get("output_bdf") or "").strip()
    if not input_bdf:
        raise RuntimeError("input_bdf is required")
    if not output_bdf:
        raise RuntimeError("output_bdf is required")

    parameters = [dict(item) for item in list(payload.get("parameters") or [])]
    parameter_preset = dict(payload.get("parameter_preset") or {})
    settings = dict(payload.get("settings") or {})
    responses = _collect_build_responses(payload)

    result = generate_sol200_workflow(
        project_id=None,
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=parameters or None,
        parameter_preset=parameter_preset or None,
        responses=responses,
        settings=settings,
    )
    return {
        "workflow": "build_sol200_bdf",
        "input_bdf": result.get("input_bdf"),
        "output_bdf": result.get("output_bdf"),
        "parameter_preset": parameter_preset or None,
        "response_count": len(responses),
        "responses": responses,
        "parameter_count": int(result.get("parameter_count") or 0),
        "generated_files": dict(result.get("generated_files") or {}),
        "preview": dict(result.get("preview") or {}),
        "parameter_preset_info": dict(result.get("parameter_preset_info") or {}),
    }


def run_export_sensitivity_vtu(payload: Dict[str, Any]) -> Dict[str, Any]:
    input_bdf = str(payload.get("input_bdf") or "").strip()
    matrix_path = str(payload.get("matrix_path") or "").strip()
    metadata_json = str(payload.get("metadata_json") or "").strip()
    output_vtu = str(payload.get("output_vtu") or "").strip()
    response_name = str(payload.get("response_name") or "").strip()
    if not input_bdf:
        raise RuntimeError("input_bdf is required")
    if not matrix_path:
        raise RuntimeError("matrix_path is required")
    if not metadata_json:
        raise RuntimeError("metadata_json is required")
    if not output_vtu:
        raise RuntimeError("output_vtu is required")
    if not response_name:
        raise RuntimeError("response_name is required")

    metadata = _load_json(metadata_json)
    parameters = [dict(item) for item in list(metadata.get("parameters") or [])]
    responses = [dict(item) for item in list(metadata.get("responses") or [])]
    parameter_names = [str(item.get("name")) for item in parameters if str(item.get("name") or "").strip()]
    response_names = [str(item.get("name")) for item in responses if str(item.get("name") or "").strip()]
    if not parameter_names:
        raise RuntimeError(f"metadata_json does not contain usable parameter names: {metadata_json}")
    if not response_names:
        raise RuntimeError(f"metadata_json does not contain usable response names: {metadata_json}")

    response_meta = next(
        (dict(item) for item in responses if str(item.get("name") or "").strip() == response_name),
        None,
    )
    if response_meta is None:
        raise RuntimeError(
            f"response_name not found in metadata_json: {response_name}; available: {response_names}"
        )

    parsed = _parse_formatted_sensitivity_csv(
        matrix_path,
        parameter_names=parameter_names,
        response_names=[response_name],
        response_rows=[response_meta],
    )
    row_names = [str(name) for name in parsed.get("row_labels") or []]
    col_names = [str(name) for name in parsed.get("column_labels") or []]
    raw_matrix = parsed.get("matrix")
    matrix = np.asarray(raw_matrix if raw_matrix is not None else [], dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise RuntimeError(f"sensitivity matrix is empty for response: {response_name}")
    row_index = 0
    matched_row_label = row_names[row_index] if row_names else response_name

    parameter_by_name = {
        str(item.get("name")): dict(item)
        for item in parameters
        if str(item.get("name") or "").strip()
    }
    coords, cell_blocks, cell_element_ids, _, bdf_model = _build_mesh_from_bdf(str(Path(input_bdf).expanduser().resolve()))
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
                ptype = str(meta.get("param_type") or meta.get("parameter_type") or meta.get("type") or "").upper()
                if meta.get("element_id") is not None and int(meta["element_id"]) == int(element_id):
                    candidates.append(float(matrix[row_index, col_idx]))
                elif ptype == "H" and meta.get("property_id") is not None and pid == int(meta["property_id"]):
                    candidates.append(float(matrix[row_index, col_idx]))
                elif ptype in {"E", "RHO"} and meta.get("material_id") is not None and int(meta["material_id"]) in mids:
                    candidates.append(float(matrix[row_index, col_idx]))
            if candidates:
                values[cell_idx] = float(max(candidates, key=lambda item: abs(item)))
        cell_data_blocks.append(values)

    resolved_output = Path(output_vtu).expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    mesh = meshio.Mesh(points=coords, cells=cell_blocks, cell_data={"sensitivity": cell_data_blocks})
    meshio.write(str(resolved_output), mesh)
    return {
        "workflow": "export_sensitivity_vtu",
        "input_bdf": str(Path(input_bdf).expanduser().resolve()),
        "matrix_path": str(Path(matrix_path).expanduser().resolve()),
        "metadata_json": str(Path(metadata_json).expanduser().resolve()),
        "output_vtu": str(resolved_output),
        "response_name": response_name,
        "matched_row_label": matched_row_label,
        "available_response_names": row_names,
        "cell_block_count": len(cell_blocks),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone tools for MAC sensitivity calculation and SOL200 BDF generation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_build = subparsers.add_parser(
        "build-sol200-bdf",
        help="Generate a SOL200 sensitivity BDF from JSON config.",
    )
    parser_build.add_argument("--config", required=True, help="Path to JSON config.")
    parser_build.add_argument("--output", help="Optional path to write result summary JSON.")

    parser_mac = subparsers.add_parser(
        "compute-mac-sensitivity",
        help="Compute MAC and dMAC/dp directly from matrix inputs.",
    )
    parser_mac.add_argument("--config", required=True, help="Path to JSON config.")
    parser_mac.add_argument("--output", help="Optional path to write result JSON.")

    parser_vtu = subparsers.add_parser(
        "export-sensitivity-vtu",
        help="Export one stored sensitivity response row to VTU from local files.",
    )
    parser_vtu.add_argument("--config", required=True, help="Path to JSON config.")
    parser_vtu.add_argument("--output", help="Optional path to write result JSON.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = _load_json(args.config)

    if args.command == "build-sol200-bdf":
        result = run_build_sol200_bdf(payload)
    elif args.command == "compute-mac-sensitivity":
        result = run_mac_sensitivity_from_payload(payload)
    elif args.command == "export-sensitivity-vtu":
        result = run_export_sensitivity_vtu(payload)
    else:
        raise RuntimeError(f"unsupported command: {args.command}")

    if args.output:
        written = _write_json(args.output, result)
        print(f"结果已写入: {written}")
    else:
        print(_json_dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
