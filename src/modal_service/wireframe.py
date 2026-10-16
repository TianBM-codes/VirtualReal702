"""Lightweight FE display geometry using experimental trace-line topology."""
import hashlib
import json
import os
from functools import lru_cache

import h5py
import numpy as np

from db import get_connection
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.core.state import registry
from src.l3.infra.manifest_repo import ManifestRepo
from . import service


def _signature(path):
    stat = os.stat(path)
    return (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4)
def _labels(signature, dataset):
    with h5py.File(signature[0], "r") as f:
        labels = np.asarray(f[dataset][:], dtype=np.int64)
    order = np.argsort(labels)
    return labels[order], order


def _rows(path, dataset, wanted):
    labels, order = _labels(_signature(path), dataset)
    positions = np.searchsorted(labels, wanted)
    valid = positions < len(labels)
    valid[valid] &= labels[positions[valid]] == np.asarray(wanted)[valid]
    rows = np.full(len(wanted), -1, dtype=np.int64)
    rows[valid] = order[positions[valid]]
    return rows


def _take(dataset, rows, frame=None):
    unique, inverse = np.unique(rows, return_inverse=True)
    values = dataset[unique, :3] if frame is None else dataset[frame, unique, :3]
    return np.asarray(values)[inverse]


@lru_cache(maxsize=2)
def _nearest_tree(signature):
    from scipy.spatial import cKDTree
    with h5py.File(signature[0], "r") as f:
        key = "nodes/coords_global" if "nodes/coords_global" in f else "nodes/coords"
        coords = f[key][:]
        labels = f["nodes/labels"][:]
    return cKDTree(coords), labels


def _sources(project_id):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid=%s ORDER BY nid", (project_id,))
        nodes = cursor.fetchall()
        cursor.execute("SELECT point1, point2 FROM t_mt_py_test_element WHERE pid=%s ORDER BY element_no", (project_id,))
        edges = cursor.fetchall()
        cursor.execute("SELECT test_node_id, instance_name, fem_node_label, transform_json FROM t_mt_py_fem_node_match WHERE pid=%s ORDER BY test_node_id", (project_id,))
        matches = cursor.fetchall()
        return nodes, edges, matches
    finally:
        cursor.close()
        connection.close()


def _build_geometry(nodes, edges, matches, paths, policy):
    from services.model_update.analysis.fem_catalog_service import _apply_transform

    transforms = [json.loads(m["transform_json"]) if isinstance(m.get("transform_json"), str)
                  else m.get("transform_json") or {} for m in matches]
    if any(t != transforms[0] for t in transforms[1:]):
        raise ValidationError("Node matches contain inconsistent registration transforms")
    transform = transforms[0] if transforms else {}
    test_coords = _apply_transform(np.asarray([[n["x"], n["y"], n["z"]] for n in nodes], dtype=float).reshape(-1, 3),
                                   translation=transform.get("translation"), rotation=transform.get("rotation"))
    lookup = {str(m["test_node_id"]): m for m in matches}
    mappings, coords, missing = [], [], []
    # Group reads by instance so only selected coordinate rows are read from HDF5.
    selected = {}
    for i, node in enumerate(nodes):
        match = lookup.get(str(node["nid"]))
        if match and match["instance_name"] in paths:
            selected.setdefault(match["instance_name"], []).append((i, int(match["fem_node_label"])))
    resolved = {}
    for instance, entries in selected.items():
        path = paths[instance]
        rows = _rows(path, "nodes/labels", [e[1] for e in entries])
        valid = rows >= 0
        with h5py.File(path, "r") as f:
            key = "nodes/coords_global" if "nodes/coords_global" in f else "nodes/coords"
            values = _take(f[key], rows[valid]) if valid.any() else []
        for entry, row, coord in zip(np.asarray(entries)[valid], rows[valid], values):
            resolved[int(entry[0])] = (instance, int(entry[1]), int(row), coord, "matched", None)
    for i, node in enumerate(nodes):
        item = resolved.get(i)
        if item is None and policy == "nearest":
            best = None
            for instance, path in paths.items():
                tree, labels = _nearest_tree(_signature(path))
                distance, row = tree.query(test_coords[i])
                if np.isfinite(distance) and (best is None or distance < best[5]):
                    best = (instance, int(labels[row]), int(row), tree.data[row], "nearest", float(distance))
            item = best
        if item is None:
            missing.append(int(node["nid"]))
            continue
        instance, label, row, coord, source, distance = item
        mappings.append({"test_node_id": int(node["nid"]), "instance": instance,
                         "fem_node_label": label, "node_row": row, "source": source, "distance": distance})
        coords.extend(np.asarray(coord).tolist())
    index_by_id = {m["test_node_id"]: i for i, m in enumerate(mappings)}
    indices, skipped = [], 0
    for edge in edges:
        a, b = index_by_id.get(int(edge["point1"])), index_by_id.get(int(edge["point2"]))
        if a is None or b is None:
            skipped += 1
        else:
            indices.extend([a, b])
    duplicates = {}
    for m in mappings:
        duplicates.setdefault((m["instance"], m["fem_node_label"]), []).append(m["test_node_id"])
    result = {"ids": [m["test_node_id"] for m in mappings], "originPos": coords,
              "elementsIndex": indices, "primitive": "lines", "mapping": mappings,
              "registration": transform, "unmatched_policy": policy,
              "diagnostics": {"unmatched_node_ids": missing, "skipped_edges": skipped,
                              "nearest_count": sum(m["source"] == "nearest" for m in mappings),
                              "duplicate_mappings": [{"instance": k[0], "fem_node_label": k[1], "test_node_ids": v}
                                                     for k, v in duplicates.items() if len(v) > 1]}}
    version_data = [result, [(name, _signature(path)) for name, path in sorted(paths.items())]]
    result["geometry_version"] = hashlib.sha256(json.dumps(version_data, sort_keys=True).encode()).hexdigest()
    return result


def _context(project_id):
    idx = registry.get(str(project_id))
    if idx is None:
        raise NotFoundError("FEM project not loaded", {"project_id": project_id})
    manifest = ManifestRepo(idx.workspace)
    # L2 retains all node rows and applies assembly transforms to coordinates.
    paths = {row["instance_name"]: os.path.join(idx.workspace, "l2", "geometry", row["instance_name"] + "_surface.h5")
             for row in manifest.list_instances()}
    paths = {name: path for name, path in paths.items() if path and os.path.exists(path)}
    if not paths:
        raise NotFoundError("FEM geometry not available")
    return idx, paths


def get_geometry(project_id, unmatched_policy="disconnect"):
    if unmatched_policy not in {"disconnect", "nearest"}:
        raise ValidationError("Unknown unmatched_policy")
    _, paths = _context(project_id)
    return _build_geometry(*_sources(project_id), paths, unmatched_policy)


def _read_selected_displacements(workspace, mappings, step, frame, result_group):
    from src.l3.services.result_service import _manifest_result_h5_path
    path = _manifest_result_h5_path(workspace, step, "U", result_group)
    values = np.zeros((len(mappings), 3), dtype=float)
    missing = []
    with h5py.File(path, "r") as f:
        for instance in sorted({m["instance"] for m in mappings}):
            positions = [i for i, m in enumerate(mappings) if m["instance"] == instance]
            base = f"NODAL/{instance}"
            if f"{base}/data" not in f:
                missing.extend(mappings[i]["test_node_id"] for i in positions)
                continue
            ds = f[f"{base}/data"]
            if frame < 0 or frame >= ds.shape[0] or ds.ndim != 3 or ds.shape[2] < 3:
                raise ValidationError("Invalid displacement frame or vector shape")
            rows = (_rows(path, f"{base}/labels", [mappings[i]["fem_node_label"] for i in positions])
                    if f"{base}/labels" in f else np.asarray([mappings[i]["node_row"] for i in positions]))
            valid = (rows >= 0) & (rows < ds.shape[1])
            if valid.any():
                values[np.asarray(positions)[valid]] = _take(ds, rows[valid], frame)
            missing.extend(mappings[i]["test_node_id"] for i, good in zip(positions, valid) if not good)
    return values, missing


def get_animation(project_id, order, unmatched_policy="disconnect", geometry_version=None, **kwargs):
    geometry = get_geometry(project_id, unmatched_policy)
    if geometry_version and geometry_version != geometry["geometry_version"]:
        raise ValidationError("Wireframe geometry changed; reload geometry")
    idx, _ = _context(project_id)
    sync = service.get_sync_animation(project_id, order, include_frames=False, **kwargs)
    fem = sync["fem"]
    values = np.zeros((len(geometry["ids"]), 3))
    missing = []
    if order != 0 and fem["available"]:
        values, missing = _read_selected_displacements(idx.workspace, geometry["mapping"],
                                                      fem["step"], fem["frame"], fem["result_group"])
    rotation = geometry["registration"].get("rotation")
    if sync["test"]["ids"]:
        from services.model_update.analysis.fem_catalog_service import _apply_transform
        test = sync["test"]
        test["originPos"] = _apply_transform(np.asarray(test["originPos"]).reshape(-1, 3),
                                             geometry["registration"].get("translation"), rotation).reshape(-1).tolist()
    # Return basis vectors instead of precomputed frames; phase/scale remain the existing rules.
    test = sync["test"]
    basis = service.get_animation(project_id, order, kwargs.get("flip", False))
    if rotation:
        from services.model_update.analysis.fem_catalog_service import _rotation_to_matrix
        matrix = _rotation_to_matrix(rotation)
        basis = {key: (np.asarray(value).reshape(-1, 3) @ matrix.T).reshape(-1).tolist()
                 for key, value in basis.items()}
    test.update(basis)
    test.pop("frames", None)
    fem.update({"ids": geometry["ids"], "real": values.reshape(-1).tolist(),
                "imag": [0.0] * values.size, "missing_result_node_ids": missing})
    return {"geometry_version": geometry["geometry_version"], "test": test, "fem": fem, "sync": sync["sync"]}
