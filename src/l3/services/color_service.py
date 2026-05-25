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
    (0.271, 0.545, 0.455),  # #458B74
    (0.961, 0.961, 0.863),  # #F5F5DC
    (0.733, 0.227, 0.227),  # #BB3A3A
    (0.000, 0.369, 0.616),  # #005E9D
    (0.757, 0.804, 0.804),  # #C1CDCD
    (0.871, 0.722, 0.529),  # #DEB887
    (0.694, 0.776, 0.929),  # #B1C6ED
    (0.545, 0.533, 0.471),  # #8B8878
    (0.914, 0.588, 0.478),  # #E9967A
    (0.741, 0.718, 0.420),  # #BDB76B
    (0.545, 0.039, 0.314),  # #8B0A50
    (0.192, 0.227, 0.592),  # #313A97
    (0.792, 0.851, 0.733),  # #CAD9BB
    (0.933, 0.788, 0.000),  # #EEC900
    (0.678, 0.847, 0.902),  # #ADD8E6
    (0.431, 0.482, 0.545),  # #6E7B8B
    (0.545, 0.278, 0.537),  # #8B4789
    (0.933, 0.910, 0.667),  # #EEE8AA
    (0.722, 0.808, 0.776),  # #B8CEC6
    (0.722, 0.722, 0.859),  # #B8B8DB
    (0.808, 0.643, 0.420),  # #CEA46B
    (0.416, 0.353, 0.804),  # #6A5ACD
    (0.933, 0.914, 0.914),  # #EEE9E9
    (0.000, 0.200, 0.400),  # #003366
]
_GREY      = (0.35, 0.35, 0.35)
_HIGHLIGHT = (0.95, 0.55, 0.10)


def _build_global_section_color_map(idx: "ModelIndex") -> Dict[str, Tuple[float, float, float]]:
    """
    Build a {region_label: (r,g,b)} map that mirrors Abaqus sequential assignment:
    sort all instances alphabetically, then number their regions 1,2,3... and assign
    palette colours in that global order.  Same label always gets the same colour
    regardless of which instance's get_color_code / get_legend_entries is called.
    """
    color_map: Dict[str, Tuple[float, float, float]] = {}
    palette_idx = 0
    for inst in sorted(idx.averaging_data.keys()):
        avd = idx.averaging_data[inst]
        unique_ids = sorted(set(int(v) for v in avd["default_domain_id"] if v >= 0))
        for i, _ in enumerate(unique_ids):
            label = f"{inst}.Region_{i + 1}"
            color_map[label] = _PALETTE[palette_idx % len(_PALETTE)]
            palette_idx += 1
    return color_map


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

    # section scheme: solely from render.h5 averaging_data (works for both ODB and INP+ODB)
    avd = idx.averaging_data.get(instance)
    if avd is not None and len(avd.get("default_domain_id", [])) > 0:
        schemes.append("section")

    has_mat = False
    has_sec = False

    if os.path.exists(geom_h5):
        with h5py.File(geom_h5, "r") as f:
            for etype in f.get("elements", {}):
                grp = f[f"elements/{etype}"]
                has_mat = "material_name" in grp
                has_sec = "section_type" in grp
                break
            if not has_mat or not has_sec:
                for sec_name in f.get("sections", {}):
                    sg = f[f"sections/{sec_name}"]
                    if not has_mat and sg.attrs.get("material_name", ""):
                        has_mat = True
                    if not has_sec and sg.attrs.get("type", ""):
                        has_sec = True
                    if has_mat and has_sec:
                        break

    if has_mat:
        schemes.append("material")
    if has_sec:
        schemes.append("section_type")

    elsets: List[str] = []
    sets_h5 = os.path.join(idx.workspace, "l1", "sets", "sets.h5")
    inst_safe = instance.replace('/', '__').replace('\\', '__').replace(' ', '_')
    if os.path.exists(sets_h5):
        with h5py.File(sets_h5, "r") as f:
            # Instance-level element sets
            inst_grp = f.get(f"element_sets/{instance}")
            if inst_grp is not None:
                elsets = list(inst_grp.keys())
            # Assembly-level element sets that contain elements in this instance
            asm_grp = f.get("assembly_sets")
            if asm_grp is not None:
                for set_safe in asm_grp.keys():
                    if inst_safe in asm_grp[set_safe] and \
                            "elem_labels" in asm_grp[set_safe][inst_safe]:
                        elsets.append(set_safe)
            elsets = sorted(set(elsets))
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

    labels, legend, val_to_id = _compute_labels_and_legend(
        idx, instance, scheme, set_names, etype_arr, elem_row_arr
    )

    pal_arr     = np.array([(e["r"], e["g"], e["b"]) for e in legend], dtype=np.float32)
    face_codes  = np.array([val_to_id[v] for v in labels], dtype=np.int32)
    face_colors = pal_arr[face_codes]   # [Rf, 3]

    vtx_ti = idx.vtx_tri_idx.get(instance)
    if vtx_ti is not None:
        colors = face_colors[vtx_ti]            # [Nv, 3] indexed geometry
    else:
        colors = np.repeat(face_colors, 3, axis=0)  # [Rf*3, 3] Triangle Soup
    return colors, legend


def get_legend(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]] = None,
) -> List[dict]:
    """Return only the legend list without building vertex colors.

    Cheap alternative to get_color_code() when only the color mapping is needed
    (e.g. populating a legend panel without loading geometry).

    Returns list[{id, legend_key, name, r, g, b}] — same format as the legend
    embedded in the GET /color-code/{instance} L3BE response.
    """
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{idx.odb_id}' render data not loaded")

    etype_arr    = idx.source_elem_etype.get(instance)
    elem_row_arr = idx.render_source_elem_row.get(instance)
    if etype_arr is None or elem_row_arr is None:
        raise NotFoundError(
            f"Instance '{instance}' not found in ODB '{idx.odb_id}'",
            {"instance": instance},
        )

    _, legend, _ = _compute_labels_and_legend(
        idx, instance, scheme, set_names, etype_arr, elem_row_arr
    )
    return legend


# ---------------------------------------------------------------------------
# Label extraction helpers
# ---------------------------------------------------------------------------

def region_face_mask(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    region: str,
) -> Optional[np.ndarray]:
    """
    Return a bool mask [Rf] where True = render face belongs to *region*.
    Returns None if scheme is unsupported or data is missing.
    """
    etype_arr    = idx.source_elem_etype.get(instance)
    elem_row_arr = idx.render_source_elem_row.get(instance)
    if etype_arr is None or elem_row_arr is None:
        return None

    try:
        if scheme == "section":
            labels = _labels_from_section_id(idx, instance, etype_arr, elem_row_arr)
        elif scheme == "etype":
            labels = _labels_from_etype(etype_arr)
        elif scheme in ("material", "section_type"):
            attr_name = "material_name" if scheme == "material" else "section_type"
            labels = _labels_from_elem_attr(idx, instance, etype_arr, elem_row_arr, attr_name)
        elif scheme == "elset":
            # _labels_from_elsets returns the set name or "other"; region IS the set name
            labels = _labels_from_elsets(idx, instance, etype_arr, elem_row_arr, [region])
        else:
            return None
    except Exception:
        return None

    return np.array([l == region for l in labels], dtype=bool)


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
    """Color by averaging region — domain ID from render.h5 averaging_data (in memory)."""
    Rf  = len(etype_arr)
    avd = idx.averaging_data.get(instance)
    if avd is None:
        return ["(none)"] * Rf

    lookup: Dict[Tuple[bytes, int], int] = {
        (avd["elem_etype"][i].tobytes().rstrip(b"\x00"), int(avd["elem_row"][i])): int(avd["default_domain_id"][i])
        for i in range(len(avd["elem_etype"]))
    }
    domain_per_face = np.array([
        lookup.get((etype_arr[fi].tobytes().rstrip(b"\x00"), int(elem_row_arr[fi])), -1)
        for fi in range(Rf)
    ], dtype=np.int32)

    if not np.any(domain_per_face >= 0):
        return ["(none)"] * Rf

    unique_ids = sorted(set(int(v) for v in domain_per_face if v >= 0))
    id_to_label = {did: f"{instance}.Region_{i + 1}" for i, did in enumerate(unique_ids)}
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
    # Priority 1: sets.h5 element_sets/{instance}/{sn}          (instance sets, INP+ODB mode)
    # Priority 2: sets.h5 assembly_sets/{sn}/{inst_safe}/elem_labels  (assembly sets)
    # Priority 3: geometry H5 instance_sets/element_sets/{sn}   (ODB-only mode)
    inst_safe = instance.replace('/', '__').replace('\\', '__').replace(' ', '_')
    slabels_dict: Dict[str, np.ndarray] = {}
    if os.path.exists(sets_h5):
        with h5py.File(sets_h5, "r") as f:
            for sn in set_names:
                key = f"element_sets/{instance}/{sn}"
                if key in f:
                    slabels_dict[sn] = f[key][:]
                    continue
                # Try assembly_sets (set_safe key may equal sn when sn has no special chars)
                asm_key = f"assembly_sets/{sn}/{inst_safe}/elem_labels"
                if asm_key in f:
                    slabels_dict[sn] = f[asm_key][:]
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


# ---------------------------------------------------------------------------
# L1 complete-value helpers (for legend completeness)
# ---------------------------------------------------------------------------

def _all_unique_vals_from_l1(idx: ModelIndex, instance: str, attr_name: str) -> List[str]:
    """
    Read *attr_name* (e.g. 'material_name', 'section_type') from ALL element
    groups in the L1 geometry H5, returning every unique non-empty value.
    Used to ensure the legend lists model-level values, not just surface-visible ones.
    """
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        return []
    vals: list = []
    try:
        with h5py.File(geom_h5, "r") as f:
            for etype_str in f.get("elements", {}):
                grp = f[f"elements/{etype_str}"]
                if attr_name not in grp:
                    continue
                for raw in grp[attr_name][:]:
                    v = raw.tobytes().rstrip(b"\x00").decode("ascii", errors="replace")
                    if v:
                        vals.append(v)
    except Exception:
        pass
    # preserve order of first occurrence, deduplicate
    seen: dict = {}
    for v in vals:
        seen.setdefault(v, None)
    return list(seen)


def _all_etypes_from_l1(idx: ModelIndex, instance: str) -> List[str]:
    """Return every element-type group name present in the L1 geometry H5."""
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        return []
    try:
        with h5py.File(geom_h5, "r") as f:
            return sorted(f.get("elements", {}).keys())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Shared labels + legend computation (used by get_color_code and get_legend)
# ---------------------------------------------------------------------------

def _compute_labels_and_legend(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]],
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
) -> Tuple[List[str], List[dict], Dict[str, int]]:
    """Compute per-face labels and build the legend list.

    Returns (labels, legend, val_to_id) where:
      labels    — per-face string label, length Rf
      legend    — [{id, legend_key, name, r, g, b}, ...]
      val_to_id — {label_value: palette_index} for building color arrays
    """
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

    if scheme == "elset" and set_names:
        ordered     = list(set_names) + ["other"]
        present     = set(labels)
        unique_vals = [v for v in ordered if v in present]
    else:
        # Surface-derived labels (in appearance order)
        unique_vals = list(dict.fromkeys(labels))
        # For etype / material / section_type: append any values that only
        # exist on interior (non-surface) elements so the legend is complete.
        if scheme in ("etype", "material", "section_type"):
            if scheme == "etype":
                all_l1 = _all_etypes_from_l1(idx, instance)
            else:
                attr_name = "material_name" if scheme == "material" else "section_type"
                all_l1 = _all_unique_vals_from_l1(idx, instance, attr_name)
            surface_set = set(unique_vals)
            for v in all_l1:
                if v and v not in surface_set:
                    unique_vals.append(v)
                    surface_set.add(v)

    val_to_id = {v: i for i, v in enumerate(unique_vals)}

    from ..infra.manifest_repo import ManifestRepo
    overrides = ManifestRepo(idx.workspace).get_legend_overrides(instance, scheme)

    global_sec_colors = _build_global_section_color_map(idx) if scheme == "section" else {}

    legend: List[dict] = []
    palette_idx = 0
    for i, val in enumerate(unique_vals):
        if scheme == "elset":
            auto_rgb = _GREY if val == "other" else _PALETTE[palette_idx % len(_PALETTE)]
            if auto_rgb != _GREY:
                palette_idx += 1
        elif not val or val in ("(none)", "(unknown)"):
            auto_rgb = _GREY
        elif scheme == "section":
            auto_rgb = global_sec_colors.get(val, _GREY)
        else:
            auto_rgb = _PALETTE[palette_idx % len(_PALETTE)]
            palette_idx += 1
        ov  = overrides.get(val, {})
        rgb = (ov["color_r"], ov["color_g"], ov["color_b"]) if ov.get("color_r") is not None else auto_rgb
        legend.append({
            "id":         i,
            "legend_key": val or "(none)",
            "name":       ov.get("display_name") or val or "(none)",
            "r":          rgb[0],
            "g":          rgb[1],
            "b":          rgb[2],
        })

    return labels, legend, val_to_id


# ---------------------------------------------------------------------------
# Legend entries (for LegendEditor floating panel)
# ---------------------------------------------------------------------------

def get_all_legend_entries(
    idx: ModelIndex,
    scheme: str,
    set_names: Optional[List[str]] = None,
) -> List[dict]:
    """Return legend entries for all instances, each entry augmented with an 'instance' field."""
    all_entries: List[dict] = []
    for inst in idx.source_elem_etype.keys():
        try:
            entries = get_legend_entries(idx, inst, scheme, set_names)
            for e in entries:
                e["instance"] = inst
            all_entries.extend(entries)
        except Exception:
            pass
    return all_entries


def get_legend_entries(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]] = None,
) -> List[dict]:
    """
    Return legend entries for the given scheme enriched with:
      legend_key, default_title, display_name, effective color,
      user_color/user_name flags, face_count.
    Used by GET /color-code/{instance}/legend-entries.
    """
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{idx.odb_id}' render data not loaded")

    etype_arr    = idx.source_elem_etype.get(instance)
    elem_row_arr = idx.render_source_elem_row.get(instance)
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

    from collections import Counter
    face_counts = Counter(labels)

    if scheme == "elset" and set_names:
        ordered   = list(set_names) + ["other"]
        present   = set(labels)
        unique_vals = [v for v in ordered if v in present]
    else:
        unique_vals = list(dict.fromkeys(labels))
        # Append values that only exist on interior (non-surface) elements so
        # the legend editor table is complete (face_count will be 0 for these).
        if scheme in ("etype", "material", "section_type"):
            if scheme == "etype":
                all_l1 = _all_etypes_from_l1(idx, instance)
            else:
                attr_name = "material_name" if scheme == "material" else "section_type"
                all_l1 = _all_unique_vals_from_l1(idx, instance, attr_name)
            surface_set = set(unique_vals)
            for v in all_l1:
                if v and v not in surface_set:
                    unique_vals.append(v)
                    surface_set.add(v)

    # Palette assignment (identical logic to get_color_code)
    global_sec_colors = _build_global_section_color_map(idx) if scheme == "section" else {}
    palette_colors: Dict[str, Tuple[float, float, float]] = {}
    palette_idx = 0
    for val in unique_vals:
        if scheme == "elset":
            rgb = _GREY if val == "other" else _PALETTE[palette_idx % len(_PALETTE)]
            if rgb != _GREY:
                palette_idx += 1
        elif not val or val in ("(none)", "(unknown)"):
            rgb = _GREY
        elif scheme == "section":
            rgb = global_sec_colors.get(val, _GREY)
        else:
            rgb = _PALETTE[palette_idx % len(_PALETTE)]
            palette_idx += 1
        palette_colors[val] = rgb

    from ..infra.manifest_repo import ManifestRepo
    overrides     = ManifestRepo(idx.workspace).get_legend_overrides(instance, scheme)
    default_titles = _default_titles_section(idx, instance, etype_arr, elem_row_arr, unique_vals) \
        if scheme == "section" else {}

    entries = []
    for val in unique_vals:
        ov         = overrides.get(val, {})
        user_name  = "display_name" in ov
        user_color = "color_r" in ov
        if user_color:
            r, g, b = ov["color_r"], ov["color_g"], ov["color_b"]
        else:
            r, g, b = palette_colors[val]
        entries.append({
            "legend_key":    val,
            "default_title": default_titles.get(val, val),
            "display_name":  ov.get("display_name"),   # None if not set
            "color_r":       r,
            "color_g":       g,
            "color_b":       b,
            "user_color":    user_color,
            "user_name":     user_name,
            "face_count":    face_counts.get(val, 0),
        })
    return entries


def _default_titles_section(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
    unique_vals: List[str],
) -> Dict[str, str]:
    """For section scheme: map each 'Region N' label → material_name as default title."""
    avd = idx.averaging_data.get(instance)
    if avd is None:
        return {}

    # Build lookup: (etype_bytes, elem_row) → domain_id
    # Also collect one representative element per domain for material lookup
    lookup:     Dict[Tuple[bytes, int], int] = {}
    domain_rep: Dict[int, Tuple[str, int]]   = {}
    for i in range(len(avd["elem_etype"])):
        etype_b = avd["elem_etype"][i].tobytes().rstrip(b"\x00")
        row     = int(avd["elem_row"][i])
        did     = int(avd["default_domain_id"][i])
        lookup[(etype_b, row)] = did
        if did not in domain_rep:
            domain_rep[did] = (etype_b.decode("ascii", errors="replace"), row)

    # Rebuild domain_id → "Region N" (same ordering as _labels_from_section_id)
    Rf = len(etype_arr)
    domain_per_face = np.array([
        lookup.get((etype_arr[fi].tobytes().rstrip(b"\x00"), int(elem_row_arr[fi])), -1)
        for fi in range(Rf)
    ], dtype=np.int32)
    unique_ids  = sorted(set(int(v) for v in domain_per_face if v >= 0))
    id_to_label = {did: f"Region {i + 1}" for i, did in enumerate(unique_ids)}
    label_to_id = {v: k for k, v in id_to_label.items()}

    # Look up material_name for each domain's representative element from geometry H5
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = (ManifestRepo(idx.workspace).get_geom_path(instance)
               or os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5"))
    domain_mat: Dict[int, str] = {}
    if os.path.exists(geom_h5):
        with h5py.File(geom_h5, "r") as f:
            for did, (etype, row) in domain_rep.items():
                grp = f.get(f"elements/{etype}")
                mat = ""
                if grp is not None and "material_name" in grp:
                    raw = grp["material_name"][row]
                    mat = raw.tobytes().rstrip(b"\x00").decode("ascii", errors="replace")
                domain_mat[did] = mat

    result: Dict[str, str] = {}
    for val in unique_vals:
        if val in ("(none)", "(unknown)"):
            result[val] = val
            continue
        did = label_to_id.get(val)
        mat = domain_mat.get(did, "") if did is not None else ""
        result[val] = mat if mat else val
    return result
