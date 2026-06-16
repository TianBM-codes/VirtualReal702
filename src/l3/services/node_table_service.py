"""
Node table query service.

Provides two operations:
  get_instance_fields  — discover which fields/components are available
                         for a given (odb_id, instance, step) combination.
  get_node_table       — batch-query result values for a set of node labels
                         and return a [N, M] float32 matrix (L3BE sections).

Only NODAL position is supported.  Missing nodes → NaN.  Missing frames
(field absent in that frame in the original ODB) → zeros (L1 limitation;
see docs/l3/L3-Node-Field-Table-Requirement.md §4.4).
"""
import json
import os
from typing import Dict, List, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, ValidationError
from ..core.state import OdbRegistry
from ..infra.manifest_repo import ManifestRepo
from src.l1.manifest_schema import canon_instance


# ── path helpers (mirrors raw_result_service convention) ───────────────────────

def _result_h5_path(workspace: str, step: str, field: str,
                    result_group: str = None) -> str:
    def safe(s):
        return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    fname = f"{safe(step)}__{safe(field)}.h5"
    if result_group:
        return os.path.join(workspace, "l1", "results", safe(result_group), fname)
    return os.path.join(workspace, "l1", "results", fname)


# Synthetic invariant suffixes computed from stress components via numpy formulas.
# MISES is kept (Abaqus-consistent); the rest are hidden until verified.
_HIDDEN_INV_SUFFIXES = ("_PRESS", "_INV3", "_MAX_PRINCIPAL", "_MID_PRINCIPAL", "_MIN_PRINCIPAL")


# ── public API ─────────────────────────────────────────────────────────────────

def get_instance_fields(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    result_group: str = None,
) -> List[dict]:
    """
    Return the fields available for (instance, step) in the given ODB.

    Each entry:
        {"name": str, "positions": [str, ...], "components": [str, ...]}

    positions reflects what is actually stored for this instance (from
    result_blocks), not the step-global aggregate in result_files.
    components comes from result_files (same across instances for a field).
    Empty components list means scalar field.
    """
    instance = canon_instance(instance)
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    repo = ManifestRepo(idx.workspace)

    if repo.get_instance_info(instance) is None:
        raise NotFoundError(
            f"Instance '{instance}' not found in ODB '{odb_id}'",
            {"instance": instance},
        )
    if repo.get_step_info(step, result_group) is None:
        raise NotFoundError(
            f"Step '{step}' not found in ODB '{odb_id}'",
            {"step": step},
        )

    rows = repo.get_fields_by_instance(step, instance, result_group)

    result = []
    for row in rows:
        fname = row["field_name"]
        if any(fname.endswith(suf) for suf in _HIDDEN_INV_SUFFIXES):
            continue
        positions_raw = row["positions"] or ""
        positions = [p.strip() for p in positions_raw.split(",") if p.strip()]
        try:
            components = json.loads(row["components"] or "[]")
        except Exception:
            components = []
        result.append({
            "name":       fname,
            "positions":  positions,
            "components": components,
        })
    return result


def get_node_table(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    node_labels: List[int],
    items: List[dict],
    result_group: str = None,
) -> Tuple[List[Tuple[str, np.ndarray]], List[dict]]:
    """
    Batch-query NODAL result values for the given node labels.

    Parameters
    ----------
    items : list of {"field": str, "component": str}
        component="" means scalar field (single-component).

    Returns
    -------
    sections : [("node_labels", [N] int32), ("values", [N, M] float32)]
        Ready for l3be_build().  NaN for nodes not present in the instance.
    columns  : [{"key": str, "field": str, "component": str}, ...]
        Column definitions in the same order as items / the M axis of values.
    """
    instance = canon_instance(instance)
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )
    if not node_labels:
        raise ValidationError("node_labels must not be empty")
    if not items:
        raise ValidationError("items must not be empty")

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    repo = ManifestRepo(idx.workspace)

    if repo.get_instance_info(instance) is None:
        raise NotFoundError(
            f"Instance '{instance}' not found in ODB '{odb_id}'",
            {"instance": instance},
        )
    if repo.get_step_info(step, result_group) is None:
        raise NotFoundError(
            f"Step '{step}' not found in ODB '{odb_id}'",
            {"step": step},
        )

    # ── validate every item and cache field metadata ───────────────────────────
    # field_meta: field_name -> {"components": [...]}
    field_meta: Dict[str, dict] = {}
    for item in items:
        field = item["field"]
        component = item["component"]

        if field not in field_meta:
            if not repo.has_nodal_block(step, field, instance, result_group):
                raise ValidationError(
                    f"Field '{field}' does not have NODAL position "
                    f"for instance '{instance}' in step '{step}'",
                    {"field": field, "instance": instance, "step": step},
                )
            rf = repo.get_result_file(step, field, result_group)
            try:
                components = json.loads(rf["components"]) if rf and rf["components"] else []
            except Exception:
                components = []
            field_meta[field] = {"components": components}

        components = field_meta[field]["components"]
        if component != "" and component not in components:
            raise ValidationError(
                f"Component '{component}' not found in field '{field}' "
                f"(available: {components})",
                {"field": field, "component": component},
            )

    # ── allocate output matrix ─────────────────────────────────────────────────
    N = len(node_labels)
    M = len(items)
    req_labels = np.array(node_labels, dtype=np.int32)
    values = np.full((N, M), np.nan, dtype=np.float32)

    # ── group items by field to open each HDF5 file once ──────────────────────
    # field -> [(col_idx, component), ...]
    field_cols: Dict[str, List[Tuple[int, str]]] = {}
    for col_idx, item in enumerate(items):
        field_cols.setdefault(item["field"], []).append((col_idx, item["component"]))

    for field, col_list in field_cols.items():
        h5_path = _result_h5_path(idx.workspace, step, field, result_group)
        if not os.path.exists(h5_path):
            continue  # leave NaN

        components = field_meta[field]["components"]

        with h5py.File(h5_path, "r") as rf:
            data_path   = f"/NODAL/{instance}/data"
            labels_path = f"/NODAL/{instance}/labels"

            if data_path not in rf:
                continue  # no data for this instance

            ds = rf[data_path]
            num_frames = ds.shape[0]
            if frame_idx >= num_frames:
                raise ValidationError(
                    f"frame_idx {frame_idx} out of range [0, {num_frames}) "
                    f"for field '{field}'",
                    {"frame_idx": frame_idx, "field": field},
                )

            # Node labels for this result dataset
            if labels_path in rf:
                result_labels = rf[labels_path][:].astype(np.int32)
            else:
                # Fallback to geometry HDF5 (should be the same ordering)
                geom_path = ManifestRepo(idx.workspace).get_geom_path(instance) or \
                            os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
                if not os.path.exists(geom_path):
                    continue
                with h5py.File(geom_path, "r") as gf:
                    result_labels = gf["nodes/labels"][:].astype(np.int32)

            # Build label → row-index mapping via sort + searchsorted
            sort_order    = np.argsort(result_labels)
            sorted_labels = result_labels[sort_order]
            ins_pos       = np.searchsorted(sorted_labels, req_labels)
            ins_clamped   = np.clip(ins_pos, 0, len(sorted_labels) - 1)
            found_mask    = sorted_labels[ins_clamped] == req_labels
            result_rows   = sort_order[ins_clamped]  # row indices into result_labels

            # Read one frame: [N_nodal, ncomp] (or [N_nodal] for scalar)
            frame_data = ds[frame_idx]
            if frame_data.ndim == 1:
                frame_data = frame_data[:, np.newaxis]  # normalise to [..., 1]

            for col_idx, component in col_list:
                comp_idx = 0 if component == "" else components.index(component)

                # Extract values for all requested nodes (found ones are valid)
                col_vals = np.full(N, np.nan, dtype=np.float32)
                col_vals[found_mask] = frame_data[result_rows[found_mask], comp_idx]
                values[:, col_idx] = col_vals

    # ── build column metadata ──────────────────────────────────────────────────
    columns = [
        {
            "key":       item["field"] if item["component"] == "" else f"{item['field']}.{item['component']}",
            "field":     item["field"],
            "component": item["component"],
        }
        for item in items
    ]

    sections: List[Tuple[str, np.ndarray]] = [
        ("node_labels", req_labels),
        ("values",      values),
    ]
    return sections, columns
