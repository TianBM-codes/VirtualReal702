"""
Pick and BBox query logic.
"""
import logging
import os
import numpy as np
from typing import List, Optional

import h5py

from ..core.errors import NotFoundError, NotReadyError, ValidationError
from ..core.state import OdbRegistry
from ..infra.hdf5_repo import HDF5Repo
from ..infra.manifest_repo import ManifestRepo
from ..schemas.query import PickOdbInfo, PickResponse, PickResultInfo, BBoxResponse, RenderFacesResponse, NearestFaceResponse, SurfacePatchRequest, SurfacePatchResponse, RayPickRequest

logger = logging.getLogger(__name__)


def _get_ready_index(registry: OdbRegistry, odb_id: str):
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' is not render-ready yet", {"odb_id": odb_id})
    return idx


def _octree_candidates(octree: dict, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """
    Traverse the octree and return render face indices from all leaves whose
    bounding box overlaps [lo, hi].  Uses iterative DFS to avoid Python recursion
    limits on deep trees.
    """
    node_bbox     = octree["node_bbox"]      # [N, 6] float32
    node_children = octree["node_children"]  # [N, 8] int32
    node_is_leaf  = octree["node_is_leaf"]   # [N]    uint8
    face_indices  = octree["face_indices"]   # [F]    int32
    leaf_offsets  = octree["leaf_offsets"]   # [N+1]  int32

    parts = []
    stack = [0]
    while stack:
        nid = stack.pop()
        bb = node_bbox[nid]
        # AABB overlap: node fully outside query box → skip
        if bb[3] < lo[0] or bb[4] < lo[1] or bb[5] < lo[2]:
            continue
        if bb[0] > hi[0] or bb[1] > hi[1] or bb[2] > hi[2]:
            continue
        if node_is_leaf[nid]:
            s = int(leaf_offsets[nid])
            e = int(leaf_offsets[nid + 1])
            if e > s:
                parts.append(face_indices[s:e])
        else:
            for child in node_children[nid]:
                if child != -1:
                    stack.append(int(child))

    if not parts:
        return np.zeros(0, dtype=np.int32)
    return np.unique(np.concatenate(parts))


def _composite_elem_key(
    elem_row: np.ndarray,
    etype_arr: Optional[np.ndarray],
    max_er: Optional[int] = None,
    etype_unique: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Build a globally-unique integer key per (etype, elem_row) pair.

    source_elem_row stores per-etype-local row numbers (each etype starts at 0),
    so elem_row alone is NOT unique across etypes within an instance.
    We encode (etype_idx, elem_row) → single int64 to get a true unique key.

    etype_arr may be a string array (e.g. b'S4', b'C3D8R').  We map strings to
    stable integer indices via searchsorted against etype_unique.

    For cross-call consistency (subset vs full array), pass the same etype_unique
    and max_er to both calls so np.isin() can match keys correctly.
    """
    if etype_arr is None:
        return elem_row.astype(np.int64)
    # Map etype strings/values → stable integer indices
    if etype_arr.dtype.kind in ('S', 'U', 'O'):
        uniq = etype_unique if etype_unique is not None else np.unique(etype_arr)
        et_idx = np.searchsorted(uniq, etype_arr).astype(np.int64)
    else:
        et_idx = etype_arr.astype(np.int64)
    if max_er is None:
        max_er = int(elem_row.max()) + 1 if len(elem_row) > 0 else 1
    return et_idx * max_er + elem_row.astype(np.int64)


# Named component → column index for well-known displacement components.
_COMP_IDX = {"U1": 0, "U2": 1, "U3": 2}


def _resolve_comp(
    component: Optional[str],
    component_idx: Optional[int],
) -> tuple:
    """
    Returns (ci: Optional[int], use_magnitude: bool).

    Priority:
      1. component_idx (explicit column index) — takes precedence over name.
      2. component name: USUM → magnitude, U1/U2/U3 → mapped index.
      3. Unknown name without component_idx → ValidationError with hint.

    Rationale: non-displacement fields (S, E, ...) have field-specific component
    orderings (S11=0, S22=1, ...) that aren't in the global name map.  The
    frontend is expected to pass component_idx for such fields.
    """
    if component_idx is not None:
        return component_idx, False
    if component is None:
        return None, False
    if component == "USUM":
        return None, True
    if component in _COMP_IDX:
        return _COMP_IDX[component], False
    raise ValidationError(
        f"Unknown component '{component}'. Pass component_idx=<int> for non-displacement fields "
        f"(e.g. S11→0, S22→1). Known names: {sorted(_COMP_IDX) + ['USUM']}",
        {"component": component},
    )


def _result_h5_path(workspace: str, step: str, field: str,
                    result_group: str = None) -> str:
    def safe(s):
        return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    fname = f"{safe(step)}__{safe(field)}.h5"
    if result_group:
        return os.path.join(workspace, "l1", "results", safe(result_group), fname)
    return os.path.join(workspace, "l1", "results", fname)


def _read_u_displacement(
    workspace: str, instance: str, node_row: int, frame_idx: int, step: str,
    result_group: str = None,
) -> Optional[List[float]]:
    """
    Read NODAL displacement [U1, U2, U3] for the given node at the given frame.
    Returns None if U field or NODAL dataset is unavailable.
    """
    u_path = _result_h5_path(workspace, step, "U", result_group)
    if not os.path.exists(u_path):
        return None
    try:
        with h5py.File(u_path, "r") as f:
            ds_path = f"/NODAL/{instance}/data"
            if ds_path not in f:
                return None
            ds = f[ds_path]
            if frame_idx >= ds.shape[0] or node_row >= ds.shape[1]:
                return None
            u_vec = np.asarray(ds[frame_idx, node_row])
            return u_vec[:3].tolist()
    except Exception:
        logger.warning("Could not read U displacement for def_coords", exc_info=True)
        return None


def _compute_mises(
    workspace: str, instance: str, elem_row: int, etype_str: str,
    frame_idx: int, step: str,
    result_group: str = None,
) -> Optional[float]:
    """
    Compute Von Mises stress for the hit element at the given frame.
    Reads S field (INTEGRATION_POINT preferred, then ELEMENT_NODAL), averages
    over integration points, then applies the Von Mises formula.
    Returns None if S field is unavailable or has fewer than 3 components.
    Component order assumed: [S11, S22, S33, S12, S13, S23].
    Shell models store only 4 components [S11, S22, S33, S12]; S13/S23 are treated as 0.
    """
    s_path = _result_h5_path(workspace, step, "S", result_group)
    if not os.path.exists(s_path):
        return None
    try:
        with h5py.File(s_path, "r") as f:
            for position in ("INTEGRATION_POINT", "ELEMENT_NODAL"):
                ds_path = f"/{position}/{instance}/{etype_str}/data"
                if ds_path not in f:
                    continue
                ds = f[ds_path]
                if frame_idx >= ds.shape[0]:
                    return None
                elem_data = np.asarray(ds[frame_idx, elem_row])
                # Average over all leading dims (integration points) until shape is 1-D
                while elem_data.ndim > 1:
                    elem_data = elem_data.mean(axis=0)
                if elem_data.ndim == 0 or len(elem_data) < 3:
                    return None
                s11, s22, s33 = float(elem_data[0]), float(elem_data[1]), float(elem_data[2])
                # Shell elements store only [S11, S22, S33, S12]; S13/S23 default to 0.
                s12 = float(elem_data[3]) if len(elem_data) > 3 else 0.0
                s13 = float(elem_data[4]) if len(elem_data) > 4 else 0.0
                s23 = float(elem_data[5]) if len(elem_data) > 5 else 0.0
                mises = float(np.sqrt(0.5 * (
                    (s11 - s22) ** 2 + (s22 - s33) ** 2 + (s33 - s11) ** 2
                    + 6.0 * (s12 ** 2 + s13 ** 2 + s23 ** 2)
                )))
                return mises
    except Exception:
        logger.warning("Could not compute S,Mises for pick", exc_info=True)
    return None


def _find_node_pos_in_elem(
    workspace: str, instance: str, etype_str: str, elem_row: int, node_row: int
) -> Optional[int]:
    """
    Return the 0-based position of node_row within the element's corner connectivity.
    Used to index into ELEMENT_NODAL data (first extra dim = corner node order).
    Returns None if the geometry file is unavailable or the node is not found.
    """
    geom_h5 = os.path.join(workspace, "l1", "geometry", f"{instance}.h5")
    try:
        with h5py.File(geom_h5, "r") as f:
            conn = np.asarray(f[f"elements/{etype_str}/conn"][elem_row])
            valid = conn[conn >= 0]  # strip sentinel -1 padding
            matches = np.where(valid == node_row)[0]
            return int(matches[0]) if len(matches) > 0 else None
    except Exception:
        return None


def _read_pick_result(
    workspace: str,
    instance: str,
    face_node_rows: List[int],          # for NODAL element pick (all 3 face corners)
    elem_row: int,                       # for ELEMENT_NODAL / INTEGRATION_POINT path
    etype_str: str,                      # for ELEMENT_NODAL / INTEGRATION_POINT path
    frame_idx: int,
    step: str,
    field: str,
    component: Optional[str],
    component_idx: Optional[int],
    pick_mode: str,
    selected_node_row: Optional[int] = None,  # node pick: the specific row chosen by node_idx
    result_group: str = None,
) -> Optional[PickResultInfo]:
    """
    Read pick result values, trying NODAL → ELEMENT_NODAL → INTEGRATION_POINT.

    NODAL:
      element pick → raw_values (one per face corner node) + display_value (mean)
      node pick    → raw_value for the single node identified by selected_node_row
                     (falls back to face_node_rows[0] when selected_node_row is None)

    ELEMENT_NODAL / INTEGRATION_POINT (fallback when NODAL absent):
      Returns a single display_value averaged over the integration/nodal points of
      the element.  Handles stress, strain, and other element-level fields.
    """
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )

    h5_path = _result_h5_path(workspace, step, field, result_group)
    if not os.path.exists(h5_path):
        return None

    ci, use_magnitude = _resolve_comp(component, component_idx)

    def _scalar(row_data) -> float:
        """Extract scalar from a 1-D or vector row."""
        if row_data.ndim == 0:
            return float(row_data)
        if use_magnitude:
            return float(np.linalg.norm(row_data))
        if ci is not None and ci < len(row_data):
            return float(row_data[ci])
        return float(np.linalg.norm(row_data))

    try:
        with h5py.File(h5_path, "r") as f:
            # ── Try NODAL ────────────────────────────────────────────────────
            ds_path = f"/NODAL/{instance}/data"
            if ds_path in f:
                ds = f[ds_path]
                nf = ds.shape[0]
                if frame_idx >= nf:
                    return None
                fd = ds[frame_idx]   # [N] or [N, ncomp]
                if pick_mode == "node":
                    node_rows_to_read = (
                        [selected_node_row] if selected_node_row is not None
                        else face_node_rows[:1]
                    )
                else:
                    node_rows_to_read = face_node_rows
                def _nval(r):
                    if fd.ndim == 1:
                        return float(fd[r])
                    return _scalar(fd[r])
                values = [_nval(r) for r in node_rows_to_read]
                if len(values) == 1:
                    return PickResultInfo(field=field, position="NODAL",
                                         component=component, raw_value=values[0])
                return PickResultInfo(field=field, position="NODAL",
                                      component=component, raw_values=values,
                                      display_value=float(np.mean(values)))

            # ── Try ELEMENT_NODAL then INTEGRATION_POINT ─────────────────────
            for position in ("ELEMENT_NODAL", "INTEGRATION_POINT"):
                ds_path = f"/{position}/{instance}/{etype_str}/data"
                if ds_path not in f:
                    continue
                ds = f[ds_path]
                nf = ds.shape[0]
                if frame_idx >= nf:
                    return None
                # Shape: [N_elem, ...extra_dims..., ncomp] or [N_elem, ncomp] or [N_elem]
                elem_data = np.asarray(ds[frame_idx, elem_row])

                # ELEMENT_NODAL in node mode: extract the specific node's extrapolated value.
                # The first extra dim corresponds to element corner nodes in conn order.
                if (position == "ELEMENT_NODAL"
                        and pick_mode == "node"
                        and selected_node_row is not None
                        and elem_data.ndim >= 1):
                    node_pos = _find_node_pos_in_elem(workspace, instance, etype_str,
                                                      elem_row, selected_node_row)
                    if node_pos is not None and node_pos < elem_data.shape[0]:
                        node_val = _scalar(elem_data[node_pos]) if elem_data[node_pos].ndim >= 1 \
                                   else float(elem_data[node_pos])
                        return PickResultInfo(field=field, position=position,
                                              component=component, raw_value=node_val)

                # Average over all leading dims (IPs / nodes) until 1-D, then extract scalar
                while hasattr(elem_data, "ndim") and elem_data.ndim > 1:
                    elem_data = elem_data.mean(axis=0)
                display = _scalar(elem_data) if hasattr(elem_data, "__len__") else float(elem_data)
                return PickResultInfo(field=field, position=position,
                                      component=component, display_value=display)

    except (ValidationError, NotFoundError):
        raise
    except Exception:
        logger.warning("Could not read pick result", exc_info=True)

    return None


def pick(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    render_face_idx: int,
    pick_mode: str = "element",     # "element" | "node"
    step: Optional[str] = None,
    field: Optional[str] = None,
    frame_idx: Optional[int] = None,
    component: Optional[str] = None,
    component_idx: Optional[int] = None,  # explicit column index, overrides component name
    node_idx: Optional[int] = None,       # node pick only: which candidate node (0-2)
    include_coords: bool = False,         # when True: return orig_coords and def_coords
    deform_scale: float = 1.0,           # scale factor for def_coords = orig + U * scale
    result_group: str = None,
) -> PickResponse:
    idx = _get_ready_index(registry, odb_id)

    # ── Resolve render_face_idx → etype + elem_row ──────────────────────────
    src_map = idx.render_source_elem_row.get(instance)
    if src_map is None:
        raise NotFoundError(
            f"Instance '{instance}' not found in ODB '{odb_id}'",
            {"instance": instance},
        )
    if render_face_idx < 0 or render_face_idx >= len(src_map):
        raise ValidationError(
            f"render_face_idx {render_face_idx} out of range [0, {len(src_map)})",
            {"render_face_idx": render_face_idx},
        )

    elem_row = int(src_map[render_face_idx])
    etype_arr = idx.source_elem_etype.get(instance)
    if etype_arr is None or render_face_idx >= len(etype_arr):
        raise ValidationError(
            f"No etype info for instance '{instance}' face {render_face_idx}",
            {"instance": instance, "render_face_idx": render_face_idx},
        )
    etype_key = etype_arr[render_face_idx]
    etype_str = etype_key.decode("ascii").rstrip("\x00")

    # ── ODB label lookup ────────────────────────────────────────────────────
    hdf5_repo = HDF5Repo(idx.workspace)
    elem_label = hdf5_repo.get_elem_label(instance, etype_str, elem_row)
    elem_node_labels = hdf5_repo.get_node_labels_for_elem(instance, etype_str, elem_row)

    # ── All render faces belonging to the same (etype, elem_row) ────────────
    # elem_row is per-etype-local, so must filter by both.
    elem_face_indices = np.where(
        (src_map == elem_row) & (etype_arr == etype_key)
    )[0].tolist()

    # ── Face corner node rows (needed for both result reading and node pick) ─
    node_rows_map = idx.source_node_rows.get(instance)
    face_node_rows: Optional[List[int]] = None
    if node_rows_map is not None and render_face_idx < len(node_rows_map):
        face_node_rows = node_rows_map[render_face_idx].tolist()

    # ── Build candidate node labels (needed before result reading for node pick) ─
    candidate_labels: Optional[List[int]] = None
    selected_node_row: Optional[int] = None
    if pick_mode == "node" and face_node_rows is not None:
        candidate_labels = hdf5_repo.get_node_labels_for_rows(instance, face_node_rows)
        # node_idx tells the backend which of the 3 face nodes the user selected.
        # The frontend determines this via screen-space raycasting (nearest vertex).
        # If not provided, defaults to 0 — first corner node of the face.
        chosen = node_idx if node_idx is not None else 0
        chosen = min(chosen, len(face_node_rows) - 1)
        selected_node_row = face_node_rows[chosen]

    # ── Result reading (NODAL → ELEMENT_NODAL → INTEGRATION_POINT) ─────────
    result_info: Optional[PickResultInfo] = None
    if step and field and frame_idx is not None and face_node_rows is not None:
        result_info = _read_pick_result(
            workspace=idx.workspace,
            instance=instance,
            face_node_rows=face_node_rows,
            elem_row=elem_row,
            etype_str=etype_str,
            frame_idx=frame_idx,
            step=step,
            field=field,
            component=component,
            component_idx=component_idx,
            pick_mode=pick_mode,
            selected_node_row=selected_node_row,
            result_group=result_group,
        )

    # ── Coords (orig + deformed) for node mode ──────────────────────────────
    orig_coords: Optional[List[float]] = None
    def_coords: Optional[List[float]] = None
    if include_coords and pick_mode == "node" and selected_node_row is not None:
        try:
            orig_coords = hdf5_repo.get_node_coords(instance, selected_node_row)
            if step and frame_idx is not None:
                u = _read_u_displacement(idx.workspace, instance, selected_node_row,
                                         frame_idx, step, result_group)
                if u is not None:
                    def_coords = [orig_coords[i] + u[i] * deform_scale for i in range(3)]
        except Exception:
            logger.warning("Could not read coords for pick", exc_info=True)

    # ── S,Mises (always computed from hit element if S field available) ──────
    mises: Optional[float] = None
    if step and frame_idx is not None:
        mises = _compute_mises(idx.workspace, instance, elem_row, etype_str,
                               frame_idx, step, result_group)

    # ── Patch result with source_elem_label ──────────────────────────────────
    if result_info is not None:
        result_info.source_elem_label = elem_label

    # ── Attached element labels (node mode; from CSR index in geometry HDF5) ──
    attached_elem_labels: Optional[List[int]] = None
    if pick_mode == "node" and selected_node_row is not None:
        try:
            attached_elem_labels = hdf5_repo.get_attached_elem_labels(instance, selected_node_row)
        except Exception:
            logger.warning("Could not read attached_elem_labels for pick", exc_info=True)

    # ── Build ODB info ───────────────────────────────────────────────────────
    if pick_mode == "node":
        if candidate_labels is None:
            candidate_labels = elem_node_labels[:3]
        selected_label = (
            candidate_labels[node_idx if node_idx is not None else 0]
            if candidate_labels else None
        )
        odb_info = PickOdbInfo(
            node_label=selected_label,
            elem_label=elem_label,
            candidate_node_labels=candidate_labels,
            orig_coords=orig_coords,
            def_coords=def_coords,
            attached_elem_labels=attached_elem_labels,
        )
    else:
        odb_info = PickOdbInfo(
            elem_label=elem_label,
            elem_type=etype_str,
            elem_node_labels=elem_node_labels,
        )

    return PickResponse(
        pick_mode=pick_mode,
        instance=instance,
        render_face_idx=render_face_idx,
        render_face_indices=elem_face_indices,
        odb=odb_info,
        result=result_info,
        mises=mises,
    )


def bbox(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    bbox_min: list,
    bbox_max: list,
    mode: str = "intersect",
    set_name: Optional[str] = None,
) -> BBoxResponse:
    idx = _get_ready_index(registry, odb_id)

    coords = idx.coords_global.get(instance)
    if coords is None:
        raise NotFoundError(
            f"Instance '{instance}' has no geometry in ODB '{odb_id}'",
            {"instance": instance},
        )
    src_map = idx.render_source_elem_row.get(instance)
    if src_map is None:
        raise NotFoundError(
            f"Instance '{instance}' has no render map in ODB '{odb_id}'",
            {"instance": instance},
        )

    lo = np.array(bbox_min, dtype=np.float32)
    hi = np.array(bbox_max, dtype=np.float32)

    node_rows = idx.source_node_rows.get(instance)
    if node_rows is None:
        raise NotFoundError(
            f"Instance '{instance}' has no node row map in ODB '{odb_id}'",
            {"instance": instance},
        )

    # Octree coarse filter: only examine faces whose leaf bbox overlaps the query box.
    # Falls back to full scan if octree was not loaded.
    octree = idx.octree.get(instance)
    if octree is not None:
        candidate_ridx = _octree_candidates(octree, lo, hi)  # [C] render face indices
    else:
        logger.warning("ODB %s instance %s: no octree, falling back to full scan", odb_id, instance)
        candidate_ridx = np.arange(len(node_rows), dtype=np.int32)

    if len(candidate_ridx) == 0:
        return BBoxResponse(set_name=None, elem_count=0, render_face_count=0)

    # Precise per-vertex containment check on candidates only.
    cand_node_rows = node_rows[candidate_ridx]   # [C, 3]
    verts = coords[cand_node_rows]               # [C, 3, 3]

    inside = (verts >= lo) & (verts <= hi)
    per_vertex = inside.all(axis=-1)

    if mode == "intersect":
        face_mask = per_vertex.any(axis=-1)
    else:
        face_mask = per_vertex.all(axis=-1)

    render_rows = candidate_ridx[np.where(face_mask)[0]].astype(np.int32)
    if len(render_rows) == 0:
        return BBoxResponse(set_name=None, elem_count=0, render_face_count=0)

    elem_rows = src_map[render_rows].astype(np.int32)
    etype_arr = idx.source_elem_etype.get(instance)
    etype_rows_all = etype_arr[render_rows] if etype_arr is not None else None

    # Unique elements by composite (etype, elem_row) key.
    # Keep unique_idx so stored arrays are 1-to-1 with elem_count.
    composite = _composite_elem_key(elem_rows, etype_rows_all)
    _, unique_idx = np.unique(composite, return_index=True)
    unique_elem_rows  = elem_rows[unique_idx]
    unique_etype_rows = etype_rows_all[unique_idx] if etype_rows_all is not None else None
    unique_elem_count = len(unique_idx)

    saved_name: Optional[str] = None
    if set_name:
        manifest = ManifestRepo(idx.workspace)
        manifest.save_user_set(
            set_name=set_name,
            total_elem_count=unique_elem_count,
            instance_data=[
                {
                    "instance_name": instance,
                    "elem_count":    unique_elem_count,
                    "render_rows":   render_rows,          # [Rf_selected] — all matched faces
                    "elem_rows":     unique_elem_rows,     # [elem_count]  — one per unique element
                    "etype_rows":    unique_etype_rows,    # [elem_count]  — paired with elem_rows
                }
            ],
        )
        saved_name = set_name

    # Batch-read element labels (capped at 2000 to keep response fast).
    elem_labels_out: Optional[List[int]] = None
    if unique_elem_count > 0 and unique_elem_count <= 2000 and unique_etype_rows is not None:
        try:
            geom_h5 = os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
            collected: List[int] = []
            with h5py.File(geom_h5, "r") as f:
                for et_key in np.unique(unique_etype_rows):
                    et_str = et_key.decode("ascii").rstrip("\x00")
                    mask = unique_etype_rows == et_key
                    rows = unique_elem_rows[mask]
                    labels_arr = f[f"elements/{et_str}/labels"][:]
                    collected.extend(int(labels_arr[r]) for r in rows)
            collected.sort()
            elem_labels_out = collected
        except Exception:
            logger.warning("Could not look up elem labels for bbox", exc_info=True)

    return BBoxResponse(
        set_name=saved_name,
        elem_count=unique_elem_count,
        render_face_count=len(render_rows),
        elem_labels=elem_labels_out,
    )


def _batch_elem_labels(
    workspace: str,
    instance: str,
    unique_elem_rows: np.ndarray,
    unique_etype_rows: Optional[np.ndarray],
) -> Optional[List[int]]:
    """Batch-read element labels from L1 HDF5, grouped by etype. Returns sorted list."""
    if unique_etype_rows is None:
        return None
    try:
        geom_h5 = os.path.join(workspace, "l1", "geometry", f"{instance}.h5")
        out: List[int] = []
        with h5py.File(geom_h5, "r") as f:
            for et_key in np.unique(unique_etype_rows):
                et_str = et_key.decode("ascii").rstrip("\x00")
                mask = unique_etype_rows == et_key
                rows = unique_elem_rows[mask]
                labels_arr = f[f"elements/{et_str}/labels"][:]
                out.extend(int(labels_arr[r]) for r in rows)
        out.sort()
        return out
    except Exception:
        logger.warning("Could not batch-read elem labels", exc_info=True)
        return None


def _point_to_triangles_sq_dist(
    p: np.ndarray,
    tris: np.ndarray,
) -> tuple:
    """
    Vectorized minimum squared distance from point p to each of N triangles.

    Based on Ericson "Real-Time Collision Detection" §5.1.5 (Voronoi region method).
    Handles all 7 regions (3 vertices, 3 edges, interior) without branching loops.

    Parameters
    ----------
    p    : (3,) float64 — query point
    tris : (N, 3, 3) float64 — N triangles, each with 3 vertices × 3 coords

    Returns
    -------
    sq_dists      : (N,) float64 — squared distances
    closest_points: (N, 3) float64 — closest point on each triangle to p
    """
    A = tris[:, 0]  # (N, 3)
    B = tris[:, 1]
    C = tris[:, 2]

    AB = B - A   # (N, 3) edge vectors from A
    AC = C - A

    AP = p - A   # (N, 3) — note: p broadcasts over N
    BP = p - B
    CP = p - C

    d1 = (AB * AP).sum(axis=1)   # dot(AB, AP), shape (N,)
    d2 = (AC * AP).sum(axis=1)
    d3 = (AB * BP).sum(axis=1)
    d4 = (AC * BP).sum(axis=1)
    d5 = (AB * CP).sum(axis=1)
    d6 = (AC * CP).sum(axis=1)

    # Auxiliary quantities used to determine which Voronoi region the point is in
    vc = d1 * d4 - d3 * d2   # proportional to bary-w for edge AB region
    vb = d5 * d2 - d1 * d6   # proportional to bary-w for edge AC region
    va = d3 * d6 - d5 * d4   # proportional to bary-w for edge BC region

    N = len(tris)
    closest = np.empty((N, 3), dtype=np.float64)
    assigned = np.zeros(N, dtype=bool)

    # ── Region A (closest to vertex A) ──────────────────────────────────────
    rA = (d1 <= 0.0) & (d2 <= 0.0)
    closest[rA] = A[rA]
    assigned |= rA

    # ── Region B (closest to vertex B) ──────────────────────────────────────
    rB = ~assigned & (d3 >= 0.0) & (d4 <= d3)
    closest[rB] = B[rB]
    assigned |= rB

    # ── Region C (closest to vertex C) ──────────────────────────────────────
    rC = ~assigned & (d6 >= 0.0) & (d5 <= d6)
    closest[rC] = C[rC]
    assigned |= rC

    # ── Region AB (closest to edge AB) ──────────────────────────────────────
    rAB = ~assigned & (vc <= 0.0) & (d1 >= 0.0) & (d3 <= 0.0)
    denom_AB = d1 - d3
    t_AB = np.where(np.abs(denom_AB) > 1e-30, d1 / denom_AB, 0.5)
    closest[rAB] = A[rAB] + t_AB[rAB, None] * AB[rAB]
    assigned |= rAB

    # ── Region AC (closest to edge AC) ──────────────────────────────────────
    rAC = ~assigned & (vb <= 0.0) & (d2 >= 0.0) & (d6 <= 0.0)
    denom_AC = d2 - d6
    t_AC = np.where(np.abs(denom_AC) > 1e-30, d2 / denom_AC, 0.5)
    closest[rAC] = A[rAC] + t_AC[rAC, None] * AC[rAC]
    assigned |= rAC

    # ── Region BC (closest to edge BC) ──────────────────────────────────────
    rBC = ~assigned & (va <= 0.0) & ((d4 - d3) >= 0.0) & ((d5 - d6) >= 0.0)
    denom_BC = (d4 - d3) + (d5 - d6)
    t_BC = np.where(np.abs(denom_BC) > 1e-30, (d4 - d3) / denom_BC, 0.5)
    closest[rBC] = B[rBC] + t_BC[rBC, None] * (C[rBC] - B[rBC])
    assigned |= rBC

    # ── Interior (projection onto triangle plane) ────────────────────────────
    rInt = ~assigned
    denom_int = va + vb + vc
    safe_denom = np.where(np.abs(denom_int) > 1e-30, denom_int, 1.0)
    bary_v = vb / safe_denom
    bary_w = vc / safe_denom
    bary_u = 1.0 - bary_v - bary_w
    closest[rInt] = (
        bary_u[rInt, None] * A[rInt]
        + bary_v[rInt, None] * B[rInt]
        + bary_w[rInt, None] * C[rInt]
    )

    diff = p - closest          # (N, 3)
    sq_dists = (diff * diff).sum(axis=1)  # (N,)
    return sq_dists, closest


def _triangle_normal(v0: np.ndarray, v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    """Return the unit normal of a triangle defined by three vertex positions."""
    n = np.cross(v1 - v0, v2 - v0)
    length = float(np.linalg.norm(n))
    if length < 1e-30:
        return np.array([0.0, 0.0, 1.0])
    return n / length


def nearest_face(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    point: List[float],
) -> NearestFaceResponse:
    """
    Find the surface triangle face closest to an arbitrary 3-D point.

    The point does NOT need to be a mesh node or element centroid — it can be
    anywhere in space.  Uses the octree for acceleration, then falls back to a
    brute-force scan if the octree is absent.

    Algorithm
    ---------
    1. Query the octree with a zero-size AABB at the query point (= find the leaf
       that contains/is nearest to the point).  Expand the box until candidates are
       found (handles points outside the mesh).
    2. Compute exact point-to-triangle distances for all candidates and find d_min.
    3. Re-query with a box of radius d_min — any triangle that could be closer
       must have at least one vertex inside this box, so this guarantees the
       global optimum without scanning every triangle.
    4. Return the best face, its unit normal, and the closest point on that face.
    """
    idx = _get_ready_index(registry, odb_id)

    coords = idx.coords_global.get(instance)
    node_rows = idx.source_node_rows.get(instance)
    if coords is None or node_rows is None:
        raise NotFoundError(
            f"Instance '{instance}' has no L2 geometry in ODB '{odb_id}'",
            {"instance": instance},
        )

    p = np.asarray(point, dtype=np.float64)
    octree = idx.octree.get(instance)

    # ── Step 1: initial candidates via octree ────────────────────────────────
    if octree is not None:
        model_diag = float(np.linalg.norm(
            coords.max(axis=0).astype(np.float64)
            - coords.min(axis=0).astype(np.float64)
        ))
        radius = max(model_diag * 0.005, 1e-6)   # start at 0.5 % of model diagonal
        candidates = np.zeros(0, dtype=np.int32)
        pf = p.astype(np.float32)
        for _ in range(20):                        # at most 20 doublings ≈ ×1M expansion
            lo = pf - np.float32(radius)
            hi = pf + np.float32(radius)
            candidates = _octree_candidates(octree, lo, hi)
            if len(candidates) > 0:
                break
            radius *= 2.0
    else:
        logger.warning(
            "ODB %s instance %s: no octree, falling back to full face scan", odb_id, instance
        )
        candidates = np.arange(len(node_rows), dtype=np.int32)

    if len(candidates) == 0:
        raise NotFoundError(
            "No surface faces found near the query point — model may be empty",
            {"point": point},
        )

    # ── Step 2: exact distances for initial candidates ───────────────────────
    cand_tris = coords[node_rows[candidates]].astype(np.float64)  # (C, 3, 3)
    sq_dists, closest_pts = _point_to_triangles_sq_dist(p, cand_tris)
    best_local = int(np.argmin(sq_dists))
    d_min = float(np.sqrt(sq_dists[best_local]))
    best_face = int(candidates[best_local])
    best_closest = closest_pts[best_local]

    # ── Step 3: re-query with expanded box to guarantee global optimum ───────
    if octree is not None and d_min > 0.0:
        lo = (p - d_min * 1.001).astype(np.float32)
        hi = (p + d_min * 1.001).astype(np.float32)
        expanded = _octree_candidates(octree, lo, hi)
        new_mask = ~np.isin(expanded, candidates)
        new_cands = expanded[new_mask]
        if len(new_cands) > 0:
            new_tris = coords[node_rows[new_cands]].astype(np.float64)
            new_sq, new_closest = _point_to_triangles_sq_dist(p, new_tris)
            new_best = int(np.argmin(new_sq))
            if new_sq[new_best] < sq_dists[best_local]:
                best_face = int(new_cands[new_best])
                d_min = float(np.sqrt(new_sq[new_best]))
                best_closest = new_closest[new_best]

    # ── Step 4: compute face normal and ODB metadata ─────────────────────────
    face_verts = coords[node_rows[best_face]].astype(np.float64)  # (3, 3)
    normal = _triangle_normal(face_verts[0], face_verts[1], face_verts[2])

    src_map = idx.render_source_elem_row.get(instance)
    etype_arr = idx.source_elem_etype.get(instance)
    elem_label: Optional[int] = None
    etype_str: Optional[str] = None

    if src_map is not None and best_face < len(src_map):
        elem_row = int(src_map[best_face])
        if etype_arr is not None and best_face < len(etype_arr):
            etype_str = etype_arr[best_face].decode("ascii").rstrip("\x00")
        try:
            hdf5_repo = HDF5Repo(idx.workspace)
            elem_label = hdf5_repo.get_elem_label(instance, etype_str or "", elem_row)
        except Exception:
            logger.warning("Could not look up elem_label for nearest_face", exc_info=True)

    return NearestFaceResponse(
        instance=instance,
        render_face_idx=best_face,
        elem_label=elem_label,
        elem_type=etype_str,
        normal=normal.tolist(),
        closest_point=best_closest.tolist(),
        distance=d_min,
    )


def _unproject_ray(
    screen_x: float,
    screen_y: float,
    viewport_width: float,
    viewport_height: float,
    vp_col_major: List[float],
) -> tuple:
    """
    Reconstruct a world-space ray from screen pixel coordinates.

    vp_col_major is the combined projection×view matrix sent by Three.js as
    [...matrix.elements] (16 floats, column-major order).

    NDC convention: X ∈ [-1,+1] left→right, Y ∈ [-1,+1] bottom→top (Y flipped
    because screen Y grows downward).  Z = -1 at near plane, +1 at far plane.

    Returns
    -------
    origin    : (3,) float64 — ray origin in world space (near-plane unproject)
    direction : (3,) float64 — unit ray direction
    """
    ndc_x = 2.0 * screen_x / viewport_width - 1.0
    ndc_y = 1.0 - 2.0 * screen_y / viewport_height   # flip Y

    # Three.js elements[] is column-major.
    # reshape(4,4) gives arr[i,j] = elements[i*4+j] = M[j][i], i.e. arr = M^T.
    vp_col = np.array(vp_col_major, dtype=np.float64).reshape(4, 4)
    VP = vp_col.T   # actual view-projection matrix (row-major)

    try:
        VP_inv = np.linalg.inv(VP)
    except np.linalg.LinAlgError:
        raise ValidationError(
            "view_projection_matrix is singular and cannot be inverted",
            {"matrix": vp_col_major},
        )

    def _unproj(z_ndc: float) -> np.ndarray:
        clip = np.array([ndc_x, ndc_y, z_ndc, 1.0])
        world = VP_inv @ clip
        if abs(world[3]) < 1e-30:
            world[3] = 1e-30
        return world[:3] / world[3]

    near = _unproj(-1.0)
    far  = _unproj(1.0)

    d = far - near
    length = float(np.linalg.norm(d))
    if length < 1e-30:
        raise ValidationError(
            "Degenerate ray: near and far unproject to the same point",
            {"screen_x": screen_x, "screen_y": screen_y},
        )
    return near, d / length


def _octree_ray_candidates(
    octree: dict,
    origin: np.ndarray,
    direction: np.ndarray,
) -> np.ndarray:
    """
    Traverse the octree and return render-face indices from all leaves whose
    bounding box is intersected by the ray (origin, direction).
    Uses the slab method (Smits' algorithm) for ray-AABB intersection.
    """
    node_bbox     = octree["node_bbox"]      # [N, 6] float32
    node_children = octree["node_children"]  # [N, 8] int32
    node_is_leaf  = octree["node_is_leaf"]   # [N]    uint8
    face_indices  = octree["face_indices"]   # [F]    int32
    leaf_offsets  = octree["leaf_offsets"]   # [N+1]  int32

    # Inverse direction (safe: avoid /0 by substituting a large value)
    inv_dir = np.where(
        np.abs(direction) > 1e-30,
        1.0 / direction,
        np.sign(direction + 1e-30) * 1e30,
    )

    parts = []
    stack = [0]
    while stack:
        nid = stack.pop()
        bb  = node_bbox[nid].astype(np.float64)
        lo, hi = bb[:3], bb[3:]

        # Slab test: compute entry/exit t along each axis
        t1 = (lo - origin) * inv_dir
        t2 = (hi - origin) * inv_dir
        t_enter = np.minimum(t1, t2).max()
        t_exit  = np.maximum(t1, t2).min()

        if t_exit < 0.0 or t_exit < t_enter:
            continue   # ray misses this AABB

        if node_is_leaf[nid]:
            s = int(leaf_offsets[nid])
            e = int(leaf_offsets[nid + 1])
            if e > s:
                parts.append(face_indices[s:e])
        else:
            for child in node_children[nid]:
                if child != -1:
                    stack.append(int(child))

    if not parts:
        return np.zeros(0, dtype=np.int32)
    return np.unique(np.concatenate(parts))


def _ray_triangle_intersect_batch(
    origin: np.ndarray,
    direction: np.ndarray,
    tris: np.ndarray,
) -> np.ndarray:
    """
    Vectorized Möller–Trumbore ray-triangle intersection test.

    Parameters
    ----------
    origin    : (3,) float64
    direction : (3,) float64 — should be normalised
    tris      : (N, 3, 3) float64 — N triangles, each [v0, v1, v2]

    Returns
    -------
    t : (N,) float64 — ray parameter for each triangle.
        inf  → no intersection (parallel, behind origin, or outside triangle).
        > 0  → intersection at origin + t * direction.
    """
    EPSILON = 1e-9
    v0 = tris[:, 0]   # (N, 3)
    v1 = tris[:, 1]
    v2 = tris[:, 2]

    e1 = v1 - v0       # (N, 3) edge v0→v1
    e2 = v2 - v0       # (N, 3) edge v0→v2

    h = np.cross(direction, e2)          # (N, 3): direction × e2
    a = (e1 * h).sum(axis=1)             # (N,)  determinant

    valid = np.abs(a) > EPSILON          # (N,) non-degenerate / non-parallel

    f = np.where(valid, 1.0 / a, 0.0)   # (N,)

    s  = origin - v0                     # (N, 3)
    u  = f * (s * h).sum(axis=1)        # (N,) first barycentric coordinate
    valid &= (u >= -EPSILON) & (u <= 1.0 + EPSILON)

    q  = np.cross(s, e1)                 # (N, 3)
    v  = f * (q @ direction)             # (N,) second barycentric coordinate
    valid &= (v >= -EPSILON) & (u + v <= 1.0 + EPSILON)

    t = f * (e2 * q).sum(axis=1)        # (N,) ray parameter
    valid &= t > EPSILON                 # intersection must be in front of origin

    result = np.full(len(tris), np.inf, dtype=np.float64)
    result[valid] = t[valid]
    return result


def ray_pick(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    screen_x: float,
    screen_y: float,
    viewport_width: float,
    viewport_height: float,
    vp_matrix_col_major: List[float],
    pick_mode: str = "element",
    node_idx: Optional[int] = None,
    step: Optional[str] = None,
    field: Optional[str] = None,
    frame_idx: Optional[int] = None,
    component_idx: Optional[int] = None,
    include_coords: bool = False,
    deform_scale: float = 1.0,
) -> PickResponse:
    """
    Ray-cast pick: reconstruct a world-space ray from the camera view-projection
    matrix and screen pixel coordinates, find the nearest surface triangle hit,
    then delegate to the existing pick() for ODB result lookup.

    Algorithm
    ---------
    1. Unproject (screen_x, screen_y) through VP_inv to get a world-space ray.
    2. Traverse the octree with a ray-AABB slab test to collect candidate faces.
       Falls back to full scan if no octree is loaded.
    3. Exact Möller–Trumbore intersection on candidates → nearest t > 0.
    4. Call pick() with the resolved render_face_idx.
    """
    idx = _get_ready_index(registry, odb_id)

    coords    = idx.coords_global.get(instance)
    node_rows = idx.source_node_rows.get(instance)
    if coords is None or node_rows is None:
        raise NotFoundError(
            f"Instance '{instance}' has no L2 geometry in ODB '{odb_id}'",
            {"instance": instance},
        )

    # ── Step 1: build world-space ray ────────────────────────────────────────
    origin, direction = _unproject_ray(
        screen_x, screen_y, viewport_width, viewport_height, vp_matrix_col_major
    )

    # ── Step 2: octree-accelerated candidate collection ──────────────────────
    octree = idx.octree.get(instance)
    if octree is not None:
        candidates = _octree_ray_candidates(octree, origin, direction)
    else:
        logger.warning(
            "ODB %s instance %s: no octree, full face scan for ray_pick", odb_id, instance
        )
        candidates = np.arange(len(node_rows), dtype=np.int32)

    if len(candidates) == 0:
        raise NotFoundError(
            "Ray did not intersect any geometry — check that screen coordinates "
            "are within the viewport and the model is visible",
            {"screen_x": screen_x, "screen_y": screen_y},
        )

    # ── Step 3: exact Möller–Trumbore on candidates ──────────────────────────
    cand_tris = coords[node_rows[candidates]].astype(np.float64)   # (C, 3, 3)
    t_values  = _ray_triangle_intersect_batch(origin, direction, cand_tris)

    best_local = int(np.argmin(t_values))
    if t_values[best_local] == np.inf:
        raise NotFoundError(
            "Ray did not intersect any geometry",
            {"screen_x": screen_x, "screen_y": screen_y},
        )

    best_face = int(candidates[best_local])

    # ── Step 4: delegate to existing pick() ──────────────────────────────────
    return pick(
        registry=registry,
        odb_id=odb_id,
        instance=instance,
        render_face_idx=best_face,
        pick_mode=pick_mode,
        step=step,
        field=field,
        frame_idx=frame_idx,
        component_idx=component_idx,
        node_idx=node_idx,
        include_coords=include_coords,
        deform_scale=deform_scale,
    )


def resolve_render_faces(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    render_face_indices: List[int],
    mode: str = "element",
) -> RenderFacesResponse:
    """
    Given a list of render face indices (collected by the frontend via screen-space
    bbox check), expand to full elements or unique nodes and return labels + geometry
    info needed to draw the correct highlight overlay.

    element mode:
      - Expands each input face to all faces of its parent element (so no half-elements).
      - Returns elem_count, elem_labels (≤2000), elem_face_indices (all triangle indices).

    node mode:
      - Collects unique node rows from all input face vertices.
      - Returns node_count, node_labels (≤2000), node_positions (≤5000).
    """
    idx = _get_ready_index(registry, odb_id)

    if not render_face_indices:
        return RenderFacesResponse(mode=mode)

    src_map   = idx.render_source_elem_row.get(instance)
    etype_arr = idx.source_elem_etype.get(instance)
    node_rows_map = idx.source_node_rows.get(instance)

    if src_map is None:
        raise NotFoundError(f"Instance '{instance}' not found in ODB '{odb_id}'",
                            {"instance": instance})

    face_idx = np.array(render_face_indices, dtype=np.int32)
    # Clamp to valid range
    face_idx = face_idx[(face_idx >= 0) & (face_idx < len(src_map))]
    if len(face_idx) == 0:
        return RenderFacesResponse(mode=mode)

    # ── Element mode ────────────────────────────────────────────────────────
    if mode == "element":
        elem_rows  = src_map[face_idx]
        etype_rows = etype_arr[face_idx] if etype_arr is not None else None

        # Compute consistent key parameters from the FULL arrays so that subset
        # keys and full-array keys are on the same scale for np.isin().
        max_er = int(src_map.max()) + 1 if len(src_map) > 0 else 1
        etype_unique = np.unique(etype_arr) if etype_arr is not None else None

        # Unique elements by composite (etype, elem_row) key
        composite = _composite_elem_key(elem_rows, etype_rows,
                                        max_er=max_er, etype_unique=etype_unique)
        sel_keys, unique_idx = np.unique(composite, return_index=True)
        unique_elem_rows  = elem_rows[unique_idx]
        unique_etype_rows = etype_rows[unique_idx] if etype_rows is not None else None

        # Expand: find ALL face indices of each selected element
        all_comp = _composite_elem_key(src_map, etype_arr,
                                       max_er=max_er, etype_unique=etype_unique)
        expand_mask = np.isin(all_comp, sel_keys)
        expanded = np.where(expand_mask)[0]
        all_face_indices = expanded.tolist()

        # For each expanded face, record which element group (0-based) it belongs to.
        # Used by the frontend to distinguish intra-element diagonals from
        # inter-element boundaries (same normal but different elements → keep edge).
        face_comp_keys = all_comp[expanded]
        elem_ids_per_face = np.searchsorted(sel_keys, face_comp_keys).tolist()

        unique_count = len(unique_idx)
        elem_labels = (
            _batch_elem_labels(idx.workspace, instance, unique_elem_rows, unique_etype_rows)
            if unique_count <= 2000 else None
        )

        return RenderFacesResponse(
            mode="element",
            elem_count=unique_count,
            elem_labels=elem_labels,
            elem_face_indices=all_face_indices,
            elem_ids_per_face=elem_ids_per_face,
        )

    # ── Node mode ────────────────────────────────────────────────────────────
    if node_rows_map is None:
        raise NotFoundError(f"Instance '{instance}' has no node row map", {"instance": instance})

    face_node_rows = node_rows_map[face_idx]          # [K, 3]
    unique_node_rows = np.unique(face_node_rows.ravel())  # sorted unique row indices

    node_count = len(unique_node_rows)

    # Labels
    node_labels: Optional[List[int]] = None
    if node_count <= 2000:
        try:
            hdf5_repo = HDF5Repo(idx.workspace)
            node_labels = hdf5_repo.get_node_labels_for_rows(
                instance, unique_node_rows.tolist()
            )
        except Exception:
            logger.warning("Could not look up node labels for render-faces", exc_info=True)

    # 3-D positions
    node_positions: Optional[List[List[float]]] = None
    if node_count <= 5000:
        coords = idx.coords_global.get(instance)
        if coords is not None:
            node_positions = coords[unique_node_rows].tolist()

    return RenderFacesResponse(
        mode="node",
        node_count=node_count,
        node_labels=node_labels,
        node_positions=node_positions,
    )


def surface_patch(
    registry: OdbRegistry,
    odb_id: str,
    req: SurfacePatchRequest,
) -> SurfacePatchResponse:
    """
    Select all surface faces that intersect an oriented rectangle.

    The rectangle lies in the plane perpendicular to `req.normal` and centred at
    `req.center`.  A local 2-D frame (u, v) is constructed as:

        n = normalize(normal)
        u = normalize(cross(up_hint, n))   # "width" axis
        v = normalize(cross(n, u))          # "height" axis

    If up_hint is parallel to n (dot > 0.999), fall back to global X [1,0,0].

    A face is selected when its triangle (projected onto the u/v plane) overlaps
    the rectangle [-width/2, width/2] × [-height/2, height/2].  This uses the
    2-D Separating Axis Theorem (5 axes: 2 AABB + 3 triangle edge normals),
    so even faces that only *touch* an edge of the rectangle are included.

    The octree is used to pre-filter candidates (AABB of the oriented rectangle),
    then the exact SAT test is applied.
    """
    idx = _get_ready_index(registry, odb_id)

    coords = idx.coords_global.get(req.instance)
    node_rows = idx.source_node_rows.get(req.instance)
    if coords is None or node_rows is None:
        raise NotFoundError(
            f"Instance '{req.instance}' has no L2 geometry in ODB '{odb_id}'",
            {"instance": req.instance},
        )

    # ── Build local frame ────────────────────────────────────────────────────
    n = np.asarray(req.normal, dtype=np.float64)
    norm_len = float(np.linalg.norm(n))
    if norm_len < 1e-12:
        raise ValidationError("normal vector must not be zero", {"normal": req.normal})
    n /= norm_len

    up = np.asarray(req.up_hint, dtype=np.float64)
    up_len = float(np.linalg.norm(up))
    if up_len < 1e-12:
        up = np.array([0.0, 1.0, 0.0])
    else:
        up /= up_len

    # Fall back if up is parallel to n
    if abs(float(np.dot(up, n))) > 0.999:
        up = np.array([1.0, 0.0, 0.0])
        if abs(float(np.dot(up, n))) > 0.999:
            up = np.array([0.0, 1.0, 0.0])

    u = np.cross(up, n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    v /= np.linalg.norm(v)

    center = np.asarray(req.center, dtype=np.float64)
    half_w = req.width  / 2.0
    half_h = req.height / 2.0

    # ── Octree pre-filter: AABB of the oriented rectangle ────────────────────
    # The rectangle corners in world space span ±half_w*u ± half_h*v from center.
    # The enclosing AABB is: center ± (|half_w*u| + |half_h*v|) per component.
    aabb_extent = np.abs(u) * half_w + np.abs(v) * half_h
    lo = (center - aabb_extent).astype(np.float32)
    hi = (center + aabb_extent).astype(np.float32)

    octree = idx.octree.get(req.instance)
    if octree is not None:
        candidates = _octree_candidates(octree, lo, hi)
    else:
        logger.warning(
            "ODB %s instance %s: no octree, full face scan for surface_patch",
            odb_id, req.instance,
        )
        candidates = np.arange(len(node_rows), dtype=np.int32)

    if len(candidates) == 0:
        return SurfacePatchResponse(
            face_count=0, elem_count=0, node_count=0,
            render_face_indices=[],
        )

    # ── 2-D SAT intersection: triangle vs. rectangle in (u,v) plane ─────────
    # Project each candidate triangle's 3 vertices onto (u, v).
    # A face is selected if its 2-D shadow overlaps the rectangle
    # [-half_w, half_w] × [-half_h, half_h].
    #
    # SAT axes tested (5 total):
    #   • u-axis (rectangle width)
    #   • v-axis (rectangle height)
    #   • 3 edge-normal axes of the projected triangle
    #
    # No separation on ALL axes → intersection → face is selected.
    cand_verts = coords[node_rows[candidates]].astype(np.float64)  # (C, 3, 3)
    offsets_v  = cand_verts - center                                # (C, 3, 3)

    lu_v = offsets_v @ u   # (C, 3) — u-projections of each vertex
    lv_v = offsets_v @ v   # (C, 3) — v-projections of each vertex

    # Axes 1 & 2: AABB separating axes
    sep = (
        (lu_v.max(axis=1) < -half_w) | (lu_v.min(axis=1) > half_w) |
        (lv_v.max(axis=1) < -half_h) | (lv_v.min(axis=1) > half_h)
    )

    # Axes 3–5: one per triangle edge normal (perpendicular to edge in 2-D)
    for i0, i1 in ((0, 1), (1, 2), (2, 0)):
        en_u = -(lv_v[:, i1] - lv_v[:, i0])   # (C,)  normal = (-dv, du)
        en_v =   lu_v[:, i1] - lu_v[:, i0]    # (C,)
        # Project all 3 triangle verts onto this axis
        projs   = en_u[:, None] * lu_v + en_v[:, None] * lv_v  # (C, 3)
        tri_min = projs.min(axis=1)
        tri_max = projs.max(axis=1)
        # AABB extent along the (per-face) axis vector
        r = np.abs(en_u) * half_w + np.abs(en_v) * half_h       # (C,)
        sep |= (tri_max < -r) | (tri_min > r)

    hit_faces = candidates[~sep]   # render face indices of overlapping triangles

    # ── Depth filter: centroid must be within ±depth/2 along the normal ──────
    # Auto-depth: half the shorter side.  This keeps only faces on the same
    # surface layer and excludes faces that project to the rectangle footprint
    # but are deep inside the model (a common issue with thin-walled parts).
    half_depth = (req.depth / 2.0) if req.depth is not None else min(half_w, half_h)
    if len(hit_faces) > 0:
        hit_verts   = coords[node_rows[hit_faces]].astype(np.float64)  # (H, 3, 3)
        centroids_h = hit_verts.mean(axis=1)                           # (H, 3)
        ln_h        = (centroids_h - center) @ n                       # (H,)
        depth_ok    = np.abs(ln_h) <= half_depth
        hit_faces   = hit_faces[depth_ok]

    if len(hit_faces) == 0:
        return SurfacePatchResponse(
            face_count=0, elem_count=0, node_count=0,
            render_face_indices=[],
        )

    # ── Expand to all faces of the parent elements ───────────────────────────
    # hit_faces are the triangles the rectangle touches.  For display we want
    # the full element silhouette (all surface triangles of each parent element).
    src_map   = idx.render_source_elem_row.get(req.instance)
    etype_arr = idx.source_elem_etype.get(req.instance)

    elem_rows  = src_map[hit_faces].astype(np.int32) if src_map is not None else None
    etype_rows = etype_arr[hit_faces] if etype_arr is not None else None

    elem_labels_out: Optional[List[int]] = None
    elem_count = 0
    # render_face_indices to return (expanded to full elements if possible)
    render_face_indices_out: np.ndarray = hit_faces
    if elem_rows is not None:
        composite = _composite_elem_key(elem_rows, etype_rows)
        _, uniq_idx = np.unique(composite, return_index=True)
        uniq_elem_rows  = elem_rows[uniq_idx]
        uniq_etype_rows = etype_rows[uniq_idx] if etype_rows is not None else None
        elem_count = len(uniq_idx)
        if elem_count <= 2000:
            elem_labels_out = _batch_elem_labels(
                idx.workspace, req.instance, uniq_elem_rows, uniq_etype_rows
            )
        # Expand: find ALL surface faces that belong to the same parent elements
        # so the frontend can highlight complete elements rather than just
        # the individual triangles the rectangle touched.
        uniq_elem_rows_set = np.unique(uniq_elem_rows)
        expanded_mask = np.isin(src_map.astype(np.int32), uniq_elem_rows_set)
        render_face_indices_out = np.where(expanded_mask)[0].astype(np.int32)

    # ── Collect unique nodes (from expanded faces for full element coverage) ──
    hit_node_rows = node_rows[render_face_indices_out]              # (H, 3)
    unique_node_rows = np.unique(hit_node_rows.ravel())         # sorted unique rows
    node_count = len(unique_node_rows)

    node_labels_out: Optional[List[int]] = None
    node_positions_out: Optional[List[List[float]]] = None

    if node_count <= 2000:
        try:
            hdf5_repo = HDF5Repo(idx.workspace)
            node_labels_out = hdf5_repo.get_node_labels_for_rows(
                req.instance, unique_node_rows.tolist()
            )
        except Exception:
            logger.warning("Could not look up node labels for surface_patch", exc_info=True)

    if node_count <= 5000:
        node_positions_out = coords[unique_node_rows].tolist()

    return SurfacePatchResponse(
        face_count=int(len(render_face_indices_out)),
        elem_count=elem_count,
        node_count=node_count,
        render_face_indices=render_face_indices_out.tolist(),
        elem_labels=elem_labels_out,
        node_labels=node_labels_out,
        node_positions=node_positions_out,
    )
