"""
缓存预热服务。

目标：用户第一次点开云图就命中缓存。做法是在三个时机后台预算"每个 step 最后
一帧的默认视图"（每个 result_group × step × field × instance，分量取默认模长/
Mises；静力 step 的第 0 帧通常是加载前的初始状态，没有预算价值）：

  1. 模型加载完成（core.state 的 on_render_ready_loaded 回调，覆盖启动预载
     与首次访问按需加载）；
  2. L3 poll 线程发现常驻模型的 result_files 出现新 (group, step, field)
     （覆盖 job_runner 在另一进程解析完新 result_group 的场景）；
  3. 手动 POST /api/odb/{odb_id}/results/warmup。
  4. 导入即预计算：poll 发现本次运行期间新导入完成（几何 ready）或新结果组
     ready 的项目，即使没人打开也主动加载并预热（run_pending）。

让路与去重：
  - 解析（job_runner）是 CPU/磁盘大户，有任何解析任务在排队/运行时（含基础几何
    ready 之后后台继续跑的 L2 高级索引阶段），自动触发的预热一律推迟（记入待办，解析全部结束后由 poll 补做）；预热进行中遇到解析
    开始也会在下一个组合处中止。手动 POST 不受限制。
  - 多 worker / 多进程：同一工作区用文件锁 l3_cache/.warmup.lock 保证同一时刻
    只有一个进程在预热；预热完成写 l3_cache/.warmup_done（结果文件签名），其他
    进程看到签名一致就不再为预热去加载模型。

另外先预算只依赖模型几何的数据：按属性/截面/单元类型等分类着色（color-code
各 scheme，elset 除外）的面分类编码、单元边线（element-mesh-edges）响应包。

预热直接调 compute_scalar_range / frame_scalars，算完自然写入内存 + 磁盘
缓存（见 result_service 的缓存层）；U 场再顺带预热变形家族的
deform-suggest-scale 统计与各 instance 顶点位移向量（同为最后一帧）。已缓存的组合命中后立即返回，所以预热
是幂等的，重复触发只补算新增的字段。整个过程单线程串行、daemon 线程执行，
失败只记日志、不影响正常请求。
"""
import hashlib
import json
import logging
import os
import sys
import threading
import time
from typing import Callable, Dict, Optional, Set

from ..core.state import OdbRegistry, registry as _global_registry
from ..infra.manifest_repo import ManifestRepo
from . import cache_store
from .color_service import warm_color_codes
from .mesh_edge_service import warm_element_mesh_edges
from .result_service import (
    compute_scalar_range,
    frame_colors,
    deform_scale_stats,
    frame_scalars,
    frame_vertex_displacements,
)

logger = logging.getLogger(__name__)

_active: Set[str] = set()
_lock = threading.Lock()
# 上次预热时看到的 (result_group, step, field) 集合；poll 检测新增用。
_warmed_targets: Dict[str, frozenset] = {}
# 因解析繁忙被推迟、或新导入待预热的模型 id（poll 空闲时处理）
_pending: Set[str] = set()
# poll 观察到的项目状态签名；变化即视为"新导入/新结果组 ready"
_seen_sig: Dict[str, tuple] = {}
_first_poll_done = False

_LOCK_NAME = ".warmup.lock"
_DONE_NAME = ".warmup_done"

# 解析繁忙判定（main 注入，读 registry.db）；未注入视为不忙（测试 / dev 模式）
_busy_fn: Optional[Callable[[], bool]] = None
_busy_cache = [0.0, False]
_BUSY_TTL = 3.0


def set_busy_check(fn: Optional[Callable[[], bool]]) -> None:
    global _busy_fn
    _busy_fn = fn
    _busy_cache[0] = 0.0


def parse_busy() -> bool:
    """是否有解析任务在排队/运行（3 秒内缓存结果，避免频繁查库）。"""
    if _busy_fn is None:
        return False
    now = time.time()
    if now - _busy_cache[0] < _BUSY_TTL:
        return _busy_cache[1]
    try:
        busy = bool(_busy_fn())
    except Exception:
        busy = False
    _busy_cache[0], _busy_cache[1] = now, busy
    return busy


_ADVANCED_STALE_S = 12 * 3600


def advanced_busy(workspace: str) -> bool:
    """L2 高级索引（--phase advanced：邻接域、边线、Octree）是否还在排队/运行。

    基础几何 ready 后项目就对外可用，高级阶段在后台继续跑（l2/advanced_status.json）；
    这期间预热一来抢资源，二来云图单元平均 / 按截面着色依赖高级数据，提前算等于白算。
    超过 12 小时没更新的 pending/running 视为进程中途死掉的残留，不再阻塞预热。
    """
    path = os.path.join(workspace, "l2", "advanced_status.json")
    try:
        st = os.stat(path)
        if time.time() - st.st_mtime > _ADVANCED_STALE_S:
            return False
        with open(path, "r", encoding="utf-8") as f:
            status = str(json.load(f).get("status") or "")
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return status in ("pending", "running")


# ── 跨进程文件锁 ────────────────────────────────────────────────────────────

class _WarmLock:
    """l3_cache/.warmup.lock 非阻塞排他锁（Linux flock / Windows msvcrt）。

    进程退出时操作系统自动释放，不会因崩溃留下死锁。
    """

    def __init__(self, workspace: str, strict: bool = False):
        self.path = os.path.join(cache_store.cache_root(workspace), _LOCK_NAME)
        self.fd = None
        self.strict = strict

    def acquire(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            if self.strict:
                raise
            return True          # 建不了锁文件（只读盘等）就不加锁，照常预热
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self.fd)
            self.fd = None


def _targets_signature(workspace: str) -> str:
    """result_files 全部条目 + 对应结果文件 (mtime,size) 的摘要；结果变了签名就变。"""
    rows = []
    try:
        with ManifestRepo(workspace)._get_conn() as conn:
            try:
                q = conn.execute("SELECT result_group, step_name, field_name, file_path "
                                 "FROM result_files").fetchall()
                rows = [(r[0], r[1], r[2], r[3]) for r in q]
            except Exception:
                q = conn.execute("SELECT step_name, field_name, file_path "
                                 "FROM result_files").fetchall()
                rows = [(None, r[0], r[1], r[2]) for r in q]
    except Exception:
        return ""
    items = []
    for rg, step, field, fp in rows:
        path = fp if (fp and os.path.isabs(fp)) else os.path.join(workspace, fp or "")
        items.append([rg, step, field, list(cache_store.file_sig(path) or ())])
    items.sort(key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return hashlib.sha1(json.dumps(items, default=str).encode()).hexdigest()


def _done_path(workspace: str) -> str:
    return os.path.join(cache_store.cache_root(workspace), _DONE_NAME)


def _read_done(workspace: str) -> str:
    try:
        with open(_done_path(workspace), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_done(workspace: str, sig: str) -> None:
    if not sig:
        return
    p = _done_path(workspace)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(sig)
        os.replace(tmp, p)
    except OSError:
        pass


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


def _last_frame(manifest: ManifestRepo, step: str, result_group) -> int:
    """step 的最后一帧：优先 frames 表的最大 frame_idx，其次 steps.num_frames-1，都没有取 0。"""
    try:
        frames = manifest.get_frames(step, result_group)
        idxs = [int(r["frame_idx"]) for r in frames if r["frame_idx"] is not None]
        if idxs:
            return max(idxs)
    except Exception:
        pass
    info = manifest.get_step_info(step, result_group)
    try:
        n = int(info.get("num_frames")) if info else 0
    except (TypeError, ValueError):
        n = 0
    return max(n - 1, 0)


class _Yield(Exception):
    """预热途中解析任务开始了 → 中止让路。"""


def warm_odb_sync(odb_id: str, reason: str = "",
                  reg: Optional[OdbRegistry] = None,
                  yield_to_parse: bool = False) -> dict:
    """同步预热一个模型各 step 最后一帧的默认视图。未加载的模型会先触发加载。

    yield_to_parse=True 时，每个组合开始前检查解析是否繁忙，繁忙则中止并返回
    status="yielded"（不记录 _warmed_targets，poll 稍后补做；已算的部分在缓存里）。
    """
    reg = reg if reg is not None else _global_registry
    t0 = time.time()
    idx = reg.get(odb_id)
    if idx is None or not idx.is_render_ready:
        return {"odb_id": odb_id, "status": "not_ready"}

    # 模型几何类（与结果无关）：着色分类编码 + 单元边线
    try:
        n_color = warm_color_codes(idx)
    except Exception:
        logger.exception("Warmup[%s]: color-code warmup failed", odb_id)
        n_color = 0
    try:
        n_edges = warm_element_mesh_edges(idx)
    except Exception:
        logger.exception("Warmup[%s]: element-mesh-edges warmup failed", odb_id)
        n_edges = 0

    manifest = ManifestRepo(idx.workspace)
    targets = _list_result_targets(manifest)
    instances = list(idx.source_node_rows.keys())
    last_frames: Dict[tuple, int] = {}
    for result_group, step, _field in targets:
        if (result_group, step) not in last_frames:
            last_frames[(result_group, step)] = _last_frame(manifest, step, result_group)

    def _check():
        if yield_to_parse and parse_busy():
            raise _Yield()

    try:
        n_ok, n_skip, n_deform = _warm_targets(
            reg, odb_id, targets, instances, last_frames, _check)
    except _Yield:
        logger.info("Warmup[%s]: parse task started, yielding (%.1fs)",
                    odb_id, time.time() - t0)
        return {"odb_id": odb_id, "status": "yielded"}
    _warmed_targets[odb_id] = frozenset(targets)

    dt = time.time() - t0
    logger.info("Warmup[%s]%s: %d color-code, %d mesh-edges, %d combo(s) warmed, "
                "%d skipped, %d deform, %.1fs", odb_id, f" ({reason})" if reason else "",
                n_color, n_edges, n_ok, n_skip, n_deform, dt)
    return {"odb_id": odb_id, "status": "done",
            "color_warmed": n_color, "mesh_edges_warmed": n_edges,
            "warmed": n_ok, "skipped": n_skip,
            "deform_warmed": n_deform, "seconds": round(dt, 1)}


def _warm_targets(reg, odb_id, targets, instances, last_frames, check):
    n_ok = n_skip = 0
    for result_group, step, field in targets:
        check()
        frame = last_frames[(result_group, step)]
        for instance in instances:
            try:
                compute_scalar_range(
                    registry=reg, odb_id=odb_id, instance=instance,
                    step=step, field=field, frame_idx=frame,
                    result_group=result_group,
                )
                frame_scalars(
                    registry=reg, odb_id=odb_id, instance=instance,
                    step=step, field=field, frame_idx=frame,
                    result_group=result_group,
                )
                n_ok += 1
            except Exception:
                # 该 instance 没有这个字段/位置数据是常态，跳过即可
                n_skip += 1
                continue
            try:
                # 旧接口 frame-colors（前端用哪个未知，两个都覆盖）：路由默认分量 U1
                frame_colors(
                    registry=reg, odb_id=odb_id, instance=instance,
                    step=step, field=field, frame_idx=frame, component="U1",
                    result_group=result_group,
                )
            except Exception:
                pass
    # 变形家族顺带预热（U 场最后一帧）：deform-suggest-scale 统计 + 每 instance
    # 顶点位移向量。deformed-positions 的整包按 scale 进缓存键、前端实际 scale
    # 不可预知 → 不预热，留给首次请求填。
    n_deform = 0
    for result_group, step, field in targets:
        if field != "U":
            continue
        check()
        frame = last_frames[(result_group, step)]
        try:
            deform_scale_stats(reg, odb_id, step, frame, result_group)
            n_deform += 1
        except Exception:
            pass
        for instance in instances:
            try:
                frame_vertex_displacements(
                    registry=reg, odb_id=odb_id, instance=instance,
                    step=step, frame_idx=frame, result_group=result_group,
                )
                n_deform += 1
            except Exception:
                # 该 instance 无 vtx_node_row / 无 U 数据是常态，跳过
                pass
    return n_ok, n_skip, n_deform


def warm_odb_async(odb_id: str, reason: str = "",
                   reg: Optional[OdbRegistry] = None,
                   force: bool = False) -> str:
    """后台线程预热；同一模型同时只跑一个。

    返回 started / already_running / deferred（解析繁忙，记入待办）。
    force=True（手动接口）不看解析是否繁忙；自动触发的预热在解析时让路。
    跨进程用文件锁去重：别的进程正在预热同一工作区时本次直接放弃。
    """
    reg = reg if reg is not None else _global_registry
    if not force and parse_busy():
        _pending.add(odb_id)
        logger.info("Warmup[%s]: parse busy, deferred (%s)", odb_id, reason)
        return "deferred"
    with _lock:
        if odb_id in _active:
            return "already_running"
        _active.add(odb_id)

    def _run():
        lock = None
        try:
            idx = reg.peek(odb_id) or reg.get(odb_id)
            if idx is None:
                return
            lock = _WarmLock(idx.workspace)
            if not lock.acquire():
                lock = None
                logger.info("Warmup[%s]: another process is warming, skip", odb_id)
                return
            sig = _targets_signature(idx.workspace)
            rep = warm_odb_sync(odb_id, reason=reason, reg=reg,
                                yield_to_parse=not force)
            if rep.get("status") == "done":
                _write_done(idx.workspace, sig)
            elif rep.get("status") == "yielded":
                _pending.add(odb_id)
        except Exception:
            logger.exception("Warmup[%s] failed", odb_id)
        finally:
            if lock is not None:
                lock.release()
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


def note_project_state(project_id: str, state: tuple) -> None:
    """poll 线程用：记录项目状态签名（几何状态 + 各结果组状态）。

    首轮 poll 只记基线（服务启动前就有的老项目不主动加载）；之后签名变化且
    项目整体就绪（几何 ready、没有排队/运行中的结果组）→ 记入待预热。
    首轮之后才第一次出现的项目同理（导入很快、两次 poll 之间就做完的情况）。
    """
    prev = _seen_sig.get(project_id)
    _seen_sig[project_id] = state
    if not _first_poll_done or prev == state:
        return
    geom_status, groups = state
    if geom_status != "ready":
        return
    if any(st in ("pending", "running") for _rg, st in groups):
        return
    _pending.add(project_id)


def finish_poll_round() -> None:
    global _first_poll_done
    _first_poll_done = True


def run_pending(reg: Optional[OdbRegistry] = None) -> None:
    """poll 线程用：解析空闲时处理待预热模型（必要时先加载进内存）。

    加载前先看 .warmup_done：别的进程已按当前结果签名预热过就不加载，省内存。
    """
    reg = reg if reg is not None else _global_registry
    if not _pending or parse_busy():
        return
    for odb_id in list(_pending):
        _pending.discard(odb_id)
        try:
            idx = reg.peek(odb_id)
            if idx is None:
                ws = reg.workspace_of(odb_id)
                if ws and _read_done(ws) and _read_done(ws) == _targets_signature(ws):
                    continue        # 已由其他进程预热（磁盘缓存齐全），不必加载
            warm_odb_async(odb_id, reason="imported", reg=reg)
        except Exception:
            logger.exception("Warmup[%s]: pending warmup failed", odb_id)
