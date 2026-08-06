from typing import Any, Dict, Optional, Sequence

import numpy as np

from src.inp import parse_inp
from src.l3.core.errors import ValidationError


def _read_text_matrix(file_path: str, row_start: int, row_count: int, col_start: int = 1) -> np.ndarray:
    values = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
        lines = handle.readlines()
    for row_index in range(int(row_start) - 1, int(row_start) - 1 + int(row_count)):
        if row_index < 0 or row_index >= len(lines):
            raise ValueError(f"row {row_index + 1} out of range for {file_path}")
        stripped = lines[row_index].strip()
        if not stripped:
            values.append([])
            continue
        tokens = stripped.split()
        start_idx = max(int(col_start) - 1, 0)
        values.append([float(token) for token in tokens[start_idx:]])
    return np.asarray(values, dtype=np.float64)


def _read_text_row_vector(file_path: str, row: int, col_start: int = 1) -> np.ndarray:
    return _read_text_matrix(file_path, row_start=row, row_count=1, col_start=col_start).reshape(-1)


def _parameter_values_from_inp(input_inp: str, parameter_names: Sequence[str]) -> np.ndarray:
    model = parse_inp(input_inp)
    lookup = {str(item.name): float(item.value) for item in model.parameters}
    values = []
    for name in parameter_names:
        if str(name) not in lookup:
            raise ValidationError(
                "parameter name was not found in the input file",
                {"input_inp": input_inp, "parameter_name": str(name)},
            )
        values.append(float(lookup[str(name)]))
    return np.asarray(values, dtype=np.float64)


def _expand_scatter_vector(scatter: Any, expected_size: int, *, label: str) -> np.ndarray:
    if np.isscalar(scatter):
        return np.full(int(expected_size), float(scatter), dtype=np.float64)
    arr = np.asarray(scatter, dtype=np.float64).reshape(-1)
    if arr.size != int(expected_size):
        raise ValidationError(
            f"{label} size does not match the expected vector size",
            {"label": label, "expected_size": int(expected_size), "actual_size": int(arr.size)},
        )
    return arr


def build_normalized_residual(
        *,
        r_model: Sequence[float],
        r_target: Sequence[float],
        eps: float = 1e-12,
) -> np.ndarray:
    r_model_arr = np.asarray(r_model, dtype=float).reshape(-1)
    r_target_arr = np.asarray(r_target, dtype=float).reshape(-1)
    if r_model_arr.shape != r_target_arr.shape:
        raise ValidationError(
            "r_model and r_target must have the same shape",
            {"r_model_shape": list(r_model_arr.shape), "r_target_shape": list(r_target_arr.shape)},
        )
    denom = np.maximum(np.abs(r_target_arr), float(eps))
    return ((r_model_arr - r_target_arr) / denom).reshape(-1, 1)


def _build_normalized_gain_matrix(
        *,
        S_norm: np.ndarray,
        p_scatter: Sequence[float],
        r_scatter: Sequence[float],
        damping: float,
        eps: float,
) -> Dict[str, np.ndarray]:
    S_norm_arr = np.asarray(S_norm, dtype=float)
    if S_norm_arr.ndim != 2:
        raise ValidationError("S_norm must be a 2D matrix", {"shape": list(S_norm_arr.shape)})

    n_response, n_param = S_norm_arr.shape
    p_scatter_arr = _expand_scatter_vector(p_scatter, n_param, label="p_scatter")
    r_scatter_arr = _expand_scatter_vector(r_scatter, n_response, label="r_scatter")

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
    from pathlib import Path

    def _update_parameter_file(file_path: Path, visited: set[Path], seen: set[str]) -> None:
        resolved_path = file_path.expanduser().resolve()
        if resolved_path in visited or not resolved_path.exists():
            return
        visited.add(resolved_path)

        lines = resolved_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        updated_lines = []
        inside_parameter_block = False

        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith("*PARAMETER"):
                inside_parameter_block = True
                updated_lines.append(line)
                continue
            if inside_parameter_block and stripped.startswith("*"):
                inside_parameter_block = False
            if inside_parameter_block and "=" in line:
                left, right = line.split("=", 1)
                name = left.strip()
                if name in parameter_values:
                    updated_lines.append(f"{name} = {format(float(parameter_values[name]), '.12g')}")
                    seen.add(name)
                    continue
            if upper.startswith("*INCLUDE") and "INPUT" in upper:
                updated_lines.append(line)
                include_path = line.split("=", 1)[1].strip().strip("\"'")
                _update_parameter_file((resolved_path.parent / include_path).resolve(), visited, seen)
                continue
            updated_lines.append(line)

        resolved_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    input_path = Path(input_inp).expanduser().resolve()
    output_path = Path(output_inp).expanduser().resolve() if output_inp else input_path
    if input_path != output_path:
        output_path.write_text(input_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    seen = set()
    _update_parameter_file(output_path, set(), seen)
    missing = sorted(name for name in parameter_values.keys() if name not in seen)
    return {
        "input_inp": str(input_path),
        "output_inp": str(output_path),
        "updated_names": sorted(seen),
        "missing_names": missing,
    }
