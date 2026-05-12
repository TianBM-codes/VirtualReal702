import heapq
import json
import math
import os
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
from .project_config_service import (
    get_test_data_mode,
    get_node_match_parameter_context,
    save_fem_model_dimensions,
)
from .project_source_service import resolve_project_source_inp_path
from .project_status_service import update_work_condition_project_status

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


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cache_dir(project_id: int) -> str:
    path = os.path.join(_repo_root(), "model_cache", f"project_{project_id}")
    os.makedirs(path, exist_ok=True)
    return path


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
)


def _quantity_description(quantity_code: str, quantity_name: Optional[str] = None) -> str:
    token = str(quantity_code or "").strip().upper()
    if token == "E":
        return "杨氏模量"
    if token == "T":
        return "壳单元厚度"
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
    if value not in {"E", "T"}:
        raise ValueError("quantity_code must be one of: E, T")
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
    quantity_maps = {"E": {}, "T": {}}
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
            entries.append(
                {
                    "set_name": str(set_name),
                    "set_type": "ELSET",
                    "set_scope": "PART",
                    "instance_name": None,
                    "part_name": str(part_name),
                    "element_labels": labels,
                    "member_count": len(labels),
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
         supports_global, supports_local, current_value, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            set_role = VALUES(set_role),
            element_family = VALUES(element_family),
            section_type = VALUES(section_type),
            material_name = VALUES(material_name),
            member_count = VALUES(member_count),
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
            FROM t_mt_py_fem_design_response_catalog
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
                   supports_global, supports_local, current_value, extra_json
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
        cursor.execute("SELECT COUNT(*) AS cnt FROM t_mt_py_fem_response_catalog WHERE pid = %s", (project_id,))
        response_catalog_count = int(cursor.fetchone()["cnt"])
        cursor.execute("SELECT COUNT(DISTINCT mode_no) AS cnt FROM t_mt_py_fem_modal_result WHERE pid = %s", (project_id,))
        fem_mode_count = int(cursor.fetchone()["cnt"])
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
                   supports_global, supports_local, current_value, extra_json
            FROM t_mt_py_fem_quantity_set_capability
            WHERE pid = %s
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


def create_optimization_parameter(project_id, candidate_code=None, quantity_code=None, lower=None, upper=None, prob_id=0,
                                  selection_mode=None, set_name=None, parameter_name=None, scatter=None,
                                  description="", set_type=None, set_scope=None,
                                  instance_name=None, part_name=None):
    # This API turns a generic candidate type plus one cataloged set into a
    # concrete optimization parameter record that Bayesian update can address.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        resolved_quantity_code = _normalize_quantity_code(
            quantity_code or _derive_quantity_code_from_candidate(candidate_code)
        )
        if not set_name:
            raise ValueError("set_name is required")
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

        query = f"""
            SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name,
                   set_role, element_family, section_type, material_name, member_count,
                   supports_global, supports_local, current_value, extra_json
            FROM t_mt_py_fem_quantity_set_capability
            WHERE pid = %s AND quantity_code IN ({", ".join(["%s"] * len(quantity_code_candidates))}) AND set_name = %s
        """
        params = [project_id, *quantity_code_candidates, set_name]
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
        query += " ORDER BY set_scope, set_type, instance_name, part_name"

        cursor.execute(query, tuple(params))
        capability_rows = cursor.fetchall() or []
        if not capability_rows:
            raise ValueError(f"quantity/set capability not found: {resolved_quantity_code} @ {set_name}")
        if len(capability_rows) > 1:
            raise ValueError(
                "multiple quantity/set capabilities matched; specify set_scope/set_type/instance_name/part_name"
            )
        capability_row = capability_rows[0]

        resolved_mode = _resolve_selection_mode_from_capability(capability_row, selection_mode)
        parameter_group_name = str(parameter_name or _default_parameter_group_name(resolved_quantity_code, capability_row["set_name"]))

        resolved_scatter = float(
            _DEFAULT_PARAMETER_SCATTER if scatter is None else scatter
        )
        if resolved_scatter <= 0:
            raise ValueError("scatter must be > 0")

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
         instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                resolved_parameter_name = f"{parameter_group_name}#{int(element_label)}"
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
            "created_parameter_count": len(created_parameters),
            "created_parameters_preview": created_parameters[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _get_latest_octree_meta(cursor, project_id):
    cursor.execute("""
        SELECT source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at
        FROM t_mt_py_fem_node_octree_cache
        WHERE pid = %s
        ORDER BY updated_at DESC
        LIMIT 1
    """, (project_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("未找到节点八叉树缓存，请先导入 inp")
    meta = dict(row)
    meta["source_file_path"] = _normalize_stored_path(meta.get("source_file_path"))
    meta["cache_file_path"] = _normalize_stored_path(meta.get("cache_file_path"))
    return meta


def _ensure_octree_cache_file(cursor, project_id: int, octree_meta: dict) -> str:
    cache_path = _normalize_stored_path(octree_meta.get("cache_file_path"))
    if cache_path and os.path.exists(cache_path):
        return cache_path

    source_file_path = _normalize_stored_path(octree_meta.get("source_file_path"))
    if not source_file_path or not os.path.exists(source_file_path):
        source_file_path = resolve_project_source_inp_path(int(project_id))

    model = parse_inp(source_file_path, resolve_refs=True)
    node_data = _collect_global_nodes(model)
    rebuilt_cache_path = _save_octree_cache(
        project_id=int(project_id),
        source_file_path=source_file_path,
        node_data=node_data,
        force_rebuild=True,
    )

    cursor.execute(
        """
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
        """,
        (
            int(project_id),
            os.path.abspath(source_file_path),
            os.path.abspath(rebuilt_cache_path),
            int(len(node_data["point_labels"])),
            int(len(node_data["entries"])),
            _json_dumps(node_data["bbox_min"].tolist()),
            _json_dumps(node_data["bbox_max"].tolist()),
        ),
    )
    return os.path.abspath(rebuilt_cache_path)


def match_test_nodes(project_id, max_distance=None, overwrite=True,
                     auto_translate=False, translation=None, rotation=None, auto_rotate=False):
    # Match imported test nodes onto the FE node cloud stored in the octree
    # cache. The saved mapping is reused by DOF matching and correlation steps.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache_path = _ensure_octree_cache_file(cursor, int(project_id), octree_meta)

        test_nodes, test_node_source_table = _load_test_nodes_for_matching(cursor, int(project_id))
        if not test_nodes:
            raise ValueError("未找到试验节点，请先导入 UNV 试验数据")

        cache = _load_octree_cache(cache_path)
        part_lookup = _cache_part_lookup(cache)
        raw_test_coords = np.array(
            [[float(row["x_position"]), float(row["y_position"]), float(row["z_position"])] for row in test_nodes],
            dtype=np.float64,
        )
        match_context = get_node_match_parameter_context(int(project_id), cursor=cursor)
        tolerance = float(match_context["tolerance"])
        recommended_max_distance = float(match_context["maximum_node_point_distance"])
        resolved_max_distance = recommended_max_distance if max_distance is None else float(max_distance)

        manual_transform = translation is not None or rotation is not None
        transform_mode = "none"
        fit_info = None
        if manual_transform:
            # Manual transform overrides auto-fit so users can reproduce or
            # compare a known registration against the automatic estimate.
            applied_translation = np.asarray(translation or [0.0, 0.0, 0.0], dtype=np.float64).reshape(3)
            applied_rotation = _rotation_from_matrix(_rotation_to_matrix(rotation), center=(rotation or {}).get("center"))
            transform_mode = "manual"
        elif auto_translate and auto_rotate and len(raw_test_coords) >= 3:
            # ICP gives the best rigid registration when both translation and
            # rotation are allowed and we have enough points to fit a transform.
            estimate = _estimate_rigid_transform_icp(cache, raw_test_coords)
            applied_translation = estimate["translation"]
            applied_rotation = _rotation_from_matrix(estimate["rotation_matrix"])
            transform_mode = estimate["mode"]
            fit_info = {
                "iterations": estimate["iterations"],
                "rmse": estimate["rmse"],
                "pair_count": estimate["pair_count"],
            }
        elif auto_translate:
            applied_translation, transform_mode = _estimate_translation(cache["point_coords"], raw_test_coords)
            applied_rotation = None
        else:
            applied_translation = np.zeros(3, dtype=np.float64)
            applied_rotation = None

        transformed_coords = _apply_transform(
            raw_test_coords,
            translation=applied_translation,
            rotation=applied_rotation,
        )
        transform_payload = {
            "mode": transform_mode,
            "translation": applied_translation.tolist(),
            "rotation": applied_rotation,
            "fit": fit_info,
        }

        matches = []
        for row, coord_after in zip(test_nodes, transformed_coords):
            # Each test node keeps both the matched FE node and the residual
            # offset after registration so mismatches are visible in the DB.
            point_idx, distance = _octree_nearest(cache, coord_after)
            if point_idx < 0:
                continue
            fem_coord = cache["point_coords"][point_idx]
            fem_label = int(cache["point_labels"][point_idx])
            inst_name = str(cache["point_instances"][point_idx])
            delta = fem_coord - coord_after
            match = {
                "test_node_id": str(row["test_node_id"]),
                "test_node_source": test_node_source_table,
                "instance_name": inst_name,
                "part_name": part_lookup.get((inst_name, fem_label)),
                "fem_node_label": fem_label,
                "fem_coord": fem_coord.tolist(),
                "distance": float(distance),
                "x_offset": float(delta[0]),
                "y_offset": float(delta[1]),
                "z_offset": float(delta[2]),
            }
            if float(distance) <= resolved_max_distance:
                matches.append(match)

        if overwrite:
            # Downstream tables depend on the node mapping, so they are cleared
            # together when the caller requests a fresh node alignment.
            cursor.execute("DELETE FROM t_mt_py_fem_node_match WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_dof_match WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_response_catalog WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_node_match
        (pid, test_node_id, instance_name, fem_node_label, distance, x_offset, y_offset, z_offset, transform_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            instance_name = VALUES(instance_name),
            fem_node_label = VALUES(fem_node_label),
            distance = VALUES(distance),
            x_offset = VALUES(x_offset),
            y_offset = VALUES(y_offset),
            z_offset = VALUES(z_offset),
            transform_json = VALUES(transform_json),
            created_at = CURRENT_TIMESTAMP
        """
        transform_json = _json_dumps(transform_payload)
        for item in matches:
            cursor.execute(insert_sql, (
                project_id,
                item["test_node_id"],
                item["instance_name"],
                item["fem_node_label"],
                item["distance"],
                item["x_offset"],
                item["y_offset"],
                item["z_offset"],
                transform_json,
            ))

        insert_sql = """
        INSERT INTO t_mt_py_fem_node_pairs
        (pid, node, point, distance, x_offset, y_offset, z_offset)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            distance = VALUES(distance),
            x_offset = VALUES(x_offset),
            y_offset = VALUES(y_offset),
            z_offset = VALUES(z_offset)
        """
        for item in matches:
            cursor.execute(insert_sql, (
                project_id,
                item["fem_node_label"],
                item["test_node_id"],
                item["distance"],
                item["x_offset"],
                item["y_offset"],
                item["z_offset"],
            ))
        update_work_condition_project_status(
            int(project_id),
            cursor=cursor,
            space_match_status=1,
        )
        conn.commit()

        return {
            "project_id": project_id,
            "tested_points": len(test_nodes),
            "matched_points": len(matches),
            "tolerance": tolerance,
            "maximum_node_point_distance": recommended_max_distance,
            "transform": transform_payload,
            "max_distance": resolved_max_distance,
            "matches_preview": matches[:20],
            "octree_cache_path": cache_path,
            "test_node_source_table": test_node_source_table,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _coord_key(values) -> tuple:
    arr = np.asarray(values, dtype=np.float64).reshape(3)
    return tuple(float(f"{item:.12g}") for item in arr.tolist())


def get_pair_node_point_result(project_id):
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache = _load_octree_cache(octree_meta["cache_file_path"])

        cursor.execute("""
            SELECT id, test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY id
        """, (project_id,))
        node_matches = cursor.fetchall()
        if not node_matches:
            raise _required_operation_error(
                "未找到节点匹配结果，请先完成测点与有限元节点的空间匹配操作",
                operation="完成测点与有限元节点的空间匹配",
                interface_key="pair_node_point_result",
            )

        cursor.execute("""
            SELECT id, measuring_point_name, x_position, y_position, z_position
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id
        """, (project_id,))
        measuring_rows = cursor.fetchall()
        test_node_lookup = {
            str(row["measuring_point_name"]): _coord_key([row["x_position"], row["y_position"], row["z_position"]])
            for row in measuring_rows
            if row.get("measuring_point_name") not in (None, "")
        }

        fem_coord_lookup = {}
        for inst_name, label, coord in zip(cache["point_instances"], cache["point_labels"], cache["point_coords"]):
            fem_coord_lookup[(str(inst_name), int(label))] = [
                float(coord[0]),
                float(coord[1]),
                float(coord[2]),
            ]

        sensor_names = []
        node_xyz = []
        for row in node_matches:
            test_node_id = str(row["test_node_id"])
            coord_key = test_node_lookup.get(test_node_id)
            if coord_key is None:
                continue
            fem_coord = fem_coord_lookup.get((str(row["instance_name"] or ""), int(row["fem_node_label"])))
            if fem_coord is None:
                continue
            sensor_names.append(test_node_id)
            node_xyz.append(fem_coord)

        return {
            "sensor_name": sensor_names,
            "node_xyz": node_xyz,
        }
    finally:
        cursor.close()
        conn.close()


def save_transform_operation(project_id: int, transform_type: str, matrix4):
    ensure_tables_exist()
    resolved_type = _normalize_transform_type(transform_type)
    resolved_matrix4 = _normalize_matrix4(matrix4)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_transform_operation (pid, transform_type, matrix4_json)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                matrix4_json = VALUES(matrix4_json),
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(project_id), resolved_type, _json_dumps(resolved_matrix4)),
        )
        conn.commit()
        return {
            "project_id": int(project_id),
            "type": resolved_type,
            "matrix4": resolved_matrix4,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_transform_auto_info(project_id: int, transform_type: str = None):
    ensure_tables_exist()
    resolved_type = _normalize_transform_type(transform_type) if transform_type is not None else None

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if resolved_type is None:
            cursor.execute(
                """
                SELECT transform_type, matrix4_json
                FROM t_mt_py_fem_transform_operation
                WHERE pid = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (int(project_id),),
            )
        else:
            cursor.execute(
                """
                SELECT transform_type, matrix4_json
                FROM t_mt_py_fem_transform_operation
                WHERE pid = %s AND transform_type = %s
                LIMIT 1
                """,
                (int(project_id), resolved_type),
            )
        row = cursor.fetchone()
        if not row:
            # raise ValueError("未找到变换操作记录")
            return {
                "type": "fem",
                "matrix4": np.eye(4, dtype=np.int32).tolist(),
            }
        return {
            "type": str(row["transform_type"]),
            "matrix4": _normalize_matrix4(_json_loads(row["matrix4_json"])),
        }
    finally:
        cursor.close()
        conn.close()


_CHANNEL_DIRECTION_TO_DOF = {
    1: ("UX", "U1", np.array([1.0, 0.0, 0.0], dtype=np.float64)),
    2: ("UY", "U2", np.array([0.0, 1.0, 0.0], dtype=np.float64)),
    3: ("UZ", "U3", np.array([0.0, 0.0, 1.0], dtype=np.float64)),
}


def _resolve_channel_direction_vector(direction_value, data_operate_value, *, measuring_point_name: str, channel_id=None):
    try:
        direction = int(direction_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "通道方向无效，必须为 1/2/3",
            {
                "measuring_point_name": measuring_point_name,
                "channel_id": channel_id,
                "direction": direction_value,
            },
        ) from exc

    direction_spec = _CHANNEL_DIRECTION_TO_DOF.get(direction)
    if direction_spec is None:
        raise ValidationError(
            "通道方向无效，必须为 1/2/3",
            {
                "measuring_point_name": measuring_point_name,
                "channel_id": channel_id,
                "direction": direction,
            },
        )

    sign_text = str(data_operate_value or "").strip()
    if sign_text not in {"+", "-"}:
        raise ValidationError(
            "通道 data_operate 无效，必须为 '+' 或 '-'",
            {
                "measuring_point_name": measuring_point_name,
                "channel_id": channel_id,
                "data_operate": data_operate_value,
            },
        )

    sign = -1.0 if sign_text == "-" else 1.0
    test_dof, fem_dof, base_direction = direction_spec
    return test_dof, fem_dof, (base_direction * sign).astype(np.float64, copy=False)


def _build_modal_unv_dof_amplitudes(cursor, project_id: int) -> Dict[Tuple[str, str], float]:
    # Use the maximum measured modal amplitude of each translational direction
    # as the DOF availability score. If one direction stays below the threshold
    # across all imported modes, that direction is treated as unusable.
    modes = _load_test_mode_vectors(cursor, int(project_id))
    amplitudes: Dict[Tuple[str, str], float] = {}
    for mode_map in modes.values():
        for point_id, vec in mode_map.items():
            vec_abs = np.abs(np.asarray(vec, dtype=np.complex128))
            point_id_text = str(point_id)
            for idx, test_dof in enumerate(TEST_DOF_SEQUENCE):
                key = (point_id_text, test_dof)
                amplitudes[key] = max(float(amplitudes.get(key, 0.0)), float(vec_abs[idx]))
    return amplitudes


def match_test_dofs(project_id, overwrite=True, min_match_score=None):
    ensure_tables_exist()
    auto_created_node_match = _ensure_node_matches(int(project_id))
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_node_id, instance_name, fem_node_label, transform_json
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id
        """, (project_id,))
        node_matches = cursor.fetchall()
        if not node_matches:
            raise _required_operation_error(
                "未找到节点匹配结果，请先完成测点与有限元节点的空间匹配操作",
                operation="完成测点与有限元节点的空间匹配",
                interface_key="match_nodes",
            )

        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache = _load_octree_cache(octree_meta["cache_file_path"])
        part_lookup = _cache_part_lookup(cache)
        node_match_lookup = {str(row["test_node_id"]): row for row in node_matches}
        modal_unv_mode = _is_modal_unv_project(int(project_id), cursor=cursor)

        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_dof_match WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_response_catalog WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_dof_match
        (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof,
         direction_x, direction_y, direction_z, match_score, transform_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            instance_name = VALUES(instance_name),
            part_name = VALUES(part_name),
            fem_node_label = VALUES(fem_node_label),
            fem_dof = VALUES(fem_dof),
            direction_x = VALUES(direction_x),
            direction_y = VALUES(direction_y),
            direction_z = VALUES(direction_z),
            match_score = VALUES(match_score),
            transform_json = VALUES(transform_json),
            created_at = CURRENT_TIMESTAMP
        """

        if modal_unv_mode:
            score_threshold = 1e-6 if min_match_score is None else float(min_match_score)
            dof_scores = _build_modal_unv_dof_amplitudes(cursor, int(project_id))
            dof_matches = []
            rejected_matches = []
            for row in node_matches:
                test_node_id = str(row["test_node_id"])
                inst_name = str(row["instance_name"] or "")
                fem_node_label = int(row["fem_node_label"])
                part_name = part_lookup.get((inst_name, fem_node_label))
                transform_payload = _json_loads(row["transform_json"]) or {}
                for test_dof, fem_dof, direction in (
                    ("UX", "U1", np.array([1.0, 0.0, 0.0], dtype=np.float64)),
                    ("UY", "U2", np.array([0.0, 1.0, 0.0], dtype=np.float64)),
                    ("UZ", "U3", np.array([0.0, 0.0, 1.0], dtype=np.float64)),
                ):
                    match_score = float(dof_scores.get((test_node_id, test_dof), 0.0))
                    if match_score < score_threshold:
                        if len(rejected_matches) < 20:
                            rejected_matches.append({
                                "test_node_id": test_node_id,
                                "test_dof": test_dof,
                                "match_score": match_score,
                            })
                        continue
                    dof_row = {
                        "test_node_id": test_node_id,
                        "test_dof": test_dof,
                        "instance_name": inst_name,
                        "part_name": part_name,
                        "fem_node_label": fem_node_label,
                        "fem_dof": fem_dof,
                        "direction": direction.tolist(),
                        "match_score": match_score,
                        "transform": transform_payload,
                        "mode": "modal_unv",
                    }
                    dof_matches.append(dof_row)
                    cursor.execute(insert_sql, (
                        project_id,
                        test_node_id,
                        test_dof,
                        inst_name,
                        part_name,
                        fem_node_label,
                        fem_dof,
                        float(direction[0]),
                        float(direction[1]),
                        float(direction[2]),
                        match_score,
                        _json_dumps(transform_payload),
                    ))
            conn.commit()
            return {
                "project_id": project_id,
                "node_match_auto_created": auto_created_node_match,
                "node_match_count": len(node_matches),
                "channel_count": 0,
                "dof_match_count": len(dof_matches),
                "dof_matches_preview": dof_matches[:20],
                "rejected_dof_count": int(len(node_matches) * len(TEST_DOF_SEQUENCE) - len(dof_matches)),
                "rejected_dof_preview": rejected_matches,
                "min_match_score": score_threshold,
                "match_mode": "modal_unv",
            }

        cursor.execute("""
            SELECT id, measuring_point_name, sensor_type_id
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id, measuring_point_name
        """, (project_id,))
        measuring_rows = cursor.fetchall() or []
        displacement_sensors = [
            row for row in measuring_rows
            if _is_displacement_static_test_sensor_type(row.get("sensor_type_id"))
        ]
        if not displacement_sensors:
            raise ValueError("未找到位移传感器测点")

        missing_node_matches = [
            str(row["measuring_point_name"])
            for row in displacement_sensors
            if str(row.get("measuring_point_name") or "") not in node_match_lookup
        ]
        if missing_node_matches:
            raise ValidationError(
                "存在位移传感器尚未完成节点匹配，无法进行自由度匹配",
                {
                    "missing_measuring_points": missing_node_matches[:20],
                    "missing_count": len(missing_node_matches),
                },
            )

        displacement_sensor_by_id = {
            int(row["id"]): row
            for row in displacement_sensors
            if row.get("id") is not None
        }

        cursor.execute("""
            SELECT id, measure_point_id, direction, data_operate
            FROM t_mt_channel_info
            WHERE project_id = %s
            ORDER BY measure_point_id, id
        """, (project_id,))
        channel_rows = cursor.fetchall() or []
        displacement_channels = [
            row for row in channel_rows
            if row.get("measure_point_id") is not None
            and int(row["measure_point_id"]) in displacement_sensor_by_id
        ]
        if not displacement_channels:
            raise ValueError("未找到位移传感器对应的通道方向配置")

        dof_matches_by_key = {}
        for channel_row in displacement_channels:
            measure_point_id = int(channel_row["measure_point_id"])
            measuring_row = displacement_sensor_by_id[measure_point_id]
            test_node_id = str(measuring_row["measuring_point_name"])
            node_match = node_match_lookup[test_node_id]
            inst_name = str(node_match["instance_name"])
            fem_node_label = int(node_match["fem_node_label"])
            part_name = part_lookup.get((inst_name, fem_node_label))
            transform_payload = _json_loads(node_match["transform_json"]) or {}
            test_dof, fem_dof, direction = _resolve_channel_direction_vector(
                channel_row.get("direction"),
                channel_row.get("data_operate"),
                measuring_point_name=test_node_id,
                channel_id=channel_row.get("id"),
            )

            dof_row = {
                "measure_point_id": measure_point_id,
                "channel_id": channel_row.get("id"),
                "test_node_id": test_node_id,
                "test_dof": test_dof,
                "instance_name": inst_name,
                "part_name": part_name,
                "fem_node_label": fem_node_label,
                "fem_dof": fem_dof,
                "direction": direction.tolist(),
                "match_score": None,
                "transform": transform_payload,
            }
            dof_matches_by_key[(test_node_id, test_dof)] = dof_row
            cursor.execute(insert_sql, (
                project_id,
                test_node_id,
                test_dof,
                inst_name,
                part_name,
                fem_node_label,
                fem_dof,
                float(direction[0]),
                float(direction[1]),
                float(direction[2]),
                1.0,
                _json_dumps(transform_payload),
            ))

        conn.commit()
        dof_matches = list(dof_matches_by_key.values())
        return {
            "project_id": project_id,
            "node_match_auto_created": auto_created_node_match,
            "node_match_count": len(node_matches),
            "displacement_sensor_count": len(displacement_sensors),
            "channel_count": len(displacement_channels),
            "dof_match_count": len(dof_matches),
            "dof_matches_preview": dof_matches[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_dof_matches(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof,
                   direction_x, direction_y, direction_z, match_score, transform_json, created_at
            FROM t_mt_py_fem_dof_match
            WHERE pid = %s
            ORDER BY test_node_id, test_dof
        """, (project_id,))
        return {
            "project_id": project_id,
            "dof_matches": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()


def build_fe_response_catalog(project_id, overwrite=True, include_test_modes=True, include_node_dofs=True):
    # Build a normalized response directory that mixes modal frequencies and
    # matched nodal DOFs into one table for optimization/correlation consumers.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_response_catalog WHERE pid = %s", (project_id,))

        rows = []
        seq_no = 1

        if include_test_modes:
            # Modal frequencies are treated as scalar responses alongside
            # displacement-based responses even though they do not map to nodes.
            cursor.execute("""
                SELECT mode_no, frequency
                FROM t_mt_py_test_modal_frequency
                WHERE pid = %s
                ORDER BY mode_no
            """, (project_id,))
            for row in cursor.fetchall():
                rows.append({
                    "response_code": f"MODE_FREQ:{int(row['mode_no'])}",
                    "response_name": f"Mode {int(row['mode_no'])} frequency",
                    "response_type": "MODAL_FREQUENCY",
                    "entity_type": "MODE",
                    "test_mode_no": int(row["mode_no"]),
                    "test_node_id": None,
                    "instance_name": None,
                    "part_name": None,
                    "fem_node_label": None,
                    "component": "FREQ",
                    "unit": "Hz",
                    "seq_no": seq_no,
                    "source_table": "t_mt_py_test_modal_frequency",
                    "extra_json": {"test_frequency": _safe_float(row["frequency"])},
                })
                seq_no += 1

        if include_node_dofs:
            # DOF-based responses come from the FE/test DOF matching table and
            # preserve the resolved projection direction in extra_json.
            cursor.execute("""
                SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof,
                       direction_x, direction_y, direction_z, match_score
                FROM t_mt_py_fem_dof_match
                WHERE pid = %s
                ORDER BY test_node_id, test_dof
            """, (project_id,))
            dof_rows = cursor.fetchall()
            if not dof_rows and not rows:
                raise ValueError("未找到试验模态或自由度匹配结果，无法构建响应目录")

            for row in dof_rows:
                response_code = (
                    f"NODE_DOF:{row['instance_name'] or '_'}:{int(row['fem_node_label'])}:"
                    f"{row['fem_dof']}:{row['test_node_id']}:{row['test_dof']}"
                )
                rows.append({
                    "response_code": response_code,
                    "response_name": f"{row['instance_name'] or 'GLOBAL'}:{int(row['fem_node_label'])} {row['fem_dof']}",
                    "response_type": "NODAL_DISPLACEMENT",
                    "entity_type": "NODE_DOF",
                    "test_mode_no": None,
                    "test_node_id": str(row["test_node_id"]),
                    "instance_name": row["instance_name"],
                    "part_name": row["part_name"],
                    "fem_node_label": int(row["fem_node_label"]),
                    "component": row["fem_dof"],
                    "unit": None,
                    "seq_no": seq_no,
                    "source_table": "t_mt_py_fem_dof_match",
                    "extra_json": {
                        "test_dof": row["test_dof"],
                        "direction": [float(row["direction_x"]), float(row["direction_y"]), float(row["direction_z"])],
                        "match_score": _safe_float(row["match_score"]),
                    },
                })
                seq_no += 1

        insert_sql = """
        INSERT INTO t_mt_py_fem_response_catalog
        (pid, response_code, response_name, response_type, entity_type, test_mode_no, test_node_id,
         instance_name, part_name, fem_node_label, component, unit, seq_no, source_table, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            response_name = VALUES(response_name),
            response_type = VALUES(response_type),
            entity_type = VALUES(entity_type),
            test_mode_no = VALUES(test_mode_no),
            test_node_id = VALUES(test_node_id),
            instance_name = VALUES(instance_name),
            part_name = VALUES(part_name),
            fem_node_label = VALUES(fem_node_label),
            component = VALUES(component),
            unit = VALUES(unit),
            seq_no = VALUES(seq_no),
            source_table = VALUES(source_table),
            extra_json = VALUES(extra_json),
            created_at = CURRENT_TIMESTAMP
        """
        for item in rows:
            cursor.execute(insert_sql, (
                project_id,
                item["response_code"],
                item["response_name"],
                item["response_type"],
                item["entity_type"],
                item["test_mode_no"],
                item["test_node_id"],
                item["instance_name"],
                item["part_name"],
                item["fem_node_label"],
                item["component"],
                item["unit"],
                item["seq_no"],
                item["source_table"],
                _json_dumps(item["extra_json"]),
            ))
        conn.commit()

        return {
            "project_id": project_id,
            "response_count": len(rows),
            "modal_frequency_count": sum(1 for row in rows if row["response_type"] == "MODAL_FREQUENCY"),
            "nodal_response_count": sum(1 for row in rows if row["response_type"] == "NODAL_DISPLACEMENT"),
            "responses_preview": rows[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_fe_response_catalog(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT response_code, response_name, response_type, entity_type, test_mode_no, test_node_id,
                   instance_name, part_name, fem_node_label, component, unit, seq_no, source_table,
                   extra_json, created_at
            FROM t_mt_py_fem_response_catalog
            WHERE pid = %s
            ORDER BY seq_no, response_code
        """, (project_id,))
        return {
            "project_id": project_id,
            "responses": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()


def _load_modal_payload(file_path=None, modes=None) -> List[dict]:
    if file_path:
        with open(file_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        modes = payload.get("modes", []) if isinstance(payload, dict) else payload
    if not modes:
        raise ValueError("modes 数据为空")
    return [dict(item) for item in modes]


def import_fe_modal_results(project_id, overwrite=True, file_path=None, modes=None):
    # Import solver modal results into a flat per-node table so the later
    # correlation pass can stream them mode-by-mode from SQL.
    ensure_tables_exist()
    modal_modes = _load_modal_payload(file_path=file_path, modes=modes)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_modal_result
        (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            frequency = VALUES(frequency),
            part_name = VALUES(part_name),
            u1 = VALUES(u1),
            u2 = VALUES(u2),
            u3 = VALUES(u3),
            extra_json = VALUES(extra_json),
            created_at = CURRENT_TIMESTAMP
        """

        row_count = 0
        preview = []
        for mode_item in modal_modes:
            mode_no = int(mode_item["mode_no"])
            frequency = _safe_float(mode_item.get("frequency"))
            for node_item in mode_item.get("nodes", []):
                vector = node_item.get("vector")
                u1 = node_item.get("u1")
                u2 = node_item.get("u2")
                u3 = node_item.get("u3")
                if vector is not None:
                    # Accept either the explicit u1/u2/u3 schema or a compact
                    # "vector" payload from external conversion scripts.
                    vector = list(vector)
                    if u1 is None:
                        u1 = vector[0]
                    if u2 is None:
                        u2 = vector[1]
                    if u3 is None:
                        u3 = vector[2]

                payload = {
                    "mode_no": mode_no,
                    "frequency": frequency,
                    "instance_name": node_item.get("instance_name"),
                    "part_name": node_item.get("part_name"),
                    "fem_node_label": int(node_item["fem_node_label"]),
                    "u1": _safe_float(u1),
                    "u2": _safe_float(u2),
                    "u3": _safe_float(u3),
                    "extra_json": node_item.get("extra_json") or {},
                }
                cursor.execute(insert_sql, (
                    project_id,
                    payload["mode_no"],
                    payload["frequency"],
                    payload["instance_name"],
                    payload["part_name"],
                    payload["fem_node_label"],
                    payload["u1"],
                    payload["u2"],
                    payload["u3"],
                    _json_dumps(payload["extra_json"]),
                ))
                row_count += 1
                if len(preview) < 20:
                    preview.append(payload)

        conn.commit()
        return {
            "project_id": project_id,
            "mode_count": len(modal_modes),
            "row_count": row_count,
            "source_file_path": os.path.abspath(file_path) if file_path else None,
            "rows_preview": preview,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_fe_modal_results(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json, created_at
            FROM t_mt_py_fem_modal_result
            WHERE pid = %s
            ORDER BY mode_no, instance_name, fem_node_label
        """, (project_id,))
        return {
            "project_id": project_id,
            "rows": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()


def _manifest_result_group_clause(result_group: Optional[str], prefix: str = "") -> Tuple[str, List[str]]:
    column = f"{prefix}result_group"
    if result_group is None:
        return f"{column} IS NULL", []
    return f"{column} = ?", [str(result_group)]


def _registry_repo() -> RegistryRepo:
    return RegistryRepo(settings.registry_db_path)


def _resolve_project_result_workspace(project_id: int, result_group: str) -> Tuple[str, dict]:
    repo = _registry_repo()
    project_row = repo.get_project(str(project_id))
    if project_row is None:
        raise NotFoundError(f"project '{project_id}' not found", {"project_id": int(project_id)})

    result_group_row = repo.get_result_group(str(project_id), str(result_group))
    if result_group_row is None:
        raise NotFoundError(
            f"result_group '{result_group}' not found for project '{project_id}'",
            {"project_id": int(project_id), "result_group": str(result_group)},
        )
    if str(result_group_row["status"] or "") != "ready":
        raise ValidationError(
            f"result_group '{result_group}' is not ready",
            {
                "project_id": int(project_id),
                "result_group": str(result_group),
                "status": result_group_row["status"],
            },
        )

    workspace = repo.resolve_workspace(str(project_row["workspace"]), settings.data_root)
    workspace_abs = _sens._workspace_path(workspace)
    return workspace_abs, dict(result_group_row)


def _collect_project_result_static_rows(
        *,
        project_id: int,
        result_group: str,
        step: Optional[str],
        frame: Optional[int],
        instances: Optional[List[str]],
) -> dict:
    workspace_abs, _ = _resolve_project_result_workspace(project_id, result_group)
    conn = _sens._manifest_conn(workspace_abs)
    try:
        rg_clause, rg_params = _manifest_result_group_clause(result_group)
        step_rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT step_name, step_number FROM steps WHERE {rg_clause} ORDER BY step_number, step_name",
                rg_params,
            ).fetchall()
        ]
        chosen_step = str(step) if step is not None else _sens._default_step_from_rows(step_rows)
        if not chosen_step:
            raise NotFoundError(
                "no steps found for project result group",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "workspace": workspace_abs,
                },
            )
        if chosen_step not in {str(row.get("step_name")) for row in step_rows}:
            available = [str(row.get("step_name")) for row in step_rows if row.get("step_name") is not None]
            raise NotFoundError(
                f"step '{chosen_step}' not found under result_group '{result_group}'",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                    "available_steps": available,
                },
            )

        frame_rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT frame_idx, frame_value, description FROM frames WHERE step_name = ? AND {rg_clause} ORDER BY frame_idx",
                [chosen_step] + rg_params,
            ).fetchall()
        ]
        if not frame_rows:
            raise NotFoundError(
                "no frames found for project result step",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                },
            )
        available_frames = [int(row["frame_idx"]) for row in frame_rows]
        chosen_frame = int(frame) if frame is not None else int(available_frames[-1])
        if chosen_frame not in set(available_frames):
            raise ValidationError(
                f"frame {chosen_frame} not found under step '{chosen_step}'",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                    "frame": chosen_frame,
                    "available_frames": available_frames,
                },
            )

        available_instances = [
            str(row["instance_name"])
            for row in conn.execute(
                f"""
                SELECT DISTINCT instance_name
                FROM result_blocks
                WHERE step_name = ? AND field_name = 'U' AND position = 'NODAL' AND {rg_clause}
                ORDER BY instance_name
                """,
                [chosen_step] + rg_params,
            ).fetchall()
        ]
        if not available_instances:
            raise NotFoundError(
                "nodal displacement field 'U' not found in project result group",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                },
            )
        if instances:
            chosen_instances = [str(item) for item in instances]
            missing = [item for item in chosen_instances if item not in available_instances]
            if missing:
                raise NotFoundError(
                    "some instances are not available in the selected project result group",
                    {
                        "project_id": int(project_id),
                        "result_group": str(result_group),
                        "missing_instances": missing,
                        "available_instances": available_instances,
                    },
                )
        else:
            chosen_instances = available_instances

        part_name_map = {}
        try:
            for row in conn.execute("SELECT instance_name, part_name FROM instances ORDER BY instance_name").fetchall():
                part_name_map[str(row["instance_name"])] = row["part_name"]
        except Exception:
            part_name_map = {}
    finally:
        conn.close()

    rows = []
    for instance_name in chosen_instances:
        comp_maps = {
            "U1": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=chosen_frame,
                aggregation="max_abs",
                component="U1",
                result_group=str(result_group),
            ),
            "U2": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=chosen_frame,
                aggregation="max_abs",
                component="U2",
                result_group=str(result_group),
            ),
            "U3": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=chosen_frame,
                aggregation="max_abs",
                component="U3",
                result_group=str(result_group),
            ),
        }
        scoped_labels = sorted(
            set(comp_maps["U1"].keys()) | set(comp_maps["U2"].keys()) | set(comp_maps["U3"].keys()),
            key=lambda item: int(str(item).split("::", 1)[-1]),
        )
        for scoped_label in scoped_labels:
            label_text = str(scoped_label).split("::", 1)[-1]
            rows.append(
                {
                    "instance_name": instance_name,
                    "part_name": part_name_map.get(instance_name),
                    "fem_node_label": int(label_text),
                    "u1": _safe_float(comp_maps["U1"].get(scoped_label)),
                    "u2": _safe_float(comp_maps["U2"].get(scoped_label)),
                    "u3": _safe_float(comp_maps["U3"].get(scoped_label)),
                    "extra_json": {
                        "source": "project_result_group",
                        "project_id": int(project_id),
                        "result_group": str(result_group),
                        "step_name": chosen_step,
                        "frame_idx": int(chosen_frame),
                    },
                }
            )

    if not rows:
        raise NotFoundError(
            "no nodal displacement rows resolved from project result group",
            {
                "project_id": int(project_id),
                "result_group": str(result_group),
                "step": chosen_step,
                "frame": int(chosen_frame),
                "instances": chosen_instances,
            },
        )

    return {
        "project_id": int(project_id),
        "workspace": workspace_abs,
        "result_group": str(result_group),
        "step_name": chosen_step,
        "frame_idx": int(chosen_frame),
        "instances": chosen_instances,
        "rows": rows,
    }


def _load_static_result_rows_from_txt(file_path: str) -> List[dict]:
    # Text imports follow the legacy fixed-column export layout used by current
    # FEMTools comparison files: node label plus 6 displacement/rotation values.
    rows: List[dict] = []
    with open(file_path, "r", encoding="utf-8") as fp:
        lines = fp.readlines()

    if len(lines) <= 3:
        raise ValueError("static result txt does not contain data rows after the first three header lines")

    for line_no, raw_line in enumerate(lines[3:], start=4):
        text = raw_line.strip()
        if not text:
            continue

        parts = text.replace(",", " ").split()
        if len(parts) < 7:
            raise ValueError(f"invalid static result line {line_no}: expected 7 columns, got {len(parts)}")

        rows.append({
            "fem_node_label": int(parts[0]),
            "u1": float(parts[1]),
            "u2": float(parts[2]),
            "u3": float(parts[3]),
            "ur1": float(parts[4]),
            "ur2": float(parts[5]),
            "ur3": float(parts[6]),
        })

    if not rows:
        raise ValueError("static result txt has no valid data rows")
    return rows


def _load_static_result_payload(file_path=None, rows=None) -> List[dict]:
    if file_path:
        return _load_static_result_rows_from_txt(file_path)

    if not rows:
        raise ValueError("static result payload is empty")

    normalized = []
    for item in rows:
        item = dict(item)
        fem_node_label = item.get("fem_node_label", item.get("node_label", item.get("node")))
        if fem_node_label is None:
            raise ValueError("static result row missing fem_node_label/node_label/node")

        normalized.append({
            "fem_node_label": int(fem_node_label),
            "u1": _safe_float(item.get("u1", item.get("ux"))),
            "u2": _safe_float(item.get("u2", item.get("uy"))),
            "u3": _safe_float(item.get("u3", item.get("uz"))),
            "ur1": _safe_float(item.get("ur1", item.get("rx"))),
            "ur2": _safe_float(item.get("ur2", item.get("ry"))),
            "ur3": _safe_float(item.get("ur3", item.get("rz"))),
            "instance_name": item.get("instance_name"),
            "part_name": item.get("part_name"),
            "load_case_no": item.get("load_case_no"),
            "extra_json": item.get("extra_json") or {},
        })
    return normalized


def _persist_fe_static_results(
        cursor,
        *,
        project_id: int,
        static_rows: Sequence[dict],
        load_case_no=1,
        instance_name=None,
        part_name=None,
        source_file_path: Optional[str] = None,
) -> dict:
    insert_sql = """
    INSERT INTO t_mt_py_fem_static_result
    (pid, load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        part_name = VALUES(part_name),
        u1 = VALUES(u1),
        u2 = VALUES(u2),
        u3 = VALUES(u3),
        ur1 = VALUES(ur1),
        ur2 = VALUES(ur2),
        ur3 = VALUES(ur3),
        extra_json = VALUES(extra_json),
        created_at = CURRENT_TIMESTAMP
    """

    row_count = 0
    preview = []
    case_nos = set()
    for item in static_rows:
        item_load_case_no = int(item.get("load_case_no") or load_case_no or 1)
        item_instance_name = item.get("instance_name") if item.get("instance_name") is not None else instance_name
        item_part_name = item.get("part_name") if item.get("part_name") is not None else part_name
        extra_json = dict(item.get("extra_json") or {})
        if source_file_path:
            extra_json["source_file_path"] = source_file_path

        payload = {
            "load_case_no": item_load_case_no,
            "instance_name": item_instance_name,
            "part_name": item_part_name,
            "fem_node_label": int(item["fem_node_label"]),
            "u1": _safe_float(item.get("u1")),
            "u2": _safe_float(item.get("u2")),
            "u3": _safe_float(item.get("u3")),
            "ur1": _safe_float(item.get("ur1")),
            "ur2": _safe_float(item.get("ur2")),
            "ur3": _safe_float(item.get("ur3")),
            "extra_json": extra_json,
        }
        cursor.execute(insert_sql, (
            int(project_id),
            payload["load_case_no"],
            payload["instance_name"],
            payload["part_name"],
            payload["fem_node_label"],
            payload["u1"],
            payload["u2"],
            payload["u3"],
            payload["ur1"],
            payload["ur2"],
            payload["ur3"],
            _json_dumps(payload["extra_json"]),
        ))
        row_count += 1
        case_nos.add(payload["load_case_no"])
        if len(preview) < 20:
            preview.append(payload)

    return {
        "project_id": int(project_id),
        "load_case_nos": sorted(case_nos),
        "row_count": row_count,
        "source_file_path": source_file_path,
        "rows_preview": preview,
    }


def import_fe_static_results(project_id, overwrite=True, file_path=None, rows=None,
                             load_case_no=1, instance_name=None, part_name=None):
    # Static results are stored with both translational and rotational
    # components so UX/UY/UZ and RX/RY/RZ can be correlated independently.
    ensure_tables_exist()
    static_rows = _load_static_result_payload(file_path=file_path, rows=rows)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_static_result WHERE pid = %s", (project_id,))
        source_file_path = os.path.abspath(file_path) if file_path else None
        result = _persist_fe_static_results(
            cursor,
            project_id=int(project_id),
            static_rows=static_rows,
            load_case_no=load_case_no,
            instance_name=instance_name,
            part_name=part_name,
            source_file_path=source_file_path,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def import_fe_static_results_from_project_result(
        *,
        project_id: int,
        result_group: str,
        load_case_no: int,
        step: Optional[str] = None,
        frame: Optional[int] = None,
        instances: Optional[List[str]] = None,
        overwrite: bool = True,
):
    ensure_tables_exist()
    static_payload = _collect_project_result_static_rows(
        project_id=int(project_id),
        result_group=str(result_group),
        step=step,
        frame=frame,
        instances=instances,
    )
    static_rows = _load_static_result_payload(
        rows=[
            {
                **row,
                "load_case_no": int(load_case_no),
            }
            for row in static_payload["rows"]
        ]
    )

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if overwrite:
            cursor.execute(
                "DELETE FROM t_mt_py_fem_static_result WHERE pid = %s AND load_case_no = %s",
                (int(project_id), int(load_case_no)),
            )
        result = _persist_fe_static_results(
            cursor,
            project_id=int(project_id),
            static_rows=static_rows,
            load_case_no=int(load_case_no),
        )
        conn.commit()
        result.update(
            {
                "result_group": str(result_group),
                "workspace": static_payload["workspace"],
                "step_name": static_payload["step_name"],
                "frame_idx": int(static_payload["frame_idx"]),
                "instances": list(static_payload["instances"]),
                "overwrite": bool(overwrite),
            }
        )
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_fe_static_results(project_id, load_case_no=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if load_case_no is None:
            cursor.execute("""
                SELECT load_case_no, instance_name, part_name, fem_node_label,
                       u1, u2, u3, ur1, ur2, ur3, extra_json, created_at
                FROM t_mt_py_fem_static_result
                WHERE pid = %s
                ORDER BY load_case_no, instance_name, fem_node_label
            """, (project_id,))
        else:
            cursor.execute("""
                SELECT load_case_no, instance_name, part_name, fem_node_label,
                       u1, u2, u3, ur1, ur2, ur3, extra_json, created_at
                FROM t_mt_py_fem_static_result
                WHERE pid = %s AND load_case_no = %s
                ORDER BY load_case_no, instance_name, fem_node_label
            """, (project_id, int(load_case_no)))
        return {
            "project_id": project_id,
            "load_case_no": None if load_case_no is None else int(load_case_no),
            "rows": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()


STATIC_COMPONENT_MAP = {
    "UX": ("ux", "u1"),
    "UY": ("uy", "u2"),
    "UZ": ("uz", "u3"),
    "RX": ("rx", "ur1"),
    "RY": ("ry", "ur2"),
    "RZ": ("rz", "ur3"),
}
_STATIC_TEST_DATA_DISPLACEMENT_TYPES = {"21", "位移", "位移传感器", "displacement", "displacement_sensor"}


def _normalize_static_test_sensor_type(value) -> str:
    text = str(value or "").strip().lower()
    if text.isdigit():
        return text
    return text.replace(" ", "_")


def _is_displacement_static_test_sensor_type(value) -> bool:
    return _normalize_static_test_sensor_type(value) in _STATIC_TEST_DATA_DISPLACEMENT_TYPES


def _coerce_static_test_float(value):
    if value is None or value == "":
        return None
    return float(value)


def _is_static_test_sensor_entry(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(
        key in payload
        for key in (
            "sensor_label",
            "measuring_point_name",
            "point_no",
            "point",
            "label",
            "name",
            "sensorName",
        )
    )


def _extract_static_test_entry_scalar(value) -> Optional[float]:
    if isinstance(value, dict):
        for key in ("value", "uy", "y", "displacement", "reading", "measurement"):
            if key in value and value.get(key) not in (None, ""):
                return _coerce_static_test_float(value.get(key))
        return None
    return _coerce_static_test_float(value)


def _extract_static_test_entry_components(value) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    if isinstance(value, dict):
        ux = _coerce_static_test_float(value.get("ux", value.get("x")))
        uy = _coerce_static_test_float(value.get("uy", value.get("y")))
        uz = _coerce_static_test_float(value.get("uz", value.get("z")))
        rx = _coerce_static_test_float(value.get("rx", value.get("ur1")))
        ry = _coerce_static_test_float(value.get("ry", value.get("ur2")))
        rz = _coerce_static_test_float(value.get("rz", value.get("ur3")))
        if ux is None and uy is None and uz is None:
            scalar = _extract_static_test_entry_scalar(value)
            if scalar is not None:
                return 0.0, float(scalar), 0.0, rx, ry, rz
        return (
            0.0 if ux is None else float(ux),
            0.0 if uy is None else float(uy),
            0.0 if uz is None else float(uz),
            rx,
            ry,
            rz,
        )

    scalar = _extract_static_test_entry_scalar(value)
    if scalar is None:
        return None, None, None, None, None, None
    return 0.0, float(scalar), 0.0, None, None, None


def _iter_static_test_payload_entries(payload) -> List[dict]:
    if payload is None:
        return []
    if isinstance(payload, str):
        payload = _json_loads(payload)
    if payload is None:
        return []

    if _is_static_test_sensor_entry(payload):
        return [dict(payload)]

    if isinstance(payload, dict):
        for key in ("data", "rows", "items", "results", "sensors", "values", "records"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [dict(item) if isinstance(item, dict) else {"value": item} for item in nested]
            if isinstance(nested, dict):
                payload = nested
                break

    if isinstance(payload, list):
        entries = []
        for item in payload:
            if isinstance(item, dict):
                normalized = dict(item)
                if not _is_static_test_sensor_entry(normalized):
                    candidates = [
                        (str(key), value)
                        for key, value in normalized.items()
                        if str(key or "").strip() not in {
                            "project_id",
                            "sensor_type",
                            "timestamp",
                            "time",
                            "created_at",
                            "updated_at",
                        }
                    ]
                    if len(candidates) == 1:
                        sensor_label, reading = candidates[0]
                        normalized = {"sensor_label": sensor_label, "value": reading}
                entries.append(normalized)
        return entries

    if isinstance(payload, dict):
        entries = []
        for sensor_label, reading in payload.items():
            if str(sensor_label or "").strip() in {"project_id", "sensor_type", "timestamp", "time", "created_at", "updated_at"}:
                continue
            entries.append({"sensor_label": str(sensor_label), "value": reading})
        return entries

    return []


def _load_latest_static_test_data_row(cursor, project_id: int):
    statements = [
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        ORDER BY id DESC
        """,
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        ORDER BY created_at DESC
        """,
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        ORDER BY create_time DESC
        """,
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        """,
    ]
    rows = None
    for sql in statements:
        try:
            cursor.execute(sql, (int(project_id),))
            rows = cursor.fetchall()
        except Exception:
            rows = None
        if rows is not None:
            break
    if not rows:
        return None
    for row in rows:
        if _is_displacement_static_test_sensor_type(row.get("sensor_type")):
            return row
    return dict(rows[0])


def _load_static_test_rows_from_data_table(cursor, project_id: int) -> List[dict]:
    row = _load_latest_static_test_data_row(cursor, project_id)
    if not row:
        return []

    try:
        payload = _json_loads(row.get("data"))
    except Exception as exc:
        raise ValueError(f"failed to parse t_mt_static_test_data.data JSON: {exc}") from exc

    entries = _iter_static_test_payload_entries(payload)
    if not entries:
        raise ValueError("t_mt_static_test_data.data does not contain any readable sensor entries")

    measuring_rows = []
    try:
        cursor.execute(
            """
            SELECT measuring_point_name, sensor_type_id
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id, measuring_point_name
            """,
            (int(project_id),),
        )
        measuring_rows = cursor.fetchall() or []
    except Exception:
        measuring_rows = []
    known_labels = {
        str(item.get("measuring_point_name")): item
        for item in measuring_rows
        if item.get("measuring_point_name") is not None
    }

    rows = []
    for entry in entries:
        sensor_label = None
        for key in ("sensor_label", "measuring_point_name", "point_no", "point", "label", "name", "sensorName"):
            value = entry.get(key)
            if value not in (None, ""):
                sensor_label = str(value)
                break
        if not sensor_label:
            continue
        if known_labels and sensor_label not in known_labels:
            continue

        raw_value = entry.get("data", entry.get("value", entry))
        ux, uy, uz, rx, ry, rz = _extract_static_test_entry_components(raw_value)
        if ux is None and uy is None and uz is None:
            continue
        rows.append(
            {
                "point": str(sensor_label),
                "ux": ux,
                "uy": uy,
                "uz": uz,
                "rx": rx,
                "ry": ry,
                "rz": rz,
                "load_factor": None,
                "extra_json": {
                    "source": "t_mt_static_test_data",
                    "sensor_type": row.get("sensor_type"),
                },
            }
        )

    if not rows:
        raise ValueError("no displacement sensor rows from t_mt_static_test_data matched the measuring points")
    return rows


def _load_test_static_rows(cursor, project_id: int, load_case_no: int, result_no: int) -> List[dict]:
    cursor.execute("""
        SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json
        FROM t_mt_py_test_static_result
        WHERE pid = %s AND load_case_no = %s AND result_no = %s
        ORDER BY point
    """, (int(project_id), int(load_case_no), int(result_no)))
    rows = cursor.fetchall() or []
    if rows:
        return rows
    return _load_static_test_rows_from_data_table(cursor, int(project_id))


def _resolve_static_case_selection_for_correlation(cursor, project_id: int, load_case_no=None, result_no=None):
    cursor.execute("""
        SELECT DISTINCT load_case_no
        FROM t_mt_py_fem_static_result
        WHERE pid = %s
        ORDER BY load_case_no
    """, (project_id,))
    fem_cases = [int(row["load_case_no"]) for row in cursor.fetchall()]
    if not fem_cases:
        raise ValueError("未找到 FEM 静态结果")

    if not _load_latest_static_test_data_row(cursor, project_id):
        raise ValueError("未找到 t_mt_static_test_data 中的试验静态数据")

    if load_case_no is None:
        chosen_load_case_no = int(fem_cases[0])
    else:
        chosen_load_case_no = int(load_case_no)
        if chosen_load_case_no not in fem_cases:
            raise ValueError(f"fem static load_case_no not found: {chosen_load_case_no}")

    if result_no is None:
        chosen_result_no = 1
    else:
        chosen_result_no = int(result_no)
        if chosen_result_no != 1:
            raise ValueError("t_mt_static_test_data only supports result_no=1")

    return chosen_load_case_no, chosen_result_no


def _resolve_static_components(components=None, include_rotations=False):
    if components:
        names = [str(comp).upper() for comp in components]
    else:
        names = ["UX", "UY", "UZ"]
        if include_rotations:
            names.extend(["RX", "RY", "RZ"])

    invalid = [name for name in names if name not in STATIC_COMPONENT_MAP]
    if invalid:
        raise ValueError(f"unsupported static components: {invalid}")
    return names


def _resolve_static_case_selection(cursor, project_id: int, load_case_no=None, result_no=None):
    cursor.execute("""
        SELECT DISTINCT load_case_no, result_no
        FROM t_mt_py_test_static_result
        WHERE pid = %s
        ORDER BY load_case_no, result_no
    """, (project_id,))
    test_pairs = cursor.fetchall()

    cursor.execute("""
        SELECT DISTINCT load_case_no
        FROM t_mt_py_fem_static_result
        WHERE pid = %s
        ORDER BY load_case_no
    """, (project_id,))
    fem_cases = [int(row["load_case_no"]) for row in cursor.fetchall()]
    if not fem_cases:
        raise ValueError("未找到 FEM 静态结果")

    if not test_pairs:
        if not _load_latest_static_test_data_row(cursor, project_id):
            raise ValueError("未找到试验静态结果")
        if load_case_no is None:
            chosen_load_case_no = int(fem_cases[0])
        else:
            chosen_load_case_no = int(load_case_no)
            if chosen_load_case_no not in fem_cases:
                raise ValueError(f"fem static load_case_no not found: {chosen_load_case_no}")

        if result_no is None:
            chosen_result_no = 1
        else:
            chosen_result_no = int(result_no)
            if chosen_result_no != 1:
                raise ValueError("t_mt_static_test_data only supports result_no=1")
        return chosen_load_case_no, chosen_result_no

    test_case_to_results = {}
    for row in test_pairs:
        test_case_to_results.setdefault(int(row["load_case_no"]), []).append(int(row["result_no"]))

    common_cases = sorted(set(test_case_to_results.keys()) & set(fem_cases))
    if load_case_no is None:
        if not common_cases:
            raise ValueError("no common static load_case_no between test and fem static tables")
        chosen_load_case_no = int(common_cases[0])
    else:
        chosen_load_case_no = int(load_case_no)
        if chosen_load_case_no not in test_case_to_results:
            raise ValueError(f"test static load_case_no not found: {chosen_load_case_no}")
        if chosen_load_case_no not in fem_cases:
            raise ValueError(f"fem static load_case_no not found: {chosen_load_case_no}")

    result_candidates = sorted(test_case_to_results[chosen_load_case_no])
    if result_no is None:
        chosen_result_no = int(result_candidates[0])
    else:
        chosen_result_no = int(result_no)
        if chosen_result_no not in result_candidates:
            raise ValueError(
                f"test static result_no not found for load_case_no={chosen_load_case_no}: {chosen_result_no}"
            )

    return chosen_load_case_no, chosen_result_no


def _build_static_alignment(test_rows, fem_rows, node_matches):
    # Prefer the persisted node-match table. If it does not exist yet, fall back
    # to direct label matching only when labels are unambiguous.
    fem_by_key = {}
    fem_by_label = {}
    duplicate_labels = set()
    for row in fem_rows:
        key = (str(row["instance_name"] or ""), int(row["fem_node_label"]))
        fem_by_key[key] = row
        label = int(row["fem_node_label"])
        if label in fem_by_label:
            duplicate_labels.add(label)
        fem_by_label[label] = row

    aligned = []
    if node_matches:
        for match in node_matches:
            test_row = test_rows.get(str(match["test_node_id"]))
            if test_row is None:
                continue
            fem_row = fem_by_key.get((str(match["instance_name"] or ""), int(match["fem_node_label"])))
            if fem_row is None:
                continue
            aligned.append((test_row, fem_row, match))
        return aligned

    for point_id, test_row in test_rows.items():
        try:
            label = int(point_id)
        except (TypeError, ValueError):
            continue
        if label in duplicate_labels:
            continue
        fem_row = fem_by_label.get(label)
        if fem_row is None:
            continue
        aligned.append((
            test_row,
            fem_row,
            {
                "test_node_id": point_id,
                "instance_name": fem_row["instance_name"],
                "fem_node_label": fem_row["fem_node_label"],
                "match_mode": "label_fallback",
            },
        ))
    return aligned


def _relative_error_percent(node_value: float, point_value: float) -> float:
    point = float(point_value)
    node = float(node_value)
    if abs(point) <= 1e-12:
        return 0.0
    return float((node - point) / point * 100.0)


def _analysis_error_node_no(fem_row: dict) -> str:
    instance_name = str(fem_row.get("instance_name") or "").strip()
    fem_node_label = int(fem_row["fem_node_label"])
    if instance_name:
        return f"{instance_name}::{fem_node_label}"
    return str(fem_node_label)


def _should_store_static_analysis_error_row(*, sensor_type_id, component_name: str, point_value: float) -> bool:
    resolved_component = str(component_name or "").upper()
    if _is_displacement_static_test_sensor_type(sensor_type_id):
        if resolved_component in {"UX", "UZ"} and abs(float(point_value)) <= 1e-12:
            return False
    return True


def _load_measuring_point_sensor_type_map(cursor, project_id: int) -> Dict[str, Optional[int]]:
    try:
        cursor.execute(
            """
            SELECT measuring_point_name, sensor_type_id
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id, measuring_point_name
            """,
            (int(project_id),),
        )
        rows = cursor.fetchall() or []
    except Exception:
        return {}

    result: Dict[str, Optional[int]] = {}
    for row in rows:
        measuring_point_name = row.get("measuring_point_name")
        if measuring_point_name in (None, ""):
            continue
        sensor_type_id = row.get("sensor_type_id")
        result[str(measuring_point_name)] = None if sensor_type_id is None else int(sensor_type_id)
    return result


def _build_static_analysis_error_rows(
    *,
    aligned_rows,
    component_names,
    load_case_no: int,
    result_no: int,
    value_prefix: str,
    sensor_type_map: Optional[Dict[str, Optional[int]]] = None,
) -> List[dict]:
    rows: List[dict] = []
    for test_row, fem_row, _match in aligned_rows:
        point_no = str(test_row["point"])
        node_no = _analysis_error_node_no(fem_row)
        sensor_type_id = None
        if sensor_type_map:
            sensor_type_id = sensor_type_map.get(point_no)
        for component_name in component_names:
            test_col, fem_col = STATIC_COMPONENT_MAP[component_name]
            point_value = test_row.get(test_col)
            node_value = fem_row.get(fem_col)
            if point_value is None or node_value is None:
                continue
            node_value = float(node_value)
            point_value = float(point_value)
            if not _should_store_static_analysis_error_row(
                sensor_type_id=sensor_type_id,
                component_name=str(component_name),
                point_value=point_value,
            ):
                continue
            rows.append(
                {
                    "load_case_no": int(load_case_no),
                    "result_no": int(result_no),
                    "point_no": point_no,
                    "node_no": node_no,
                    "component_name": str(component_name),
                    "point_value": point_value,
                    f"{value_prefix}_node_value": node_value,
                    f"{value_prefix}_relative_error": _relative_error_percent(node_value, point_value),
                    f"{value_prefix}_abs_error": float(abs(node_value - point_value)),
                    "sensor_type_id": sensor_type_id,
                }
            )
    return rows


def _upsert_analysis_error_rows(cursor, project_id: int, rows: Sequence[dict], *, value_prefix: str) -> None:
    if value_prefix not in {"initial", "updated"}:
        raise ValueError("value_prefix must be initial or updated")
    point_value_update_sql = "point_value = VALUES(point_value)," if value_prefix == "initial" else ""
    insert_sql = f"""
        INSERT INTO t_mt_py_fem_analysis_error
        (pid, load_case_no, result_no, point_no, node_no, component_name, point_value,
         {value_prefix}_node_value, {value_prefix}_relative_error, {value_prefix}_abs_error, sensor_type_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            node_no = VALUES(node_no),
            {point_value_update_sql}
            {value_prefix}_node_value = VALUES({value_prefix}_node_value),
            {value_prefix}_relative_error = VALUES({value_prefix}_relative_error),
            {value_prefix}_abs_error = VALUES({value_prefix}_abs_error),
            sensor_type_id = VALUES(sensor_type_id)
    """
    for row in rows:
        cursor.execute(
            insert_sql,
            (
                int(project_id),
                int(row["load_case_no"]),
                int(row["result_no"]),
                str(row["point_no"]),
                str(row["node_no"]),
                str(row["component_name"]),
                row.get("point_value"),
                row.get(f"{value_prefix}_node_value"),
                row.get(f"{value_prefix}_relative_error"),
                row.get(f"{value_prefix}_abs_error"),
                row.get("sensor_type_id"),
            ),
        )


def store_updated_static_analysis_error(
    *,
    project_id: int,
    fem_rows: Sequence[dict],
    load_case_no=None,
    result_no=None,
    components=None,
    include_rotations: bool = False,
):
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT DISTINCT load_case_no, result_no
            FROM t_mt_py_test_static_result
            WHERE pid = %s
            ORDER BY load_case_no, result_no
        """, (project_id,))
        test_pairs = cursor.fetchall()
        if not test_pairs:
            raise ValueError("未找到试验静态结果")

        test_case_to_results = {}
        for row in test_pairs:
            test_case_to_results.setdefault(int(row["load_case_no"]), []).append(int(row["result_no"]))

        if load_case_no is None:
            chosen_load_case_no = int(sorted(test_case_to_results.keys())[0])
        else:
            chosen_load_case_no = int(load_case_no)
            if chosen_load_case_no not in test_case_to_results:
                raise ValueError(f"test static load_case_no not found: {chosen_load_case_no}")

        result_candidates = sorted(test_case_to_results[chosen_load_case_no])
        if result_no is None:
            chosen_result_no = int(result_candidates[0])
        else:
            chosen_result_no = int(result_no)
            if chosen_result_no not in result_candidates:
                raise ValueError(
                    f"test static result_no not found for load_case_no={chosen_load_case_no}: {chosen_result_no}"
                )

        component_names = _resolve_static_components(
            components=components,
            include_rotations=include_rotations,
        )
        test_rows_raw = _load_static_test_rows_from_data_table(cursor, int(project_id))
        if not test_rows_raw:
            raise ValueError("未找到 t_mt_static_test_data 中的试验静态数据记录")
        test_rows = {str(row["point"]): row for row in test_rows_raw}

        cursor.execute("""
            SELECT test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id
        """, (project_id,))
        node_matches = cursor.fetchall()

        aligned_rows = _build_static_alignment(test_rows, list(fem_rows or []), node_matches)
        if not aligned_rows:
            raise ValueError("试验静态结果与修正后 FEM 静态结果之间未找到可对齐的数据行")

        sensor_type_map = _load_measuring_point_sensor_type_map(cursor, int(project_id))
        error_rows = _build_static_analysis_error_rows(
            aligned_rows=aligned_rows,
            component_names=component_names,
            load_case_no=chosen_load_case_no,
            result_no=chosen_result_no,
            value_prefix="updated",
            sensor_type_map=sensor_type_map,
        )
        _upsert_analysis_error_rows(cursor, project_id, error_rows, value_prefix="updated")
        conn.commit()
        return {
            "project_id": int(project_id),
            "load_case_no": int(chosen_load_case_no),
            "result_no": int(chosen_result_no),
            "components": component_names,
            "analysis_error_row_count": len(error_rows),
            "analysis_error_preview": error_rows[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def compute_static_correlation(
    project_id,
    load_case_no=None,
    result_no=None,
    components=None,
    include_rotations=False,
):
    # Static correlation compares one chosen test static result against one FE
    # load case after resolving node alignment and the requested components.
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _require_non_modal_project(int(project_id), cursor=cursor)
        chosen_load_case_no, chosen_result_no = _resolve_static_case_selection_for_correlation(
            cursor,
            project_id,
            load_case_no=load_case_no,
            result_no=result_no,
        )
        component_names = _resolve_static_components(
            components=components,
            include_rotations=include_rotations,
        )

        test_rows_raw = _load_static_test_rows_from_data_table(cursor, int(project_id))
        if not test_rows_raw:
            raise ValueError("未找到 t_mt_static_test_data 中的试验静态数据记录")
        test_rows = {str(row["point"]): row for row in test_rows_raw}

        cursor.execute("""
            SELECT load_case_no, instance_name, part_name, fem_node_label,
                   u1, u2, u3, ur1, ur2, ur3, extra_json
            FROM t_mt_py_fem_static_result
            WHERE pid = %s AND load_case_no = %s
            ORDER BY instance_name, fem_node_label
        """, (project_id, chosen_load_case_no))
        fem_rows = cursor.fetchall()
        if not fem_rows:
            raise ValueError("未找到所选 FEM 静态结果记录")

        cursor.execute("""
            SELECT test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id
        """, (project_id,))
        node_matches = cursor.fetchall()

        aligned_rows = _build_static_alignment(test_rows, fem_rows, node_matches)
        if not aligned_rows:
            raise ValueError("试验静态结果与 FEM 静态结果之间未找到可对齐的数据行")

        sensor_type_map = _load_measuring_point_sensor_type_map(cursor, int(project_id))
        test_values = []
        fem_values = []
        anchors = []
        component_counts = {name: 0 for name in component_names}
        analysis_error_rows = []

        for test_row, fem_row, match in aligned_rows:
            # Each aligned point can contribute multiple scalar channels
            # depending on the requested component list.
            for comp_name in component_names:
                test_col, fem_col = STATIC_COMPONENT_MAP[comp_name]
                test_val = test_row.get(test_col)
                fem_val = fem_row.get(fem_col)
                if test_val is None or fem_val is None:
                    continue
                test_values.append(complex(float(test_val), 0.0))
                fem_values.append(complex(float(fem_val), 0.0))
                component_counts[comp_name] += 1
                sensor_type_id = sensor_type_map.get(str(test_row["point"]))
                if _should_store_static_analysis_error_row(
                    sensor_type_id=sensor_type_id,
                    component_name=str(comp_name),
                    point_value=float(test_val),
                ):
                    analysis_error_rows.append(
                        {
                            "load_case_no": int(chosen_load_case_no),
                            "result_no": int(chosen_result_no),
                            "point_no": str(test_row["point"]),
                            "node_no": _analysis_error_node_no(fem_row),
                            "component_name": comp_name,
                            "point_value": float(test_val),
                            "initial_node_value": float(fem_val),
                            "initial_relative_error": _relative_error_percent(float(fem_val), float(test_val)),
                            "initial_abs_error": float(abs(float(fem_val) - float(test_val))),
                            "sensor_type_id": sensor_type_id,
                        }
                    )
                if len(anchors) < 50:
                    anchors.append({
                        "test_point": str(test_row["point"]),
                        "instance_name": fem_row["instance_name"],
                        "fem_node_label": int(fem_row["fem_node_label"]),
                        "component": comp_name,
                        "test_value": float(test_val),
                        "fem_value": float(fem_val),
                        "match_mode": match.get("match_mode", "node_match"),
                    })

        if len(test_values) < 2:
            raise ValueError("可对齐的静态值数量不足，无法计算 dac/dsf")

        metrics = _compute_dac_dsf(
            np.asarray(test_values, dtype=np.complex128),
            np.asarray(fem_values, dtype=np.complex128),
        )

        return {
            "project_id": project_id,
            "load_case_no": int(chosen_load_case_no),
            "result_no": int(chosen_result_no),
            "components": component_names,
            "aligned_point_count": len(aligned_rows),
            "value_count": len(test_values),
            "component_value_counts": component_counts,
            "analysis_error_row_count": len(analysis_error_rows),
            "analysis_error_preview": analysis_error_rows[:20],
            "dac": metrics["dac"],
            "dsf": metrics["dsf"],
            "_analysis_error_rows": analysis_error_rows,
            "extra": {
                "scale_real": metrics["scale_real"],
                "scale_imag": metrics["scale_imag"],
                "scale_phase_deg": metrics["scale_phase_deg"],
                "test_norm": metrics["test_norm"],
                "fem_norm": metrics["fem_norm"],
                "residual_norm": metrics["residual_norm"],
                "anchors_preview": anchors,
            },
        }
    finally:
        cursor.close()
        conn.close()


def _ensure_node_matches(project_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_node_id
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            LIMIT 1
        """, (int(project_id),))
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if row:
        return False

    match_test_nodes(int(project_id), overwrite=False)
    return True


def _ensure_static_node_matches(project_id: int) -> bool:
    return _ensure_node_matches(project_id)


def evaluate_static_correlation(
    project_id,
    load_case_no=None,
    result_no=None,
    components=None,
    include_rotations=False,
):
    _ensure_static_node_matches(int(project_id))
    result = compute_static_correlation(
        project_id=project_id,
        load_case_no=load_case_no,
        result_no=result_no,
        components=components,
        include_rotations=include_rotations,
    )

    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        analysis_error_rows = list(result.get("_analysis_error_rows") or [])
        cursor.execute("""
            INSERT INTO t_mt_py_fem_dac_dsf (pid, dac, dsf)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dac = VALUES(dac),
                dsf = VALUES(dsf)
        """, (
            int(project_id),
            float(result["dac"]),
            float(result["dsf"]),
        ))
        _upsert_analysis_error_rows(cursor, project_id, analysis_error_rows, value_prefix="initial")
        update_work_condition_project_status(
            int(project_id),
            cursor=cursor,
            consistency_status=1,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    result.pop("_analysis_error_rows", None)
    return result


def _load_test_mode_vectors(cursor, project_id: int) -> Dict[int, Dict[str, np.ndarray]]:
    # Test modal shapes are stored in multiple historical schemas. This loader
    # normalizes them into complex 3-component vectors keyed by test point id.
    modes: Dict[int, Dict[str, np.ndarray]] = {}

    cursor.execute("""
        SELECT mode_no, point, ux, uy, uz
        FROM t_mt_py_test_modal_shape_real
        WHERE pid = %s
        ORDER BY mode_no, point
    """, (project_id,))
    for row in cursor.fetchall():
        modes.setdefault(int(row["mode_no"]), {})[str(row["point"])] = np.array([
            complex(float(row["ux"] or 0.0), 0.0),
            complex(float(row["uy"] or 0.0), 0.0),
            complex(float(row["uz"] or 0.0), 0.0),
        ], dtype=np.complex128)

    cursor.execute("""
        SELECT mode_no, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz
        FROM t_mt_py_test_modal_shape_imag
        WHERE pid = %s
        ORDER BY mode_no, point
    """, (project_id,))
    for row in cursor.fetchall():
        modes.setdefault(int(row["mode_no"]), {})[str(row["point"])] = np.array([
            complex(float(row["re_ux"] or 0.0), float(row["im_ux"] or 0.0)),
            complex(float(row["re_uy"] or 0.0), float(row["im_uy"] or 0.0)),
            complex(float(row["re_uz"] or 0.0), float(row["im_uz"] or 0.0)),
        ], dtype=np.complex128)

    if modes:
        return modes

    cursor.execute("""
        SELECT mode_no, modal_shape
        FROM t_mt_py_test_modal_shape
        WHERE pid = %s
        ORDER BY mode_no
    """, (project_id,))
    for row in cursor.fetchall():
        shape_json = _json_loads(row["modal_shape"]) or {}
        for point_id, comp_map in shape_json.items():
            real = comp_map.get("real", [0.0, 0.0, 0.0])
            imag = comp_map.get("imag", [0.0, 0.0, 0.0])
            modes.setdefault(int(row["mode_no"]), {})[str(point_id)] = np.array([
                complex(float(real[0]), float(imag[0])),
                complex(float(real[1]), float(imag[1])),
                complex(float(real[2]), float(imag[2])),
            ], dtype=np.complex128)
    return modes


def _load_fem_mode_vectors(cursor, project_id: int):
    cursor.execute("""
        SELECT mode_no, frequency, instance_name, fem_node_label, u1, u2, u3
        FROM t_mt_py_fem_modal_result
        WHERE pid = %s
        ORDER BY mode_no, instance_name, fem_node_label
    """, (project_id,))
    modes: Dict[int, Dict[Tuple[str, int], np.ndarray]] = {}
    freqs = {}
    for row in cursor.fetchall():
        mode_no = int(row["mode_no"])
        inst_name = str(row["instance_name"] or "")
        freqs.setdefault(mode_no, _safe_float(row["frequency"]))
        modes.setdefault(mode_no, {})[(inst_name, int(row["fem_node_label"]))] = np.array([
            float(row["u1"] or 0.0),
            float(row["u2"] or 0.0),
            float(row["u3"] or 0.0),
        ], dtype=np.float64)
    return modes, freqs


def _load_test_modal_frequencies(cursor, project_id: int) -> Dict[int, float]:
    cursor.execute("""
        SELECT mode_no, frequency
        FROM t_mt_py_test_modal_frequency
        WHERE pid = %s
        ORDER BY mode_no
    """, (project_id,))
    return {int(row["mode_no"]): _safe_float(row["frequency"]) for row in cursor.fetchall()}


def _compute_dac_dsf(test_vec: np.ndarray, fem_vec: np.ndarray) -> dict:
    test_energy = float(np.vdot(test_vec, test_vec).real)
    fem_energy = float(np.vdot(fem_vec, fem_vec).real)
    if test_energy <= 1e-18 or fem_energy <= 1e-18:
        raise ValueError("向量能量为 0")

    cross = np.vdot(test_vec, fem_vec)
    scale = np.vdot(fem_vec, test_vec) / np.vdot(test_vec, test_vec)
    residual = test_vec - scale * fem_vec
    return {
        "dac": float(100.0 * (abs(cross) ** 2) / (test_energy * fem_energy)),
        "mac": float(100.0 * (abs(cross) ** 2) / (test_energy * fem_energy)),
        "dsf": float(abs(scale)),
        "scale_real": float(scale.real),
        "scale_imag": float(scale.imag),
        "scale_phase_deg": float(math.degrees(math.atan2(scale.imag, scale.real))) if abs(scale) > 1e-18 else 0.0,
        "test_norm": float(math.sqrt(test_energy)),
        "fem_norm": float(math.sqrt(fem_energy)),
        "residual_norm": float(np.sqrt(np.vdot(residual, residual).real)),
    }


def compute_modal_correlation(project_id, overwrite=True):
    # Modal correlation enumerates all test-mode / FE-mode combinations and
    # scores them using the already-resolved DOF correspondence table.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _require_modal_project(int(project_id), cursor=cursor)
        cursor.execute("""
            SELECT test_node_id, test_dof, instance_name, fem_node_label, fem_dof,
                   direction_x, direction_y, direction_z
            FROM t_mt_py_fem_dof_match
            WHERE pid = %s
            ORDER BY test_node_id, test_dof
        """, (project_id,))
        dof_matches = cursor.fetchall()
        if not dof_matches:
            raise _required_operation_error(
                "未找到自由度匹配结果，请先完成试验自由度与有限元自由度的匹配操作",
                operation="完成试验自由度与有限元自由度的匹配",
                interface_key="match_dofs",
            )

        test_modes = _load_test_mode_vectors(cursor, project_id)
        if not test_modes:
            raise ValueError("未找到试验模态振型数据")

        test_freqs = _load_test_modal_frequencies(cursor, project_id)
        fem_modes, fem_freqs = _load_fem_mode_vectors(cursor, project_id)
        if not fem_modes:
            raise _required_operation_error(
                "未找到有限元模态结果，请先完成有限元模态结果导入操作",
                operation="完成有限元模态结果导入",
                interface_key="import_fem_modal",
            )

        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_modal_correlation
        (pid, test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, mac, freq_test, freq_fem, freq_error_ratio, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            dof_pair_count = VALUES(dof_pair_count),
            dac = VALUES(dac),
            dsf = VALUES(dsf),
            mac = VALUES(mac),
            freq_test = VALUES(freq_test),
            freq_fem = VALUES(freq_fem),
            freq_error_ratio = VALUES(freq_error_ratio),
            extra_json = VALUES(extra_json),
            created_at = CURRENT_TIMESTAMP
        """

        results = []
        for test_mode_no, test_mode_map in sorted(test_modes.items()):
            for fem_mode_no, fem_mode_map in sorted(fem_modes.items()):
                test_values = []
                fem_values = []
                anchors = []

                for match in dof_matches:
                    # The FE modal vector is projected onto the matched test DOF
                    # direction before DAC/DSF are evaluated.
                    test_point = test_mode_map.get(str(match["test_node_id"]))
                    if test_point is None:
                        continue

                    comp_idx = DOF_COMPONENT_INDEX[str(match["test_dof"]).upper()]
                    test_scalar = complex(test_point[comp_idx])
                    fe_key = (str(match["instance_name"] or ""), int(match["fem_node_label"]))
                    fem_vector = fem_mode_map.get(fe_key)
                    if fem_vector is None:
                        continue

                    direction = np.array([
                        float(match["direction_x"]),
                        float(match["direction_y"]),
                        float(match["direction_z"]),
                    ], dtype=np.float64)
                    fem_scalar = complex(float(np.dot(fem_vector, direction)), 0.0)
                    test_values.append(test_scalar)
                    fem_values.append(fem_scalar)
                    if len(anchors) < 20:
                        anchors.append({
                            "test_node_id": str(match["test_node_id"]),
                            "test_dof": match["test_dof"],
                            "instance_name": match["instance_name"],
                            "fem_node_label": int(match["fem_node_label"]),
                            "fem_dof": match["fem_dof"],
                        })

                if len(test_values) < 2:
                    continue

                metrics = _compute_dac_dsf(
                    np.asarray(test_values, dtype=np.complex128),
                    np.asarray(fem_values, dtype=np.complex128),
                )
                freq_test = test_freqs.get(test_mode_no)
                freq_fem = fem_freqs.get(fem_mode_no)
                freq_error_ratio = None
                if freq_test is not None and abs(freq_test) > 1e-18 and freq_fem is not None:
                    freq_error_ratio = float((freq_fem - freq_test) / freq_test)

                item = {
                    "test_mode_no": int(test_mode_no),
                    "fem_mode_no": int(fem_mode_no),
                    "dof_pair_count": len(test_values),
                    "dac": metrics["dac"],
                    "mac": metrics["mac"],
                    "dsf": metrics["dsf"],
                    "freq_test": freq_test,
                    "freq_fem": freq_fem,
                    "freq_error_ratio": freq_error_ratio,
                    "extra_json": {
                        "scale_real": metrics["scale_real"],
                        "scale_imag": metrics["scale_imag"],
                        "scale_phase_deg": metrics["scale_phase_deg"],
                        "test_norm": metrics["test_norm"],
                        "fem_norm": metrics["fem_norm"],
                        "residual_norm": metrics["residual_norm"],
                        "anchors_preview": anchors,
                    },
                }
                results.append(item)
                cursor.execute(insert_sql, (
                    project_id,
                    item["test_mode_no"],
                    item["fem_mode_no"],
                    item["dof_pair_count"],
                    item["dac"],
                    item["dsf"],
                    item["mac"],
                    item["freq_test"],
                    item["freq_fem"],
                    item["freq_error_ratio"],
                    _json_dumps(item["extra_json"]),
                ))

        if not results:
            raise ValueError("未生成有效的模态相关性配对结果")

        best_pair = max(results, key=lambda row: row["dac"])
        # Keep the single strongest modal pair in the legacy static-shape pair
        # table because some existing consumers still read that summary record.
        cursor.execute("""
            INSERT INTO t_mt_py_fem_static_shape_pairs
            (pid, fem_res, test_res, DAC, DSF)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                fem_res = VALUES(fem_res),
                test_res = VALUES(test_res),
                DAC = VALUES(DAC),
                DSF = VALUES(DSF)
        """, (
            project_id,
            f"MODE_{best_pair['fem_mode_no']}",
            f"MODE_{best_pair['test_mode_no']}",
            best_pair["dac"],
            best_pair["dsf"],
        ))

        conn.commit()
        best_by_test_mode = {}
        for item in results:
            key = item["test_mode_no"]
            best = best_by_test_mode.get(key)
            if best is None or item["dac"] > best["dac"]:
                best_by_test_mode[key] = item

        return {
            "project_id": project_id,
            "comparison_count": len(results),
            "best_pairs_by_test_mode": [best_by_test_mode[key] for key in sorted(best_by_test_mode)],
            "results_preview": results[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_modal_correlation(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_mode_no, fem_mode_no, mac
            FROM t_mt_py_fem_modal_correlation
            WHERE pid = %s
            ORDER BY fem_mode_no, test_mode_no
        """, (project_id,))
        rows = cursor.fetchall()
        fem_mode_order = sorted({int(row["fem_mode_no"]) for row in rows})
        test_mode_order = sorted({int(row["test_mode_no"]) for row in rows})
        fem_mode_index = {mode_no: idx for idx, mode_no in enumerate(fem_mode_order)}
        test_mode_index = {mode_no: idx for idx, mode_no in enumerate(test_mode_order)}
        mac_matrix = [[None for _ in test_mode_order] for _ in fem_mode_order]
        for row in rows:
            row_idx = fem_mode_index[int(row["fem_mode_no"])]
            col_idx = test_mode_index[int(row["test_mode_no"])]
            mac_matrix[row_idx][col_idx] = float(row["mac"]) if row["mac"] is not None else None
        return {
            "project_id": project_id,
            "row_mode_order": fem_mode_order,
            "column_mode_order": test_mode_order,
            "matrix": mac_matrix,
        }
    finally:
        cursor.close()
        conn.close()


def get_modal_correlation_matrix_payload(project_id):
    raw = get_modal_correlation(project_id)
    row_mode_order = [str(item) for item in (raw.get("row_mode_order") or [])]
    column_mode_order = [str(item) for item in (raw.get("column_mode_order") or [])]
    matrix = [list(row) for row in (raw.get("matrix") or [])]

    heatmap_points = []
    for row_index, _row_name in enumerate(row_mode_order):
        current_row = matrix[row_index] if row_index < len(matrix) else []
        for col_index, _col_name in enumerate(column_mode_order):
            heatmap_points.append([
                row_index,
                col_index,
                current_row[col_index] if col_index < len(current_row) else None,
            ])

    return {
        "project_id": int(project_id),
        "row_mode_order": row_mode_order,
        "column_mode_order": column_mode_order,
        "data": {
            "rows": row_mode_order,
            "column": column_mode_order,
            "data": heatmap_points,
        },
        "summary": {
            "fem_mode_count": len(row_mode_order),
            "test_mode_count": len(column_mode_order),
            "point_count": len(heatmap_points),
        },
    }


def get_modal_correlation_table_payload(project_id):
    raw = get_modal_correlation(project_id)
    row_mode_order = [str(item) for item in (raw.get("row_mode_order") or [])]
    column_mode_order = [str(item) for item in (raw.get("column_mode_order") or [])]
    matrix = [list(row) for row in (raw.get("matrix") or [])]

    table_rows = []
    for row_index, _row_name in enumerate(row_mode_order):
        current_row = matrix[row_index] if row_index < len(matrix) else []
        row_item = {}
        for col_index, col_name in enumerate(column_mode_order):
            row_item[col_name] = current_row[col_index] if col_index < len(current_row) else None
        table_rows.append(row_item)

    return {
        "project_id": int(project_id),
        "row_mode_order": row_mode_order,
        "column_mode_order": column_mode_order,
        "rows": row_mode_order,
        "column": column_mode_order,
        "data": table_rows,
        "summary": {
            "fem_mode_count": len(row_mode_order),
            "test_mode_count": len(column_mode_order),
            "point_count": len(row_mode_order) * len(column_mode_order),
        },
    }
