"""
User-defined field service.

Supports storing a scalar value associated with a named set of element labels,
and rendering it as a cloud map (per-vertex RGBA aligned to Triangle Soup).

Use case: each sensitivity parameter Txx produces ONE scalar result applied to
ALL elements in a user-defined set.  Elements outside the set display neutral gray.
Each Txx → one named user field → one cloud-map call.
"""
import os
from typing import List, Optional, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, NotReadyError, ValidationError
from ..core.state import OdbRegistry
from ..infra.colormap import apply_jet_with_neutral
from ..infra.manifest_repo import ManifestRepo


# ── internal helpers ──────────────────────────────────────────────────────────

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


def get_face_mask_for_elem_labels(
    idx,
    instance: str,
    user_labels_set: set,
) -> np.ndarray:
    """
    Build a boolean mask [Rf] where True = render face belongs to an element
    in user_labels_set.

    Mapping: for each render face:
      etype = src_etype[face_i]
      elem_row = src_elem_row[face_i]
      label = geom_hdf5["elements/{etype}/labels"][elem_row]
    """
    src_elem_row = idx.render_source_elem_row.get(instance)
    src_etype    = idx.source_elem_etype.get(instance)

    if src_elem_row is None or src_etype is None:
        return np.zeros(0, dtype=bool)

    Rf = len(src_elem_row)
    face_mask = np.zeros(Rf, dtype=bool)

    geom_path = _geom_h5_path(idx.workspace, instance)
    if not os.path.exists(geom_path):
        return face_mask

    with h5py.File(geom_path, "r") as f:
        for etype_bytes in np.unique(src_etype):
            etype_str = etype_bytes.decode("ascii").rstrip("\x00")
            labels_ds = f"elements/{etype_str}/labels"
            if labels_ds not in f:
                continue

            geom_labels = f[labels_ds][:].astype(np.int32)

            etype_mask        = src_etype == etype_bytes
            etype_face_idx    = np.where(etype_mask)[0]
            etype_elem_rows   = src_elem_row[etype_mask]

            valid = etype_elem_rows < len(geom_labels)
            face_labels = np.full(len(etype_face_idx), -1, dtype=np.int32)
            face_labels[valid] = geom_labels[etype_elem_rows[valid]]

            in_set = np.isin(face_labels, list(user_labels_set))
            face_mask[etype_face_idx[in_set]] = True

    return face_mask


# ── public API ────────────────────────────────────────────────────────────────

def save_user_field(
    registry: OdbRegistry,
    odb_id: str,
    name: str,
    instance: str,
    value: float,
    element_labels: List[int],
) -> dict:
    """
    Persist a named user field.  Returns {id, name, instance, value, elem_count}.

    Validation:
      - ODB must be loaded (any status).
      - element_labels must not be empty.
      - name must be a non-empty string.
    """
    if not name or not name.strip():
        raise ValidationError("name must not be empty")
    if not element_labels:
        raise ValidationError("element_labels must not be empty")

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    repo = ManifestRepo(idx.workspace)
    labels_arr = np.array(element_labels, dtype=np.int32)

    field_id = repo.save_user_field(
        name=name,
        instance_name=instance,
        value=value,
        element_labels=labels_arr,
    )
    return {
        "id":         field_id,
        "name":       name,
        "instance":   instance,
        "value":      value,
        "elem_count": len(labels_arr),
    }


def list_user_fields(
    registry: OdbRegistry,
    odb_id: str,
    instance: Optional[str] = None,
) -> List[dict]:
    """List all user fields stored for this ODB, optionally filtered by instance."""
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    repo = ManifestRepo(idx.workspace)
    return repo.list_user_fields(instance_name=instance)


def delete_user_field(
    registry: OdbRegistry,
    odb_id: str,
    name: str,
    instance: str,
) -> bool:
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    repo = ManifestRepo(idx.workspace)
    return repo.delete_user_field(name, instance)


def get_user_field_colors(
    registry: OdbRegistry,
    odb_id: str,
    name: str,
    instance: str,
    val_min: Optional[float] = None,
    val_max: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build per-vertex RGBA cloud map for a user-defined field.

    Elements in the set → colored by the stored scalar value (jet colormap).
    Elements outside   → neutral gray.

    Parameters
    ----------
    val_min / val_max : explicit normalization range (useful when comparing
        multiple Txx fields on the same scale). Defaults to the stored value
        (all elements show the same jet-midpoint color when min==max).

    Returns
    -------
    color_per_vertex : [Rf*3, 4] uint8  — aligned to Triangle Soup
    legend_range     : [2] float32      — [val_min_used, val_max_used]
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' is not render-ready yet")

    src_node_rows = idx.source_node_rows.get(instance)
    if src_node_rows is None:
        raise NotFoundError(
            f"Instance '{instance}' has no render map",
            {"instance": instance},
        )

    repo = ManifestRepo(idx.workspace)
    field = repo.get_user_field(name, instance)
    if field is None:
        raise NotFoundError(
            f"User field '{name}' not found for instance '{instance}'",
            {"name": name, "instance": instance},
        )

    stored_value    = field["value"]
    element_labels  = field["element_labels"]
    user_labels_set = set(element_labels.tolist())

    # Build per-face mask [Rf]
    face_mask = get_face_mask_for_elem_labels(idx, instance, user_labels_set)
    if face_mask.size == 0:
        Rf = src_node_rows.shape[0]
        face_mask = np.zeros(Rf, dtype=bool)

    # Build per-face scalar: stored_value for matched faces, NaN elsewhere
    Rf = face_mask.shape[0]
    scalar_face = np.full(Rf, np.nan, dtype=np.float32)
    scalar_face[face_mask] = stored_value

    # Determine normalization range
    effective_min = val_min if val_min is not None else stored_value
    effective_max = val_max if val_max is not None else stored_value
    if effective_max < effective_min:
        effective_min, effective_max = effective_max, effective_min

    # Per-face RGBA: NaN → gray, others → jet
    colors_face = apply_jet_with_neutral(scalar_face, effective_min, effective_max)  # [Rf, 4]

    # Expand to per-vertex (Triangle Soup: each face = 3 vertices)
    color_per_vertex = np.repeat(colors_face, 3, axis=0)  # [Rf*3, 4]

    legend_range = np.array([effective_min, effective_max], dtype=np.float32)
    return color_per_vertex, legend_range
