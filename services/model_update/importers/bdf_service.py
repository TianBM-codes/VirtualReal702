from db import get_connection, ensure_tables_exist, clear_fem_tables
from BDFParserPyNastran import BDFParser


def _safe_float(value):
    """
    None -> NULL
    其余数值转 float
    """
    if value is None:
        return None
    return float(value)


def import_bdf_data(file_path, project_id, clear_before_insert=True):
    ensure_tables_exist()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        if clear_before_insert:
            clear_fem_tables(cursor, project_id)

        bdf_parser = BDFParser(file_path)
        bdf_parser.parse()
        bdf_info = bdf_parser.GetDatabaseData()

        """
        1. 材料总览表
        bdf_info["materials_overview"] = [(mat_id, "ISOTROPIC"), ...]
        """
        material_sql = """
        INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type)
        VALUES (%s, %s, %s)
        """
        for mat_id, mat_type in bdf_info.get("materials_overview", []):
            cursor.execute(material_sql, (
                mat_id,
                project_id,
                mat_type
            ))

        """
        2. 各向同性材料表
        bdf_info["isotonic_list"] = [(mat_id, rho, E, nu, ge), ...]
        """
        isotropic_sql = """
        INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        for mat_id, rho, e_value, nu, ge in bdf_info.get("isotropic_list", []):
            cursor.execute(isotropic_sql, (
                mat_id,
                project_id,
                _safe_float(rho),
                _safe_float(e_value),
                _safe_float(nu),
                _safe_float(ge)
            ))

        """
        3. 属性总览表
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
        4. 壳单元属性表
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
        5. 梁单元属性表
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

        """
        6. 边界条件表
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

        conn.commit()

        return {
            "file_path": file_path,
            "cleared_before_insert": clear_before_insert,
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
