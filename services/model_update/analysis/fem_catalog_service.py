"""FEM catalog and parameter management helpers.

This module owns INP import/catalog building plus optimization-parameter
metadata derived from the parsed model.
"""

import heapq
import json
import math
import os
import re
from src.inp import parse_inp
from src.inp.parameter_mapping import (
    extract_design_response_rows,
    extract_parameter_definition_rows,
    extract_parameter_target_rows,
)
from db import get_connection, ensure_tables_exist
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from src.l3.core.config import settings
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.infra.registry_repo import RegistryRepo

from . import sensitivity_service as _sens
from .console_log_service import safe_write_console_event
from .project_log_service import log_project_error, log_project_info, log_project_step
from .project_config_service import (
    get_test_data_mode,
    get_node_match_parameter_context,
    save_fem_model_dimensions,
)
from .fem_modal_bundle_service import list_fem_modal_frequencies
from .project_path_service import resolve_project_cal_subdir
from .project_source_service import resolve_project_source_inp_path
from .project_status_service import update_work_condition_project_status
from services.model_update.analysis.sensitivity_service import _parse_id_list, _parse_optional_json_object

# This module is the "model-update integration" layer around parsed INP data.
# It converts the parsed model into database catalogs, builds the spatial cache
# used for test/FE matching, and stores the aligned response data consumed by
# Bayesian/model-correlation workflows.
OCTREE_MAX_DEPTH = 8
OCTREE_LEAF_SIZE = 256
TEST_DOF_SEQUENCE = ("UX", "UY", "UZ")
FE_DOF_SEQUENCE = ("U1", "U2", "U3")
DOF_COMPONENT_INDEX = {
    "UX": 0,
    "UY": 1,
    "UZ": 2,
    "U1": 0,
    "U2": 1,
    "U3": 2,
}

_DEVELOPER_INTERFACE_HINTS = {
    "match_nodes": {"method": "POST", "path": "/match/nodes"},
    "pair_node_point_result": {"method": "POST", "path": "/get/pair_node_point_result"},
    "match_dofs": {"method": "POST", "path": "/match/dofs"},
    "import_fem_modal": {"method": "POST", "path": "/import/fem/modal"},
}


def _required_operation_error(message: str, *, operation: str, interface_key: str) -> ValidationError:
    return ValidationError(
        message,
        {
            "required_operation": operation,
            "developer_interface_hint": dict(_DEVELOPER_INTERFACE_HINTS[interface_key]),
        },
    )


def _cache_dir(project_id: int) -> str:
    return resolve_project_cal_subdir(int(project_id), "octree")


def _safe_float(value):
    if value is None:
        return None
    return float(value)


def _json_dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def _json_loads(value):
    if not value:
        return None
    if isinstance(value, dict):
        return value
    return json.loads(value)


_ALLOWED_PARAMETER_USAGE_SCOPE = {"SENSITIVITY", "UPDATE"}
_DEFAULT_PARAMETER_USAGE_SCOPE = ("SENSITIVITY", "UPDATE")
_ALLOWED_RESPONSE_SOLVER_SCOPE = {"DSA", "SOL200", "BAYESIAN"}
_DEFAULT_MODAL_RESPONSE_SOLVER_SCOPE = ("SOL200", "BAYESIAN")
_ALLOWED_MODAL_RESPONSE_TYPES = {"MODAL_FREQUENCY", "MODAL_MAC"}
_DEFAULT_RESPONSE_SCATTER = 0.05


def _normalize_string_scope(
        raw_value,
        *,
        allowed: Sequence[str],
        default_values: Sequence[str],
        field_name: str,
) -> List[str]:
    allowed_set = {str(item).strip().upper() for item in allowed}
    values = list(raw_value or default_values or [])
    if not values:
        values = list(default_values or [])
    normalized = []
    seen = set()
    for item in values:
        token = str(item or "").strip().upper()
        if not token:
            continue
        if token not in allowed_set:
            raise ValidationError(
                f"unsupported {field_name}",
                {field_name: token, "allowed": sorted(allowed_set)},
            )
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    if not normalized:
        normalized = [str(item).strip().upper() for item in (default_values or []) if str(item).strip()]
    if not normalized:
        raise ValidationError(f"{field_name} cannot be empty", {field_name: raw_value})
    return normalized


def _normalize_parameter_usage_scope(raw_value) -> List[str]:
    return _normalize_string_scope(
        raw_value,
        allowed=_ALLOWED_PARAMETER_USAGE_SCOPE,
        default_values=_DEFAULT_PARAMETER_USAGE_SCOPE,
        field_name="usage_scope",
    )


def _normalize_response_solver_scope(raw_value, *, default_values=None) -> List[str]:
    return _normalize_string_scope(
        raw_value,
        allowed=_ALLOWED_RESPONSE_SOLVER_SCOPE,
        default_values=default_values or _DEFAULT_MODAL_RESPONSE_SOLVER_SCOPE,
        field_name="solver_scope",
    )


def _normalize_modal_response_types(raw_value) -> List[str]:
    return _normalize_string_scope(
        raw_value,
        allowed=_ALLOWED_MODAL_RESPONSE_TYPES,
        default_values=("MODAL_FREQUENCY",),
        field_name="response_types",
    )


def _scope_contains(scope_values, expected: str) -> bool:
    token = str(expected or "").strip().upper()
    return token in {str(item or "").strip().upper() for item in list(scope_values or [])}


def _parse_json_list(raw_value, *, default_values: Optional[Sequence[str]] = None) -> List[str]:
    if raw_value in (None, ""):
        return [str(item).strip().upper() for item in list(default_values or []) if str(item).strip()]
    if isinstance(raw_value, list):
        return [str(item).strip().upper() for item in raw_value if str(item).strip()]
    if isinstance(raw_value, tuple):
        return [str(item).strip().upper() for item in list(raw_value) if str(item).strip()]
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except Exception:
            parsed = [part.strip() for part in raw_value.split(",")]
        if isinstance(parsed, list):
            return [str(item).strip().upper() for item in parsed if str(item).strip()]
        if parsed in (None, ""):
            return [str(item).strip().upper() for item in list(default_values or []) if str(item).strip()]
        return [str(parsed).strip().upper()]
    return [str(item).strip().upper() for item in list(default_values or []) if str(item).strip()]


def _clear_import_inp_catalog_tables(cursor, project_id: int) -> None:
    # Importing a new INP should only refresh tables rebuilt from the INP
    # itself. User-maintained selected-parameter / response tables stay intact.
    tables = (
        "t_mt_py_fem_material_overview",
        "t_mt_py_fem_isotropic",
        "t_mt_py_fem_property",
        "t_mt_py_fem_shell_property",
        "t_mt_py_fem_beam_property",
        "t_mt_py_fem_boundary",
        "t_mt_py_fem_parameter_definition",
        "t_mt_py_fem_parameter_target",
        "t_mt_py_fem_quantity_set_capability",
        "t_mt_py_fem_node_octree_cache",
    )
    for table_name in tables:
        cursor.execute(f"DELETE FROM {table_name} WHERE pid = %s", (project_id,))


def _normalize_stored_path(path_value) -> Optional[str]:
    if path_value is None:
        return None
    text = str(path_value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return os.path.abspath(text) if text else None


def _normalize_transform_type(transform_type: str) -> str:
    value = str(transform_type or "").strip().lower()
    if value not in {"fem", "test"}:
        raise ValueError("type must be 'fem' or 'test'")
    return value


def _normalize_matrix4(matrix4) -> List[List[float]]:
    rows = list(matrix4 or [])
    if len(rows) != 4:
        raise ValueError("matrix4 must contain 4 rows")
    normalized = []
    for row in rows:
        values = list(row or [])
        if len(values) != 4:
            raise ValueError("matrix4 must be a 4x4 matrix")
        normalized.append([float(item) for item in values])
    return normalized


def _build_rotation_matrix(axis: Sequence[float], angle_deg: float) -> np.ndarray:
    axis_vec = np.asarray(axis, dtype=np.float64).reshape(3)
    axis_norm = float(np.linalg.norm(axis_vec))
    if axis_norm <= 1e-12 or abs(float(angle_deg)) <= 1e-12:
        return np.eye(3, dtype=np.float64)

    axis_vec = axis_vec / axis_norm
    theta = math.radians(float(angle_deg))
    c = math.cos(theta)
    s = math.sin(theta)
    t = 1.0 - c
    ax, ay, az = axis_vec
    return np.array([
        [t * ax * ax + c, t * ax * ay - s * az, t * ax * az + s * ay],
        [t * ax * ay + s * az, t * ay * ay + c, t * ay * az - s * ax],
        [t * ax * az - s * ay, t * ay * az + s * ax, t * az * az + c],
    ], dtype=np.float64)


def _rotation_to_matrix(rotation=None) -> np.ndarray:
    if not rotation:
        return np.eye(3, dtype=np.float64)
    if "matrix" in rotation and rotation["matrix"] is not None:
        return np.asarray(rotation["matrix"], dtype=np.float64).reshape(3, 3)
    return _build_rotation_matrix(
        rotation.get("axis", [0.0, 0.0, 1.0]),
        rotation.get("angle_deg", 0.0),
    )


def _rotation_from_matrix(rot_m: np.ndarray, center=None) -> dict:
    rot_m = np.asarray(rot_m, dtype=np.float64).reshape(3, 3)
    trace = float(np.trace(rot_m))
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    theta = math.acos(cos_theta)
    if theta <= 1e-12:
        axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        angle_deg = 0.0
    else:
        axis = np.array([
            rot_m[2, 1] - rot_m[1, 2],
            rot_m[0, 2] - rot_m[2, 0],
            rot_m[1, 0] - rot_m[0, 1],
        ], dtype=np.float64)
        denom = 2.0 * math.sin(theta)
        if abs(denom) > 1e-12:
            axis = axis / denom
        axis_norm = float(np.linalg.norm(axis))
        if axis_norm <= 1e-12:
            axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        else:
            axis = axis / axis_norm
        angle_deg = math.degrees(theta)

    return {
        "center": list(center or [0.0, 0.0, 0.0]),
        "axis": axis.tolist(),
        "angle_deg": float(angle_deg),
        "matrix": rot_m.tolist(),
    }


def _apply_transform(coords: np.ndarray, translation=None, rotation=None) -> np.ndarray:
    if coords.size == 0:
        return coords.copy()

    out = coords.astype(np.float64, copy=True)
    rot_m = _rotation_to_matrix(rotation)
    center = np.asarray((rotation or {}).get("center", [0.0, 0.0, 0.0]), dtype=np.float64).reshape(3)
    if not np.allclose(rot_m, np.eye(3)):
        out = ((rot_m @ (out - center).T).T + center)
    if translation is not None:
        out += np.asarray(translation, dtype=np.float64).reshape(3)
    return out.astype(np.float64, copy=False)


def _estimate_translation(fe_coords: np.ndarray, test_coords: np.ndarray, tolerance_ratio: float = 0.02):
    if fe_coords.size == 0 or test_coords.size == 0:
        return np.zeros(3, dtype=np.float64), "empty"

    fe_min = fe_coords.min(axis=0)
    fe_max = fe_coords.max(axis=0)
    test_min = test_coords.min(axis=0)
    test_max = test_coords.max(axis=0)
    fe_diag = float(np.linalg.norm(fe_max - fe_min))
    test_diag = float(np.linalg.norm(test_max - test_min))
    fe_cent = fe_coords.mean(axis=0)
    test_cent = test_coords.mean(axis=0)
    center_delta = fe_cent - test_cent
    center_dist = float(np.linalg.norm(center_delta))

    if fe_diag < 1e-12 or test_diag < 1e-12:
        return center_delta, "centroid"

    size_ratio = test_diag / fe_diag
    if 0.9 <= size_ratio <= 1.1 and center_dist <= fe_diag * tolerance_ratio:
        return np.zeros(3, dtype=np.float64), "none"

    return center_delta, "centroid"


def _best_fit_rigid_transform(src: np.ndarray, dst: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    if len(src) != len(dst) or len(src) < 3:
        raise ValueError("rigid transform requires >=3 paired points")

    src_cent = src.mean(axis=0)
    dst_cent = dst.mean(axis=0)
    src_centered = src - src_cent
    dst_centered = dst - dst_cent
    h_mat = src_centered.T @ dst_centered
    u_mat, _, vt_mat = np.linalg.svd(h_mat)
    rot_m = vt_mat.T @ u_mat.T
    if np.linalg.det(rot_m) < 0:
        vt_mat[-1, :] *= -1.0
        rot_m = vt_mat.T @ u_mat.T

    trans = dst_cent - (rot_m @ src_cent)
    return rot_m, trans


def _sample_rows(coords: np.ndarray, limit: int = 2000) -> np.ndarray:
    if len(coords) <= limit:
        return coords
    idx = np.linspace(0, len(coords) - 1, num=limit, dtype=np.int64)
    return coords[idx]


def _nearest_points(cache: dict, query_coords: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    point_ids = np.full(len(query_coords), -1, dtype=np.int64)
    distances = np.full(len(query_coords), np.inf, dtype=np.float64)
    for idx, point in enumerate(query_coords):
        point_ids[idx], distances[idx] = _octree_nearest(cache, point)
    return point_ids, distances


def _estimate_rigid_transform_icp(cache: dict, test_coords: np.ndarray,
                                  max_iterations: int = 15, sample_limit: int = 2000) -> dict:
    sample = _sample_rows(test_coords.astype(np.float64, copy=False), limit=sample_limit)
    trans0, mode0 = _estimate_translation(cache["point_coords"], sample)
    rot_total = np.eye(3, dtype=np.float64)
    trans_total = np.asarray(trans0, dtype=np.float64).reshape(3)

    rmse = None
    pair_count = 0
    for iteration in range(1, max_iterations + 1):
        transformed = _apply_transform(
            sample,
            translation=trans_total,
            rotation={"matrix": rot_total.tolist()},
        )
        nearest_idx, distances = _nearest_points(cache, transformed)
        valid_mask = nearest_idx >= 0
        if int(valid_mask.sum()) < 3:
            break

        transformed_valid = transformed[valid_mask]
        matched = cache["point_coords"][nearest_idx[valid_mask]]
        used_dist = distances[valid_mask]
        if len(used_dist) >= 6:
            threshold = float(np.quantile(used_dist, 0.8))
            threshold = max(threshold, float(np.median(used_dist)))
            inlier_mask = used_dist <= threshold
            if int(inlier_mask.sum()) >= 3:
                transformed_valid = transformed_valid[inlier_mask]
                matched = matched[inlier_mask]
                used_dist = used_dist[inlier_mask]

        if len(transformed_valid) < 3:
            break

        delta_rot, delta_trans = _best_fit_rigid_transform(transformed_valid, matched)
        rot_total = delta_rot @ rot_total
        trans_total = delta_rot @ trans_total + delta_trans
        rmse_new = float(math.sqrt(np.mean(np.square(used_dist)))) if len(used_dist) else 0.0
        pair_count = int(len(used_dist))
        if rmse is not None and abs(rmse - rmse_new) <= 1e-8:
            rmse = rmse_new
            break
        rmse = rmse_new

    return {
        "rotation_matrix": rot_total,
        "translation": trans_total,
        "mode": "auto_rigid" if not np.allclose(rot_total, np.eye(3), atol=1e-6) else mode0,
        "iterations": iteration if "iteration" in locals() else 0,
        "rmse": rmse,
        "pair_count": pair_count,
    }


def _build_octree(points: np.ndarray, max_depth: int = OCTREE_MAX_DEPTH, leaf_size: int = OCTREE_LEAF_SIZE):
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("points must be [N, 3]")
    if len(points) == 0:
        raise ValueError("cannot build octree for empty point set")

    node_bbox = []
    node_children = []
    node_is_leaf = []
    leaf_point_map = {}

    def recurse(point_ids: np.ndarray, depth: int, lo: np.ndarray, hi: np.ndarray) -> int:
        node_idx = len(node_bbox)
        node_bbox.append([float(lo[0]), float(lo[1]), float(lo[2]),
                          float(hi[0]), float(hi[1]), float(hi[2])])
        node_children.append([-1] * 8)
        node_is_leaf.append(0)

        if depth >= max_depth or len(point_ids) <= leaf_size:
            node_is_leaf[node_idx] = 1
            leaf_point_map[node_idx] = point_ids.astype(np.int32, copy=True)
            return node_idx

        center = (lo + hi) * 0.5
        rel = points[point_ids] >= center[None, :]
        octants = (rel[:, 0].astype(np.int32) << 2) | (rel[:, 1].astype(np.int32) << 1) | rel[:, 2].astype(np.int32)

        any_child = False
        for octant in range(8):
            child_ids = point_ids[octants == octant]
            if len(child_ids) == 0:
                continue

            child_lo = lo.copy()
            child_hi = hi.copy()
            for axis in range(3):
                if (octant >> (2 - axis)) & 1:
                    child_lo[axis] = center[axis]
                else:
                    child_hi[axis] = center[axis]

            if np.allclose(child_lo, child_hi):
                continue

            child_idx = recurse(child_ids, depth + 1, child_lo, child_hi)
            node_children[node_idx][octant] = child_idx
            any_child = True

        if not any_child:
            node_is_leaf[node_idx] = 1
            leaf_point_map[node_idx] = point_ids.astype(np.int32, copy=True)

        return node_idx

    all_ids = np.arange(len(points), dtype=np.int32)
    root_lo = points.min(axis=0).astype(np.float64)
    root_hi = points.max(axis=0).astype(np.float64)
    pad = np.maximum((root_hi - root_lo) * 1e-6, 1e-9)
    recurse(all_ids, 0, root_lo - pad, root_hi + pad)

    point_indices = []
    point_offsets = []
    cursor = 0
    for idx in range(len(node_bbox)):
        point_offsets.append(cursor)
        if node_is_leaf[idx]:
            ids = leaf_point_map.get(idx, np.zeros(0, dtype=np.int32))
            point_indices.append(ids)
            cursor += len(ids)
    point_offsets.append(cursor)

    point_indices_arr = np.concatenate(point_indices).astype(np.int32, copy=False) if point_indices else np.zeros(0, dtype=np.int32)
    return {
        "node_bbox": np.asarray(node_bbox, dtype=np.float32),
        "node_children": np.asarray(node_children, dtype=np.int32),
        "node_is_leaf": np.asarray(node_is_leaf, dtype=np.uint8),
        "point_indices": point_indices_arr,
        "point_offsets": np.asarray(point_offsets, dtype=np.int32),
    }


def _bbox_dist_sq(point: np.ndarray, bbox_row: np.ndarray) -> float:
    dx = 0.0
    for axis in range(3):
        lo = float(bbox_row[axis])
        hi = float(bbox_row[axis + 3])
        if point[axis] < lo:
            diff = lo - float(point[axis])
            dx += diff * diff
        elif point[axis] > hi:
            diff = float(point[axis]) - hi
            dx += diff * diff
    return dx


def _octree_nearest(cache: dict, query_point: np.ndarray):
    node_bbox = cache["node_bbox"]
    node_children = cache["node_children"]
    node_is_leaf = cache["node_is_leaf"]
    point_indices = cache["point_indices"]
    point_offsets = cache["point_offsets"]
    point_coords = cache["point_coords"]

    best_idx = -1
    best_dist_sq = float("inf")
    heap = [(0.0, 0)]

    while heap:
        dist_sq, node_idx = heapq.heappop(heap)
        if dist_sq > best_dist_sq:
            continue

        if int(node_is_leaf[node_idx]) == 1:
            start = int(point_offsets[node_idx])
            end = int(point_offsets[node_idx + 1])
            if start >= end:
                continue
            ids = point_indices[start:end]
            pts = point_coords[ids]
            diff = pts - query_point[None, :]
            sq = np.einsum("ij,ij->i", diff, diff)
            local = int(np.argmin(sq))
            if float(sq[local]) < best_dist_sq:
                best_dist_sq = float(sq[local])
                best_idx = int(ids[local])
            continue

        for child_idx in node_children[node_idx]:
            if int(child_idx) < 0:
                continue
            child_dist_sq = _bbox_dist_sq(query_point, node_bbox[int(child_idx)])
            if child_dist_sq <= best_dist_sq:
                heapq.heappush(heap, (child_dist_sq, int(child_idx)))

    return best_idx, math.sqrt(best_dist_sq) if best_idx >= 0 else float("inf")


def _collect_global_nodes(model) -> dict:
    # The imported cache must live in global coordinates because later matching
    # works against measured test points, not part-local coordinates.
    entries = []
    if model.assembly and model.assembly.instances:
        for inst_name, inst in model.assembly.instances.items():
            part = model.parts.get(inst.part_name)
            if part is None or not part.nodes:
                continue
            labels = np.array(sorted(part.nodes.keys()), dtype=np.int64)
            coords_local = np.array(
                [[part.nodes[label].x, part.nodes[label].y, part.nodes[label].z] for label in labels],
                dtype=np.float64,
            )
            transform = getattr(inst, "transform_matrix", None)
            if transform is None:
                coords_global = coords_local
            else:
                hom = np.ones((len(coords_local), 4), dtype=np.float64)
                hom[:, :3] = coords_local
                coords_global = (transform @ hom.T).T[:, :3]
            entries.append({
                "instance_name": inst_name,
                "part_name": inst.part_name,
                "labels": labels,
                "coords_global": coords_global.astype(np.float64, copy=False),
            })
    else:
        for part_name, part in model.parts.items():
            if not part.nodes:
                continue
            labels = np.array(sorted(part.nodes.keys()), dtype=np.int64)
            coords_global = np.array(
                [[part.nodes[label].x, part.nodes[label].y, part.nodes[label].z] for label in labels],
                dtype=np.float64,
            )
            entries.append({
                "instance_name": part_name,
                "part_name": part_name,
                "labels": labels,
                "coords_global": coords_global,
            })

    if not entries:
        raise ValueError("no nodes found in inp model")

    point_coords = np.vstack([e["coords_global"] for e in entries]).astype(np.float64, copy=False)
    point_labels = np.concatenate([e["labels"] for e in entries]).astype(np.int64, copy=False)
    point_instances = np.concatenate([np.full(len(e["labels"]), e["instance_name"], dtype=object) for e in entries])
    point_parts = np.concatenate([np.full(len(e["labels"]), e["part_name"], dtype=object) for e in entries])

    return {
        "entries": entries,
        "point_coords": point_coords,
        "point_labels": point_labels,
        "point_instances": point_instances,
        "point_parts": point_parts,
        "bbox_min": point_coords.min(axis=0),
        "bbox_max": point_coords.max(axis=0),
    }


def _save_octree_cache(project_id: int, source_file_path: str, node_data: dict, force_rebuild: bool = False):
    # Cache the global FE node cloud once per imported INP so repeated node
    # matching does not need to rebuild the octree from scratch.
    cache_dir = _cache_dir(project_id)
    stem = os.path.splitext(os.path.basename(source_file_path))[0]
    cache_path = os.path.join(cache_dir, f"{stem}.node_octree.npz")

    if force_rebuild or not os.path.exists(cache_path):
        tree = _build_octree(node_data["point_coords"])
        np.savez_compressed(
            cache_path,
            node_bbox=tree["node_bbox"],
            node_children=tree["node_children"],
            node_is_leaf=tree["node_is_leaf"],
            point_indices=tree["point_indices"],
            point_offsets=tree["point_offsets"],
            point_coords=node_data["point_coords"].astype(np.float32),
            point_labels=node_data["point_labels"].astype(np.int64),
            point_instances=node_data["point_instances"].astype("U128"),
            point_parts=node_data["point_parts"].astype("U128"),
            bbox_min=node_data["bbox_min"].astype(np.float64),
            bbox_max=node_data["bbox_max"].astype(np.float64),
        )

    return cache_path


def _load_octree_cache(cache_file_path: str) -> dict:
    with np.load(cache_file_path, allow_pickle=False) as data:
        return {
            "node_bbox": data["node_bbox"],
            "node_children": data["node_children"],
            "node_is_leaf": data["node_is_leaf"],
            "point_indices": data["point_indices"],
            "point_offsets": data["point_offsets"],
            "point_coords": data["point_coords"].astype(np.float64),
            "point_labels": data["point_labels"],
            "point_instances": data["point_instances"],
            "point_parts": data["point_parts"],
            "bbox_min": data["bbox_min"].astype(np.float64),
            "bbox_max": data["bbox_max"].astype(np.float64),
        }


def _cache_part_lookup(cache: dict) -> Dict[Tuple[str, int], str]:
    lookup = {}
    for inst_name, label, part_name in zip(cache["point_instances"], cache["point_labels"], cache["point_parts"]):
        lookup[(str(inst_name), int(label))] = str(part_name)
    return lookup


def _safe_float_zero(value, default: float = 0.0) -> float:
    value = _safe_float(value)
    return default if value is None else value


def _is_modal_unv_project(project_id: int, *, cursor=None) -> bool:
    return str(get_test_data_mode(int(project_id), cursor=cursor) or "").strip().lower() == "modal_unv"


def _require_modal_project(project_id: int, *, cursor=None) -> None:
    test_data_mode = str(get_test_data_mode(int(project_id), cursor=cursor) or "").strip().lower()
    if test_data_mode != "modal_unv":
        raise ValidationError(
            "modal correlation requires a modal project",
            {"project_id": int(project_id), "test_data_mode": test_data_mode or None, "expected": "modal_unv"},
        )


def _require_non_modal_project(project_id: int, *, cursor=None) -> None:
    test_data_mode = str(get_test_data_mode(int(project_id), cursor=cursor) or "").strip().lower()
    if test_data_mode == "modal_unv":
        raise ValidationError(
            "static correlation is not available for modal projects",
            {"project_id": int(project_id), "test_data_mode": test_data_mode, "disallowed": "modal_unv"},
        )


def _load_test_nodes_for_matching(cursor, project_id: int):
    if _is_modal_unv_project(int(project_id), cursor=cursor):
        cursor.execute(
            """
            SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position
            FROM t_mt_py_test_node
            WHERE pid = %s
            ORDER BY nid
            """,
            (int(project_id),),
        )
        rows = cursor.fetchall() or []
        if rows:
            return rows, "t_mt_py_test_node"

    cursor.execute(
        """
        SELECT measuring_point_name AS test_node_id, x_position, y_position, z_position
        FROM t_mt_measuring_point_info
        WHERE project_id = %s
        ORDER BY measuring_point_name
        """,
        (int(project_id),),
    )
    return cursor.fetchall() or [], "t_mt_measuring_point_info"


def _extract_legacy_material_rows(model):
    overview_rows = []
    isotropic_rows = []
    material_id_map = {}

    for idx, (mat_name, material) in enumerate(sorted(model.materials.items()), start=1):
        material_id_map[mat_name] = idx
        mat_type = "MATERIAL"
        if material.elastic and material.elastic.elastic_type:
            mat_type = str(material.elastic.elastic_type).upper()

        overview_rows.append({
            "id": idx,
            "type": mat_type,
            "name": mat_name,
        })

        elastic = material.elastic
        elastic_data = list(getattr(elastic, "data", []) or []) if elastic is not None else []
        elastic_type = str(getattr(elastic, "elastic_type", "") or "").upper()
        row0 = list(elastic_data[0]) if elastic_data else []
        if elastic_type in {"ISOTROPIC", "ISO"} and row0:
            isotropic_rows.append({
                "id": idx,
                "rho": _safe_float_zero(material.density_data[0][0] if material.density_data else 0.0),
                "e": _safe_float_zero(row0[0] if len(row0) >= 1 else 0.0),
                "nu": _safe_float_zero(row0[1] if len(row0) >= 2 else 0.0),
                "ge": _safe_float_zero(row0[2] if len(row0) >= 3 else 0.0),
            })

    return {
        "overview_rows": overview_rows,
        "isotropic_rows": isotropic_rows,
        "material_id_map": material_id_map,
    }


def _extract_legacy_property_rows(model):
    overview_rows = []
    shell_rows = []
    beam_rows = []
    property_seq = 1

    for part_name, part in sorted(model.parts.items()):
        for section in part.sections:
            row = {
                "id": property_seq,
                "type": str(section.section_type).upper(),
                "part_name": part_name,
                "elset_name": section.elset_name,
                "material_name": section.material_name,
            }
            overview_rows.append(row)

            if row["type"] in ("SHELL", "MEMBRANE"):
                shell_rows.append({
                    "id": property_seq,
                    "thickness": _safe_float_zero(section.thickness),
                    "nsm": _safe_float_zero(section.extra.get("nsm", 0.0)),
                    "theta": _safe_float_zero(section.extra.get("theta", 0.0)),
                    "element_set": str(section.elset_name or "") or None,
                })
            elif row["type"] == "BEAM":
                dims = list(section.extra.get("dims", []) or [])
                beam_rows.append({
                    "id": property_seq,
                    "ax": _safe_float_zero(dims[0] if len(dims) >= 1 else 0.0),
                    "ay": _safe_float_zero(dims[1] if len(dims) >= 2 else 0.0),
                    "az": _safe_float_zero(dims[2] if len(dims) >= 3 else 0.0),
                    "ix": _safe_float_zero(dims[3] if len(dims) >= 4 else 0.0),
                    "iy": _safe_float_zero(dims[4] if len(dims) >= 5 else 0.0),
                    "iz": _safe_float_zero(dims[5] if len(dims) >= 6 else 0.0),
                    "cw": _safe_float_zero(dims[6] if len(dims) >= 7 else 0.0),
                    "yn": _safe_float_zero(dims[7] if len(dims) >= 8 else 0.0),
                    "zn": _safe_float_zero(dims[8] if len(dims) >= 9 else 0.0),
                    "nsm": _safe_float_zero(section.extra.get("nsm", dims[9] if len(dims) >= 10 else 0.0)),
                })

            property_seq += 1

    return {
        "overview_rows": overview_rows,
        "shell_rows": shell_rows,
        "beam_rows": beam_rows,
    }


def _lookup_bc_node_labels(model, target_name: str) -> List[int]:
    labels = []
    seen = set()

    if target_name is None:
        return labels

    text = str(target_name).strip()
    if not text:
        return labels

    if text.lstrip("+-").isdigit():
        return [int(text)]

    if model.assembly and text in model.assembly.nsets:
        for node_label in model.assembly.nsets[text].node_labels:
            node_int = int(node_label)
            if node_int not in seen:
                seen.add(node_int)
                labels.append(node_int)

    for _, part in sorted(model.parts.items()):
        nset = part.nsets.get(text)
        if not nset:
            continue
        for node_label in nset.node_labels:
            node_int = int(node_label)
            if node_int not in seen:
                seen.add(node_int)
                labels.append(node_int)

    return labels


def _extract_legacy_boundary_rows(model):
    dof_fields = {
        1: "ux",
        2: "uy",
        3: "uz",
        4: "rx",
        5: "ry",
        6: "rz",
    }
    node_map = {}

    for step in model.steps:
        for bc in step.boundary_conditions:
            node_labels = _lookup_bc_node_labels(model, bc.nset_name)
            if not node_labels:
                continue
            value = _safe_float_zero(bc.value)
            dof_start = int(min(bc.dof_start, bc.dof_end))
            dof_end = int(max(bc.dof_start, bc.dof_end))
            for node_label in node_labels:
                node_row = node_map.setdefault(int(node_label), {
                    "node": int(node_label),
                    "ux": None,
                    "uy": None,
                    "uz": None,
                    "rx": None,
                    "ry": None,
                    "rz": None,
                })
                for dof in range(dof_start, dof_end + 1):
                    field = dof_fields.get(dof)
                    if field:
                        node_row[field] = value

    rows = []
    for idx, node_label in enumerate(sorted(node_map), start=1):
        row = dict(node_map[node_label])
        row["id"] = idx
        rows.append(row)
    return rows


_DEFAULT_PARAMETER_SCATTER = 0.25
_SUPPORTED_CORRECTION_QUANTITIES = (
    {"quantity_code": "E", "quantity_name": "E", "unit": None, "enabled": 1, "sort_no": 1},
    {"quantity_code": "T", "quantity_name": "T", "unit": None, "enabled": 1, "sort_no": 2},
    {"quantity_code": "RHO", "quantity_name": "RHO", "unit": None, "enabled": 1, "sort_no": 3},
)


def _quantity_description(quantity_code: str, quantity_name: Optional[str] = None) -> str:
    token = str(quantity_code or "").strip().upper()
    if token == "E":
        return "杨氏模量"
    if token == "T":
        return "壳单元厚度"
    if token == "RHO":
        return "密度"
    return f"{str(quantity_name or token).strip()} parameter."


def _normalize_quantity_code_for_compare(quantity_code: Optional[str]) -> str:
    token = str(quantity_code or "").strip().upper()
    if token == "H":
        return "T"
    return token


def _append_unique_name(names: List[str], seen: set, value: Optional[str]) -> None:
    token = str(value or "").strip()
    if not token or token in seen:
        return
    seen.add(token)
    names.append(token)


def _build_inp_parameter_options(
        supported_quantities: Sequence[dict],
        quantity_set_capabilities: Sequence[dict],
) -> List[dict]:
    quantity_rows = [
        dict(row)
        for row in supported_quantities
        if int(row.get("enabled", 1) or 0) == 1
    ]
    quantity_rows.sort(key=lambda row: (int(row.get("sort_no", 0) or 0), str(row.get("quantity_code") or "")))

    canonical_quantity_rows = []
    seen_quantity_codes = set()
    for quantity in quantity_rows:
        quantity_code = _normalize_quantity_code_for_compare(quantity.get("quantity_code"))
        if not quantity_code or quantity_code in seen_quantity_codes:
            continue
        seen_quantity_codes.add(quantity_code)
        canonical_quantity = dict(quantity)
        canonical_quantity["quantity_code"] = quantity_code
        canonical_quantity_rows.append(canonical_quantity)

    capability_rows = [dict(row) for row in (quantity_set_capabilities or [])]
    result = []
    for quantity in canonical_quantity_rows:
        quantity_code = _normalize_quantity_code_for_compare(quantity.get("quantity_code"))
        quantity_name = str(quantity.get("quantity_name") or quantity_code).strip()
        description = _quantity_description(quantity_code, quantity_name)
        matched_capabilities = [
            row for row in capability_rows
            if _normalize_quantity_code_for_compare(row.get("quantity_code")) == quantity_code
        ]
        set_names = []
        seen_names = set()
        ordered_rows = sorted(
            matched_capabilities,
            key=lambda row: (
                str(row.get("set_scope") or ""),
                str(row.get("set_type") or ""),
                str(row.get("set_name") or ""),
                str(row.get("instance_name") or ""),
                str(row.get("part_name") or ""),
            ),
        )
        for row in ordered_rows:
            if not row.get("supports_global") and not row.get("supports_local"):
                continue
            _append_unique_name(set_names, seen_names, row.get("set_name"))
        if not set_names:
            continue
        result.append(
            {
                "parameter_name": quantity_code,
                "description": description,
                "level": "GLOBAL",
                "sets": [{"rows": val} for val in set_names],
            }
        )
    return result


def _normalize_quantity_code(quantity_code: str) -> str:
    value = str(quantity_code or "").strip().upper()
    if value == "H":
        return "T"
    if value not in {"E", "T", "RHO"}:
        raise ValueError("quantity_code must be one of: E, T, RHO")
    return value


def _normalize_selection_mode(selection_mode: str) -> str:
    value = str(selection_mode or "").strip().upper()
    if value not in {"GLOBAL", "LOCAL"}:
        raise ValueError("selection_mode must be GLOBAL or LOCAL")
    return value


def _derive_quantity_code_from_candidate(candidate_code: Optional[str]) -> Optional[str]:
    token = str(candidate_code or "").strip().upper()
    if not token:
        return None
    if token.endswith(":THICKNESS"):
        return "T"
    if token.endswith(":E"):
        return "E"
    return None


def _element_family_from_abaqus_type(abaqus_type: Optional[str]) -> str:
    token = str(abaqus_type or "").strip().upper()
    if token.startswith(("S", "SC", "M3D")):
        return "SHELL"
    if token.startswith(("CPS", "CPE", "CAX")):
        return "SHELL"
    if token.startswith("C3D"):
        return "SOLID"
    if token.startswith(("B", "T3D", "PIPE", "CONN")):
        return "BEAM"
    return "OTHER"


def _resolve_material_elastic_modulus(material) -> Optional[float]:
    elastic = getattr(material, "elastic", None)
    data = list(getattr(elastic, "data", []) or [])
    if not data:
        return None
    row0 = list(data[0] or [])
    if not row0:
        return None
    return _safe_float(row0[0])


def _resolve_section_thickness(section, parameter_defs: Dict[str, dict]) -> Optional[float]:
    if getattr(section, "thickness", None) is not None:
        return _safe_float(section.thickness)
    parameter_name = getattr(section, "thickness_parameter", None)
    if parameter_name:
        row = parameter_defs.get(str(parameter_name))
        if row is not None:
            return _safe_float(row.get("scalar_value"))
    return None


def _target_keys_for_scope(set_scope: str, part_name: Optional[str], instance_name: Optional[str], labels: Sequence[int]) -> List[str]:
    if str(set_scope).upper() == "ASSEMBLY":
        prefix = f"INST::{str(instance_name or '').strip()}"
    else:
        prefix = f"PART::{str(part_name or '').strip()}"
    return [f"{prefix}::{int(label)}" for label in labels]


def _build_section_parameter_maps(model, parameter_defs: Dict[str, dict]):
    quantity_maps = {"E": {}, "T": {}, "RHO": {}}
    property_sets = {}

    for part_name, part in model.parts.items():
        for section in list(getattr(part, "sections", []) or []):
            elset_name = str(getattr(section, "elset_name", "") or "")
            elset = part.elsets.get(elset_name)
            labels = sorted(set(int(label) for label in (getattr(elset, "elem_labels", []) or [])))
            if not labels:
                continue

            section_type = str(getattr(section, "section_type", "") or "").upper()
            material_name = str(getattr(section, "material_name", "") or "").strip() or None
            h_value = _resolve_section_thickness(section, parameter_defs) if section_type in {"SHELL", "MEMBRANE"} else None
            material = model.materials.get(material_name) if material_name else None
            e_value = _resolve_material_elastic_modulus(material) if material is not None else None
            families = {
                _element_family_from_abaqus_type(getattr(part.elements.get(int(label)), "abaqus_type", None))
                for label in labels
                if int(label) in part.elements
            }
            property_sets[(part_name, elset_name)] = {
                "set_role": "PROPERTY_SET",
                "section_type": section_type or None,
                "material_name": material_name,
                "element_family": next(iter(families)) if len(families) == 1 else "MIXED",
                "labels": labels,
                "quantity_values": {
                    "E": e_value,
                    "T": h_value,
                },
            }

            for label in labels:
                key = (str(part_name), int(label))
                if e_value is not None:
                    quantity_maps["E"][key] = float(e_value)
                if h_value is not None:
                    quantity_maps["T"][key] = float(h_value)

    return quantity_maps, property_sets


def _iter_elset_entries(model) -> List[dict]:
    entries = []
    for part_name, part in model.parts.items():
        for set_name, elset in part.elsets.items():
            labels = sorted(set(int(label) for label in (elset.elem_labels or [])))
            if not labels:
                continue
            # Internal sets (_PickedSetNN) are exposed too — they are the actual
            # section regions and thus the most meaningful tunable property sets —
            # but flagged so the UI can tell them apart from user-named sets.
            entries.append(
                {
                    "set_name": str(set_name),
                    "set_type": "ELSET",
                    "set_scope": "PART",
                    "instance_name": None,
                    "part_name": str(part_name),
                    "element_labels": labels,
                    "member_count": len(labels),
                    "is_internal": 1 if getattr(elset, "internal", False) else 0,
                }
            )

    if model.assembly:
        for set_name, elset in model.assembly.elsets.items():
            labels = sorted(set(int(label) for label in (elset.elem_labels or [])))
            if not labels:
                continue
            inst_name = getattr(elset, "instance_name", None)
            part_name = None
            if inst_name and inst_name in model.assembly.instances:
                part_name = model.assembly.instances[inst_name].part_name
            entries.append(
                {
                    "set_name": str(set_name),
                    "set_type": "ELSET",
                    "set_scope": "ASSEMBLY",
                    "instance_name": str(inst_name) if inst_name else None,
                    "part_name": str(part_name) if part_name else None,
                    "element_labels": labels,
                    "member_count": len(labels),
                    "is_internal": 1 if getattr(elset, "internal", False) else 0,
                }
            )
    return entries


def _extract_quantity_set_capabilities(model) -> List[dict]:
    parameter_defs = {
        str(item["parameter_name"]): item for item in extract_parameter_definition_rows(model)
    }
    quantity_maps, property_sets = _build_section_parameter_maps(model, parameter_defs)
    capability_rows = []

    for set_entry in _iter_elset_entries(model):
        part_name = str(set_entry.get("part_name") or "")
        labels = [int(label) for label in set_entry["element_labels"]]
        part = model.parts.get(part_name)
        if part is None:
            continue
        families = {
            _element_family_from_abaqus_type(getattr(part.elements.get(int(label)), "abaqus_type", None))
            for label in labels
            if int(label) in part.elements
        }
        element_family = next(iter(families)) if len(families) == 1 else "MIXED"
        property_info = property_sets.get((part_name, str(set_entry["set_name"])))

        for quantity in _SUPPORTED_CORRECTION_QUANTITIES:
            quantity_code = str(quantity["quantity_code"])
            values_by_label = {
                str(label): quantity_maps[quantity_code][(part_name, int(label))]
                for label in labels
                if (part_name, int(label)) in quantity_maps[quantity_code]
            }
            supports_global = False
            supports_local = False
            set_role = "HETEROGENEOUS_SET"
            section_type = None
            material_name = None

            if property_info is not None:
                set_role = "PROPERTY_SET"
                section_type = property_info.get("section_type")
                material_name = property_info.get("material_name")
                if quantity_code == "T":
                    if property_info["quantity_values"].get("T") is not None and element_family == "SHELL":
                        supports_global = True
                        supports_local = True
                elif quantity_code == "E":
                    if property_info["quantity_values"].get("E") is not None and element_family in {"SHELL", "SOLID", "BEAM"}:
                        supports_global = True
                        supports_local = True
            elif element_family in {"SHELL", "SOLID", "BEAM"}:
                set_role = "HOMOGENEOUS_TYPE_SET"
                if quantity_code == "T":
                    supports_local = element_family == "SHELL" and len(values_by_label) == len(labels)
                elif quantity_code == "E":
                    supports_local = len(values_by_label) == len(labels)

            if not supports_global and not supports_local:
                continue

            distinct_values = sorted({float(value) for value in values_by_label.values()})
            current_value = distinct_values[0] if len(distinct_values) == 1 else None
            target_keys = _target_keys_for_scope(
                set_scope=str(set_entry["set_scope"]),
                part_name=set_entry.get("part_name"),
                instance_name=set_entry.get("instance_name"),
                labels=labels,
            )
            capability_rows.append(
                {
                    "quantity_code": quantity_code,
                    "set_name": str(set_entry["set_name"]),
                    "set_type": str(set_entry["set_type"]),
                    "set_scope": str(set_entry["set_scope"]),
                    "instance_name": set_entry.get("instance_name"),
                    "part_name": set_entry.get("part_name"),
                    "set_role": set_role,
                    "element_family": element_family,
                    "section_type": section_type,
                    "material_name": material_name,
                    "member_count": int(set_entry["member_count"]),
                    "is_internal": int(set_entry.get("is_internal", 0)),
                    "supports_global": bool(supports_global),
                    "supports_local": bool(supports_local),
                    "current_value": current_value,
                    "extra_json": {
                        "element_labels": labels,
                        "target_keys": target_keys,
                        "target_keys_by_label": {
                            str(label): _target_keys_for_scope(
                                set_scope=str(set_entry["set_scope"]),
                                part_name=set_entry.get("part_name"),
                                instance_name=set_entry.get("instance_name"),
                                labels=[int(label)],
                            )
                            for label in labels
                        },
                        "element_values": values_by_label,
                    },
                }
            )

    capability_rows.sort(
        key=lambda item: (
            str(item["quantity_code"]),
            str(item["set_scope"]),
            str(item["set_type"]),
            str(item["set_name"]),
            str(item.get("instance_name") or ""),
            str(item.get("part_name") or ""),
        )
    )
    return capability_rows


def import_inp_catalog(file_path, project_id, clear_before_insert=True,
                       build_octree=True, force_rebuild_octree=False,
                       model=None):
    # Central INP import pipeline:
    # 1. parse the model and derive catalogs/parameter metadata
    # 2. optionally build the global-node octree cache
    # 3. refresh the database tables that the rest of the API queries
    ensure_tables_exist()
    if model is None:
        model = parse_inp(file_path, resolve_refs=True)
    legacy_materials = _extract_legacy_material_rows(model)
    legacy_properties = _extract_legacy_property_rows(model)
    legacy_boundaries = _extract_legacy_boundary_rows(model)
    parameter_definitions = extract_parameter_definition_rows(model)
    parameter_targets = extract_parameter_target_rows(model)
    design_responses = extract_design_response_rows(model)
    supported_quantities = list(_SUPPORTED_CORRECTION_QUANTITIES)
    quantity_set_capabilities = _extract_quantity_set_capabilities(model)
    node_data = _collect_global_nodes(model)
    cache_path = _save_octree_cache(
        project_id=project_id,
        source_file_path=file_path,
        node_data=node_data,
        force_rebuild=force_rebuild_octree,
    ) if build_octree else None

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Regardless of full-refresh mode, keep user-maintained optimization
        # parameter / response tables untouched during INP import.
        _clear_import_inp_catalog_tables(cursor, project_id)

        material_overview_sql = """
        INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Type = VALUES(Type)
        """
        for item in legacy_materials["overview_rows"]:
            cursor.execute(material_overview_sql, (
                item["id"],
                project_id,
                item["type"],
            ))

        isotropic_sql = """
        INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            RHO = VALUES(RHO),
            E = VALUES(E),
            NU = VALUES(NU),
            GE = VALUES(GE)
        """
        for item in legacy_materials["isotropic_rows"]:
            cursor.execute(isotropic_sql, (
                item["id"],
                project_id,
                item["rho"],
                item["e"],
                item["nu"],
                item["ge"],
            ))

        property_overview_sql = """
        INSERT INTO t_mt_py_fem_property (Id, pid, Type)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Type = VALUES(Type)
        """
        for item in legacy_properties["overview_rows"]:
            cursor.execute(property_overview_sql, (
                item["id"],
                project_id,
                item["type"],
            ))

        shell_property_sql = """
        INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA, element_set)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Thickness = VALUES(Thickness),
            NSM = VALUES(NSM),
            THETA = VALUES(THETA),
            element_set = VALUES(element_set)
        """
        for item in legacy_properties["shell_rows"]:
            cursor.execute(shell_property_sql, (
                item["id"],
                project_id,
                item["thickness"],
                item["nsm"],
                item["theta"],
                item.get("element_set"),
            ))

        beam_property_sql = """
        INSERT INTO t_mt_py_fem_beam_property
        (Id, pid, AX, AY, AZ, IX, IY, IZ, CW, YN, ZN, NSM)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            AX = VALUES(AX),
            AY = VALUES(AY),
            AZ = VALUES(AZ),
            IX = VALUES(IX),
            IY = VALUES(IY),
            IZ = VALUES(IZ),
            CW = VALUES(CW),
            YN = VALUES(YN),
            ZN = VALUES(ZN),
            NSM = VALUES(NSM)
        """
        for item in legacy_properties["beam_rows"]:
            cursor.execute(beam_property_sql, (
                item["id"],
                project_id,
                item["ax"],
                item["ay"],
                item["az"],
                item["ix"],
                item["iy"],
                item["iz"],
                item["cw"],
                item["yn"],
                item["zn"],
                item["nsm"],
            ))

        boundary_sql = """
        INSERT INTO t_mt_py_fem_boundary
        (Id, pid, Node, UX, UY, UZ, RX, RY, RZ)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Node = VALUES(Node),
            UX = VALUES(UX),
            UY = VALUES(UY),
            UZ = VALUES(UZ),
            RX = VALUES(RX),
            RY = VALUES(RY),
            RZ = VALUES(RZ)
        """
        for item in legacy_boundaries:
            cursor.execute(boundary_sql, (
                item["id"],
                project_id,
                item["node"],
                item["ux"],
                item["uy"],
                item["uz"],
                item["rx"],
                item["ry"],
                item["rz"],
            ))

        parameter_definition_sql = """
        INSERT INTO t_mt_py_fem_parameter_definition
        (pid, parameter_name, expression, scalar_value, is_design_parameter, design_order, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        for item in parameter_definitions:
            cursor.execute(parameter_definition_sql, (
                project_id,
                item["parameter_name"],
                item.get("expression"),
                item.get("scalar_value"),
                1 if item.get("is_design_parameter") else 0,
                item.get("design_order"),
                _json_dumps(item.get("extra_json") or {}),
            ))

        parameter_target_sql = """
        INSERT INTO t_mt_py_fem_parameter_target
        (pid, parameter_name, target_type, set_name, set_type, set_scope, instance_name, part_name,
         source_keyword, source_path, component_name, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for item in parameter_targets:
            cursor.execute(parameter_target_sql, (
                project_id,
                item["parameter_name"],
                item["target_type"],
                item["set_name"],
                item["set_type"],
                item["set_scope"],
                item.get("instance_name"),
                item.get("part_name"),
                item["source_keyword"],
                item["source_path"],
                item.get("component_name"),
                _json_dumps(item.get("extra_json") or {}),
            ))

        quantity_sql = """
        INSERT INTO t_mt_py_fem_supported_quantity
        (quantity_code, quantity_name, unit, enabled, sort_no)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            quantity_name = VALUES(quantity_name),
            unit = VALUES(unit),
            enabled = VALUES(enabled),
            sort_no = VALUES(sort_no)
        """
        for item in supported_quantities:
            cursor.execute(quantity_sql, (
                item["quantity_code"],
                item["quantity_name"],
                item.get("unit"),
                int(item.get("enabled", 1)),
                int(item.get("sort_no", 0)),
            ))

        capability_sql = """
        INSERT INTO t_mt_py_fem_quantity_set_capability
        (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name,
         set_role, element_family, section_type, material_name, member_count,
         is_internal, supports_global, supports_local, current_value, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            set_role = VALUES(set_role),
            element_family = VALUES(element_family),
            section_type = VALUES(section_type),
            material_name = VALUES(material_name),
            member_count = VALUES(member_count),
            is_internal = VALUES(is_internal),
            supports_global = VALUES(supports_global),
            supports_local = VALUES(supports_local),
            current_value = VALUES(current_value),
            extra_json = VALUES(extra_json)
        """
        for item in quantity_set_capabilities:
            cursor.execute(capability_sql, (
                project_id,
                item["quantity_code"],
                item["set_name"],
                item["set_type"],
                item["set_scope"],
                item.get("instance_name"),
                item.get("part_name"),
                item["set_role"],
                item.get("element_family"),
                item.get("section_type"),
                item.get("material_name"),
                int(item["member_count"]),
                int(item.get("is_internal", 0)),
                1 if item.get("supports_global") else 0,
                1 if item.get("supports_local") else 0,
                item.get("current_value"),
                _json_dumps(item.get("extra_json") or {}),
            ))

        if cache_path:
            octree_sql = """
            INSERT INTO t_mt_py_fem_node_octree_cache
            (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                cache_file_path = VALUES(cache_file_path),
                node_count = VALUES(node_count),
                instance_count = VALUES(instance_count),
                bbox_min = VALUES(bbox_min),
                bbox_max = VALUES(bbox_max),
                updated_at = NOW()
            """
            cursor.execute(octree_sql, (
                project_id,
                os.path.abspath(file_path),
                os.path.abspath(cache_path),
                int(len(node_data["point_labels"])),
                int(len(node_data["entries"])),
                _json_dumps(node_data["bbox_min"].tolist()),
                _json_dumps(node_data["bbox_max"].tolist()),
            ))

        project_config = save_fem_model_dimensions(
            project_id=project_id,
            bbox_min=node_data["bbox_min"],
            bbox_max=node_data["bbox_max"],
            cursor=cursor,
        )

        conn.commit()
        result = {
            "file_path": os.path.abspath(file_path),
            "project_id": project_id,
            "material_count": len(legacy_materials["overview_rows"]),
            "isotropic_material_count": len(legacy_materials["isotropic_rows"]),
            "property_count": len(legacy_properties["overview_rows"]),
            "shell_property_count": len(legacy_properties["shell_rows"]),
            "beam_property_count": len(legacy_properties["beam_rows"]),
            "boundary_count": len(legacy_boundaries),
            "parameter_definition_count": len(parameter_definitions),
            "parameter_target_count": len(parameter_targets),
            "design_response_count": len(design_responses),
            "supported_quantity_count": len(supported_quantities),
            "quantity_set_capability_count": len(quantity_set_capabilities),
            "instance_count": len(node_data["entries"]),
            "node_count": int(len(node_data["point_labels"])),
            "octree_cache_path": os.path.abspath(cache_path) if cache_path else None,
            "project_config": project_config,
            "diagnostics": len(getattr(model, "diagnostics", []) or []),
            "parameter_definitions_preview": parameter_definitions[:10],
            "parameter_targets_preview": parameter_targets[:10],
            "design_responses_preview": design_responses[:10],
            "supported_quantities_preview": supported_quantities[:10],
            "quantity_set_capabilities_preview": quantity_set_capabilities[:10],
        }
        safe_write_console_event(
            int(project_id),
            "INP导入完成",
            [
                f"文件: {os.path.abspath(file_path)}",
                f"节点数: {int(len(node_data['point_labels']))}",
                f"实例数: {len(node_data['entries'])}",
                f"设计参数数: {len(parameter_definitions)}",
                f"设计响应数: {len(design_responses)}",
            ],
        )
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_inp_catalog(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT parameter_name, expression, scalar_value, is_design_parameter, design_order, extra_json
            FROM t_mt_py_fem_parameter_definition
            WHERE pid = %s
            ORDER BY is_design_parameter DESC, design_order, parameter_name
        """, (project_id,))
        parameter_definitions = cursor.fetchall()

        cursor.execute("""
            SELECT parameter_name, target_type, set_name, set_type, set_scope, instance_name, part_name,
                   source_keyword, source_path, component_name, extra_json
            FROM t_mt_py_fem_parameter_target
            WHERE pid = %s
            ORDER BY parameter_name, source_path
        """, (project_id,))
        parameter_targets = cursor.fetchall()

        cursor.execute("""
            SELECT response_no, request_no, step_name, frequency, region_type, set_name, variables_json, extra_json
            FROM t_mt_py_fem_static_sensitivity_response_catalog
            WHERE pid = %s
            ORDER BY response_no, request_no
        """, (project_id,))
        design_responses = cursor.fetchall()

        cursor.execute("""
            SELECT quantity_code, quantity_name, unit, enabled, sort_no
            FROM t_mt_py_fem_supported_quantity
            WHERE enabled = 1
            ORDER BY sort_no, quantity_code
        """)
        supported_quantities = cursor.fetchall()

        cursor.execute("""
            SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name,
                   set_role, element_family, section_type, material_name, member_count,
                   is_internal, supports_global, supports_local, current_value, extra_json
            FROM t_mt_py_fem_quantity_set_capability
            WHERE pid = %s
            ORDER BY quantity_code, set_scope, set_type, set_name, instance_name, part_name
        """, (project_id,))
        quantity_set_capabilities = cursor.fetchall()

        cursor.execute("""
            SELECT parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope,
                   instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, extra_json, created_at
            FROM t_mt_py_fem_selected_parameter
            WHERE pid = %s
            ORDER BY created_at DESC, parameter_name
        """, (project_id,))
        opt_params = cursor.fetchall()

        cursor.execute("""
            SELECT source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at
            FROM t_mt_py_fem_node_octree_cache
            WHERE pid = %s
            ORDER BY updated_at DESC
            LIMIT 1
        """, (project_id,))
        octree = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_node_match WHERE pid = %s", (project_id,))
        node_match_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_dof_match WHERE pid = %s", (project_id,))
        dof_match_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s", (project_id,))
        response_catalog_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(DISTINCT mode_no) AS cnt FROM t_mt_py_fem_modal_result WHERE pid = %s", (project_id,))
        fem_mode_count = int(cursor.fetchone()["cnt"])
        if fem_mode_count == 0:
            fem_mode_count = int(len(list_fem_modal_frequencies(int(project_id), cursor=cursor)))
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))
        correlation_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_material_overview WHERE pid = %s", (project_id,))
        material_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_isotropic WHERE pid = %s", (project_id,))
        isotropic_material_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_property WHERE pid = %s", (project_id,))
        property_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_shell_property WHERE pid = %s", (project_id,))
        shell_property_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_beam_property WHERE pid = %s", (project_id,))
        beam_property_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_boundary WHERE pid = %s", (project_id,))
        boundary_count = int(cursor.fetchone()["cnt"])

        return {
            "project_id": project_id,
            "material_count": material_count,
            "isotropic_material_count": isotropic_material_count,
            "property_count": property_count,
            "shell_property_count": shell_property_count,
            "beam_property_count": beam_property_count,
            "boundary_count": boundary_count,
            "parameter_definition_count": len(parameter_definitions),
            "parameter_target_count": len(parameter_targets),
            "design_response_count": len(design_responses),
            "supported_quantity_count": len(supported_quantities),
            "quantity_set_capability_count": len(quantity_set_capabilities),
            "parameter_definitions": parameter_definitions,
            "parameter_targets": parameter_targets,
            "design_responses": design_responses,
            "supported_quantities": supported_quantities,
            "quantity_set_capabilities": quantity_set_capabilities,
            "optimization_parameters": opt_params,
            "octree_cache": octree,
            "node_match_count": node_match_count,
            "dof_match_count": dof_match_count,
            "response_catalog_count": response_catalog_count,
            "fem_mode_count": fem_mode_count,
            "modal_correlation_count": correlation_count,
        }
    finally:
        cursor.close()
        conn.close()


def get_inp_parameter_options(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT quantity_code, quantity_name, unit, enabled, sort_no
            FROM t_mt_py_fem_supported_quantity
            WHERE enabled = 1
            ORDER BY sort_no, quantity_code
        """)
        supported_quantities = cursor.fetchall() or []

        cursor.execute("""
            SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name,
                   set_role, element_family, section_type, material_name, member_count,
                   is_internal, supports_global, supports_local, current_value, extra_json
            FROM t_mt_py_fem_quantity_set_capability
            WHERE pid = %s
            AND supports_global=1
            ORDER BY quantity_code, set_scope, set_type, set_name, instance_name, part_name
        """, (project_id,))
        quantity_set_capabilities = cursor.fetchall() or []

        return _build_inp_parameter_options(
            supported_quantities=supported_quantities,
            quantity_set_capabilities=quantity_set_capabilities,
        )
    finally:
        cursor.close()
        conn.close()


def _load_project_inp_model(project_id: int):
    resolved_path = resolve_project_source_inp_path(int(project_id))
    return parse_inp(resolved_path), os.path.abspath(resolved_path)


def _model_instance_rows(model) -> List[dict]:
    rows: List[dict] = []
    if model.assembly and model.assembly.instances:
        for instance_name, inst in sorted(model.assembly.instances.items()):
            rows.append(
                {
                    "instance_name": str(instance_name),
                    "part_name": str(inst.part_name),
                    "set_scope": "ASSEMBLY",
                }
            )
        return rows

    for part_name in sorted(model.parts.keys()):
        rows.append(
            {
                "instance_name": str(part_name),
                "part_name": str(part_name),
                "set_scope": "PART",
            }
        )
    return rows


def get_project_abaqus_instances_and_steps(project_id: int) -> dict:
    model, source_inp = _load_project_inp_model(int(project_id))
    instances = _model_instance_rows(model)
    steps = []
    for idx, step in enumerate(list(getattr(model, "steps", []) or []), start=1):
        steps.append(
            {
                "step_name": str(getattr(step, "name", None) or f"Step-{idx}"),
                "step_index": int(idx - 1),
            }
        )
    return {
        "project_id": int(project_id),
        "source_inp": source_inp,
        "instance_count": len(instances),
        "step_count": len(steps),
        "instances": instances,
        "steps": steps,
    }


def resolve_abaqus_instance_context(project_id: int, instance_name: str) -> dict:
    resolved_instance_name = str(instance_name or "").strip()
    if not resolved_instance_name:
        raise ValidationError("instance_name is required", {"instance_name": instance_name})

    model, source_inp = _load_project_inp_model(int(project_id))
    if model.assembly and model.assembly.instances:
        inst = model.assembly.instances.get(resolved_instance_name)
        if inst is None:
            raise NotFoundError(
                "instance not found in project inp model",
                {"project_id": int(project_id), "instance_name": resolved_instance_name, "source_inp": source_inp},
            )
        return {
            "project_id": int(project_id),
            "source_inp": source_inp,
            "instance_name": resolved_instance_name,
            "part_name": str(inst.part_name),
            "set_scope": "ASSEMBLY",
        }

    if resolved_instance_name not in model.parts:
        raise NotFoundError(
            "instance not found in project inp model",
            {"project_id": int(project_id), "instance_name": resolved_instance_name, "source_inp": source_inp},
        )
    return {
        "project_id": int(project_id),
        "source_inp": source_inp,
        "instance_name": resolved_instance_name,
        "part_name": resolved_instance_name,
        "set_scope": "PART",
    }


def validate_abaqus_instance_node_label(project_id: int, instance_name: str, node_label: int) -> dict:
    context = resolve_abaqus_instance_context(int(project_id), instance_name)
    model, _source_inp = _load_project_inp_model(int(project_id))
    part = model.parts.get(str(context["part_name"]))
    exists = bool(part is not None and int(node_label) in getattr(part, "nodes", {}))
    return {
        "project_id": int(project_id),
        "instance_name": str(context["instance_name"]),
        "part_name": str(context["part_name"]),
        "set_scope": str(context["set_scope"]),
        "node_label": int(node_label),
        "exists": bool(exists),
    }


def _default_parameter_group_name(quantity_code: str, set_name: str) -> str:
    return f"{str(quantity_code).upper()}@{str(set_name)}"


def _extract_target_keys(extra_json) -> set:
    payload = _json_loads(extra_json) or {}
    return {str(item) for item in (payload.get("target_keys") or [])}


def _set_identity_key(row: dict) -> Tuple[str, str, str, Optional[str], Optional[str]]:
    return (
        str(row.get("set_name") or ""),
        str(row.get("set_type") or ""),
        str(row.get("set_scope") or ""),
        str(row.get("instance_name")) if row.get("instance_name") is not None else None,
        str(row.get("part_name")) if row.get("part_name") is not None else None,
    )


def _scope_identity_filter(set_scope: str) -> Tuple[str, str]:
    normalized_scope = str(set_scope or "").upper()
    if normalized_scope == "ASSEMBLY":
        return "instance_name", normalized_scope
    return "part_name", normalized_scope


def _resolve_selection_mode_from_capability(capability_row: dict, requested_mode: Optional[str]) -> str:
    if requested_mode:
        mode = _normalize_selection_mode(requested_mode)
    else:
        mode = "GLOBAL" if capability_row.get("supports_global") else "LOCAL"

    if mode == "GLOBAL" and not capability_row.get("supports_global"):
        raise ValueError("the selected set does not support GLOBAL for this quantity")
    if mode == "LOCAL" and not capability_row.get("supports_local"):
        raise ValueError("the selected set does not support LOCAL for this quantity")
    return mode


def _resolve_quantity_set_capability_for_parameter_create(
        cursor,
        *,
        project_id: int,
        quantity_code_candidates: List[str],
        set_name: str,
        set_type: Optional[str] = None,
        set_scope: Optional[str] = None,
        instance_name: Optional[str] = None,
        part_name: Optional[str] = None,
) -> dict:
    # Parameter creation accepts a short "quantity + set" reference from the
    # caller, then resolves it into one concrete capability row. That row is
    # the shared bridge from Abaqus/Nastran selection semantics to the formal
    # selected-parameter record used by sensitivity/update workflows.
    query = f"""
        SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name,
               set_role, element_family, section_type, material_name, member_count,
               supports_global, supports_local, current_value, extra_json
        FROM t_mt_py_fem_quantity_set_capability
        WHERE pid = %s AND quantity_code IN ({", ".join(["%s"] * len(quantity_code_candidates))}) AND set_name = %s
    """
    params = [int(project_id), *quantity_code_candidates, set_name]
    if set_type:
        query += " AND set_type = %s"
        params.append(set_type)
    if set_scope:
        query += " AND set_scope = %s"
        params.append(set_scope)
    if instance_name:
        query += " AND instance_name = %s"
        params.append(instance_name)
    if part_name:
        query += " AND part_name = %s"
        params.append(part_name)
    query += " LIMIT 2"

    cursor.execute(query, tuple(params))
    capability_rows = cursor.fetchall() or []
    if not capability_rows:
        raise ValueError(f"quantity/set capability not found: {quantity_code_candidates[0]} @ {set_name}")
    if len(capability_rows) > 1:
        raise ValueError(
            "multiple quantity/set capabilities matched; specify set_scope/set_type/instance_name/part_name"
        )
    return dict(capability_rows[0])


def _safe_manual_set_name(quantity_code: str, selection_mode: str, parameter_name: Optional[str]) -> str:
    raw = str(parameter_name or f"{quantity_code}_{selection_mode}").strip()
    token = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_") or f"{quantity_code}_{selection_mode}"
    return f"MANUAL_{str(quantity_code).upper()}_{str(selection_mode).upper()}_{token}"


def _safe_manual_response_set_name(region_type: str, response_name: Optional[str], response_no: int) -> str:
    region = str(region_type or "").strip().upper()
    prefix = "NODE" if region == "NODE" else "ELEMENT"
    raw = str(response_name or f"RESP_{int(response_no)}").strip()
    token = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_") or f"RESP_{int(response_no)}"
    return f"MANUAL_RESP_{prefix}_{token}_{int(response_no)}"


def _build_static_sensitivity_response_signature(
        *,
        region_type: str,
        set_name: Optional[str],
        set_scope: Optional[str],
        instance_name: Optional[str],
        part_name: Optional[str],
        step_name: Optional[str],
        frequency: int,
        variables,
        node_labels: Optional[List[int]] = None,
        element_labels: Optional[List[int]] = None,
        manual_mode: bool = False,
) -> dict:
    payload = {
        "region_type": str(region_type or "").strip().upper(),
        "set_name": str(set_name or "").strip(),
        "set_scope": str(set_scope or "").strip().upper(),
        "instance_name": str(instance_name or "").strip(),
        "part_name": str(part_name or "").strip(),
        "step_name": str(step_name or "").strip(),
        "frequency": int(frequency),
        "variables": tuple(str(item or "").strip().upper() for item in (variables or [])),
        "manual_mode": bool(manual_mode),
    }
    if manual_mode:
        payload["node_labels"] = tuple(sorted(int(item) for item in (node_labels or [])))
        payload["element_labels"] = tuple(sorted(int(item) for item in (element_labels or [])))
        payload["set_name"] = ""
    return payload


def _extract_static_sensitivity_response_signature(row: dict) -> dict:
    extra_json = _parse_optional_json_object(row.get("extra_json"))
    variables = _json_loads(row.get("variables_json")) or []
    node_labels = [int(item) for item in (extra_json.get("node_labels") or [])]
    element_labels = [int(item) for item in (extra_json.get("element_labels") or [])]
    manual_mode = bool(
        str(extra_json.get("set_source") or "").strip().lower() == "manual"
        or node_labels
        or element_labels
    )
    return _build_static_sensitivity_response_signature(
        region_type=row.get("region_type"),
        set_name=row.get("set_name"),
        set_scope=row.get("set_scope"),
        instance_name=row.get("instance_name"),
        part_name=row.get("part_name"),
        step_name=row.get("step_name"),
        frequency=int(row.get("frequency") or 1),
        variables=variables,
        node_labels=node_labels,
        element_labels=element_labels,
        manual_mode=manual_mode,
    )


def _row_element_labels(row: dict) -> set:
    labels = set()
    element_label = row.get("element_label")
    if element_label not in (None, ""):
        labels.add(int(element_label))
    payload = _json_loads(row.get("extra_json")) or {}
    for item in payload.get("element_labels") or []:
        labels.add(int(item))
    return labels


def _resolve_manual_element_current_values(
        cursor,
        *,
        project_id: int,
        quantity_code_candidates: List[str],
        element_labels: List[int],
) -> Dict[int, float]:
    cursor.execute(
        f"""
        SELECT set_name, current_value, extra_json
        FROM t_mt_py_fem_quantity_set_capability
        WHERE pid = %s
          AND quantity_code IN ({", ".join(["%s"] * len(quantity_code_candidates))})
        """,
        (int(project_id), *quantity_code_candidates),
    )
    requested = {int(label) for label in element_labels}
    value_map: Dict[int, float] = {}
    for row in cursor.fetchall() or []:
        extra = _json_loads(row.get("extra_json")) or {}
        row_labels = [int(item) for item in (extra.get("element_labels") or [])]
        if not row_labels:
            continue
        row_values = {
            int(key): _safe_float(value)
            for key, value in dict(extra.get("element_values") or {}).items()
            if _safe_float(value) is not None
        }
        shared_value = _safe_float(row.get("current_value"))
        for label in row_labels:
            if label not in requested:
                continue
            candidate = row_values.get(label, shared_value)
            if candidate is None:
                continue
            existing = value_map.get(label)
            if existing is not None and not np.isclose(existing, candidate):
                raise ValueError(
                    f"conflicting current_value detected for element_label={label}"
                )
            value_map[label] = float(candidate)

    missing = sorted(label for label in requested if label not in value_map)
    if missing:
        raise ValueError(
            "could not resolve current_value for some element_labels from imported inp catalog"
        )
    return value_map


def _safe_parameter_token(raw: Optional[str], fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", str(raw or "").strip()).strip("_")
    return token or str(fallback)


def _create_all_elements_e_parameters(
        cursor,
        *,
        project_id: int,
        quantity_code: str,
        lower: float,
        upper: float,
        prob_id: int,
        selection_mode: Optional[str],
        parameter_name: Optional[str],
        scatter: float,
        description: str,
        usage_scope: Sequence[str],
        current_value: Optional[float],
) -> dict:
    if str(quantity_code).upper() != "E":
        raise ValidationError(
            "all_elements_e only supports quantity_code=E",
            {"quantity_code": quantity_code},
        )
    if current_value is not None:
        raise ValidationError(
            "all_elements_e does not support overriding current_value",
            {"current_value": current_value},
        )
    resolved_mode = _normalize_selection_mode(selection_mode or "LOCAL")
    if resolved_mode != "LOCAL":
        raise ValidationError(
            "all_elements_e only supports LOCAL selection_mode",
            {"selection_mode": selection_mode, "resolved_selection_mode": resolved_mode},
        )

    cursor.execute(
        """
        SELECT set_name, set_type, set_scope, instance_name, part_name, set_role, current_value, extra_json
        FROM t_mt_py_fem_quantity_set_capability
        WHERE pid = %s AND quantity_code = %s AND supports_local = 1
        ORDER BY
            CASE WHEN set_scope = 'ASSEMBLY' THEN 0 ELSE 1 END,
            CASE WHEN set_role = 'PROPERTY_SET' THEN 0 ELSE 1 END,
            set_name, instance_name, part_name
        """,
        (int(project_id), "E"),
    )
    capability_rows = cursor.fetchall() or []
    if not capability_rows:
        raise ValidationError(
            "no local E capability rows were found; import the inp catalog first",
            {"project_id": int(project_id), "quantity_code": "E"},
        )

    parameter_group_name = str(parameter_name or "ALL_ELEMENTS_E").strip() or "ALL_ELEMENTS_E"
    virtual_set_name = f"ALL_ELEMENTS_E::{_safe_parameter_token(parameter_group_name, 'ALL_ELEMENTS_E')}"
    deduped_targets: Dict[str, dict] = {}
    for capability_row in capability_rows:
        capability_extra = _json_loads(capability_row.get("extra_json")) or {}
        target_keys_by_label = {
            str(key): [str(item) for item in (value or []) if str(item or "").strip()]
            for key, value in dict(capability_extra.get("target_keys_by_label") or {}).items()
        }
        element_values = {
            str(key): _safe_float(value)
            for key, value in dict(capability_extra.get("element_values") or {}).items()
        }
        shared_value = _safe_float(capability_row.get("current_value"))
        for raw_label, raw_target_keys in target_keys_by_label.items():
            element_label = int(raw_label)
            current_element_value = element_values.get(str(element_label), shared_value)
            if current_element_value is None:
                continue
            for target_key in raw_target_keys:
                target_key = str(target_key).strip()
                if not target_key:
                    continue
                scope, scope_name, _ = target_key.split("::", 2)
                item = deduped_targets.get(target_key)
                if item is None:
                    deduped_targets[target_key] = {
                        "target_key": target_key,
                        "element_label": element_label,
                        "set_scope": "ASSEMBLY" if scope == "INST" else "PART",
                        "instance_name": scope_name if scope == "INST" else None,
                        "part_name": scope_name if scope == "PART" else capability_row.get("part_name"),
                        "current_value": float(current_element_value),
                        "source_set_names": [str(capability_row.get("set_name") or "")],
                    }
                    continue
                existing_value = _safe_float(item.get("current_value"))
                if existing_value is not None and not np.isclose(existing_value, current_element_value):
                    raise ValidationError(
                        "conflicting E values were resolved for the same element target",
                        {
                            "target_key": target_key,
                            "existing_value": existing_value,
                            "incoming_value": float(current_element_value),
                        },
                    )
                source_set_names = set(item.get("source_set_names") or [])
                source_set_names.add(str(capability_row.get("set_name") or ""))
                item["source_set_names"] = sorted(source_set_names)

    if not deduped_targets:
        raise ValidationError(
            "all_elements_e did not resolve any element targets from the imported inp catalog",
            {"project_id": int(project_id), "quantity_code": "E"},
        )

    cursor.execute(
        """
        SELECT set_scope, instance_name, part_name, element_label, extra_json
        FROM t_mt_py_fem_selected_parameter
        WHERE pid = %s AND quantity_code = %s
        """,
        (int(project_id), "E"),
    )
    existing_target_keys = set()
    existing_scope_keys = set()
    for row in cursor.fetchall() or []:
        existing_target_keys.update(_extract_target_keys(row.get("extra_json")))
        element_label = row.get("element_label")
        if element_label is None:
            continue
        existing_scope_keys.add((
            str(row.get("set_scope") or "").upper(),
            str(row.get("instance_name") or ""),
            str(row.get("part_name") or ""),
            int(element_label),
        ))

    for target_key, item in deduped_targets.items():
        if target_key in existing_target_keys:
            raise ValidationError(
                "all_elements_e overlaps with an existing E parameter",
                {"target_key": target_key},
            )
        scope_key = (
            str(item.get("set_scope") or "").upper(),
            str(item.get("instance_name") or ""),
            str(item.get("part_name") or ""),
            int(item["element_label"]),
        )
        if scope_key in existing_scope_keys:
            raise ValidationError(
                "all_elements_e overlaps with an existing E parameter",
                {
                    "set_scope": item.get("set_scope"),
                    "instance_name": item.get("instance_name"),
                    "part_name": item.get("part_name"),
                    "element_label": int(item["element_label"]),
                },
            )

    insert_sql = """
    INSERT INTO t_mt_py_fem_selected_parameter
    (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope,
     instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    created_parameters = []
    insert_rows = []
    for item in sorted(
            deduped_targets.values(),
            key=lambda row: (
                    str(row.get("set_scope") or ""),
                    str(row.get("instance_name") or ""),
                    str(row.get("part_name") or ""),
                    int(row["element_label"]),
            ),
    ):
        scope_token = _safe_parameter_token(
            item.get("instance_name") or item.get("part_name"),
            item.get("set_scope") or "ELEMENT",
        )
        resolved_parameter_name = f"{parameter_group_name}_{scope_token}_EL{int(item['element_label'])}"
        extra_json = {
            "target_keys": [str(item["target_key"])],
            "element_labels": [int(item["element_label"])],
            "set_source": "all_elements_e",
            "virtual_set_name": virtual_set_name,
            "source_set_names": list(item.get("source_set_names") or []),
        }
        insert_rows.append((
            int(project_id),
            parameter_group_name,
            resolved_parameter_name,
            "E",
            resolved_mode,
            virtual_set_name,
            "AUTO_ALL_ELEMENTS_E",
            item["set_scope"],
            item.get("instance_name"),
            item.get("part_name"),
            int(item["element_label"]),
            float(item["current_value"]),
            float(lower),
            float(upper),
            int(prob_id),
            float(scatter),
            description or "",
            _json_dumps(list(usage_scope or [])),
            _json_dumps(extra_json),
        ))
        created_parameters.append({
            "parameter_name": resolved_parameter_name,
            "element_label": int(item["element_label"]),
            "set_scope": item["set_scope"],
            "instance_name": item.get("instance_name"),
            "part_name": item.get("part_name"),
            "current_value": float(item["current_value"]),
        })

    if len(insert_rows) == 1:
        cursor.execute(insert_sql, insert_rows[0])
    elif hasattr(cursor, "executemany"):
        cursor.executemany(insert_sql, insert_rows)
    else:
        for row in insert_rows:
            cursor.execute(insert_sql, row)

    return {
        "project_id": int(project_id),
        "parameter_group_name": parameter_group_name,
        "quantity_code": "E",
        "selection_mode": resolved_mode,
        "set_name": virtual_set_name,
        "set_type": "AUTO_ALL_ELEMENTS_E",
        "set_scope": "MIXED",
        "instance_name": None,
        "part_name": None,
        "lower": float(lower),
        "upper": float(upper),
        "prob_id": int(prob_id),
        "scatter": float(scatter),
        "description": description or "",
        "usage_scope": list(usage_scope or []),
        "created_parameter_count": len(created_parameters),
        "created_parameters_preview": created_parameters[:20],
        "all_elements_e": True,
    }


def _normalize_response_variables(raw_variables) -> List[str]:
    if not isinstance(raw_variables, (list, tuple, set)):
        raise ValidationError("variables must be a non-empty list", {"variables": raw_variables})
    result = []
    seen = set()
    for item in raw_variables:
        token = str(item or "").strip().upper()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    if not result:
        raise ValidationError("variables must contain at least one non-empty item", {"variables": raw_variables})
    return result


def create_optimization_parameter(project_id, candidate_code=None, quantity_code=None, lower=None, upper=None, prob_id=0,
                                  selection_mode=None, set_name=None, parameter_name=None, scatter=None,
                                  description="", set_type=None, set_scope=None,
                                  instance_name=None, part_name=None,
                                  element_labels=None, current_value=None,
                                  usage_scope=None, all_elements_e: bool = False):
    # This API turns a generic candidate type plus one cataloged set into a
    # concrete optimization parameter record that Bayesian update can address.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        resolved_quantity_code = _normalize_quantity_code(
            quantity_code or _derive_quantity_code_from_candidate(candidate_code)
        )
        if lower is None or upper is None:
            raise ValueError("lower and upper are required")
        resolved_lower = float(lower)
        resolved_upper = float(upper)
        if resolved_lower > resolved_upper:
            raise ValueError("lower must be <= upper")
        resolved_prob_id = int(prob_id or 0)
        quantity_code_candidates = [resolved_quantity_code]
        if resolved_quantity_code == "T":
            quantity_code_candidates.append("H")
        provided_element_labels = [int(item) for item in (element_labels or [])]
        has_manual_elements = bool(provided_element_labels)
        if has_manual_elements and set_name:
            raise ValueError("set_name and element_labels cannot be provided together")
        if not all_elements_e and not has_manual_elements and not set_name:
            raise ValueError("set_name is required when element_labels is not provided")

        resolved_scatter = float(
            _DEFAULT_PARAMETER_SCATTER if scatter is None else scatter
        )
        resolved_usage_scope = _normalize_parameter_usage_scope(usage_scope)
        if resolved_scatter <= 0:
            raise ValueError("scatter must be > 0")
        if all_elements_e:
            result = _create_all_elements_e_parameters(
                cursor,
                project_id=int(project_id),
                quantity_code=resolved_quantity_code,
                lower=resolved_lower,
                upper=resolved_upper,
                prob_id=resolved_prob_id,
                selection_mode=selection_mode,
                parameter_name=parameter_name,
                scatter=resolved_scatter,
                description=description,
                usage_scope=resolved_usage_scope,
                current_value=current_value,
            )
            conn.commit()
            return result

        if has_manual_elements:
            resolved_mode = _normalize_selection_mode(selection_mode or "LOCAL")
            display_quantity_code = str(quantity_code or resolved_quantity_code).strip().upper() or resolved_quantity_code
            parameter_group_name = str(
                parameter_name or _default_parameter_group_name(
                    resolved_quantity_code,
                    _safe_manual_set_name(display_quantity_code, resolved_mode, parameter_name),
                )
            )
            manual_set_name = str(set_name or _safe_manual_set_name(
                display_quantity_code,
                resolved_mode,
                parameter_group_name,
            ))
            resolved_current_value = None if current_value is None else _safe_float(current_value)
            resolved_element_values: Dict[int, float] = {}
            if resolved_current_value is None:
                resolved_element_values = _resolve_manual_element_current_values(
                    cursor,
                    project_id=int(project_id),
                    quantity_code_candidates=quantity_code_candidates,
                    element_labels=provided_element_labels,
                )
                if resolved_mode == "GLOBAL":
                    distinct_values = sorted({float(value) for value in resolved_element_values.values()})
                    if len(distinct_values) != 1:
                        raise ValueError(
                            "manual GLOBAL parameter spans multiple current values; specify current_value explicitly or switch to LOCAL"
                        )
                    resolved_current_value = float(distinct_values[0])

            incoming_element_label_set = {int(label) for label in provided_element_labels}
            cursor.execute(
                f"""
                SELECT set_name, set_type, set_scope, instance_name, part_name, element_label, extra_json
                FROM t_mt_py_fem_selected_parameter
                WHERE pid = %s
                  AND quantity_code IN ({", ".join(["%s"] * len(quantity_code_candidates))})
                """,
                (project_id, *quantity_code_candidates),
            )
            for row in cursor.fetchall() or []:
                if incoming_element_label_set & _row_element_labels(row):
                    raise ValueError("已存在相同集合的相同修正量，重复定义")

            insert_sql = """
            INSERT INTO t_mt_py_fem_selected_parameter
            (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope,
             instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            created_parameters = []
            insert_rows = []
            if resolved_mode == "GLOBAL":
                resolved_parameter_name = parameter_group_name
                insert_rows.append((
                    project_id,
                    parameter_group_name,
                    resolved_parameter_name,
                    resolved_quantity_code,
                    resolved_mode,
                    manual_set_name,
                    str(set_type or "ELSET"),
                    str(set_scope or "MANUAL"),
                    instance_name,
                    part_name,
                    None,
                    resolved_current_value,
                    resolved_lower,
                    resolved_upper,
                    resolved_prob_id,
                    resolved_scatter,
                    description or "",
                    _json_dumps(resolved_usage_scope),
                    _json_dumps({
                        "target_keys": [],
                        "element_labels": provided_element_labels,
                        "set_source": "manual",
                        "virtual_set_name": manual_set_name,
                        "current_value": resolved_current_value,
                    }),
                ))
                created_parameters.append(
                    {
                        "parameter_name": resolved_parameter_name,
                        "element_label": None,
                        "current_value": resolved_current_value,
                    }
                )
            else:
                for element_label in provided_element_labels:
                    resolved_parameter_name = f"{parameter_group_name}_EL{int(element_label)}"
                    row_current_value = resolved_current_value
                    if row_current_value is None:
                        row_current_value = resolved_element_values.get(int(element_label))
                    insert_rows.append((
                        project_id,
                        parameter_group_name,
                        resolved_parameter_name,
                        resolved_quantity_code,
                        resolved_mode,
                        manual_set_name,
                        str(set_type or "ELSET"),
                        str(set_scope or "MANUAL"),
                        instance_name,
                        part_name,
                        int(element_label),
                        row_current_value,
                        resolved_lower,
                        resolved_upper,
                        resolved_prob_id,
                        resolved_scatter,
                        description or "",
                        _json_dumps(resolved_usage_scope),
                        _json_dumps({
                            "target_keys": [],
                            "element_labels": [int(element_label)],
                            "set_source": "manual",
                            "virtual_set_name": manual_set_name,
                            "current_value": row_current_value,
                        }),
                    ))
                    created_parameters.append(
                        {
                            "parameter_name": resolved_parameter_name,
                            "element_label": int(element_label),
                            "current_value": row_current_value,
                        }
                    )

            if len(insert_rows) == 1:
                cursor.execute(insert_sql, insert_rows[0])
            elif hasattr(cursor, "executemany"):
                cursor.executemany(insert_sql, insert_rows)
            else:
                for row in insert_rows:
                    cursor.execute(insert_sql, row)
            conn.commit()

            return {
                "project_id": project_id,
                "parameter_group_name": parameter_group_name,
                "quantity_code": resolved_quantity_code,
                "selection_mode": resolved_mode,
                "set_name": manual_set_name,
                "set_type": str(set_type or "ELSET"),
                "set_scope": str(set_scope or "MANUAL"),
                "instance_name": instance_name,
                "part_name": part_name,
                "lower": resolved_lower,
                "upper": resolved_upper,
                "prob_id": resolved_prob_id,
                "scatter": resolved_scatter,
                "description": description or "",
                "usage_scope": resolved_usage_scope,
                "created_parameter_count": len(created_parameters),
                "created_parameters_preview": created_parameters[:20],
                "element_labels_preview": provided_element_labels[:20],
                "manual_set_created": True,
            }

        capability_row = _resolve_quantity_set_capability_for_parameter_create(
            cursor,
            project_id=int(project_id),
            quantity_code_candidates=quantity_code_candidates,
            set_name=str(set_name),
            set_type=str(set_type) if set_type else None,
            set_scope=str(set_scope) if set_scope else None,
            instance_name=str(instance_name) if instance_name else None,
            part_name=str(part_name) if part_name else None,
        )

        resolved_mode = _resolve_selection_mode_from_capability(capability_row, selection_mode)
        parameter_group_name = str(parameter_name or _default_parameter_group_name(resolved_quantity_code, capability_row["set_name"]))

        capability_extra = _json_loads(capability_row.get("extra_json")) or {}
        element_labels = [int(item) for item in (capability_extra.get("element_labels") or [])]
        target_keys = [str(item) for item in (capability_extra.get("target_keys") or [])]
        target_keys_by_label = {
            str(key): [str(item) for item in (value or [])]
            for key, value in dict(capability_extra.get("target_keys_by_label") or {}).items()
        }
        element_values = {
            str(key): _safe_float(value)
            for key, value in dict(capability_extra.get("element_values") or {}).items()
        }
        if not element_labels or not target_keys:
            raise ValueError("selected capability row does not contain target element information")

        incoming_element_label_set = {int(label) for label in element_labels}
        scope_identity_field, normalized_scope = _scope_identity_filter(capability_row.get("set_scope"))
        scope_identity_value = capability_row.get(scope_identity_field)
        cursor.execute(f"""
            SELECT set_name, set_type, set_scope, instance_name, part_name, element_label
            FROM t_mt_py_fem_selected_parameter
            WHERE pid = %s
              AND quantity_code IN ({", ".join(["%s"] * len(quantity_code_candidates))})
              AND set_scope = %s
              AND {scope_identity_field} <=> %s
        """, (project_id, *quantity_code_candidates, normalized_scope, scope_identity_value))
        overlap_global_set_keys = set()
        for row in cursor.fetchall() or []:
            element_label = row.get("element_label")
            if element_label is not None:
                if int(element_label) in incoming_element_label_set:
                    raise ValueError("the selected set overlaps with an existing parameter of the same quantity")
                continue
            overlap_global_set_keys.add(_set_identity_key(row))

        if overlap_global_set_keys:
            overlapping_set_names = sorted({key[0] for key in overlap_global_set_keys})
            cursor.execute(f"""
                SELECT set_name, set_type, set_scope, instance_name, part_name, extra_json
                FROM t_mt_py_fem_quantity_set_capability
                WHERE pid = %s
                  AND quantity_code IN ({", ".join(["%s"] * len(quantity_code_candidates))})
                  AND set_scope = %s
                  AND {scope_identity_field} <=> %s
                  AND set_name IN ({", ".join(["%s"] * len(overlapping_set_names))})
            """, (project_id, *quantity_code_candidates, normalized_scope, scope_identity_value, *overlapping_set_names))
            for row in cursor.fetchall() or []:
                if _set_identity_key(row) not in overlap_global_set_keys:
                    continue
                existing_extra = _json_loads(row.get("extra_json")) or {}
                existing_labels = {
                    int(label) for label in (existing_extra.get("element_labels") or [])
                }
                if incoming_element_label_set & existing_labels:
                    raise ValueError("the selected set overlaps with an existing parameter of the same quantity")

        insert_sql = """
        INSERT INTO t_mt_py_fem_selected_parameter
        (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope,
         instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        created_parameters = []
        insert_rows = []
        if resolved_mode == "GLOBAL":
            resolved_parameter_name = parameter_group_name
            insert_rows.append((
                project_id,
                parameter_group_name,
                resolved_parameter_name,
                resolved_quantity_code,
                resolved_mode,
                capability_row["set_name"],
                capability_row["set_type"],
                capability_row["set_scope"],
                capability_row["instance_name"],
                capability_row["part_name"],
                None,
                capability_row.get("current_value"),
                resolved_lower,
                resolved_upper,
                resolved_prob_id,
                resolved_scatter,
                description or "",
                _json_dumps(resolved_usage_scope),
                _json_dumps({
                    "target_keys": target_keys,
                    "element_labels": element_labels,
                }),
            ))
            created_parameters.append(
                {
                    "parameter_name": resolved_parameter_name,
                    "element_label": None,
                    "current_value": capability_row.get("current_value"),
                }
            )
        else:
            for element_label in element_labels:
                resolved_parameter_name = f"{parameter_group_name}_EL{int(element_label)}"
                row_target_keys = target_keys_by_label.get(str(element_label)) or []
                current_value = element_values.get(str(element_label), capability_row.get("current_value"))
                insert_rows.append((
                    project_id,
                    parameter_group_name,
                    resolved_parameter_name,
                    resolved_quantity_code,
                    resolved_mode,
                    capability_row["set_name"],
                    capability_row["set_type"],
                    capability_row["set_scope"],
                    capability_row["instance_name"],
                    capability_row["part_name"],
                    int(element_label),
                    current_value,
                    resolved_lower,
                    resolved_upper,
                    resolved_prob_id,
                    resolved_scatter,
                    description or "",
                    _json_dumps(resolved_usage_scope),
                    _json_dumps({
                        "target_keys": row_target_keys,
                        "element_labels": [int(element_label)],
                    }),
                ))
                created_parameters.append(
                    {
                        "parameter_name": resolved_parameter_name,
                        "element_label": int(element_label),
                        "current_value": current_value,
                    }
                )
        if len(insert_rows) == 1:
            cursor.execute(insert_sql, insert_rows[0])
        elif hasattr(cursor, "executemany"):
            cursor.executemany(insert_sql, insert_rows)
        else:
            for row in insert_rows:
                cursor.execute(insert_sql, row)
        conn.commit()

        return {
            "project_id": project_id,
            "parameter_group_name": parameter_group_name,
            "quantity_code": resolved_quantity_code,
            "selection_mode": resolved_mode,
            "set_name": capability_row["set_name"],
            "set_type": capability_row["set_type"],
            "set_scope": capability_row["set_scope"],
            "instance_name": capability_row["instance_name"],
            "part_name": capability_row["part_name"],
            "lower": resolved_lower,
            "upper": resolved_upper,
            "prob_id": resolved_prob_id,
            "scatter": resolved_scatter,
            "description": description or "",
            "usage_scope": resolved_usage_scope,
            "created_parameter_count": len(created_parameters),
            "created_parameters_preview": created_parameters[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_design_response_catalog_entry(
        *,
        project_id: int,
        region_type: str,
        variables,
        set_name: Optional[str] = None,
        set_scope: Optional[str] = None,
        instance_name: Optional[str] = None,
        part_name: Optional[str] = None,
        node_labels: Optional[List[int]] = None,
        element_labels: Optional[List[int]] = None,
        step_name: Optional[str] = None,
        frequency: int = 1,
        response_name: Optional[str] = None,
) -> dict:
    ensure_tables_exist()

    resolved_region = str(region_type or "").strip().upper()
    if resolved_region not in {"NODE", "ELEMENT"}:
        raise ValidationError("region_type must be NODE or ELEMENT", {"region_type": region_type})

    resolved_variables = _normalize_response_variables(variables)
    normalized_set_name = str(set_name or "").strip()
    normalized_node_labels = sorted({int(item) for item in (node_labels or [])})
    normalized_element_labels = sorted({int(item) for item in (element_labels or [])})

    if resolved_region == "NODE" and normalized_element_labels:
        raise ValidationError(
            "NODE response cannot use element_labels",
            {"element_label_count": len(normalized_element_labels)},
        )
    if resolved_region == "ELEMENT" and normalized_node_labels:
        raise ValidationError(
            "ELEMENT response cannot use node_labels",
            {"node_label_count": len(normalized_node_labels)},
        )

    has_set = bool(normalized_set_name)
    has_manual_labels = bool(normalized_node_labels or normalized_element_labels)
    if has_set == has_manual_labels:
        raise ValidationError(
            "set_name and labels must choose exactly one input mode",
            {
                "set_name": normalized_set_name or None,
                "node_label_count": len(normalized_node_labels),
                "element_label_count": len(normalized_element_labels),
            },
        )

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        resolved_step_name = str(step_name or "").strip() or None
        resolved_set_scope = str(set_scope or "").strip().upper() or None
        resolved_instance_name = str(instance_name or "").strip() or None
        resolved_part_name = str(part_name or "").strip() or None
        incoming_signature = _build_static_sensitivity_response_signature(
            region_type=resolved_region,
            set_name=normalized_set_name,
            set_scope=resolved_set_scope,
            instance_name=resolved_instance_name,
            part_name=resolved_part_name,
            step_name=resolved_step_name,
            frequency=int(frequency),
            variables=resolved_variables,
            node_labels=normalized_node_labels,
            element_labels=normalized_element_labels,
            manual_mode=has_manual_labels,
        )
        cursor.execute(
            """
            SELECT response_no, request_no, step_name, frequency, region_type, set_name,
                   set_scope, instance_name, part_name, variables_json, extra_json
            FROM t_mt_py_fem_static_sensitivity_response_catalog
            WHERE pid = %s
            """,
            (int(project_id),),
        )
        for existing_row in cursor.fetchall() or []:
            if _extract_static_sensitivity_response_signature(existing_row) == incoming_signature:
                raise ValidationError(
                    "重复的响应定义, 数据库中已存在响应",
                    {
                        "project_id": int(project_id),
                        "existing_response_no": int(existing_row.get("response_no") or 0),
                        "existing_request_no": int(existing_row.get("request_no") or 0),
                        "region_type": resolved_region,
                        "step_name": resolved_step_name,
                        "set_name": normalized_set_name or None,
                        "set_scope": resolved_set_scope,
                        "instance_name": resolved_instance_name,
                        "part_name": resolved_part_name,
                        "variables": list(resolved_variables),
                        "node_labels": list(normalized_node_labels),
                        "element_labels": list(normalized_element_labels),
                    },
                )

        cursor.execute(
            """
            SELECT COALESCE(MAX(response_no), 0) AS max_no
            FROM t_mt_py_fem_static_sensitivity_response_catalog
            WHERE pid = %s
            """,
            (int(project_id),),
        )
        max_no_row = cursor.fetchone() or {}
        response_no = int(max_no_row.get("max_no") or 0) + 1
        request_no = 1
        resolved_response_name = str(response_name or "").strip() or f"RESP_{response_no}"

        extra_json = {"response_name": resolved_response_name}
        if has_manual_labels:
            normalized_set_name = _safe_manual_response_set_name(
                resolved_region, resolved_response_name, response_no
            )
            extra_json["set_source"] = "manual"
            extra_json["virtual_set_name"] = normalized_set_name
            if resolved_region == "NODE":
                extra_json["node_labels"] = normalized_node_labels
            else:
                extra_json["element_labels"] = normalized_element_labels

        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_static_sensitivity_response_catalog
            (pid, response_no, request_no, step_name, frequency, region_type, set_name,
             set_scope, instance_name, part_name, variables_json, extra_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(project_id),
                int(response_no),
                int(request_no),
                resolved_step_name,
                int(frequency),
                resolved_region,
                normalized_set_name,
                resolved_set_scope,
                resolved_instance_name,
                resolved_part_name,
                _json_dumps(resolved_variables),
                _json_dumps(extra_json),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    safe_write_console_event(
        int(project_id),
        "设计响应创建完成",
        [
            f"响应名: {resolved_response_name}",
            f"区域类型: {resolved_region}",
            f"集合名: {normalized_set_name}",
            f"变量: {', '.join(resolved_variables)}",
            f"来源: {'手工标签' if has_manual_labels else '已有集合'}",
        ],
    )

    payload = {
        "project_id": int(project_id),
        "response_no": int(response_no),
        "request_no": int(request_no),
        "response_name": resolved_response_name,
        "step_name": resolved_step_name,
        "frequency": int(frequency),
        "region_type": resolved_region,
        "set_name": normalized_set_name,
        "set_scope": resolved_set_scope,
        "instance_name": resolved_instance_name,
        "part_name": resolved_part_name,
        "variables": resolved_variables,
    }
    if resolved_region == "NODE" and normalized_node_labels:
        payload["node_labels"] = normalized_node_labels
    if resolved_region == "ELEMENT" and normalized_element_labels:
        payload["element_labels"] = normalized_element_labels
    return payload


def resolve_abaqus_sensor_node_match(project_id: int, sensor_name: str) -> dict:
    ensure_tables_exist()

    resolved_sensor_name = str(sensor_name or "").strip()
    if not resolved_sensor_name:
        raise ValidationError("sensor_name is required", {"sensor_name": sensor_name})

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s AND test_node_id = %s
            LIMIT 2
            """,
            (int(project_id), resolved_sensor_name),
        )
        rows = [dict(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()

    if not rows:
        raise NotFoundError(
            "sensor_name was not found in node-match results",
            {"project_id": int(project_id), "sensor_name": resolved_sensor_name},
        )

    if len(rows) > 1:
        raise ValidationError(
            "sensor_name maps to multiple node-match rows",
            {"project_id": int(project_id), "sensor_name": resolved_sensor_name, "match_count": len(rows)},
        )

    row = rows[0]
    instance_name = str(row.get("instance_name") or "").strip()
    fem_node_label = row.get("fem_node_label")
    if not instance_name:
        raise ValidationError(
            "sensor_name match has no instance_name",
            {"project_id": int(project_id), "sensor_name": resolved_sensor_name},
        )
    if fem_node_label is None:
        raise ValidationError(
            "sensor_name match has no fem_node_label",
            {"project_id": int(project_id), "sensor_name": resolved_sensor_name},
        )

    return {
        "sensor_name": resolved_sensor_name,
        "instance_name": instance_name,
        "node_label": int(fem_node_label),
    }


def list_design_response_catalog_entries(project_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT response_no, request_no, step_name, frequency, region_type,
                   set_name, set_scope, instance_name, part_name, variables_json, extra_json
            FROM t_mt_py_fem_static_sensitivity_response_catalog
            WHERE pid = %s
            ORDER BY response_no ASC, request_no ASC
            """,
            (int(project_id),),
        )
        responses = []
        for row in cursor.fetchall() or []:
            item = dict(row)
            extra_json = _parse_optional_json_object(item.get("extra_json"))
            try:
                variables = json.loads(item.get("variables_json")) if item.get("variables_json") else []
            except Exception:
                variables = []
            payload = {
                "response_no": int(item.get("response_no") or 0),
                "request_no": int(item.get("request_no") or 0),
                "response_name": str(extra_json.get("response_name") or f"RESP_{item.get('response_no') or 0}"),
                "step_name": item.get("step_name"),
                "frequency": int(item.get("frequency") or 1),
                "region_type": str(item.get("region_type") or "").upper(),
                "set_name": str(item.get("set_name") or ""),
                "set_scope": str(item.get("set_scope") or "").upper() or None,
                "instance_name": str(item.get("instance_name") or "") or None,
                "part_name": str(item.get("part_name") or "") or None,
                "variables": [str(v).strip().upper() for v in (variables or []) if str(v).strip()],
                "set_source": str(extra_json.get("set_source") or "catalog"),
                "extra_json": extra_json,
            }
            if payload["region_type"] == "NODE":
                payload["node_labels"] = _parse_id_list(extra_json.get("node_labels"))
            elif payload["region_type"] == "ELEMENT":
                payload["element_labels"] = _parse_id_list(extra_json.get("element_labels"))
            responses.append(payload)
        return {
            "project_id": int(project_id),
            "response_count": len(responses),
            "responses": responses,
        }
    finally:
        cursor.close()
        conn.close()


def clear_design_response_catalog_entries(project_id: int) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = %s",
            (int(project_id),),
        )
        deleted = int(cursor.rowcount or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    safe_write_console_event(
        int(project_id),
        "设计响应已清空",
        [f"删除条数: {deleted}"],
    )
    return {
        "project_id": int(project_id),
        "deleted_count": deleted,
    }
