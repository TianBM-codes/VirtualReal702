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
import re
from typing import Dict, List, Optional, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, NotReadyError, ValidationError
from ..core.state import ModelIndex
from src.l1.manifest_schema import canon_instance

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


def _build_global_elset_color_map(idx: "ModelIndex") -> Dict[str, Tuple[float, float, float]]:
    """
    Enumerate every element-set name that exists in this workspace and assign
    palette colours globally (sorted order) so the same set always gets the
    same colour regardless of which request or instance is being rendered.

    Four sources (all checked, names deduplicated by insertion order):
      1. sets.h5  element_sets/{inst}/{sn}            — INP-exported instance sets
      2. sets.h5  assembly_sets/{sn}                  — assembly-level sets
      3. geometry/{inst}.h5  instance_sets/element_sets/{sn}  — ODB instance sets
      4. sets.h5  part_sets/{part}/element_sets/{sn}  — part-level sets via dump_sets
    """
    sets_h5 = os.path.join(idx.workspace, "l1", "sets", "sets.h5")
    seen: Dict[str, None] = {}

    if os.path.exists(sets_h5):
        try:
            with h5py.File(sets_h5, "r") as f:
                inst_grp = f.get("element_sets")
                if inst_grp is not None:
                    for inst in sorted(inst_grp.keys()):
                        for sn in sorted(inst_grp[inst].keys()):
                            seen.setdefault(sn, None)
                asm_grp = f.get("assembly_sets")
                if asm_grp is not None:
                    for sn in sorted(asm_grp.keys()):
                        seen.setdefault(sn, None)
                parts_grp = f.get("part_sets")
                if parts_grp is not None:
                    for part_safe in sorted(parts_grp.keys()):
                        eg = f.get(f"part_sets/{part_safe}/element_sets")
                        if eg is not None:
                            for sn in sorted(eg.keys()):
                                seen.setdefault(sn, None)
        except Exception:
            pass

    from ..infra.manifest_repo import ManifestRepo
    for inst in sorted(idx.source_elem_etype.keys()):
        geom_h5 = ManifestRepo(idx.workspace).get_geom_path(inst) or \
                  os.path.join(idx.workspace, "l1", "geometry", f"{inst}.h5")
        if not os.path.exists(geom_h5):
            continue
        try:
            with h5py.File(geom_h5, "r") as f:
                isets = f.get("instance_sets/element_sets")
                if isets is not None:
                    for sn in sorted(isets.keys()):
                        seen.setdefault(sn, None)
        except Exception:
            pass

    return {sn: _PALETTE[i % len(_PALETTE)] for i, sn in enumerate(seen)}


def _build_global_color_map(idx: "ModelIndex", scheme: str) -> Dict[str, Tuple[float, float, float]]:
    """
    Build a globally consistent {label: (r,g,b)} map for all schemes.
    Collects every possible value across all instances (sorted alphabetically by
    instance name) so that the same etype/material/section_type/region/set always
    gets the same palette colour regardless of which instance is being rendered.

    The map is independent of which instance is requested and derived purely from
    immutable L1 data, so it is memoized per scheme on the ModelIndex. Without this,
    get_all_legend_entries (which calls get_legend_entries once per instance) would
    rebuild the whole-model scan N times → O(N²) H5 opens.
    """
    cache_key = ("colormap", scheme)
    cached = idx.legend_scan_cache.get(cache_key)
    if cached is not None:
        return cached

    if scheme == "section":
        result = _build_global_section_color_map(idx)
        idx.legend_scan_cache[cache_key] = result
        return result
    if scheme == "elset":
        result = _build_global_elset_color_map(idx)
        idx.legend_scan_cache[cache_key] = result
        return result
    if scheme == "section_assignment":
        seen_sa: Dict[str, None] = {}
        for inst in sorted(idx.source_elem_etype.keys()):
            for name in _section_assignments(idx, inst).keys():
                seen_sa.setdefault(name, None)
        result = {n: _PALETTE[i % len(_PALETTE)] for i, n in enumerate(seen_sa)}
        idx.legend_scan_cache[cache_key] = result
        return result

    seen: Dict[str, None] = {}
    if scheme == "etype":
        for inst in sorted(idx.source_elem_etype.keys()):
            for v in sorted(_all_etypes_from_l1(idx, inst)):
                if v:
                    seen.setdefault(v, None)
    elif scheme in ("material", "section_type"):
        attr_name = "material_name" if scheme == "material" else "section_type"
        for inst in sorted(idx.source_elem_etype.keys()):
            for v in _all_unique_vals_from_l1(idx, inst, attr_name):
                if v:
                    seen.setdefault(v, None)

    result = {v: _PALETTE[i % len(_PALETTE)] for i, v in enumerate(seen)}
    idx.legend_scan_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_schemes(idx: ModelIndex, instance: str) -> dict:
    """
    Return the available coloring schemes and elset names for *instance*.
    """
    instance = canon_instance(instance)
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

    # section_assignment: color by actual Abaqus section assignment (distinct
    # from "section" above, which is averaging regions).
    if _has_section_assignments(idx, instance):
        schemes.append("section_assignment")

    elsets_seen: Dict[str, None] = {}
    sets_h5 = os.path.join(idx.workspace, "l1", "sets", "sets.h5")
    inst_safe = instance.replace('/', '__').replace('\\', '__').replace(' ', '_')
    if os.path.exists(sets_h5):
        with h5py.File(sets_h5, "r") as f:
            # INP-exported instance-level sets: element_sets/{instance}/{sn}
            inst_grp = f.get(f"element_sets/{instance}")
            if inst_grp is not None:
                for sn in inst_grp.keys():
                    elsets_seen.setdefault(sn, None)
            # Assembly-level sets that have elements in this instance
            asm_grp = f.get("assembly_sets")
            if asm_grp is not None:
                for set_safe in asm_grp.keys():
                    if inst_safe in asm_grp[set_safe] and \
                            "elem_labels" in asm_grp[set_safe][inst_safe]:
                        elsets_seen.setdefault(set_safe, None)
    # ODB instance-level sets live in geometry H5 (always check, not only as fallback)
    if os.path.exists(geom_h5):
        try:
            with h5py.File(geom_h5, "r") as f:
                isets_grp = f.get("instance_sets/element_sets")
                if isets_grp is not None:
                    for sn in isets_grp.keys():
                        elsets_seen.setdefault(sn, None)
        except Exception:
            pass
    elsets = sorted(elsets_seen)
    if elsets:
        schemes.append("elset")

    return {"schemes": schemes, "elsets": elsets}


def get_all_schemes(idx: ModelIndex) -> dict:
    """Union of coloring schemes across *all* instances, plus the full
    instance-scoped elset list.

    Element-set names are only unique within an instance (label scoping), so a
    bare name like ``_PickedSet6`` may exist on several instances and mean a
    different element group on each. The per-instance ``get_schemes`` can't
    express the whole model. Here every set is returned as the qualified string
    ``INSTANCE.setname`` (e.g. ``OMEGA-1._PickedSet6``) so the caller can show
    them all at once and the elset endpoints route each selection back to the
    one instance it belongs to (see ``_split_instance_prefix``).

    Returned ``elsets`` is a flat list of qualified-name strings — drop-in
    compatible with the existing per-instance ``{schemes, elsets}`` shape, just
    with more (and prefixed) entries, so the front-end needs no change.

    Returns: {"schemes": [union, canonical order], "elsets": ["INST.name", ...]}
    """
    # Canonical display order; any extra scheme is appended afterwards.
    order = ["etype", "section", "material", "section_type",
             "section_assignment", "elset"]
    schemes_seen: Dict[str, None] = {}
    elsets: List[str] = []
    for inst in sorted(idx.source_elem_etype.keys()):
        try:
            s = get_schemes(idx, inst)
        except Exception:
            continue
        for sc in s.get("schemes", []):
            schemes_seen.setdefault(sc, None)
        for name in s.get("elsets", []):
            elsets.append(f"{inst}.{name}")

    schemes = [sc for sc in order if sc in schemes_seen]
    schemes.extend(sc for sc in schemes_seen if sc not in schemes)
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
    instance = canon_instance(instance)
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
    instance = canon_instance(instance)
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
    instance = canon_instance(instance)
    etype_arr    = idx.source_elem_etype.get(instance)
    elem_row_arr = idx.render_source_elem_row.get(instance)
    if etype_arr is None or elem_row_arr is None:
        return None

    try:
        if scheme == "section":
            labels = _labels_from_section_id(idx, instance, etype_arr, elem_row_arr)
        elif scheme == "section_assignment":
            labels = _labels_from_section_assignment(idx, instance, etype_arr, elem_row_arr)
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


def _split_instance_prefix(name: str, known_instances) -> Tuple[Optional[str], str]:
    """Split an ``INSTANCE.localname`` element-set name.

    Element-set names are only unique within an instance, so the color-code list
    exposes them as ``INSTANCE.setname`` (e.g. ``OMEGA-1._PickedSet6``). The
    front-end broadcasts the whole selected list to every instance, so each
    instance must recognise which entries are its own.

    Returns ``(owner_instance, local_name)``: if *name* starts with a recognised
    instance prefix, owner is that instance (canonicalised) and local_name is the
    remainder; otherwise owner is None and local_name is *name* unchanged (a bare
    name is treated as local to whichever instance is asking — backward compat).
    """
    dot = name.find(".")
    if dot != -1:
        prefix = canon_instance(name[:dot])
        if prefix in known_instances:
            return prefix, name[dot + 1:]
    return None, name


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

    *set_names* may be ``INSTANCE.setname`` qualified. Entries belonging to a
    *different* instance contribute nothing here (and never raise "not found"):
    every instance receives the full broadcast list and just picks out its own.
    The label kept for a matching face is the *original* (possibly qualified)
    name, so the caller's palette ordering over set_names stays consistent across
    instances.
    """
    from ..infra.manifest_repo import ManifestRepo
    sets_h5 = os.path.join(idx.workspace, "l1", "sets", "sets.h5")
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")

    # Resolve each requested name to the local set name to look up in THIS
    # instance, or None when it belongs to another instance (skip, no error).
    known = set(idx.source_elem_etype.keys())
    lookup_name: Dict[str, Optional[str]] = {}
    for sn in set_names:
        owner, local = _split_instance_prefix(sn, known)
        lookup_name[sn] = None if (owner is not None and owner != instance) else local
    active = [sn for sn in set_names if lookup_name[sn] is not None]

    # Load each active set's label array. Try all four sources in order; first
    # hit wins. Stored under the ORIGINAL name sn (the label), looked up by its
    # local name lk:
    #   1. sets.h5  element_sets/{instance}/{lk}              (INP-exported instance sets)
    #   2. sets.h5  assembly_sets/{lk}/{inst_safe}/elem_labels (assembly-level sets)
    #   3. geometry H5  instance_sets/element_sets/{lk}       (ODB instance sets, always checked)
    #   4. sets.h5  part_sets/*/element_sets/{lk}             (part-level sets via dump_sets)
    inst_safe = instance.replace('/', '__').replace('\\', '__').replace(' ', '_')
    slabels_dict: Dict[str, np.ndarray] = {}
    if os.path.exists(sets_h5):
        with h5py.File(sets_h5, "r") as f:
            for sn in active:
                if sn in slabels_dict:
                    continue
                lk = lookup_name[sn]
                key = f"element_sets/{instance}/{lk}"
                if key in f:
                    slabels_dict[sn] = f[key][:]
                    continue
                asm_key = f"assembly_sets/{lk}/{inst_safe}/elem_labels"
                if asm_key in f:
                    slabels_dict[sn] = f[asm_key][:]
    # Always also check geometry H5 for ODB instance-level sets
    if os.path.exists(geom_h5):
        try:
            with h5py.File(geom_h5, "r") as f:
                for sn in active:
                    if sn in slabels_dict:
                        continue
                    geom_key = f"instance_sets/element_sets/{lookup_name[sn]}"
                    if geom_key in f:
                        slabels_dict[sn] = f[geom_key][:]
        except Exception:
            pass
    # Part-level sets stored in sets.h5 part_sets (search across all parts)
    missing = [sn for sn in active if sn not in slabels_dict]
    if missing and os.path.exists(sets_h5):
        try:
            with h5py.File(sets_h5, "r") as f:
                parts_grp = f.get("part_sets")
                if parts_grp is not None:
                    for sn in missing:
                        lk = lookup_name[sn]
                        for part_safe in parts_grp.keys():
                            pk = f"part_sets/{part_safe}/element_sets/{lk}"
                            if pk in f:
                                slabels_dict[sn] = f[pk][:]
                                break
        except Exception:
            pass
    # Only sets that genuinely belong to THIS instance can be "not found";
    # entries for other instances were filtered out above.
    still_missing = [sn for sn in active if sn not in slabels_dict]
    if still_missing:
        raise ValidationError(
            f"Set(s) not found: {still_missing}", {"set_names": still_missing}
        )
    set_label_arrays: List[Tuple[str, np.ndarray]] = [
        (sn, slabels_dict[sn]) for sn in set_names if sn in slabels_dict
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
# Section assignment helpers (scheme="section_assignment")
#
# IMPORTANT: this is a different concept from the existing scheme="section",
# which colors by *averaging regions* (surface union-find domains, "Region N").
# Here we color by the actual Abaqus *section assignment*: each element belongs
# to exactly one section (via that section's element_set), so element counts are
# well-defined for ALL elements incl. interior — they are the element_set sizes.
# ---------------------------------------------------------------------------

def _safe_set_name(name: str) -> str:
    """Mirror the L1/L2 set-name sanitizer used for instance_sets keys."""
    return name.replace('/', '__').replace('\\', '__').replace(' ', '_')


def _clean_section_name(grp_key: str) -> str:
    """L1 stores section groups as '{i}__{safe(name)}' (new) or the raw name
    (old). Strip the leading numeric index for a human-readable label."""
    m = re.match(r"^\d+__(.+)$", grp_key)
    return m.group(1) if m else grp_key


def _section_assignments(idx: ModelIndex, instance: str):
    """Return OrderedDict {section_name: element_label_array} for *instance*,
    read from L1 geometry: each section group's element_set → its element labels.
    Covers ALL elements (incl. interior). Memoized on the ModelIndex."""
    from collections import OrderedDict
    cache_key = ("section_assign", instance)
    cached = idx.legend_scan_cache.get(cache_key)
    if cached is not None:
        return cached

    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    out: "OrderedDict[str, np.ndarray]" = OrderedDict()
    if not os.path.exists(geom_h5):
        idx.legend_scan_cache[cache_key] = out
        return out
    try:
        with h5py.File(geom_h5, "r") as f:
            sg_root = f.get("sections")
            if sg_root is not None:
                for grp_key in sg_root:
                    eset = f[f"sections/{grp_key}"].attrs.get("element_set", "")
                    if isinstance(eset, bytes):
                        eset = eset.decode("utf-8", errors="replace")
                    if not eset:
                        continue
                    key = f"instance_sets/element_sets/{_safe_set_name(eset)}"
                    if key not in f:
                        continue
                    name   = _clean_section_name(grp_key)
                    labels = f[key][:]
                    if name in out:
                        out[name] = np.concatenate([out[name], labels])
                    else:
                        out[name] = labels
    except Exception:
        pass
    idx.legend_scan_cache[cache_key] = out
    return out


def _has_section_assignments(idx: ModelIndex, instance: str) -> bool:
    """Lightweight check (attrs + key existence only, no array loads) used by
    get_schemes to decide whether to expose the section_assignment scheme."""
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        return False
    try:
        with h5py.File(geom_h5, "r") as f:
            sg_root = f.get("sections")
            if sg_root is None:
                return False
            for grp_key in sg_root:
                eset = f[f"sections/{grp_key}"].attrs.get("element_set", "")
                if isinstance(eset, bytes):
                    eset = eset.decode("utf-8", errors="replace")
                if eset and f"instance_sets/element_sets/{_safe_set_name(eset)}" in f:
                    return True
    except Exception:
        return False
    return False


def _section_assignment_counts(idx: ModelIndex, instance: str) -> Dict[str, int]:
    """{section_name: total element count} over ALL elements (incl. interior)."""
    return {
        name: int(np.unique(labels).size)
        for name, labels in _section_assignments(idx, instance).items()
    }


def _labels_from_section_assignment(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
) -> List[str]:
    """Per-face label = the section name whose element_set owns the face's
    element, else '(none)'. Each element belongs to one section; first match
    wins (assignments are normally non-overlapping)."""
    assignments = _section_assignments(idx, instance)
    Rf = len(etype_arr)
    if not assignments:
        return ["(none)"] * Rf

    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")

    names = list(assignments.keys())
    code  = np.zeros(Rf, dtype=np.int32)   # 0 = "(none)"
    etype_strs = np.array([
        b.tobytes().rstrip(b"\x00").decode("ascii", errors="replace")
        for b in etype_arr
    ])
    try:
        with h5py.File(geom_h5, "r") as f:
            for etype_str in np.unique(etype_strs):
                mask = (etype_strs == etype_str)
                grp  = f.get(f"elements/{etype_str}")
                if grp is None or "labels" not in grp:
                    continue
                elem_labels      = grp["labels"][:]
                face_elem_labels = elem_labels[elem_row_arr[mask]]
                # Reverse order so names[0] wins on overlap.
                for si in range(len(names) - 1, -1, -1):
                    hit            = np.isin(face_elem_labels, assignments[names[si]])
                    code_mask      = code[mask]
                    code_mask[hit] = si + 1
                    code[mask]     = code_mask
    except Exception:
        return ["(none)"] * Rf

    code_to_name = {0: "(none)"}
    for si, n in enumerate(names):
        code_to_name[si + 1] = n
    return [code_to_name[int(c)] for c in code]


# ---------------------------------------------------------------------------
# L1 complete-value helpers (for legend completeness)
# ---------------------------------------------------------------------------

def _all_unique_vals_from_l1(idx: ModelIndex, instance: str, attr_name: str) -> List[str]:
    """
    Read *attr_name* (e.g. 'material_name', 'section_type') from ALL element
    groups in the L1 geometry H5, returning every unique non-empty value.
    Used to ensure the legend lists model-level values, not just surface-visible ones.
    """
    cache_key = ("l1_vals", instance, attr_name)
    cached = idx.legend_scan_cache.get(cache_key)
    if cached is not None:
        return cached

    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        idx.legend_scan_cache[cache_key] = []
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
    result = list(seen)
    idx.legend_scan_cache[cache_key] = result
    return result


def _all_etypes_from_l1(idx: ModelIndex, instance: str) -> List[str]:
    """Return every element-type group name present in the L1 geometry H5."""
    cache_key = ("l1_etypes", instance)
    cached = idx.legend_scan_cache.get(cache_key)
    if cached is not None:
        return cached

    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        idx.legend_scan_cache[cache_key] = []
        return []
    try:
        with h5py.File(geom_h5, "r") as f:
            result = sorted(f.get("elements", {}).keys())
    except Exception:
        result = []
    idx.legend_scan_cache[cache_key] = result
    return result


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
    elif scheme == "section_assignment":
        labels = _labels_from_section_assignment(idx, instance, etype_arr, elem_row_arr)
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
        # For etype / material / section_type / section_assignment: append any
        # values that only exist on interior (non-surface) elements so the
        # legend is complete.
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
        elif scheme == "section_assignment":
            surface_set = set(unique_vals)
            for v in _section_assignments(idx, instance).keys():
                if v and v not in surface_set:
                    unique_vals.append(v)
                    surface_set.add(v)

    val_to_id = {v: i for i, v in enumerate(unique_vals)}

    from ..infra.manifest_repo import ManifestRepo
    overrides = ManifestRepo(idx.workspace).get_legend_overrides(instance, scheme)

    global_colors = _build_global_color_map(idx, scheme)
    known = set(idx.source_elem_etype.keys())

    legend: List[dict] = []
    for i, val in enumerate(unique_vals):
        if not val or val in ("(none)", "(unknown)", "other"):
            auto_rgb = _GREY
        else:
            # elset labels are 'INSTANCE.setname'; the global elset color map is
            # keyed by bare set name, so strip the prefix for the lookup.
            ckey = _split_instance_prefix(val, known)[1] if scheme == "elset" else val
            auto_rgb = global_colors.get(ckey, _GREY)
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

def _surface_elem_counts_per_label(
    labels: List[str],
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
):
    """Count distinct *surface* elements per label.

    `labels` is per-render-face (length Rf), so Counter(labels) over-counts: a
    single element can contribute several surface faces. Every face of one
    element carries the same label, so we deduplicate faces by their source
    element key (etype, elem_row) and tally one element per unique key.

    Only sees elements exposed on the render surface — used for the `section`
    scheme, whose regions are themselves a surface-only concept (averaging
    domains built by union-find over surface adjacency).
    """
    from collections import Counter
    Rf = len(labels)
    if Rf == 0:
        return Counter()
    labels_arr = np.asarray(labels)
    # Encode the S8 etype bytes to small int codes, then dedup (etype, elem_row).
    _, etype_codes = np.unique(etype_arr, return_inverse=True)
    pairs = np.stack(
        [etype_codes.astype(np.int64), np.asarray(elem_row_arr, dtype=np.int64)],
        axis=1,
    )
    _, first_face_of_elem = np.unique(pairs, axis=0, return_index=True)
    return Counter(labels_arr[first_face_of_elem].tolist())


def _l1_etype_counts(idx: ModelIndex, instance: str) -> Dict[str, int]:
    """Total element count per element type from L1 geometry (all elements,
    incl. interior). Keyed by the etype string (== the legend_key for scheme=etype)."""
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    counts: Dict[str, int] = {}
    if not os.path.exists(geom_h5):
        return counts
    try:
        with h5py.File(geom_h5, "r") as f:
            for etype_str in f.get("elements", {}):
                grp = f[f"elements/{etype_str}"]
                if "labels" in grp:
                    counts[etype_str] = int(grp["labels"].shape[0])
    except Exception:
        pass
    return counts


def _l1_attr_counts(idx: ModelIndex, instance: str, attr_name: str) -> Dict[str, int]:
    """Total element count per attribute value (material_name / section_type)
    from L1 geometry, over ALL elements. Vectorized with np.unique per etype group."""
    from collections import Counter
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    counts: Counter = Counter()
    if not os.path.exists(geom_h5):
        return dict(counts)
    try:
        with h5py.File(geom_h5, "r") as f:
            for etype_str in f.get("elements", {}):
                grp = f[f"elements/{etype_str}"]
                n_elem = int(grp["labels"].shape[0]) if "labels" in grp else 0
                if attr_name not in grp:
                    if n_elem:
                        counts["(none)"] += n_elem
                    continue
                vals, cnts = np.unique(grp[attr_name][:], return_counts=True)
                for v, c in zip(vals, cnts):
                    s = v.tobytes().rstrip(b"\x00").decode("ascii", errors="replace") or "(none)"
                    counts[s] += int(c)
    except Exception:
        pass
    return dict(counts)


def _full_element_arrays(idx: ModelIndex, instance: str):
    """Build per-element (etype_arr [T] S-bytes, elem_row_arr [T] int32) covering
    ALL elements of the instance, grouped by etype. elem_row is the 0-based row
    within each etype's L1 datasets. Lets the per-face label helpers be reused to
    label every element (not just surface faces). Returns (None, None) if missing."""
    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")
    if not os.path.exists(geom_h5):
        return None, None
    etype_chunks: List[np.ndarray] = []
    row_chunks: List[np.ndarray] = []
    try:
        with h5py.File(geom_h5, "r") as f:
            for etype_str in f.get("elements", {}):
                grp = f[f"elements/{etype_str}"]
                if "labels" not in grp:
                    continue
                n = int(grp["labels"].shape[0])
                if n == 0:
                    continue
                etype_chunks.append(np.full(n, etype_str.encode("ascii"), dtype="S16"))
                row_chunks.append(np.arange(n, dtype=np.int32))
    except Exception:
        return None, None
    if not etype_chunks:
        return np.empty(0, dtype="S16"), np.empty(0, dtype=np.int32)
    return np.concatenate(etype_chunks), np.concatenate(row_chunks)


def _total_elem_counts_per_label(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]],
    surface_counts,
):
    """Actual element count per label over ALL elements (incl. interior).

    - etype / material / section_type: counted directly from L1 per-element
      datasets — exact totals.
    - section_assignment: element_set size per section (exact totals).
    - elset: relabel every element (reusing _labels_from_elsets) and tally.
    - section: regions are a surface-only concept, so fall back to surface_counts.

    Results (other than section) are memoized on idx.legend_scan_cache since L1 is
    immutable.
    """
    from collections import Counter
    if scheme == "section":
        return surface_counts

    set_key = tuple(set_names) if set_names else ()
    cache_key = ("total_counts", instance, scheme, set_key)
    cached = idx.legend_scan_cache.get(cache_key)
    if cached is not None:
        return cached

    if scheme == "etype":
        counts = _l1_etype_counts(idx, instance)
    elif scheme in ("material", "section_type"):
        attr_name = "material_name" if scheme == "material" else "section_type"
        counts = _l1_attr_counts(idx, instance, attr_name)
    elif scheme == "section_assignment":
        counts = _section_assignment_counts(idx, instance)
    elif scheme == "elset":
        full_etype, full_row = _full_element_arrays(idx, instance)
        if full_etype is None or len(full_etype) == 0:
            counts = {}
        else:
            labels = _labels_from_elsets(idx, instance, full_etype, full_row, set_names or [])
            counts = dict(Counter(labels))
    else:
        counts = dict(surface_counts)

    idx.legend_scan_cache[cache_key] = counts
    return counts


def get_all_legend_entries(
    idx: ModelIndex,
    scheme: str,
    set_names: Optional[List[str]] = None,
) -> List[dict]:
    """Return legend entries for all instances.

    For section / section_assignment schemes: labels are instance-scoped (a region
    or a section name may differ between instances), so entries are kept per
    instance with an 'instance' field.
    For other schemes (etype/material/section_type): legend_key is shared across instances,
    so entries are deduplicated by legend_key and face_count is summed.
    """
    all_entries: List[dict] = []
    for inst in sorted(idx.source_elem_etype.keys()):
        try:
            entries = get_legend_entries(idx, inst, scheme, set_names)
            for e in entries:
                e["instance"] = inst
            all_entries.extend(entries)
        except Exception:
            pass

    # For schemes where labels are globally consistent, deduplicate by legend_key
    if scheme not in ("section", "section_assignment", "elset"):
        merged: Dict[str, dict] = {}
        for e in all_entries:
            key = e["legend_key"]
            if key not in merged:
                merged[key] = dict(e)
            else:
                merged[key]["face_count"] = merged[key].get("face_count", 0) + e.get("face_count", 0)
                merged[key]["elem_count"] = merged[key].get("elem_count", 0) + e.get("elem_count", 0)
        all_entries = list(merged.values())
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
    instance = canon_instance(instance)
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
    elif scheme == "section_assignment":
        labels = _labels_from_section_assignment(idx, instance, etype_arr, elem_row_arr)
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
    # elem_count = actual total elements per label (incl. interior, from L1),
    # except scheme=section where regions are surface-defined.
    surface_elem_counts = _surface_elem_counts_per_label(labels, etype_arr, elem_row_arr)
    elem_counts = _total_elem_counts_per_label(
        idx, instance, scheme, set_names, surface_elem_counts
    )

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
        elif scheme == "section_assignment":
            surface_set = set(unique_vals)
            for v in _section_assignments(idx, instance).keys():
                if v and v not in surface_set:
                    unique_vals.append(v)
                    surface_set.add(v)

    # Palette assignment (identical logic to get_color_code)
    global_colors = _build_global_color_map(idx, scheme)
    known = set(idx.source_elem_etype.keys())
    palette_colors: Dict[str, Tuple[float, float, float]] = {}
    for val in unique_vals:
        if not val or val in ("(none)", "(unknown)", "other"):
            rgb = _GREY
        else:
            # elset labels are 'INSTANCE.setname'; global elset colors are keyed
            # by bare set name, so strip the prefix for the lookup.
            ckey = _split_instance_prefix(val, known)[1] if scheme == "elset" else val
            rgb = global_colors.get(ckey, _GREY)
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
            "elem_count":    elem_counts.get(val, 0),
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
    id_to_label = {did: f"{instance}.Region_{i + 1}" for i, did in enumerate(unique_ids)}
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
