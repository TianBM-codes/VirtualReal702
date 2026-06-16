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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.model_update.analysis.nastran_sol200_service import generate_sol200_workflow


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _load_json(path: str) -> Dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    with resolved.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise RuntimeError(f"config must be a JSON object: {resolved}")
    return payload


def _write_json(path: str, payload: Dict[str, Any]) -> str:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(_json_dumps(payload), encoding="utf-8")
    return str(resolved)


def _array_from_inline_or_file(spec: Any, *, name: str, expected_ndim: Optional[int] = None) -> np.ndarray:
    if isinstance(spec, (list, tuple)):
        arr = np.asarray(spec, dtype=np.float64)
    elif isinstance(spec, dict) and spec.get("path"):
        path = Path(str(spec["path"])).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"{name} file not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".npy":
            arr = np.load(path)
        elif suffix == ".npz":
            key = str(spec.get("key") or "").strip()
            if not key:
                raise RuntimeError(f"{name} npz input requires key: {path}")
            with np.load(path) as npz:
                if key not in npz:
                    raise RuntimeError(f"{name} npz key not found: {key}")
                arr = npz[key]
        elif suffix in {".json"}:
            arr = np.asarray(json.loads(path.read_text(encoding="utf-8")), dtype=np.float64)
        elif suffix in {".csv", ".txt"}:
            delimiter = spec.get("delimiter")
            arr = np.loadtxt(path, delimiter=delimiter if delimiter not in ("", None) else None, dtype=np.float64)
        else:
            raise RuntimeError(f"unsupported {name} file format: {path}")
    else:
        raise RuntimeError(f"{name} must be a nested list or a {{path: ...}} object")

    if expected_ndim is not None and arr.ndim != expected_ndim:
        raise RuntimeError(f"{name} must be a {expected_ndim}D array, got shape {tuple(arr.shape)}")
    return np.asarray(arr, dtype=np.float64)


def _normalize_pairs(raw_pairs: Sequence[Any], *, index_base: int) -> List[Tuple[int, int]]:
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
            raise RuntimeError(f"invalid pair entry: {item!r}")
        if exp_idx is None or sim_idx is None:
            raise RuntimeError(f"pair is missing exp/sim mode indices: {item!r}")
        pairs.append((int(exp_idx) - index_base, int(sim_idx) - index_base))
    if not pairs:
        raise RuntimeError("pairs must not be empty")
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
            raise RuntimeError(
                f"mode vector norm is too small for pair (exp={exp_idx}, sim={sim_idx}); "
                f"b_i={b_i}, c_j={c_j}"
            )

        g_ij = (2.0 * a_ij / (b_i * c_j)) * (psi_exp - (a_ij / c_j) * psi_sim)
        dmac_dp[pair_index, :] = g_ij @ dphi_dp[sim_idx]
        g_vectors.append(g_ij)

    return dmac_dp, g_vectors


def _summarize_pair_results(
    *,
    pairs: Sequence[Tuple[int, int]],
    mac_matrix: np.ndarray,
    dmac_dp: np.ndarray,
    g_vectors: Sequence[np.ndarray],
    parameter_names: Sequence[str],
    index_base: int,
) -> List[dict]:
    rows: List[dict] = []
    for pair_index, (exp_idx, sim_idx) in enumerate(list(pairs or [])):
        gradient = np.asarray(dmac_dp[pair_index], dtype=np.float64).reshape(-1)
        rows.append(
            {
                "pair_index": int(pair_index),
                "exp_mode": int(exp_idx + index_base),
                "sim_mode": int(sim_idx + index_base),
                "mac": float(mac_matrix[exp_idx, sim_idx]),
                "dmac_dp": gradient.tolist(),
                "dmac_by_parameter": {
                    str(parameter_names[param_index]): float(value)
                    for param_index, value in enumerate(gradient.tolist())
                },
                "g_vector": np.asarray(g_vectors[pair_index], dtype=np.float64).tolist(),
            }
        )
    return rows


def run_mac_sensitivity_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    phi_exp = _array_from_inline_or_file(payload.get("phi_exp"), name="phi_exp", expected_ndim=2)
    phi_sim = _array_from_inline_or_file(payload.get("phi_sim"), name="phi_sim", expected_ndim=2)
    dphi_dp = _array_from_inline_or_file(payload.get("dphi_dp"), name="dphi_dp", expected_ndim=3)

    index_base = int(payload.get("index_base", 1))
    if index_base not in {0, 1}:
        raise RuntimeError(f"index_base must be 0 or 1, got {index_base}")
    pairs = _normalize_pairs(payload.get("pairs") or [], index_base=index_base)

    if phi_exp.shape[0] != phi_sim.shape[0]:
        raise RuntimeError(f"phi_exp and phi_sim sensor dimension mismatch: {phi_exp.shape} vs {phi_sim.shape}")
    if dphi_dp.shape[1] != phi_sim.shape[0]:
        raise RuntimeError(f"dphi_dp sensor dimension mismatch: {dphi_dp.shape} vs phi_sim {phi_sim.shape}")
    if dphi_dp.shape[0] != phi_sim.shape[1]:
        raise RuntimeError(f"dphi_dp mode dimension mismatch: {dphi_dp.shape} vs phi_sim {phi_sim.shape}")

    parameter_names = [str(item) for item in list(payload.get("parameter_names") or [])]
    if parameter_names and len(parameter_names) != dphi_dp.shape[2]:
        raise RuntimeError(
            f"parameter_names count mismatch: {len(parameter_names)} vs dphi_dp.shape[2]={dphi_dp.shape[2]}"
        )
    if not parameter_names:
        parameter_names = [f"p{idx + index_base}" for idx in range(dphi_dp.shape[2])]

    for exp_idx, sim_idx in pairs:
        if exp_idx < 0 or exp_idx >= phi_exp.shape[1]:
            raise RuntimeError(f"exp pair index out of range: {exp_idx + index_base}")
        if sim_idx < 0 or sim_idx >= phi_sim.shape[1]:
            raise RuntimeError(f"sim pair index out of range: {sim_idx + index_base}")

    phi_sim_aligned, dphi_dp_aligned, sign_flips = align_mode_signs(phi_exp, phi_sim, dphi_dp, pairs)
    mac_matrix = compute_mac_matrix(phi_exp, phi_sim_aligned)
    dmac_dp, g_vectors = compute_mac_sensitivity(phi_exp, phi_sim_aligned, dphi_dp_aligned, pairs)

    pair_results = _summarize_pair_results(
        pairs=pairs,
        mac_matrix=mac_matrix,
        dmac_dp=dmac_dp,
        g_vectors=g_vectors,
        parameter_names=parameter_names,
        index_base=index_base,
    )

    return {
        "workflow": "compute_mac_sensitivity",
        "index_base": int(index_base),
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
        "mac_matrix": np.asarray(mac_matrix, dtype=np.float64).tolist(),
        "pair_results": pair_results,
        "phi_sim_aligned": np.asarray(phi_sim_aligned, dtype=np.float64).tolist(),
        "dphi_dp_aligned": np.asarray(dphi_dp_aligned, dtype=np.float64).tolist(),
    }


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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = _load_json(args.config)

    if args.command == "build-sol200-bdf":
        result = run_build_sol200_bdf(payload)
    elif args.command == "compute-mac-sensitivity":
        result = run_mac_sensitivity_from_payload(payload)
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
