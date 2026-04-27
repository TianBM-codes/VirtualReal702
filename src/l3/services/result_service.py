"""
Frame-color / frame-scalar computation:
  L1 results HDF5 → per-vertex uint8 RGBA or float32 scalars aligned to render geometry.

Position fallback order: NODAL → ELEMENT_NODAL → INTEGRATION_POINT
"""
import logging
import math
import os
from collections import defaultdict
from typing import Dict, Literal, Optional, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, NotReadyError, ValidationError
from ..core.state import OdbRegistry
from ..infra.colormap import apply_jet
from ..infra.manifest_repo import ManifestRepo

logger = logging.getLogger(__name__)

Component = Literal["U1", "U2", "U3"]


# ─── Generic component extraction ────────────────────────────────────────────

def _extract_component(data: np.ndarray, component_idx: Optional[int]) -> np.ndarray:
    """
    Extract a scalar from the last axis of data.

    data:          [..., ncomp] or scalar [...]
    component_idx: int → use as direct index into last axis
                   None → compute magnitude (L2 norm over last axis)
    Returns float32 array with one fewer dimension.
    """
    if data.ndim == 1:
        return data.astype(np.float32)
    if component_idx is None:
        return np.linalg.norm(data, axis=-1).astype(np.float32)
    ci = int(component_idx)
    if ci < data.shape[-1]:
        return data[..., ci].astype(np.float32)
    # component_idx out of range (element type has fewer components) → NaN so
    # those faces render as no-data (grey) rather than a spurious magnitude.
    return np.full(data.shape[:-1], np.nan, dtype=np.float32)


def _scalar_nodal_by_idx(f, instance: str, frame_idx: int,
                          component_idx: Optional[int]) -> Optional[Tuple]:
    """
    Read NODAL data → (scalar_node [N_nodes] float32, num_frames).
    Returns None if dataset is absent.
    """
    ds_path = f"/NODAL/{instance}/data"
    if ds_path not in f:
        return None
    ds = f[ds_path]
    num_frames = ds.shape[0]
    if frame_idx >= num_frames:
        raise ValidationError(
            f"frame_idx {frame_idx} out of range [0, {num_frames})",
            {"frame_idx": frame_idx},
        )
    frame_data = ds[frame_idx]   # [N, ncomp] or [N]
    # Collapse extra dims (e.g. [N, 1, ncomp] from some exporters)
    while frame_data.ndim > 2:
        frame_data = frame_data.mean(axis=1)
    return _extract_component(frame_data, component_idx), num_frames


def _scalar_elem_pos_by_idx(f, position: str, instance: str, frame_idx: int,
                             component_idx: Optional[int],
                             src_etype: np.ndarray,
                             src_elem_row: np.ndarray) -> Optional[Tuple]:
    """
    Read ELEMENT_NODAL or INTEGRATION_POINT →
        (scalar_per_face [Rf] float32, num_frames, global_range or None).
    Returns None if no etype group was found.

    global_range = (val_min, val_max) from surface faces only (NaN excluded),
    matching Abaqus which bases its legend on the visible surface.
    """
    Rf = len(src_etype)
    scalar_face = np.full(Rf, np.nan, dtype=np.float32)
    num_frames = None
    found_any = False

    for etype_bytes in np.unique(src_etype):
        etype_str = etype_bytes.decode("ascii").rstrip("\x00")
        etype_grp_path = f"/{position}/{instance}/{etype_str}"
        if etype_grp_path not in f:
            continue

        etype_grp = f[etype_grp_path]

        # Solid elements: data is directly at etype_grp/data
        # Shell elements: data is in sp{n} subgroups; use sp1 (section point 1) only
        if "data" in etype_grp:
            ds = etype_grp["data"]
        else:
            sp_keys = sorted(k for k in etype_grp.keys() if k.startswith("sp"))
            sp_key = "sp1" if "sp1" in etype_grp else (sp_keys[0] if sp_keys else None)
            if sp_key is None or "data" not in etype_grp[sp_key]:
                continue
            ds = etype_grp[sp_key]["data"]

        if num_frames is None:
            num_frames = ds.shape[0]

        frame_data = ds[frame_idx]

        # Average over middle dimensions (local_nodes or ips) until [N_elem, ncomp] or [N_elem]
        while frame_data.ndim > 2:
            frame_data = frame_data.mean(axis=1)

        scalar_elem = _extract_component(frame_data, component_idx)   # [N_elem]

        mask = src_etype == etype_bytes
        elem_rows = src_elem_row[mask]
        valid = elem_rows < len(scalar_elem)
        scalar_face[np.where(mask)[0][valid]] = scalar_elem[elem_rows[valid]]
        found_any = True

    if not found_any:
        return None
    # global_range from surface faces only (matches Abaqus: legend uses visible surface values)
    valid_face = scalar_face[np.isfinite(scalar_face)]
    global_range = (float(valid_face.min()), float(valid_face.max())) if valid_face.size > 0 else None
    return scalar_face, num_frames, global_range
_COMP_IDX = {"U1": 0, "U2": 1, "U3": 2}


def _resolve_magnitude_field(workspace: str, step: str, field: str,
                             result_group: str = None):
    """
    If field ends with '_MAGNITUDE' and the parent field's H5 exists, return
    (parent_field, None) so the caller can compute L2 norm on the fly.
    Returns (None, None) if the pattern doesn't match or the parent doesn't exist.

    Example: 'U_MAGNITUDE' → ('U', None)  — component_idx=None means L2 norm
    """
    if not field.endswith("_MAGNITUDE"):
        return None, None
    parent = field[: -len("_MAGNITUDE")]
    if not parent:
        return None, None
    parent_path = _manifest_result_h5_path(workspace, step, parent, result_group)
    if os.path.exists(parent_path):
        return parent, None
    return None, None


def _result_h5_path(workspace: str, step: str, field: str,
                    result_group: str = None) -> str:
    def safe(s):
        return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    fname = "{}__{}.h5".format(safe(step), safe(field))
    if result_group:
        return os.path.join(workspace, "l1", "results", safe(result_group), fname)
    return os.path.join(workspace, "l1", "results", fname)


def _manifest_result_h5_path(workspace: str, step: str, field: str,
                              result_group: str = None) -> str:
    """
    Get result H5 path from manifest.db file_path column (authoritative),
    falling back to _result_h5_path if no manifest entry exists.

    Also tries result_group=NULL as a secondary fallback so that legacy
    ODB projects whose result_files rows were only partially migrated
    (result_group column still NULL) can still be served correctly.
    """
    try:
        manifest = ManifestRepo(workspace)
        rf = manifest.get_result_file(step, field, result_group)
        if rf is None and result_group is not None:
            # Partial migration: result_files row still has NULL result_group
            rf = manifest.get_result_file(step, field, None)
        if rf and rf["file_path"]:
            rel = rf["file_path"].replace("\\", os.sep).replace("/", os.sep)
            return os.path.join(workspace, rel)
    except Exception:
        pass
    return _result_h5_path(workspace, step, field, result_group)


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
        etype_grp_path = f"/{position}/{instance}/{etype_str}"
        if etype_grp_path not in f:
            continue

        etype_grp = f[etype_grp_path]

        # Solid elements: data directly; shell elements: sp{n} subgroups → use sp1 only
        if "data" in etype_grp:
            ds = etype_grp["data"]
        else:
            sp_keys = sorted(k for k in etype_grp.keys() if k.startswith("sp"))
            sp_key = "sp1" if "sp1" in etype_grp else (sp_keys[0] if sp_keys else None)
            if sp_key is None or "data" not in etype_grp[sp_key]:
                continue
            ds = etype_grp[sp_key]["data"]

        if num_frames is None:
            num_frames = ds.shape[0]

        frame_data = ds[frame_idx]

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
    result_group: str = None,
    set_name: str = None,
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

    h5_path = _manifest_result_h5_path(idx.workspace, step, field, result_group)
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
                face_node_vals = scalar_node[src_node_rows]   # [Nt, 3]
                face_vals = face_node_vals.mean(axis=1)        # [Nt]
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
                scalar_vertex = np.repeat(elem_mean[inverse], 3)  # [Nt*3]
            else:
                scalar_vertex = scalar_node[src_node_rows.ravel()]  # [Nt*3]

        # ── Try ELEMENT_NODAL ──────────────────────────────────────────────
        if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
            result = _scalar_from_element_position(
                f, "ELEMENT_NODAL", instance, frame_idx, component,
                src_etype, src_elem_row,
            )
            if result is not None:
                scalar_face, num_frames = result
                scalar_vertex = np.repeat(scalar_face, 3)  # [Nt*3]

        # ── Try INTEGRATION_POINT ──────────────────────────────────────────
        if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
            result = _scalar_from_element_position(
                f, "INTEGRATION_POINT", instance, frame_idx, component,
                src_etype, src_elem_row,
            )
            if result is not None:
                scalar_face, num_frames = result
                scalar_vertex = np.repeat(scalar_face, 3)  # [Nt*3]

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

    # Apply set filter: keep only triangles belonging to the named set
    if set_name is not None:
        manifest = ManifestRepo(idx.workspace)
        render_rows = manifest.get_user_set_render_rows(set_name, instance)
        if render_rows is None:
            from .user_field_service import get_face_mask_for_elem_labels
            elem_labels = manifest.get_element_set_labels(set_name, instance)
            if elem_labels is not None and len(elem_labels) > 0:
                face_mask = get_face_mask_for_elem_labels(
                    idx, instance, set(elem_labels.tolist())
                )
                render_rows = np.where(face_mask)[0].astype(np.int32)
        if render_rows is not None and len(render_rows) > 0:
            # render_rows are triangle indices; each triangle has 3 vertices in soup
            vtx_idx = (render_rows[:, None] * 3 + np.arange(3)).ravel()
            scalar_vertex = scalar_vertex[vtx_idx]

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

    color_per_vertex = apply_jet(normalized)   # [Nt_subset*3, 4] uint8

    return color_per_vertex, legend_range


# ─── frame_scalars: backend returns t values, frontend applies colormap ───────

def frame_scalars(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    field: str,
    frame_idx: int,
    component_idx: Optional[int] = None,
    render_mode: str = "smooth",
    result_group: str = None,
    set_name: str = None,
    feature_angle: Optional[float] = 20.0,
    average_threshold: float = 0.75,
    use_geometry_split: bool = True,
) -> Tuple[np.ndarray, np.ndarray, str]:
    """
    Returns (u_per_vertex [Nv] float32, legend_range [2] float32, result_position str).

    u_per_vertex: normalized scalar t ∈ [0, 1] for each render vertex.
    legend_range: [val_min, val_max] in original field units.
    result_position: 'NODAL' | 'ELEMENT_NODAL' | 'ELEMENT_NODAL_FLAT' |
                     'INTEGRATION_POINT_FLAT'

    component_idx=None → magnitude (L2 norm).
    component_idx=0,1,2,... → direct index into the result component axis.

    Position fallback: NODAL → ELEMENT_NODAL (averaged) → INTEGRATION_POINT (flat).
    feature_angle: degrees for shell/membrane geometric splitting; None = section-only.
    use_geometry_split: False = ignore feature_angle, use section-only domains.
    average_threshold: 75% threshold for conditional node averaging.
    """
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

    h5_path = _manifest_result_h5_path(idx.workspace, step, field, result_group)
    if not os.path.exists(h5_path):
        # Transparent fallback: PARENT_MAGNITUDE → PARENT + compute L2 norm.
        # e.g. field='U_MAGNITUDE' → open U.h5 with component_idx=None.
        parent_field, mag_idx = _resolve_magnitude_field(
            idx.workspace, step, field, result_group)
        if parent_field is not None:
            field = parent_field
            component_idx = mag_idx  # None → L2 norm in _extract_component
            h5_path = _manifest_result_h5_path(idx.workspace, step, field, result_group)
        if not os.path.exists(h5_path):
            raise NotFoundError(
                f"Result file not found for step='{step}' field='{field}'",
                {"step": step, "field": field},
            )

    _manifest = ManifestRepo(idx.workspace)
    geom_h5_path = _manifest.get_geom_path(instance)

    # Indexed geometry arrays — present only when L2 produced an index buffer.
    # vtx_nr [Nv]: L1 node row for each unique vertex
    # vtx_ti [Nv]: representative triangle index per vertex (for flat/element-level scatter)
    # When None, geometry is Triangle Soup and results are returned as [Nt*3].
    vtx_nr = idx.vtx_node_row.get(instance)
    vtx_ti = idx.vtx_tri_idx.get(instance)

    scalar_vertex = None
    num_frames = None
    result_position = "NODAL"
    global_range = None   # (val_min, val_max) from full model; set per code-path below

    with h5py.File(h5_path, "r") as f:

        # ── NODAL ────────────────────────────────────────────────────────────
        result = _scalar_nodal_by_idx(f, instance, frame_idx, component_idx)
        if result is not None:
            scalar_node, num_frames = result
            result_position = "NODAL"

            # Extend sparse NODAL fields so indexing always succeeds
            max_node_row = int(src_node_rows.max()) if src_node_rows.size else 0
            if max_node_row >= len(scalar_node):
                extended = np.full(max_node_row + 1, np.nan, dtype=np.float32)
                extended[:len(scalar_node)] = scalar_node
                scalar_node = extended

            # Global range from ALL nodes (nanmin/nanmax ignores the NaN fill above)
            finite_nodes = scalar_node[np.isfinite(scalar_node)]
            if finite_nodes.size > 0:
                global_range = (float(finite_nodes.min()), float(finite_nodes.max()))

            if render_mode == "flat" and src_elem_row is not None:
                # Per-element average of node values
                face_node_vals = scalar_node[src_node_rows]   # [Nt, 3]
                face_vals = face_node_vals.mean(axis=1)        # [Nt]
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
                if vtx_ti is not None:
                    scalar_vertex = elem_mean[inverse[vtx_ti]]  # [Nv] indexed
                else:
                    scalar_vertex = np.repeat(elem_mean[inverse], 3)  # [Nt*3] soup
            elif vtx_nr is not None:
                scalar_vertex = scalar_node[vtx_nr]            # [Nv] indexed smooth
            else:
                scalar_vertex = scalar_node[src_node_rows.ravel()]  # [Nt*3] soup smooth

        # ── ELEMENT_NODAL (per-local-node with domain averaging) ─────────
        if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
            local_node_idx = idx.source_local_node_idx.get(instance)
            render_idx     = idx.render_indices.get(instance)
            avd            = idx.averaging_data.get(instance)

            if (render_mode != "flat"
                    and local_node_idx is not None and render_idx is not None
                    and avd is not None and vtx_nr is not None):
                fa = feature_angle if use_geometry_split else None
                domain_id = _get_domain_ids(idx, instance, fa)
                if domain_id is not None:
                    en_result = _en_per_vertex_averaged(
                        f, instance, frame_idx, component_idx,
                        src_etype, src_elem_row,
                        local_node_idx, vtx_nr, render_idx,
                        domain_id,
                        avd["elem_etype"], avd["elem_row"],
                        average_threshold=average_threshold,
                    )
                    if en_result is not None:
                        scalar_vertex, num_frames, global_range = en_result
                        result_position = "ELEMENT_NODAL"

            # Flat fallback if averaging data not available
            if scalar_vertex is None:
                result = _scalar_elem_pos_by_idx(
                    f, "ELEMENT_NODAL", instance, frame_idx, component_idx,
                    src_etype, src_elem_row,
                )
                if result is not None:
                    scalar_face, num_frames, global_range = result
                    result_position = "ELEMENT_NODAL_FLAT"
                    if vtx_ti is not None:
                        scalar_vertex = scalar_face[vtx_ti]     # [Nv] indexed
                    else:
                        scalar_vertex = np.repeat(scalar_face, 3)  # [Nt*3] soup

        # ── All-element global range for ELEMENT_NODAL paths ────────────
        # Override surface-only range with full-model averaged range using
        # section_id from geometry H5 (all elements including interior).
        if result_position.startswith("ELEMENT_NODAL") and geom_h5_path is not None:
            all_range = _compute_en_global_range(
                f, geom_h5_path, instance, frame_idx, component_idx,
                average_threshold,
            )
            if all_range is not None:
                global_range = all_range

        # ── INTEGRATION_POINT (flat fallback) ────────────────────────────
        if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
            result = _scalar_elem_pos_by_idx(
                f, "INTEGRATION_POINT", instance, frame_idx, component_idx,
                src_etype, src_elem_row,
            )
            if result is not None:
                scalar_face, num_frames, global_range = result
                result_position = "INTEGRATION_POINT_FLAT"
                if vtx_ti is not None:
                    scalar_vertex = scalar_face[vtx_ti]         # [Nv] indexed
                else:
                    scalar_vertex = np.repeat(scalar_face, 3)  # [Nt*3] soup

    if scalar_vertex is None:
        logger.warning(
            "frame_scalars: no data found  field=%s instance=%s step=%s frame=%s "
            "h5=%s src_etypes=%s",
            field, instance, step, frame_idx, h5_path,
            [e.decode("ascii", errors="replace").rstrip("\x00")
             for e in (np.unique(src_etype).tolist() if src_etype is not None else [])],
        )
        raise NotFoundError(
            f"No result data (NODAL/ELEMENT_NODAL/INTEGRATION_POINT) "
            f"for instance '{instance}' in field '{field}'",
            {"instance": instance, "field": field},
        )

    if num_frames is not None and frame_idx >= num_frames:
        raise ValidationError(
            f"frame_idx {frame_idx} out of range [0, {num_frames})",
            {"frame_idx": frame_idx},
        )

    # Apply set filter: keep only vertices belonging to the named set
    if set_name is not None:
        manifest = ManifestRepo(idx.workspace)
        render_rows = manifest.get_user_set_render_rows(set_name, instance)
        if render_rows is None:
            from .user_field_service import get_face_mask_for_elem_labels
            elem_labels = manifest.get_element_set_labels(set_name, instance)
            if elem_labels is not None and len(elem_labels) > 0:
                face_mask = get_face_mask_for_elem_labels(
                    idx, instance, set(elem_labels.tolist())
                )
                render_rows = np.where(face_mask)[0].astype(np.int32)
        if render_rows is not None and len(render_rows) > 0:
            render_idx = idx.render_indices.get(instance)
            if render_idx is not None:
                # indexed geometry: compact to unique vertices of the selected triangles
                used_vtx = np.unique(render_idx[render_rows].ravel())
                scalar_vertex = scalar_vertex[used_vtx]
            else:
                # soup geometry: each triangle occupies 3 contiguous vertices
                vtx_idx = (render_rows[:, None] * 3 + np.arange(3)).ravel()
                scalar_vertex = scalar_vertex[vtx_idx]

    # NaN = element type has no data for this component (e.g. shell missing S33).
    # Preserve NaN through normalization so the frontend can render those faces grey.
    # Use full-model range (computed above per code-path) so the legend matches Abaqus.
    # Fall back to surface-only range only if no global range was captured.
    if global_range is not None and np.isfinite(global_range[0]):
        val_min, val_max = float(global_range[0]), float(global_range[1])
    else:
        finite = scalar_vertex[np.isfinite(scalar_vertex)]
        if finite.size > 0:
            val_min, val_max = float(finite.min()), float(finite.max())
        else:
            val_min, val_max = 0.0, 0.0
    legend_range = np.array([val_min, val_max], dtype=np.float32)

    span = val_max - val_min
    has_data = np.isfinite(scalar_vertex)
    if span < 1e-12:
        u_per_vertex = np.where(has_data, 0.0, np.nan).astype(np.float32)
    else:
        u_per_vertex = np.where(
            has_data,
            np.clip((scalar_vertex - val_min) / span, 0.0, 1.0),
            np.nan,
        ).astype(np.float32)

    return u_per_vertex, legend_range, result_position


# ─── Averaging domain helpers ─────────────────────────────────────────────────

_ELEM_KIND_SOLID    = 0
_ELEM_KIND_SHELL    = 1
_ELEM_KIND_MEMBRANE = 2


def _union_find_domains(section_id, elem_kind, adj_src, adj_dst, adj_angle_deg,
                        feature_angle_deg=20.0):
    """Union-Find domain partition (mirrors L2 build_domain_ids)."""
    E      = len(section_id)
    parent = np.arange(E, dtype=np.int32)
    rank   = np.zeros(E, dtype=np.int32)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    cos_thr = math.cos(math.radians(feature_angle_deg))

    for i in range(len(adj_src)):
        ea, eb = int(adj_src[i]), int(adj_dst[i])
        if section_id[ea] != section_id[eb]:
            continue
        ka, kb = int(elem_kind[ea]), int(elem_kind[eb])
        if ka == _ELEM_KIND_SOLID and kb == _ELEM_KIND_SOLID:
            union(ea, eb)
        elif ka in (_ELEM_KIND_SHELL, _ELEM_KIND_MEMBRANE) and \
             kb in (_ELEM_KIND_SHELL, _ELEM_KIND_MEMBRANE):
            if math.cos(math.radians(float(adj_angle_deg[i]))) >= cos_thr:
                union(ea, eb)

    root_to_did = {}
    domain_id   = np.empty(E, dtype=np.int32)
    did_counter = 0
    for ei in range(E):
        root = find(ei)
        key  = (int(section_id[ei]), root)
        if key not in root_to_did:
            root_to_did[key] = did_counter
            did_counter += 1
        domain_id[ei] = root_to_did[key]
    return domain_id


# Module-level LRU-style cache: key → domain_id array
# keyed by (odb_id, instance, feature_angle_rounded_or_None)
_DOMAIN_ID_CACHE: Dict[tuple, np.ndarray] = {}
_DOMAIN_CACHE_MAX = 16


def _get_domain_ids(idx, instance: str,
                    feature_angle: Optional[float]) -> Optional[np.ndarray]:
    """
    Return domain_id [E] int32 for unique surface elements.

    feature_angle=None  → section-only partition (no angle split)
    feature_angle=20.0  → default (reads precomputed default_domain_id)
    otherwise           → recompute with union-find using adj_angle_deg
    """
    avd = idx.averaging_data.get(instance)
    if avd is None:
        return None

    default_angle = avd["default_feature_angle_deg"]
    fa_key = None if feature_angle is None else round(float(feature_angle), 4)
    cache_key = (idx.odb_id, instance, fa_key)

    if cache_key in _DOMAIN_ID_CACHE:
        return _DOMAIN_ID_CACHE[cache_key]

    if feature_angle is None:
        # Section-only: each section = one domain, no angle splitting
        sec_id = avd["elem_section_id"]
        _, inv = np.unique(sec_id, return_inverse=True)
        result = inv.astype(np.int32)

    elif fa_key == round(default_angle, 4):
        result = avd["default_domain_id"]

    else:
        # Recompute union-find with new feature_angle (inline)
        result = _union_find_domains(
            avd["elem_section_id"], avd["elem_kind"],
            avd["adj_src"], avd["adj_dst"], avd["adj_angle_deg"],
            feature_angle_deg=float(feature_angle),
        )

    if len(_DOMAIN_ID_CACHE) >= _DOMAIN_CACHE_MAX:
        _DOMAIN_ID_CACHE.pop(next(iter(_DOMAIN_ID_CACHE)))
    _DOMAIN_ID_CACHE[cache_key] = result
    return result


def _en_per_vertex_averaged(
    f_h5, instance: str, frame_idx: int, component_idx: Optional[int],
    src_etype: np.ndarray, src_elem_row: np.ndarray,
    src_local_node_idx: np.ndarray,
    vtx_node_row: np.ndarray, render_indices: np.ndarray,
    domain_id: np.ndarray,
    avg_elem_etype: np.ndarray, avg_elem_row: np.ndarray,
    average_threshold: float = 0.75,
) -> Optional[Tuple[np.ndarray, int, Optional[Tuple[float, float]]]]:
    """
    Read ELEMENT_NODAL data and apply per-domain 75% conditional averaging.

    Returns (scalar_vertex [Nv] float32, num_frames, global_range) or None.
    global_range = (val_min, val_max) from surface post-averaged values only,
    matching Abaqus which bases its legend on the averaged values of the
    visible exterior surface nodes.

    Per domain:
      1. Collect node_row → [values from all elements sharing that node]
      2. domain_range = max(all domain values) - min(all domain values)
      3. For each node: spread > threshold * domain_range → keep original per-elem value
                        else → replace with mean
    """
    Nv = len(vtx_node_row)
    Nt = len(src_elem_row)

    # Build (etype_bytes, elem_row) → global_elem_idx
    elem_to_gidx: Dict[tuple, int] = {
        (avg_elem_etype[i].tobytes(), int(avg_elem_row[i])): i
        for i in range(len(avg_elem_row))
    }

    # Step 1: read raw EN scalars per etype → scalar_en_by_etype[etype_str] = [N_elem, n_local]
    scalar_en_by_etype: Dict[bytes, np.ndarray] = {}
    num_frames = None

    for etype_bytes in np.unique(src_etype):
        etype_str = etype_bytes.decode("ascii").rstrip("\x00")
        grp_path  = f"/ELEMENT_NODAL/{instance}/{etype_str}"
        if grp_path not in f_h5:
            continue
        grp = f_h5[grp_path]

        if "data" in grp:
            ds = grp["data"]
        else:
            sp_keys = sorted(k for k in grp.keys() if k.startswith("sp"))
            if not sp_keys:
                continue
            sp_key = "sp1" if "sp1" in grp else sp_keys[0]
            ds = grp[sp_key]["data"]

        if num_frames is None:
            num_frames = int(ds.shape[0])

        fd = ds[frame_idx]              # [N_elem, n_local, ncomp?]
        while fd.ndim > 3:
            fd = fd.mean(axis=2)
        if fd.ndim == 3:
            sc = _extract_component(
                fd.reshape(-1, fd.shape[-1]), component_idx
            ).reshape(fd.shape[0], fd.shape[1])
        else:
            sc = fd.astype(np.float32)
        scalar_en_by_etype[etype_bytes] = sc

    if not scalar_en_by_etype or num_frames is None:
        return None

    # Step 2: for each triangle corner, look up raw value → build domain samples
    # domain_node_vals[did][node_row] = list of (tri_corner_flat_idx, raw_value)
    # tri_corner_flat_idx = tri * 3 + corner; lets us scatter back to vertices
    domain_node_vals: Dict[int, Dict[int, list]] = defaultdict(lambda: defaultdict(list))
    # vtx_corner_val[vtx] = raw scalar for the specific (er, li) of that vertex
    vtx_corner_val = np.full(Nv, np.nan, dtype=np.float32)

    for tri in range(Nt):
        eb  = src_etype[tri]
        er  = int(src_elem_row[tri])
        sc  = scalar_en_by_etype.get(eb)
        if sc is None:
            continue
        gidx = elem_to_gidx.get((eb.tobytes(), er))
        did  = int(domain_id[gidx]) if (gidx is not None and gidx < len(domain_id)) else -1

        for corner in range(3):
            vtx = int(render_indices[tri, corner])
            li  = int(src_local_node_idx[tri, corner])
            if er >= sc.shape[0] or li >= sc.shape[1]:
                continue
            val = float(sc[er, li])
            vtx_corner_val[vtx] = val          # same val for same vertex regardless of tri
            if did >= 0:
                nr = int(vtx_node_row[vtx])
                domain_node_vals[did][nr].append(val)

    # Step 3: per-domain 75% conditional averaging → node_row → averaged scalar
    node_averaged: Dict[tuple, float] = {}   # (did, node_row) → value

    for did, node_dict in domain_node_vals.items():
        all_v = [v for lst in node_dict.values() for v in lst]
        d_range = max(all_v) - min(all_v) if all_v else 0.0
        for nr, lst in node_dict.items():
            spread = max(lst) - min(lst)
            if d_range < 1e-12 or spread <= average_threshold * d_range:
                node_averaged[(did, nr)] = float(sum(lst) / len(lst))
            # else: keep per-element original (handled via vtx_corner_val below)

    # Step 4: assemble scalar_vertex
    scalar_vertex = vtx_corner_val.copy()   # default: original per-elem values

    for tri in range(Nt):
        eb   = src_etype[tri]
        er   = int(src_elem_row[tri])
        gidx = elem_to_gidx.get((eb.tobytes(), er))
        did  = int(domain_id[gidx]) if (gidx is not None and gidx < len(domain_id)) else -1
        if did < 0:
            continue
        for corner in range(3):
            vtx = int(render_indices[tri, corner])
            nr  = int(vtx_node_row[vtx])
            avg = node_averaged.get((did, nr))
            if avg is not None:
                scalar_vertex[vtx] = avg

    # Step 5: global range from surface post-averaged values only.
    # Abaqus legend = min/max of averaged nodal values on the visible surface.
    # np.isfinite excludes vertices with no matching element data (NaN) so they
    # don't corrupt the range via nan_to_num(nan=0.0) later.
    surf_valid = scalar_vertex[np.isfinite(scalar_vertex)]
    global_range = (float(surf_valid.min()), float(surf_valid.max())) if surf_valid.size > 0 else None
    return scalar_vertex, num_frames, global_range


def _compute_en_global_range(
    result_h5,
    geom_h5_path: str,
    instance: str,
    frame_idx: int,
    component_idx: Optional[int],
    average_threshold: float = 0.75,
) -> Optional[Tuple[float, float]]:
    """
    Compute global legend range from ALL elements (including interior) using
    section-only partitioned 75% conditional averaging.

    Returns (global_min, global_max), or None if section_id data is unavailable.
    """
    if not os.path.exists(geom_h5_path):
        return None

    all_secs  = []
    all_nodes = []
    all_vals  = []

    try:
        with h5py.File(geom_h5_path, 'r') as geom_f:
            if 'elements' not in geom_f:
                return None

            for etype_key in geom_f['elements']:
                grp_path = f'/ELEMENT_NODAL/{instance}/{etype_key}'
                if grp_path not in result_h5:
                    continue

                geom_grp = geom_f['elements'][etype_key]
                if 'conn' not in geom_grp or 'section_id' not in geom_grp:
                    continue

                sec_id   = geom_grp['section_id'][:]   # [N_geom] int32
                conn     = geom_grp['conn'][:]          # [N_geom, n_corner] int32
                N_geom   = len(sec_id)
                n_corner = conn.shape[1]

                res_grp = result_h5[grp_path]
                if 'data' in res_grp:
                    ds = res_grp['data']
                else:
                    sp_keys = sorted(k for k in res_grp.keys() if k.startswith('sp'))
                    if not sp_keys:
                        continue
                    ds = res_grp[sp_keys[0]]['data']

                if frame_idx >= int(ds.shape[0]):
                    continue

                fd = ds[frame_idx]
                while fd.ndim > 3:
                    fd = fd.mean(axis=2)
                if fd.ndim == 3:
                    sc = _extract_component(
                        fd.reshape(-1, fd.shape[-1]), component_idx
                    ).reshape(fd.shape[0], fd.shape[1])
                elif fd.ndim == 2:
                    sc = _extract_component(fd, component_idx)[:, np.newaxis]
                else:
                    sc = fd.astype(np.float32)[:, np.newaxis]

                N_result = sc.shape[0]
                n_local  = sc.shape[1]
                N        = min(N_geom, N_result)
                n_shared = min(n_local, n_corner)
                if N == 0 or n_shared == 0:
                    continue

                sc_sub   = sc[:N, :n_shared].astype(np.float64)
                conn_sub = conn[:N, :n_shared]
                sec_rep  = np.repeat(sec_id[:N], n_shared)
                node_rep = conn_sub.ravel()
                val_rep  = sc_sub.ravel()

                valid = np.isfinite(val_rep)
                if valid.any():
                    all_secs.append(sec_rep[valid].astype(np.int32))
                    all_nodes.append(node_rep[valid].astype(np.int32))
                    all_vals.append(val_rep[valid])
    except Exception:
        logger.exception("_compute_en_global_range: failed reading %s", geom_h5_path)
        return None

    if not all_vals:
        return None

    sec_ids   = np.concatenate(all_secs)
    node_rows = np.concatenate(all_nodes)
    values    = np.concatenate(all_vals)

    # Per-domain raw range (denominator for the 75% threshold)
    dom_sort      = np.argsort(sec_ids, kind='stable')
    sec_dom       = sec_ids[dom_sort]
    val_dom       = values[dom_sort]
    dom_bounds    = np.concatenate([[0],
                                     np.where(sec_dom[1:] != sec_dom[:-1])[0] + 1,
                                     [len(sec_dom)]])
    dom_min_arr   = np.minimum.reduceat(val_dom, dom_bounds[:-1])
    dom_max_arr   = np.maximum.reduceat(val_dom, dom_bounds[:-1])
    dom_range_arr = dom_max_arr - dom_min_arr
    dom_sec_arr   = sec_dom[dom_bounds[:-1]]
    dom_range_map = {int(s): float(r) for s, r in zip(dom_sec_arr, dom_range_arr)}

    # Per-(section, node) stats via compound key
    max_nr      = int(node_rows.max()) + 1
    compound    = sec_ids.astype(np.int64) * max_nr + node_rows.astype(np.int64)
    node_sort   = np.argsort(compound, kind='stable')
    comp_s      = compound[node_sort]
    sec_s       = sec_ids[node_sort]
    val_s       = values[node_sort]

    node_bounds   = np.concatenate([[0],
                                     np.where(comp_s[1:] != comp_s[:-1])[0] + 1,
                                     [len(comp_s)]])
    node_min_arr  = np.minimum.reduceat(val_s, node_bounds[:-1])
    node_max_arr  = np.maximum.reduceat(val_s, node_bounds[:-1])
    node_sum_arr  = np.add.reduceat(val_s, node_bounds[:-1])
    node_cnt_arr  = np.diff(node_bounds).astype(np.float64)
    node_mean_arr = node_sum_arr / node_cnt_arr
    node_spr_arr  = node_max_arr - node_min_arr
    node_sec_arr  = sec_s[node_bounds[:-1]]

    dom_range_per_node = np.array(
        [dom_range_map.get(int(s), 0.0) for s in node_sec_arr], dtype=np.float64
    )

    # 75% threshold: nodes whose spread ≤ 75% of their section domain range are
    # averaged (use mean); the rest keep per-element values.
    # Legend range = min/max of post-averaging values across all sections.
    avg_mask = (
        (dom_range_per_node < 1e-12) |
        (node_spr_arr <= average_threshold * dom_range_per_node)
    )

    g_min, g_max = np.inf, -np.inf
    if avg_mask.any():
        m = node_mean_arr[avg_mask]
        g_min = min(g_min, float(m.min()))
        g_max = max(g_max, float(m.max()))
    if (~avg_mask).any():
        g_min = min(g_min, float(node_min_arr[~avg_mask].min()))
        g_max = max(g_max, float(node_max_arr[~avg_mask].max()))

    if not (np.isfinite(g_min) and np.isfinite(g_max)):
        return None
    return float(g_min), float(g_max)


# ─── frame_deformed_positions ─────────────────────────────────────────────────

def _compute_vertex_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """
    Compute per-vertex normals from deformed positions and triangle index buffer.

    positions : [Nv, 3] float32
    indices   : [Nt, 3] int32
    Returns     [Nv, 3] float32  (unit normals)
    """
    v0 = positions[indices[:, 0]]
    v1 = positions[indices[:, 1]]
    v2 = positions[indices[:, 2]]
    face_normals = np.cross(v1 - v0, v2 - v0)          # [Nt, 3]

    normals = np.zeros_like(positions, dtype=np.float64)
    np.add.at(normals, indices[:, 0], face_normals)
    np.add.at(normals, indices[:, 1], face_normals)
    np.add.at(normals, indices[:, 2], face_normals)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths  = np.where(lengths < 1e-12, 1.0, lengths)
    return (normals / lengths).astype(np.float32)


def frame_deformed_positions(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    scale: float = 1.0,
    result_group: str = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return (deformed_positions [Nv, 3], normals [Nv, 3]) float32.

    Deformed = original_positions + scale * U_per_vertex
    Normals are recomputed from the deformed geometry (server-side, avoids
    expensive JS computeVertexNormals on weak-CPU frontends).

    Requires indexed geometry (vtx_node_row present in ModelIndex).
    """
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' render data not loaded")

    vtx_nr = idx.vtx_node_row.get(instance)
    if vtx_nr is None:
        raise NotFoundError(
            f"Instance '{instance}' has no vtx_node_row; indexed geometry required for deformed shape",
            {"instance": instance},
        )

    render_h5 = os.path.join(idx.workspace, "l2", "render", f"{instance}_render.h5")
    if not os.path.exists(render_h5):
        raise NotFoundError(
            f"Render H5 not found for instance '{instance}'",
            {"instance": instance},
        )

    with h5py.File(render_h5, "r") as f:
        positions = np.ascontiguousarray(f["render/positions"][:], dtype=np.float32)
        indices   = np.ascontiguousarray(f["render/indices"][:],   dtype=np.int32) \
                    if "render/indices" in f else None

    h5_path = _manifest_result_h5_path(idx.workspace, step, "U", result_group)
    if not os.path.exists(h5_path):
        raise NotFoundError(
            f"U field not found for step='{step}'",
            {"step": step, "field": "U"},
        )

    with h5py.File(h5_path, "r") as f:
        ds_path = f"/NODAL/{instance}/data"
        if ds_path not in f:
            raise NotFoundError(
                f"No NODAL U data for instance '{instance}'",
                {"instance": instance},
            )
        ds = f[ds_path]
        num_frames = ds.shape[0]
        if frame_idx >= num_frames:
            raise ValidationError(
                f"frame_idx {frame_idx} out of range [0, {num_frames})",
                {"frame_idx": frame_idx},
            )
        disp_node = ds[frame_idx].astype(np.float32)   # [N_nodes, 3]

    if disp_node.ndim == 1:
        raise ValidationError(
            "U field is scalar; expected 3-component vector",
            {"instance": instance},
        )

    # Map node displacement to render vertices, pad if sparse
    n_nodes = disp_node.shape[0]
    max_nr  = int(vtx_nr.max())
    if max_nr >= n_nodes:
        padded = np.zeros((max_nr + 1, disp_node.shape[1]), dtype=np.float32)
        padded[:n_nodes] = disp_node
        disp_node = padded

    disp_vertex = disp_node[vtx_nr]                             # [Nv, 3]
    deformed    = (positions + np.float32(scale) * disp_vertex).astype(np.float32)

    normals = _compute_vertex_normals(deformed, indices) if indices is not None \
              else np.zeros_like(deformed)

    return deformed, normals
