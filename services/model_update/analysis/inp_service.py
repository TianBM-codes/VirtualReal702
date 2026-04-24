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
from db import get_connection, ensure_tables_exist, clear_fem_tables
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

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
        if elastic and str(elastic.elastic_type).upper() == "ISOTROPIC" and elastic.data:
            row0 = list(elastic.data[0])
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
    {"quantity_code": "H", "quantity_name": "H", "unit": None, "enabled": 1, "sort_no": 2},
)


def _quantity_description(quantity_code: str, quantity_name: Optional[str] = None) -> str:
    token = str(quantity_code or "").strip().upper()
    if token == "E":
        return "杨氏模量"
    if token == "H":
        return "壳单元厚度"
    return f"{str(quantity_name or token).strip()} parameter."


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

    capability_rows = [dict(row) for row in (quantity_set_capabilities or [])]
    result = []
    for quantity in quantity_rows:
        quantity_code = str(quantity.get("quantity_code") or "").strip().upper()
        quantity_name = str(quantity.get("quantity_name") or quantity_code).strip()
        description = _quantity_description(quantity_code, quantity_name)
        matched_capabilities = [
            row for row in capability_rows
            if str(row.get("quantity_code") or "").strip().upper() == quantity_code
        ]
        for level, support_key in (("GLOBAL", "supports_global"), ("LOCAL", "supports_local")):
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
                if not row.get(support_key):
                    continue
                _append_unique_name(set_names, seen_names, row.get("set_name"))
            if not set_names:
                continue
            result.append(
                {
                    "parameter_name": quantity_name,
                    "description": description,
                    "level": level,
                    "sets": [{"rows": val} for val in set_names],
                }
            )
    return result


def _normalize_quantity_code(quantity_code: str) -> str:
    value = str(quantity_code or "").strip().upper()
    if value not in {"E", "H"}:
        raise ValueError("quantity_code must be one of: E, H")
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
        return "H"
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
    quantity_maps = {"E": {}, "H": {}}
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
                    "H": h_value,
                },
            }

            for label in labels:
                key = (str(part_name), int(label))
                if e_value is not None:
                    quantity_maps["E"][key] = float(e_value)
                if h_value is not None:
                    quantity_maps["H"][key] = float(h_value)

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
                if quantity_code == "H":
                    if property_info["quantity_values"].get("H") is not None and element_family == "SHELL":
                        supports_global = True
                        supports_local = True
                elif quantity_code == "E":
                    if property_info["quantity_values"].get("E") is not None and element_family in {"SHELL", "SOLID", "BEAM"}:
                        supports_global = True
                        supports_local = True
            elif element_family in {"SHELL", "SOLID", "BEAM"}:
                set_role = "HOMOGENEOUS_TYPE_SET"
                if quantity_code == "H":
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
        if clear_before_insert:
            # Full refresh for the FEM-side catalog tables when a new INP is
            # imported for the same project.
            clear_fem_tables(cursor, project_id)
        else:
            # Keep unrelated FEM imports intact, but replace the parameter and
            # response metadata derived directly from the current INP file.
            cursor.execute("DELETE FROM t_mt_py_fem_parameter_definition WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_parameter_target WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_design_response_catalog WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_selected_parameter WHERE pid = %s", (project_id,))

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
        INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Thickness = VALUES(Thickness),
            NSM = VALUES(NSM),
            THETA = VALUES(THETA)
        """
        for item in legacy_properties["shell_rows"]:
            cursor.execute(shell_property_sql, (
                item["id"],
                project_id,
                item["thickness"],
                item["nsm"],
                item["theta"],
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

        design_response_sql = """
        INSERT INTO t_mt_py_fem_design_response_catalog
        (pid, response_no, request_no, step_name, frequency, region_type, set_name, variables_json, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for item in design_responses:
            cursor.execute(design_response_sql, (
                project_id,
                item["response_no"],
                item["request_no"],
                item.get("step_name"),
                item["frequency"],
                item["region_type"],
                item["set_name"],
                _json_dumps(item.get("variables") or []),
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

        conn.commit()
        return {
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
            "diagnostics": len(getattr(model, "diagnostics", []) or []),
            "parameter_definitions_preview": parameter_definitions[:10],
            "parameter_targets_preview": parameter_targets[:10],
            "design_responses_preview": design_responses[:10],
            "supported_quantities_preview": supported_quantities[:10],
            "quantity_set_capabilities_preview": quantity_set_capabilities[:10],
        }
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

        query = """
            SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name,
                   set_role, element_family, section_type, material_name, member_count,
                   supports_global, supports_local, current_value, extra_json
            FROM t_mt_py_fem_quantity_set_capability
            WHERE pid = %s AND quantity_code = %s AND set_name = %s
        """
        params = [project_id, resolved_quantity_code, set_name]
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

        cursor.execute("""
            SELECT quantity_code, extra_json
            FROM t_mt_py_fem_selected_parameter
            WHERE pid = %s AND quantity_code = %s
        """, (project_id, resolved_quantity_code))
        incoming_overlap_keys = set(target_keys)
        for row in cursor.fetchall() or []:
            if incoming_overlap_keys & _extract_target_keys(row.get("extra_json")):
                raise ValueError("the selected set overlaps with an existing parameter of the same quantity")

        insert_sql = """
        INSERT INTO t_mt_py_fem_selected_parameter
        (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope,
         instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        created_parameters = []
        if resolved_mode == "GLOBAL":
            resolved_parameter_name = parameter_group_name
            cursor.execute(insert_sql, (
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
                cursor.execute(insert_sql, (
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
        raise ValueError("node octree cache not found, import inp first")
    return row


def match_test_nodes(project_id, max_distance=None, overwrite=True,
                     auto_translate=True, translation=None, rotation=None, auto_rotate=True):
    # Match imported test nodes onto the FE node cloud stored in the octree
    # cache. The saved mapping is reused by DOF matching and correlation steps.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache_path = octree_meta["cache_file_path"]
        if not os.path.exists(cache_path):
            raise ValueError(f"octree cache file not found: {cache_path}")

        cursor.execute("""
            SELECT nid, x, y, z
            FROM t_mt_py_test_node
            WHERE pid = %s
            ORDER BY nid
        """, (project_id,))
        test_nodes = cursor.fetchall()
        if not test_nodes:
            raise ValueError("test nodes not found, import UNV test data first")

        cache = _load_octree_cache(cache_path)
        part_lookup = _cache_part_lookup(cache)
        raw_test_coords = np.array(
            [[float(row["x"]), float(row["y"]), float(row["z"])] for row in test_nodes],
            dtype=np.float64,
        )

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
                "test_node_id": str(row["nid"]),
                "instance_name": inst_name,
                "part_name": part_lookup.get((inst_name, fem_label)),
                "fem_node_label": fem_label,
                "fem_coord": fem_coord.tolist(),
                "distance": float(distance),
                "x_offset": float(delta[0]),
                "y_offset": float(delta[1]),
                "z_offset": float(delta[2]),
            }
            if max_distance is None or float(distance) <= float(max_distance):
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
        conn.commit()

        return {
            "project_id": project_id,
            "tested_points": len(test_nodes),
            "matched_points": len(matches),
            "transform": transform_payload,
            "max_distance": max_distance,
            "matches_preview": matches[:20],
            "octree_cache_path": cache_path,
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
            raise ValueError("node matches not found, run /pair/node_point first")

        cursor.execute("""
            SELECT nid, x, y, z
            FROM t_mt_py_test_node
            WHERE pid = %s
            ORDER BY nid
        """, (project_id,))
        test_nodes = cursor.fetchall()
        test_node_lookup = {
            str(row["nid"]): _coord_key([row["x"], row["y"], row["z"]])
            for row in test_nodes
        }

        cursor.execute("""
            SELECT id, measuring_point_name, x_position, y_position, z_position
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id
        """, (project_id,))
        measuring_rows = cursor.fetchall()
        sensor_lookup = {
            _coord_key([row["x_position"], row["y_position"], row["z_position"]]): str(row["measuring_point_name"])
            for row in measuring_rows
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
            sensor_name = sensor_lookup.get(coord_key)
            if not sensor_name:
                continue
            fem_coord = fem_coord_lookup.get((str(row["instance_name"] or ""), int(row["fem_node_label"])))
            if fem_coord is None:
                continue
            sensor_names.append(sensor_name)
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
            raise ValueError("transform operation not found")
        return {
            "type": str(row["transform_type"]),
            "matrix4": _normalize_matrix4(_json_loads(row["matrix4_json"])),
        }
    finally:
        cursor.close()
        conn.close()


def _get_test_modal_point_ids(cursor, project_id: int) -> set:
    point_ids = set()
    cursor.execute("""
        SELECT DISTINCT point
        FROM t_mt_py_test_modal_shape_real
        WHERE pid = %s
    """, (project_id,))
    point_ids.update(str(row["point"]) for row in cursor.fetchall())
    cursor.execute("""
        SELECT DISTINCT point
        FROM t_mt_py_test_modal_shape_imag
        WHERE pid = %s
    """, (project_id,))
    point_ids.update(str(row["point"]) for row in cursor.fetchall())
    return point_ids


def match_test_dofs(project_id, overwrite=True):
    ensure_tables_exist()
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
            raise ValueError("node matches not found, run /match/nodes first")

        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache = _load_octree_cache(octree_meta["cache_file_path"])
        part_lookup = _cache_part_lookup(cache)
        test_modal_points = _get_test_modal_point_ids(cursor, project_id)
        filtered_matches = [row for row in node_matches if not test_modal_points or str(row["test_node_id"]) in test_modal_points]
        if not filtered_matches:
            raise ValueError("no matched test nodes have modal shape data")

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

        dof_matches = []
        for row in filtered_matches:
            transform_payload = _json_loads(row["transform_json"]) or {}
            rot_m = _rotation_to_matrix(transform_payload.get("rotation"))
            inst_name = str(row["instance_name"])
            fem_node_label = int(row["fem_node_label"])
            part_name = part_lookup.get((inst_name, fem_node_label))

            for axis_idx, test_dof in enumerate(TEST_DOF_SEQUENCE):
                basis = np.zeros(3, dtype=np.float64)
                basis[axis_idx] = 1.0
                direction = rot_m @ basis
                direction_norm = float(np.linalg.norm(direction))
                if direction_norm <= 1e-12:
                    direction = basis
                    direction_norm = 1.0
                direction = direction / direction_norm
                best_idx = int(np.argmax(np.abs(direction)))
                fem_dof = FE_DOF_SEQUENCE[best_idx]
                match_score = float(abs(direction[best_idx]))

                dof_row = {
                    "test_node_id": str(row["test_node_id"]),
                    "test_dof": test_dof,
                    "instance_name": inst_name,
                    "part_name": part_name,
                    "fem_node_label": fem_node_label,
                    "fem_dof": fem_dof,
                    "direction": direction.tolist(),
                    "match_score": match_score,
                    "transform": transform_payload,
                }
                dof_matches.append(dof_row)
                cursor.execute(insert_sql, (
                    project_id,
                    dof_row["test_node_id"],
                    dof_row["test_dof"],
                    dof_row["instance_name"],
                    dof_row["part_name"],
                    dof_row["fem_node_label"],
                    dof_row["fem_dof"],
                    float(direction[0]),
                    float(direction[1]),
                    float(direction[2]),
                    match_score,
                    _json_dumps(transform_payload),
                ))

        conn.commit()
        return {
            "project_id": project_id,
            "node_match_count": len(filtered_matches),
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
                raise ValueError("no test modes or dof matches found to build response catalog")

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
        raise ValueError("modes payload is empty")
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
        source_file_path = os.path.abspath(file_path) if file_path else None
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
                project_id,
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

        conn.commit()
        return {
            "project_id": project_id,
            "load_case_nos": sorted(case_nos),
            "row_count": row_count,
            "source_file_path": source_file_path,
            "rows_preview": preview,
        }
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
    if not test_pairs:
        raise ValueError("test static results not found")

    cursor.execute("""
        SELECT DISTINCT load_case_no
        FROM t_mt_py_fem_static_result
        WHERE pid = %s
        ORDER BY load_case_no
    """, (project_id,))
    fem_cases = [int(row["load_case_no"]) for row in cursor.fetchall()]
    if not fem_cases:
        raise ValueError("fem static results not found")

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
        label = int(point_id)
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


def _build_static_analysis_error_rows(
    *,
    aligned_rows,
    component_names,
    load_case_no: int,
    result_no: int,
    value_prefix: str,
) -> List[dict]:
    rows: List[dict] = []
    for test_row, fem_row, _match in aligned_rows:
        point_no = str(test_row["point"])
        node_no = _analysis_error_node_no(fem_row)
        for component_name in component_names:
            test_col, fem_col = STATIC_COMPONENT_MAP[component_name]
            point_value = test_row.get(test_col)
            node_value = fem_row.get(fem_col)
            if point_value is None or node_value is None:
                continue
            node_value = float(node_value)
            point_value = float(point_value)
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
                    "sensor_type_id": None,
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
            raise ValueError("test static results not found")

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
        cursor.execute("""
            SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json
            FROM t_mt_py_test_static_result
            WHERE pid = %s AND load_case_no = %s AND result_no = %s
            ORDER BY point
        """, (project_id, chosen_load_case_no, chosen_result_no))
        test_rows_raw = cursor.fetchall()
        if not test_rows_raw:
            raise ValueError("selected test static result rows not found")
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
            raise ValueError("no aligned static rows found between test and updated fem results")

        error_rows = _build_static_analysis_error_rows(
            aligned_rows=aligned_rows,
            component_names=component_names,
            load_case_no=chosen_load_case_no,
            result_no=chosen_result_no,
            value_prefix="updated",
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
        chosen_load_case_no, chosen_result_no = _resolve_static_case_selection(
            cursor,
            project_id,
            load_case_no=load_case_no,
            result_no=result_no,
        )
        component_names = _resolve_static_components(
            components=components,
            include_rotations=include_rotations,
        )

        cursor.execute("""
            SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json
            FROM t_mt_py_test_static_result
            WHERE pid = %s AND load_case_no = %s AND result_no = %s
            ORDER BY point
        """, (project_id, chosen_load_case_no, chosen_result_no))
        test_rows_raw = cursor.fetchall()
        if not test_rows_raw:
            raise ValueError("selected test static result rows not found")
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
            raise ValueError("selected fem static result rows not found")

        cursor.execute("""
            SELECT test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id
        """, (project_id,))
        node_matches = cursor.fetchall()

        aligned_rows = _build_static_alignment(test_rows, fem_rows, node_matches)
        if not aligned_rows:
            raise ValueError("no aligned static rows found between test and fem results")

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
                        "sensor_type_id": None,
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
            raise ValueError("not enough aligned static values to compute dac/dsf")

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


def evaluate_static_correlation(
    project_id,
    load_case_no=None,
    result_no=None,
    components=None,
    include_rotations=False,
):
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
        raise ValueError("vector energy is zero")

    cross = np.vdot(test_vec, fem_vec)
    scale = np.vdot(fem_vec, test_vec) / np.vdot(test_vec, test_vec)
    residual = test_vec - scale * fem_vec
    return {
        "dac": float(100.0 * (abs(cross) ** 2) / (test_energy * fem_energy)),
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
        cursor.execute("""
            SELECT test_node_id, test_dof, instance_name, fem_node_label, fem_dof,
                   direction_x, direction_y, direction_z
            FROM t_mt_py_fem_dof_match
            WHERE pid = %s
            ORDER BY test_node_id, test_dof
        """, (project_id,))
        dof_matches = cursor.fetchall()
        if not dof_matches:
            raise ValueError("dof matches not found, run /match/dofs first")

        test_modes = _load_test_mode_vectors(cursor, project_id)
        if not test_modes:
            raise ValueError("test modal shapes not found")

        test_freqs = _load_test_modal_frequencies(cursor, project_id)
        fem_modes, fem_freqs = _load_fem_mode_vectors(cursor, project_id)
        if not fem_modes:
            raise ValueError("fem modal results not found, run /import/fem/modal first")

        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_modal_correlation
        (pid, test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, freq_test, freq_fem, freq_error_ratio, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            dof_pair_count = VALUES(dof_pair_count),
            dac = VALUES(dac),
            dsf = VALUES(dsf),
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
                    item["freq_test"],
                    item["freq_fem"],
                    item["freq_error_ratio"],
                    _json_dumps(item["extra_json"]),
                ))

        if not results:
            raise ValueError("no valid modal correlation pairs were produced")

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
            SELECT test_mode_no, fem_mode_no, dof_pair_count, dac, dsf,
                   freq_test, freq_fem, freq_error_ratio, extra_json, created_at
            FROM t_mt_py_fem_modal_correlation
            WHERE pid = %s
            ORDER BY test_mode_no, dac DESC, fem_mode_no
        """, (project_id,))
        return {
            "project_id": project_id,
            "correlations": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()
