"""
ODB Model — 从 L1 HDF5 加载几何/材料/集合数据，返回结构化 model 对象。

用法:
    from src.l1.odb_model import load_odb_model

    model = load_odb_model("/data/<odb_id>")

    for inst_name, inst in model.instances.items():
        print(inst_name, inst.node_labels.shape, inst.node_coords.shape)
        for etype, eg in inst.element_groups.items():
            print("  ", etype, eg.elem_labels.shape, eg.conn_labels.shape)

前提: l1_pack.py 已成功完成（l1/ 目录和 manifest.db 已存在）。
不需要 Abaqus，纯 Python 3 + h5py + numpy。
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import h5py
import numpy as np


# ---------------------------------------------------------------------------
# Model dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OdbSection:
    name: str
    section_type: str          # "SOLID" | "SHELL" | "BEAM" | ""
    elset_name: str
    material_name: str
    thickness: Optional[float] = None   # shell only


@dataclass
class OdbMaterial:
    name: str
    material_type: str                        # e.g. "ISOTROPIC"
    elastic_table: Optional[np.ndarray] = None  # [rows, cols] float64; cols: E, nu [, T]


@dataclass
class OdbElementGroup:
    """所有同类型单元的数据块。"""
    etype: str                  # e.g. "C3D8R"
    elem_labels: np.ndarray     # [M] int32  — 单元标签（Abaqus label）
    conn_labels: np.ndarray     # [M, nc] int32 — 角节点标签（已转换，非行号）


@dataclass
class OdbInstance:
    name: str
    part_name: str
    transform: np.ndarray               # [4, 3] float64（与 assembly.h5 一致）
    node_labels: np.ndarray             # [N] int32
    node_coords: np.ndarray             # [N, 3] float64
    element_groups: Dict[str, OdbElementGroup]   # etype → group
    sections: List[OdbSection]
    materials: Dict[str, OdbMaterial]
    node_sets: Dict[str, np.ndarray]    # set_name → node label array
    element_sets: Dict[str, np.ndarray] # set_name → elem label array


@dataclass
class OdbModel:
    workspace: str
    instances: Dict[str, OdbInstance] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_odb_model(workspace: str) -> OdbModel:
    """
    从 workspace 读取 L1 HDF5 文件，返回 OdbModel。

    Args:
        workspace: ODB workspace 根目录（含 manifest.db 和 l1/ 子目录）。

    Returns:
        OdbModel，其中每个 instance 包含节点、单元、截面、材料、集合数据。

    Raises:
        FileNotFoundError: manifest.db 或 assembly.h5 不存在。
        RuntimeError: workspace 尚未完成 L1 处理（manifest.db 缺少 instances 表）。
    """
    manifest_path = os.path.join(workspace, "manifest.db")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"manifest.db not found in {workspace!r}")

    assembly_path = os.path.join(workspace, "l1", "assembly.h5")
    if not os.path.exists(assembly_path):
        raise FileNotFoundError(f"assembly.h5 not found in {workspace!r}")

    model = OdbModel(workspace=workspace)

    # ── 读取 manifest.db 取实例列表及 geom_path ─────────────────────────────
    with sqlite3.connect(manifest_path, timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT instance_name, part_name, geom_path FROM instances"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"manifest.db in {workspace!r} appears incomplete: {exc}"
            ) from exc

    if not rows:
        return model

    # ── 读取 assembly.h5 取 transform ───────────────────────────────────────
    transforms: Dict[str, np.ndarray] = {}
    with h5py.File(assembly_path, "r") as af:
        inst_grp = af.get("instances", {})
        for iname in inst_grp:
            ds = inst_grp[iname].get("transform")
            transforms[iname] = ds[:] if ds is not None else np.eye(4, 3)

    # ── 逐 instance 加载几何 HDF5 ────────────────────────────────────────────
    for row in rows:
        inst_name = row["instance_name"]
        part_name = row["part_name"] or ""
        geom_rel  = row["geom_path"] or ""

        geom_abs = os.path.join(workspace, geom_rel) if geom_rel else ""
        if not geom_abs or not os.path.exists(geom_abs):
            continue

        transform = transforms.get(inst_name, np.eye(4, 3))

        with h5py.File(geom_abs, "r") as gf:
            node_labels, node_coords = _load_nodes(gf)
            element_groups           = _load_elements(gf, node_labels)
            sections                 = _load_sections(gf)
            materials                = _load_materials(gf)
            node_sets                = _load_node_sets(gf, node_labels)
            element_sets             = _load_element_sets(gf, element_groups)

        model.instances[inst_name] = OdbInstance(
            name=inst_name,
            part_name=part_name,
            transform=transform,
            node_labels=node_labels,
            node_coords=node_coords,
            element_groups=element_groups,
            sections=sections,
            materials=materials,
            node_sets=node_sets,
            element_sets=element_sets,
        )

    return model


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_nodes(gf: h5py.File):
    labels = gf["nodes/labels"][:].astype(np.int32)
    coords = gf["nodes/coords"][:].astype(np.float64)
    return labels, coords


def _load_elements(
    gf: h5py.File, node_labels: np.ndarray
) -> Dict[str, OdbElementGroup]:
    groups: Dict[str, OdbElementGroup] = {}
    elem_grp = gf.get("elements")
    if elem_grp is None:
        return groups

    for etype_safe in elem_grp:
        eg = elem_grp[etype_safe]
        if "labels" not in eg or "conn" not in eg:
            continue
        elem_labels = eg["labels"][:].astype(np.int32)
        conn_rows   = eg["conn"][:].astype(np.int32)   # row indices into node_labels
        # 转成节点标签（Abaqus label），方便外部代码直接使用
        conn_labels = node_labels[conn_rows]
        groups[etype_safe] = OdbElementGroup(
            etype=etype_safe,
            elem_labels=elem_labels,
            conn_labels=conn_labels,
        )
    return groups


def _load_sections(gf: h5py.File) -> List[OdbSection]:
    sections: List[OdbSection] = []
    sec_grp = gf.get("sections")
    if sec_grp is None:
        return sections
    for sname in sec_grp:
        sg = sec_grp[sname]
        thickness_raw = sg.attrs.get("thickness", float("nan"))
        thickness = None if (thickness_raw != thickness_raw) else float(thickness_raw)
        sections.append(OdbSection(
            name=sname,
            section_type=str(sg.attrs.get("type", "")),
            elset_name=str(sg.attrs.get("element_set", "")),
            material_name=str(sg.attrs.get("material_name", "")),
            thickness=thickness,
        ))
    return sections


def _load_materials(gf: h5py.File) -> Dict[str, OdbMaterial]:
    materials: Dict[str, OdbMaterial] = {}
    mat_grp = gf.get("materials")
    if mat_grp is None:
        return materials
    for mname in mat_grp:
        mg = mat_grp[mname]
        mat_type = str(mg.attrs.get("type", ""))
        elastic_table = None
        if "elastic_table" in mg:
            elastic_table = mg["elastic_table"][:].astype(np.float64)
        materials[mname] = OdbMaterial(
            name=mname,
            material_type=mat_type,
            elastic_table=elastic_table,
        )
    return materials


def _load_node_sets(
    gf: h5py.File, node_labels: np.ndarray
) -> Dict[str, np.ndarray]:
    node_sets: Dict[str, np.ndarray] = {}
    nsets_grp = gf.get("instance_sets/node_sets")
    if nsets_grp is None:
        return node_sets
    for sname in nsets_grp:
        # abaqus_dump.py 写入的是 Abaqus 节点标签，直接使用
        node_sets[sname] = nsets_grp[sname][:].astype(np.int32)
    return node_sets


def _load_element_sets(
    gf: h5py.File, element_groups: Dict[str, OdbElementGroup]
) -> Dict[str, np.ndarray]:
    element_sets: Dict[str, np.ndarray] = {}
    esets_grp = gf.get("instance_sets/element_sets")
    if esets_grp is None:
        return element_sets
    for sname in esets_grp:
        # abaqus_dump.py 写入的是 Abaqus 单元标签，直接使用
        element_sets[sname] = esets_grp[sname][:].astype(np.int32)
    return element_sets
