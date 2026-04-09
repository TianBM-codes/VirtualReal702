"""
Frame-color computation:
  L1 results HDF5 → per-vertex uint8 RGBA aligned to Triangle Soup.

Position fallback order: NODAL → ELEMENT_NODAL → INTEGRATION_POINT
"""
import logging
import os
from typing import Literal

import h5py
import numpy as np

from ..core.errors import NotFoundError, NotReadyError, ValidationError
from ..core.state import OdbRegistry
from ..infra.colormap import apply_jet

logger = logging.getLogger(__name__)

Component = Literal["U1", "U2", "U3", "USUM"]
_COMP_IDX = {"U1": 0, "U2": 1, "U3": 2}


def _result_h5_path(workspace: str, step: str, field: str) -> str:
    def safe(s):
        return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    fname = "{}__{}.h5".format(safe(step), safe(field))
    return os.path.join(workspace, "l1", "results", fname)


def _scalar_from_nodal(f, instance: str, frame_idx: int, component: Component):
    """
    Read NODAL data → scalar per node [N_nodes].
    Returns (scalar_node, num_frames) or None if not present.
    """
    ds_path = f"/NODAL/{instance}/data"
    if ds_path not in f:
        return None
    ds = f[ds_path]
    num_frames = ds.shape[0]
    if frame_idx >= num_frames:
        from ..core.errors import ValidationError
        raise ValidationError(
            f"frame_idx {frame_idx} out of range [0, {num_frames})",
            {"frame_idx": frame_idx},
        )
    frame_data = ds[frame_idx]   # [N, ncomp]
    if component == "USUM":
        if frame_data.ndim == 1:
            return frame_data.astype(np.float32), num_frames
        return np.linalg.norm(frame_data, axis=1).astype(np.float32), num_frames
    ci = _COMP_IDX.get(component, 0)
    if frame_data.ndim == 1:
        return frame_data.astype(np.float32), num_frames
    if ci >= frame_data.shape[1]:
        return np.linalg.norm(frame_data, axis=1).astype(np.float32), num_frames
    return frame_data[:, ci].astype(np.float32), num_frames


def _scalar_from_element_position(f, position: str, instance: str,
                                   frame_idx: int, component: Component,
                                   src_etype: np.ndarray, src_elem_row: np.ndarray):
    """
    Read ELEMENT_NODAL or INTEGRATION_POINT data → scalar per render face [Rf].

    For each unique etype group, reads data[frame_idx] shape [N_elem, ...extra..., ncomp],
    averages over extra dims, extracts component, then maps via src_elem_row.

    Returns (scalar_per_face [Rf], num_frames) or None if no etype group found.
    """
    Rf = len(src_etype)
    scalar_face = np.full(Rf, np.nan, dtype=np.float32)
    num_frames = None
    found_any = False

    unique_etypes = np.unique(src_etype)
    for etype_bytes in unique_etypes:
        etype_str = etype_bytes.decode("ascii").rstrip("\x00")
        ds_path = f"/{position}/{instance}/{etype_str}/data"
        if ds_path not in f:
            continue

        ds = f[ds_path]
        if num_frames is None:
            num_frames = ds.shape[0]

        frame_data = ds[frame_idx]   # [N_elem, ...extra..., ncomp]

        # Average over all middle dimensions until shape is [N_elem, ncomp] or [N_elem]
        while frame_data.ndim > 2:
            frame_data = frame_data.mean(axis=1)

        # Extract component or magnitude
        if frame_data.ndim == 2:
            ci = _COMP_IDX.get(component, 0)
            if component == "USUM":
                scalar_elem = np.linalg.norm(frame_data, axis=1).astype(np.float32)
            elif ci < frame_data.shape[1]:
                scalar_elem = frame_data[:, ci].astype(np.float32)
            else:
                scalar_elem = np.linalg.norm(frame_data, axis=1).astype(np.float32)
        else:
            scalar_elem = frame_data.astype(np.float32)

        mask = src_etype == etype_bytes
        elem_rows = src_elem_row[mask]
        valid = elem_rows < len(scalar_elem)
        scalar_face[np.where(mask)[0][valid]] = scalar_elem[elem_rows[valid]]
        found_any = True

    if not found_any:
        return None
    return scalar_face, num_frames


def frame_colors(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame_idx: int,
    component: Component = "USUM",
    render_mode: str = "smooth",
) -> tuple:
    """
    Returns (color_per_vertex [Rf*3, 4] uint8, legend_range [2] float32)

    color_per_vertex is aligned to Triangle Soup vertex order.
    Tries NODAL first; falls back to ELEMENT_NODAL then INTEGRATION_POINT.
    """
    # Validate frame_idx sign upfront — NumPy/HDF5 silently accept negative indices
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found")
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' is not render-ready yet")

    src_node_rows = idx.source_node_rows.get(instance)
    src_elem_row  = idx.render_source_elem_row.get(instance)
    src_etype     = idx.source_elem_etype.get(instance)

    if src_node_rows is None:
        raise NotFoundError(
            f"Instance '{instance}' has no render map",
            {"instance": instance},
        )

    h5_path = _result_h5_path(idx.workspace, step, field)
    if not os.path.exists(h5_path):
        raise NotFoundError(
            f"Result file not found for step='{step}' field='{field}'",
            {"step": step, "field": field},
        )

    scalar_vertex = None
    num_frames = None

    with h5py.File(h5_path, "r") as f:
        # ── Try NODAL ──────────────────────────────────────────────────────
        result = _scalar_from_nodal(f, instance, frame_idx, component)
        if result is not None:
            scalar_node, num_frames = result
            n_result_nodes = len(scalar_node)
            max_node_row = int(src_node_rows.max()) if src_node_rows.size else 0
            if max_node_row >= n_result_nodes:
                # Sparse NODAL field: dataset covers fewer nodes than the full geometry.
                # Extend with NaN so indexing always succeeds; NaN → 0 later via nan_to_num.
                extended = np.full(max_node_row + 1, np.nan, dtype=np.float32)
                extended[:n_result_nodes] = scalar_node
                scalar_node = extended

            if render_mode == "flat" and src_elem_row is not None:
                # Average node values per face, then average per (etype, elem_row) element.
                # Must use composite key: src_elem_row is per-etype-local, not globally unique.
                face_node_vals = scalar_node[src_node_rows]   # [Rf, 3]
                face_vals = face_node_vals.mean(axis=1)        # [Rf]
                if src_etype is not None:
                    _, et_idx = np.unique(src_etype, return_inverse=True)
                    max_er = int(src_elem_row.max()) + 1
                    composite = et_idx.astype(np.int64) * max_er + src_elem_row.astype(np.int64)
                else:
                    composite = src_elem_row.astype(np.int64)
                _, inverse = np.unique(composite, return_inverse=True)
                n_groups = int(inverse.max()) + 1
                elem_sum = np.zeros(n_groups, dtype=np.float64)
                np.add.at(elem_sum, inverse, face_vals)
                elem_cnt = np.bincount(inverse, minlength=n_groups).astype(np.float64)
                elem_mean = (elem_sum / np.where(elem_cnt > 0, elem_cnt, 1)).astype(np.float32)
                scalar_vertex = np.repeat(elem_mean[inverse], 3)  # [Rf*3]
            else:
                vertex_rows = src_node_rows.ravel()   # [Rf*3]
                scalar_vertex = scalar_node[vertex_rows]

        # ── Try ELEMENT_NODAL ──────────────────────────────────────────────
        if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
            result = _scalar_from_element_position(
                f, "ELEMENT_NODAL", instance, frame_idx, component,
                src_etype, src_elem_row,
            )
            if result is not None:
                scalar_face, num_frames = result
                scalar_vertex = np.repeat(scalar_face, 3)   # [Rf*3]

        # ── Try INTEGRATION_POINT ──────────────────────────────────────────
        if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
            result = _scalar_from_element_position(
                f, "INTEGRATION_POINT", instance, frame_idx, component,
                src_etype, src_elem_row,
            )
            if result is not None:
                scalar_face, num_frames = result
                scalar_vertex = np.repeat(scalar_face, 3)   # [Rf*3]

    if scalar_vertex is None:
        raise NotFoundError(
            f"No result data (NODAL/ELEMENT_NODAL/INTEGRATION_POINT) "
            f"for instance '{instance}' in field '{field}'",
            {"instance": instance, "field": field},
        )

    # Validate frame range (use num_frames from whichever path was taken)
    if num_frames is not None and (frame_idx < 0 or frame_idx >= num_frames):
        raise ValidationError(
            f"frame_idx {frame_idx} out of range [0, {num_frames})",
            {"frame_idx": frame_idx},
        )

    # Replace any NaN (unmapped faces) with 0
    scalar_vertex = np.nan_to_num(scalar_vertex, nan=0.0)

    val_min = float(scalar_vertex.min())
    val_max = float(scalar_vertex.max())
    legend_range = np.array([val_min, val_max], dtype=np.float32)

    span = val_max - val_min
    if span < 1e-12:
        normalized = np.zeros_like(scalar_vertex)
    else:
        normalized = (scalar_vertex - val_min) / span

    color_per_vertex = apply_jet(normalized)   # [Rf*3, 4] uint8

    return color_per_vertex, legend_range
