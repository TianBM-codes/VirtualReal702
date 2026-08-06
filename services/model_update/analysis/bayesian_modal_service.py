import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.l3.core.errors import ValidationError

from . import solver_service as _solver


def _extract_modal_response_values_from_op2(
        *,
        op2_path: str,
        response_rows: Sequence[dict],
        source_label: str = "modal",
) -> np.ndarray:
    from services.model_update.importers.op2_service import _extract_mode_frequency, _read_op2

    resolved_op2 = _solver._abs_file(op2_path, "op2_path")
    op2 = _read_op2(str(resolved_op2))
    eigenvectors = getattr(op2, "eigenvectors", {}) or {}
    if not eigenvectors:
        raise ValidationError(
            f"modal eigenvectors were not found in the {source_label} OP2 result",
            {"op2_path": str(resolved_op2), "source_label": source_label},
        )
    first_subcase_id = sorted(int(key) for key in eigenvectors.keys())[0]
    eigen_data = eigenvectors[first_subcase_id]
    modes_array = np.asarray(getattr(eigen_data, "modes", []), dtype=np.int64)
    if modes_array.size == 0:
        raise ValidationError(
            f"modal mode numbers were not found in the {source_label} OP2 result",
            {"op2_path": str(resolved_op2), "subcase_id": first_subcase_id, "source_label": source_label},
        )
    response_values: List[float] = []
    missing_modes: List[int] = []
    for row in response_rows or []:
        mode_number = row.get("mode_number")
        if mode_number is None:
            raise ValidationError(
                "mode_number is required for modal Bayesian responses",
                {"response_row": dict(row or {}), "source_label": source_label},
            )
        found = np.where(modes_array == int(mode_number))[0]
        if len(found) == 0:
            missing_modes.append(int(mode_number))
            continue
        mode_index = int(found[0])
        frequency, _eigenvalue, warnings = _extract_mode_frequency(eigen_data, mode_index)
        if warnings:
            raise ValidationError(
                f"failed to resolve modal frequency from the {source_label} OP2 result",
                {
                    "op2_path": str(resolved_op2),
                    "subcase_id": first_subcase_id,
                    "mode_number": int(mode_number),
                    "warnings": warnings,
                    "source_label": source_label,
                },
            )
        response_values.append(float(frequency))
    if missing_modes:
        raise ValidationError(
            f"some requested modal frequency responses were not found in the {source_label} OP2 result",
            {
                "op2_path": str(resolved_op2),
                "subcase_id": first_subcase_id,
                "missing_mode_numbers": missing_modes,
                "source_label": source_label,
            },
        )
    return np.asarray(response_values, dtype=np.float64)


def _extract_modal_mode_vectors_from_op2(
        *,
        op2_path: str,
        bdf_path: str,
        mode_numbers: Sequence[int],
) -> Dict[int, Dict[Tuple[str, int], np.ndarray]]:
    from services.model_update.importers.op2_service import build_modal_import_payload

    requested_modes = sorted({int(mode_no) for mode_no in list(mode_numbers or [])})
    if not requested_modes:
        return {}

    payload = build_modal_import_payload(
        op2_path=op2_path,
        bdf_path=bdf_path,
        mode_numbers=requested_modes,
    )
    mode_maps: Dict[int, Dict[Tuple[str, int], np.ndarray]] = {}
    for mode_row in list(payload.get("modes") or []):
        mode_no = int(mode_row["mode_no"])
        node_map: Dict[Tuple[str, int], np.ndarray] = {}
        for node_row in list(mode_row.get("nodes") or []):
            instance_name = str(node_row.get("instance_name") or "")
            node_map[(instance_name, int(node_row["fem_node_label"]))] = np.array(
                [
                    float(node_row.get("u1") or 0.0),
                    float(node_row.get("u2") or 0.0),
                    float(node_row.get("u3") or 0.0),
                ],
                dtype=np.float64,
            )
        mode_maps[mode_no] = node_map
    return mode_maps


def _extract_modal_response_values_by_semantics(
        *,
        op2_path: str,
        response_rows: Sequence[dict],
        project_id: Optional[int] = None,
        bdf_path: Optional[str] = None,
        mac_scale: float = 100.0,
        source_label: str = "modal",
) -> np.ndarray:
    has_modal_mac = any(
        str(item.get("response_type") or item.get("type") or "").strip().upper() == "MODAL_MAC"
        for item in (response_rows or [])
    )
    if not has_modal_mac:
        return _extract_modal_response_values_from_op2(
            op2_path=op2_path,
            response_rows=response_rows,
            source_label=source_label,
        )

    if project_id is None:
        raise ValidationError(
            "project_id is required when extracting MODAL_MAC response values from OP2",
            {"source_label": source_label, "op2_path": op2_path},
        )
    if not str(bdf_path or "").strip():
        raise ValidationError(
            "bdf_path is required when extracting MODAL_MAC response values from OP2",
            {"source_label": source_label, "op2_path": op2_path, "project_id": int(project_id)},
        )

    frequency_rows = []
    requested_mac_modes: List[int] = []
    for row in response_rows or []:
        resolved_type = str(row.get("response_type") or row.get("type") or "").strip().upper()
        if resolved_type == "MODAL_MAC":
            fem_mode_no = row.get("fem_mode_no", row.get("mode_number"))
            test_mode_no = row.get("test_mode_no")
            if fem_mode_no is None or test_mode_no is None:
                raise ValidationError(
                    "MODAL_MAC response is missing fem/test mode numbers during modal response extraction",
                    {"response_row": dict(row or {}), "source_label": source_label},
                )
            requested_mac_modes.append(int(fem_mode_no))
        else:
            frequency_rows.append(dict(row))

    frequency_values: Dict[Tuple[str, Optional[int]], float] = {}
    if frequency_rows:
        frequency_vector = _extract_modal_response_values_from_op2(
            op2_path=op2_path,
            response_rows=frequency_rows,
            source_label=source_label,
        )
        for index, row in enumerate(frequency_rows):
            frequency_values[
                (str(row.get("response_type") or row.get("type") or "").strip().upper(), row.get("mode_number"))
            ] = float(frequency_vector[index])

    fem_mode_maps = _extract_modal_mode_vectors_from_op2(
        op2_path=op2_path,
        bdf_path=str(bdf_path),
        mode_numbers=requested_mac_modes,
    )
    from .modal_mac_service import compute_project_modal_mac_from_fem_mode_map

    values: List[float] = []
    for row in response_rows or []:
        resolved_type = str(row.get("response_type") or row.get("type") or "").strip().upper()
        if resolved_type == "MODAL_MAC":
            fem_mode_no = int(row.get("fem_mode_no", row.get("mode_number")))
            test_mode_no = int(row["test_mode_no"])
            fem_mode_map = fem_mode_maps.get(fem_mode_no)
            if not fem_mode_map:
                raise ValidationError(
                    "requested MODAL_MAC fem mode vectors were not found in the updated OP2 result",
                    {
                        "project_id": int(project_id),
                        "fem_mode_no": fem_mode_no,
                        "test_mode_no": test_mode_no,
                        "source_label": source_label,
                        "op2_path": op2_path,
                        "bdf_path": bdf_path,
                    },
                )
            mac_payload = compute_project_modal_mac_from_fem_mode_map(
                project_id=int(project_id),
                test_mode_no=test_mode_no,
                fem_mode_map=fem_mode_map,
                mac_scale=float(mac_scale),
            )
            values.append(float(mac_payload["mac"]))
            continue

        key = (resolved_type, row.get("mode_number"))
        if key not in frequency_values:
            raise ValidationError(
                "failed to align modal frequency response values during mixed modal extraction",
                {"response_row": dict(row or {}), "source_label": source_label},
            )
        values.append(float(frequency_values[key]))

    return np.asarray(values, dtype=np.float64)


def _resolve_solver_op2_path(solver_payload: Dict[str, any], *, source_label: str) -> str:
    solver = dict(solver_payload.get("solver") or {})
    if not bool(solver.get("ok", False)):
        raise ValidationError(
            f"{source_label} solve failed",
            {"source_label": source_label, "solver": solver},
        )
    summary = dict(solver.get("artifacts_summary") or {})
    for path_text in list(summary.get("op2_files") or []):
        text = str(path_text or "").strip()
        if not text:
            continue
        path = Path(text).expanduser().resolve()
        if path.exists() and path.is_file():
            return str(path)
    raise ValidationError(
        f"{source_label} solve did not produce an OP2 file",
        {
            "source_label": source_label,
            "artifacts_summary": summary,
            "warnings": solver_payload.get("warnings") or [],
        },
    )


def _run_sol103_modal_response_values(
        *,
        input_bdf: str,
        response_rows: Sequence[dict],
        output_bdf: str,
        project_id: Optional[int] = None,
        settings: Optional[Dict[str, any]] = None,
        nastran: Optional[str] = None,
        timeout_sec: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
) -> Dict[str, any]:
    from services.model_update.analysis.solver_service import run_nastran_sol103_job

    sol103_payload = run_nastran_sol103_job(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        settings=dict(settings or {}),
        nastran=nastran,
        run_solver=True,
        timeout_sec=timeout_sec,
        extra_args=list(extra_args or []),
    )
    op2_path = _resolve_solver_op2_path(sol103_payload, source_label="SOL103 modal")
    response_values = _extract_modal_response_values_by_semantics(
        op2_path=op2_path,
        response_rows=response_rows,
        project_id=project_id,
        bdf_path=input_bdf,
        source_label="SOL103 modal",
    )
    return {
        "solver_payload": sol103_payload,
        "op2_path": op2_path,
        "response_values": response_values,
    }


def _apply_physical_parameter_value_floor(
        parameter_columns: Sequence[dict],
        parameter_values: Sequence[float],
) -> np.ndarray:
    values = np.asarray(parameter_values, dtype=np.float64).reshape(-1).copy()
    if values.size == 0:
        return values
    if len(list(parameter_columns or [])) != int(values.size):
        raise ValidationError(
            "parameter_columns count does not match parameter_values size",
            {"parameter_count": len(list(parameter_columns or [])), "value_count": int(values.size)},
        )

    for index, raw_row in enumerate(parameter_columns or []):
        row = dict(raw_row or {})
        resolved_type = str(row.get("param_type") or row.get("parameter_type") or row.get("type") or "").upper()
        if resolved_type not in {"E", "RHO", "H", "T"}:
            continue
        candidate = float(values[index])
        if candidate > 0.0:
            continue
        baseline = row.get("parameter_value", row.get("initial_value", row.get("initial")))
        try:
            baseline_value = abs(float(baseline))
        except Exception:
            baseline_value = 0.0
        values[index] = max(baseline_value * 1.0e-6, 1.0e-12)
    return values


def _build_sol200_parameter_rows(parameter_columns: Sequence[dict], parameter_values: Sequence[float]) -> List[dict]:
    def _positive_floor_from_row(row: dict, candidate_value: float) -> float:
        baseline = row.get("parameter_value", row.get("initial_value", row.get("initial")))
        try:
            baseline_value = abs(float(baseline))
        except Exception:
            baseline_value = 0.0
        return max(float(candidate_value), max(baseline_value * 1.0e-6, 1.0e-12))

    rows: List[dict] = []
    values = list(parameter_values) if parameter_values is not None else []
    for index, raw_row in enumerate(parameter_columns or []):
        row = dict(raw_row or {})
        resolved_name = str(
            row.get("parameter_name")
            or row.get("param_name")
            or row.get("field")
            or f"parameter_{index + 1}"
        ).strip() or f"parameter_{index + 1}"
        resolved_type = str(row.get("param_type") or row.get("parameter_type") or row.get("type") or "").upper()
        row["parameter_name"] = resolved_name
        row["name"] = resolved_name
        row["parameter_type"] = resolved_type
        row["type"] = resolved_type
        resolved_initial = float(values[index])
        if resolved_type in {"E", "RHO", "H", "T"} and resolved_initial <= 0.0:
            resolved_initial = _positive_floor_from_row(row, resolved_initial)
        row["initial"] = resolved_initial
        if row.get("lower_bound") is not None:
            resolved_lower = float(row["lower_bound"])
            if resolved_type in {"E", "RHO", "H", "T"} and resolved_lower <= 0.0:
                resolved_lower = _positive_floor_from_row(row, resolved_lower)
            row["lower"] = resolved_lower
        elif row.get("lower") is not None:
            resolved_lower = float(row["lower"])
            if resolved_type in {"E", "RHO", "H", "T"} and resolved_lower <= 0.0:
                resolved_lower = _positive_floor_from_row(row, resolved_lower)
            row["lower"] = resolved_lower
        if row.get("upper_bound") is not None:
            row["upper"] = float(row["upper_bound"])
        elif row.get("upper") is not None:
            row["upper"] = float(row["upper"])
        rows.append(row)
    return rows


def _build_sol200_response_rows(response_rows: Sequence[dict]) -> List[dict]:
    rows: List[dict] = []
    for index, raw_row in enumerate(response_rows or [], start=1):
        row = dict(raw_row or {})
        response_type = str(row.get("response_type") or row.get("type") or "").upper()
        mode_number = row.get("mode_number")
        if response_type not in {"FREQ", "MODAL_FREQUENCY"} or mode_number is None:
            raise ValidationError(
                "SOL200 modal Bayesian only supports modal frequency responses",
                {"response_row": row, "index": index},
            )
        rows.append(
            {
                "name": str(row.get("response_name") or f"FREQ_MODE_{int(mode_number)}").strip(),
                "type": "FREQ",
                "mode_number": int(mode_number),
            }
        )
    return rows
