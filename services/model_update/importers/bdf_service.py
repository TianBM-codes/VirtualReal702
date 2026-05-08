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


def _safe_int(value):
    """
    None -> NULL
    其余数值转 int
    """
    if value is None:
        return None
    return int(value)


def import_bdf_data(file_path, project_id, file_id, clear_before_insert=True):
    """
    将 BDF 解析结果写入数据库

    :param file_path: bdf 文件路径
    :param project_id: 工程 ID
    :param file_id: 文件 ID
    :param clear_before_insert: 是否先按 pid 清空 FEM 相关表
    :return:
    """
    ensure_tables_exist()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        if clear_before_insert:
            clear_fem_tables(cursor, project_id)

        bdf_parser = BDFParser(file_path)
        bdf_parser.parse()
        bdf_info = bdf_parser.GetDatabaseData()

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
            (Id, pid, Offset, Theta, GE, NSM, Layers)
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
       

if __name__ == '__main__':
    import_bdf_data(
        file_path=r"D:\SiPESC_yuan\project\702_force_verify\model\227.bdf",
        project_id=1000,
        file_id=2000,
        clear_before_insert=True
    )
