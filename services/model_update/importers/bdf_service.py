import os
import json

import numpy as np

from db import get_connection, ensure_tables_exist, clear_fem_tables
from BDFParserPyNastran import BDFParser
from services.model_update.analysis.console_log_service import safe_write_console_event
from services.model_update.analysis.inp_service import (
    _SUPPORTED_CORRECTION_QUANTITIES,
    _save_octree_cache,
)
from services.model_update.analysis.project_config_service import save_fem_model_dimensions, upsert_project_config


def _safe_float(value):
    """
    None -> NULL
    其余数值转 float
    """
    if value is None:
        return None
    return float(value)


def _safe_int(value):
    """
    None -> NULL
    其余数值转 int
    """
    if value is None:
        return None
    return int(value)


def _json_dumps(data):
    return json.dumps(data, ensure_ascii=False)


def _element_property_id(element):
    try:
        pid = element.Pid()
        return int(pid) if pid is not None else None
    except Exception:
        pid = getattr(element, "pid", None)
        return int(pid) if pid is not None else None


def _property_material_id(prop):
    if prop is None:
        return None
    for attr_name in ("mid1", "mid", "Mid1", "Mid"):
        value = getattr(prop, attr_name, None)
        if hasattr(value, "mid"):
            try:
                return int(value.mid)
            except Exception:
                continue
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return None


def _material_elastic_modulus(material):
    if material is None:
        return None
    for attr_name in ("e", "E", "e11"):
        value = getattr(material, attr_name, None)
        if value is not None:
            try:
                return float(value)
            except Exception:
                pass
    try:
        fields = list(material.raw_fields() or [])
    except Exception:
        fields = []
    material_type = str(getattr(material, "type", "") or "").upper()
    if material_type == "MAT1" and len(fields) > 2 and fields[2] not in (None, ""):
        return float(fields[2])
    if material_type == "MAT8" and len(fields) > 2 and fields[2] not in (None, ""):
        return float(fields[2])
    return None


def _material_density(material):
    if material is None:
        return None
    for attr_name in ("rho", "Rho", "RHO"):
        value = getattr(material, attr_name, None)
        if value is not None:
            try:
                return float(value)
            except Exception:
                pass
    try:
        fields = list(material.raw_fields() or [])
    except Exception:
        fields = []
    material_type = str(getattr(material, "type", "") or "").upper()
    if material_type in {"MAT1", "MAT8", "MAT9"} and len(fields) > 1 and fields[1] not in (None, ""):
        return float(fields[1])
    return None


def _property_thickness(prop):
    if prop is None:
        return None
    ptype = str(getattr(prop, "type", "") or "").upper()
    if ptype == "PSHELL":
        value = getattr(prop, "t", None)
        if isinstance(value, np.ndarray):
            value = value[0] if len(value) else None
        return _safe_float(value)
    if ptype in {"PCOMP", "PCOMPG"}:
        if hasattr(prop, "TotalThickness"):
            try:
                return float(prop.TotalThickness())
            except Exception:
                pass
        if hasattr(prop, "Thickness"):
            try:
                return float(prop.Thickness())
            except Exception:
                pass
    return None


def _property_element_family(prop):
    ptype = str(getattr(prop, "type", "") or "").upper()
    if ptype in {"PSHELL", "PCOMP", "PCOMPG"}:
        return "SHELL"
    if ptype in {"PBAR", "PBEAM", "PROD", "PTUBE", "PBARL", "PBEAML"}:
        return "BEAM"
    if ptype in {"PSOLID", "PLSOLID", "PIHEX", "PCOMPS"}:
        return "SOLID"
    return "OTHER"


def _build_bdf_property_set_capabilities(bdf_parser):
    bdf_model = bdf_parser.bdf
    property_elements = {}
    material_elements = {}
    for eid, element in sorted((bdf_model.elements or {}).items()):
        pid = _element_property_id(element)
        if pid is None:
            continue
        property_elements.setdefault(int(pid), []).append(int(eid))
        prop = bdf_model.properties.get(int(pid))
        material_id = _property_material_id(prop)
        if material_id is not None:
            material_elements.setdefault(int(material_id), []).append(int(eid))

    capability_rows = []
    for pid, element_labels in sorted(property_elements.items()):
        prop = bdf_model.properties.get(int(pid))
        if prop is None:
            continue

        element_family = _property_element_family(prop)
        material_id = _property_material_id(prop)
        material = bdf_model.materials.get(int(material_id)) if material_id is not None else None
        material_name = f"MID_{int(material_id)}" if material_id is not None else None
        e_value = _material_elastic_modulus(material)
        rho_value = _material_density(material)
        t_value = _property_thickness(prop)
        part_name = "BDF_MODEL"
        set_name = f"PROPERTY_{int(pid)}"
        target_keys = [f"PART::{part_name}::{int(label)}" for label in element_labels]
        target_keys_by_label = {
            str(int(label)): [f"PART::{part_name}::{int(label)}"] for label in element_labels
        }

        quantity_values = {
            "E": e_value,
            "T": t_value,
            "RHO": rho_value,
        }
        section_type = str(getattr(prop, "type", "") or "").upper() or None

        for quantity in _SUPPORTED_CORRECTION_QUANTITIES:
            quantity_code = str(quantity["quantity_code"])
            current_value = quantity_values.get(quantity_code)
            supports_global = False
            supports_local = False
            if quantity_code == "E" and current_value is not None and element_family in {"SHELL", "SOLID", "BEAM"}:
                supports_global = True
                supports_local = True
            elif quantity_code == "RHO" and current_value is not None and element_family in {"SHELL", "SOLID", "BEAM"}:
                supports_global = True
                supports_local = True
            elif quantity_code == "T" and current_value is not None and element_family == "SHELL":
                supports_global = True
                supports_local = True

            if not supports_global and not supports_local:
                continue

            element_values = {
                str(int(label)): float(current_value) for label in element_labels
            }
            capability_rows.append(
                {
                    "quantity_code": quantity_code,
                    "set_name": set_name,
                    "set_type": "PROPERTY",
                    "set_scope": "PART",
                    "instance_name": None,
                    "part_name": part_name,
                    "set_role": "PROPERTY_SET",
                    "element_family": element_family,
                    "section_type": section_type,
                    "material_name": material_name,
                    "member_count": len(element_labels),
                    "supports_global": supports_global,
                    "supports_local": supports_local,
                    "current_value": float(current_value),
                    "extra_json": {
                        "property_id": int(pid),
                        "material_id": int(material_id) if material_id is not None else None,
                        "element_labels": [int(label) for label in element_labels],
                        "target_keys": target_keys,
                        "target_keys_by_label": target_keys_by_label,
                        "element_values": element_values,
                    },
                }
            )

    for material_id, element_labels in sorted(material_elements.items()):
        material = bdf_model.materials.get(int(material_id))
        if material is None:
            continue
        part_name = "BDF_MODEL"
        set_name = f"MAT1_{int(material_id)}"
        target_keys = [f"PART::{part_name}::{int(label)}" for label in element_labels]
        target_keys_by_label = {
            str(int(label)): [f"PART::{part_name}::{int(label)}"] for label in element_labels
        }
        e_value = _material_elastic_modulus(material)
        rho_value = _material_density(material)
        quantity_values = {
            "E": e_value,
            "RHO": rho_value,
        }
        for quantity in _SUPPORTED_CORRECTION_QUANTITIES:
            quantity_code = str(quantity["quantity_code"])
            current_value = quantity_values.get(quantity_code)
            if quantity_code not in {"E", "RHO"} or current_value is None:
                continue
            element_values = {
                str(int(label)): float(current_value) for label in element_labels
            }
            capability_rows.append(
                {
                    "quantity_code": quantity_code,
                    "set_name": set_name,
                    "set_type": "MATERIAL",
                    "set_scope": "PART",
                    "instance_name": None,
                    "part_name": part_name,
                    "set_role": "MATERIAL_SET",
                    "element_family": "MIXED",
                    "section_type": str(getattr(material, "type", "") or "").upper() or None,
                    "material_name": set_name,
                    "member_count": len(element_labels),
                    "supports_global": True,
                    "supports_local": True,
                    "current_value": float(current_value),
                    "extra_json": {
                        "material_id": int(material_id),
                        "element_labels": [int(label) for label in element_labels],
                        "target_keys": target_keys,
                        "target_keys_by_label": target_keys_by_label,
                        "element_values": element_values,
                    },
                }
            )

    return capability_rows


def _build_bdf_octree_node_data(bdf_parser):
    point_coords = np.asarray([node.coord for node in bdf_parser.nodes], dtype=np.float64).reshape((-1, 3))
    if point_coords.size == 0:
        raise ValueError("BDF 中未找到可用于构建 octree 的节点")

    point_labels = np.asarray([int(node.id) for node in bdf_parser.nodes], dtype=np.int64)
    point_instances = np.asarray(["BDF_MODEL"] * len(point_labels), dtype="U128")
    point_parts = np.asarray(["BDF_MODEL"] * len(point_labels), dtype="U128")
    return {
        "point_coords": point_coords,
        "point_labels": point_labels,
        "point_instances": point_instances,
        "point_parts": point_parts,
        "bbox_min": point_coords.min(axis=0),
        "bbox_max": point_coords.max(axis=0),
    }


def import_bdf_data(file_path, project_id, file_id=None, clear_before_insert=True):
    """
    将 BDF 解析结果写入数据库

    :param file_path: bdf 文件路径
    :param project_id: 工程 ID
    :param file_id: 文件 ID
    :param clear_before_insert: 是否先按 pid 清空 FEM 相关表
    :return:
    """
    ensure_tables_exist()

    bdf_parser = BDFParser(file_path)
    bdf_parser.parse()
    bdf_info = bdf_parser.GetDatabaseData()
    quantity_set_capabilities = _build_bdf_property_set_capabilities(bdf_parser)
    octree_node_data = _build_bdf_octree_node_data(bdf_parser)

    conn = get_connection()
    cursor = conn.cursor()

    try:
        if clear_before_insert:
            clear_fem_tables(cursor, project_id)

        # =========================================================
        # 1. 坐标系表
        # bdf_info["coordinate_systems"] = [
        #   {
        #       "ID": ...,
        #       "RID": ...,
        #       "Type": ...,
        #       "X1": ..., ... "X9": ...
        #   }, ...
        # ]
        # =========================================================
        coord_sql = """
                INSERT INTO t_mt_py_fem_coord
                (pid, fid, coord_no, ref_coord_no, coord_type,
                 x1, x2, x3, x4, x5, x6, x7, x8, x9)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
        for row in bdf_info.get("coordinate_systems", []):
            cursor.execute(coord_sql, (
                project_id,
                file_id,
                _safe_int(row.get("ID")),
                _safe_int(row.get("RID")),
                row.get("Type"),
                _safe_float(row.get("X1")),
                _safe_float(row.get("X2")),
                _safe_float(row.get("X3")),
                _safe_float(row.get("X4")),
                _safe_float(row.get("X5")),
                _safe_float(row.get("X6")),
                _safe_float(row.get("X7")),
                _safe_float(row.get("X8")),
                _safe_float(row.get("X9")),
            ))

        quantity_sql = """
        INSERT INTO t_mt_py_fem_supported_quantity
        (quantity_code, quantity_name, unit, enabled, sort_no)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            quantity_name = VALUES(quantity_name),
            unit = VALUES(unit),
            enabled = VALUES(enabled),
            sort_no = VALUES(sort_no)
        """
        for item in _SUPPORTED_CORRECTION_QUANTITIES:
            cursor.execute(quantity_sql, (
                item["quantity_code"],
                item["quantity_name"],
                item.get("unit"),
                int(item.get("enabled", 1)),
                int(item.get("sort_no", 0)),
            ))

        # =========================================================
        # 2. 材料总览表
        # bdf_info["materials_overview"] = [(mat_id, "ISOTROPIC"), ...]
        # =========================================================
        material_sql = """
        INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type)
        VALUES (%s, %s, %s)
        """
        for mat_id, mat_type in bdf_info.get("materials_overview", []):
            cursor.execute(material_sql, (
                _safe_int(mat_id),
                project_id,
                mat_type
            ))

        # =========================================================
        # 3. 各向同性材料表 MAT1
        # bdf_info["isotropic_list"] = [(mat_id, rho, E, nu, ge), ...]
        # =========================================================
        isotropic_sql = """
            INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE)
            VALUES (%s, %s, %s, %s, %s, %s)
            """
        for mat_id, rho, e_value, nu, ge in bdf_info.get("isotropic_list", []):
            cursor.execute(isotropic_sql, (
                _safe_int(mat_id),
                project_id,
                _safe_float(rho),
                _safe_float(e_value),
                _safe_float(nu),
                _safe_float(ge)
            ))

        # =========================================================
        # 4. 正交各向异性 2D 材料表 MAT8
        # bdf_info["ortho2d_list"] =
        # [(mat_id, rho, ex, ey, gxy, nuxy, gxz, gyz, ge), ...]
        # =========================================================
        ortho2d_sql = """
            INSERT INTO t_mt_py_fem_ortho2d
            (Id, pid, RHO, EX, EY, GXY, NUXY, GXZ, GYZ, GE)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        for mat_id, rho, ex, ey, gxy, nuxy, gxz, gyz, ge in bdf_info.get("ortho2d_list", []):
            cursor.execute(ortho2d_sql, (
                _safe_int(mat_id),
                project_id,
                _safe_float(rho),
                _safe_float(ex),
                _safe_float(ey),
                _safe_float(gxy),
                _safe_float(nuxy),
                _safe_float(gxz),
                _safe_float(gyz),
                _safe_float(ge)
            ))

        # =========================================================
        # 5. 各向异性 3D 材料表 MAT9
        # bdf_info["aniso3d_list"] =
        # (
        #   mat_id, rho,
        #   d11, d12, d13, d14, d15, d16,
        #   d22, d23, d24, d25, d26,
        #   d33, d34, d35, d36,
        #   d44, d45, d46,
        #   d55, d56,
        #   d66,
        #   ge
        # )
        # =========================================================
        aniso3d_sql = """
            INSERT INTO t_mt_py_fem_aniso3d
            (Id, pid, RHO,
             D11, D12, D13, D14, D15, D16,
             D22, D23, D24, D25, D26,
             D33, D34, D35, D36,
             D44, D45, D46,
             D55, D56,
             D66, GE)
            VALUES
            (%s, %s, %s,
             %s, %s, %s, %s, %s, %s,
             %s, %s, %s, %s, %s,
             %s, %s, %s, %s,
             %s, %s, %s,
             %s, %s,
             %s, %s)
            """
        for item in bdf_info.get("aniso3d_list", []):
            (
                mat_id, rho,
                d11, d12, d13, d14, d15, d16,
                d22, d23, d24, d25, d26,
                d33, d34, d35, d36,
                d44, d45, d46,
                d55, d56,
                d66,
                ge
            ) = item

            cursor.execute(aniso3d_sql, (
                _safe_int(mat_id),
                project_id,
                _safe_float(rho),

                _safe_float(d11), _safe_float(d12), _safe_float(d13),
                _safe_float(d14), _safe_float(d15), _safe_float(d16),

                _safe_float(d22), _safe_float(d23), _safe_float(d24),
                _safe_float(d25), _safe_float(d26),

                _safe_float(d33), _safe_float(d34), _safe_float(d35), _safe_float(d36),

                _safe_float(d44), _safe_float(d45), _safe_float(d46),

                _safe_float(d55), _safe_float(d56),

                _safe_float(d66),
                _safe_float(ge)
            ))

        """
        6. 属性总览表
        bdf_info["property_overview"] = [(pid, "SHELL"), ...]
        """
        property_sql = """
        INSERT INTO t_mt_py_fem_property (Id, pid, Type)
        VALUES (%s, %s, %s)
        """
        for prop_id, prop_type in bdf_info.get("property_overview", []):
            cursor.execute(property_sql, (
                prop_id,
                project_id,
                prop_type
            ))

        """
        7. 壳单元属性表
        bdf_info["shell_properties"] = [(pid, thickness, nsm, theta), ...]
        """
        shell_sql = """
        INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA)
        VALUES (%s, %s, %s, %s, %s)
        """
        for prop_id, thickness, nsm, theta in bdf_info.get("shell_properties", []):
            cursor.execute(shell_sql, (
                prop_id,
                project_id,
                _safe_float(thickness),
                _safe_float(nsm),
                _safe_float(theta)
            ))

        """
        8. 梁单元属性表
        bdf_info["bar_properties"] = [
            (pid, ax, ay, az, ix, iy, iz, cw, yn, zn, nsm), ...
        ]
        """
        beam_sql = """
        INSERT INTO t_mt_py_fem_beam_property
        (Id, pid, AX, AY, AZ, IX, IY, IZ, CW, YN, ZN, NSM)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for prop_id, ax, ay, az, ix, iy, iz, cw, yn, zn, nsm in bdf_info.get("bar_properties", []):
            cursor.execute(beam_sql, (
                prop_id,
                project_id,
                _safe_float(ax),
                _safe_float(ay),
                _safe_float(az),
                _safe_float(ix),
                _safe_float(iy),
                _safe_float(iz),
                _safe_float(cw),
                _safe_float(yn),
                _safe_float(zn),
                _safe_float(nsm)
            ))
        # 9. 实体属性表
        solid_sql = """
            INSERT INTO t_mt_py_fem_solid_property (Id, pid, MID, CID)
            VALUES (%s, %s, %s, %s)
            """
        for prop_id, mid, cid in bdf_info.get("solid_properties", []):
            cursor.execute(solid_sql, (
                _safe_int(prop_id),
                project_id,
                _safe_int(mid),
                _safe_int(cid)
            ))

        # 9. 分层属性表（PCOMP / Layered）
        layered_sql = """
            INSERT INTO t_mt_py_fem_layered_property
            (Id, pid, Offset_L, Theta, GE, NSM, Layers)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
        for prop_id, offset, theta, ge, nsm, layers in bdf_info.get("layered_properties", []):
            cursor.execute(layered_sql, (
                _safe_int(prop_id),
                project_id,
                _safe_float(offset),
                _safe_float(theta),
                _safe_float(ge),
                _safe_float(nsm),
                _safe_int(layers)
            ))

        """
        9. 边界条件表
        bdf_info["boundary"] = [(node, [ux, uy, uz, rx, ry, rz]), ...]

        注意：
        如果某自由度没有约束，则 GetDatabaseData() 里通常是 None
        这里会直接插入为数据库 NULL
        所以表结构必须允许 NULL
        """
        boundary_sql = """
        INSERT INTO t_mt_py_fem_boundary
        (Id, pid, Node, UX, UY, UZ, RX, RY, RZ)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for idx, (node, enforced) in enumerate(bdf_info.get("boundary", []), start=1):
            ux, uy, uz, rx, ry, rz = enforced
            cursor.execute(boundary_sql, (
                idx,
                project_id,
                node,
                _safe_float(ux),
                _safe_float(uy),
                _safe_float(uz),
                _safe_float(rx),
                _safe_float(ry),
                _safe_float(rz)
            ))

        capability_sql = """
        INSERT INTO t_mt_py_fem_quantity_set_capability
        (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name,
         set_role, element_family, section_type, material_name, member_count,
         supports_global, supports_local, current_value, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            set_role = VALUES(set_role),
            element_family = VALUES(element_family),
            section_type = VALUES(section_type),
            material_name = VALUES(material_name),
            member_count = VALUES(member_count),
            supports_global = VALUES(supports_global),
            supports_local = VALUES(supports_local),
            current_value = VALUES(current_value),
            extra_json = VALUES(extra_json)
        """
        for item in quantity_set_capabilities:
            cursor.execute(capability_sql, (
                int(project_id),
                item["quantity_code"],
                item["set_name"],
                item["set_type"],
                item["set_scope"],
                item.get("instance_name"),
                item.get("part_name"),
                item["set_role"],
                item.get("element_family"),
                item.get("section_type"),
                item.get("material_name"),
                int(item["member_count"]),
                1 if item.get("supports_global") else 0,
                1 if item.get("supports_local") else 0,
                item.get("current_value"),
                _json_dumps(item.get("extra_json") or {}),
            ))

        cache_path = _save_octree_cache(
            int(project_id),
            os.path.abspath(file_path),
            octree_node_data,
            force_rebuild=True,
        )
        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_node_octree_cache
            (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE
                cache_file_path = VALUES(cache_file_path),
                node_count = VALUES(node_count),
                instance_count = VALUES(instance_count),
                bbox_min = VALUES(bbox_min),
                bbox_max = VALUES(bbox_max),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(project_id),
                os.path.abspath(file_path),
                os.path.abspath(cache_path),
                int(len(octree_node_data["point_labels"])),
                1,
                str(octree_node_data["bbox_min"].tolist()),
                str(octree_node_data["bbox_max"].tolist()),
            ),
        )
        project_config = save_fem_model_dimensions(
            project_id=int(project_id),
            bbox_min=octree_node_data["bbox_min"],
            bbox_max=octree_node_data["bbox_max"],
            cursor=cursor,
        )
        project_config = upsert_project_config(
            int(project_id),
            extra_json={
                "fem_data_source": "bdf",
                "fem_octree_source_file": os.path.abspath(file_path),
            },
            cursor=cursor,
        )

        conn.commit()

        density_defined_count = sum(
            1 for _mat_id, rho, _e_value, _nu, _ge in bdf_info.get("isotropic_list", [])
            if rho is not None
        )
        result = {
            "file_path": os.path.abspath(file_path),
            "cleared_before_insert": clear_before_insert,
            "material_count": len(bdf_info.get("materials_overview", [])),
            "isotropic_material_count": len(bdf_info.get("isotropic_list", [])),
            "isotropic_density_defined_count": int(density_defined_count),
            "property_count": len(bdf_info.get("property_overview", [])),
            "shell_property_count": len(bdf_info.get("shell_properties", [])),
            "beam_property_count": len(bdf_info.get("bar_properties", [])),
            "solid_property_count": len(bdf_info.get("solid_properties", [])),
            "layered_property_count": len(bdf_info.get("layered_properties", [])),
            "boundary_count": len(bdf_info.get("boundary", [])),
            "supported_quantity_count": len(_SUPPORTED_CORRECTION_QUANTITIES),
            "quantity_set_capability_count": len(quantity_set_capabilities),
            "octree_cache_path": os.path.abspath(cache_path),
            "octree_node_count": int(len(octree_node_data["point_labels"])),
            "project_config": project_config,
        }
        safe_write_console_event(
            int(project_id),
            "BDF导入完成",
            [
                f"文件: {os.path.abspath(file_path)}",
                f"材料数: {result['material_count']}",
                f"各向同性材料数: {result['isotropic_material_count']}",
                f"已解析密度的各向同性材料数: {result['isotropic_density_defined_count']}",
                f"属性数: {result['property_count']}",
                f"节点数: {result['octree_node_count']}",
            ],
        )

        return result

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
       

if __name__ == '__main__':
    import_bdf_data(
        file_path=r"D:\SiPESC_yuan\project\702_force_verify\model\227.bdf",
        project_id=1000,
        file_id=2000,
        clear_before_insert=True
    )
