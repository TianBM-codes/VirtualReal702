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
from ..schemas.query import PickOdbInfo, PickResponse, PickResultInfo, BBoxResponse, RenderFacesResponse

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


def _result_h5_path(workspace: str, step: str, field: str) -> str:
    def safe(s):
        return s.replace("/", "__").replace("\\", "__").replace(" ", "_")
    return os.path.join(workspace, "l1", "results", f"{safe(step)}__{safe(field)}.h5")


def _read_u_displacement(
    workspace: str, instance: str, node_row: int, frame_idx: int, step: str
) -> Optional[List[float]]:
    """
    Read NODAL displacement [U1, U2, U3] for the given node at the given frame.
    Returns None if U field or NODAL dataset is unavailable.
    """
    u_path = _result_h5_path(workspace, step, "U")
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
) -> Optional[float]:
    """
    Compute Von Mises stress for the hit element at the given frame.
    Reads S field (INTEGRATION_POINT preferred, then ELEMENT_NODAL), averages
    over integration points, then applies the Von Mises formula.
    Returns None if S field is unavailable or has fewer than 3 components.
    Component order assumed: [S11, S22, S33, S12, S13, S23].
    Shell models store only 4 components [S11, S22, S33, S12]; S13/S23 are treated as 0.
    """
    s_path = _result_h5_path(workspace, step, "S")
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

    h5_path = _result_h5_path(workspace, step, field)
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
        )

    # ── Coords (orig + deformed) for node mode ──────────────────────────────
    orig_coords: Optional[List[float]] = None
    def_coords: Optional[List[float]] = None
    if include_coords and pick_mode == "node" and selected_node_row is not None:
        try:
            orig_coords = hdf5_repo.get_node_coords(instance, selected_node_row)
            if step and frame_idx is not None:
                u = _read_u_displacement(idx.workspace, instance, selected_node_row, frame_idx, step)
                if u is not None:
                    def_coords = [orig_coords[i] + u[i] * deform_scale for i in range(3)]
        except Exception:
            logger.warning("Could not read coords for pick", exc_info=True)

    # ── S,Mises (always computed from hit element if S field available) ──────
    mises: Optional[float] = None
    if step and frame_idx is not None:
        mises = _compute_mises(idx.workspace, instance, elem_row, etype_str, frame_idx, step)

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
