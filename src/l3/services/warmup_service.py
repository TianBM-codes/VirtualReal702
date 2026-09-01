"""
缓存预热服务。

目标：用户第一次点开云图就命中缓存。做法是在三个时机后台预算"第 0 帧默认
视图"（每个 result_group × step × field × instance，分量取默认模长/Mises）：

  1. 模型加载完成（core.state 的 on_render_ready_loaded 回调，覆盖启动预载
     与首次访问按需加载）；
  2. L3 poll 线程发现常驻模型的 result_files 出现新 (group, step, field)
     （覆盖 job_runner 在另一进程解析完新 result_group 的场景）；
  3. 手动 POST /api/odb/{odb_id}/results/warmup。

预热直接调 compute_scalar_range / frame_scalars，算完自然写入内存 + 磁盘
缓存（见 result_service 的缓存层）；U 场再顺带预热变形家族的
deform-suggest-scale 统计与各 instance 第 0 帧顶点位移向量。已缓存的组合命中后立即返回，所以预热
是幂等的，重复触发只补算新增的字段。整个过程单线程串行、daemon 线程执行，
失败只记日志、不影响正常请求。
"""
import logging
import threading
import time
from typing import Dict, Optional, Set

from ..core.state import OdbRegistry, registry as _global_registry
from ..infra.manifest_repo import ManifestRepo
from .result_service import (
    compute_scalar_range,
    deform_scale_stats,
    frame_scalars,
    frame_vertex_displacements,
)

logger = logging.getLogger(__name__)

_active: Set[str] = set()
_lock = threading.Lock()
# 上次预热时看到的 (result_group, step, field) 集合；poll 检测新增用。
_warmed_targets: Dict[str, frozenset] = {}


def _list_result_targets(manifest: ManifestRepo) -> list:
    """result_files 表里所有 (result_group, step, field) 组合。"""
    try:
        with manifest._get_conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT DISTINCT result_group, step_name, field_name "
                    "FROM result_files"
                ).fetchall()
                return [(r["result_group"], r["step_name"], r["field_name"])
                        for r in rows]
            except Exception:
                # 老库没有 result_group 列
                rows = conn.execute(
                    "SELECT DISTINCT step_name, field_name FROM result_files"
                ).fetchall()
                return [(None, r["step_name"], r["field_name"]) for r in rows]
    except Exception:
        return []


def warm_odb_sync(odb_id: str, reason: str = "",
                  reg: Optional[OdbRegistry] = None) -> dict:
    """同步预热一个模型的第 0 帧默认视图。未加载的模型会先触发加载。"""
    reg = reg if reg is not None else _global_registry
    t0 = time.time()
    idx = reg.get(odb_id)
    if idx is None or not idx.is_render_ready:
        return {"odb_id": odb_id, "status": "not_ready"}

    manifest = ManifestRepo(idx.workspace)
    targets = _list_result_targets(manifest)
    instances = list(idx.source_node_rows.keys())
    n_ok = n_skip = 0
    for result_group, step, field in targets:
        for instance in instances:
            try:
                compute_scalar_range(
                    registry=reg, odb_id=odb_id, instance=instance,
                    step=step, field=field, frame_idx=0,
                    result_group=result_group,
                )
                frame_scalars(
                    registry=reg, odb_id=odb_id, instance=instance,
                    step=step, field=field, frame_idx=0,
                    result_group=result_group,
                )
                n_ok += 1
            except Exception:
                # 该 instance 没有这个字段/位置数据是常态，跳过即可
                n_skip += 1
    # 变形家族顺带预热（U 场第 0 帧）：deform-suggest-scale 统计 + 每 instance
    # 顶点位移向量。deformed-positions 的整包按 scale 进缓存键、前端实际 scale
    # 不可预知 → 不预热，留给首次请求填。
    n_deform = 0
    for result_group, step, field in targets:
        if field != "U":
            continue
        try:
            deform_scale_stats(reg, odb_id, step, 0, result_group)
            n_deform += 1
        except Exception:
            pass
        for instance in instances:
            try:
                frame_vertex_displacements(
                    registry=reg, odb_id=odb_id, instance=instance,
                    step=step, frame_idx=0, result_group=result_group,
                )
                n_deform += 1
            except Exception:
                # 该 instance 无 vtx_node_row / 无 U 数据是常态，跳过
                pass
    _warmed_targets[odb_id] = frozenset(targets)

    dt = time.time() - t0
    logger.info("Warmup[%s]%s: %d combo(s) warmed, %d skipped, %d deform, %.1fs",
                odb_id, f" ({reason})" if reason else "", n_ok, n_skip, n_deform, dt)
    return {"odb_id": odb_id, "status": "done",
            "warmed": n_ok, "skipped": n_skip,
            "deform_warmed": n_deform, "seconds": round(dt, 1)}


def warm_odb_async(odb_id: str, reason: str = "",
                   reg: Optional[OdbRegistry] = None) -> str:
    """后台线程预热；同一模型同时只跑一个，返回 started / already_running。"""
    with _lock:
        if odb_id in _active:
            return "already_running"
        _active.add(odb_id)

    def _run():
        try:
            warm_odb_sync(odb_id, reason=reason, reg=reg)
        except Exception:
            logger.exception("Warmup[%s] failed", odb_id)
        finally:
            with _lock:
                _active.discard(odb_id)

    threading.Thread(target=_run, daemon=True,
                     name=f"cache-warmup-{odb_id}").start()
    return "started"


def maybe_warm_new_targets(odb_id: str,
                           reg: Optional[OdbRegistry] = None) -> None:
    """poll 线程用：常驻模型的 result_files 出现新组合时重新预热（幂等）。"""
    reg = reg if reg is not None else _global_registry
    idx = reg.peek(odb_id)      # 只看常驻，不触发加载
    if idx is None or not idx.is_render_ready:
        return
    targets = frozenset(_list_result_targets(ManifestRepo(idx.workspace)))
    if not targets or _warmed_targets.get(odb_id) == targets:
        return
    warm_odb_async(odb_id, reason="new result targets", reg=reg)
