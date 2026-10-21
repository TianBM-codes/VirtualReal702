"""
全量预计算计划：枚举模型显示类接口在默认渲染参数下的全部有限组合，并按顺序执行。

设计见 docs/l3/L3-Full-Precompute-Cache-Proposal.md（ready 必须等全量计划完成，§0.1）。

覆盖范围（每个 result_group × step × field × 有结果块的 instance × 全部真实帧）：
  - frame-scalar-range：默认分量 + 每个实际分量；
  - frame-scalars：默认分量 + 每个实际分量 × smooth/flat；
  - frame-colors：U1/U2/U3 × smooth/flat（路由只接受这三个分量）；
  - U 场：每帧顶点位移向量（vertex-displacements / deformed-* / modal-* 共用）与
    deform-suggest-scale 统计。
无表面三角形的实例（纯梁/质量点）跳过云图类，只准备位移。合法的全 NaN 结果
（NotFoundError，接口同样返回空 payload）记为跳过；其他异常在 strict 模式下直接失败。

任意参数（scale、feature_angle、平均阈值、集合、归一化范围……）不在计划内，
首次请求时计算并缓存。

已缓存的组合命中后立即返回，所以计划可以重复执行：中途退出后再次执行，
已落盘的部分全部命中，只补算剩余项。
"""
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ..core.errors import NotFoundError
from ..infra.manifest_repo import ManifestRepo
from .result_service import (
    compute_scalar_range, deform_scale_stats, frame_colors, frame_scalars,
    frame_vertex_displacements,
)

logger = logging.getLogger(__name__)

# 覆盖范围变化时加 1：后台预热的完成标记带此版本，旧项目会按新计划补算。
PLAN_VERSION = 1

RENDER_MODES = ("smooth", "flat")
FRAME_COLORS_COMPONENTS = ("U1", "U2", "U3")
_PROGRESS_INTERVAL_S = 30.0


class PlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    result_group: Optional[str]
    step: str
    field: str
    file_path: Optional[str]
    instances: Tuple[str, ...]
    frames: Tuple[int, ...]
    n_components: int

    @property
    def context(self) -> str:
        return f"{self.result_group}/{self.step}/{self.field}"

    @property
    def component_options(self) -> Tuple[Optional[int], ...]:
        """None = 接口省略 component_idx 的默认请求（矢量取模长、标量取原值）。"""
        return (None,) + tuple(range(self.n_components))


def _columns(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _frames_of(manifest: ManifestRepo, step: str, result_group) -> Tuple[int, ...]:
    """真实帧编号：优先 frames 表，其次 steps.num_frames，都没有只取第 0 帧。"""
    try:
        idxs = sorted({int(r["frame_idx"]) for r in manifest.get_frames(step, result_group)
                       if r["frame_idx"] is not None})
        if idxs:
            return tuple(idxs)
    except Exception:
        pass
    try:
        info = manifest.get_step_info(step, result_group)
        n = int(info.get("num_frames")) if info else 0
    except Exception:
        n = 0
    return tuple(range(n)) if n > 0 else (0,)


def list_targets(manifest: ManifestRepo, *, result_group=None, group_only: bool = False,
                 default_instances=()) -> List[Target]:
    """从 manifest 枚举计划目标。缺 result_blocks 表的老库用 default_instances。"""
    with manifest._get_conn() as conn:
        cols = _columns(conn, "result_files")
        if not cols:
            return []
        select = ", ".join(c if c in cols else f"NULL AS {c}" for c in
                           ("result_group", "step_name", "field_name", "file_path", "components"))
        sql = f"SELECT {select} FROM result_files"
        params: tuple = ()
        if group_only:
            sql += " WHERE result_group IS ?"
            params = (result_group,)
        rows = conn.execute(sql, params).fetchall()
        block_instances: Dict[tuple, set] = {}
        has_blocks = bool(_columns(conn, "result_blocks"))
        if has_blocks:
            for rg, step, field, inst in conn.execute(
                    "SELECT DISTINCT result_group, step_name, field_name, instance_name "
                    "FROM result_blocks"):
                block_instances.setdefault((rg, step, field), set()).add(inst)

    targets = []
    seen = set()
    for rg, step, field, file_path, comps in rows:
        if (rg, step, field) in seen:
            continue
        seen.add((rg, step, field))
        try:
            n_comp = len(json.loads(comps)) if comps else 0
        except (TypeError, ValueError):
            n_comp = 0
        insts = block_instances.get((rg, step, field)) if has_blocks else set(default_instances)
        targets.append(Target(
            result_group=rg, step=step, field=field, file_path=file_path,
            instances=tuple(sorted(insts or ())),
            frames=_frames_of(manifest, step, rg), n_components=n_comp,
        ))
    return targets


def _has_surface(idx, instance: str) -> bool:
    rows = getattr(idx, "render_source_elem_row", None) or {}
    return instance not in rows or len(rows[instance]) > 0


def count_calls(idx, targets: List[Target]) -> int:
    total = 0
    for t in targets:
        per_frame_surface = len(t.component_options) * (1 + len(RENDER_MODES)) \
            + len(FRAME_COLORS_COMPONENTS) * len(RENDER_MODES)
        for inst in t.instances:
            per_frame = per_frame_surface if _has_surface(idx, inst) else 0
            if t.field == "U":
                per_frame += 1
            total += per_frame * len(t.frames)
        if t.field == "U":
            total += len(t.frames)
    return total


def run_plan(reg, odb_id: str, idx, targets: List[Target], *, strict: bool,
             check: Optional[Callable[[], None]] = None,
             progress: Optional[Callable[[str, int], None]] = None) -> dict:
    """
    按 (目标, 实例, 帧) 顺序执行计划，同一结果文件的读取集中在一起。

    strict=True：除合法的无数据（NotFoundError）外任何失败都抛 PlanError；
    strict=False（后台预热）：失败只计数。check() 在每次计算前调用，可抛异常中止。
    progress(message, percent_of_plan) 约每 30 秒及结束时调用一次。
    返回 {"total", "done", "skipped", "failed", "seconds", "by_kind": {种类: 成功数}}。
    """
    total = count_calls(idx, targets)
    stats = {"total": total, "done": 0, "skipped": 0, "failed": 0, "by_kind": {}}
    t0 = time.time()
    last_report = [t0]

    def _report(force: bool = False):
        now = time.time()
        if progress is None or (not force and now - last_report[0] < _PROGRESS_INTERVAL_S):
            return
        last_report[0] = now
        finished = stats["done"] + stats["skipped"] + stats["failed"]
        pct = int(finished * 100 / total) if total else 100
        elapsed = now - t0
        eta = elapsed / finished * (total - finished) if finished else 0.0
        progress(f"全量缓存 {finished}/{total}（{pct}%），已用 {elapsed / 60:.1f} 分钟，"
                 f"预计还需 {eta / 60:.1f} 分钟；跳过 {stats['skipped']}，失败 {stats['failed']}",
                 pct)

    def _call(kind: str, context: str, fn, *args, **kwargs):
        if check is not None:
            check()
        try:
            fn(*args, **kwargs)
            stats["done"] += 1
            stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
        except NotFoundError as exc:
            # 有结果块但全 NaN 等合法的"该实例无此量"：接口返回空 payload 置灰。
            stats["skipped"] += 1
            logger.debug("plan %s: no data, skipped (%s)", context, exc)
        except Exception as exc:
            if strict:
                raise PlanError(f"{context} 计算失败：{exc}") from exc
            stats["failed"] += 1
            logger.debug("plan %s failed: %s", context, exc)
        _report()

    for t in targets:
        rg = t.result_group
        for instance in t.instances:
            surface = _has_surface(idx, instance)
            for frame in t.frames:
                ctx = f"{t.context}/{instance} 第 {frame} 帧"
                if surface:
                    for comp in t.component_options:
                        cctx = f"{ctx} 分量 {'默认' if comp is None else comp}"
                        _call("range", cctx, compute_scalar_range, reg, odb_id, instance, t.step, t.field,
                              frame, component_idx=comp, result_group=rg)
                        for mode in RENDER_MODES:
                            _call("scalars", f"{cctx} {mode}", frame_scalars, reg, odb_id, instance,
                                  t.step, t.field, frame, component_idx=comp,
                                  render_mode=mode, result_group=rg)
                    for comp in FRAME_COLORS_COMPONENTS:
                        for mode in RENDER_MODES:
                            _call("colors", f"{ctx} frame-colors {comp} {mode}", frame_colors, reg, odb_id,
                                  instance, t.step, t.field, frame, component=comp,
                                  render_mode=mode, result_group=rg)
                if t.field == "U":
                    _call("displacement", f"{ctx} 位移", frame_vertex_displacements, reg, odb_id, instance,
                          t.step, frame, result_group=rg)
        if t.field == "U":
            for frame in t.frames:
                _call("deform_stats", f"{t.context} 第 {frame} 帧 变形比例", deform_scale_stats,
                      reg, odb_id, t.step, frame, rg)

    stats["seconds"] = round(time.time() - t0, 1)
    _report(force=True)
    return stats
