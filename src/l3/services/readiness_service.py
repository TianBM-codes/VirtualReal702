"""解析流水线的最终屏障：不依赖对外 ready，不使用自动预热的让路队列。"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from ..core.state import ModelIndex, OdbRegistry
from ..infra.manifest_repo import ManifestRepo
from . import cache_store
from .color_service import warm_color_codes
from .mesh_edge_service import warm_element_mesh_edges
from .precompute_plan import list_targets, run_plan, run_plan_parallel, worker_limit
from .warmup_service import _WarmLock


logger = logging.getLogger(__name__)


class PreparationError(RuntimeError):
    pass


def prepare_workspace(odb_id: str, workspace: str, result_group=None,
                      *, group_only: bool = False, progress=None) -> None:
    """成功返回后才允许提交 ready；仅预计算有结果块的实例，不吞掉实际错误。

    按全量计划（services/precompute_plan.py）准备每 step 全部帧、全部分量、
    smooth/flat 的云图与范围、U 位移和变形比例；另含几何分类与
    单元边线。与 scale 等有关的任意请求不在承诺范围。
    progress(message, percent) 用于把进度写进项目日志。
    """
    lock = _WarmLock(workspace, strict=True)
    acquired = False
    try:
        with open(os.path.join(workspace, "l2", "advanced_status.json"), encoding="utf-8") as f:
            advanced = json.load(f)
        if advanced.get("status") != "ready":
            raise PreparationError("高级索引尚未成功：" + str(advanced))

        # 等的是正在执行的计算，不是 ready 状态；超时明确失败，避免无限等待。
        deadline = time.monotonic() + 300
        while not lock.acquire():
            if time.monotonic() >= deadline:
                raise PreparationError("等待缓存锁超时，请重试")
            time.sleep(0.2)
        acquired = True

        idx = ModelIndex(odb_id, workspace)
        idx.load_l2_render_data()
        if not idx.is_render_ready or idx.advanced_status != "ready":
            raise PreparationError("渲染几何或高级索引加载失败")
        # 直接注入局部索引，避免触发全局加载回调和自动预热，也不把 running 对外注册。
        reg = OdbRegistry(max_loaded=1)
        reg.loaded[odb_id] = idx
        manifest = ManifestRepo(workspace)
        targets = list_targets(manifest, result_group=result_group, group_only=group_only)
        with manifest._get_conn() as conn:
            instances = {r[0] for r in conn.execute("SELECT instance_name FROM instances")}
        if not instances:
            raise PreparationError("模型没有实例")
        if instances - set(idx.render_source_elem_row):
            raise PreparationError("部分实例缺少渲染几何：" + str(sorted(instances - set(idx.render_source_elem_row))))
        if instances - set(idx.averaging_data) or instances - set(idx.octree):
            raise PreparationError("部分实例的高级索引缺失或加载失败")
        if group_only and not targets:
            raise PreparationError(f"结果组 {result_group} 没有可用结果文件")
        for t in targets:
            path = t.file_path or ""
            path = path if os.path.isabs(path) else os.path.join(workspace, path)
            if not t.file_path or not os.path.isfile(path):
                raise PreparationError(f"{t.context} 结果文件不存在：{path}")
            if not t.instances:
                raise PreparationError(f"{t.context} 缺少结果块元数据")
            unknown = set(t.instances) - instances
            if unknown:
                raise PreparationError(f"{t.context} 的实例不属于模型：{sorted(unknown)}")

        with cache_store.require_persistent_cache() as entries:
            warm_color_codes(idx, strict=True)
            warm_element_mesh_edges(idx, strict=True)
            # 合法的全 NaN 结果（如实体实例的面内主应变）由计划记为跳过，与接口返回空
            # payload 置灰一致；文件/块缺失已在上面硬校验过。
            workers = worker_limit()
            if workers > 1 and len(targets) > 1:
                # 子进程各自加载索引并计算，落盘条目并入 entries，退出本 with 时统一核验
                stats = run_plan_parallel(odb_id, workspace, idx, targets, max_workers=workers,
                                          entries=entries, progress=progress)
            else:
                stats = run_plan(reg, odb_id, idx, targets, strict=True, progress=progress)
        logger.info("prepare %s: full cache plan done %s", odb_id, stats)
    except Exception as exc:
        raise PreparationError(f"[全量缓存准备失败] {exc}") from exc
    finally:
        if acquired:
            lock.release()


def _job_log_progress(connect, entry_id):
    """把计划进度写进 job_logs（与解析日志同一张表，前端日志面板可见）；写失败不影响准备。"""
    tz_bj = timezone(timedelta(hours=8))

    def _progress(message: str, percent: int) -> None:
        try:
            with connect() as conn:
                conn.execute(
                    "INSERT INTO job_logs (odb_id, ts, level, stage, message, percent)"
                    " VALUES (?,?,?,?,?,?)",
                    (entry_id, datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S"), "info",
                     "cache_prepare", message, 95 + min(max(percent, 0), 100) * 4 // 100),
                )
        except Exception as exc:
            logger.warning("cache prepare progress log failed (%s): %s", entry_id, exc)

    return _progress


def prepare_registered(connect, data_root, entry_id, *, kind="project", result_group=None):
    """先关闭 registry 读事务，再进行耗时计算，避免占着 SQLite 写锁。"""
    with connect() as conn:
        if kind == "job":
            row = conn.execute("SELECT workspace FROM odb_jobs WHERE odb_id=?", (entry_id,)).fetchone()
        else:
            row = conn.execute("SELECT workspace, source_type FROM projects WHERE project_id=?", (entry_id,)).fetchone()
    if row is None:
        raise PreparationError(f"找不到工作区注册记录：{entry_id}")
    workspace = row["workspace"]
    if not os.path.isabs(workspace):
        workspace = os.path.join(data_root, workspace)
    if kind == "project" and row["source_type"] in ("odb", "rst"):
        with connect() as conn:
            adopted = conn.execute(
                "SELECT 1 FROM result_groups WHERE project_id=? AND result_group='default_result'",
                (entry_id,),
            ).fetchone()
        if not adopted:
            raise PreparationError("默认结果组尚未成功注册")
        with ManifestRepo(workspace)._get_conn() as conn:
            for table in ("result_files", "result_blocks", "steps", "frames"):
                if conn.execute(f"SELECT 1 FROM {table} WHERE result_group IS NULL LIMIT 1").fetchone():
                    raise PreparationError(f"默认结果组登记不完整：{table}")
    prepare_workspace(entry_id, workspace, result_group, group_only=kind == "group",
                      progress=_job_log_progress(connect, entry_id))
