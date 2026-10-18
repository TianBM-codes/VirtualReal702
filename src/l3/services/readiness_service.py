"""解析流水线的最终屏障：不依赖对外 ready，不使用自动预热的让路队列。"""
import json
import os
import time

from ..core.state import ModelIndex, OdbRegistry
from ..infra.manifest_repo import ManifestRepo
from . import cache_store
from .color_service import warm_color_codes
from .mesh_edge_service import warm_element_mesh_edges
from .result_service import (
    compute_scalar_range, frame_scalars, frame_colors,
    deform_scale_stats, frame_vertex_displacements,
)
from .warmup_service import _WarmLock, _last_frame


class PreparationError(RuntimeError):
    pass


def prepare_workspace(odb_id: str, workspace: str, result_group=None,
                      *, group_only: bool = False) -> None:
    """成功返回后才允许提交 ready；仅预计算有结果块的实例，不吞掉实际错误。

    每 step 最后一帧：默认标量云图与范围、frame-colors 默认 U1、U 位移和
    变形比例；另含几何分类与单元边线。与 scale 等有关的任意请求不在承诺范围。
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
                raise PreparationError("等待首屏缓存锁超时，请重试")
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
        with manifest._get_conn() as conn:
            targets = conn.execute(
                "SELECT result_group, step_name, field_name, file_path FROM result_files"
                + (" WHERE result_group IS ?" if group_only else ""),
                (result_group,) if group_only else (),
            ).fetchall()
            blocks = conn.execute(
                "SELECT DISTINCT result_group, step_name, field_name, instance_name FROM result_blocks"
            ).fetchall()
            instances = {r[0] for r in conn.execute("SELECT instance_name FROM instances")}
        if not instances:
            raise PreparationError("模型没有实例")
        if instances - set(idx.render_source_elem_row):
            raise PreparationError("部分实例缺少渲染几何：" + str(sorted(instances - set(idx.render_source_elem_row))))
        if instances - set(idx.averaging_data) or instances - set(idx.octree):
            raise PreparationError("部分实例的高级索引缺失或加载失败")
        if group_only and not targets:
            raise PreparationError(f"结果组 {result_group} 没有可用结果文件")
        block_instances = {}
        for rg, step, field, inst in blocks:
            block_instances.setdefault((rg, step, field), set()).add(inst)

        with cache_store.require_persistent_cache():
            warm_color_codes(idx, strict=True)
            warm_element_mesh_edges(idx, strict=True)
            for rg, step, field, file_path in targets:
                context = f"{rg}/{step}/{field}"
                path = file_path if os.path.isabs(file_path) else os.path.join(workspace, file_path)
                if not os.path.isfile(path):
                    raise PreparationError(f"{context} 结果文件不存在：{path}")
                available = block_instances.get((rg, step, field), set())
                if not available:
                    raise PreparationError(f"{context} 缺少结果块元数据")
                unknown = available - instances
                if unknown:
                    raise PreparationError(f"{context} 的实例不属于模型：{sorted(unknown)}")
                frame = _last_frame(manifest, step, rg)
                for instance in sorted(available):
                    try:
                        # 梁/质量点实例没有表面三角形，云图缓存不适用，U 位移仍准备。
                        if len(idx.render_source_elem_row[instance]):
                            compute_scalar_range(reg, odb_id, instance, step, field, frame, result_group=rg)
                            frame_scalars(reg, odb_id, instance, step, field, frame, result_group=rg)
                            frame_colors(reg, odb_id, instance, step, field, frame,
                                         component="U1", result_group=rg)
                        if field == "U":
                            frame_vertex_displacements(reg, odb_id, instance, step, frame, result_group=rg)
                    except Exception as exc:
                        raise PreparationError(f"{context}/{instance} 首屏计算失败：{exc}") from exc
                if field == "U":
                    deform_scale_stats(reg, odb_id, step, frame, rg)
    except Exception as exc:
        raise PreparationError(f"[首屏准备失败] {exc}") from exc
    finally:
        if acquired:
            lock.release()


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
    prepare_workspace(entry_id, workspace, result_group, group_only=kind == "group")
