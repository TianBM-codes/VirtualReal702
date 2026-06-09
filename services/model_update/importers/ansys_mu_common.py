# -*- coding: utf-8 -*-
"""
ansys_mu_common.py — Ansys（CDB / RST）→ model_update 数据库导入的共享逻辑。

CDB 与 RST 拿到的网格/材料对象不同，但归一成同一个中间结构后，写库逻辑完全一致，
抽到这里共用。写入的表与 bdf_service.import_bdf_data 对齐（材料、属性、壳厚、
修正量集合 capability、节点 octree 缓存、工程尺寸/配置），这样模型修正主线
（灵敏度 / 贝叶斯）拿到的元数据与 BDF 来源完全同构。

中间结构 model dict:
    {
      'node_labels': int[N], 'node_coords': float[N,3],
      'elem_label':  int[E], 'elem_mat': int[E], 'elem_real': int[E],
      'elem_sec':    int[E], 'elem_etype': int[E],
      'materials':   {mat_id: {'EX':, 'NUXY':, 'DENS':, ...}},
      'sections':    {sec_id: {'type':, 'thickness':}},
      'reals':       {real_id: [r1, ...]},
    }
"""
import os

import numpy as np

from db import get_connection, ensure_tables_exist, clear_fem_tables
from services.model_update.analysis.console_log_service import safe_write_console_event
from services.model_update.analysis.inp_service import (
    _SUPPORTED_CORRECTION_QUANTITIES,
    _save_octree_cache,
)
from services.model_update.analysis.project_config_service import (
    save_fem_model_dimensions, upsert_project_config,
)
import json as _json

from src.l1.ansys_etype_map import family_for_etype, model_update_family

_PART_NAME = "ANSYS_MODEL"


def _safe_float(v):
    return None if v is None else float(v)


def _shell_thickness(sec_id, real_id, sections, reals):
    """壳厚：优先 SECBLOCK，其次实常数首值。"""
    if sec_id in sections and sections[sec_id].get("thickness"):
        return float(sections[sec_id]["thickness"])
    if real_id in reals and reals[real_id]:
        return float(reals[real_id][0])
    return None


def _build_capabilities(model):
    """按 (mat, real, sec) 组合构建修正量 capability 行（E / T / RHO）。"""
    elem_label = np.asarray(model["elem_label"], dtype=np.int64)
    elem_mat   = np.asarray(model["elem_mat"], dtype=np.int64)
    elem_real  = np.asarray(model["elem_real"], dtype=np.int64)
    elem_sec   = np.asarray(model["elem_sec"], dtype=np.int64)
    elem_etype = np.asarray(model["elem_etype"], dtype=np.int64)
    materials  = model["materials"]
    sections   = model["sections"]
    reals      = model["reals"]

    combos = {}
    for i in range(len(elem_label)):
        key = (int(elem_mat[i]), int(elem_real[i]), int(elem_sec[i]))
        combos.setdefault(key, []).append((int(elem_label[i]), int(elem_etype[i])))

    rows = []
    for (mat_id, real_id, sec_id), members in sorted(combos.items()):
        labels = [m[0] for m in members]
        family = model_update_family(members[0][1]) if members else "OTHER"
        mprops = materials.get(mat_id, {})
        e_val   = _safe_float(mprops.get("EX"))
        rho_val = _safe_float(mprops.get("DENS"))
        t_val   = _shell_thickness(sec_id, real_id, sections, reals) \
            if family == "SHELL" else None
        material_name = "MID_{}".format(mat_id)
        set_name = "PROPERTY_M{}_R{}_S{}".format(mat_id, real_id, sec_id)
        section_type = sections.get(sec_id, {}).get("type") or family
        target_keys = ["PART::{}::{}".format(_PART_NAME, l) for l in labels]
        target_keys_by_label = {str(l): ["PART::{}::{}".format(_PART_NAME, l)] for l in labels}

        qvals = {"E": e_val, "T": t_val, "RHO": rho_val}
        for quantity in _SUPPORTED_CORRECTION_QUANTITIES:
            qcode = str(quantity["quantity_code"])
            cur = qvals.get(qcode)
            if cur is None:
                continue
            if qcode == "E" and family not in ("SHELL", "SOLID", "BEAM"):
                continue
            if qcode == "RHO" and family not in ("SHELL", "SOLID", "BEAM"):
                continue
            if qcode == "T" and family != "SHELL":
                continue
            element_values = {str(l): float(cur) for l in labels}
            rows.append({
                "quantity_code": qcode,
                "set_name": set_name,
                "set_type": "PROPERTY",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": _PART_NAME,
                "set_role": "PROPERTY_SET",
                "element_family": family,
                "section_type": section_type,
                "material_name": material_name,
                "member_count": len(labels),
                "supports_global": True,
                "supports_local": True,
                "current_value": float(cur),
                "extra_json": {
                    "material_id": mat_id,
                    "real_id": real_id,
                    "section_id": sec_id,
                    "element_labels": labels,
                    "target_keys": target_keys,
                    "target_keys_by_label": target_keys_by_label,
                    "element_values": element_values,
                },
            })
    return rows


def import_ansys_model(model, file_path, project_id, source, clear_before_insert=True):
    """把归一化的 Ansys model dict 写入 model_update 数据库。source ∈ {'cdb','rst'}。"""
    ensure_tables_exist()

    node_labels = np.asarray(model["node_labels"], dtype=np.int64)
    node_coords = np.asarray(model["node_coords"], dtype=np.float64).reshape(-1, 3)
    materials   = model["materials"]
    sections    = model["sections"]
    reals       = model["reals"]
    capabilities = _build_capabilities(model)

    octree_node_data = {
        "point_coords": node_coords,
        "point_labels": node_labels,
        "point_instances": np.asarray([_PART_NAME] * len(node_labels), dtype="U128"),
        "point_parts": np.asarray([_PART_NAME] * len(node_labels), dtype="U128"),
        "bbox_min": node_coords.min(axis=0),
        "bbox_max": node_coords.max(axis=0),
    }

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if clear_before_insert:
            clear_fem_tables(cursor, project_id)

        # 支持的修正量
        for item in _SUPPORTED_CORRECTION_QUANTITIES:
            cursor.execute(
                "INSERT INTO t_mt_py_fem_supported_quantity"
                " (quantity_code, quantity_name, unit, enabled, sort_no)"
                " VALUES (%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE quantity_name=VALUES(quantity_name),"
                " unit=VALUES(unit), enabled=VALUES(enabled), sort_no=VALUES(sort_no)",
                (item["quantity_code"], item["quantity_name"], item.get("unit"),
                 int(item.get("enabled", 1)), int(item.get("sort_no", 0))),
            )

        # 材料总览 + 各向同性
        for mat_id, props in sorted(materials.items()):
            mtype = "ISOTROPIC" if props.get("EX") is not None else \
                ("THERMAL" if "KXX" in props else "OTHER")
            cursor.execute(
                "INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type) VALUES (%s,%s,%s)",
                (int(mat_id), project_id, mtype))
            if props.get("EX") is not None:
                cursor.execute(
                    "INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (int(mat_id), project_id,
                     _safe_float(props.get("DENS")), _safe_float(props.get("EX")),
                     _safe_float(props.get("NUXY", props.get("PRXY"))),
                     _safe_float(props.get("DAMP"))))

        # 属性总览 + 壳属性（按 section 号）
        for sec_id, sec in sorted(sections.items()):
            sec_type = (sec.get("type") or "").upper()
            cursor.execute(
                "INSERT INTO t_mt_py_fem_property (Id, pid, Type) VALUES (%s,%s,%s)",
                (int(sec_id), project_id, sec_type or "SHELL"))
            if sec_type == "SHELL" or sec.get("thickness") is not None:
                cursor.execute(
                    "INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (int(sec_id), project_id, _safe_float(sec.get("thickness")), None, None))

        # 修正量集合 capability
        for item in capabilities:
            cursor.execute(
                "INSERT INTO t_mt_py_fem_quantity_set_capability"
                " (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name,"
                "  set_role, element_family, section_type, material_name, member_count,"
                "  supports_global, supports_local, current_value, extra_json)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE set_role=VALUES(set_role),"
                " element_family=VALUES(element_family), section_type=VALUES(section_type),"
                " material_name=VALUES(material_name), member_count=VALUES(member_count),"
                " supports_global=VALUES(supports_global), supports_local=VALUES(supports_local),"
                " current_value=VALUES(current_value), extra_json=VALUES(extra_json)",
                (int(project_id), item["quantity_code"], item["set_name"], item["set_type"],
                 item["set_scope"], item.get("instance_name"), item.get("part_name"),
                 item["set_role"], item.get("element_family"), item.get("section_type"),
                 item.get("material_name"), int(item["member_count"]),
                 1 if item.get("supports_global") else 0,
                 1 if item.get("supports_local") else 0,
                 item.get("current_value"),
                 _json.dumps(item.get("extra_json") or {}, ensure_ascii=False)))

        # 节点 octree 缓存 + 工程尺寸 + 配置
        cache_path = _save_octree_cache(
            int(project_id), os.path.abspath(file_path), octree_node_data, force_rebuild=True)
        cursor.execute(
            "INSERT INTO t_mt_py_fem_node_octree_cache"
            " (pid, source_file_path, cache_file_path, node_count, instance_count,"
            "  bbox_min, bbox_max, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)"
            " ON DUPLICATE KEY UPDATE cache_file_path=VALUES(cache_file_path),"
            " node_count=VALUES(node_count), instance_count=VALUES(instance_count),"
            " bbox_min=VALUES(bbox_min), bbox_max=VALUES(bbox_max), updated_at=CURRENT_TIMESTAMP",
            (int(project_id), os.path.abspath(file_path), os.path.abspath(cache_path),
             int(len(node_labels)), 1,
             str(octree_node_data["bbox_min"].tolist()),
             str(octree_node_data["bbox_max"].tolist())))

        save_fem_model_dimensions(
            project_id=int(project_id),
            bbox_min=octree_node_data["bbox_min"],
            bbox_max=octree_node_data["bbox_max"],
            cursor=cursor)
        project_config = upsert_project_config(
            int(project_id),
            extra_json={"fem_data_source": source,
                        "fem_octree_source_file": os.path.abspath(file_path)},
            cursor=cursor)

        conn.commit()

        result = {
            "file_path": os.path.abspath(file_path),
            "source": source,
            "cleared_before_insert": clear_before_insert,
            "material_count": len(materials),
            "isotropic_material_count": sum(1 for p in materials.values() if p.get("EX") is not None),
            "section_count": len(sections),
            "quantity_set_capability_count": len(capabilities),
            "octree_node_count": int(len(node_labels)),
            "project_config": project_config,
        }
        safe_write_console_event(
            int(project_id),
            "{}导入完成".format(source.upper()),
            [f"文件: {result['file_path']}",
             f"材料数: {result['material_count']}",
             f"截面数: {result['section_count']}",
             f"修正量集合数: {result['quantity_set_capability_count']}",
             f"节点数: {result['octree_node_count']}"],
        )
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
