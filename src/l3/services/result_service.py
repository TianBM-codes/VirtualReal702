"""
Frame-color / frame-scalar computation:
  L1 results HDF5 → per-vertex uint8 RGBA or float32 scalars aligned to render geometry.

Position fallback order: NODAL → ELEMENT_NODAL → INTEGRATION_POINT → WHOLE_ELEMENT
"""
import logging
import os
import re
import json
import threading
from collections import OrderedDict
from typing import Dict, Literal, Optional, Tuple

import h5py
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from ..core.errors import NotFoundError, NotReadyError, ValidationError
from ..core.state import OdbRegistry
from ..infra.colormap import apply_jet, apply_jet_with_neutral
from ..infra.manifest_repo import ManifestRepo
from src.l1.manifest_schema import canon_instance
from . import cache_store

logger = logging.getLogger(__name__)

Component = Literal["U1", "U2", "U3"]


# ─── Result caches ───────────────────────────────────────────────────────────
# 内存层 + 工作区磁盘层统一由 cache_store 管理（容量、开关、淘汰、目录布局见
# 该模块与 docs/l3/L3-Precompute-Cache-Design.md）。这里只保留结果计算专用的
# 薄封装：所有 key 都含结果文件 (mtime_ns, size) 签名，依赖渲染几何的 key 另含
# render.h5 签名；磁盘条目按结果组分桶（cache_store.group_bucket）。

_CACHE_MISS = cache_store.MISS
_result_h5_sig = cache_store.file_sig
_vertex_cache_get = cache_store.mem_get
_vertex_cache_put = cache_store.mem_put
_range_cache_get = cache_store.small_get
_range_cache_put = cache_store.small_put


def clear_result_caches() -> None:
    """清空内存结果缓存（测试与运维用）。磁盘缓存不动，删工作区即删。"""
    global _FRAME_MEMO_BYTES
    cache_store.clear_memory()
    with _FRAME_MEMO_LOCK:
        _FRAME_MEMO.clear()
        _FRAME_MEMO_BYTES = 0
    with _EN_INDEX_LOCK:
        _EN_GEOM_CACHE.clear()
        _EN_RANGE_INDEX.clear()
    _STORED_MAG_MEMO.clear()


def _vertex_disk_get(workspace: str, bucket: str, key: tuple) -> Optional[tuple]:
    """frame_scalars 条目 → (scalar_vertex, global_range, result_position, num_frames)。"""
    d = cache_store.arrays_get(workspace, bucket, key)
    if d is None:
        return None
    try:
        gr = d["global_range"]
        global_range = (float(gr[0]), float(gr[1])) if gr.size == 2 else None
        result_position = bytes(d["result_position"][()]).decode("ascii")
        nf = int(d["num_frames"][()])
        return d["scalar_vertex"], global_range, result_position, (None if nf < 0 else nf)
    except (KeyError, ValueError, IndexError):
        return None


def _vertex_disk_put(workspace: str, bucket: str, key: tuple, value: tuple) -> None:
    scalar_vertex, global_range, result_position, num_frames = value
    cache_store.arrays_put(workspace, bucket, key, {
        "scalar_vertex": scalar_vertex,
        "global_range": (np.asarray(global_range, dtype=np.float64)
                         if global_range is not None
                         else np.zeros(0, dtype=np.float64)),
        "result_position": np.array(result_position.encode("ascii")),
        "num_frames": np.array(-1 if num_frames is None else int(num_frames),
                               dtype=np.int64),
    })


def _range_disk_get(workspace: str, bucket: str, key: tuple):
    """命中返回 (min, max) 或 None，未命中返回 _CACHE_MISS。"""
    v = cache_store.json_get(workspace, bucket, key)
    if v is _CACHE_MISS or v is None:
        return v
    return float(v[0]), float(v[1])


def _range_disk_put(workspace: str, bucket: str, key: tuple, value) -> None:
    cache_store.json_put(
        workspace, bucket, key,
        None if value is None else [float(value[0]), float(value[1])],
    )


# ─── Generic component extraction ────────────────────────────────────────────

_STORED_MAGNITUDE_COMPONENTS = ("USUM",)
_STORED_MAG_MEMO: Dict[tuple, Tuple[Optional[int], bool]] = {}


def stored_magnitude_col(f) -> Optional[int]:
    """
    结果文件自带的合量列下标（OP2 位移在 U1/U2/U3 后附了现成的 USUM 列），没有返回 None。

    对这类数据求模长前必须先去掉该列，否则 √(U1²+U2²+U3²+USUM²) = √2·|U|。
    只认 meta/components 里明确登记的名字，ODB 等不带合量列的文件不受影响。
    """
    if "meta/components" not in f:
        return None
    names = [c.decode() if isinstance(c, bytes) else str(c)
             for c in f["meta/components"][()]]
    for i, name in enumerate(names):
        if name.upper() in _STORED_MAGNITUDE_COMPONENTS:
            return i if len(names) > 1 else None
    return None


def drop_stored_magnitude(data: np.ndarray, mag_col: Optional[int]) -> np.ndarray:
    """去掉 data 最后一维里的自带合量列，供求模长使用；mag_col 为 None 或不匹配时原样返回。"""
    data = np.asarray(data)
    if mag_col is None or data.ndim == 0 or data.shape[-1] < 2 or mag_col >= data.shape[-1]:
        return data
    return np.delete(data, mag_col, axis=-1)


def _component_cache_token(h5_path: str, h5_sig, component_idx: Optional[int]):
    """
    缓存键里的分量部分。默认分量（None）在带自带合量列的文件上改为 "mag-excl-USUM"，
    让修复 √2 合量 bug 之前写入的错误缓存不再命中。含 WHOLE_ELEMENT 块的文件
    （如 STATUS）再包一层 ("we", ...)：支持该位置之前缓存的"无范围"不再命中。
    其他文件的键保持不变。
    """
    memo_key = (h5_path, h5_sig)
    if memo_key not in _STORED_MAG_MEMO:
        try:
            with h5py.File(h5_path, "r") as f:
                _STORED_MAG_MEMO[memo_key] = (stored_magnitude_col(f),
                                              "WHOLE_ELEMENT" in f)
        except OSError:
            return None if component_idx is None else int(component_idx)
    mag_col, has_whole_element = _STORED_MAG_MEMO[memo_key]
    if component_idx is not None:
        token = int(component_idx)
    else:
        token = "mag-excl-USUM" if mag_col is not None else None
    return ("we", token) if has_whole_element else token


# 原始结果帧的进程内复用：全量准备时同一帧要按多个分量、smooth/flat、全模型
# 范围反复读取（一帧 S 约 118 MB，读一次 0.24 s）。只是少读几遍文件，数据与
# 直接读取逐字节相同；key 含结果文件签名，文件重写后自然失效。
_FRAME_MEMO: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_FRAME_MEMO_BYTES = 0
_FRAME_MEMO_CAP = 256 * 1024 * 1024
_FRAME_MEMO_LOCK = threading.Lock()


def _frame_slice(ds, frame_idx: int):
    """ds[frame_idx]，同一结果文件的同一帧在进程内只读一次。返回只读数组。"""
    global _FRAME_MEMO_BYTES
    path = ds.file.filename
    sig = cache_store.file_sig(path)
    key = (path, sig, ds.name, int(frame_idx))
    if sig is not None:
        with _FRAME_MEMO_LOCK:
            hit = _FRAME_MEMO.get(key)
            if hit is not None:
                _FRAME_MEMO.move_to_end(key)
                return hit
    data = ds[frame_idx]
    if sig is None or not isinstance(data, np.ndarray) or data.nbytes > _FRAME_MEMO_CAP // 2:
        return data
    data.setflags(write=False)
    with _FRAME_MEMO_LOCK:
        if key not in _FRAME_MEMO:
            _FRAME_MEMO[key] = data
            _FRAME_MEMO_BYTES += data.nbytes
            while _FRAME_MEMO_BYTES > _FRAME_MEMO_CAP and _FRAME_MEMO:
                _, old = _FRAME_MEMO.popitem(last=False)
                _FRAME_MEMO_BYTES -= old.nbytes
    return data


def _extract_component(data: np.ndarray, component_idx: Optional[int],
                       mag_col: Optional[int] = None) -> np.ndarray:
    """
    Extract a scalar from the last axis of data.

    data:          [..., ncomp] or scalar [...]
    component_idx: int → use as direct index into last axis
                   None → magnitude (L2 norm) for vector fields, but the raw
                          signed value for scalar fields (ncomp == 1)
    mag_col:       stored magnitude column (see stored_magnitude_col), excluded
                   from the norm
    Returns float32 array with one fewer dimension.
    """
    if data.ndim == 1:
        return data.astype(np.float32)
    if component_idx is None:
        # Scalar fields (e.g. Abaqus invariants S_PRESS / S_INV3 / principal
        # stresses, which are stored as ncomp==1) must keep their sign. Taking the
        # L2 norm of a single component is just abs(), which flips negative values
        # positive and makes the cloud map disagree with Abaqus. Only true vector
        # fields (U, RF, …) should collapse to a magnitude.
        if data.shape[-1] == 1:
            return data[..., 0].astype(np.float32)
        return np.linalg.norm(drop_stored_magnitude(data, mag_col), axis=-1).astype(np.float32)
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
    frame_data = _frame_slice(ds, frame_idx)   # [N, ncomp] or [N]
    # Collapse extra dims (e.g. [N, 1, ncomp] from some exporters)
    while frame_data.ndim > 2:
        frame_data = frame_data.mean(axis=1)
    return _extract_component(frame_data, component_idx, stored_magnitude_col(f)), num_frames


def _expand_sparse_nodal_to_geometry_rows(
    *,
    f,
    instance: str,
    scalar_node: np.ndarray,
    workspace: str,
) -> np.ndarray:
    """
    Expand sparse NODAL datasets that store explicit node labels instead of a
    dense geometry-row-aligned array.

    Sensitivity files may only contain values for a handful of response nodes
    and accompany `/NODAL/<instance>/data` with `/NODAL/<instance>/labels`.
    Rendering code indexes by geometry node row, so remap those labels back
    onto the full geometry node label array and fill all other rows with NaN.
    """
    labels_path = f"/NODAL/{instance}/labels"
    if labels_path not in f:
        return scalar_node

    result_labels = np.asarray(f[labels_path][:], dtype=np.int32).reshape(-1)
    scalar_arr = np.asarray(scalar_node, dtype=np.float32).reshape(-1)
    if result_labels.size == 0 or scalar_arr.size == 0:
        return scalar_arr

    try:
        manifest = ManifestRepo(workspace)
        geom_h5_path = manifest.get_geom_path(instance)
    except Exception:
        geom_h5_path = None
    if not geom_h5_path:
        geom_h5_path = os.path.join(workspace, "l1", "geometry", f"{canon_instance(instance)}.h5")
    if not geom_h5_path or not os.path.exists(geom_h5_path):
        return scalar_arr

    try:
        with h5py.File(geom_h5_path, "r") as geom_f:
            if "nodes/labels" not in geom_f:
                return scalar_arr
            geom_labels = np.asarray(geom_f["nodes/labels"][:], dtype=np.int32).reshape(-1)
    except Exception:
        logger.exception(
            "failed to read geometry labels for sparse nodal remap: workspace=%s instance=%s",
            workspace,
            instance,
        )
        return scalar_arr

    expanded = np.full(len(geom_labels), np.nan, dtype=np.float32)
    rows = np.searchsorted(geom_labels, result_labels)
    valid = (
        (rows >= 0)
        & (rows < len(geom_labels))
        & (geom_labels[rows] == result_labels)
    )
    if not np.any(valid):
        return scalar_arr
    expanded[rows[valid]] = scalar_arr[valid]
    return expanded


def _mask_partial_nan_triangles_soup(scalar_vertex: np.ndarray) -> np.ndarray:
    """
    Triangle Soup [Nt*3]：稀疏 NODAL 场（接触输出 CPRESS/CSHEAR 等）里，接触面
    边缘节点会被相邻侧面的三角形共享，逐顶点插值会把颜色"拖"到无数据的侧面上
    （云图溢出）。这里把"三个角只要有一个 NaN"的三角形整体置 NaN（前端渲灰），
    得到与 Abaqus 一致的干净单面边界。只适用于 soup 顶点序（长度为 3 的倍数）；
    稠密场没有 NaN，原样返回，行为不变。
    """
    if scalar_vertex.size == 0 or scalar_vertex.size % 3 != 0:
        return scalar_vertex
    if not np.isnan(scalar_vertex).any():
        return scalar_vertex
    tri = scalar_vertex.reshape(-1, 3).copy()
    tri[np.isnan(tri).any(axis=1)] = np.nan
    return tri.reshape(-1)


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
    comp_oob_all = True   # 选中分量是否对所有 etype 块都越界(=该实例无此分量)

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

        frame_data = _frame_slice(ds, frame_idx)

        # Average over middle dimensions (local_nodes or ips) until [N_elem, ncomp] or [N_elem]
        while frame_data.ndim > 2:
            frame_data = frame_data.mean(axis=1)

        # 该块是否真的有这个分量(壳块只有 4 分量, 选 S13/S23 时越界)。
        if (component_idx is None or frame_data.ndim == 1
                or int(component_idx) < frame_data.shape[-1]):
            comp_oob_all = False

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
    if valid_face.size == 0:
        # 区分两种"全 NaN":
        #   (a) 选中分量对该实例所有单元类型都越界(如纯壳/壳块选 S13/S23/E13/E23)→
        #       这是合法的"该实例无此分量", 返回全 NaN 结果(range=None)让前端置灰,
        #       不再 fall through 到其他 position, 否则会一路 None → 报 no result data。
        #   (b) 块存在但 L1 未填值(不变量场的 ELEMENT_NODAL 块只在 IP 出值)→ 返回
        #       None, 让调用方继续尝试 INTEGRATION_POINT。
        if component_idx is not None and comp_oob_all:
            return scalar_face, num_frames, None
        return None
    global_range = (float(valid_face.min()), float(valid_face.max()))
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


_SENSITIVITY_FRAME_ALIAS_RE = re.compile(r"^(?P<field>.+)__FRAME_(?P<frame>\d+)$")


def _resolve_sensitivity_frame_alias(field: str, frame_idx: int) -> Tuple[str, int]:
    """
    Resolve pseudo-fields emitted by the sensitivity picker back to the
    underlying field + concrete frame.
    """
    match = _SENSITIVITY_FRAME_ALIAS_RE.match(str(field or ""))
    if not match:
        return field, frame_idx
    return match.group("field"), int(match.group("frame"))


def _manifest_result_h5_path(workspace: str, step: str, field: str,
                              result_group: str = None) -> str:
    """
    Get result H5 path from manifest.db file_path column (authoritative),
    falling back to convention-based path building. 实现已下沉到
    ManifestRepo.result_h5_abspath，与 node_table/query/node_time_value 共用。
    """
    return ManifestRepo(workspace).result_h5_abspath(step, field, result_group)


def _should_force_flat_external_element_render(
    workspace: str,
    step: str,
    field: str,
    result_group: str = None,
) -> bool:
    """
    External element-only fields such as sparse sensitivity clouds should keep
    unassigned elements as no-data instead of being smoothed across shared
    surface vertices. Force flat rendering for those fields.
    """
    try:
        manifest = ManifestRepo(workspace)
        rf = manifest.get_result_file(step, field, result_group)
        if rf is None and result_group is not None:
            rf = manifest.get_result_file(step, field, None)
        if rf is None:
            return False

        source = str(rf["source"] or "").strip().lower()
        if source != "external":
            return False

        positions_raw = rf["positions"]
        try:
            positions = json.loads(positions_raw) if isinstance(positions_raw, str) else list(positions_raw or [])
        except Exception:
            positions = []
        positions = [str(item or "").strip().upper() for item in positions]
        return "ELEMENT_NODAL" in positions and "NODAL" not in positions
    except Exception:
        return False


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
    frame_data = _frame_slice(ds, frame_idx)   # [N, ncomp]
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

        frame_data = _frame_slice(ds, frame_idx)

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
    instance = canon_instance(instance)
    # Validate frame_idx sign upfront — NumPy/HDF5 silently accept negative indices
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )
    field, frame_idx = _resolve_sensitivity_frame_alias(field, frame_idx)

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
    effective_render_mode = render_mode
    if render_mode != "flat" and _should_force_flat_external_element_render(
        idx.workspace, step, field, result_group
    ):
        effective_render_mode = "flat"

    scalar_vertex = None
    num_frames = None
    legend_override = None   # NODAL smooth: node-level range computed pre-mask

    # set 过滤与着色之前的 (scalar_vertex, legend_override, num_frames) 只由
    # (结果文件版本, 几何, instance, 帧, 分量, 渲染模式) 决定 → 走统一缓存。
    h5_sig = _result_h5_sig(h5_path)
    bucket = cache_store.group_bucket(result_group)
    colors_key = (
        "frame_colors", odb_id, instance, h5_path, h5_sig,
        cache_store.render_sig(idx.workspace, instance), int(frame_idx),
        str(component), effective_render_mode,
    )
    cached = _frame_colors_cache_get(idx.workspace, bucket, colors_key) \
        if h5_sig is not None else None
    if cached is not None:
        scalar_vertex, legend_override, num_frames = cached
    else:
        scalar_vertex, legend_override, num_frames = _frame_colors_scalars(
            h5_path, idx, instance, frame_idx, component, effective_render_mode,
            src_node_rows, src_elem_row, src_etype)
        if scalar_vertex is not None and h5_sig is not None:
            scalar_vertex.setflags(write=False)
            _frame_colors_cache_put(idx.workspace, bucket, colors_key,
                                    (scalar_vertex, legend_override, num_frames))
    return _frame_colors_finish(idx, instance, field, frame_idx, set_name,
                                scalar_vertex, legend_override, num_frames)


def _frame_colors_cache_get(workspace: str, bucket: str, key: tuple) -> Optional[tuple]:
    hit = _vertex_cache_get(key)
    if hit is not None:
        return hit
    d = cache_store.arrays_get(workspace, bucket, key)
    if d is None or "scalar_vertex" not in d:
        return None
    lo = d["legend_override"]
    nf = int(d["num_frames"][()])
    value = (d["scalar_vertex"],
             (float(lo[0]), float(lo[1])) if lo.size == 2 else None,
             None if nf < 0 else nf)
    _vertex_cache_put(key, value)              # 磁盘命中 → 提升到内存层
    return value


def _frame_colors_cache_put(workspace: str, bucket: str, key: tuple, value: tuple) -> None:
    scalar_vertex, legend_override, num_frames = value
    _vertex_cache_put(key, value)
    cache_store.arrays_put(workspace, bucket, key, {
        "scalar_vertex": scalar_vertex,
        "legend_override": (np.asarray(legend_override, dtype=np.float64)
                            if legend_override is not None
                            else np.zeros(0, dtype=np.float64)),
        "num_frames": np.array(-1 if num_frames is None else int(num_frames),
                               dtype=np.int64),
    })


def _frame_colors_scalars(h5_path, idx, instance, frame_idx, component,
                          effective_render_mode, src_node_rows, src_elem_row, src_etype):
    """frame_colors 的重计算部分：读结果 H5 → soup 顶点标量 [Nt*3]（可缓存）。

    Returns (scalar_vertex or None, legend_override or None, num_frames)。
    """
    scalar_vertex = None
    num_frames = None
    legend_override = None   # NODAL smooth: node-level range computed pre-mask

    with h5py.File(h5_path, "r") as f:
        # ── Try NODAL ──────────────────────────────────────────────────────
        result = _scalar_from_nodal(f, instance, frame_idx, component)
        if result is not None:
            scalar_node, num_frames = result
            # 稀疏 NODAL 场（接触输出等）：data 行号 ≠ 几何节点行号，必须按
            # /NODAL/<inst>/labels 映射回几何节点行（缺数据的节点补 NaN），
            # 否则值会张冠李戴。稠密场 labels 与几何一致，恒等映射。
            scalar_node = _expand_sparse_nodal_to_geometry_rows(
                f=f,
                instance=instance,
                scalar_node=scalar_node,
                workspace=idx.workspace,
            )
            n_result_nodes = len(scalar_node)
            max_node_row = int(src_node_rows.max()) if src_node_rows.size else 0
            if max_node_row >= n_result_nodes:
                # Sparse NODAL field: dataset covers fewer nodes than the full geometry.
                # Extend with NaN so indexing always succeeds; NaN → 0 later via nan_to_num.
                extended = np.full(max_node_row + 1, np.nan, dtype=np.float32)
                extended[:n_result_nodes] = scalar_node
                scalar_node = extended

            if effective_render_mode == "flat" and src_elem_row is not None:
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
                # 图例范围在掩蔽前按节点真实值算（接触面边缘节点的值 CAE 也计入），
                # 掩蔽只影响上色不影响 legend。
                _finite_pre = scalar_vertex[np.isfinite(scalar_vertex)]
                if _finite_pre.size > 0:
                    legend_override = (float(_finite_pre.min()),
                                       float(_finite_pre.max()))
                scalar_vertex = _mask_partial_nan_triangles_soup(scalar_vertex)

        # ── Try ELEMENT_NODAL ──────────────────────────────────────────────
        if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
            result = _scalar_from_element_position(
                f, "ELEMENT_NODAL", instance, frame_idx, component,
                src_etype, src_elem_row,
            )
            if result is not None:
                scalar_face, num_frames = result
                scalar_vertex = np.repeat(scalar_face, 3)  # [Nt*3]

        # ── Try INTEGRATION_POINT, then WHOLE_ELEMENT (e.g. STATUS) ─────────
        for position in ("INTEGRATION_POINT", "WHOLE_ELEMENT"):
            if scalar_vertex is not None or src_etype is None or src_elem_row is None:
                break
            result = _scalar_from_element_position(
                f, position, instance, frame_idx, component,
                src_etype, src_elem_row,
            )
            if result is not None:
                scalar_face, num_frames = result
                scalar_vertex = np.repeat(scalar_face, 3)  # [Nt*3]

    return scalar_vertex, legend_override, num_frames


def _frame_colors_finish(idx, instance, field, frame_idx, set_name,
                         scalar_vertex, legend_override, num_frames):
    """frame_colors 的按请求部分：帧校验 → set 过滤 → 图例范围 → jet 着色。"""
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

    if legend_override is not None and set_name is None:
        # NODAL smooth：用掩蔽前的节点级范围（含接触面边缘节点），与 CAE 一致
        val_min, val_max = legend_override
    else:
        finite = scalar_vertex[np.isfinite(scalar_vertex)]
        if finite.size > 0:
            val_min = float(finite.min())
            val_max = float(finite.max())
        else:
            val_min = 0.0
            val_max = 0.0
    legend_range = np.array([val_min, val_max], dtype=np.float32)

    span = val_max - val_min
    if np.isfinite(scalar_vertex).all():
        if span < 1e-12:
            normalized = np.zeros_like(scalar_vertex)
        else:
            normalized = (scalar_vertex - val_min) / span
        color_per_vertex = apply_jet(normalized)   # [Nt_subset*3, 4] uint8
    else:
        color_per_vertex = apply_jet_with_neutral(
            scalar_vertex.astype(np.float32),
            val_min,
            val_max,
        )

    return color_per_vertex, legend_range


# ─── compute_scalar_range: range-only helper (no vertex scatter) ─────────────

def compute_scalar_range(
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
) -> Optional[Tuple[float, float]]:
    """
    Return (val_min, val_max) for the given instance/field/frame using the same
    range logic as frame_scalars(), but without building u_per_vertex.

    set_name: when given, the range is computed over only the elements in that
    set (the "min=blue / max=red over the selected set" case). We delegate to
    frame_scalars() with no override so it returns the set-subset range via its
    legend_range, keeping the set-filter semantics identical to actual coloring.

    Returns None if no result data is found for this instance.
    Raises NotFoundError / NotReadyError / ValidationError on hard failures.
    """
    instance = canon_instance(instance)
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )

    # Set filter: reuse frame_scalars' set→subset→range path so the range matches
    # exactly what coloring will use. Subsets are small, so the extra scatter is cheap.
    #
    # First probe whether this instance actually contains the set. frame_scalars
    # silently falls back to the full instance when a set is absent, which would
    # pollute the union range — so for the range endpoint we instead skip such an
    # instance (return None), mirroring "no data for this field" handling.
    if set_name is not None:
        idx = registry.get(odb_id)
        if idx is None:
            raise NotFoundError(f"ODB '{odb_id}' not found")
        if not idx.is_render_ready:
            raise NotReadyError(f"ODB '{odb_id}' is not render-ready yet")
        _manifest = ManifestRepo(idx.workspace)
        _rows = _manifest.get_user_set_render_rows(set_name, instance)
        if _rows is None:
            _labels = _manifest.get_element_set_labels(set_name, instance)
            if _labels is None or len(_labels) == 0:
                return None
            from .user_field_service import get_face_mask_for_elem_labels
            _mask = get_face_mask_for_elem_labels(idx, instance, set(_labels.tolist()))
            if not bool(_mask.any()):
                return None
        elif len(_rows) == 0:
            return None
        _, legend_range, _ = frame_scalars(
            registry=registry,
            odb_id=odb_id,
            instance=instance,
            step=step,
            field=field,
            frame_idx=frame_idx,
            component_idx=component_idx,
            render_mode=render_mode,
            result_group=result_group,
            set_name=set_name,
            feature_angle=feature_angle,
            average_threshold=average_threshold,
            use_geometry_split=use_geometry_split,
        )
        return (float(legend_range[0]), float(legend_range[1]))

    field, frame_idx = _resolve_sensitivity_frame_alias(field, frame_idx)

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
        parent_field, mag_idx = _resolve_magnitude_field(
            idx.workspace, step, field, result_group)
        if parent_field is not None:
            field = parent_field
            component_idx = mag_idx
            h5_path = _manifest_result_h5_path(idx.workspace, step, field, result_group)
        if not os.path.exists(h5_path):
            raise NotFoundError(
                f"Result file not found for step='{step}' field='{field}'",
                {"step": step, "field": field},
            )
    effective_render_mode = render_mode
    if render_mode != "flat" and _should_force_flat_external_element_render(
        idx.workspace, step, field, result_group
    ):
        effective_render_mode = "flat"

    _manifest = ManifestRepo(idx.workspace)
    geom_h5_path = _manifest.get_geom_path(instance)
    averaging = idx.averaging_data.get(instance, {})
    all_element_domain = averaging.get("all_element_domain")
    advanced_version = int(getattr(idx, "_advanced_status_mtime_ns", 0))

    # 无 set 的范围只由结果文件版本 + (instance, 帧, 分量, 阈值) 决定，整体缓存；
    # 前端逐帧/切分量反复询问范围时不再重读 H5。
    h5_sig = _result_h5_sig(h5_path)
    range_key = (
        "scalar_range", odb_id, instance, h5_path, h5_sig, int(frame_idx),
        _component_cache_token(h5_path, h5_sig, component_idx),
        float(average_threshold), advanced_version,
    )
    if h5_sig is not None:
        hit = _range_cache_get(range_key)
        if hit is not _CACHE_MISS:
            return hit
        hit = _range_disk_get(idx.workspace, cache_store.group_bucket(result_group),
                              range_key)
        if hit is not _CACHE_MISS:
            _range_cache_put(range_key, hit)        # 磁盘命中 → 提升到内存层
            return hit

    result_range = _scalar_range_from_h5(
        h5_path=h5_path,
        instance=instance,
        frame_idx=frame_idx,
        component_idx=component_idx,
        src_etype=src_etype,
        src_elem_row=src_elem_row,
        geom_h5_path=geom_h5_path,
        all_element_domain=all_element_domain,
        domain_version=advanced_version,
        average_threshold=average_threshold,
    )
    if h5_sig is not None:
        _range_cache_put(range_key, result_range)
        _range_disk_put(idx.workspace, cache_store.group_bucket(result_group),
                        range_key, result_range)
    return result_range


def _scalar_range_from_h5(
    *,
    h5_path: str,
    instance: str,
    frame_idx: int,
    component_idx: Optional[int],
    src_etype: Optional[np.ndarray],
    src_elem_row: Optional[np.ndarray],
    geom_h5_path: Optional[str],
    all_element_domain: Optional[dict],
    domain_version: int,
    average_threshold: float,
) -> Optional[Tuple[float, float]]:
    """compute_scalar_range 无 set 路径的计算主体（原 with 块原样搬出以便缓存）。"""
    with h5py.File(h5_path, "r") as f:

        # ── NODAL ────────────────────────────────────────────────────────────
        result = _scalar_nodal_by_idx(f, instance, frame_idx, component_idx)
        if result is not None:
            scalar_node, _ = result
            finite = scalar_node[np.isfinite(scalar_node)]
            if finite.size > 0:
                return (float(finite.min()), float(finite.max()))
            return None

        # ── ELEMENT_NODAL ────────────────────────────────────────────────────
        if src_etype is not None and src_elem_row is not None:
            found_en = False
            result = _scalar_elem_pos_by_idx(
                f, "ELEMENT_NODAL", instance, frame_idx, component_idx,
                src_etype, src_elem_row,
            )
            if result is not None:
                found_en = True
                _, _, surface_range = result
                # Prefer full-model range (mirrors frame_scalars ELEMENT_NODAL override)
                if geom_h5_path is not None:
                    all_range = _compute_en_global_range(
                        f, geom_h5_path, instance, frame_idx, component_idx,
                        average_threshold, all_element_domain, domain_version,
                    )
                    if all_range is not None:
                        return all_range
                if surface_range is not None:
                    return surface_range

            # ── INTEGRATION_POINT, then WHOLE_ELEMENT (flat fallback) ────────
            if not found_en:
                for position in ("INTEGRATION_POINT", "WHOLE_ELEMENT"):
                    result = _scalar_elem_pos_by_idx(
                        f, position, instance, frame_idx, component_idx,
                        src_etype, src_elem_row,
                    )
                    if result is not None:
                        _, _, rng = result
                        return rng

    return None


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
    set_mode: str = "clip",
    feature_angle: Optional[float] = 20.0,
    average_threshold: float = 0.75,
    use_geometry_split: bool = True,
    override_min: Optional[float] = None,
    override_max: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, str]:
    """
    Returns (u_per_vertex [Nv] float32, legend_range [2] float32, result_position str).

    u_per_vertex: normalized scalar t ∈ [0, 1] for each render vertex.
    legend_range: [val_min, val_max] in original field units.
    result_position: 'NODAL' | 'ELEMENT_NODAL' | 'ELEMENT_NODAL_FLAT' |
                     'INTEGRATION_POINT_FLAT' | 'WHOLE_ELEMENT_FLAT'

    component_idx=None → magnitude (L2 norm).
    component_idx=0,1,2,... → direct index into the result component axis.

    set_name: optional user/element set to restrict to. set_mode controls how:
      'clip' = drop non-set vertices (pair with geometry subset, mode A);
      'mask' = keep all vertices, set non-set ones to NaN so the full model stays
               visible and only the set region is colored (mode B). Either way the
               normalization range is computed over the set only.

    override_min/override_max: when both are provided, skip per-instance range
    computation and use these values directly (global normalization mode).

    Position fallback: NODAL → ELEMENT_NODAL (averaged) → INTEGRATION_POINT (flat)
    → WHOLE_ELEMENT (flat, e.g. STATUS).
    feature_angle: degrees for shell/membrane geometric splitting; None = section-only.
    use_geometry_split: False = ignore feature_angle, use section-only domains.
    average_threshold: 75% threshold for conditional node averaging.
    """
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )
    field, frame_idx = _resolve_sensitivity_frame_alias(field, frame_idx)

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

    effective_render_mode = render_mode
    if render_mode != "flat" and _should_force_flat_external_element_render(
        idx.workspace, step, field, result_group
    ):
        effective_render_mode = "flat"

    _manifest = ManifestRepo(idx.workspace)
    geom_h5_path = _manifest.get_geom_path(instance)
    averaging = idx.averaging_data.get(instance, {})
    all_element_domain = averaging.get("all_element_domain")
    advanced_version = int(getattr(idx, "_advanced_status_mtime_ns", 0))

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

    # 归一化前的 scalar_vertex 只由 (结果文件版本, instance, 帧, 分量, 渲染参数)
    # 决定，与 set 过滤 / override 无关 → 在这里缓存，命中时跳过整个 H5 读取与
    # 条件平均；set 过滤和归一化仍在缓存之后按本次请求参数执行。
    h5_sig = _result_h5_sig(h5_path)
    fa_cache_key = None if feature_angle is None else round(float(feature_angle), 4)
    vertex_key = (
        "frame_scalars", odb_id, instance, h5_path, h5_sig,
        cache_store.render_sig(idx.workspace, instance), int(frame_idx),
        _component_cache_token(h5_path, h5_sig, component_idx),
        effective_render_mode, fa_cache_key, float(average_threshold),
        bool(use_geometry_split), advanced_version,
    )
    cached = _vertex_cache_get(vertex_key) if h5_sig is not None else None
    cache_status = "memory_hit" if cached is not None else "miss"
    if cached is None and h5_sig is not None:
        cached = _vertex_disk_get(idx.workspace, cache_store.group_bucket(result_group),
                                  vertex_key)
        if cached is not None:
            cache_status = "disk_hit"
            _vertex_cache_put(vertex_key, cached)   # 磁盘命中 → 提升到内存层
    logger.info(
        "[frame-scalars cache] status=%s odb_id=%r result_group=%r "
        "instance=%r step=%r field=%r frame=%d component_idx=%s mode=%s "
        "feature_angle=%s average_threshold=%s use_geometry_split=%s",
        cache_status, odb_id, result_group, instance, step, field, frame_idx,
        "mag" if component_idx is None else component_idx, effective_render_mode,
        feature_angle, average_threshold, use_geometry_split,
    )
    if cached is not None:
        scalar_vertex, global_range, result_position, num_frames = cached
    else:
        with h5py.File(h5_path, "r") as f:

            # ── NODAL ────────────────────────────────────────────────────────────
            result = _scalar_nodal_by_idx(f, instance, frame_idx, component_idx)
            if result is not None:
                scalar_node, num_frames = result
                scalar_node = _expand_sparse_nodal_to_geometry_rows(
                    f=f,
                    instance=instance,
                    scalar_node=scalar_node,
                    workspace=idx.workspace,
                )
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

                if effective_render_mode == "flat" and src_elem_row is not None:
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
                    # soup smooth：稀疏场对"部分角点无数据"的三角形整体置灰，
                    # 避免接触面颜色沿共享节点溢出到侧面（global_range 已在上面
                    # 按节点级算好，掩蔽不影响图例）。
                    scalar_vertex = _mask_partial_nan_triangles_soup(
                        scalar_node[src_node_rows.ravel()])         # [Nt*3] soup smooth

            # ── ELEMENT_NODAL (per-local-node with domain averaging) ─────────
            if scalar_vertex is None and src_etype is not None and src_elem_row is not None:
                local_node_idx = idx.source_local_node_idx.get(instance)
                render_idx     = idx.render_indices.get(instance)
                avd            = idx.averaging_data.get(instance)

                if (effective_render_mode != "flat"
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
                    average_threshold, all_element_domain, advanced_version,
                )
                if all_range is not None:
                    global_range = all_range

            # ── INTEGRATION_POINT, then WHOLE_ELEMENT (flat fallback) ────────
            for position in ("INTEGRATION_POINT", "WHOLE_ELEMENT"):
                if scalar_vertex is not None or src_etype is None or src_elem_row is None:
                    break
                result = _scalar_elem_pos_by_idx(
                    f, position, instance, frame_idx, component_idx,
                    src_etype, src_elem_row,
                )
                if result is not None:
                    scalar_face, num_frames, global_range = result
                    result_position = f"{position}_FLAT"
                    if vtx_ti is not None:
                        scalar_vertex = scalar_face[vtx_ti]         # [Nv] indexed
                    else:
                        scalar_vertex = np.repeat(scalar_face, 3)  # [Nt*3] soup

        if scalar_vertex is not None and h5_sig is not None:
            # 缓存的数组会被多个请求共享：置为只读，下游只允许整体重新赋值
            # （set 过滤 / 归一化都是产生新数组，不做原地修改）。
            scalar_vertex.setflags(write=False)
            value = (scalar_vertex, global_range, result_position, num_frames)
            _vertex_cache_put(vertex_key, value)
            _vertex_disk_put(idx.workspace, cache_store.group_bucket(result_group),
                             vertex_key, value)

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

    # Apply set filter. Two modes:
    #   clip (mode A): drop non-set vertices entirely — caller pairs this with
    #                  geometry subset (render-buffers?set=) so only the set shows.
    #   mask (mode B): keep ALL vertices but blank out non-set ones to NaN, so the
    #                  full model stays visible and only the set region gets the
    #                  colormap (the frontend already renders NaN vertices grey).
    # In both modes the normalization range below ends up over the set only (NaN
    # is excluded from finite), giving "min=blue / max=red over the selected set".
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
                # indexed geometry: unique vertices of the selected triangles
                set_vtx = np.unique(render_idx[render_rows].ravel())
            else:
                # soup geometry: each triangle occupies 3 contiguous vertices
                set_vtx = (render_rows[:, None] * 3 + np.arange(3)).ravel()
            if set_mode == "mask":
                keep = np.zeros(scalar_vertex.shape[0], dtype=bool)
                keep[set_vtx] = True
                scalar_vertex = np.where(
                    keep, scalar_vertex, np.nan
                ).astype(np.float32)
            else:  # clip
                scalar_vertex = scalar_vertex[set_vtx]

    # NaN = element type has no data for this component (e.g. shell missing S33).
    # Preserve NaN through normalization so the frontend can render those faces grey.
    # Priority: caller-supplied override (global mode) > set-subset range (when a set
    # filter is active) > full-model range > surface fallback.
    #
    # When set_name is given, scalar_vertex was already compacted to the set's faces
    # above, so its own min/max IS the set-subset range — exactly what "min=blue /
    # max=red over the selected set" needs. We put it ahead of global_range so a set
    # filter shrinks the color scale to the set. override still wins (the frontend's
    # two-step flow passes the set-subset range it just fetched as override).
    if override_min is not None and override_max is not None:
        val_min, val_max = float(override_min), float(override_max)
    elif set_name is not None:
        finite = scalar_vertex[np.isfinite(scalar_vertex)]
        if finite.size > 0:
            val_min, val_max = float(finite.min()), float(finite.max())
        else:
            val_min, val_max = 0.0, 0.0
    elif global_range is not None and np.isfinite(global_range[0]):
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
    if span < 1e-30:
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
    """SciPy sparse-graph domain partition (mirrors L2 build_domain_ids)."""
    E = len(section_id)
    if E == 0:
        return np.zeros(0, dtype=np.int32)

    src = np.asarray(adj_src, dtype=np.int64)
    dst = np.asarray(adj_dst, dtype=np.int64)
    angle = np.asarray(adj_angle_deg)
    valid_rows = np.flatnonzero(
        (src >= 0) & (src < E) & (dst >= 0) & (dst < E))
    if len(valid_rows):
        va = src[valid_rows]
        vb = dst[valid_rows]
        ka = elem_kind[va]
        kb = elem_kind[vb]
        solid_pair = ((ka == _ELEM_KIND_SOLID) &
                      (kb == _ELEM_KIND_SOLID))
        shell_pair = (
            ((ka == _ELEM_KIND_SHELL) | (ka == _ELEM_KIND_MEMBRANE)) &
            ((kb == _ELEM_KIND_SHELL) | (kb == _ELEM_KIND_MEMBRANE)) &
            (angle[valid_rows] <= feature_angle_deg)
        )
        keep = (section_id[va] == section_id[vb]) & (solid_pair | shell_pair)
        src_keep = va[keep]
        dst_keep = vb[keep]
    else:
        src_keep = np.zeros(0, dtype=np.int64)
        dst_keep = np.zeros(0, dtype=np.int64)

    rows = np.concatenate((src_keep, dst_keep))
    cols = np.concatenate((dst_keep, src_keep))
    graph = coo_matrix(
        (np.ones(len(rows), dtype=np.uint8), (rows, cols)),
        shape=(E, E),
    ).tocsr()
    _, labels = connected_components(
        graph, directed=False, return_labels=True)
    _, first_indices = np.unique(labels, return_index=True)
    first_indices.sort()
    label_to_domain = np.empty(E, dtype=np.int32)
    label_to_domain[labels[first_indices]] = np.arange(
        len(first_indices), dtype=np.int32)
    return label_to_domain[labels]


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

        fd = _frame_slice(ds, frame_idx)              # [N_elem, n_local, ncomp?]
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

    # Step 2 (per etype): map (etype, elem_row) → unique-element idx (gidx) → domain,
    # and gather each corner's raw value. Dense row→gidx lookup tables replace the
    # old per-triangle Python dict lookups; bounds rules match the old loop
    # (missing etype block or out-of-range er/li → no data for that corner).
    tri_did    = np.full(Nt, -1, dtype=np.int64)   # domain id per triangle, -1 = none
    corner_val = np.full((Nt, 3), np.nan, dtype=np.float32)
    corner_ok  = np.zeros((Nt, 3), dtype=bool)
    dom_len    = len(domain_id)

    for etype_bytes in np.unique(src_etype):
        tmask = src_etype == etype_bytes
        er = src_elem_row[tmask].astype(np.int64)          # [k]

        # row → gidx table for this etype (last entry wins, like the old dict);
        # rows beyond the table or gidx ≥ len(domain_id) stay at did = -1.
        gpos = np.where(avg_elem_etype == etype_bytes)[0]
        if gpos.size and er.size and dom_len:
            arow = avg_elem_row[gpos].astype(np.int64)
            lut = np.full(int(arow.max()) + 2, -1, dtype=np.int64)
            lut[arow] = gpos
            gid = lut[np.minimum(er, lut.size - 1)]
            safe = (gid >= 0) & (gid < dom_len)
            did_e = np.full(er.shape, -1, dtype=np.int64)
            did_e[safe] = domain_id[gid[safe]]
            tri_did[tmask] = did_e

        sc = scalar_en_by_etype.get(etype_bytes)
        if sc is None or er.size == 0:
            continue
        li = src_local_node_idx[tmask].astype(np.int64)    # [k, 3]
        ok = (er[:, None] < sc.shape[0]) & (li < sc.shape[1])
        vals = sc[np.minimum(er, sc.shape[0] - 1)[:, None],
                  np.minimum(li, sc.shape[1] - 1)]
        vals[~ok] = np.nan
        corner_val[tmask] = vals
        corner_ok[tmask]  = ok

    flat_vtx = render_indices.ravel()                        # [Nt*3]
    flat_val = corner_val.reshape(-1)
    flat_ok  = corner_ok.reshape(-1)
    flat_did = np.repeat(tri_did, 3)

    # vtx_corner_val[vtx] = raw scalar for the specific (er, li) of that vertex
    # (same val for same vertex regardless of tri, so duplicate writes are benign)
    vtx_corner_val = np.full(Nv, np.nan, dtype=np.float32)
    vtx_corner_val[flat_vtx[flat_ok]] = flat_val[flat_ok]

    # Step 3: per-(domain, node_row) stats via sort + reduceat, then the 75%
    # conditional-averaging rule (spread ≤ threshold × domain range → use mean).
    scalar_vertex = vtx_corner_val.copy()   # default: original per-elem values
    nr_stride = int(vtx_node_row.max()) + 1 if Nv else 1

    # Non-finite values (e.g. component out of range for one etype in a mixed
    # solid/shell instance) render grey but must not poison domain statistics.
    grp = flat_ok & (flat_did >= 0) & np.isfinite(flat_val)
    if grp.any():
        grp_idx = np.where(grp)[0]          # flat (tri*3+corner) position per sample
        g_did = flat_did[grp_idx]
        g_nr  = vtx_node_row[flat_vtx[grp_idx]].astype(np.int64)
        g_val = flat_val[grp_idx].astype(np.float64)
        g_key = g_did * nr_stride + g_nr    # sorts by (did, node_row)

        order = np.argsort(g_key)
        key_s = g_key[order]
        did_s = g_did[order]
        val_s = g_val[order]

        new_node = np.concatenate([[True], key_s[1:] != key_s[:-1]])
        starts   = np.where(new_node)[0]
        node_did = did_s[starts]
        node_min = np.minimum.reduceat(val_s, starts)
        node_max = np.maximum.reduceat(val_s, starts)
        node_cnt = np.diff(np.concatenate([starts, [len(key_s)]]))
        node_mean   = np.add.reduceat(val_s, starts) / node_cnt
        node_spread = node_max - node_min

        # Domain raw range per node group. did_s is sorted (did-major key), so
        # domains are contiguous and align with the node groups' did order.
        dom_starts = np.where(np.concatenate([[True], did_s[1:] != did_s[:-1]]))[0]
        dom_range  = (np.maximum.reduceat(val_s, dom_starts)
                      - np.minimum.reduceat(val_s, dom_starts))
        node_dom = np.cumsum(np.concatenate([[0], node_did[1:] != node_did[:-1]]))
        node_dom_range = dom_range[node_dom]

        do_avg = ((node_dom_range < 1e-12)
                  | (node_spread <= average_threshold * node_dom_range))
        node_mean32 = node_mean.astype(np.float32)

        # Step 4: scatter averaged values back onto vertices in flat (tri, corner)
        # order, like the old loop. Samples get their own group's value directly;
        # the rare corners with a valid domain but no own data (missing etype block
        # or out-of-range er/li) may still pick up a neighbour's averaged value —
        # those few go through a binary search over the averaged group keys.
        ent_grp = np.cumsum(new_node) - 1               # group id per sorted sample
        ent_hit = np.zeros(flat_val.shape[0], dtype=bool)
        ent_val = np.full(flat_val.shape[0], np.nan, dtype=np.float32)
        orig_pos = grp_idx[order]
        ent_hit[orig_pos] = do_avg[ent_grp]
        ent_val[orig_pos] = np.where(do_avg[ent_grp], node_mean32[ent_grp], np.nan)

        rest = (flat_did >= 0) & ~grp
        if rest.any():
            avg_keys = key_s[starts][do_avg]            # ascending
            avg_vals = node_mean32[do_avg]
            if avg_keys.size:
                rest_idx = np.where(rest)[0]
                r_key = (flat_did[rest_idx] * nr_stride
                         + vtx_node_row[flat_vtx[rest_idx]].astype(np.int64))
                p = np.minimum(np.searchsorted(avg_keys, r_key), avg_keys.size - 1)
                hit = avg_keys[p] == r_key
                ent_hit[rest_idx] = hit
                ent_val[rest_idx[hit]] = avg_vals[p[hit]]

        scalar_vertex[flat_vtx[ent_hit]] = ent_val[ent_hit]

    # Step 5: global range from surface post-averaged values only.
    # Abaqus legend = min/max of averaged nodal values on the visible surface.
    # np.isfinite excludes vertices with no matching element data (NaN) so they
    # don't corrupt the range via nan_to_num(nan=0.0) later.
    surf_valid = scalar_vertex[np.isfinite(scalar_vertex)]
    # All-NaN ELEMENT_NODAL (e.g. invariant fields whose EN block L1 left empty) →
    # treat as "no EN data" so frame_scalars falls through to INTEGRATION_POINT
    # instead of locking onto an all-grey EN result.
    if surf_valid.size == 0:
        return None
    global_range = (float(surf_valid.min()), float(surf_valid.max()))
    return scalar_vertex, num_frames, global_range


def _load_full_conn_rows(geom_f, geom_h5_path: str, etype_key: str):
    """Full (mid-node) connectivity rows for one etype, or None.

    conn_full in <inst>_highorder.h5 stores node LABELS; map to rows via the
    geometry file's sorted node label array.  Used so high-order mid-node
    values reach the legend range (see HighOrder-Midside-Subdivision-Design).
    """
    if not geom_h5_path.endswith(".h5"):
        return None
    ho_path = geom_h5_path[:-3] + "_highorder.h5"
    if not os.path.exists(ho_path) or "nodes/labels" not in geom_f:
        return None
    node_labels = geom_f["nodes/labels"][:]
    try:
        with h5py.File(ho_path, "r") as f_ho:
            grp_path = f"elements/{etype_key}"
            if grp_path not in f_ho or "conn_full" not in f_ho[grp_path]:
                return None
            cf = f_ho[grp_path]["conn_full"][:]
    except Exception:
        return None
    return np.searchsorted(node_labels, cf).astype(np.int32)


def _compute_en_global_range(
    result_h5,
    geom_h5_path: str,
    instance: str,
    frame_idx: int,
    component_idx: Optional[int],
    average_threshold: float = 0.75,
    all_element_domain: Optional[dict] = None,
    domain_version: int = 0,
) -> Optional[Tuple[float, float]]:
    """
    Cached front for _compute_en_global_range_uncached: the full-model averaged
    range re-reads the whole geometry + ELEMENT_NODAL datasets, yet only depends
    on the result file version and a handful of parameters — ideal cache food.
    """
    h5_path = result_h5.filename
    sig = _result_h5_sig(h5_path)
    key = (
        "en_range", h5_path, sig, geom_h5_path, instance, int(frame_idx),
        None if component_idx is None else int(component_idx),
        float(average_threshold), int(domain_version),
    )
    if sig is not None:
        # 中间结果：全量准备时同一帧同一分量的 smooth / flat / range 共用一次计算。
        hit = cache_store.small_get(key, intermediate=True)
        if hit is not _CACHE_MISS:
            return hit
    result = _compute_en_global_range_uncached(
        result_h5, geom_h5_path, instance, frame_idx, component_idx,
        average_threshold, all_element_domain, domain_version,
    )
    if sig is not None:
        _range_cache_put(key, result)
    return result


# 全模型平均范围的“只依赖网格”部分：每种结果布局（参与的单元类型及其 N/n_shared）
# 只建一次。见 _en_range_index。
_EN_GEOM_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_EN_RANGE_INDEX: "OrderedDict[tuple, dict]" = OrderedDict()
_EN_INDEX_MAX = 2
_EN_INDEX_LOCK = threading.Lock()


def _lru_get(cache, key):
    with _EN_INDEX_LOCK:
        hit = cache.get(key)
        if hit is not None:
            cache.move_to_end(key)
        return hit


def _lru_put(cache, key, value):
    with _EN_INDEX_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > _EN_INDEX_MAX:
            cache.popitem(last=False)


def _en_range_geometry(geom_f, geom_h5_path: str, all_element_domain, domain_version):
    """各单元类型的 (区域编号 sec_id, 连接 conn)，conn 优先含中节点的完整连接。

    domain_version 为 None 时不缓存（每次照原样读取）。
    """
    key = None
    if domain_version is not None:
        ho_path = geom_h5_path[:-3] + "_highorder.h5" if geom_h5_path.endswith(".h5") else ""
        key = (geom_h5_path, cache_store.file_sig(geom_h5_path),
               cache_store.file_sig(ho_path) if ho_path else None, int(domain_version))
        hit = _lru_get(_EN_GEOM_CACHE, key)
        if hit is not None:
            return hit
    parts = {}
    for etype_key in geom_f['elements']:
        geom_grp = geom_f['elements'][etype_key]
        if 'conn' not in geom_grp or 'section_id' not in geom_grp:
            continue
        sec_id = None
        if all_element_domain is not None:
            domain_values = all_element_domain.get(etype_key)
            if domain_values is not None and len(domain_values) == len(geom_grp['section_id']):
                sec_id = np.asarray(domain_values, dtype=np.int32)
        if sec_id is None:
            sec_id = geom_grp['section_id'][:]   # [N_geom] int32
        conn = geom_grp['conn'][:]               # [N_geom, n_corner] int32
        # Prefer full connectivity (corner + mid-nodes) so mid-node
        # extrema — which often hold the field min/max on high-order
        # elements — are included in the legend range, matching Abaqus.
        conn_full = _load_full_conn_rows(geom_f, geom_h5_path, etype_key)
        if (conn_full is not None
                and conn_full.shape[0] == conn.shape[0]
                and conn_full.shape[1] > conn.shape[1]):
            conn = conn_full
        parts[etype_key] = (sec_id, conn)
    if key is not None:
        _lru_put(_EN_GEOM_CACHE, key, parts)
    return parts


def _en_range_index(geom_key, layout, geom_parts):
    """
    把各单元类型的 (区域, 节点) 采样拼起来并做两次稳定排序，按布局缓存。

    稳定排序的性质：先去掉 NaN 再排序，与先排序再按原顺序去掉 NaN，得到的序列
    完全相同。所以每次只需按本帧的 NaN 掩码过滤预排好的下标，结果与逐次排序逐位一致。
    """
    key = None if geom_key is None else (geom_key, layout)
    if key is not None:
        hit = _lru_get(_EN_RANGE_INDEX, key)
        if hit is not None:
            return hit
    secs, nodes = [], []
    for etype_key, n, n_shared in layout:
        sec_id, conn = geom_parts[etype_key]
        secs.append(np.repeat(sec_id[:n], n_shared).astype(np.int32))
        nodes.append(conn[:n, :n_shared].ravel().astype(np.int32))
    sec_full = np.concatenate(secs)
    node_full = np.concatenate(nodes)
    max_nr = int(node_full.max()) + 1
    compound = sec_full.astype(np.int64) * max_nr + node_full.astype(np.int64)
    index = {
        "sec": sec_full,
        "node": node_full,
        "dom_perm": np.argsort(sec_full, kind='stable'),
        "node_perm": np.argsort(compound, kind='stable'),
    }
    if key is not None:
        _lru_put(_EN_RANGE_INDEX, key, index)
    return index


def _compute_en_global_range_uncached(
    result_h5,
    geom_h5_path: str,
    instance: str,
    frame_idx: int,
    component_idx: Optional[int],
    average_threshold: float = 0.75,
    all_element_domain: Optional[dict] = None,
    domain_version: Optional[int] = None,
) -> Optional[Tuple[float, float]]:
    """
    Compute global legend range from ALL elements (including interior) using
    L2 geometric domains when available, otherwise L1 section domains.

    domain_version 不为 None 时复用按网格预建的排序下标（_en_range_index），
    结果与逐次排序逐位一致；为 None 时每次重建。

    Returns (global_min, global_max), or None if section_id data is unavailable.
    """
    if not os.path.exists(geom_h5_path):
        return None

    layout = []
    all_vals = []

    try:
        with h5py.File(geom_h5_path, 'r') as geom_f:
            if 'elements' not in geom_f:
                return None
            geom_parts = _en_range_geometry(geom_f, geom_h5_path, all_element_domain,
                                            domain_version)

            for etype_key, (sec_id, conn) in geom_parts.items():
                grp_path = f'/ELEMENT_NODAL/{instance}/{etype_key}'
                if grp_path not in result_h5:
                    continue
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

                fd = _frame_slice(ds, frame_idx)
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

                layout.append((etype_key, N, n_shared))
                all_vals.append(sc[:N, :n_shared].astype(np.float64).ravel())
    except Exception:
        logger.exception("_compute_en_global_range: failed reading %s", geom_h5_path)
        return None

    if not all_vals:
        return None
    values_full = np.concatenate(all_vals)
    valid = np.isfinite(values_full)
    if not valid.any():
        return None

    geom_key = None
    if domain_version is not None:
        ho_path = geom_h5_path[:-3] + "_highorder.h5" if geom_h5_path.endswith(".h5") else ""
        geom_key = (geom_h5_path, cache_store.file_sig(geom_h5_path),
                    cache_store.file_sig(ho_path) if ho_path else None, int(domain_version))
    index = _en_range_index(geom_key, tuple(layout), geom_parts)
    sec_full, node_full = index["sec"], index["node"]

    # Per-domain raw range (denominator for the 75% threshold)
    dom_sort      = index["dom_perm"][valid[index["dom_perm"]]]
    sec_dom       = sec_full[dom_sort]
    val_dom       = values_full[dom_sort]
    dom_bounds    = np.concatenate([[0],
                                     np.where(sec_dom[1:] != sec_dom[:-1])[0] + 1,
                                     [len(sec_dom)]])
    dom_min_arr   = np.minimum.reduceat(val_dom, dom_bounds[:-1])
    dom_max_arr   = np.maximum.reduceat(val_dom, dom_bounds[:-1])
    dom_range_arr = dom_max_arr - dom_min_arr
    dom_sec_arr   = sec_dom[dom_bounds[:-1]]

    # Per-(section, node) stats; the stable (section, node) order is precomputed
    node_sort   = index["node_perm"][valid[index["node_perm"]]]
    sec_s       = sec_full[node_sort]
    node_s      = node_full[node_sort]
    val_s       = values_full[node_sort]

    new_group     = (sec_s[1:] != sec_s[:-1]) | (node_s[1:] != node_s[:-1])
    node_bounds   = np.concatenate([[0], np.where(new_group)[0] + 1, [len(sec_s)]])
    node_min_arr  = np.minimum.reduceat(val_s, node_bounds[:-1])
    node_max_arr  = np.maximum.reduceat(val_s, node_bounds[:-1])
    node_sum_arr  = np.add.reduceat(val_s, node_bounds[:-1])
    node_cnt_arr  = np.diff(node_bounds).astype(np.float64)
    node_mean_arr = node_sum_arr / node_cnt_arr
    node_spr_arr  = node_max_arr - node_min_arr
    node_sec_arr  = sec_s[node_bounds[:-1]]

    # dom_sec_arr 升序且包含 node_sec_arr 的全部取值（来自同一组 sec_ids）
    dom_range_per_node = dom_range_arr[np.searchsorted(dom_sec_arr, node_sec_arr)]

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

def _read_raw_node_displacements(
    idx,
    instance: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> np.ndarray:
    """
    Read U NODAL displacement for frame_idx as [n_result_nodes, 3] float32,
    indexed by geometry node row (same convention as vtx_node_row and the
    geometry element `conn` arrays).  No scatter to vertices, no padding —
    callers pad/index as needed.
    """
    if frame_idx < 0:
        raise ValidationError(
            f"frame_idx must be >= 0, got {frame_idx}",
            {"frame_idx": frame_idx},
        )

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
        disp_node = ds[frame_idx, :, :3].astype(np.float32)

    if disp_node.ndim == 1:
        raise ValidationError(
            "U field is scalar; expected 3-component vector",
            {"instance": instance},
        )

    return disp_node


def _load_raw_node_displacements(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> Tuple[np.ndarray, object]:
    """Registry-level wrapper of _read_raw_node_displacements → (disp_node, idx)."""
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' render data not loaded")
    disp_node = _read_raw_node_displacements(idx, instance, step, frame_idx, result_group)
    return disp_node, idx


def _disp_at_rows(disp_node: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """
    Index disp_node [n_nodes, 3] by node rows [K], padding with zeros for any
    row beyond the result array (node has no U output → stays put).
    Returns [K, 3] float32.
    """
    n_nodes = disp_node.shape[0]
    if rows.size == 0:
        return np.zeros((0, 3), dtype=np.float32)
    max_nr = int(rows.max())
    if max_nr >= n_nodes:
        padded = np.zeros((max_nr + 1, disp_node.shape[1]), dtype=np.float32)
        padded[:n_nodes] = disp_node
        disp_node = padded
    return disp_node[rows]


def _get_render_positions(idx, instance: str) -> Optional[np.ndarray]:
    """
    render/positions [Nv, 3] float32（只读）。优先取 ModelIndex 常驻副本
    （load_l2_render_data 已加载），缺失时回退读 render.h5 并回填常驻 dict
    （老索引/测试注入的 idx 也就此受益）。找不到返回 None。
    """
    pos = idx.render_positions.get(instance)
    if pos is not None:
        return pos
    render_h5 = os.path.join(idx.workspace, "l2", "render", f"{instance}_render.h5")
    if not os.path.exists(render_h5):
        return None
    with h5py.File(render_h5, "r") as f:
        if "render/positions" not in f:
            return None
        pos = np.ascontiguousarray(f["render/positions"][:], dtype=np.float32)
    pos.setflags(write=False)
    idx.render_positions[instance] = pos
    return pos


def _get_render_indices(idx, instance: str) -> Optional[np.ndarray]:
    """render/indices [Nt, 3] int32。常驻副本优先，缺失回退读盘并回填。"""
    ind = idx.render_indices.get(instance)
    if ind is not None:
        return ind
    render_h5 = os.path.join(idx.workspace, "l2", "render", f"{instance}_render.h5")
    if not os.path.exists(render_h5):
        return None
    with h5py.File(render_h5, "r") as f:
        if "render/indices" not in f:
            return None
        ind = np.ascontiguousarray(f["render/indices"][:], dtype=np.int32)
    ind.setflags(write=False)
    idx.render_indices[instance] = ind
    return ind


def _cached_disp_vertex(
    idx,
    instance: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> np.ndarray:
    """
    顶点位移向量 [Nv, 3] float32（只读），带两级缓存：
    key = (U 结果文件签名, instance, 帧)。vertex-displacements / modal-shape /
    modal-animation 共用（modal-animation 的多帧 sin 合成在此之上现算，不缓存）。
    """
    vtx_nr = idx.vtx_node_row.get(instance)
    if vtx_nr is None:
        raise NotFoundError(
            f"Instance '{instance}' has no vtx_node_row; indexed geometry required",
            {"instance": instance},
        )

    h5_path = _manifest_result_h5_path(idx.workspace, step, "U", result_group)
    h5_sig = _result_h5_sig(h5_path)
    key = ("disp_vertex", idx.odb_id, instance, h5_path, h5_sig,
           cache_store.render_sig(idx.workspace, instance), int(frame_idx))
    bucket = cache_store.group_bucket(result_group)
    if h5_sig is not None:
        cached = _vertex_cache_get(key)
        if cached is None:
            d = cache_store.arrays_get(idx.workspace, bucket, key)
            cached = d.get("disp") if d is not None else None
            if cached is not None:
                _vertex_cache_put(key, cached)   # 磁盘命中 → 提升到内存层
        if cached is not None:
            return cached

    disp_node = _read_raw_node_displacements(idx, instance, step, frame_idx, result_group)
    disp_vertex = _disp_at_rows(disp_node, vtx_nr)
    disp_vertex.setflags(write=False)
    if h5_sig is not None:
        _vertex_cache_put(key, disp_vertex)
        cache_store.arrays_put(idx.workspace, bucket, key, {"disp": disp_vertex})
    return disp_vertex


def _load_vertex_displacements(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> Tuple[np.ndarray, object]:
    """
    Load U NODAL displacement for frame_idx and map from nodes to render vertices.
    Returns (disp_vertex [Nv, 3] float32, idx).  Cached (see _cached_disp_vertex).
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' render data not loaded")
    return _cached_disp_vertex(idx, instance, step, frame_idx, result_group), idx


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

    # np.add.at 在 8M 三角形上是秒级慢操作；bincount 按 (角, 分量) 聚合快一个量级
    n_verts = positions.shape[0]
    normals = np.zeros((n_verts, 3), dtype=np.float64)
    for corner in range(3):
        col = indices[:, corner]
        for c in range(3):
            normals[:, c] += np.bincount(
                col, weights=face_normals[:, c], minlength=n_verts)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths  = np.where(lengths < 1e-12, 1.0, lengths)
    return (normals / lengths).astype(np.float32)


def _compute_vertex_normals_vtk(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Compute point normals via VTK/PyVista, falling back to caller on import/runtime errors."""
    import pyvista as pv

    n_faces = int(indices.shape[0])
    faces = np.empty((n_faces, 4), dtype=np.int64)
    faces[:, 0] = 3
    faces[:, 1:] = indices.astype(np.int64, copy=False)
    mesh = pv.PolyData(np.ascontiguousarray(positions, dtype=np.float32), faces.ravel())
    mesh = mesh.compute_normals(
        point_normals=True,
        cell_normals=False,
        split_vertices=False,
        auto_orient_normals=False,
        consistent_normals=False,
        inplace=False,
    )
    return np.ascontiguousarray(mesh.point_data["Normals"], dtype=np.float32)


def _compute_vertex_normals_fast(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    backend = os.getenv("APP_L3_NORMAL_BACKEND", "pyvista").strip().lower()
    if backend in ("pyvista", "vtk"):
        try:
            return _compute_vertex_normals_vtk(positions, indices)
        except Exception:
            logger.exception("VTK/PyVista normal computation failed; falling back to NumPy")
    return _compute_vertex_normals(positions, indices)


def _deform_surface_from_disp(idx, instance, disp_node, scale):
    """
    Surface deformed positions + normals from a preloaded raw node displacement
    array (see _load_raw_node_displacements).  Factored out so callers that also
    need aux geometry can share a single U read.
    Returns (deformed [Nv, 3], normals [Nv, 3]) float32.
    """
    vtx_nr = idx.vtx_node_row.get(instance)
    if vtx_nr is None:
        raise NotFoundError(
            f"Instance '{instance}' has no vtx_node_row; indexed geometry required",
            {"instance": instance},
        )
    disp_vertex = _disp_at_rows(disp_node, vtx_nr)

    positions = _get_render_positions(idx, instance)
    if positions is None:
        raise NotFoundError(
            f"Render H5 not found for instance '{instance}'",
            {"instance": instance},
        )
    indices = _get_render_indices(idx, instance)

    deformed = (positions + np.float32(scale) * disp_vertex).astype(np.float32)
    if indices is not None:
        normals = _compute_vertex_normals_fast(deformed, indices)
    else:
        normals = np.zeros_like(deformed)
    return deformed, normals


def _deform_aux_from_disp(idx, instance, disp_node, scale):
    """
    Aux (line/point/coupling) deformed L3BE sections from a preloaded raw node
    displacement array.  Best-effort: returns [] when the surface H5 is missing
    or carries no node_rows.  See frame_deformed_aux_geometry for section layout.
    """
    surface_h5 = os.path.join(idx.workspace, "l2", "geometry", f"{instance}_surface.h5")
    if not os.path.exists(surface_h5):
        return []

    # (group, positions-dataset, node_rows-dataset, section_name, reshape-to-[K,3])
    specs = [
        ("lines",     "lines/positions",     "lines/node_rows",     "line_positions",     True),
        ("points",    "points/positions",    "points/node_rows",    "point_positions",    False),
        ("couplings", "couplings/positions", "couplings/node_rows", "coupling_positions", False),
    ]

    collected = []   # (section_name, orig_pos [K,3] float32, rows [K] int)
    with h5py.File(surface_h5, "r") as f:
        for _grp, pos_path, rows_path, section, needs_reshape in specs:
            if pos_path not in f or rows_path not in f:
                continue
            pos  = np.ascontiguousarray(f[pos_path][:], dtype=np.float32)
            rows = np.ascontiguousarray(f[rows_path][:], dtype=np.int64).reshape(-1)
            if needs_reshape:
                pos = pos.reshape(-1, 3)   # [N,2,3] -> [N*2,3]
            if pos.shape[0] != rows.shape[0]:
                # Defensive: shapes must align 1:1 (endpoint order preserved)
                continue
            collected.append((section, pos, rows))

    sections = []
    for section, pos, rows in collected:
        disp = _disp_at_rows(disp_node, rows)                  # [K, 3]
        deformed = (pos + np.float32(scale) * disp).astype(np.float32)
        sections.append((section, np.ascontiguousarray(deformed)))
    return sections


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
    Shares the frame_deformed_with_aux cache (aux sections are cheap to carry).
    """
    positions, normals, _aux = frame_deformed_with_aux(
        registry, odb_id, instance, step, frame_idx, scale, result_group
    )
    return positions, normals


def frame_deformed_with_aux(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    scale: float = 1.0,
    result_group: str = None,
) -> Tuple[np.ndarray, np.ndarray, list]:
    """
    Combined surface + aux deform sharing a SINGLE U read — the hot path for the
    deformed-positions endpoint (matters for large models / animation).

    Returns (positions [Nv,3], normals [Nv,3], aux_sections) where aux_sections is
    the list from _deform_aux_from_disp (line/point/coupling; possibly empty).

    本函数不缓存：deformed-positions 接口在路由层按 (U 结果文件签名, 几何签名,
    instance, 帧, scale) 缓存最终响应包（cache_store payloads），命中时不会进到这里。
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' render data not loaded")

    disp_node = _read_raw_node_displacements(idx, instance, step, frame_idx, result_group)
    positions, normals = _deform_surface_from_disp(idx, instance, disp_node, scale)
    aux = _deform_aux_from_disp(idx, instance, disp_node, scale)
    return positions, normals, aux


def frame_vertex_displacements(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> np.ndarray:
    """
    Return raw U displacement per render vertex [Nv, 3] float32, without scale or position offset.

    Requires indexed geometry (vtx_node_row present in ModelIndex).
    """
    disp_vertex, _ = _load_vertex_displacements(
        registry, odb_id, instance, step, frame_idx, result_group
    )
    return disp_vertex


def frame_deformed_aux_geometry(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    scale: float = 1.0,
    result_group: str = None,
) -> list:
    """
    Compute deformed positions for the auxiliary (non-surface) geometries that
    live in l2/geometry/<inst>_surface.h5: beam/truss lines, MASS/ROTARYI points,
    and RBE2/coupling spider lines.  Each carries a per-endpoint node_rows dataset
    written by L2 ingest; deformed = original + scale * U[node_row].

    Returns a list of (section_name, ndarray) L3BE sections for whichever aux
    geometries exist AND have node_rows.  Empty list if none apply (e.g. surface
    predates the node_rows change, or instance has no line/point/coupling data).
    Never raises for a missing surface file — aux geometry is best-effort overlay.

    Section names mirror the /geometry endpoints so the frontend reuses them:
      line_positions     [Nl*2, 3] float32
      point_positions    [Np,   3] float32
      coupling_positions [Nc*2, 3] float32

    Standalone entry (own U read).  The deformed-positions endpoint instead uses
    frame_deformed_with_aux to share one U read with the surface deform.
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})

    surface_h5 = os.path.join(idx.workspace, "l2", "geometry", f"{instance}_surface.h5")
    if not os.path.exists(surface_h5):
        return []

    disp_node, _ = _load_raw_node_displacements(
        registry, odb_id, instance, step, frame_idx, result_group
    )
    return _deform_aux_from_disp(idx, instance, disp_node, scale)


def deform_scale_stats(
    registry: OdbRegistry,
    odb_id: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> dict:
    """
    Aggregate the raw quantities behind the deform-scale suggestion, across ALL
    instances regardless of which are currently displayed:
      bbox_min / bbox_max — assembly-level bounding box (union of instance bboxes), or None
      max_disp            — max(|U|) across all three displacement directions, all instances
      nlgeom              — True if the step ran with nlgeom (displacement already physical)

    Exposed separately so callers that need a scale consistent with OTHER geometry
    (e.g. the test-mesh sync view) can combine these stats with their own bbox.
    """
    import json as _json

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' render data not loaded")

    # 为算一个标量要把该帧所有 instance 的整块 U 读盘 → 结果 dict 按
    # (U 结果文件签名, step, 帧) 走小结果缓存（内存 LRU + JSON 磁盘层）。
    h5_path = _manifest_result_h5_path(idx.workspace, step, "U", result_group)
    h5_sig = _result_h5_sig(h5_path)
    stats_key = ("deform_stats", odb_id, h5_path, h5_sig, step,
                 int(frame_idx), result_group)
    if h5_sig is not None:
        hit = _range_cache_get(stats_key)
        if hit is _CACHE_MISS:
            hit = cache_store.json_get(idx.workspace, cache_store.group_bucket(result_group),
                                       stats_key)
            if hit is not _CACHE_MISS:
                _range_cache_put(stats_key, hit)
        if hit is not _CACHE_MISS and isinstance(hit, dict):
            return dict(hit)

    manifest = ManifestRepo(idx.workspace)

    step_info = manifest.get_step_info(step, result_group)
    nlgeom = bool(step_info.get("nlgeom")) if step_info is not None else False

    stats = {"bbox_min": None, "bbox_max": None, "max_disp": 0.0, "nlgeom": nlgeom}

    all_instances = manifest.list_instances()
    if not all_instances:
        return stats

    # Aggregate assembly-level bbox across all instances
    global_min = None
    global_max = None
    for row in all_instances:
        bbox_min_raw = row.get("bbox_min")
        bbox_max_raw = row.get("bbox_max")
        if bbox_min_raw is None or bbox_max_raw is None:
            continue
        lo = np.array(
            _json.loads(bbox_min_raw) if isinstance(bbox_min_raw, str) else bbox_min_raw,
            dtype=np.float64,
        )
        hi = np.array(
            _json.loads(bbox_max_raw) if isinstance(bbox_max_raw, str) else bbox_max_raw,
            dtype=np.float64,
        )
        global_min = lo if global_min is None else np.minimum(global_min, lo)
        global_max = hi if global_max is None else np.maximum(global_max, hi)

    if global_min is None:
        return stats
    stats["bbox_min"] = global_min.tolist()
    stats["bbox_max"] = global_max.tolist()

    if not os.path.exists(h5_path):
        return stats

    # Aggregate max displacement across all instances
    max_scalar_disp = 0.0
    with h5py.File(h5_path, "r") as f:
        for row in all_instances:
            inst = row["instance_name"]
            ds_path = f"/NODAL/{inst}/data"
            if ds_path not in f:
                continue
            ds = f[ds_path]
            if frame_idx < 0 or frame_idx >= ds.shape[0]:
                continue
            disp_node = ds[frame_idx, :, :3].astype(np.float64)   # [N_nodes, 3] — UX/UY/UZ only
            if disp_node.ndim != 2 or disp_node.shape[1] < 3:
                continue
            inst_max = float(np.max(np.abs(disp_node)))
            if inst_max > max_scalar_disp:
                max_scalar_disp = inst_max

    stats["max_disp"] = max_scalar_disp
    if h5_sig is not None:
        _range_cache_put(stats_key, dict(stats))
        cache_store.json_put(idx.workspace, cache_store.group_bucket(result_group),
                             stats_key, stats)
    return stats


def suggest_deform_scale(
    registry: OdbRegistry,
    odb_id: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> float:
    """
    Compute a globally consistent deformation scale factor for the given step/frame.

    Aggregates across ALL instances regardless of which are currently displayed:
      maxScalarSize = longest edge of the assembly-level bounding box (union of all instance bboxes)
      maxScalarDisp = max(|U|) across all three displacement directions across all instances
      scale = maxScalarSize / 10 / maxScalarDisp  (returns 0 if maxScalarDisp == 0)
    """
    stats = deform_scale_stats(registry, odb_id, step, frame_idx, result_group)
    if stats["nlgeom"]:
        return 1.0
    if stats["bbox_min"] is None or stats["max_disp"] == 0.0:
        return 0.0
    max_scalar_size = float(np.max(np.asarray(stats["bbox_max"]) - np.asarray(stats["bbox_min"])))
    return float(max_scalar_size / 10.0 / stats["max_disp"])


# ── 模态谐波动画辅助 ─────────────────────────────────────────────────────────

def _load_disp_vertex(
    idx,
    instance: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
):
    """
    共用帮助：读取指定帧的顶点位移向量 [Nv, 3] float32，以及原始坐标和索引。
    返回 (positions [Nv,3], disp_vertex [Nv,3], indices [Nt,3] or None)。
    positions/indices 取 ModelIndex 常驻副本，disp_vertex 走两级缓存。
    """
    positions = _get_render_positions(idx, instance)
    if positions is None:
        raise NotFoundError(f"Render H5 not found for instance '{instance}'", {"instance": instance})
    indices = _get_render_indices(idx, instance)

    disp_vertex = _cached_disp_vertex(idx, instance, step, frame_idx, result_group)
    return positions, disp_vertex, indices


def modal_shape_displacement(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    result_group: str = None,
) -> np.ndarray:
    """
    返回指定模态阶次（frame_idx）的顶点位移向量 [Nv, 3] float32。
    不乘 scale、不加坐标，供前端 GPU shader 模式使用。
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' render data not loaded")

    _positions, disp_vertex, _indices = _load_disp_vertex(idx, instance, step, frame_idx, result_group)
    return disp_vertex


def modal_animation_frames(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    step: str,
    frame_idx: int,
    scale: float = 1.0,
    n_frames: int = 20,
    result_group: str = None,
) -> bytes:
    """
    预计算 n_frames 帧谐波动画坐标，打包成二进制返回。

    每帧 = positions + scale * sin(2π * i / n_frames) * disp_vertex

    二进制格式：
      [n_frames uint32][n_verts uint32][n_frames × n_verts × 3 × float32]
    """
    n_frames = max(4, min(n_frames, 120))   # 限制范围，防止内存爆炸

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    if not idx.is_render_ready:
        raise NotReadyError(f"ODB '{odb_id}' render data not loaded")

    positions, disp_vertex, _indices = _load_disp_vertex(idx, instance, step, frame_idx, result_group)

    n_verts = positions.shape[0]
    phases  = np.sin(2.0 * np.pi * np.arange(n_frames) / n_frames, dtype=np.float64)

    # [n_frames, Nv, 3] float32
    frames = (
        positions[np.newaxis, :, :]                                # [1, Nv, 3]
        + np.float32(scale) * phases[:, np.newaxis, np.newaxis]    # [n_frames, 1, 1]
        * disp_vertex[np.newaxis, :, :]                            # [1, Nv, 3]
    ).astype(np.float32)

    header = np.array([n_frames, n_verts], dtype=np.uint32)
    return header.tobytes() + frames.tobytes()
