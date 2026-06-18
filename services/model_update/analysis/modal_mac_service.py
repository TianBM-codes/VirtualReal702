from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.l3.core.errors import ValidationError


DEFAULT_MAC_SCALE = 100.0


def _validation_error(message: str, details: Optional[Dict[str, Any]] = None) -> ValidationError:
    return ValidationError(message, details or {})


def array_from_inline_or_file(spec: Any, *, name: str, expected_ndim: Optional[int] = None) -> np.ndarray:
    if isinstance(spec, (list, tuple)):
        arr = np.asarray(spec, dtype=np.float64)
    elif isinstance(spec, dict) and spec.get("path"):
        path = Path(str(spec["path"])).expanduser().resolve()
        if not path.exists():
            raise _validation_error(f"{name} file not found", {"name": name, "path": str(path)})
        suffix = path.suffix.lower()
        if suffix == ".npy":
            arr = np.load(path)
        elif suffix == ".npz":
            key = str(spec.get("key") or "").strip()
            if not key:
                raise _validation_error(f"{name} npz input requires key", {"name": name, "path": str(path)})
            with np.load(path) as npz:
                if key not in npz:
                    raise _validation_error(
                        f"{name} npz key not found",
                        {"name": name, "path": str(path), "key": key},
                    )
                arr = npz[key]
        elif suffix == ".json":
            arr = np.asarray(json.loads(path.read_text(encoding="utf-8-sig")), dtype=np.float64)
        elif suffix in {".csv", ".txt"}:
            delimiter = spec.get("delimiter")
            arr = np.loadtxt(path, delimiter=delimiter if delimiter not in ("", None) else None, dtype=np.float64)
        else:
            raise _validation_error(
                f"unsupported {name} file format",
                {"name": name, "path": str(path), "suffix": suffix},
            )
    else:
        raise _validation_error(
            f"{name} must be a nested list or a {{path: ...}} object",
            {"name": name, "actual_type": type(spec).__name__},
        )

    if expected_ndim is not None and arr.ndim != expected_ndim:
        raise _validation_error(
            f"{name} must be a {expected_ndim}D array",
            {"name": name, "shape": list(arr.shape), "expected_ndim": expected_ndim},
        )
    return np.asarray(arr, dtype=np.float64)


def normalize_pairs(raw_pairs: Sequence[Any], *, index_base: int) -> List[Tuple[int, int]]:
    pairs: List[Tuple[int, int]] = []
    for item in list(raw_pairs or []):
        if isinstance(item, dict):
            exp_idx = item.get("exp_mode")
            if exp_idx is None:
                exp_idx = item.get("exp_index")
            sim_idx = item.get("sim_mode")
            if sim_idx is None:
                sim_idx = item.get("sim_index")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            exp_idx = item[0]
            sim_idx = item[1]
        else:
            raise _validation_error("invalid pair entry", {"pair": item})
        if exp_idx is None or sim_idx is None:
            raise _validation_error("pair is missing exp/sim mode indices", {"pair": item})
        pairs.append((int(exp_idx) - index_base, int(sim_idx) - index_base))
    if not pairs:
        raise _validation_error("pairs must not be empty")
    return pairs


def compute_mac_matrix(phi_exp: np.ndarray, phi_sim: np.ndarray) -> np.ndarray:
    numerator = (phi_exp.T @ phi_sim) ** 2
    norm_exp = np.sum(phi_exp ** 2, axis=0)
    norm_sim = np.sum(phi_sim ** 2, axis=0)
    denominator = np.outer(norm_exp, norm_sim)
    return numerator / np.maximum(denominator, 1.0e-30)


def align_mode_signs(
    phi_exp: np.ndarray,
    phi_sim: np.ndarray,
    dphi_dp: np.ndarray,
    pairs: Sequence[Tuple[int, int]],
) -> Tuple[np.ndarray, np.ndarray, List[dict]]:
    phi_sim_aligned = np.asarray(phi_sim, dtype=np.float64).copy()
    dphi_dp_aligned = np.asarray(dphi_dp, dtype=np.float64).copy()
    flips: List[dict] = []

    for exp_idx, sim_idx in list(pairs or []):
        cross = float(phi_exp[:, exp_idx] @ phi_sim_aligned[:, sim_idx])
        flipped = False
        if cross < 0.0:
            phi_sim_aligned[:, sim_idx] *= -1.0
            dphi_dp_aligned[sim_idx, :, :] *= -1.0
            flipped = True
            cross = -cross
        flips.append(
            {
                "exp_mode_index": int(exp_idx),
                "sim_mode_index": int(sim_idx),
                "flipped": bool(flipped),
                "aligned_cross": float(cross),
            }
        )
    return phi_sim_aligned, dphi_dp_aligned, flips


def compute_mac_sensitivity(
    phi_exp: np.ndarray,
    phi_sim: np.ndarray,
    dphi_dp: np.ndarray,
    pairs: Sequence[Tuple[int, int]],
) -> Tuple[np.ndarray, List[np.ndarray]]:
    n_pairs = len(list(pairs or []))
    n_param = int(dphi_dp.shape[2])
    dmac_dp = np.zeros((n_pairs, n_param), dtype=np.float64)
    g_vectors: List[np.ndarray] = []

    for pair_index, (exp_idx, sim_idx) in enumerate(list(pairs or [])):
        psi_exp = phi_exp[:, exp_idx]
        psi_sim = phi_sim[:, sim_idx]

        a_ij = float(psi_exp @ psi_sim)
        b_i = float(psi_exp @ psi_exp)
        c_j = float(psi_sim @ psi_sim)
        if abs(b_i) <= 1.0e-30 or abs(c_j) <= 1.0e-30:
            raise _validation_error(
                "mode vector norm is too small for MAC sensitivity",
                {"exp_mode_index": int(exp_idx), "sim_mode_index": int(sim_idx), "b_i": b_i, "c_j": c_j},
            )

        g_ij = (2.0 * a_ij / (b_i * c_j)) * (psi_exp - (a_ij / c_j) * psi_sim)
        dmac_dp[pair_index, :] = g_ij @ dphi_dp[sim_idx]
        g_vectors.append(g_ij)

    return dmac_dp, g_vectors


def _summarize_pair_results(
    *,
    pairs: Sequence[Tuple[int, int]],
    mac_matrix_raw: np.ndarray,
    dmac_dp_raw: np.ndarray,
    g_vectors: Sequence[np.ndarray],
    parameter_names: Sequence[str],
    index_base: int,
    mac_scale: float,
) -> List[dict]:
    rows: List[dict] = []
    for pair_index, (exp_idx, sim_idx) in enumerate(list(pairs or [])):
        gradient_raw = np.asarray(dmac_dp_raw[pair_index], dtype=np.float64).reshape(-1)
        gradient_scaled = gradient_raw * float(mac_scale)
        rows.append(
            {
                "pair_index": int(pair_index),
                "exp_mode": int(exp_idx + index_base),
                "sim_mode": int(sim_idx + index_base),
                "mac": float(mac_matrix_raw[exp_idx, sim_idx] * float(mac_scale)),
                "mac_raw": float(mac_matrix_raw[exp_idx, sim_idx]),
                "dmac_dp": gradient_scaled.tolist(),
                "dmac_dp_raw": gradient_raw.tolist(),
                "dmac_by_parameter": {
                    str(parameter_names[param_index]): float(value)
                    for param_index, value in enumerate(gradient_scaled.tolist())
                },
                "dmac_by_parameter_raw": {
                    str(parameter_names[param_index]): float(value)
                    for param_index, value in enumerate(gradient_raw.tolist())
                },
                "g_vector": np.asarray(g_vectors[pair_index], dtype=np.float64).tolist(),
            }
        )
    return rows


def run_modal_mac_sensitivity(payload: Dict[str, Any]) -> Dict[str, Any]:
    phi_exp = array_from_inline_or_file(payload.get("phi_exp"), name="phi_exp", expected_ndim=2)
    phi_sim = array_from_inline_or_file(payload.get("phi_sim"), name="phi_sim", expected_ndim=2)
    dphi_dp = array_from_inline_or_file(payload.get("dphi_dp"), name="dphi_dp", expected_ndim=3)

    index_base = int(payload.get("index_base", 1))
    if index_base not in {0, 1}:
        raise _validation_error("index_base must be 0 or 1", {"index_base": index_base})
    mac_scale = float(payload.get("mac_scale", DEFAULT_MAC_SCALE))
    pairs = normalize_pairs(payload.get("pairs") or [], index_base=index_base)

    if phi_exp.shape[0] != phi_sim.shape[0]:
        raise _validation_error(
            "phi_exp and phi_sim sensor dimension mismatch",
            {"phi_exp_shape": list(phi_exp.shape), "phi_sim_shape": list(phi_sim.shape)},
        )
    if dphi_dp.shape[1] != phi_sim.shape[0]:
        raise _validation_error(
            "dphi_dp sensor dimension mismatch",
            {"dphi_dp_shape": list(dphi_dp.shape), "phi_sim_shape": list(phi_sim.shape)},
        )
    if dphi_dp.shape[0] != phi_sim.shape[1]:
        raise _validation_error(
            "dphi_dp mode dimension mismatch",
            {"dphi_dp_shape": list(dphi_dp.shape), "phi_sim_shape": list(phi_sim.shape)},
        )

    parameter_names = [str(item) for item in list(payload.get("parameter_names") or [])]
    if parameter_names and len(parameter_names) != dphi_dp.shape[2]:
        raise _validation_error(
            "parameter_names count mismatch",
            {"parameter_names_count": len(parameter_names), "dphi_dp_param_count": int(dphi_dp.shape[2])},
        )
    if not parameter_names:
        parameter_names = [f"p{idx + index_base}" for idx in range(dphi_dp.shape[2])]

    for exp_idx, sim_idx in pairs:
        if exp_idx < 0 or exp_idx >= phi_exp.shape[1]:
            raise _validation_error(
                "exp pair index out of range",
                {"exp_mode": int(exp_idx + index_base), "phi_exp_mode_count": int(phi_exp.shape[1])},
            )
        if sim_idx < 0 or sim_idx >= phi_sim.shape[1]:
            raise _validation_error(
                "sim pair index out of range",
                {"sim_mode": int(sim_idx + index_base), "phi_sim_mode_count": int(phi_sim.shape[1])},
            )

    phi_sim_aligned, dphi_dp_aligned, sign_flips = align_mode_signs(phi_exp, phi_sim, dphi_dp, pairs)
    mac_matrix_raw = compute_mac_matrix(phi_exp, phi_sim_aligned)
    dmac_dp_raw, g_vectors = compute_mac_sensitivity(phi_exp, phi_sim_aligned, dphi_dp_aligned, pairs)
    pair_results = _summarize_pair_results(
        pairs=pairs,
        mac_matrix_raw=mac_matrix_raw,
        dmac_dp_raw=dmac_dp_raw,
        g_vectors=g_vectors,
        parameter_names=parameter_names,
        index_base=index_base,
        mac_scale=mac_scale,
    )

    return {
        "workflow": "compute_modal_mac_sensitivity",
        "index_base": int(index_base),
        "mac_scale": float(mac_scale),
        "mac_unit": "percent" if abs(mac_scale - 100.0) <= 1.0e-12 else "scaled",
        "shape": {
            "phi_exp": list(phi_exp.shape),
            "phi_sim": list(phi_sim.shape),
            "dphi_dp": list(dphi_dp.shape),
        },
        "parameter_names": parameter_names,
        "sensor_labels": list(payload.get("sensor_labels") or []),
        "pairs": [
            {"exp_mode": int(exp_idx + index_base), "sim_mode": int(sim_idx + index_base)}
            for exp_idx, sim_idx in pairs
        ],
        "sign_flips": sign_flips,
        "mac_matrix": np.asarray(mac_matrix_raw * float(mac_scale), dtype=np.float64).tolist(),
        "mac_matrix_raw": np.asarray(mac_matrix_raw, dtype=np.float64).tolist(),
        "pair_results": pair_results,
        "phi_sim_aligned": np.asarray(phi_sim_aligned, dtype=np.float64).tolist(),
        "dphi_dp_aligned": np.asarray(dphi_dp_aligned, dtype=np.float64).tolist(),
    }
