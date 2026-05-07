"""
DSA 灵敏度场合并服务。

将 d_U_T1 … d_U_TN 等离散字段合并为全局元素场
d_{node_label}_{Uc}_T，写入 workspace 供 frame-scalars 接口直接使用。

不访问 MySQL —— parameter_rows 由调用方查好后传入。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Dict, List, Optional

import h5py
import numpy as np

from ..infra.manifest_repo import ManifestRepo
from .external_result_writer import ExternalResultWriter

_DSA_TOKEN_RE = re.compile(r"^([A-Z_][A-Z0-9_]*?)(\d+)$", re.IGNORECASE)


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _manifest_conn(workspace_abs: str) -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(workspace_abs, "manifest.db"), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _field_param_index(field_prefix: str, field_name: str) -> Optional[int]:
    """从字段名提取参数序号（1-based）。d_U_T3 → 3。提取失败返回 None。"""
    if not field_name.startswith(field_prefix):
        return None
    suffix = field_name[len(field_prefix):]
    m = _DSA_TOKEN_RE.fullmatch(suffix)
    if m:
        return int(m.group(2))
    # 兼容纯数字后缀，如 field_prefix="d_U_T"，field_name="d_U_T3"
    prefix_tail = field_prefix.rstrip("_").rsplit("_", 1)[-1]
    combined = prefix_tail + suffix
    m2 = _DSA_TOKEN_RE.fullmatch(combined)
    if m2:
        return int(m2.group(2))
    return None


def _read_dsa_field(workspace_abs: str, step: str, field_name: str,
                    frame: int, source_result_group: Optional[str]):
    """
    从 workspace H5 读取一个 DSA 字段在指定帧的数据。

    返回:
        components: List[str]  — 分量名列表，如 ['U1','U2','U3']
        blocks: List[dict]     — 每个 instance 一条：
            {instance_name, labels: int32 array, data: float32 array [R, C]}
    """
    conn = _manifest_conn(workspace_abs)
    try:
        rg_col = "result_group"
        if source_result_group is None:
            rg_clause = f"{rg_col} IS NULL"
            rg_params: list = []
        else:
            rg_clause = f"{rg_col} = ?"
            rg_params = [source_result_group]

        rf = conn.execute(
            f"SELECT file_path, components FROM result_files"
            f" WHERE step_name=? AND field_name=? AND {rg_clause}",
            [step, field_name] + rg_params,
        ).fetchone()
        if rf is None:
            return None, []

        components_raw = rf["file_path"] and rf["components"]
        try:
            components = json.loads(rf["components"]) if rf["components"] else []
        except Exception:
            components = []

        h5_file = os.path.join(workspace_abs, str(rf["file_path"]))
        if not os.path.exists(h5_file):
            return components, []

        block_rows = conn.execute(
            f"SELECT instance_name, h5_path FROM result_blocks"
            f" WHERE step_name=? AND field_name=? AND {rg_clause}"
            f" ORDER BY instance_name",
            [step, field_name] + rg_params,
        ).fetchall()

        blocks = []
        with h5py.File(h5_file, "r") as hf:
            for row in block_rows:
                grp_path = str(row["h5_path"])
                if grp_path not in hf:
                    continue
                grp = hf[grp_path]
                if "labels" not in grp or "data" not in grp:
                    continue
                labels = grp["labels"][:].astype(np.int32)
                raw = grp["data"]
                # shape: [num_frames, R, C] or [R, C]
                if raw.ndim == 3:
                    data = raw[int(frame)].astype(np.float32)
                elif raw.ndim == 2:
                    data = raw[:].astype(np.float32)
                else:
                    continue
                blocks.append({
                    "instance_name": str(row["instance_name"]),
                    "labels": labels,
                    "data": data,  # [R, C]
                })
        return components, blocks
    finally:
        conn.close()


def _resolve_element_labels(
    repo: ManifestRepo,
    param_row: dict,
) -> Dict[str, List[int]]:
    """
    从 manifest.db element_sets + instances 解析参数对应的单元 labels。

    返回 {instance_name: [label, ...]}。
    """
    result: Dict[str, List[int]] = {}

    element_label = param_row.get("element_label")
    if element_label is not None:
        instance_name = param_row.get("instance_name") or ""
        if instance_name:
            result[instance_name] = [int(element_label)]
            return result
        # instance_name is NULL in DB: resolve via part_name, or fall back to all instances.
        # An element label is scoped to an instance, so we must not use "" as the key.
        part_name = param_row.get("part_name")
        if part_name:
            for inst in repo.get_instances_by_part_name(part_name):
                result[str(inst)] = [int(element_label)]
        else:
            for inst_row in repo.list_instances():
                result[str(inst_row["instance_name"])] = [int(element_label)]
        return result

    set_name = str(param_row.get("set_name") or "")
    set_scope = str(param_row.get("set_scope") or "PART").upper()
    instance_name = param_row.get("instance_name")
    part_name = param_row.get("part_name")

    if set_scope == "ASSEMBLY" and instance_name:
        labels_arr = repo.get_element_set_labels(set_name, instance_name)
        if labels_arr is not None and len(labels_arr):
            result[str(instance_name)] = labels_arr.tolist()
        return result

    # PART scope：按 part_name 展开所有 instances
    candidate_instances = (
        repo.get_instances_by_part_name(part_name)
        if part_name
        else ([str(instance_name)] if instance_name else [])
    )
    for inst in candidate_instances:
        labels_arr = repo.get_element_set_labels(set_name, inst)
        if labels_arr is not None and len(labels_arr):
            result[str(inst)] = labels_arr.tolist()
    return result


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def merge_dsa_fields(
    *,
    workspace: str,
    step: str,
    frame: int,
    field_prefix: str,
    instances: List[str],
    parameter_rows: List[dict],
    result_group: str,
    source_result_group: Optional[str] = None,
) -> List[dict]:
    """
    将 d_U_T1…TN 合并为全局元素场，写入 workspace。

    参数:
        workspace           workspace 根目录（含 manifest.db）
        step                分析步名
        frame               帧索引（0-based）
        field_prefix        DSA 字段前缀，如 "d_U_T"
        instances           参与合并的 instance 列表（空表示全部）
        parameter_rows      MySQL 查出的参数记录列表，按序号顺序排列
                            每条含: parameter_name, set_name, set_scope,
                                    part_name, instance_name, element_label
        result_group        写入 workspace 时使用的 result_group 名
        source_result_group 读取原始 d_U_Tk 时的 result_group（None 表示旧格式）

    返回已写入字段列表:
        [{ field_name, response_node_label, component, instance_count,
           element_count }, ...]
    """
    workspace_abs = os.path.abspath(workspace)
    repo = ManifestRepo(workspace_abs)
    writer = ExternalResultWriter(workspace_abs, result_group)

    # ── 1. 发现并排序所有 d_U_Tk 字段 ──────────────────────────────────────
    conn = _manifest_conn(workspace_abs)
    try:
        rg_clause = (
            "result_group IS NULL"
            if source_result_group is None
            else "result_group = ?"
        )
        rg_params = [] if source_result_group is None else [source_result_group]

        field_rows = conn.execute(
            f"SELECT DISTINCT field_name FROM result_files"
            f" WHERE step_name=? AND {rg_clause}",
            [step] + rg_params,
        ).fetchall()
    finally:
        conn.close()

    dsa_field_names = []
    for row in field_rows:
        fn = str(row["field_name"])
        idx = _field_param_index(field_prefix, fn)
        if idx is not None:
            dsa_field_names.append((idx, fn))

    if not dsa_field_names:
        return []

    dsa_field_names.sort(key=lambda x: x[0])  # 按参数序号升序

    # ── 2. 读取每个字段的响应节点数据 ────────────────────────────────────────
    # field_data: {field_name: (components, blocks)}
    field_data: Dict[str, tuple] = {}
    components: List[str] = []

    for param_idx, field_name in dsa_field_names:
        comps, blocks = _read_dsa_field(
            workspace_abs, step, field_name, frame, source_result_group
        )
        if not blocks:
            continue
        field_data[field_name] = (comps, blocks)
        if not components and comps:
            components = comps

    if not field_data:
        return []

    if not components:
        components = ["C1", "C2", "C3"]

    # ── 3. 建立参数序号 → element labels 映射 ────────────────────────────────
    # param_elements: {field_name: {instance_name: [label, ...]}}
    param_elements: Dict[str, Dict[str, List[int]]] = {}
    for param_idx, field_name in dsa_field_names:
        if field_name not in field_data:
            continue
        row_idx = param_idx - 1  # 1-based → 0-based
        if row_idx < 0 or row_idx >= len(parameter_rows):
            continue
        param_row = parameter_rows[row_idx]
        param_elements[field_name] = _resolve_element_labels(repo, param_row)

    # ── 4. 收集所有响应节点 labels（跨所有字段、所有 instance 块） ────────────
    # response_labels: 有序去重列表（任一字段的 blocks 结构相同）
    first_blocks = next(iter(field_data.values()))[1]
    all_response_labels: List[int] = []
    seen: set = set()
    for blk in first_blocks:
        for lbl in blk["labels"].tolist():
            if lbl not in seen:
                seen.add(lbl)
                all_response_labels.append(int(lbl))

    # ── 5. 确定参与的 instance 列表 ──────────────────────────────────────────
    if instances:
        target_instances = [str(i) for i in instances]
    else:
        all_inst = set()
        for inst_map in param_elements.values():
            all_inst.update(inst_map.keys())
        target_instances = sorted(all_inst)

    # ── 6. 按 (响应节点, 分量) 构建并写入合并场 ──────────────────────────────
    written: List[dict] = []
    num_components = len(components)

    for node_label in all_response_labels:
        for comp_idx, comp_name in enumerate(components):
            merged_field_name = f"d_{node_label}_{comp_name}_T"
            writer.clear_field(step, merged_field_name)

            total_elements = 0
            inst_count = 0

            for inst_name in target_instances:
                # 对此 instance 收集 {label → scalar}
                label_value_map: Dict[int, float] = {}

                for _, field_name in dsa_field_names:
                    if field_name not in field_data or field_name not in param_elements:
                        continue
                    inst_labels = param_elements[field_name].get(inst_name)
                    if not inst_labels:
                        continue

                    # 从该字段找到 node_label 对应的值
                    comps_list, blocks = field_data[field_name]
                    scalar: Optional[float] = None
                    for blk in blocks:
                        labels_arr = blk["labels"]
                        pos = int(np.searchsorted(labels_arr, node_label))
                        if pos < len(labels_arr) and labels_arr[pos] == node_label:
                            data_row = blk["data"][pos]  # shape [C]
                            c = comp_idx if comp_idx < len(data_row) else 0
                            scalar = float(data_row[c])
                            break

                    if scalar is None:
                        continue

                    for lbl in inst_labels:
                        label_value_map[int(lbl)] = scalar

                if not label_value_map:
                    continue

                frame_data = [
                    {"label": lbl, "values": [val]}
                    for lbl, val in label_value_map.items()
                ]
                frames_payload = [{
                    "frame_idx": 0,
                    "frame_value": float(frame),
                    "data": frame_data,
                }]
                writer.write_element(
                    instance=inst_name,
                    step=step,
                    field=merged_field_name,
                    components=["value"],
                    frames=frames_payload,
                )
                total_elements += len(label_value_map)
                inst_count += 1

            if inst_count > 0:
                written.append({
                    "field_name": merged_field_name,
                    "response_node_label": node_label,
                    "component": comp_name,
                    "component_idx": comp_idx,
                    "instance_count": inst_count,
                    "element_count": total_elements,
                })

    return written
