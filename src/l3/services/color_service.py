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
from . import cache_store

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
        dom = np.asarray(avd["default_domain_id"])
        unique_ids = np.unique(dom[dom >= 0])
        for i in range(len(unique_ids)):
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
    # 结果只由各实例几何文件、sets.h5 与高级索引决定；每次都要开这些文件（约 100 ms），
    # 按文件签名缓存，文件重写或高级索引重载后自动失效。
    from ..infra.manifest_repo import ManifestRepo
    manifest = ManifestRepo(idx.workspace)
    geom_sigs = []
    for inst in sorted(idx.source_elem_etype.keys()):
        path = manifest.get_geom_path(canon_instance(inst)) or \
            os.path.join(idx.workspace, "l1", "geometry", f"{inst}.h5")
        geom_sigs.append((inst, path, cache_store.file_sig(path)))
    key = ("color_schemes", idx.workspace, tuple(geom_sigs),
           cache_store.file_sig(os.path.join(idx.workspace, "l1", "sets", "sets.h5")),
           int(getattr(idx, "_advanced_status_mtime_ns", 0)))
    hit = cache_store.small_get(key)
    if hit is not cache_store.MISS:
        return {"schemes": list(hit["schemes"]), "elsets": list(hit["elsets"])}
    result = _get_all_schemes_uncached(idx)
    cache_store.small_put(key, result)
    return {"schemes": list(result["schemes"]), "elsets": list(result["elsets"])}


def _get_all_schemes_uncached(idx: ModelIndex) -> dict:
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

    face_ids, legend = _compute_labels_and_legend(
        idx, instance, scheme, set_names, etype_arr, elem_row_arr
    )

    pal_arr     = np.array([(e["r"], e["g"], e["b"]) for e in legend],
                           dtype=np.float32).reshape(-1, 3)
    face_colors = pal_arr[face_ids]   # [Rf, 3]

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

    _, legend = _compute_labels_and_legend(
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

    if scheme not in _SCHEMES:
        return None
    try:
        # elset: labels are the set name or "other"; region IS the set name
        codes, vals = _face_codes(idx, instance, scheme,
                                  [region] if scheme == "elset" else None,
                                  etype_arr, elem_row_arr)
    except Exception:
        return None
    if region not in vals:
        return np.zeros(len(codes), dtype=bool)
    return codes == vals.index(region)


# 着色分类统一用"编码"表示：codes [Rf] int32 + vals List[str]，第 i 个面的标签
# = vals[codes[i]]。几百万面片时逐面构造 Python 字符串列表本身就要数秒，
# 所有计算都在 numpy 整数数组上完成，只有去重后的少量取值才解码成字符串。

_SCHEMES = ("etype", "section", "section_assignment", "material", "section_type", "elset")


def _decode(b) -> str:
    return bytes(b).rstrip(b"\x00").decode("ascii", errors="replace")


def _etype_groups(etype_arr: np.ndarray) -> Tuple[List[str], np.ndarray]:
    """(去重后的 etype 字符串, 每个面在其中的下标)：每种 etype 只解码一次。"""
    uniq, inv = np.unique(np.asarray(etype_arr), return_inverse=True)
    return [_decode(u) for u in uniq], inv.reshape(-1)


def _compact_codes(codes: np.ndarray, vals: List[str]) -> Tuple[np.ndarray, List[str]]:
    """把 vals 重排成"按面首次出现的顺序"、去掉未出现与重复的取值，并重映射 codes。

    与旧实现 list(dict.fromkeys(labels)) 的图例顺序完全一致。
    """
    codes = np.asarray(codes, dtype=np.int64).reshape(-1)
    if codes.size == 0:
        return np.zeros(0, dtype=np.int32), []
    present, first = np.unique(codes, return_index=True)
    order = present[np.argsort(first, kind="stable")]
    remap = np.zeros(len(vals), dtype=np.int32)
    new_vals: List[str] = []
    pos: Dict[str, int] = {}
    for i in order:
        v = vals[int(i)]
        if v not in pos:
            pos[v] = len(new_vals)
            new_vals.append(v)
        remap[int(i)] = pos[v]
    return remap[codes], new_vals


def _domain_per_face(avd: dict, etype_arr: np.ndarray, elem_row_arr: np.ndarray) -> np.ndarray:
    """每个渲染面所属的均化域 id（render.h5 averaging_data 查表），查不到为 -1。

    等价于旧实现的 {(etype, elem_row): domain_id} 字典逐面查找（重复键后者覆盖前者），
    改为 (etype 编码, elem_row) 合成 int64 键后排序 + searchsorted。
    """
    a_et = np.asarray(avd["elem_etype"])
    f_et = np.asarray(etype_arr)
    Rf = len(f_et)
    if len(a_et) == 0 or Rf == 0:
        return np.full(Rf, -1, dtype=np.int32)
    width = max(a_et.dtype.itemsize if a_et.dtype.kind == "S" else 32,
                f_et.dtype.itemsize if f_et.dtype.kind == "S" else 32)
    dt = f"S{width}"
    _, inv = np.unique(np.concatenate([a_et.astype(dt), f_et.astype(dt)]),
                       return_inverse=True)
    inv = inv.reshape(-1).astype(np.int64)
    a_row = np.asarray(avd["elem_row"], dtype=np.int64)
    f_row = np.asarray(elem_row_arr, dtype=np.int64)
    base = int(max(a_row.max(), f_row.max())) + 1
    a_key = inv[:len(a_et)] * base + a_row
    f_key = inv[len(a_et):] * base + f_row
    # 重复键取最后一次出现（与 dict 覆盖语义一致）：反转后取首次
    uk, ui = np.unique(a_key[::-1], return_index=True)
    dom = np.asarray(avd["default_domain_id"]).reshape(-1)[::-1][ui]
    pos = np.clip(np.searchsorted(uk, f_key), 0, len(uk) - 1)
    hit = uk[pos] == f_key
    return np.where(hit, dom[pos], -1).astype(np.int32)


def _codes_from_etype(etype_arr: np.ndarray) -> Tuple[np.ndarray, List[str]]:
    names, inv = _etype_groups(etype_arr)
    return inv.astype(np.int32), names


def _codes_from_section_id(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
) -> Tuple[np.ndarray, List[str]]:
    """Color by averaging region — domain ID from render.h5 averaging_data (in memory).

    Region 编号按域 id 升序：f"{instance}.Region_{i+1}"；查不到域的面为 "(none)"。
    """
    Rf = len(etype_arr)
    avd = idx.averaging_data.get(instance)
    if avd is None:
        return np.zeros(Rf, dtype=np.int32), ["(none)"]
    domain_per_face = _domain_per_face(avd, etype_arr, elem_row_arr)
    if not np.any(domain_per_face >= 0):
        return np.zeros(Rf, dtype=np.int32), ["(none)"]
    unique_ids = np.unique(domain_per_face[domain_per_face >= 0])
    codes = np.zeros(Rf, dtype=np.int32)                      # 0 = "(none)"
    ok = domain_per_face >= 0
    codes[ok] = np.searchsorted(unique_ids, domain_per_face[ok]).astype(np.int32) + 1
    vals = ["(none)"] + [f"{instance}.Region_{i + 1}" for i in range(len(unique_ids))]
    return codes, vals


def _codes_from_elem_attr(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
    attr_name: str,
) -> Tuple[np.ndarray, List[str]]:
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
    etype_names, etype_inv = _etype_groups(etype_arr)
    elem_row_arr = np.asarray(elem_row_arr)

    with h5py.File(geom_h5, "r") as f:
        for gi, etype_str in enumerate(etype_names):
            grp = f.get(f"elements/{etype_str}")
            if grp is None or attr_name not in grp:
                continue
            mask         = etype_inv == gi
            attr_data    = grp[attr_name][:]     # [M] S64/S16
            result[mask] = attr_data[elem_row_arr[mask]]

    uniq, inv = np.unique(result, return_inverse=True)
    return inv.reshape(-1).astype(np.int32), [_decode(u) or "(none)" for u in uniq]


def _codes_to_labels(codes: np.ndarray, vals: List[str]) -> List[str]:
    lut = np.array(vals, dtype=object) if vals else np.zeros(0, dtype=object)
    return lut[np.asarray(codes, dtype=np.int64)].tolist()


def _labels_from_etype(etype_arr: np.ndarray) -> List[str]:
    """Decode the S8 bytes array into clean etype strings."""
    return _codes_to_labels(*_codes_from_etype(etype_arr))


def _labels_from_section_id(idx, instance, etype_arr, elem_row_arr) -> List[str]:
    return _codes_to_labels(*_codes_from_section_id(idx, instance, etype_arr, elem_row_arr))


def _labels_from_elem_attr(idx, instance, etype_arr, elem_row_arr, attr_name) -> List[str]:
    return _codes_to_labels(
        *_codes_from_elem_attr(idx, instance, etype_arr, elem_row_arr, attr_name))


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
    return _codes_to_labels(
        *_codes_from_elsets(idx, instance, etype_arr, elem_row_arr, set_names))


def _codes_from_elsets(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
    set_names: List[str],
) -> Tuple[np.ndarray, List[str]]:
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
    etype_names, etype_inv = _etype_groups(etype_arr)
    elem_row_arr = np.asarray(elem_row_arr)

    with h5py.File(geom_h5, "r") as f:
        for gi, etype_str in enumerate(etype_names):
            mask      = etype_inv == gi
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

    return face_set, ["other"] + [sn for sn, _ in set_label_arrays]


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


def _labels_from_section_assignment(idx, instance, etype_arr, elem_row_arr) -> List[str]:
    return _codes_to_labels(
        *_codes_from_section_assignment(idx, instance, etype_arr, elem_row_arr))


def _codes_from_section_assignment(
    idx: ModelIndex,
    instance: str,
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
) -> Tuple[np.ndarray, List[str]]:
    """Per-face label = the section name whose element_set owns the face's
    element, else '(none)'. Each element belongs to one section; first match
    wins (assignments are normally non-overlapping)."""
    assignments = _section_assignments(idx, instance)
    Rf = len(etype_arr)
    if not assignments:
        return np.zeros(Rf, dtype=np.int32), ["(none)"]

    from ..infra.manifest_repo import ManifestRepo
    geom_h5 = ManifestRepo(idx.workspace).get_geom_path(instance) or \
              os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")

    names = list(assignments.keys())
    code  = np.zeros(Rf, dtype=np.int32)   # 0 = "(none)"
    etype_names, etype_inv = _etype_groups(etype_arr)
    elem_row_arr = np.asarray(elem_row_arr)
    try:
        with h5py.File(geom_h5, "r") as f:
            for gi, etype_str in enumerate(etype_names):
                mask = etype_inv == gi
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
        return np.zeros(Rf, dtype=np.int32), ["(none)"]

    return code, ["(none)"] + names


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

def _geom_h5_path(idx: ModelIndex, instance: str) -> str:
    from ..infra.manifest_repo import ManifestRepo
    return ManifestRepo(idx.workspace).get_geom_path(instance) or \
        os.path.join(idx.workspace, "l1", "geometry", f"{instance}.h5")


def _face_codes(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]],
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
) -> Tuple[np.ndarray, List[str]]:
    """每个渲染面的分类编码（已按首次出现顺序压缩）：(codes [Rf] int32 只读, vals)。

    分类只取决于模型几何（L1 geometry / sets、L2 render.h5），导入后不再变化 →
    走统一缓存：内存层 + 工作区磁盘 model 桶（scheme=elset 的集合组合无穷，只进内存）。
    key 带 render.h5 / L1 几何 / sets.h5 签名，L2 重跑后自然失效。用户改色/改名
    （legend overrides）不影响这里，每次请求在编码之上现查。
    """
    if scheme not in _SCHEMES:
        raise ValidationError(f"Unknown scheme '{scheme}'", {"scheme": scheme})
    if scheme == "elset" and not set_names:
        raise ValidationError("set_names required for elset scheme", {})

    ws = idx.workspace
    sets_sig = cache_store.file_sig(os.path.join(ws, "l1", "sets", "sets.h5")) \
        if scheme == "elset" else None
    key = ("color_codes", ws, instance, scheme,
           tuple(set_names) if scheme == "elset" else (),
           cache_store.render_sig(ws, instance),
           cache_store.file_sig(_geom_h5_path(idx, instance)), sets_sig,
           len(etype_arr),
           # section / region 分类依赖 L2 高级阶段的 averaging_data，高级索引
           # 加载前后结果不同；与 result_service 的 advanced_version 同口径
           int(getattr(idx, "_advanced_status_mtime_ns", 0)))
    hit = cache_store.mem_get(key)
    if hit is not None:
        return hit
    to_disk = scheme != "elset"
    if to_disk:
        d = cache_store.arrays_get(ws, cache_store.MODEL_BUCKET, key)
        if d is not None and "codes" in d and "vals" in d:
            value = (d["codes"], json.loads(bytes(d["vals"]).decode("utf-8")))
            cache_store.mem_put(key, value)
            return value

    if scheme == "etype":
        codes, vals = _codes_from_etype(etype_arr)
    elif scheme == "section":
        codes, vals = _codes_from_section_id(idx, instance, etype_arr, elem_row_arr)
    elif scheme == "section_assignment":
        codes, vals = _codes_from_section_assignment(idx, instance, etype_arr, elem_row_arr)
    elif scheme in ("material", "section_type"):
        attr_name = "material_name" if scheme == "material" else "section_type"
        codes, vals = _codes_from_elem_attr(idx, instance, etype_arr, elem_row_arr, attr_name)
    else:
        codes, vals = _codes_from_elsets(idx, instance, etype_arr, elem_row_arr, set_names)

    codes, vals = _compact_codes(codes, vals)
    codes.setflags(write=False)
    value = (codes, vals)
    cache_store.mem_put(key, value)
    if to_disk:
        cache_store.arrays_put(ws, cache_store.MODEL_BUCKET, key, {
            "codes": codes,
            "vals": np.frombuffer(json.dumps(vals).encode("utf-8"), dtype=np.uint8),
        })
    return value


def _legend_values(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]],
    vals: List[str],
) -> List[str]:
    """图例取值顺序：表面出现顺序，etype/material/section_type/section_assignment
    再补上只存在于内部单元的取值；elset 按 set_names 顺序 + other。"""
    if scheme == "elset" and set_names:
        present = set(vals)
        return [v for v in list(set_names) + ["other"] if v in present]

    unique_vals = list(vals)
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
    return unique_vals


def _compute_labels_and_legend(
    idx: ModelIndex,
    instance: str,
    scheme: str,
    set_names: Optional[List[str]],
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
) -> Tuple[np.ndarray, List[dict]]:
    """Compute per-face legend ids and build the legend list.

    Returns (face_ids, legend) where:
      face_ids — [Rf] int32, index into legend for every render face
      legend   — [{id, legend_key, name, r, g, b}, ...]
    """
    codes, vals = _face_codes(idx, instance, scheme, set_names, etype_arr, elem_row_arr)
    unique_vals = _legend_values(idx, instance, scheme, set_names, vals)
    val_to_id = {v: i for i, v in enumerate(unique_vals)}
    code_to_id = np.array([val_to_id[v] for v in vals], dtype=np.int32)
    face_ids = code_to_id[codes] if len(vals) else np.zeros(len(codes), dtype=np.int32)

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

    return face_ids, legend


def warm_color_codes(idx: ModelIndex, strict: bool = False) -> int:
    """预计算所有 instance × 可用 scheme（除 elset）的面分类编码，返回成功个数。"""
    n = 0
    for inst in sorted(idx.source_elem_etype.keys()):
        etype_arr    = idx.source_elem_etype.get(inst)
        elem_row_arr = idx.render_source_elem_row.get(inst)
        if etype_arr is None or elem_row_arr is None:
            continue
        try:
            schemes = get_schemes(idx, inst)["schemes"]
        except Exception:
            if strict:
                raise
            schemes = ["etype"]
        for scheme in schemes:
            if scheme == "elset":
                continue
            try:
                _face_codes(idx, inst, scheme, None, etype_arr, elem_row_arr)
                n += 1
            except Exception:
                if strict:
                    raise
                pass
    return n


# ---------------------------------------------------------------------------
# Legend entries (for LegendEditor floating panel)
# ---------------------------------------------------------------------------

def _surface_elem_counts_per_label(
    codes: np.ndarray,
    vals: List[str],
    etype_arr: np.ndarray,
    elem_row_arr: np.ndarray,
):
    """Count distinct *surface* elements per label.

    Per-face counts over-count: a single element can contribute several surface
    faces. Every face of one element carries the same label, so we deduplicate
    faces by their source element key (etype, elem_row) and tally one element
    per unique key.

    Only sees elements exposed on the render surface — used for the `section`
    scheme, whose regions are themselves a surface-only concept (averaging
    domains built by union-find over surface adjacency).
    """
    from collections import Counter
    if len(codes) == 0:
        return Counter()
    _, etype_codes = np.unique(np.asarray(etype_arr), return_inverse=True)
    rows = np.asarray(elem_row_arr, dtype=np.int64)
    elem_key = etype_codes.reshape(-1).astype(np.int64) * (int(rows.max()) + 1) + rows
    _, first_face_of_elem = np.unique(elem_key, return_index=True)
    cnt = np.bincount(codes[first_face_of_elem], minlength=len(vals))
    return Counter({vals[i]: int(c) for i, c in enumerate(cnt) if c})


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

    codes, vals = _face_codes(idx, instance, scheme, set_names, etype_arr, elem_row_arr)
    counts_arr = np.bincount(codes, minlength=len(vals)) if len(vals) else []
    face_counts = {v: int(c) for v, c in zip(vals, counts_arr)}
    # elem_count = actual total elements per label (incl. interior, from L1),
    # except scheme=section where regions are surface-defined.
    surface_elem_counts = _surface_elem_counts_per_label(codes, vals, etype_arr, elem_row_arr)
    elem_counts = _total_elem_counts_per_label(
        idx, instance, scheme, set_names, surface_elem_counts
    )

    unique_vals = _legend_values(idx, instance, scheme, set_names, vals)

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
    """For section scheme: map each 'Region N' label → material_name as default title.

    每个域的代表单元 = averaging_data 中该域第一次出现的单元；Region 编号规则与
    _codes_from_section_id 一致（表面出现的域 id 升序）。
    """
    avd = idx.averaging_data.get(instance)
    if avd is None:
        return {}

    dom_all = np.asarray(avd["default_domain_id"]).reshape(-1)
    rep_dids, rep_idx = np.unique(dom_all, return_index=True)
    domain_rep = {int(d): (_decode(avd["elem_etype"][i]), int(avd["elem_row"][i]))
                  for d, i in zip(rep_dids, rep_idx)}

    domain_per_face = _domain_per_face(avd, etype_arr, elem_row_arr)
    unique_ids = np.unique(domain_per_face[domain_per_face >= 0])
    label_to_id = {f"{instance}.Region_{i + 1}": int(did) for i, did in enumerate(unique_ids)}

    geom_h5 = _geom_h5_path(idx, instance)
    domain_mat: Dict[int, str] = {}
    if os.path.exists(geom_h5):
        with h5py.File(geom_h5, "r") as f:
            # 每种单元类型的 material_name 整列只读一次（逐行读 HDF5 时数百个区域
            # 要几百毫秒），取值与逐行读取相同。
            mat_cols: Dict[str, Optional[np.ndarray]] = {}
            for val in unique_vals:
                did = label_to_id.get(val)
                if did is None or did not in domain_rep or did in domain_mat:
                    continue
                etype, row = domain_rep[did]
                if etype not in mat_cols:
                    grp = f.get(f"elements/{etype}")
                    mat_cols[etype] = grp["material_name"][()] \
                        if grp is not None and "material_name" in grp else None
                col = mat_cols[etype]
                domain_mat[did] = _decode(col[row]) if col is not None else ""

    result: Dict[str, str] = {}
    for val in unique_vals:
        if val in ("(none)", "(unknown)"):
            result[val] = val
            continue
        did = label_to_id.get(val)
        mat = domain_mat.get(did, "") if did is not None else ""
        result[val] = mat if mat else val
    return result
