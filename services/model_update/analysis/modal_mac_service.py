from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from db import get_connection
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


def _normalize_response_type(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_component_token(value: Any) -> str:
    token = str(value or "").strip().upper()
    mapping = {
        "1": "U1",
        "2": "U2",
        "3": "U3",
        "X": "U1",
        "Y": "U2",
        "Z": "U3",
        "UX": "U1",
        "UY": "U2",
        "UZ": "U3",
        "U1": "U1",
        "U2": "U2",
        "U3": "U3",
    }
    resolved = mapping.get(token)
    if not resolved:
        raise _validation_error(
            "unsupported modal displacement component",
            {"component": value, "allowed": ["U1", "U2", "U3", "UX", "UY", "UZ", "1", "2", "3"]},
        )
    return resolved


def _load_project_node_matches(project_id: int) -> List[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id, instance_name, fem_node_label
            """,
            (int(project_id),),
        )
        return [dict(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def _load_project_modal_vectors(project_id: int) -> Tuple[Dict[int, Dict[str, np.ndarray]], Dict[int, Dict[Tuple[str, int], np.ndarray]]]:
    from .fem_correlation_service import _load_fem_mode_vectors, _load_test_mode_vectors

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        test_modes = _load_test_mode_vectors(cursor, int(project_id))
        fem_modes, _ = _load_fem_mode_vectors(cursor, int(project_id))
        return test_modes, fem_modes
    finally:
        cursor.close()
        conn.close()


def _build_project_modal_mac_vectors_from_maps(
    *,
    project_id: int,
    test_mode_map: Dict[str, np.ndarray],
    fem_mode_map: Dict[Tuple[str, int], np.ndarray],
    test_mode_no: Optional[int] = None,
    fem_mode_no: Optional[int] = None,
) -> Dict[str, Any]:
    node_matches = _load_project_node_matches(int(project_id))
    if not node_matches:
        raise _validation_error(
            "node matches are required before building project MAC vectors",
            {"project_id": int(project_id)},
        )

    phi_exp_values: List[float] = []
    phi_sim_values: List[float] = []
    sensor_labels: List[str] = []
    node_pairs: List[dict] = []

    for row in node_matches:
        test_node_id = str(row.get("test_node_id") or "").strip()
        instance_name = str(row.get("instance_name") or "").strip()
        fem_node_label = row.get("fem_node_label")
        if not test_node_id or fem_node_label is None:
            continue
        test_vector = test_mode_map.get(test_node_id)
        fem_vector = fem_mode_map.get((instance_name, int(fem_node_label)))
        if test_vector is None or fem_vector is None:
            continue
        test_arr = np.asarray(test_vector, dtype=np.complex128).reshape(3)
        fem_arr = np.asarray(fem_vector, dtype=np.float64).reshape(3)
        for comp_index, comp_name in enumerate(("U1", "U2", "U3")):
            phi_exp_values.append(float(np.real(test_arr[comp_index])))
            phi_sim_values.append(float(fem_arr[comp_index]))
            sensor_labels.append(f"{test_node_id}:{comp_name}")
        node_pairs.append(
            {
                "test_node_id": test_node_id,
                "instance_name": instance_name,
                "fem_node_label": int(fem_node_label),
            }
        )

    if not node_pairs:
        raise _validation_error(
            "no overlapping node-match modal vectors were found",
            {
                "project_id": int(project_id),
                "test_mode_no": int(test_mode_no) if test_mode_no is not None else None,
                "fem_mode_no": int(fem_mode_no) if fem_mode_no is not None else None,
            },
        )

    phi_exp = np.asarray(phi_exp_values, dtype=np.float64).reshape(-1, 1)
    phi_sim = np.asarray(phi_sim_values, dtype=np.float64).reshape(-1, 1)
    return {
        "phi_exp": phi_exp,
        "phi_sim": phi_sim,
        "sensor_labels": sensor_labels,
        "node_pairs": node_pairs,
    }


def build_project_modal_mac_vectors(
    *,
    project_id: int,
    test_mode_no: int,
    fem_mode_no: int,
) -> Dict[str, Any]:
    test_modes, fem_modes = _load_project_modal_vectors(int(project_id))
    test_mode_map = dict(test_modes.get(int(test_mode_no)) or {})
    fem_mode_map = dict(fem_modes.get(int(fem_mode_no)) or {})
    if not test_mode_map:
        raise _validation_error(
            "test mode not found for project MAC computation",
            {"project_id": int(project_id), "test_mode_no": int(test_mode_no)},
        )
    if not fem_mode_map:
        raise _validation_error(
            "fem mode not found for project MAC computation",
            {"project_id": int(project_id), "fem_mode_no": int(fem_mode_no)},
        )
    return _build_project_modal_mac_vectors_from_maps(
        project_id=int(project_id),
        test_mode_map=test_mode_map,
        fem_mode_map=fem_mode_map,
        test_mode_no=int(test_mode_no),
        fem_mode_no=int(fem_mode_no),
    )


def compute_project_modal_mac(
    *,
    project_id: int,
    test_mode_no: int,
    fem_mode_no: int,
    mac_scale: float = DEFAULT_MAC_SCALE,
) -> Dict[str, Any]:
    vectors = build_project_modal_mac_vectors(
        project_id=int(project_id),
        test_mode_no=int(test_mode_no),
        fem_mode_no=int(fem_mode_no),
    )
    payload = run_modal_mac_sensitivity(
        {
            "phi_exp": vectors["phi_exp"].tolist(),
            "phi_sim": vectors["phi_sim"].tolist(),
            "dphi_dp": np.zeros((1, vectors["phi_sim"].shape[0], 1), dtype=np.float64).tolist(),
            "pairs": [{"exp_mode": 1, "sim_mode": 1}],
            "index_base": 1,
            "parameter_names": ["dummy"],
            "sensor_labels": list(vectors["sensor_labels"]),
            "mac_scale": float(mac_scale),
        }
    )
    pair_result = dict((payload.get("pair_results") or [{}])[0] or {})
    return {
        **vectors,
        "mac": float(pair_result.get("mac")),
        "mac_raw": float(pair_result.get("mac_raw")),
        "sign_flips": payload.get("sign_flips") or [],
        "phi_sim_aligned": np.asarray(payload.get("phi_sim_aligned") or [], dtype=np.float64),
    }


def compute_project_modal_mac_from_fem_mode_map(
    *,
    project_id: int,
    test_mode_no: int,
    fem_mode_map: Dict[Tuple[str, int], np.ndarray],
    mac_scale: float = DEFAULT_MAC_SCALE,
) -> Dict[str, Any]:
    test_modes, _ = _load_project_modal_vectors(int(project_id))
    test_mode_map = dict(test_modes.get(int(test_mode_no)) or {})
    if not test_mode_map:
        raise _validation_error(
            "test mode not found for project MAC computation",
            {"project_id": int(project_id), "test_mode_no": int(test_mode_no)},
        )
    vectors = _build_project_modal_mac_vectors_from_maps(
        project_id=int(project_id),
        test_mode_map=test_mode_map,
        fem_mode_map=dict(fem_mode_map or {}),
        test_mode_no=int(test_mode_no),
    )
    payload = run_modal_mac_sensitivity(
        {
            "phi_exp": vectors["phi_exp"].tolist(),
            "phi_sim": vectors["phi_sim"].tolist(),
            "dphi_dp": np.zeros((1, vectors["phi_sim"].shape[0], 1), dtype=np.float64).tolist(),
            "pairs": [{"exp_mode": 1, "sim_mode": 1}],
            "index_base": 1,
            "parameter_names": ["dummy"],
            "sensor_labels": list(vectors["sensor_labels"]),
            "mac_scale": float(mac_scale),
        }
    )
    pair_result = dict((payload.get("pair_results") or [{}])[0] or {})
    return {
        **vectors,
        "mac": float(pair_result.get("mac")),
        "mac_raw": float(pair_result.get("mac_raw")),
        "sign_flips": payload.get("sign_flips") or [],
        "phi_sim_aligned": np.asarray(payload.get("phi_sim_aligned") or [], dtype=np.float64),
    }


def expand_modal_mac_responses_to_sol200_displacements(
    *,
    project_id: int,
    response_rows: Sequence[dict],
) -> Dict[str, Any]:
    node_matches = _load_project_node_matches(int(project_id))
    if not node_matches:
        raise _validation_error(
            "node matches are required before expanding MODAL_MAC responses",
            {"project_id": int(project_id)},
        )

    expanded_rows: List[dict] = []
    mapping_rows: List[dict] = []
    for raw_row in list(response_rows or []):
        row = dict(raw_row or {})
        response_type = _normalize_response_type(row.get("response_type") or row.get("type"))
        if response_type != "MODAL_MAC":
            continue
        extra = dict(row.get("extra_json") or {})
        fem_mode_no = extra.get("fem_mode_no", row.get("mode_number"))
        test_mode_no = extra.get("test_mode_no")
        if fem_mode_no is None or test_mode_no is None:
            raise _validation_error(
                "MODAL_MAC response is missing fem/test mode numbers",
                {"response_name": row.get("response_name"), "response_row": row},
            )
        response_name = str(row.get("response_name") or f"MAC_MODE_FE{int(fem_mode_no)}_TEST{int(test_mode_no)}").strip()
        generated_names: List[str] = []
        for node_row in node_matches:
            instance_name = str(node_row.get("instance_name") or "").strip()
            fem_node_label = int(node_row["fem_node_label"])
            test_node_id = str(node_row["test_node_id"])
            for component in ("U1", "U2", "U3"):
                disp_name = f"M{int(fem_mode_no)}T{int(test_mode_no)}N{int(fem_node_label)}{component}"
                generated_names.append(disp_name)
                expanded_rows.append(
                    {
                        "name": disp_name,
                        "type": "DISP",
                        "mode_number": int(fem_mode_no),
                        "node_id": int(fem_node_label),
                        "component": component,
                        "extra_json": {
                            "source_response_name": response_name,
                            "response_type": "MODAL_MAC",
                            "fem_mode_no": int(fem_mode_no),
                            "test_mode_no": int(test_mode_no),
                            "test_node_id": test_node_id,
                            "instance_name": instance_name,
                            "fem_node_label": int(fem_node_label),
                            "component": component,
                        },
                    }
                )
        mapping_rows.append(
            {
                "response_name": response_name,
                "response_type": "MODAL_MAC",
                "fem_mode_no": int(fem_mode_no),
                "test_mode_no": int(test_mode_no),
                "expanded_response_count": len(generated_names),
                "generated_response_names": generated_names,
            }
        )

    if not expanded_rows:
        raise _validation_error(
            "no MODAL_MAC responses were available for SOL200 displacement expansion",
            {"project_id": int(project_id)},
        )
    return {
        "expanded_rows": expanded_rows,
        "mapping_rows": mapping_rows,
    }


def build_modal_mac_matrix_from_displacement_sensitivity(
    *,
    project_id: int,
    mac_response_rows: Sequence[dict],
    parameter_columns: Sequence[dict],
    displacement_response_rows: Sequence[dict],
    displacement_matrix: Sequence[Sequence[float]],
    mac_scale: float = DEFAULT_MAC_SCALE,
) -> Dict[str, Any]:
    matrix = np.asarray(displacement_matrix, dtype=np.float64)
    disp_rows = [dict(item or {}) for item in (displacement_response_rows or [])]
    if matrix.ndim != 2 or matrix.shape[0] != len(disp_rows):
        raise _validation_error(
            "displacement sensitivity matrix shape does not match response metadata",
            {"matrix_shape": list(matrix.shape), "response_count": len(disp_rows)},
        )

    row_index: Dict[Tuple[int, int, str], int] = {}
    for index, row in enumerate(disp_rows):
        extra = dict(row.get("extra_json") or {})
        mode_number = row.get("mode_number", extra.get("mode_number", extra.get("fem_mode_no")))
        node_id = row.get("node_id", extra.get("node_id", extra.get("fem_node_label")))
        component = row.get("component", extra.get("component"))
        if mode_number is None or node_id is None or component is None:
            continue
        row_index[(int(mode_number), int(node_id), _normalize_component_token(component))] = index

    response_rows: List[dict] = []
    response_matrix_rows: List[List[float]] = []
    for raw_row in list(mac_response_rows or []):
        row = dict(raw_row or {})
        extra = dict(row.get("extra_json") or {})
        fem_mode_no = extra.get("fem_mode_no", row.get("mode_number"))
        test_mode_no = extra.get("test_mode_no")
        if fem_mode_no is None or test_mode_no is None:
            raise _validation_error(
                "MODAL_MAC response is missing fem/test mode numbers",
                {"response_row": row},
            )
        vectors = build_project_modal_mac_vectors(
            project_id=int(project_id),
            test_mode_no=int(test_mode_no),
            fem_mode_no=int(fem_mode_no),
        )
        sensor_labels = list(vectors["sensor_labels"])
        node_pairs = list(vectors["node_pairs"])
        dphi_dp = np.zeros((1, len(sensor_labels), len(parameter_columns or [])), dtype=np.float64)
        sensor_offset = 0
        for node_pair in node_pairs:
            fem_node_label = int(node_pair["fem_node_label"])
            for component in ("U1", "U2", "U3"):
                lookup_key = (int(fem_mode_no), fem_node_label, component)
                disp_index = row_index.get(lookup_key)
                if disp_index is None:
                    raise _validation_error(
                        "required modal displacement sensitivity row is missing",
                        {
                            "project_id": int(project_id),
                            "response_name": row.get("response_name"),
                            "fem_mode_no": int(fem_mode_no),
                            "test_mode_no": int(test_mode_no),
                            "fem_node_label": fem_node_label,
                            "component": component,
                        },
                    )
                dphi_dp[0, sensor_offset, :] = matrix[disp_index, :]
                sensor_offset += 1

        result = run_modal_mac_sensitivity(
            {
                "phi_exp": vectors["phi_exp"].tolist(),
                "phi_sim": vectors["phi_sim"].tolist(),
                "dphi_dp": dphi_dp.tolist(),
                "pairs": [{"exp_mode": 1, "sim_mode": 1}],
                "index_base": 1,
                "parameter_names": [
                    str(item.get("parameter_name") or item.get("param_name") or f"p{index + 1}")
                    for index, item in enumerate(parameter_columns or [])
                ],
                "sensor_labels": sensor_labels,
                "mac_scale": float(mac_scale),
            }
        )
        pair_result = dict((result.get("pair_results") or [{}])[0] or {})
        response_rows.append(
            {
                "response_name": str(row.get("response_name") or f"MAC_MODE_FE{int(fem_mode_no)}_TEST{int(test_mode_no)}").strip(),
                "response_type": "MODAL_MAC",
                "mode_number": int(fem_mode_no),
                "unit": "percent" if abs(float(mac_scale) - 100.0) <= 1.0e-12 else "scaled",
            }
        )
        response_matrix_rows.append([float(value) for value in list(pair_result.get("dmac_dp") or [])])

    return {
        "response_rows": response_rows,
        "parameter_columns": [dict(item or {}) for item in (parameter_columns or [])],
        "matrix": response_matrix_rows,
        "mac_scale": float(mac_scale),
    }
