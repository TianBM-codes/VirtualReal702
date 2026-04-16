import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.inp import parse_inp
from src.inp.parameter_mapping import build_parameter_target_map
from src.l3.core.errors import NotFoundError, ValidationError

from . import sensitivity_service as _sens
from . import solver_service as _solver


_PARAMETER_ASSIGNMENT_RE = re.compile(r"^\s*([^=\s,]+)\s*=\s*(.+?)\s*$")
_ITERATION_CLEANUP_SUFFIXES = (".com", ".prt", ".pmg", ".pes", ".par", ".msg", ".sta", ".dat")


def _format_scalar(value: float) -> str:
    return format(float(value), ".12g")


def _normalize_optional_path(path: Optional[str]) -> Optional[str]:
    return os.path.abspath(path) if path else None


def _clone_jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _clone_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone_jsonable(item) for item in value]
    return value


def read_row_from_m_n(file_path: str, m: int, n: int) -> np.ndarray:
    if int(m) < 1 or int(n) < 1:
        raise ValidationError("m and n must be >= 1", {"m": m, "n": n})

    path = _solver._abs_file(file_path, "file_path")
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        lines = handle.readlines()

    if int(m) > len(lines):
        raise ValidationError(
            "row index exceeds file length",
            {"file_path": str(path), "row": int(m), "line_count": len(lines)},
        )

    parts = lines[int(m) - 1].strip().split()
    if int(n) > len(parts):
        raise ValidationError(
            "column index exceeds row length",
            {"file_path": str(path), "row": int(m), "column": int(n), "column_count": len(parts)},
        )

    try:
        values = [float(token) for token in parts[int(n) - 1:]]
    except ValueError as exc:
        raise ValidationError(
            "failed to parse numeric values from the selected text row",
            {"file_path": str(path), "row": int(m), "column": int(n)},
        ) from exc
    return np.asarray(values, dtype=np.float64).reshape(-1, 1)


def _scalarize(value: Any, *, field: str, row_key: str) -> float:
    arr = np.asarray(value, dtype=np.float64)
    if arr.size != 1:
        raise ValidationError(
            "bayesian update requires scalar normalized sensitivities and scalar response values",
            {"field": field, "row_key": row_key, "value": _clone_jsonable(value)},
        )
    return float(arr.reshape(-1)[0])


def _response_row_key(
    *,
    instance: str,
    response_field: str,
    response_component: Optional[str],
    response_position: str,
    response_label: str,
) -> str:
    component = str(response_component or "")
    return f"{instance}|{response_field}|{component}|{response_position}|{response_label}"


def _ordered_dsa_field_names(field_prefix: str, field_names: Sequence[str]) -> List[str]:
    def _sort_key(name: str) -> Tuple[int, int, str, str]:
        token = _sens._extract_dsa_field_token(field_prefix, name)
        match = _sens._DSA_PARAMETER_TOKEN_RE.fullmatch(token)
        if match:
            return (0, int(match.group(2)), token, name)
        return (1, 10**9, token, name)

    return sorted({str(item) for item in field_names}, key=_sort_key)


def _vector_from_input(
    raw_value: Any,
    items: Sequence[dict],
    *,
    label: str,
    key_candidates: Sequence[str],
) -> List[float]:
    if raw_value is None:
        raise ValidationError(f"{label} is required", {label: raw_value})

    if isinstance(raw_value, (int, float, np.integer, np.floating)):
        return [float(raw_value)] * len(items)

    if isinstance(raw_value, np.ndarray):
        values = np.asarray(raw_value, dtype=np.float64).reshape(-1).tolist()
        if len(values) != len(items):
            raise ValidationError(
                f"{label} length does not match resolved item count",
                {"expected": len(items), "actual": len(values)},
            )
        return [float(v) for v in values]

    if isinstance(raw_value, (list, tuple)):
        values = [float(v) for v in raw_value]
        if len(values) != len(items):
            raise ValidationError(
                f"{label} length does not match resolved item count",
                {"expected": len(items), "actual": len(values)},
            )
        return values

    if isinstance(raw_value, dict):
        resolved = []
        missing = []
        for item in items:
            chosen = None
            for key_name in key_candidates:
                candidate = item.get(key_name)
                if candidate is None:
                    continue
                candidate_key = str(candidate)
                if candidate_key in raw_value:
                    chosen = raw_value[candidate_key]
                    break
            if chosen is None:
                missing.append({key: item.get(key) for key in key_candidates if item.get(key) is not None})
            else:
                resolved.append(float(chosen))
        if missing:
            raise ValidationError(
                f"{label} is missing values for some resolved items",
                {"missing": missing[:10], "available_keys": sorted(str(key) for key in raw_value.keys())[:20]},
            )
        return resolved

    raise ValidationError(
        f"unsupported {label} value type",
        {"label": label, "value_type": type(raw_value).__name__},
    )


def _default_scatter_vector(
    items: Sequence[dict],
    *,
    default_value: float,
    metadata_key: Optional[str] = None,
) -> List[float]:
    values: List[float] = []
    for item in items:
        value = item.get(metadata_key) if metadata_key else None
        if value is None:
            values.append(float(default_value))
            continue
        numeric = float(value)
        if numeric <= 0:
            raise ValidationError(
                "scatter must be > 0",
                {"metadata_key": metadata_key, "item": dict(item), "value": value},
            )
        values.append(numeric)
    return values


def _resolve_scatter_vector(
    raw_value: Any,
    items: Sequence[dict],
    *,
    label: str,
    key_candidates: Sequence[str],
    default_value: float,
    metadata_key: Optional[str] = None,
) -> np.ndarray:
    if raw_value is None:
        return np.asarray(
            _default_scatter_vector(items, default_value=default_value, metadata_key=metadata_key),
            dtype=np.float64,
        )
    return np.asarray(
        _vector_from_input(raw_value, items, label=label, key_candidates=key_candidates),
        dtype=np.float64,
    )


def _save_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clone_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _save_vector_txt(path: Path, values: Sequence[float]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(str(path), np.asarray(values, dtype=np.float64).reshape(1, -1), fmt="%.12g")
    return str(path.resolve())


def _save_matrix_txt(path: Path, matrix: Sequence[Sequence[float]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(str(path), np.asarray(matrix, dtype=np.float64), fmt="%.12g")
    return str(path.resolve())


def _iteration_dir(root_dir: Path, iteration: int) -> Path:
    path = (root_dir / f"iteration_{int(iteration):03d}").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_iteration_artifacts(root_dir: Path, iteration_result: dict) -> dict:
    iteration_dir = _iteration_dir(root_dir, int(iteration_result["iteration"]))
    files = {
        "summary_json": _save_json(iteration_dir / "summary.json", iteration_result),
        "sensitivity_matrix_txt": _save_matrix_txt(iteration_dir / "sensitivity_matrix.txt", iteration_result["sensitivity_matrix"]),
        "response_values_txt": _save_vector_txt(iteration_dir / "response_values.txt", iteration_result["response_values"]),
        "target_responses_txt": _save_vector_txt(iteration_dir / "target_responses.txt", iteration_result["target_responses"]),
        "parameter_values_txt": _save_vector_txt(iteration_dir / "parameter_values.txt", iteration_result["parameter_values"]),
        "updated_parameter_values_txt": _save_vector_txt(
            iteration_dir / "updated_parameter_values.txt",
            iteration_result["bayesian"]["p_new"],
        ),
        # This file is for manual cross-checking against FEMTools: parameter -> target elements.
        "parameter_element_mapping_json": _save_json(
            iteration_dir / "parameter_element_mapping.json",
            iteration_result.get("parameter_element_mapping", []),
        ),
    }
    return {"iteration_dir": str(iteration_dir), "files": files}


def _group_scoped_targets(targets: Sequence[object]) -> Dict[str, List[int]]:
    grouped: Dict[str, List[int]] = {}
    for item in targets:
        text = str(item)
        if "::" in text:
            scope_name, label_text = text.split("::", 1)
        else:
            scope_name, label_text = "", text
        try:
            label = int(label_text)
        except ValueError:
            continue
        grouped.setdefault(scope_name, []).append(label)

    for scope_name, labels in grouped.items():
        grouped[scope_name] = sorted(set(int(label) for label in labels))
    return grouped


def _parameter_element_mapping_entry(
    *,
    model,
    source_field_name: str,
    parameter_token: str,
    mapped_parameter_name: str,
    parameter_value: float,
    target_rows: Sequence[dict],
    mapping_mode: str,
    scatter: float,
) -> dict:
    # Persist the resolved parameter-to-element mapping used by this iteration so
    # a bad update (for example negative thickness) can be traced back to its target set.
    result = {
        "field": str(source_field_name),
        "parameter_token": str(parameter_token),
        "parameter_name": str(mapped_parameter_name),
        "parameter_value": float(parameter_value),
        "mapping_mode": str(mapping_mode),
        "scatter": float(scatter),
        "target_rows": [_clone_jsonable(dict(row)) for row in target_rows],
    }
    try:
        target_kind, scoped_targets = _sens._parameter_target_labels(model, dict(target_rows[0])) if len(target_rows) == 1 else (None, [])
        if len(target_rows) > 1:
            all_targets: List[object] = []
            resolved_kind = None
            for row in target_rows:
                row_kind, row_targets = _sens._parameter_target_labels(model, dict(row))
                if resolved_kind is None:
                    resolved_kind = row_kind
                elif resolved_kind != row_kind:
                    resolved_kind = "mixed"
                all_targets.extend(list(row_targets))
            target_kind = resolved_kind
            scoped_targets = all_targets
        result.update(
            {
                "target_kind": target_kind,
                "target_count": len(scoped_targets),
                "scoped_targets": [str(item) for item in scoped_targets],
                "targets_by_scope": _group_scoped_targets(scoped_targets),
            }
        )
    except Exception as exc:
        result.update(
            {
                "target_kind": None,
                "target_count": 0,
                "scoped_targets": [],
                "targets_by_scope": {},
                "mapping_error": str(exc),
            }
        )
    return result


def _read_text_matrix(file_path: str, row_start: int, row_count: int, col_start: int = 1) -> np.ndarray:
    if int(row_count) <= 0:
        raise ValidationError("row_count must be > 0", {"row_count": row_count})
    rows = []
    expected_cols = None
    for offset in range(int(row_count)):
        row = read_row_from_m_n(file_path, int(row_start) + offset, int(col_start)).reshape(-1)
        if expected_cols is None:
            expected_cols = len(row)
        elif len(row) != expected_cols:
            raise ValidationError(
                "text matrix rows have inconsistent column counts",
                {
                    "file_path": os.path.abspath(file_path),
                    "row_start": int(row_start),
                    "row_count": int(row_count),
                    "expected_cols": expected_cols,
                    "actual_cols": len(row),
                    "row": int(row_start) + offset,
                },
            )
        rows.append(row)
    return np.vstack(rows)


def _read_text_row_vector(file_path: str, row: int, col_start: int = 1) -> np.ndarray:
    return read_row_from_m_n(file_path, int(row), int(col_start)).reshape(-1)


def _parameter_values_from_inp(input_inp: str, parameter_names: Sequence[str]) -> np.ndarray:
    model = parse_inp(input_inp)
    values = []
    missing = []
    for name in parameter_names:
        definition = getattr(model, "parameters", {}).get(str(name))
        scalar_value = getattr(definition, "scalar_value", None) if definition is not None else None
        if scalar_value is None:
            missing.append(str(name))
        else:
            values.append(float(scalar_value))
    if missing:
        raise ValidationError(
            "some parameter values could not be resolved from the inp file",
            {"input_inp": os.path.abspath(input_inp), "missing_parameters": missing[:20]},
        )
    return np.asarray(values, dtype=np.float64)


def _expand_scatter_vector(scatter: Any, expected_size: int, *, label: str) -> np.ndarray:
    values = np.asarray(scatter, dtype=np.float64).reshape(-1)
    if values.size == 1 and int(expected_size) > 1:
        values = np.full(int(expected_size), float(values[0]), dtype=np.float64)
    if values.size != int(expected_size):
        raise ValidationError(
            f"{label} size mismatch",
            {"expected": int(expected_size), "actual": int(values.size)},
        )
    if np.any(values <= 0):
        raise ValidationError(
            f"{label} must be > 0",
            {"label": label, "values": values.tolist()},
        )
    return values


def build_normalized_residual(
    r_model: Any,
    r_target: Any,
    *,
    eps: float = 1e-12,
) -> np.ndarray:
    # Follow the FEMTools-style normalized residual used in Untitled-3.py:
    # y_i = (r_model_i - r_target_i) / |r_model_i|
    # If the residual is written as dR_i = r_target_i - r_model_i, then:
    #   -y_i = dR_i / |r_model_i|
    # The later update uses x = -G_n y, so the sign convention is equivalent
    # to applying the normalized residual dR / r in the update direction.
    r_model_arr = np.asarray(r_model, dtype=np.float64).reshape(-1)
    r_target_arr = np.asarray(r_target, dtype=np.float64).reshape(-1)
    if r_model_arr.shape != r_target_arr.shape:
        raise ValidationError(
            "r_model and r_target size mismatch",
            {"r_model_size": int(r_model_arr.size), "r_target_size": int(r_target_arr.size)},
        )
    return (
        (r_model_arr - r_target_arr).reshape(-1, 1)
        / np.maximum(np.abs(r_model_arr).reshape(-1, 1), float(eps))
    )


def _build_normalized_gain_matrix(
    S_norm: Any,
    p_scatter: Any,
    r_scatter: Any,
    *,
    damping: float = 1e-8,
    eps: float = 1e-12,
) -> dict:
    S_norm_arr = np.asarray(S_norm, dtype=np.float64)
    if S_norm_arr.ndim != 2:
        raise ValidationError("S_norm must be a 2D matrix", {"shape": list(S_norm_arr.shape)})

    n_resp, n_param = S_norm_arr.shape
    p_scatter_arr = _expand_scatter_vector(p_scatter, n_param, label="parameter scatter")
    r_scatter_arr = _expand_scatter_vector(r_scatter, n_resp, label="response scatter")

    # Both covariance-like matrices are defined in normalized space, so the
    # gain matrix below is also a normalized-space quantity.
    #
    # The key point is that S_norm is not the raw sensitivity dR/dp.
    # Upstream DSA assembly already converts each scalar sensitivity to:
    #
    #   S_norm(j, i) = (dR_j / dp_i) * p_i / r_j
    #
    # Therefore G_n is the gain matrix associated with the normalized system,
    # not a raw gain matrix that still needs an extra p/r factor afterwards.
    Cp_n = 2.0 * np.diag(1.0 / np.maximum(p_scatter_arr, float(eps)) ** 2)
    Cr_n = np.diag(1.0 / np.maximum(r_scatter_arr, float(eps)) ** 2)
    Cp_n_eff = Cp_n + float(damping) * np.eye(n_param)

    Cp_n_inv = np.linalg.inv(Cp_n_eff)
    Cr_n_inv = np.linalg.inv(Cr_n)
    innovation_cov = Cr_n_inv + S_norm_arr @ Cp_n_inv @ S_norm_arr.T
    G_n = Cp_n_inv @ S_norm_arr.T @ np.linalg.inv(innovation_cov)

    return {
        "Cp_n": Cp_n,
        "Cr_n": Cr_n,
        "Cp_n_eff": Cp_n_eff,
        "Cp_n_inv": Cp_n_inv,
        "Cr_n_inv": Cr_n_inv,
        "innovation_cov": innovation_cov,
        "G_n": G_n,
        "p_scatter": p_scatter_arr,
        "r_scatter": r_scatter_arr,
    }


def bayesian_update_normalized(
    p_current,
    r_model,
    r_target,
    S_norm,
    p_scatter,
    r_scatter,
    damping: float = 1e-8,
    step_scale: float = 1.0,
    lower_bound=None,
    upper_bound=None,
    p_ref=None,
) -> dict:
    p_current = np.asarray(p_current, dtype=float).reshape(-1)
    r_model = np.asarray(r_model, dtype=float).reshape(-1)
    r_target = np.asarray(r_target, dtype=float).reshape(-1)
    S_norm = np.asarray(S_norm, dtype=float)

    if S_norm.ndim != 2:
        raise ValidationError("S_norm must be a 2D matrix", {"shape": list(S_norm.shape)})
    if S_norm.shape != (len(r_model), len(p_current)):
        raise ValidationError(
            "S_norm shape does not match response and parameter counts",
            {"shape": list(S_norm.shape), "response_count": len(r_model), "parameter_count": len(p_current)},
        )

    eps = 1e-12
    delta_r = r_model - r_target

    if p_ref is None:
        p_ref = p_current.copy()
    p_ref = np.asarray(p_ref, dtype=float).reshape(-1)
    p_ref = np.maximum(np.abs(p_ref), eps)

    Dp = np.diag(p_ref)
    # y is the normalized residual vector and G_n is the normalized gain matrix.
    # The update is applied in two stages:
    #
    #   x = -G_n * y
    #   dp = Dp * x
    #   p_new = p_current + dp
    #
    # Combining them gives the practical form:
    #
    #   p_new = p_current + Dp * (-G_n * y)
    #
    # For a single response / single parameter case this corresponds to the
    # same idea as:
    #
    #   p_u = p_0 + p_ii * (...) * dR / r_jj
    #
    # where:
    #   1. the p_i / r_j factor is already embedded in S_norm
    #   2. G_n is built from S_norm, Cp_n, Cr_n in normalized space
    #   3. Dp multiplies back the parameter scale before writing p_new
    y = build_normalized_residual(r_model=r_model, r_target=r_target, eps=eps)
    gain_payload = _build_normalized_gain_matrix(
        S_norm=S_norm,
        p_scatter=p_scatter,
        r_scatter=r_scatter,
        damping=damping,
        eps=eps,
    )
    G_n = gain_payload["G_n"]

    x = float(step_scale) * (G_n @ (-y))
    dp = Dp @ x
    p_new = p_current + dp.reshape(-1)

    if lower_bound is not None:
        p_new = np.maximum(p_new, np.asarray(lower_bound, dtype=float).reshape(-1))
    if upper_bound is not None:
        p_new = np.minimum(p_new, np.asarray(upper_bound, dtype=float).reshape(-1))

    return {
        "delta_r": delta_r.reshape(-1, 1),
        "y": y,
        "x": x,
        "dp": dp,
        "p_new": p_new,
        "G_n": G_n,
        "Cp_n": gain_payload["Cp_n"],
        "Cr_n": gain_payload["Cr_n"],
        "Cp_n_eff": gain_payload["Cp_n_eff"],
        "innovation_cov": gain_payload["innovation_cov"],
        "normalized_parameter_scatter": gain_payload["p_scatter"],
        "normalized_response_scatter": gain_payload["r_scatter"],
    }


def update_parameter_section_values(
    input_inp: str,
    parameter_values: Dict[str, float],
    *,
    output_inp: Optional[str] = None,
) -> dict:
    input_path = _solver._abs_file(input_inp, "input_inp")
    output_path = Path(output_inp).expanduser().resolve() if output_inp else input_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = input_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    updated_lines: List[str] = []
    inside_parameter_block = False
    seen = set()

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("*PARAMETER"):
            inside_parameter_block = True
            updated_lines.append(line)
            continue
        if inside_parameter_block and stripped.startswith("*"):
            inside_parameter_block = False

        if inside_parameter_block and stripped and not stripped.startswith("**"):
            parts = [part.strip() for part in line.split(",") if part.strip()]
            replaced_parts = []
            changed = False
            for part in parts:
                match = _PARAMETER_ASSIGNMENT_RE.match(part)
                if not match:
                    replaced_parts.append(part)
                    continue
                name = str(match.group(1)).strip()
                if name in parameter_values:
                    replaced_parts.append(f"{name}={_format_scalar(parameter_values[name])}")
                    seen.add(name)
                    changed = True
                else:
                    replaced_parts.append(f"{name}={match.group(2).strip()}")
            updated_lines.append(",".join(replaced_parts) if changed else line)
            continue

        updated_lines.append(line)

    missing = sorted(str(name) for name in parameter_values.keys() if str(name) not in seen)
    if missing:
        raise ValidationError(
            "some parameters were not found under any *PARAMETER block",
            {"missing_parameters": missing[:20], "input_inp": str(input_path)},
        )

    output_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return {
        "input_inp": str(input_path),
        "output_inp": str(output_path),
        "updated_parameters": {str(key): float(value) for key, value in parameter_values.items()},
    }


def build_dsa_normalized_sensitivity_matrix(
    *,
    project_id: int,
    odb_id: Optional[str] = None,
    base_url: Optional[str] = None,
    inp_path: Optional[str] = None,
    workspace: Optional[str] = None,
    odb_path: Optional[str] = None,
    workspace_root: Optional[str] = None,
    step: Optional[str] = None,
    instances: Optional[List[str]] = None,
    field_prefix: str = "d_U_",
    response_component: Optional[str] = None,
    position: Optional[str] = None,
    aggregation: str = "max_abs",
    frame: int = 0,
    abaqus: str = "abaqus",
    python3: Optional[str] = None,
    keep_raw: bool = False,
    timeout: int = 60,
) -> dict:
    if aggregation not in _sens._AGGREGATIONS and aggregation != "first":
        raise ValidationError(
            f"unsupported aggregation '{aggregation}'",
            {"aggregation": aggregation, "allowed": sorted(_sens._AGGREGATIONS | {'first'})},
        )

    resolved_inp_path = _normalize_optional_path(inp_path) or _sens._resolve_inp_path_from_project(project_id)
    if not os.path.exists(resolved_inp_path):
        raise NotFoundError(f"inp file not found: {resolved_inp_path}", {"inp_path": resolved_inp_path})

    resolved_workspace = _normalize_optional_path(workspace)
    workspace_built = False
    client = None
    selector = _sens._build_field_selector(field_prefix=field_prefix)
    resolved_base_url = base_url or "http://127.0.0.1:18765"

    if odb_path:
        odb_abs = os.path.abspath(odb_path)
        if not resolved_workspace:
            workspace_parent = Path(workspace_root).expanduser().resolve() if workspace_root else Path(odb_abs).resolve().parent
            workspace_parent.mkdir(parents=True, exist_ok=True)
            resolved_workspace = str((workspace_parent / f"{Path(odb_abs).stem}_workspace").resolve())
        _sens.build_workspace_from_odb(
            odb_path=odb_abs,
            workspace=resolved_workspace,
            abaqus=abaqus,
            python3=python3,
            keep_raw=keep_raw,
        )
        resolved_workspace = _sens._workspace_path(resolved_workspace)
        workspace_built = True
    elif resolved_workspace:
        resolved_workspace = _sens._workspace_path(resolved_workspace)

    if resolved_workspace:
        discovery = _sens._discover_sensitivity_fields_from_workspace(
            resolved_workspace,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        source_mode = "workspace"
    elif odb_id and not base_url:
        discovery = _sens._discover_sensitivity_fields_from_registry(
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
        client = _sens.ODBClient(base_url=resolved_base_url, timeout=timeout)
        discovery = _sens._discover_sensitivity_fields(
            client,
            odb_id,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        source_mode = "l3_api"

    # DSA columns come from result fields, then are mapped back to design parameters
    # and finally to INP target sets/sections.
    dsa_model = parse_inp(resolved_inp_path)
    dsa_parameter_rows = _sens._load_project_optimization_parameters(project_id)
    dsa_parameter_map = (
        _sens._build_dsa_parameter_row_map(dsa_parameter_rows, field_prefix, discovery["field_names"])
        if dsa_parameter_rows
        else {}
    )
    dsa_direct_target_map = build_parameter_target_map(dsa_model)
    dsa_design_parameter_name_map = _sens._build_dsa_design_parameter_name_map(dsa_model)
    dsa_response_specs = _sens._resolve_dsa_response_spec(dsa_model, step_name=discovery["step"]) or []
    if not dsa_response_specs:
        raise ValidationError(
            "no usable design response definition was found in the inp file",
            {"inp_path": resolved_inp_path, "step": discovery["step"]},
        )
    selected_response_specs, explicit_response = _sens._select_dsa_response_specs(
        list(dsa_response_specs),
        field_prefix=field_prefix,
        response_component=response_component,
    )

    response_value_cache: Dict[Tuple[str, str, str, str, Optional[str], Optional[int], int, str], Dict[str, Any]] = {}
    row_order: List[str] = []
    row_meta_map: Dict[str, dict] = {}
    row_response_map: Dict[str, float] = {}
    column_meta_map: Dict[str, dict] = {}
    column_value_map: Dict[str, Dict[str, float]] = {}

    for instance_name in discovery["instances"]:
        for field_meta in discovery["per_instance"][instance_name]:
            source_field_name = str(field_meta["field"])
            selected_position = str(field_meta["position"])
            parameter_token = _sens._extract_dsa_field_token(field_prefix, source_field_name)

            direct_target_rows = list(dsa_direct_target_map.get(parameter_token, []))
            mapped_parameter_name = parameter_token
            token_match = _sens._DSA_PARAMETER_TOKEN_RE.fullmatch(parameter_token)
            token_index = int(token_match.group(2)) if token_match else None
            if not direct_target_rows and token_index is not None and token_index in dsa_design_parameter_name_map:
                mapped_parameter_name = dsa_design_parameter_name_map[token_index]
                direct_target_rows = list(dsa_direct_target_map.get(mapped_parameter_name, []))

            target_rows = direct_target_rows
            mapping_mode = "inp_parameter"
            if not target_rows:
                if not dsa_parameter_map:
                    raise ValidationError(
                        "unable to map DSA field to a model-update parameter",
                        {"field": source_field_name, "field_prefix": field_prefix},
                    )
                parameter_row = _sens._resolve_dsa_parameter_row(dsa_parameter_map, source_field_name)
                target_rows = [parameter_row]
                mapped_parameter_name = str(parameter_row.get("parameter_name") or parameter_token)
                mapping_mode = "optimization_parameter"

            # A DSA field may match multiple design responses in the INP. We keep the
            # candidate with the largest overlap of result labels.
            candidate_specs = list(selected_response_specs)

            best_score = -1
            best_specs = []
            best_sensitivity_map = None
            best_response_map = None
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
                    candidate_dsa_map = _sens._workspace_result_label_map(
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
                candidate_dsa_map = _sens._normalize_vtu_label_map(candidate_dsa_map)
                if not candidate_dsa_map:
                    continue

                response_field_meta = _sens._resolve_response_field_meta(
                    source_mode=source_mode,
                    workspace=resolved_workspace,
                    client=client,
                    odb_id=odb_id,
                    step=discovery["step"],
                    instance=instance_name,
                    field=candidate_field_name,
                )
                candidate_position = _sens._pick_response_position(response_field_meta, spec.get("preferred_position"))
                cache_key = (
                    str(instance_name),
                    str(discovery["step"]),
                    candidate_field_name,
                    candidate_position,
                    candidate_component,
                    candidate_component_index,
                    int(frame),
                    str(aggregation),
                )
                if cache_key not in response_value_cache:
                    if source_mode in {"workspace", "registry"}:
                        cached_response_map = _sens._workspace_result_label_map(
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
                    response_value_cache[cache_key] = _sens._normalize_vtu_label_map(cached_response_map)

                candidate_response_map = response_value_cache[cache_key]
                overlap = sorted(set(candidate_dsa_map.keys()) & set(candidate_response_map.keys()))
                score = len(overlap)
                if score <= 0:
                    continue
                if score > best_score:
                    best_score = score
                    best_specs = [spec]
                    best_sensitivity_map = candidate_dsa_map
                    best_response_map = candidate_response_map
                    best_position = candidate_position
                    best_response_field_meta = response_field_meta
                elif score == best_score:
                    best_specs.append(spec)

            if len(best_specs) > 1:
                raise ValidationError(
                    "multiple design responses match the requested DSA field",
                    {
                        "field_prefix": field_prefix,
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
            if not best_specs or best_sensitivity_map is None or best_response_map is None or best_position is None:
                raise ValidationError(
                    "unable to resolve a normalized design-response sensitivity map",
                    {"field": source_field_name, "instance": instance_name, "field_prefix": field_prefix},
                )

            chosen_response_spec = best_specs[0]
            response_field_name = str(chosen_response_spec["field_name"])
            resolved_response_component = (
                explicit_response["component"]
                if explicit_response is not None
                else chosen_response_spec.get("component")
            )
            if (
                explicit_response is None
                and resolved_response_component is None
                and best_response_field_meta is not None
            ):
                (
                    best_sensitivity_map,
                    best_response_map,
                    inferred_component,
                    _,
                ) = _sens._resolve_vector_design_response_component(
                    best_sensitivity_map,
                    best_response_map,
                    response_field_meta=best_response_field_meta,
                    response_field_name=response_field_name,
                    source_field_name=source_field_name,
                    instance_name=str(instance_name),
                )
                if inferred_component is not None:
                    resolved_response_component = inferred_component
            parameter_value = _sens._resolve_dsa_parameter_scalar_value(
                dsa_model,
                parameter_name=mapped_parameter_name,
                target_rows=target_rows,
            )

            existing_column_meta = column_meta_map.get(source_field_name)
            parameter_scatter = float(
                parameter_row.get("scatter", _sens._DEFAULT_PARAMETER_SCATTER)
                if mapping_mode == "optimization_parameter"
                else _sens._DEFAULT_PARAMETER_SCATTER
            )
            column_meta = {
                "field": source_field_name,
                "parameter_token": parameter_token,
                "parameter_name": mapped_parameter_name,
                "parameter_value": float(parameter_value),
                "mapping_mode": mapping_mode,
                "scatter": parameter_scatter,
                "element_mapping": _parameter_element_mapping_entry(
                    model=dsa_model,
                    source_field_name=source_field_name,
                    parameter_token=parameter_token,
                    mapped_parameter_name=mapped_parameter_name,
                    parameter_value=float(parameter_value),
                    target_rows=target_rows,
                    mapping_mode=mapping_mode,
                    scatter=parameter_scatter,
                ),
            }
            if existing_column_meta is not None and existing_column_meta != column_meta:
                raise ValidationError(
                    "inconsistent parameter metadata found across DSA result blocks",
                    {"field": source_field_name, "existing": existing_column_meta, "incoming": column_meta},
                )
            column_meta_map[source_field_name] = column_meta
            column_values = column_value_map.setdefault(source_field_name, {})

            matched_labels = sorted(set(best_sensitivity_map.keys()) & set(best_response_map.keys()))
            if not matched_labels:
                raise ValidationError(
                    "no overlapping response labels were found for DSA normalization",
                    {"source_field": source_field_name, "response_field": response_field_name, "instance": instance_name},
                )

            for response_label in matched_labels:
                row_key = _response_row_key(
                    instance=str(instance_name),
                    response_field=response_field_name,
                    response_component=resolved_response_component,
                    response_position=str(best_position),
                    response_label=str(response_label),
                )
                normalized_value = _sens._normalize_dsa_sensitivity_value(
                    best_sensitivity_map[response_label],
                    parameter_value=parameter_value,
                    response_value=best_response_map[response_label],
                    source_field=source_field_name,
                    response_field=response_field_name,
                )
                response_scalar = _scalarize(best_response_map[response_label], field=response_field_name, row_key=row_key)
                normalized_scalar = _scalarize(normalized_value, field=source_field_name, row_key=row_key)

                if row_key not in row_meta_map:
                    row_order.append(row_key)
                    row_meta_map[row_key] = {
                        "row_key": row_key,
                        "instance": str(instance_name),
                        "response_field": response_field_name,
                        "response_component": resolved_response_component,
                        "response_position": str(best_position),
                        "response_label": str(response_label),
                    }
                if row_key in row_response_map and not np.isclose(row_response_map[row_key], response_scalar):
                    raise ValidationError(
                        "inconsistent current response values found while building the normalized sensitivity matrix",
                        {
                            "row_key": row_key,
                            "existing_response_value": row_response_map[row_key],
                            "incoming_response_value": response_scalar,
                        },
                    )
                row_response_map[row_key] = response_scalar
                if row_key in column_values and not np.isclose(column_values[row_key], normalized_scalar):
                    raise ValidationError(
                        "conflicting normalized sensitivity values found while building the sensitivity matrix",
                        {
                            "row_key": row_key,
                            "field": source_field_name,
                            "existing_value": column_values[row_key],
                            "incoming_value": normalized_scalar,
                        },
                    )
                column_values[row_key] = normalized_scalar

    ordered_fields = _ordered_dsa_field_names(field_prefix, list(column_meta_map.keys()))
    ordered_rows = [row_meta_map[row_key] for row_key in row_order]
    ordered_columns = [column_meta_map[field_name] for field_name in ordered_fields]

    matrix = np.empty((len(ordered_rows), len(ordered_columns)), dtype=np.float64)
    for col_idx, field_name in enumerate(ordered_fields):
        values_for_field = column_value_map.get(field_name, {})
        for row_idx, row_meta in enumerate(ordered_rows):
            row_key = str(row_meta["row_key"])
            if row_key not in values_for_field:
                raise ValidationError(
                    "normalized sensitivity matrix is incomplete for the resolved response rows",
                    {"field": field_name, "missing_row": row_key},
                )
            matrix[row_idx, col_idx] = float(values_for_field[row_key])

    response_values = [float(row_response_map[str(item["row_key"])]) for item in ordered_rows]
    parameter_values = [float(item["parameter_value"]) for item in ordered_columns]

    return {
        "project_id": project_id,
        "odb_id": odb_id,
        "base_url": resolved_base_url if source_mode == "l3_api" else None,
        "workspace": resolved_workspace,
        "source_mode": source_mode,
        "workspace_built": workspace_built,
        "inp_path": resolved_inp_path,
        "step": discovery["step"],
        "instances": discovery["instances"],
        "frame": int(frame),
        "aggregation": aggregation,
        "field_prefix": field_prefix,
        "response_component": explicit_response["component"] if explicit_response is not None else None,
        "response_rows": ordered_rows,
        "parameter_columns": ordered_columns,
        "response_values": response_values,
        "parameter_values": parameter_values,
        "matrix": matrix.tolist(),
    }


def _copy_iteration_input(input_inp: str, output_dir: Path, iteration: int, *, base_stem: Optional[str] = None) -> Path:
    source_path = Path(input_inp).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_base_stem = str(base_stem or source_path.stem)
    copied_path = (output_dir / f"{resolved_base_stem}_iter{iteration}.inp").resolve()
    shutil.copyfile(str(source_path), str(copied_path))
    return copied_path


def _cleanup_iteration_solver_files(workdir: Path, job_name: str) -> List[str]:
    deleted: List[str] = []
    for suffix in _ITERATION_CLEANUP_SUFFIXES:
        path = (workdir / f"{job_name}{suffix}").resolve()
        if not path.exists() or not path.is_file():
            continue
        path.unlink()
        deleted.append(str(path))
    return deleted


def _run_iteration_solver(
    *,
    inp_path: Path,
    output_dir: Path,
    iteration: int,
    abaqus: str,
    job_name: Optional[str],
    cpus: Optional[int],
    interactive: bool,
    timeout_sec: Optional[int],
    extra_args: Optional[List[str]],
    python3: Optional[str],
    keep_raw: bool,
) -> dict:
    # Each Bayesian iteration solves the current INP first, then converts the new ODB
    # into a queryable workspace for sensitivity/result extraction.
    # Once the workspace has been built, the Abaqus process files are no longer
    # needed for the Bayesian loop, so they are removed to keep the work
    # directory small across many iterations.
    resolved_job_name = _solver._sanitize_job_name(job_name or inp_path.stem)
    command = _solver._build_abaqus_command(
        abaqus=abaqus,
        inp_path=inp_path,
        job_name=resolved_job_name,
        cpus=cpus,
        interactive=interactive,
        extra_args=extra_args,
    )
    solver_result = _solver._run_local_solver(
        command=command,
        workdir=inp_path.parent,
        artifact_stem=resolved_job_name,
        artifact_suffixes=_solver._ABAQUS_ARTIFACT_SUFFIXES,
        timeout_sec=timeout_sec,
    )
    if not solver_result.get("ok"):
        raise ValidationError(
            "abaqus sensitivity rerun failed during bayesian update",
            {"iteration": iteration, "solver": solver_result},
        )

    odb_path = solver_result.get("artifacts", {}).get("odb")
    if not odb_path:
        raise ValidationError(
            "solver completed without producing an odb artifact",
            {"iteration": iteration, "solver": solver_result},
        )

    workspace_dir = (output_dir / f"workspace_iter{iteration}").resolve()
    workspace_info = _sens.build_workspace_from_odb(
        odb_path=odb_path,
        workspace=str(workspace_dir),
        abaqus=abaqus,
        python3=python3,
        keep_raw=keep_raw,
    )
    deleted_process_files = _cleanup_iteration_solver_files(inp_path.parent, resolved_job_name)
    solver_result["deleted_process_files"] = deleted_process_files
    return {
        "job_name": resolved_job_name,
        "command_preview": command,
        "solver": solver_result,
        "workspace": workspace_info,
    }


def run_bayesian_update_workflow(
    *,
    project_id: int,
    input_inp: str,
    target_responses: Any,
    parameter_scatter: Any = None,
    response_scatter: Any = None,
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
    iterations: int = 1,
    damping: float = 1e-8,
    step_scale: float = 1.0,
    lower_bound: Any = None,
    upper_bound: Any = None,
    abaqus: str = "abaqus",
    python3: Optional[str] = None,
    keep_raw: bool = False,
    timeout: int = 60,
    job_name: Optional[str] = None,
    cpus: Optional[int] = None,
    interactive: bool = True,
    run_solver: bool = False,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> dict:
    if int(iterations) <= 0:
        raise ValidationError("iterations must be > 0", {"iterations": iterations})

    input_path = _solver._abs_file(input_inp, "input_inp")
    input_base_stem = input_path.stem
    root_dir = _solver._abs_dir(output_dir, input_path.parent / f"{input_path.stem}_bayesian")
    current_inp = _copy_iteration_input(str(input_path), root_dir, 0, base_stem=input_base_stem)

    has_initial_source = bool(workspace or odb_path or odb_id)
    if int(iterations) > 1 and not run_solver:
        raise ValidationError(
            "run_solver must be enabled when iterations > 1",
            {"iterations": iterations, "run_solver": run_solver},
        )
    if not has_initial_source and not run_solver:
        raise ValidationError(
            "workspace, odb_path, or odb_id is required when run_solver is disabled",
            {"workspace": workspace, "odb_path": odb_path, "odb_id": odb_id, "run_solver": run_solver},
        )

    iteration_results = []
    next_source = {
        "workspace": _normalize_optional_path(workspace),
        "odb_path": _normalize_optional_path(odb_path),
        "odb_id": odb_id,
        "base_url": base_url,
    }

    for iteration_index in range(int(iterations)):
        # If the caller did not provide an existing result source, iteration 0 must
        # solve the copied INP before the sensitivity matrix can be assembled.
        if iteration_index == 0 and not has_initial_source:
            solver_payload = _run_iteration_solver(
                inp_path=current_inp,
                output_dir=root_dir,
                iteration=iteration_index,
                abaqus=abaqus,
                job_name=f"{job_name}_iter{iteration_index}" if job_name else None,
                cpus=cpus,
                interactive=interactive,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
                python3=python3,
                keep_raw=keep_raw,
            )
            next_source = {
                "workspace": solver_payload["workspace"]["workspace"],
                "odb_path": None,
                "odb_id": None,
                "base_url": None,
            }
        else:
            solver_payload = None

        # Matrix construction always uses the current iteration INP so parameter
        # values and target-set mappings stay aligned with the file being updated.
        matrix_payload = build_dsa_normalized_sensitivity_matrix(
            project_id=project_id,
            odb_id=next_source.get("odb_id"),
            base_url=next_source.get("base_url"),
            inp_path=str(current_inp),
            workspace=next_source.get("workspace"),
            odb_path=next_source.get("odb_path"),
            workspace_root=str(root_dir),
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

        parameter_columns = list(matrix_payload["parameter_columns"])
        response_rows = list(matrix_payload["response_rows"])
        S_norm = np.asarray(matrix_payload["matrix"], dtype=np.float64)
        p_current = np.asarray(matrix_payload["parameter_values"], dtype=np.float64)
        r_model = np.asarray(matrix_payload["response_values"], dtype=np.float64)
        r_target = np.asarray(
            _vector_from_input(
                target_responses,
                response_rows,
                label="target_responses",
                key_candidates=("row_key", "response_label"),
            ),
            dtype=np.float64,
        )
        p_scatter = _resolve_scatter_vector(
            parameter_scatter,
            parameter_columns,
            label="parameter_scatter",
            key_candidates=("parameter_name", "field", "parameter_token"),
            default_value=_sens._DEFAULT_PARAMETER_SCATTER,
            metadata_key="scatter",
        )
        r_scatter = _resolve_scatter_vector(
            response_scatter,
            response_rows,
            label="response_scatter",
            key_candidates=("row_key", "response_label"),
            default_value=_sens._DEFAULT_RESPONSE_SCATTER,
        )
        lower_bound_values = None
        if lower_bound is not None:
            lower_bound_values = np.asarray(
                _vector_from_input(
                    lower_bound,
                    parameter_columns,
                    label="lower_bound",
                    key_candidates=("parameter_name", "field", "parameter_token"),
                ),
                dtype=np.float64,
            )
        upper_bound_values = None
        if upper_bound is not None:
            upper_bound_values = np.asarray(
                _vector_from_input(
                    upper_bound,
                    parameter_columns,
                    label="upper_bound",
                    key_candidates=("parameter_name", "field", "parameter_token"),
                ),
                dtype=np.float64,
            )

        update_payload = bayesian_update_normalized(
            p_current=p_current,
            r_model=r_model,
            r_target=r_target,
            S_norm=S_norm,
            p_scatter=p_scatter,
            r_scatter=r_scatter,
            damping=damping,
            step_scale=step_scale,
            lower_bound=lower_bound_values,
            upper_bound=upper_bound_values,
            p_ref=p_current,
        )

        parameter_updates = {
            str(column["parameter_name"]): float(update_payload["p_new"][col_idx])
            for col_idx, column in enumerate(parameter_columns)
        }
        parameter_element_mapping = []
        for col_idx, column in enumerate(parameter_columns):
            mapping_entry = _clone_jsonable(column.get("element_mapping") or {})
            mapping_entry["updated_parameter_value"] = float(update_payload["p_new"][col_idx])
            parameter_element_mapping.append(mapping_entry)
        next_inp = _copy_iteration_input(
            str(current_inp),
            root_dir,
            iteration_index + 1,
            base_stem=input_base_stem,
        )
        update_parameter_section_values(
            str(next_inp),
            parameter_updates,
            output_inp=str(next_inp),
        )

        iteration_result = {
            "iteration": iteration_index + 1,
            "input_inp": str(current_inp),
            "source": {
                "workspace": matrix_payload.get("workspace"),
                "source_mode": matrix_payload.get("source_mode"),
                "workspace_built": matrix_payload.get("workspace_built"),
                "odb_id": matrix_payload.get("odb_id"),
            },
            "sensitivity_matrix": matrix_payload["matrix"],
            "response_values": matrix_payload["response_values"],
            "target_responses": r_target.tolist(),
            "parameter_values": matrix_payload["parameter_values"],
            "parameter_scatter": p_scatter.tolist(),
            "response_scatter": r_scatter.tolist(),
            "parameter_columns": parameter_columns,
            "parameter_element_mapping": parameter_element_mapping,
            "response_rows": response_rows,
            "bayesian": _clone_jsonable(update_payload),
            "updated_inp": str(next_inp),
            "solver": solver_payload,
        }
        iteration_result["saved_artifacts"] = _save_iteration_artifacts(root_dir, iteration_result)
        iteration_results.append(iteration_result)

        current_inp = next_inp
        next_source = {"workspace": None, "odb_path": None, "odb_id": None, "base_url": None}
        if iteration_index < int(iterations) - 1:
            # For later iterations we always rerun the freshly updated INP instead of
            # reusing the previous workspace.
            rerun_payload = _run_iteration_solver(
                inp_path=current_inp,
                output_dir=root_dir,
                iteration=iteration_index + 1,
                abaqus=abaqus,
                job_name=f"{job_name}_iter{iteration_index + 1}" if job_name else None,
                cpus=cpus,
                interactive=interactive,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
                python3=python3,
                keep_raw=keep_raw,
            )
            iteration_result["next_iteration_solver"] = rerun_payload
            next_source["workspace"] = rerun_payload["workspace"]["workspace"]

    final_iteration = iteration_results[-1]
    return {
        "project_id": project_id,
        "input_inp": str(input_path),
        "output_dir": str(root_dir),
        "iterations": int(iterations),
        "field_prefix": field_prefix,
        "response_component": response_component,
        "step": step,
        "instances": [str(item) for item in (instances or [])],
        "final_updated_inp": final_iteration["updated_inp"],
        "final_parameter_values": final_iteration["bayesian"]["p_new"],
        "parameter_columns": final_iteration["parameter_columns"],
        "response_rows": final_iteration["response_rows"],
        "iteration_results": iteration_results,
    }


def run_bayesian_update_from_text(
    *,
    sensitivity_matrix_file: str,
    sensitivity_row_start: int,
    sensitivity_row_count: int,
    sensitivity_col_start: int = 1,
    model_response_file: str,
    model_response_row: int,
    model_response_col_start: int = 1,
    target_response_file: str,
    target_response_row: int,
    target_response_col_start: int = 1,
    parameter_names: List[str],
    parameter_scatter: Any = None,
    response_scatter: Any = None,
    input_inp: Optional[str] = None,
    parameter_values: Optional[Any] = None,
    damping: float = 1e-8,
    step_scale: float = 1.0,
    lower_bound: Any = None,
    upper_bound: Any = None,
    output_dir: Optional[str] = None,
    case_name: str = "bayesian_text_check",
) -> dict:
    S_norm = _read_text_matrix(
        sensitivity_matrix_file,
        row_start=int(sensitivity_row_start),
        row_count=int(sensitivity_row_count),
        col_start=int(sensitivity_col_start),
    )
    r_model = _read_text_row_vector(model_response_file, row=int(model_response_row), col_start=int(model_response_col_start))
    r_target = _read_text_row_vector(target_response_file, row=int(target_response_row), col_start=int(target_response_col_start))

    parameter_items = [
        {"parameter_name": str(name), "field": str(name), "parameter_token": str(name)}
        for name in parameter_names
    ]
    response_items = [{"row_key": f"r{i + 1}", "response_label": f"r{i + 1}"} for i in range(len(r_model))]

    if parameter_values is None:
        if not input_inp:
            raise ValidationError(
                "input_inp is required when parameter_values is not provided",
                {"input_inp": input_inp, "parameter_values": parameter_values},
            )
        p_current = _parameter_values_from_inp(input_inp, parameter_names)
    else:
        p_current = np.asarray(
            _vector_from_input(
                parameter_values,
                parameter_items,
                label="parameter_values",
                key_candidates=("parameter_name", "field", "parameter_token"),
            ),
            dtype=np.float64,
        )

    if len(r_target) != len(r_model):
        raise ValidationError(
            "target response vector length does not match model response length",
            {"target_len": len(r_target), "model_len": len(r_model)},
        )
    if S_norm.shape != (len(r_model), len(p_current)):
        raise ValidationError(
            "normalized sensitivity matrix shape does not match the supplied response and parameter sizes",
            {
                "matrix_shape": list(S_norm.shape),
                "response_count": len(r_model),
                "parameter_count": len(p_current),
            },
        )

    p_scatter = _resolve_scatter_vector(
        parameter_scatter,
        parameter_items,
        label="parameter_scatter",
        key_candidates=("parameter_name", "field", "parameter_token"),
        default_value=_sens._DEFAULT_PARAMETER_SCATTER,
    )
    r_scatter = _resolve_scatter_vector(
        response_scatter,
        response_items,
        label="response_scatter",
        key_candidates=("row_key", "response_label"),
        default_value=_sens._DEFAULT_RESPONSE_SCATTER,
    )
    lower_bound_values = None
    if lower_bound is not None:
        lower_bound_values = np.asarray(
            _vector_from_input(
                lower_bound,
                parameter_items,
                label="lower_bound",
                key_candidates=("parameter_name", "field", "parameter_token"),
            ),
            dtype=np.float64,
        )
    upper_bound_values = None
    if upper_bound is not None:
        upper_bound_values = np.asarray(
            _vector_from_input(
                upper_bound,
                parameter_items,
                label="upper_bound",
                key_candidates=("parameter_name", "field", "parameter_token"),
            ),
            dtype=np.float64,
        )

    update_payload = bayesian_update_normalized(
        p_current=p_current,
        r_model=r_model,
        r_target=r_target,
        S_norm=S_norm,
        p_scatter=p_scatter,
        r_scatter=r_scatter,
        damping=damping,
        step_scale=step_scale,
        lower_bound=lower_bound_values,
        upper_bound=upper_bound_values,
        p_ref=p_current,
    )

    result = {
        "case_name": str(case_name),
        "input_inp": _normalize_optional_path(input_inp),
        "sensitivity_matrix_file": os.path.abspath(sensitivity_matrix_file),
        "model_response_file": os.path.abspath(model_response_file),
        "target_response_file": os.path.abspath(target_response_file),
        "parameter_names": [str(name) for name in parameter_names],
        "parameter_values": p_current.tolist(),
        "parameter_scatter": p_scatter.tolist(),
        "response_scatter": r_scatter.tolist(),
        "sensitivity_matrix": S_norm.tolist(),
        "model_response": r_model.tolist(),
        "target_response": r_target.tolist(),
        "bayesian": _clone_jsonable(update_payload),
    }

    if output_dir:
        output_root = _solver._abs_dir(output_dir, Path.cwd() / str(case_name))
        saved = {
            "summary_json": _save_json(output_root / "bayesian_text_check.json", result),
            "sensitivity_matrix_txt": _save_matrix_txt(output_root / "sensitivity_matrix.txt", S_norm.tolist()),
            "model_response_txt": _save_vector_txt(output_root / "model_response.txt", r_model.tolist()),
            "target_response_txt": _save_vector_txt(output_root / "target_response.txt", r_target.tolist()),
            "parameter_values_txt": _save_vector_txt(output_root / "parameter_values.txt", p_current.tolist()),
            "updated_parameter_values_txt": _save_vector_txt(
                output_root / "updated_parameter_values.txt",
                result["bayesian"]["p_new"],
            ),
        }
        result["saved_artifacts"] = {"output_dir": str(output_root), "files": saved}

    return result
