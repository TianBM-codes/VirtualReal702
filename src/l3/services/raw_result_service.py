"""
Raw result extraction service.

Reads L1 result HDF5 files and returns per-node or per-element data
(NODAL / ELEMENT_NODAL / INTEGRATION_POINT) for a given frame.

This is the calculation-facing counterpart of frame_colors: instead of
coloring triangles for rendering, it returns the actual numerical arrays
plus the corresponding node/element labels so callers can map values back
to ODB topology.
"""
import json
import os
from typing import Dict, List, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, ValidationError
from ..core.state import OdbRegistry

POSITIONS = {"NODAL", "ELEMENT_NODAL", "INTEGRATION_POINT"}


def _result_h5_path(workspace: str, step: str, field: str,
                    result_group: str = None) -> str:
    def safe(s):
        return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    fname = f"{safe(step)}__{safe(field)}.h5"
    if result_group:
        return os.path.join(workspace, "l1", "results", safe(result_group), fname)
    return os.path.join(workspace, "l1", "results", fname)


def _geom_h5_path(workspace: str, instance: str) -> str:
    """Resolve L1 geometry path via manifest; fall back to safe-name construction."""
    try:
        import sqlite3
        with sqlite3.connect(os.path.join(workspace, "manifest.db")) as conn:
            row = conn.execute(
                "SELECT geom_path FROM instances WHERE instance_name=?", (instance,)
            ).fetchone()
            if row and row[0]:
                return os.path.join(workspace, row[0])
    except Exception:
        pass
    def _safe(s): return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    return os.path.join(workspace, "l1", "geometry", f"{_safe(instance)}.h5")


def _safe_section_name(prefix: str, etype: str) -> str:
    """
    Build a <=32-char L3BE section name from a prefix and element type string.
    Special chars are replaced with underscores.
    """
    safe = "".join(c if c.isalnum() else "_" for c in etype)
    name = f"{prefix}{safe}"
    return name[:32]


def _read_components_from_manifest(workspace: str, step: str, field: str,
                                   result_group: str = None) -> List[str]:
    """
    Try to read component names from manifest.db result_files table.
    result_group=None matches rows where result_group IS NULL (legacy/single-group).
    Returns [] if unavailable.
    """
    try:
        import sqlite3
        db = os.path.join(workspace, "manifest.db")
        if not os.path.exists(db):
            return []
        with sqlite3.connect(db) as conn:
            if result_group is None:
                row = conn.execute(
                    "SELECT components FROM result_files"
                    " WHERE step_name=? AND field_name=? AND result_group IS NULL",
                    (step, field),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT components FROM result_files"
                    " WHERE step_name=? AND field_name=? AND result_group=?",
                    (step, field, result_group),
                ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return []


def get_raw_values(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame_idx: int,
    position: str,
    result_group: str = None,
) -> Tuple[List[Tuple[str, np.ndarray]], List[str], List[str]]:
    """
    Extract numerical result values from L1 HDF5 for one frame.

    Returns:
        sections     — list of (name, ndarray) ready for l3be_build()
        components   — list of component name strings (e.g. ["S11","S22","S33","S12","S13","S23"])
        etype_groups — list of element type strings present in the data
                       (empty list for NODAL position)

    Section layout by position:

      NODAL:
        "node_labels"          [N]             int32   — ODB node labels
        "values"               [N, ncomp]      float32 — result values

      ELEMENT_NODAL / INTEGRATION_POINT (one group per element type):
        "el_{etype}"           [M]             int32   — ODB element labels
        "v_{etype}"            [M, n_ip, ncomp] float32 — result values
                                                         n_ip = integration points or
                                                                corner nodes per element
                               [M, ncomp] if the HDF5 dataset already collapsed the extra dim

    The etype suffix in section names is sanitised to alphanumeric + underscore,
    and truncated to fit the 32-char L3BE name limit.
    """
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )
    if position not in POSITIONS:
        raise ValidationError(
            f"position must be one of {sorted(POSITIONS)}, got '{position}'",
            {"position": position},
        )

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    h5_path = _result_h5_path(idx.workspace, step, field, result_group)
    if not os.path.exists(h5_path):
        raise NotFoundError(
            f"No result file for step='{step}' field='{field}'",
            {"step": step, "field": field},
        )

    geom_path = _geom_h5_path(idx.workspace, instance)
    if not os.path.exists(geom_path):
        raise NotFoundError(
            f"Geometry not found for instance '{instance}'",
            {"instance": instance},
        )

    # Try to get component names from manifest first (cheapest, no HDF5 open needed)
    components = _read_components_from_manifest(idx.workspace, step, field, result_group)

    sections: List[Tuple[str, np.ndarray]] = []
    etype_groups: List[str] = []

    with h5py.File(h5_path, "r") as rf, h5py.File(geom_path, "r") as gf:

        if position == "NODAL":
            ds_path = f"/NODAL/{instance}/data"
            if ds_path not in rf:
                raise NotFoundError(
                    f"No NODAL data for instance '{instance}' in field '{field}'",
                    {"instance": instance, "position": "NODAL"},
                )

            ds = rf[ds_path]
            _check_frame(frame_idx, ds.shape[0])
            frame_data = ds[frame_idx]                  # [N] or [N, ncomp]

            if frame_data.ndim == 1:
                frame_data = frame_data[:, np.newaxis]   # normalise to [N, 1]

            if not components:
                components = _infer_components(field, frame_data.shape[-1])

            node_labels = gf["nodes/labels"][:].astype(np.int32)
            sections.append(("node_labels", node_labels))
            sections.append(("values", frame_data.astype(np.float32)))

        else:
            # ELEMENT_NODAL or INTEGRATION_POINT
            pos_root = f"/{position}/{instance}"
            if pos_root not in rf:
                raise NotFoundError(
                    f"No {position} data for instance '{instance}' in field '{field}'",
                    {"instance": instance, "position": position},
                )

            for etype_str in rf[pos_root].keys():
                ds_path = f"{pos_root}/{etype_str}/data"
                if ds_path not in rf:
                    continue

                ds = rf[ds_path]
                _check_frame(frame_idx, ds.shape[0])
                frame_data = ds[frame_idx]               # [M] or [M, n_ip, ncomp] etc.

                if frame_data.ndim == 1:
                    frame_data = frame_data[:, np.newaxis]   # [M, 1]

                if not components:
                    components = _infer_components(field, frame_data.shape[-1])

                label_path = f"elements/{etype_str}/labels"
                if label_path not in gf:
                    continue
                elem_labels = gf[label_path][:].astype(np.int32)

                el_name = _safe_section_name("el_", etype_str)
                v_name  = _safe_section_name("v_",  etype_str)
                sections.append((el_name, elem_labels))
                sections.append((v_name,  frame_data.astype(np.float32)))
                etype_groups.append(etype_str)

    if not sections:
        raise NotFoundError(
            f"No {position} data for instance '{instance}' field '{field}'",
            {"instance": instance, "field": field, "position": position},
        )

    return sections, components, etype_groups


# ── helpers ────────────────────────────────────────────────────────────────────

def _check_frame(frame_idx: int, num_frames: int) -> None:
    if frame_idx >= num_frames:
        raise ValidationError(
            f"frame_idx {frame_idx} out of range [0, {num_frames})",
            {"frame_idx": frame_idx},
        )


def _infer_components(field: str, ncomp: int) -> List[str]:
    """
    Best-effort component names from field name + component count.
    Real names come from manifest; this is the fallback.
    """
    KNOWN = {
        ("U",  3): ["U1", "U2", "U3"],
        ("RF", 3): ["RF1", "RF2", "RF3"],
        ("CF", 3): ["CF1", "CF2", "CF3"],
        ("S",  6): ["S11", "S22", "S33", "S12", "S13", "S23"],
        ("LE", 6): ["LE11", "LE22", "LE33", "LE12", "LE13", "LE23"],
        ("E",  6): ["E11", "E22", "E33", "E12", "E13", "E23"],
        ("PE", 6): ["PE11", "PE22", "PE33", "PE12", "PE13", "PE23"],
    }
    return KNOWN.get((field, ncomp), [f"{field}{i+1}" for i in range(ncomp)])


def raw_values_to_json_payload(
    sections: List[Tuple[str, np.ndarray]],
    components: List[str],
    etype_groups: List[str],
    *,
    position: str,
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame_idx: int,
) -> Dict:
    """
    Convert raw-value sections to a JSON-serializable payload.
    """
    payload: Dict = {
        "odb_id": odb_id,
        "instance": instance,
        "step": step,
        "field": field,
        "frame": frame_idx,
        "position": position,
        "components": components,
    }

    section_map = {name: arr for name, arr in sections}
    if position == "NODAL":
        payload["node_labels"] = section_map["node_labels"].astype(np.int32).tolist()
        payload["values"] = section_map["values"].astype(np.float32).tolist()
        return payload

    groups = []
    for etype in etype_groups:
        groups.append(
            {
                "etype": etype,
                "elem_labels": section_map[_safe_section_name("el_", etype)].astype(np.int32).tolist(),
                "values": section_map[_safe_section_name("v_", etype)].astype(np.float32).tolist(),
            }
        )
    payload["etype_groups"] = groups
    return payload
