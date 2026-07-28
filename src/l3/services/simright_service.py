"""
Simright DATA-API adapter.

Implements the dispatch logic for:
  POST /applications/3dlite/api/v1/query
  body: {"name": "<handler>", "args": {...}}

Each handler returns a plain Python dict that becomes the "data" field
in the standard simright response envelope:
  {"code": 200, "data": <dict>, "message": ""}

filename → odb_id resolution:
  Try exact match → strip leading "/" → basename without extension.
  Callers use resolve_odb_id() before querying.

stateId convention (simright is 1-based, our frame_idx is 0-based):
  frame_idx = stateId - 1
  stateId = -1 means last frame → frame_idx = num_frames - 1

varName parsing:
  "S"             → field="S",  component=None
  "S.EL.VonMises" → field="S",  component="VonMises"
  "D.N.X"         → field="D",  component="X"
  component string is mapped to a column index when reading HDF5.
"""
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np

from ..core.config import settings
from ..core.errors import NotFoundError, ValidationError
from ..core.state import OdbRegistry
from ..infra.manifest_repo import ManifestRepo
from ..infra.registry_repo import RegistryRepo
from src.l1.manifest_schema import canon_instance

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component-name → column-index lookup
# ---------------------------------------------------------------------------
_COMP_ALIASES: Dict[str, int] = {
    # Displacement / position
    "X": 0, "Y": 1, "Z": 2,
    "U1": 0, "U2": 1, "U3": 2,
    # Stress / strain components (Abaqus ordering: 11,22,33,12,13,23)
    "11": 0, "22": 1, "33": 2, "12": 3, "13": 4, "23": 5,
    "S11": 0, "S22": 1, "S33": 2, "S12": 3, "S13": 4, "S23": 5,
    "E11": 0, "E22": 1, "E33": 2, "E12": 3, "E13": 4, "E23": 5,
    # Magnitude / invariants → compute separately
    "Magnitude": -1, "VonMises": -1, "Mises": -1, "USUM": -1,
}


def _comp_to_idx(comp: Optional[str]) -> Optional[int]:
    """Return column index or None (=magnitude/sum). Returns -1 for magnitude."""
    if comp is None:
        return 0
    return _COMP_ALIASES.get(comp, 0)


def _is_magnitude(comp: Optional[str]) -> bool:
    if comp is None:
        return False
    return _COMP_ALIASES.get(comp, 0) == -1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result_h5_path(workspace: str, step: str, field: str,
                    result_group: str = None) -> str:
    def safe(s):
        return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    fname = f"{safe(step)}__{safe(field)}.h5"
    if result_group:
        return os.path.join(workspace, "l1", "results", safe(result_group), fname)
    return os.path.join(workspace, "l1", "results", fname)


def _geom_h5_path(workspace: str, instance_name: str) -> str:
    """Resolve L1 geometry path via manifest; fall back to safe-name construction."""
    instance_name = canon_instance(instance_name)
    try:
        import sqlite3
        with sqlite3.connect(os.path.join(workspace, "manifest.db")) as conn:
            row = conn.execute(
                "SELECT geom_path FROM instances WHERE instance_name=?", (instance_name,)
            ).fetchone()
            if row and row[0]:
                return os.path.join(workspace, row[0])
    except Exception:
        pass
    def _safe(s): return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    return os.path.join(workspace, "l1", "geometry", f"{_safe(instance_name)}.h5")


def parse_var_name(var_name: str) -> Tuple[str, Optional[str]]:
    """Split "S.EL.VonMises" → ("S", "VonMises"). "U" → ("U", None)."""
    if not var_name:
        return "", None
    parts = var_name.split(".")
    field = parts[0]
    component = parts[-1] if len(parts) > 1 else None
    return field, component


def resolve_odb_id(filename: str, registry: OdbRegistry) -> str:
    """
    Map a simright filename to one of our known odb_ids.
    Tries: exact → strip-slash → basename-no-ext.
    Raises NotFoundError if nothing matches.

    Searches every *known* id, not just the resident ones: the registry only
    keeps a working set in RAM, so matching on registry.loaded would fail for
    any ODB that has been evicted or never accessed since startup.
    """
    candidates = [
        filename,
        filename.lstrip("/"),
        os.path.splitext(os.path.basename(filename))[0],
        os.path.basename(filename),
    ]
    known_ids = registry.known_ids()
    for c in candidates:
        if c in known_ids:
            return c
    # Fuzzy: any known odb_id that appears in filename or vice versa
    for oid in known_ids:
        if oid in filename or filename in oid:
            return oid
    raise NotFoundError(
        f"No known ODB matches filename '{filename}'",
        {"filename": filename},
    )


def _resolve_step(manifest: ManifestRepo, loadcase_name: Optional[str],
                  loadcase_index: Optional[int]) -> Optional[str]:
    """Return step_name from name or index. Returns None if not specified."""
    steps = manifest.list_steps()
    if not steps:
        return None
    if loadcase_index is not None:
        idx = int(loadcase_index)
        if 0 <= idx < len(steps):
            return steps[idx]["step_name"]
        return steps[0]["step_name"]
    if loadcase_name:
        for s in steps:
            if s["step_name"] == loadcase_name:
                return s["step_name"]
        # fallback: first step
    return steps[0]["step_name"]


def _resolve_frame_idx(manifest: ManifestRepo, step_name: str,
                       state_id: Optional[int]) -> int:
    """Convert 1-based stateId to 0-based frame_idx."""
    frames = manifest.list_frames(step_name)
    num_frames = len(frames)
    if num_frames == 0:
        return 0
    if state_id is None:
        return num_frames - 1  # default: last frame
    if state_id == -1:
        return num_frames - 1
    return max(0, min(int(state_id) - 1, num_frames - 1))


def _read_nodal_scalar(f: h5py.File, instance_name: str, frame_idx: int,
                       comp_idx: Optional[int], magnitude: bool) -> Optional[np.ndarray]:
    """
    Read NODAL data for one instance at one frame.
    Returns 1-D float32 array of length N_nodes, or None if dataset missing.
    """
    instance_name = canon_instance(instance_name)
    ds_path = f"/NODAL/{instance_name}/data"
    if ds_path not in f:
        return None
    ds = f[ds_path]
    # shape: [frames, N, ncomp] or [frames, N]
    frame_data = ds[frame_idx]  # [N, ncomp] or [N]
    if frame_data.ndim == 1 or (frame_data.ndim == 2 and frame_data.shape[1] == 1):
        arr = frame_data.ravel().astype(np.float32)
    elif magnitude:
        arr = np.linalg.norm(frame_data, axis=1).astype(np.float32)
    else:
        ci = comp_idx or 0
        if ci < frame_data.shape[1]:
            arr = frame_data[:, ci].astype(np.float32)
        else:
            arr = np.linalg.norm(frame_data, axis=1).astype(np.float32)
    return arr


def _read_nodal_timeseries(f: h5py.File, instance_name: str, node_row: int,
                           frame_indices: Optional[List[int]],
                           comp_idx: int, magnitude: bool) -> List[float]:
    """Return list of scalar values across frames for one node row."""
    instance_name = canon_instance(instance_name)
    ds_path = f"/NODAL/{instance_name}/data"
    if ds_path not in f:
        return []
    ds = f[ds_path]
    num_frames = ds.shape[0]
    if frame_indices:
        idxs = [i for i in frame_indices if 0 <= i < num_frames]
    else:
        idxs = list(range(num_frames))
    results = []
    for fi in idxs:
        frame_data = ds[fi, node_row]  # scalar or [ncomp]
        if np.isscalar(frame_data) or (hasattr(frame_data, "ndim") and frame_data.ndim == 0):
            results.append(float(frame_data))
        elif magnitude:
            results.append(float(np.linalg.norm(frame_data)))
        else:
            arr = np.asarray(frame_data)
            ci = comp_idx if comp_idx < len(arr) else 0
            results.append(float(arr[ci]))
    return results


def _find_node_in_instances(workspace: str, instances: List[dict],
                             node_label: int) -> Optional[Tuple[dict, int]]:
    """
    Search geometry HDF5 files for `node_label`.
    Returns (instance_dict, node_row) or None.
    """
    for inst in instances:
        geom_path = _geom_h5_path(workspace, inst["instance_name"])
        if not os.path.exists(geom_path):
            continue
        try:
            with h5py.File(geom_path, "r") as f:
                if "nodes/labels" not in f:
                    continue
                labels = f["nodes/labels"][:]
                pos = np.searchsorted(labels, node_label)
                if pos < len(labels) and labels[pos] == node_label:
                    return inst, int(pos)
        except Exception:
            continue
    return None


def _find_elem_in_instances(workspace: str, instances: List[dict],
                             elem_label: int) -> Optional[Tuple[dict, str, int]]:
    """
    Search geometry HDF5 files for `elem_label`.
    Returns (instance_dict, etype_str, elem_row) or None.
    """
    for inst in instances:
        geom_path = _geom_h5_path(workspace, inst["instance_name"])
        if not os.path.exists(geom_path):
            continue
        try:
            with h5py.File(geom_path, "r") as f:
                if "elements" not in f:
                    continue
                for etype in f["elements"].keys():
                    labels = f[f"elements/{etype}/labels"][:]
                    pos = np.searchsorted(labels, elem_label)
                    if pos < len(labels) and labels[pos] == elem_label:
                        return inst, etype, int(pos)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

def handle_loadcases(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Return list of steps as {id, name}."""
    steps = manifest.list_steps()
    value = [{"id": i + 1, "name": s["step_name"]} for i, s in enumerate(steps)]
    return {"value": value}


def handle_variables(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Return variable tree for a step."""
    loadcase_name = args.get("loadcaseName")
    loadcase_index = args.get("loadcaseIndex")
    step_name = _resolve_step(manifest, loadcase_name, loadcase_index)
    fields = manifest.list_result_files(step_name)

    # Build tree: {field: [field.comp1, field.comp2, ...]}
    tree: Dict[str, List[str]] = {}
    for f in fields:
        fname = f["field_name"]
        comps_raw = f.get("components") or "[]"
        try:
            comps = json.loads(comps_raw) if isinstance(comps_raw, str) else comps_raw
        except Exception:
            comps = []
        if comps:
            tree[fname] = [f"{fname}.{c}" for c in comps]
        else:
            tree[fname] = [fname]

    variables = [{k: v} for k, v in tree.items()]
    return {"variables": variables}


def handle_assemble(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Return assembly tree: instances as parts, all under root assembly."""
    instances = manifest.list_instances()
    node_sets = manifest.list_node_sets()
    elem_sets = manifest.list_element_sets()

    parts = []
    for i, inst in enumerate(instances):
        parts.append({
            "id": i,
            "oriId": inst["rowid"],
            "name": inst["instance_name"],
            "partName": inst.get("part_name", ""),
        })

    # Combine sets (type 1=node, 2=element)
    seen_sets = {}
    set_list = []
    set_counter = len(parts)
    for ns in node_sets:
        key = ns["set_name"]
        if key not in seen_sets:
            seen_sets[key] = {"id": set_counter, "name": key, "type": 1,
                              "instance": ns.get("instance_name", "")}
            set_list.append(seen_sets[key])
            set_counter += 1
    for es in elem_sets:
        key = f"__elem__{es['set_name']}"
        if key not in seen_sets:
            seen_sets[key] = {"id": set_counter, "name": es["set_name"], "type": 2,
                              "instance": es.get("instance_name", "")}
            set_list.append(seen_sets[key])
            set_counter += 1

    return {
        "root": 0,
        "assms": [{"id": 0, "name": "", "parts": list(range(len(parts)))}],
        "parts": parts,
        "sets": set_list,
    }


def handle_extreme_value(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Find global min/max of a result field across specified frames."""
    var_name = args.get("varName", "")
    rtype = args.get("rtype", "max").lower()
    loadcase_name = args.get("loadcaseName")
    loadcase_index = args.get("loadcaseIndex")
    state_ids = args.get("stateIds", [])  # 1-based list or []

    field, component = parse_var_name(var_name)
    comp_idx = _comp_to_idx(component)
    magnitude = _is_magnitude(component)

    step_name = _resolve_step(manifest, loadcase_name, loadcase_index)
    if not step_name:
        return {"value": None, "nodeId": None}

    frames = manifest.list_frames(step_name)
    num_frames = len(frames)
    if not num_frames:
        return {"value": None, "nodeId": None}

    # Resolve which frame indices to scan
    if state_ids == [] or state_ids is None:
        frame_indices = list(range(num_frames))
    elif state_ids == [-1]:
        frame_indices = [num_frames - 1]
    else:
        frame_indices = [max(0, min(int(s) - 1, num_frames - 1)) for s in state_ids]

    h5_path = _result_h5_path(workspace, step_name, field)
    if not os.path.exists(h5_path):
        return {"value": None, "nodeId": None}

    instances = manifest.list_instances()
    best_val = None
    best_frame = frame_indices[0]
    best_inst_idx = 0
    best_node_row = 0

    try:
        with h5py.File(h5_path, "r") as f:
            for fi in frame_indices:
                for inst_i, inst in enumerate(instances):
                    inst_name = inst["instance_name"]
                    arr = _read_nodal_scalar(f, inst_name, fi, comp_idx, magnitude)
                    if arr is None or len(arr) == 0:
                        continue
                    if rtype == "max":
                        local_row = int(np.argmax(arr))
                        local_val = float(arr[local_row])
                        if best_val is None or local_val > best_val:
                            best_val = local_val
                            best_frame = fi
                            best_inst_idx = inst_i
                            best_node_row = local_row
                    else:
                        local_row = int(np.argmin(arr))
                        local_val = float(arr[local_row])
                        if best_val is None or local_val < best_val:
                            best_val = local_val
                            best_frame = fi
                            best_inst_idx = inst_i
                            best_node_row = local_row
    except Exception as e:
        logger.exception("extremeValue: failed to read HDF5")
        return {"value": None, "nodeId": None}

    if best_val is None:
        return {"value": None, "nodeId": None}

    # Look up node label and coordinates
    best_inst = instances[best_inst_idx]
    geom_path = _geom_h5_path(workspace, best_inst["instance_name"])
    node_label = best_node_row
    node_pos = [0.0, 0.0, 0.0]
    try:
        with h5py.File(geom_path, "r") as gf:
            if "nodes/labels" in gf:
                node_label = int(gf["nodes/labels"][best_node_row])
            if "nodes/coords" in gf:
                node_pos = gf["nodes/coords"][best_node_row].tolist()
    except Exception:
        pass

    return {
        "partId": best_inst_idx,
        "oriId": best_inst["rowid"],
        "stateId": best_frame + 1,
        "nodeId": node_label,
        "nodePos": node_pos,
        "value": best_val,
    }


def handle_node_info(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Return node positions and result values for given node IDs."""
    node_ids = args.get("nodeIds", [])
    state_ids = args.get("stateIds", [])
    var_name = args.get("varName", "")
    loadcase_name = args.get("loadcaseName")
    loadcase_index = args.get("loadcaseIndex")

    field, component = parse_var_name(var_name) if var_name else ("", None)
    comp_idx = _comp_to_idx(component)
    magnitude = _is_magnitude(component)

    step_name = _resolve_step(manifest, loadcase_name, loadcase_index)
    instances = manifest.list_instances()

    results = []
    for node_label in node_ids:
        found = _find_node_in_instances(workspace, instances, int(node_label))
        if found is None:
            results.append({"nodeId": node_label, "nodePos": None, "values": []})
            continue
        inst, node_row = found
        inst_idx = next(i for i, x in enumerate(instances)
                        if x["instance_name"] == inst["instance_name"])

        # Get coordinates
        geom_path = _geom_h5_path(workspace, inst["instance_name"])
        node_pos = [0.0, 0.0, 0.0]
        try:
            with h5py.File(geom_path, "r") as gf:
                if "nodes/coords" in gf:
                    node_pos = gf["nodes/coords"][node_row].tolist()
        except Exception:
            pass

        # Get result values per requested frame
        values = []
        if field and step_name:
            frames = manifest.list_frames(step_name)
            num_frames = len(frames)
            if state_ids:
                frame_indices = [max(0, min(int(s) - 1, num_frames - 1)) for s in state_ids]
            else:
                frame_indices = list(range(num_frames))

            h5_path = _result_h5_path(workspace, step_name, field)
            if os.path.exists(h5_path):
                try:
                    with h5py.File(h5_path, "r") as f:
                        values = _read_nodal_timeseries(
                            f, inst["instance_name"], node_row,
                            frame_indices, comp_idx, magnitude,
                        )
                except Exception:
                    logger.exception("nodeInfo: failed reading result HDF5")

        entry = {
            "partId": inst_idx,
            "oriId": inst["rowid"],
            "nodeId": node_label,
            "nodePos": node_pos,
            "values": values,
        }
        if state_ids and values:
            entry["stateId"] = state_ids[0]
        results.append(entry)

    return results


def handle_element_info(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Return element centroid positions and result values for given element IDs."""
    elem_ids = args.get("elemIds", [])
    state_ids = args.get("stateIds", [])
    var_name = args.get("varName", "")
    loadcase_name = args.get("loadcaseName")
    loadcase_index = args.get("loadcaseIndex")

    field, component = parse_var_name(var_name) if var_name else ("", None)
    comp_idx = _comp_to_idx(component)
    magnitude = _is_magnitude(component)

    step_name = _resolve_step(manifest, loadcase_name, loadcase_index)
    instances = manifest.list_instances()

    results = []
    for elem_label in elem_ids:
        found = _find_elem_in_instances(workspace, instances, int(elem_label))
        if found is None:
            results.append({"elemId": elem_label, "elemPos": None, "values": []})
            continue
        inst, etype, elem_row = found
        inst_idx = next(i for i, x in enumerate(instances)
                        if x["instance_name"] == inst["instance_name"])

        # Get element centroid from node coords
        geom_path = _geom_h5_path(workspace, inst["instance_name"])
        elem_pos = [0.0, 0.0, 0.0]
        node_labels_of_elem = []
        try:
            with h5py.File(geom_path, "r") as gf:
                conn_ds = gf[f"elements/{etype}/conn"]
                node_rows = conn_ds[elem_row]
                valid_rows = [int(r) for r in node_rows if r >= 0]
                if "nodes/coords" in gf and valid_rows:
                    coords = gf["nodes/coords"][:]
                    centroid = coords[valid_rows].mean(axis=0)
                    elem_pos = centroid.tolist()
                if "nodes/labels" in gf and valid_rows:
                    all_labels = gf["nodes/labels"][:]
                    node_labels_of_elem = [int(all_labels[r]) for r in valid_rows]
        except Exception:
            pass

        # Get result values — try INTEGRATION_POINT or ELEMENT_NODAL
        values = []
        if field and step_name:
            frames = manifest.list_frames(step_name)
            num_frames = len(frames)
            if state_ids:
                frame_indices = [max(0, min(int(s) - 1, num_frames - 1)) for s in state_ids]
            else:
                frame_indices = [num_frames - 1] if num_frames else []

            h5_path = _result_h5_path(workspace, step_name, field)
            if os.path.exists(h5_path):
                try:
                    with h5py.File(h5_path, "r") as f:
                        for fi in frame_indices:
                            for pos in ("INTEGRATION_POINT", "ELEMENT_NODAL", "NODAL"):
                                ds_path = f"/{pos}/{inst['instance_name']}"
                                if etype:
                                    ds_path += f"/{etype}/data"
                                else:
                                    ds_path += "/data"
                                if ds_path not in f:
                                    continue
                                ds = f[ds_path]
                                frame_data = ds[fi, elem_row]  # [nip, ncomp] or [ncomp]
                                arr = np.asarray(frame_data)
                                if arr.ndim == 0:
                                    values.append(float(arr))
                                elif arr.ndim == 1:
                                    if magnitude:
                                        values.append(float(np.linalg.norm(arr)))
                                    else:
                                        ci = comp_idx or 0
                                        values.append(float(arr[ci] if ci < len(arr) else arr[0]))
                                else:  # [nip, ncomp]
                                    if magnitude:
                                        values.extend(np.linalg.norm(arr, axis=1).tolist())
                                    else:
                                        ci = comp_idx or 0
                                        values.extend(arr[:, ci].tolist()
                                                       if ci < arr.shape[1]
                                                       else arr[:, 0].tolist())
                                break  # found position
                except Exception:
                    logger.exception("elementInfo: failed reading result HDF5")

        entry = {
            "partId": inst_idx,
            "oriId": inst["rowid"],
            "elemId": elem_label,
            "edx": elem_row,
            "elemPos": elem_pos,
            "nids": node_labels_of_elem,
            "values": values,
        }
        if state_ids and frame_indices:
            entry["stateId"] = state_ids[0]
        results.append(entry)

    return results


def handle_xy_curve_data1(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Return XY time-series for a list of node IDs."""
    node_ids = args.get("nodeIds", [])
    y_var_name = args.get("yVarName", "")
    x_var_name = args.get("xVarName", "var_time")
    loadcase_name = args.get("loadcaseName")
    loadcase_index = args.get("loadcaseIndex")
    state_ids = args.get("stateIds", [])

    y_field, y_component = parse_var_name(y_var_name) if y_var_name else ("", None)
    comp_idx = _comp_to_idx(y_component)
    magnitude = _is_magnitude(y_component)

    step_name = _resolve_step(manifest, loadcase_name, loadcase_index)
    if not step_name:
        return []

    frames = manifest.list_frames(step_name)
    num_frames = len(frames)
    if not num_frames:
        return []

    if state_ids:
        frame_indices = [max(0, min(int(s) - 1, num_frames - 1)) for s in state_ids]
    else:
        frame_indices = list(range(num_frames))

    # Build X axis values
    if x_var_name in ("var_time",):
        x_values = [frames[i]["frame_value"] for i in frame_indices]
    elif x_var_name in ("var_frequency", "var_mode"):
        x_values = [i + 1 for i in frame_indices]
    else:
        x_values = [frames[i]["frame_value"] for i in frame_indices]

    instances = manifest.list_instances()
    h5_path = _result_h5_path(workspace, step_name, y_field)

    results = []
    for node_label in node_ids:
        found = _find_node_in_instances(workspace, instances, int(node_label))
        if found is None:
            results.append({
                "nodeId": node_label, "data": [], "xVarName": x_var_name,
                "yVarName": y_var_name,
            })
            continue
        inst, node_row = found

        y_values = []
        if os.path.exists(h5_path):
            try:
                with h5py.File(h5_path, "r") as f:
                    y_values = _read_nodal_timeseries(
                        f, inst["instance_name"], node_row,
                        frame_indices, comp_idx, magnitude,
                    )
            except Exception:
                logger.exception("XYCurveData1: failed reading HDF5")

        # Pair [x, y] per frame
        data = [[x, y] for x, y in zip(x_values, y_values)]
        results.append({
            "nodeId": node_label,
            "data": data,
            "xVarName": x_var_name,
            "yVarName": y_var_name,
        })

    return results


def handle_freq_value(args: Dict, workspace: str, manifest: ManifestRepo) -> Dict:
    """Return modal frequencies: list of [order, frequency_hz]."""
    method = args.get("method", "order")
    range_ = args.get("range", [1, 9999])
    modal_index = args.get("modalIndex", 0)

    steps = manifest.list_steps()
    freq_steps = [s for s in steps if s.get("procedure", "") in ("FREQUENCY", "BUCKLE")]
    if not freq_steps:
        return {"value": []}
    step_name = freq_steps[min(modal_index, len(freq_steps) - 1)]["step_name"]
    frames = manifest.list_frames(step_name)

    if method == "order":
        lo, hi = int(range_[0]), int(range_[1])
        value = [
            [f["frame_idx"] + 1, f["frame_value"]]
            for f in frames
            if lo <= f["frame_idx"] + 1 <= hi
        ]
    else:  # freq range
        lo_f, hi_f = float(range_[0]), float(range_[1])
        value = [
            [f["frame_idx"] + 1, f["frame_value"]]
            for f in frames
            if lo_f <= f["frame_value"] <= hi_f
        ]

    return {"value": value}


# ---------------------------------------------------------------------------
# New handlers — batch 2
# ---------------------------------------------------------------------------

def _inst_bbox(inst: dict) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Parse bbox_min / bbox_max from an instances row. Returns (lo, hi) or None."""
    try:
        lo = np.array(json.loads(inst["bbox_min"]), dtype=np.float32)
        hi = np.array(json.loads(inst["bbox_max"]), dtype=np.float32)
        return lo, hi
    except Exception:
        return None


def _box_to_aabb(box: List[float]) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convert simright box [cx, cy, cz, w, h, d] to (bbox_min, bbox_max).
    """
    cx, cy, cz, w, h, d = float(box[0]), float(box[1]), float(box[2]), \
                           float(box[3]), float(box[4]), float(box[5])
    lo = np.array([cx - w / 2, cy - h / 2, cz - d / 2], dtype=np.float32)
    hi = np.array([cx + w / 2, cy + h / 2, cz + d / 2], dtype=np.float32)
    return lo, hi


def _aabb_overlap(lo: np.ndarray, hi: np.ndarray,
                  inst_lo: np.ndarray, inst_hi: np.ndarray) -> bool:
    return bool(np.all(inst_hi >= lo) and np.all(inst_lo <= hi))


def _octree_candidates(octree: dict, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Reuse the same traversal logic as query_service (no import to avoid cycle)."""
    node_bbox     = octree["node_bbox"]
    node_children = octree["node_children"]
    node_is_leaf  = octree["node_is_leaf"]
    face_indices  = octree["face_indices"]
    leaf_offsets  = octree["leaf_offsets"]
    parts = []
    stack = [0]
    while stack:
        nid = stack.pop()
        bb = node_bbox[nid]
        if bb[3] < lo[0] or bb[4] < lo[1] or bb[5] < lo[2]:
            continue
        if bb[0] > hi[0] or bb[1] > hi[1] or bb[2] > hi[2]:
            continue
        if node_is_leaf[nid]:
            s, e = int(leaf_offsets[nid]), int(leaf_offsets[nid + 1])
            if e > s:
                parts.append(face_indices[s:e])
        else:
            for child in node_children[nid]:
                if child != -1:
                    stack.append(int(child))
    if not parts:
        return np.zeros(0, dtype=np.int32)
    return np.concatenate(parts)


def handle_hit_entities(args: Dict, workspace: str, manifest: ManifestRepo,
                        idx) -> Any:
    """
    Detect which parts (instances) overlap or are contained by a bounding box.

    box format: [cx, cy, cz, w, h, d]  (center + dimensions)
    checkLevel:
      0 — only compare instance bounding boxes (fast, from manifest.db)
      1 — also verify individual surface vertices are inside the box
    contained: if True, require the part to be fully inside the box (only for bbox level).
    """
    box = args.get("box", [])
    if len(box) < 6:
        raise ValidationError("hitEntities requires box=[cx,cy,cz,w,h,d]", {})

    check_level = int(args.get("checkLevel", 1))
    contained   = bool(args.get("contained", False))

    lo, hi = _box_to_aabb(box)

    instances = manifest.list_instances()
    hit_part_ids  = []
    hit_ori_ids   = []

    for i, inst in enumerate(instances):
        inst_name = inst["instance_name"]

        # --- Level 0: instance AABB from manifest ---
        bbox = _inst_bbox(inst)
        if bbox is not None:
            inst_lo, inst_hi = bbox
            if contained:
                # Part must be fully inside the query box
                if not (np.all(inst_lo >= lo) and np.all(inst_hi <= hi)):
                    continue
            else:
                if not _aabb_overlap(lo, hi, inst_lo, inst_hi):
                    continue
            if check_level == 0:
                hit_part_ids.append(i)
                hit_ori_ids.append(inst["rowid"])
                continue

        # --- Level 1: vertex-level check ---
        # Prefer in-memory coords_global (L2), fall back to L1 geometry HDF5
        coords = idx.coords_global.get(inst_name) if idx else None

        if coords is None:
            geom_path = _geom_h5_path(workspace, inst_name)
            if os.path.exists(geom_path):
                try:
                    with h5py.File(geom_path, "r") as f:
                        if "nodes/coords" in f:
                            coords = f["nodes/coords"][:]
                except Exception:
                    pass

        if coords is not None and len(coords) > 0:
            mask = (np.all(coords >= lo, axis=1) & np.all(coords <= hi, axis=1))
            if mask.any():
                hit_part_ids.append(i)
                hit_ori_ids.append(inst["rowid"])
        elif bbox is not None:
            # Bbox overlapped but no vertex data — count as hit at level 1 too
            hit_part_ids.append(i)
            hit_ori_ids.append(inst["rowid"])

    if not hit_part_ids:
        return []
    return [{"partids": hit_part_ids, "oriIds": hit_ori_ids}]


def handle_measure_value(args: Dict, workspace: str, manifest: ManifestRepo,
                         idx) -> Any:
    """
    Geometric measurement between nodes.

    measureType:
      1 — distance between exactly 2 nodes
      2 — angle (degrees) at the middle node of 3 nodes (A-B-C → angle at B)
      3 — relative angle: angle at middle node compared to baseStateId frame

    entitiesIds: [{partId, nodeId}, ...]   (partId = 0-based instance index)
    stateId: 1-based frame index (for deformed coordinates)
    dispScale: displacement scale factor (default 1.0)
    """
    measure_type = int(args.get("measureType", 1))
    entities     = args.get("entitiesIds", [])
    state_id     = args.get("stateId")
    base_state   = args.get("baseStateId", 0)
    disp_scale   = float(args.get("dispScale", 1.0))

    if not entities:
        raise ValidationError("measureValue: entitiesIds is required", {})

    instances = manifest.list_instances()

    def _get_node_pos(partId: int, node_label: int,
                      frame_idx: Optional[int]) -> np.ndarray:
        """Return 3D position for a node, optionally deformed."""
        if partId < 0 or partId >= len(instances):
            raise ValidationError(f"measureValue: partId {partId} out of range", {})
        inst = instances[partId]
        inst_name = inst["instance_name"]

        # Undeformed coordinates
        geom_path = _geom_h5_path(workspace, inst_name)
        if not os.path.exists(geom_path):
            raise ValidationError(
                f"measureValue: geometry file missing for instance '{inst_name}'", {})
        with h5py.File(geom_path, "r") as f:
            labels = f["nodes/labels"][:]
            pos = np.searchsorted(labels, node_label)
            if pos >= len(labels) or labels[pos] != node_label:
                raise ValidationError(
                    f"measureValue: node {node_label} not found in instance '{inst_name}'", {})
            coords = f["nodes/coords"][pos].astype(np.float64)

        # Add displacement if frame specified
        if frame_idx is not None:
            steps = manifest.list_steps()
            if steps:
                step_name = steps[0]["step_name"]
                frames = manifest.list_frames(step_name)
                fi = max(0, min(frame_idx, len(frames) - 1))
                u_path = _result_h5_path(workspace, step_name, "U")
                if os.path.exists(u_path):
                    try:
                        with h5py.File(u_path, "r") as f:
                            ds_path = f"/NODAL/{inst_name}/data"
                            if ds_path in f:
                                u = f[ds_path][fi, pos]  # [3] or scalar
                                coords += np.asarray(u, dtype=np.float64) * disp_scale
                    except Exception:
                        pass
        return coords

    # Resolve frame index
    frame_idx: Optional[int] = None
    if state_id is not None:
        steps = manifest.list_steps()
        if steps:
            frames = manifest.list_frames(steps[0]["step_name"])
            frame_idx = max(0, min(int(state_id) - 1, len(frames) - 1))

    base_frame: Optional[int] = None
    if measure_type == 3 and base_state is not None:
        steps = manifest.list_steps()
        if steps:
            frames = manifest.list_frames(steps[0]["step_name"])
            base_frame = max(0, min(int(base_state) - 1, len(frames) - 1))

    # Gather node positions
    positions = []
    for e in entities:
        part_id   = int(e.get("partId", 0))
        node_lbl  = int(e.get("nodeId", 0))
        positions.append(_get_node_pos(part_id, node_lbl, frame_idx))

    if measure_type == 1:
        # Distance between all consecutive pairs, or all-to-first if > 2
        if len(positions) < 2:
            raise ValidationError("measureValue distance requires at least 2 nodes", {})
        distances = []
        for i in range(1, len(positions)):
            d = float(np.linalg.norm(positions[i] - positions[0]))
            distances.append(d)
        return distances[0] if len(distances) == 1 else distances

    elif measure_type == 2:
        # Angle at positions[1] (middle), A-B-C → angle at B
        if len(positions) < 3:
            raise ValidationError("measureValue angle requires 3 nodes", {})
        A, B, C = positions[0], positions[1], positions[2]
        BA = A - B
        BC = C - B
        cos_a = np.dot(BA, BC) / (np.linalg.norm(BA) * np.linalg.norm(BC) + 1e-12)
        angle = float(np.degrees(np.arccos(np.clip(cos_a, -1.0, 1.0))))
        return angle

    elif measure_type == 3:
        # Relative angle: angle at frame vs angle at base frame
        if len(positions) < 3:
            raise ValidationError("measureValue relative angle requires 3 nodes", {})
        base_positions = []
        for e in entities:
            base_positions.append(
                _get_node_pos(int(e.get("partId", 0)), int(e.get("nodeId", 0)), base_frame))
        def _angle(pts):
            A, B, C = pts
            BA = A - B; BC = C - B
            cos_a = np.dot(BA, BC) / (np.linalg.norm(BA) * np.linalg.norm(BC) + 1e-12)
            return float(np.degrees(np.arccos(np.clip(cos_a, -1.0, 1.0))))
        return _angle(positions) - _angle(base_positions)

    else:
        raise ValidationError(f"measureValue: unknown measureType {measure_type}", {})


def _find_nearest_node_global(
    workspace: str, instances: List[dict], idx,
    query_pos: np.ndarray,
) -> Optional[Tuple[dict, int, int, np.ndarray]]:
    """
    Find the nearest node to `query_pos` (global/assembly coordinates).

    Prefers in-memory coords_global (L2, global frame).
    Falls back to L1 `nodes/coords` (local frame, less accurate if instance
    has a transform, but usable when L2 is not yet ready).

    Returns (inst_dict, inst_index, node_label, node_pos) or None.
    """
    best_dist2  = np.inf
    best_inst   = None
    best_inst_i = 0
    best_label  = 0
    best_pos    = np.zeros(3, dtype=np.float32)

    for i, inst in enumerate(instances):
        inst_name = inst["instance_name"]

        # Try in-memory global coords first
        coords = idx.coords_global.get(inst_name) if idx else None

        if coords is None:
            geom_path = _geom_h5_path(workspace, inst_name)
            if not os.path.exists(geom_path):
                continue
            try:
                with h5py.File(geom_path, "r") as f:
                    if "nodes/coords" not in f:
                        continue
                    coords = f["nodes/coords"][:]
            except Exception:
                continue

        if len(coords) == 0:
            continue

        diffs = coords - query_pos
        dist2 = (diffs ** 2).sum(axis=1)
        row   = int(np.argmin(dist2))
        d2    = float(dist2[row])

        if d2 < best_dist2:
            best_dist2  = d2
            best_inst   = inst
            best_inst_i = i
            best_pos    = coords[row].astype(np.float32)
            # Look up node label
            geom_path = _geom_h5_path(workspace, inst_name)
            if os.path.exists(geom_path):
                try:
                    with h5py.File(geom_path, "r") as f:
                        if "nodes/labels" in f:
                            best_label = int(f["nodes/labels"][row])
                        else:
                            best_label = row
                except Exception:
                    best_label = row
            else:
                best_label = int(best_pos[0])  # fallback, shouldn't happen

    if best_inst is None:
        return None
    return best_inst, best_inst_i, best_label, best_pos


def handle_node_id(args: Dict, workspace: str, manifest: ManifestRepo, idx) -> Any:
    """
    Find the nearest loaded node to each queried position.

    nodePosList: [[x, y, z], ...]
    Returns: [{"type": 65792, "stateId": stateId, "nodeId": label, "nodePos": [x,y,z]}, ...]
    """
    node_pos_list = args.get("nodePosList", [])
    state_id      = args.get("stateId")

    if not node_pos_list:
        raise ValidationError("nodeId: nodePosList is required", {})

    instances = manifest.list_instances()
    results   = []

    for qpos_raw in node_pos_list:
        qpos   = np.array(qpos_raw, dtype=np.float32)
        found  = _find_nearest_node_global(workspace, instances, idx, qpos)
        if found is None:
            results.append({"nodeId": None, "nodePos": None})
            continue
        inst, inst_i, label, node_pos = found
        entry = {
            "type":    65792,
            "nodeId":  label,
            "nodePos": node_pos.tolist(),
            "partId":  inst_i,
            "oriId":   inst["rowid"],
        }
        if state_id is not None:
            entry["stateId"] = int(state_id)
        results.append(entry)

    return results


def handle_probe_group_by_pos(args: Dict, workspace: str,
                              manifest: ManifestRepo, idx) -> Any:
    """
    Create probe groups for a list of positions.

    Finds the nearest node to each position, reads its result value at
    the requested frame, and packages the results in the simright probe format.
    """
    node_pos_list = args.get("nodePosList", [])
    name_prefix   = args.get("namePrefix", "probe")
    loadcase_name = args.get("loadcaseName")
    loadcase_index = args.get("loadcaseIndex")
    state_id      = args.get("stateId")
    var_name      = args.get("varName", "")

    if not node_pos_list:
        raise ValidationError("probeGroupByPos: nodePosList is required", {})

    field, component = parse_var_name(var_name) if var_name else ("", None)
    comp_idx  = _comp_to_idx(component)
    magnitude = _is_magnitude(component)

    step_name = _resolve_step(manifest, loadcase_name, loadcase_index)
    instances = manifest.list_instances()

    frames     = manifest.list_frames(step_name) if step_name else []
    num_frames = len(frames)
    frame_idx  = _resolve_frame_idx(manifest, step_name, state_id) if step_name else 0
    actual_state_id = frame_idx + 1

    group_id   = str(uuid.uuid4())
    group_name = f"{name_prefix}_组1" if name_prefix else "探针组1"
    probes     = []

    for k, qpos_raw in enumerate(node_pos_list, start=1):
        qpos  = np.array(qpos_raw, dtype=np.float32)
        found = _find_nearest_node_global(workspace, instances, idx, qpos)
        if found is None:
            continue
        inst, inst_i, label, node_pos = found

        # Get undeformed position (base)
        node_pos_base = node_pos.tolist()

        # Find node_row for result lookup
        geom_path = _geom_h5_path(workspace, inst["instance_name"])
        node_row  = 0
        elem_ids  = []
        try:
            with h5py.File(geom_path, "r") as f:
                if "nodes/labels" in f:
                    labels   = f["nodes/labels"][:]
                    node_row = int(np.searchsorted(labels, label))
                if "node_to_elements" in f:
                    g = f["node_to_elements"]
                    s = int(g["offsets"][node_row])
                    e = int(g["offsets"][node_row + 1])
                    if e > s:
                        elem_ids = [int(x) for x in g["elem_label_data"][s:e]]
        except Exception:
            pass

        # Read result value
        values   = []
        val_str  = ""
        if field and step_name:
            h5_path = _result_h5_path(workspace, step_name, field)
            if os.path.exists(h5_path):
                try:
                    with h5py.File(h5_path, "r") as f:
                        v = _read_nodal_timeseries(
                            f, inst["instance_name"], node_row,
                            [frame_idx], comp_idx, magnitude,
                        )
                        values = v
                except Exception:
                    pass
        scalar = values[0] if values else 0.0
        val_str = f"{scalar:.3e}"

        probe_name = f"{name_prefix}_{k}"
        probes.append({
            "partId":      inst_i,
            "oriId":       inst["rowid"],
            "stateId":     actual_state_id,
            "nodeId":      label,
            "ndx":         node_row,
            "pndx":        node_row,
            "vdx":         node_row,
            "nodePos":     node_pos.tolist(),
            "nodePosBase": node_pos_base,
            "elemIds":     elem_ids,
            "values":      values,
            "id":          f"{inst_i}_{label}_{node_row}",
            "type":        "node",
            "calcType":    "node",
            "kind":        "user",
            "name":        probe_name,
            "displayName": probe_name,
            "visible":     True,
            "selected":    False,
            "groupId":     group_id,
            "groupInfo": {
                "id":       group_id,
                "name":     group_name,
                "index":    1,
                "selected": False,
            },
            "value": val_str,
        })

    return {
        "sprobes": {
            "probes": probes,
            "groups": [{"id": group_id, "name": group_name, "index": 1, "selected": False}],
        }
    }


def handle_nearest_face(
    args: Dict,
    workspace: str,
    manifest: ManifestRepo,
    odb_id: str,
    registry: OdbRegistry,
) -> Dict:
    """
    Find the surface triangle face closest to an arbitrary point in 3-D space.

    args
    ----
    filename : str    — model identifier (resolved to odb_id by dispatch)
    pos      : [x, y, z] float — query point in global coordinates
    instance : str (optional) — limit search to this instance; defaults to
               the first render-ready instance if omitted

    Returns
    -------
    {
        "instance":      str,
        "renderFaceIdx": int,
        "elemLabel":     int | null,
        "elemType":      str | null,
        "normal":        [nx, ny, nz],   # unit outward normal
        "closestPoint":  [cx, cy, cz],   # closest point ON the face
        "distance":      float           # distance from pos to closestPoint
    }
    """
    from .query_service import nearest_face as _nearest_face  # avoid top-level circular import

    pos = args.get("pos")
    if pos is None or len(pos) != 3:
        raise ValidationError(
            "args.pos must be a list of 3 floats [x, y, z]",
            {"pos": pos},
        )

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not loaded", {"odb_id": odb_id})

    # Resolve instance: use provided name, or pick the first available instance
    instance = args.get("instance", "")
    if not instance:
        instances = list((idx.coords_global or {}).keys())
        if not instances:
            raise NotFoundError(
                f"ODB '{odb_id}' has no render-ready geometry",
                {"odb_id": odb_id},
            )
        instance = instances[0]

    result = _nearest_face(registry, odb_id, instance, [float(v) for v in pos])

    return {
        "instance":      result.instance,
        "renderFaceIdx": result.render_face_idx,
        "elemLabel":     result.elem_label,
        "elemType":      result.elem_type,
        "normal":        result.normal,
        "closestPoint":  result.closest_point,
        "distance":      result.distance,
    }


def handle_delete_model_file(args: Dict, workspace: str, manifest: ManifestRepo,
                             odb_id: str, registry: OdbRegistry) -> Any:
    """
    Soft-delete an ODB: unload from memory and remove from registry.db.
    Workspace files on disk are NOT deleted (use DELETE /api/jobs/{id}?hard=true
    for that).  Mirrors the simright 'deleteModelFile' semantics (remove RDB/VDB).
    """
    repo = RegistryRepo(settings.registry_db_path)
    row  = repo.get_job(odb_id)

    if row is not None:
        if row.get("status") in ("l1_running", "l2_running"):
            raise ValidationError(
                f"Cannot delete ODB '{odb_id}' while it is currently processing", {})
        registry.unload(odb_id)
        repo.delete_job(odb_id)
    else:
        # Not in registry.db (e.g. loaded via dev-mode env var) — just unload
        registry.unload(odb_id)

    return "delete success"


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "loadcases":        handle_loadcases,
    "variables":        handle_variables,
    "assemble":         handle_assemble,
    "extremeValue":     handle_extreme_value,
    "nodeInfo":         handle_node_info,
    "elementInfo":      handle_element_info,
    "XYCurveData1":     handle_xy_curve_data1,
    "freqValue":        handle_freq_value,
    # Batch 2
    "hitEntities":      handle_hit_entities,
    "measureValue":     handle_measure_value,
    "nodeId":           handle_node_id,
    "probeGroupByPos":  handle_probe_group_by_pos,
    "deleteModelFile":  handle_delete_model_file,
    # Batch 3
    "nearestFace":      handle_nearest_face,
}


# Handlers that need the ModelIndex (octree / coords_global) passed in
_IDX_HANDLERS = {
    "hitEntities", "measureValue", "nodeId", "probeGroupByPos",
}
# Handlers that also need odb_id + registry
_REGISTRY_HANDLERS = {"deleteModelFile", "nearestFace"}


def dispatch(name: str, args: Dict, registry: OdbRegistry) -> Any:
    """
    Main entry point.  Resolves odb_id from args["filename"], then
    calls the matching handler.  Returns the "data" value for the response.
    """
    filename = args.get("filename", "")
    if not filename:
        raise ValidationError("args.filename is required", {"name": name})

    odb_id = resolve_odb_id(filename, registry)
    idx    = registry.get(odb_id)
    workspace = idx.workspace

    manifest = ManifestRepo(workspace)

    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValidationError(
            f"Unknown query name '{name}'",
            {"name": name, "supported": sorted(_HANDLERS.keys())},
        )

    if name in _REGISTRY_HANDLERS:
        return handler(args, workspace, manifest, odb_id, registry)
    if name in _IDX_HANDLERS:
        return handler(args, workspace, manifest, idx)
    return handler(args, workspace, manifest)
