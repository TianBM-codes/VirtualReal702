"""
Node time-value query service.

按"时间"查询一组节点上某个 NODAL 场的取值（全部分量 + 可用不变量）。

时间的两种口径
--------------
  - 传 step   → time 是该 step 内的局部时间（即 Abaqus 的 step time，
                对应 manifest frames 表的 frame_value）。
  - 不传 step → time 是全局时间：按 step_number 顺序，把各 step 的时长
                （= 该 step 最后一帧的 frame_value）首尾相接累加。
                全局口径只纳入有时间语义的 step（STATIC / DYNAMIC），
                FREQUENCY / BUCKLE 的 frame_value 是频率/特征值，不参与。

时间匹配策略（time 落不到帧上时）
--------------------------------
  prev   → 取时间 ≤ time 的最近一帧
  next   → 取时间 ≥ time 的最近一帧
  interp → 取夹住 time 的两帧做线性插值。
           不变量按"先逐帧取值、再对标量插值"处理（与直接对插值后的
           张量重算不变量略有差别，但与存储式不变量的读取口径一致）。

  time 正好命中某帧（相对容差 1e-9）时三种策略等价，直接取该帧。
  越界规则：time 早于第一帧时 prev/interp 报错（next 取第一帧）；
  晚于最后一帧时 next/interp 报错（prev 取最后一帧）。

  显式传 FREQUENCY/BUCKLE 步时，"time" 轴实际是 frame_value（模态阶次/
  频率/特征值）：exact/prev/next 按该轴取最近一帧有意义，interp 落在
  两帧之间时报错（两个模态振型插值无物理意义）。

本服务的错误一律抛 ValidationError / NotFoundError，message 用中文（会直接
展示给最终用户）。路由层把它们转成 HTTP 200 + 信封里的 code/message，见
api/routes/node_table.py。

值的读取方式与 node_table_service 相同：L1 结果 HDF5 的
/NODAL/<instance>/{data,labels}，label → 行号用排序 + searchsorted。
"""
import json
import os
from typing import Dict, List, Optional, Tuple

import h5py
import numpy as np

from ..core.errors import NotFoundError, ValidationError
from ..core.state import OdbRegistry
from ..infra.manifest_repo import ManifestRepo
from .node_table_service import _result_h5_path
from src.l1.manifest_schema import canon_instance

# 有时间语义（frame_value 是时间）的 procedure；全局时间轴只串联这些 step
_TIME_PROCEDURES = ("STATIC", "DYNAMIC")

# MAGNITUDE 是唯一可以在查询时现算的不变量（向量场的 L2 范数）
_ONTHEFLY_INVARIANTS = ("MAGNITUDE",)

_VALID_TIME_MATCH = ("prev", "next", "interp")


# ── timeline ──────────────────────────────────────────────────────────────────

def _build_timeline(repo: ManifestRepo, step: Optional[str],
                    result_group: Optional[str]) -> Tuple[List[dict], str]:
    """
    Build the ordered list of frame points on the queried time axis.

    Each point: {"step", "frame_idx", "step_time", "global_time", "time"}
    ("time" is the axis value actually compared against the request:
     step_time in step mode, global_time in global mode.)

    Returns (points, time_mode) with time_mode in {"step", "global"}.
    """
    if step is not None:
        if repo.get_step_info(step, result_group) is None:
            raise NotFoundError(f"分析步 '{step}' 不存在", {"step": step})
        points = []
        for row in repo.get_frames(step, result_group):
            t = float(row["frame_value"] or 0.0)
            points.append({
                "step": step, "frame_idx": int(row["frame_idx"]),
                "step_time": t, "global_time": None, "time": t,
            })
        if not points:
            raise NotFoundError(f"分析步 '{step}' 没有任何帧", {"step": step})
        points.sort(key=lambda p: (p["time"], p["frame_idx"]))
        return points, "step"

    steps = [s for s in repo.list_steps_by_group(result_group)
             if (s.get("procedure") or "").upper() in _TIME_PROCEDURES]
    if not steps:
        raise ValidationError(
            "该结果组里没有 STATIC/DYNAMIC 分析步，无法按全局时间查询；"
            "其他类型的分析步（如 FREQUENCY/BUCKLE）请显式传 step 参数",
            {"result_group": result_group},
        )
    points = []
    offset = 0.0
    for srow in steps:
        sname = srow["step_name"]
        # 新数据（L1 已提取 step.totalTime）直接用精确的 step 起始全局时间；
        # 旧数据没有该列/为 NULL 时退回"各 step 末帧时间累加"的推算
        exact_start = srow.get("total_time")
        if exact_start is not None:
            offset = float(exact_start)
        frames = repo.get_frames(sname, result_group)
        step_times = [float(r["frame_value"] or 0.0) for r in frames]
        for row, t in zip(frames, step_times):
            points.append({
                "step": sname, "frame_idx": int(row["frame_idx"]),
                "step_time": t, "global_time": offset + t, "time": offset + t,
            })
        # 推进到下一 step 的起点：优先精确时长 timePeriod，否则用最大帧时间推算
        period = srow.get("time_period")
        if period is not None:
            offset += float(period)
        else:
            offset += max(step_times) if step_times else 0.0
    if not points:
        raise NotFoundError("所有 STATIC/DYNAMIC 分析步都没有帧数据",
                            {"result_group": result_group})
    points.sort(key=lambda p: (p["time"], p["step"], p["frame_idx"]))
    return points, "global"


def _resolve_frames(points: List[dict], time: float,
                    time_match: str) -> Tuple[List[Tuple[dict, float]], str]:
    """
    Pick the frame(s) + weights for the requested time.

    Returns (selection, resolved_mode); selection is [(point, weight), ...]
    with weights summing to 1. resolved_mode in {exact, prev, next, interp}.
    """
    times = np.array([p["time"] for p in points], dtype=np.float64)
    tol = 1e-9 * max(1.0, abs(time), float(np.max(np.abs(times))))

    exact = np.nonzero(np.abs(times - time) <= tol)[0]
    if exact.size:
        return [(points[int(exact[0])], 1.0)], "exact"

    t_range = {"time_min": float(times[0]), "time_max": float(times[-1]),
               "requested_time": time}
    right = int(np.searchsorted(times, time))   # first index with times[i] > time
    left = right - 1

    if time_match == "prev":
        if left < 0:
            raise ValidationError(
                "请求的时间早于第一帧，不存在更早的帧（time_match='prev'）",
                t_range)
        return [(points[left], 1.0)], "prev"

    if time_match == "next":
        if right >= len(points):
            raise ValidationError(
                "请求的时间晚于最后一帧，不存在更晚的帧（time_match='next'）",
                t_range)
        return [(points[right], 1.0)], "next"

    # interp
    if left < 0 or right >= len(points):
        raise ValidationError(
            f"请求的时间超出帧的时间范围 [{times[0]}, {times[-1]}]，无法插值",
            t_range)
    t0, t1 = times[left], times[right]
    if t1 - t0 <= tol:  # 两帧时间重合（如跨 step 边界），退化为取左帧
        return [(points[left], 1.0)], "exact"
    w = float((time - t0) / (t1 - t0))
    return [(points[left], 1.0 - w), (points[right], w)], "interp"


# ── HDF5 reading ──────────────────────────────────────────────────────────────

def _read_frame_matrix(workspace: str, step: str, field: str, instance: str,
                       frame_idx: int, req_labels: np.ndarray,
                       result_group: Optional[str]) -> Optional[np.ndarray]:
    """
    Read one frame of a NODAL field for the requested labels.

    Returns [N, ncomp] float64 (NaN = node not present), or None when the
    file/dataset does not exist for this (step, field, instance).
    """
    h5_path = _result_h5_path(workspace, step, field, result_group)
    if not os.path.exists(h5_path):
        return None
    with h5py.File(h5_path, "r") as rf:
        data_path = f"/NODAL/{instance}/data"
        labels_path = f"/NODAL/{instance}/labels"
        if data_path not in rf:
            return None
        ds = rf[data_path]
        if frame_idx >= ds.shape[0]:
            raise ValidationError(
                f"帧号 {frame_idx} 超出范围 [0, {ds.shape[0]})："
                f"分析步 '{step}' 的字段 '{field}' 没有这一帧",
                {"frame_idx": frame_idx, "field": field, "step": step},
            )
        if labels_path in rf:
            result_labels = rf[labels_path][:].astype(np.int64)
        else:
            geom_path = os.path.join(workspace, "l1", "geometry", f"{instance}.h5")
            if not os.path.exists(geom_path):
                return None
            with h5py.File(geom_path, "r") as gf:
                result_labels = gf["nodes/labels"][:].astype(np.int64)

        sort_order = np.argsort(result_labels)
        sorted_labels = result_labels[sort_order]
        ins_pos = np.searchsorted(sorted_labels, req_labels)
        ins_clamped = np.clip(ins_pos, 0, len(sorted_labels) - 1)
        found_mask = sorted_labels[ins_clamped] == req_labels
        result_rows = sort_order[ins_clamped]

        frame_data = ds[frame_idx]
        if frame_data.ndim == 1:
            frame_data = frame_data[:, np.newaxis]

        out = np.full((len(req_labels), frame_data.shape[1]), np.nan, dtype=np.float64)
        out[found_mask] = frame_data[result_rows[found_mask]]
        return out


# ── public API ────────────────────────────────────────────────────────────────

def get_node_time_value(
    registry: OdbRegistry,
    odb_id: str,
    instance: str,
    field: str,
    node_labels: List[int],
    time: float,
    step: Optional[str] = None,
    time_match: str = "interp",
    result_group: Optional[str] = None,
) -> dict:
    """
    Query all components + available invariants of a NODAL field for a set of
    node labels at an arbitrary time (see module docstring for semantics).
    """
    instance = canon_instance(instance)
    if time_match not in _VALID_TIME_MATCH:
        raise ValidationError(
            f"time_match 只能是 {list(_VALID_TIME_MATCH)} 之一，收到 '{time_match}'",
            {"time_match": time_match})
    if not node_labels:
        raise ValidationError("node_labels 不能为空")
    if not field:
        raise ValidationError("field 不能为空")

    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"工程 '{odb_id}' 不存在", {"odb_id": odb_id})
    repo = ManifestRepo(idx.workspace)
    if repo.get_instance_info(instance) is None:
        raise NotFoundError(f"工程 '{odb_id}' 中不存在部件实例 '{instance}'",
                            {"instance": instance})

    points, time_mode = _build_timeline(repo, step, result_group)
    selection, resolved_mode = _resolve_frames(points, float(time), time_match)

    # FREQUENCY/BUCKLE 步的 frame_value 是模态阶次/频率/特征值，不是时间：
    # 按该轴取最近一帧（exact/prev/next）有意义，但两个模态振型之间做线性
    # 插值在物理上没有意义，直接拒绝并提示改用 prev/next
    if step is not None and resolved_mode == "interp":
        sinfo = repo.get_step_info(step, result_group) or {}
        proc = (sinfo.get("procedure") or "").upper()
        if proc not in _TIME_PROCEDURES:
            raise ValidationError(
                f"分析步 '{step}' 是 {proc} 类型：它的 frame_value 是模态阶次/频率/"
                f"特征值，不是时间轴，两个模态振型之间做插值没有物理意义。"
                f"请改用 time_match='prev' 或 'next'，或把 time 填成某一帧的确切"
                f" frame_value。",
                {"step": step, "procedure": proc, "time_match": time_match})
    involved_steps = []
    for point, _w in selection:
        if point["step"] not in involved_steps:
            involved_steps.append(point["step"])

    # ── field metadata (components) — must exist in every involved step ───────
    components: List[str] = []
    db_invariants: List[str] = []
    for i, sname in enumerate(involved_steps):
        if not repo.has_nodal_block(sname, field, instance, result_group):
            raise ValidationError(
                f"分析步 '{sname}' 中，字段 '{field}' 在部件实例 '{instance}' 上"
                f"没有 NODAL（节点）位置的数据",
                {"field": field, "instance": instance, "step": sname})
        rf_row = repo.get_result_file(sname, field, result_group)
        if i == 0 and rf_row:
            try:
                components = json.loads(rf_row["components"] or "[]")
            except Exception:
                components = []
            try:  # 旧 manifest.db 可能没有 invariants 列
                raw_inv = rf_row["invariants"] if "invariants" in rf_row.keys() else "[]"
                db_invariants = json.loads(raw_inv or "[]")
            except Exception:
                db_invariants = []

    # ── discover invariants: stored synthetic fields "<field>_<SUFFIX>" ───────
    # (must be available in every involved step to survive interpolation)
    stored_inv: List[str] = []
    for i, sname in enumerate(involved_steps):
        rows = repo.get_fields_by_instance(sname, instance, result_group)
        suffixes = set()
        for row in rows:
            fname = row["field_name"]
            if not fname.startswith(field + "_"):
                continue
            try:
                comps = json.loads(row["components"] or "[]")
            except Exception:
                comps = []
            if not comps:
                suffixes.add(fname[len(field) + 1:])
        stored_inv = sorted(suffixes) if i == 0 else [s for s in stored_inv if s in suffixes]

    onthefly_inv = [inv for inv in db_invariants
                    if inv in _ONTHEFLY_INVARIANTS
                    and inv not in stored_inv and len(components) >= 2]
    invariants = stored_inv + onthefly_inv

    # ── read + blend frames ────────────────────────────────────────────────────
    req_labels = np.array(node_labels, dtype=np.int64)
    N = len(node_labels)
    ncomp = max(1, len(components))

    main_vals = np.zeros((N, ncomp), dtype=np.float64)
    inv_vals = {inv: np.zeros(N, dtype=np.float64) for inv in invariants}
    main_missing = False

    for point, weight in selection:
        mat = _read_frame_matrix(idx.workspace, point["step"], field, instance,
                                 point["frame_idx"], req_labels, result_group)
        if mat is None:
            main_missing = True
            break
        if mat.shape[1] != ncomp:
            ncomp_actual = mat.shape[1]
            raise ValidationError(
                f"字段 '{field}' 的数据有 {ncomp_actual} 个分量，与 manifest 中"
                f"声明的 {ncomp} 个不一致",
                {"field": field, "step": point["step"]})
        main_vals += weight * mat

        for inv in invariants:
            if inv in onthefly_inv:  # 现算：该帧分量的 L2 范数，再按权重混合
                inv_frame = np.sqrt(np.sum(mat * mat, axis=1))
            else:
                inv_mat = _read_frame_matrix(
                    idx.workspace, point["step"], f"{field}_{inv}", instance,
                    point["frame_idx"], req_labels, result_group)
                inv_frame = inv_mat[:, 0] if inv_mat is not None else np.full(N, np.nan)
            inv_vals[inv] += weight * inv_frame

    if main_missing:
        raise NotFoundError(
            f"找不到字段 '{field}' 在部件实例 '{instance}' 上的结果数据",
            {"field": field, "instance": instance, "steps": involved_steps})

    # ── assemble JSON-safe response ────────────────────────────────────────────
    def _f(v: float):
        return float(v) if np.isfinite(v) else None

    comp_names = components if components else [field]
    nodes_out = []
    for i, label in enumerate(node_labels):
        found = bool(np.any(np.isfinite(main_vals[i])))
        values = None
        if found:
            values = {name: _f(main_vals[i, j]) for j, name in enumerate(comp_names)}
            for inv in invariants:
                values[inv] = _f(inv_vals[inv][i])
        nodes_out.append({"label": int(label), "found": found, "values": values})

    times = [p["time"] for p in points]
    return {
        "instance": instance,
        "field": field,
        "components": comp_names,
        "invariants": invariants,
        "time_mode": time_mode,
        "step": step,
        "requested_time": float(time),
        "time_match": time_match,
        "resolved_mode": resolved_mode,
        "frames_used": [
            {
                "step": p["step"],
                "frame_idx": p["frame_idx"],
                "step_time": p["step_time"],
                "global_time": p["global_time"],
                "weight": round(w, 12),
            }
            for p, w in selection
        ],
        "time_range": {"min": float(min(times)), "max": float(max(times))},
        "nodes": nodes_out,
    }
