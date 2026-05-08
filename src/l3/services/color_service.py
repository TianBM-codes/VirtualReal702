"""
Color-code service: per-face attribute coloring for the viewer.

Supports five schemes:
  etype        — color by element type string (always available)
  section      — color by section assignment name (requires section_names in geometry H5)
  material     — color by material name (requires INP-exported material_name attr)
  section_type — color by section type (SOLID/SHELL/...) (requires INP export)
  elset        — highlight a named element set; set_name query param required

Response: per-vertex float32 RGB [Rf*3, 3] ready for direct vertex color update,
plus a legend list [{id, name, r, g, b}, ...].
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, NotReadyError, ValidationError
from ..core.state import ModelIndex

# ---------------------------------------------------------------------------
# Colour palette  (qualitative, 16 entries)
# ---------------------------------------------------------------------------

_PALETTE: List[Tuple[float, float, float]] = [
    (0.27, 0.52, 0.95),  # blue
    (0.95, 0.39, 0.27),  # coral
    (0.27, 0.78, 0.44),  # green
    (0.95, 0.78, 0.18),  # yellow
    (0.63, 0.27, 0.95),  # purple
    (0.18, 0.82, 0.90),  # cyan
    (0.95, 0.55, 0.18),  # orange
    (0.95, 0.27, 0.62),  # pink
    (0.47, 0.78, 0.18),  # lime
    (0.18, 0.47, 0.78),  # steel blue
    (0.78, 0.27, 0.27),  # dark red
    (0.18, 0.63, 0.63),  # teal
    (0.78, 0.63, 0.18),  # gold
    (0.55, 0.18, 0.47),  # mauve
    (0.39, 0.63, 0.18),  # olive
    (0.18, 0.27, 0.63),  # navy
]
_GREY      = (0.35, 0.35, 0.35)
_HIGHLIGHT = (0.95, 0.55, 0.10)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_schemes(idx: ModelIndex, instance: str) -> dict:
    """
    Return the available coloring schemes and elset names for *instance*.
    """
    schemes: List[str] = ["etype"]

    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")

    has_section_id = False
    has_mat = False
    has_sec = False

    if os.path.exists(geom_h5):
        with h5py.File(geom_h5, "r") as f:
            has_section_id = any(
                "section_id" in f[f"elements/{e}"]
                for e in f.get("elements", {})
            )
            for etype in f.get("elements", {}):
                grp = f[f"elements/{etype}"]
                has_mat = "material_name" in grp
                has_sec = "section_type" in grp
                break
            # Fallback for INP+ODB: check sections/<name> group attrs
            if not has_mat or not has_sec:
                for sec_name in f.get("sections", {}):
                    sg = f[f"sections/{sec_name}"]
                    if not has_mat and sg.attrs.get("material_name", ""):
                        has_mat = True
                    if not has_sec and sg.attrs.get("type", ""):
                        has_sec = True
                    if has_mat and has_sec:
                        break

    # Fallback for section scheme: averaging_data already loaded from render.h5
    # Covers INP+ODB mode where geometry H5 has no per-element section_id.
    if not has_section_id and instance in idx.averaging_data:
        avd = idx.averaging_data[instance]
        sec_ids = avd.get("elem_section_id")
        if sec_ids is not None and len(sec_ids) > 0 and int(np.max(sec_ids)) > 0:
            has_section_id = True

    if has_section_id:
        schemes.append("section")
    if has_mat:
        schemes.append("material")
    if has_sec:
        schemes.append("section_type")

    elsets: List[str] = []
    sets_h5 = os.path.join(idx.workspace, "l1", "sets", "sets.h5")
    if os.path.exists(sets_h5):
        with h5py.File(sets_h5, "r") as f:
            inst_grp = f.get(f"element_sets/{instance}")
            if inst_grp is not None:
                elsets = sorted(inst_grp.keys())
    # ODB-only fallback: instance sets live in geometry H5, not sets.h5
    if not elsets and os.path.exists(geom_h5):
        with h5py.File(geom_h5, "r") as f:
            isets_grp = f.get("instance_sets/element_sets")
            if isets_grp is not None:
                elsets = sorted(isets_grp.keys())
    if elsets:
        schemes.append("elset")

    return {"schemes": schemes, "elsets": elsets}


def get_color_code(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]] = None,
) -> Tuple[np.ndarray, List[dict]]:
    """
    Build per-vertex color array and legend for the requested scheme.

    For scheme='elset', set_names is a list of set names to highlight
    (each gets its own palette color, unlisted elements stay grey).

    Returns:
        colors   [Rf*3, 3] float32   — ready for Three.js colorAttr
        legend   list[{id, name, r, g, b}]
    """
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{idx.odb_id}' render data not loaded")

    etype_arr    = idx.source_elem_etype.get(instance)       # [Rf] S8 bytes
    elem_row_arr = idx.render_source_elem_row.get(instance)  # [Rf] int32

    if etype_arr is None or elem_row_arr is None:
        raise NotFoundError(
            f"Instance '{instance}' not found in ODB '{idx.odb_id}'",
            {"instance": instance},
        )

    if scheme == "etype":
        labels = _labels_from_etype(etype_arr)
    elif scheme == "section":
        labels = _labels_from_section_id(idx, instance, etype_arr, elem_row_arr)
    elif scheme in ("material", "section_type"):
        attr_name = "material_name" if scheme == "material" else "section_type"
        labels = _labels_from_elem_attr(idx, instance, etype_arr, elem_row_arr, attr_name)
    elif scheme == "elset":
        if not set_names:
            raise ValidationError("set_names required for elset scheme", {})
        labels = _labels_from_elsets(idx, instance, etype_arr, elem_row_arr, set_names)
    else:
        raise ValidationError(f"Unknown scheme '{scheme}'", {"scheme": scheme})

    # Build legend
    # For elset: named sets get palette colors (in selection order), "other" gets grey
    # For other schemes: first-occurrence order
    if scheme == "elset" and set_names:
        # Fixed order: selected sets first (palette colors), then "other" (grey)
        ordered = list(set_names) + ["other"]
        present = set(labels)
        ordered = [v for v in ordered if v in present]
        unique_vals = ordered
    else:
        unique_vals = list(dict.fromkeys(labels))

    val_to_id = {v: i for i, v in enumerate(unique_vals)}

    legend = []
    palette_idx = 0   # counts only "real" categories to keep palette consistent
    for i, val in enumerate(unique_vals):
        if scheme == "elset":
            rgb = _GREY if val == "other" else _PALETTE[palette_idx % len(_PALETTE)]
        elif not val or val in ("(none)", "(unknown)"):
            rgb = _GREY   # unassigned elements always grey
        else:
            rgb = _PALETTE[palette_idx % len(_PALETTE)]
        if rgb != _GREY:
            palette_idx += 1
        legend.append({
            "id": i, "name": val or "(none)",
            "r": rgb[0], "g": rgb[1], "b": rgb[2],
        })

    pal_arr     = np.array([(e["r"], e["g"], e["b"]) for e in legend], dtype=np.float32)
    face_codes  = np.array([val_to_id[v] for v in labels], dtype=np.int32)
    face_colors = pal_arr[face_codes]   # [Rf, 3]

    # Substitute user-defined display names (stored in manifest.db display_names table).
    from ..infra.manifest_repo import ManifestRepo
    display_names = ManifestRepo(idx.workspace).get_display_names(instance, scheme)
    if display_names:
        for item in legend:
            if item["name"] in display_names:
                item["name"] = display_names[item["name"]]

    vtx_ti = idx.vtx_tri_idx.get(instance)
    if vtx_ti is not None:
        colors = face_colors[vtx_ti]            # [Nv, 3] indexed geometry
    else:
        colors = np.repeat(face_colors, 3, axis=0)  # [Rf*3, 3] Triangle Soup
    return colors, legend


# ---------------------------------------------------------------------------
# Label extraction helpers
# ---------------------------------------------------------------------------

def _labels_from_etype(etype_arr: np.ndarray) -> List[str]:
    """Decode the S8 bytes array into clean etype strings."""
    return [
        b.tobytes().rstrip(b"\x00").decode("ascii", errors="replace")
        for b in etype_arr
    ]


def _labels_from_section_id(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
) -> List[str]:
    """
    Color by averaging region (refined section domain).

    After l1_pack's shell refinement pass, section_id values are reassigned
    to new integer domain IDs that no longer correspond to section_names indices.
    We just number unique domain IDs in sorted order: Region 1, Region 2, ...
    """
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        raise NotFoundError(f"Geometry H5 not found for '{instance}'", {})

    Rf = len(etype_arr)
    domain_per_face = np.full(Rf, -1, dtype=np.int32)

    etype_strs = np.array([
        b.tobytes().rstrip(b"\x00").decode("ascii", errors="replace")
        for b in etype_arr
    ])

    with h5py.File(geom_h5, "r") as f:
        has_any = False
        for etype_str in np.unique(etype_strs):
            mask = (etype_strs == etype_str)
            grp  = f.get(f"elements/{etype_str}")
            if grp is None or "section_id" not in grp:
                continue
            has_any = True
            sid_data              = grp["section_id"][:]
            domain_per_face[mask] = sid_data[elem_row_arr[mask]]

    if not has_any:
        # INP+ODB fallback: use averaging_data loaded from render.h5.
        # averaging_data["elem_*"] arrays are indexed over unique surface elements.
        avd = idx.averaging_data.get(instance)
        if avd is None:
            return ["(none)"] * Rf
        av_etype  = avd["elem_etype"]          # [E] S8
        av_row    = avd["elem_row"]             # [E] int32
        av_domain = avd["default_domain_id"]    # [E] int32
        lookup: Dict[Tuple[bytes, int], int] = {
            (av_etype[i].tobytes().rstrip(b"\x00"), int(av_row[i])): int(av_domain[i])
            for i in range(len(av_etype))
        }
        for fi in range(Rf):
            key = (etype_arr[fi].tobytes().rstrip(b"\x00"), int(elem_row_arr[fi]))
            domain_per_face[fi] = lookup.get(key, -1)
        if not np.any(domain_per_face >= 0):
            return ["(none)"] * Rf

    # Map each unique domain ID → human-readable label "Region N" (sorted order).
    unique_ids = sorted(set(int(v) for v in domain_per_face if v >= 0))
    id_to_label = {did: f"Region {i + 1}" for i, did in enumerate(unique_ids)}

    return [id_to_label.get(int(v), "(none)") for v in domain_per_face]


def _labels_from_elem_attr(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
    attr_name: str,
) -> List[str]:
    """
    Look up per-element string attributes (material_name / section_type)
    from L1 geometry H5, grouped by etype for efficiency.
    """
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        raise NotFoundError(f"Geometry H5 not found for '{instance}'", {})

    Rf     = len(etype_arr)
    result = np.full(Rf, b"", dtype="S64")

    # Decode all etype strings upfront — numpy S8 byte comparison can silently
    # fail to match when bytes scalars have different lengths/padding, so we
    # compare plain Python strings instead.
    etype_strs = np.array([
        b.tobytes().rstrip(b"\x00").decode("ascii", errors="replace")
        for b in etype_arr
    ])

    with h5py.File(geom_h5, "r") as f:
        for etype_str in np.unique(etype_strs):
            mask      = (etype_strs == etype_str)
            grp       = f.get(f"elements/{etype_str}")
            if grp is None or attr_name not in grp:
                continue
            attr_data          = grp[attr_name][:]     # [M] S64/S16
            result[mask]       = attr_data[elem_row_arr[mask]]

    return [
        s.tobytes().rstrip(b"\x00").decode("ascii", errors="replace") or "(none)"
        for s in result
    ]


def _labels_from_elsets(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
    set_names: List[str],
) -> List[str]:
    """
    Return per-face label: the first matching set name if the element belongs
    to any of *set_names*, else "other".  Priority = order of set_names.
    """
    from ..infra.manifest_repo import ManifestRepo
    sets_h5 = os.path.join(idx.workspace, "l1", "sets", "sets.h5")
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")

    # Load all requested sets' label arrays.
    # Primary:  sets.h5 element_sets/{instance}/{sn}          (INP+ODB mode)
    # Fallback: geometry H5 instance_sets/element_sets/{sn}   (ODB-only mode)
    slabels_dict: Dict[str, np.ndarray] = {}
    if os.path.exists(sets_h5):
        with h5py.File(sets_h5, "r") as f:
            for sn in set_names:
                key = f"element_sets/{instance}/{sn}"
                if key in f:
                    slabels_dict[sn] = f[key][:]
    missing = [sn for sn in set_names if sn not in slabels_dict]
    if missing:
        if not os.path.exists(geom_h5):
            raise NotFoundError("No sets data found for this workspace", {})
        with h5py.File(geom_h5, "r") as f:
            for sn in missing:
                geom_key = f"instance_sets/element_sets/{sn}"
                if geom_key not in f:
                    raise ValidationError(f"Set '{sn}' not found", {"set_name": sn})
                slabels_dict[sn] = f[geom_key][:]
    set_label_arrays: List[Tuple[str, np.ndarray]] = [
        (sn, slabels_dict[sn]) for sn in set_names
    ]

    Rf         = len(etype_arr)
    # face_set[i] = index into set_names + 1 (0 = "other")
    face_set   = np.zeros(Rf, dtype=np.int32)

    etype_strs = np.array([
        b.tobytes().rstrip(b"\x00").decode("ascii", errors="replace")
        for b in etype_arr
    ])

    with h5py.File(geom_h5, "r") as f:
        for etype_str in np.unique(etype_strs):
            mask      = (etype_strs == etype_str)
            grp       = f.get(f"elements/{etype_str}")
            if grp is None or "labels" not in grp:
                continue
            elem_labels      = grp["labels"][:]
            face_elem_labels = elem_labels[elem_row_arr[mask]]

            # Assign in reverse priority order so set_names[0] wins (overwrites later)
            for si in range(len(set_label_arrays) - 1, -1, -1):
                sn, slabels = set_label_arrays[si]
                hit = np.isin(face_elem_labels, slabels)
                face_set_mask         = face_set[mask]
                face_set_mask[hit]    = si + 1
                face_set[mask]        = face_set_mask

    # Convert code → label string
    code_to_name = {0: "other"}
    for si, (sn, _) in enumerate(set_label_arrays):
        code_to_name[si + 1] = sn

    return [code_to_name[c] for c in face_set]
