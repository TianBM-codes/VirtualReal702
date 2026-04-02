import json

import meshio
import numpy as np

from db import get_connection, ensure_tables_exist, clear_unv_tables
from FemToolsUNVParser import parse_unv


def _to_builtin(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_to_builtin(item) for item in value.tolist()]
    if isinstance(value, dict):
        return {str(key): _to_builtin(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_builtin(item) for item in value]
    return value


def _safe_float(value):
    if value is None:
        return None
    return float(_to_builtin(value))


def _safe_int(value):
    if value is None:
        return None
    return int(_to_builtin(value))


def parse_unv_file(file_path):
    """
    解析试验测试模态结果，并保存到mysql数据库中
    """
    try:
        nodes, _, test_ele_lines, test_modes, _, message = parse_unv(file_path)
    except KeyError as e:
        raise e

    test_nodes = [{"nid": _safe_int(iter_node.id), "ics": _safe_int(iter_node.ics), "ocs": _safe_int(iter_node.ocs),
                   "x": _safe_float(iter_node.coord[0]), "y": _safe_float(iter_node.coord[1]), "z": _safe_float(iter_node.coord[2])}
                  for iter_node in nodes]

    test_elements = []
    ele_no = 1
    for line in test_ele_lines:
        for ii in range(len(line) - 1):
            test_elements.append(
                {
                    "element_no": ele_no,
                    "element_type": "LINE2",
                    "point1": line[ii],
                    "point2": line[ii + 1],
                    "point3": None,
                    "point4": None,
                }
            )
            ele_no += 1

    return test_nodes, test_elements, test_modes, message


def import_unv_data(file_path, project_id, file_id, clear_before_insert=True):
    """
    导入unv文件至数据库
    :param file_path:
    :param project_id:
    :param file_id:
    :param clear_before_insert:
    :return:
    """
    ensure_tables_exist()
    try:
        test_nodes, test_elements, test_modes, message = parse_unv_file(file_path)
    except KeyError as e:
        raise e

    conn = get_connection()
    cursor = conn.cursor()

    try:
        if clear_before_insert:
            clear_unv_tables(cursor, project_id)

        node_sql = """
        INSERT INTO t_mt_py_test_node (nid, pid, fid, ics, ocs, x, y, z)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        for node in test_nodes:
            cursor.execute(node_sql, (
                str(node['nid']),
                project_id,
                file_id,
                _safe_int(node["ics"]),
                _safe_int(node["ocs"]),
                _safe_float(node["x"]),
                _safe_float(node["y"]),
                _safe_float(node["z"])
            ))

        element_sql = """
        INSERT INTO t_mt_py_test_element (
            element_no, pid, element_type,
            point1, point2, point3, point4
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s
        )
        """

        for elem in test_elements:
            cursor.execute(element_sql, (
                _safe_int(elem["element_no"]),
                project_id,
                elem["element_type"],
                _safe_int(elem.get("point1")),
                _safe_int(elem.get("point2")),
                _safe_int(elem.get("point3")),
                _safe_int(elem.get("point4")),
            ))

        freq_sql = """
        INSERT INTO t_mt_py_test_modal_frequency (mode_no, pid, fid, frequency, damping, eigenvalue_Re, eigenvalue_Im)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """

        for mode in test_modes:
            cursor.execute(freq_sql, (
                _safe_int(mode["modal_number"]),
                project_id,
                file_id,
                _safe_float(mode["frequency"]),
                _safe_float(mode["damping"]),
                _safe_float(mode.get("eigenvalue_Re")),
                _safe_float(mode.get("eigenvalue_Im"))
            ))

        modal_sql = """
        INSERT INTO t_mt_py_test_modal_shape (mode_no, pid, modal_shape)
        VALUES (%s, %s, %s)
        """

        for mode in test_modes:
            mode_num = _safe_int(mode["modal_number"])
            mode_shape = json.dumps(_to_builtin(mode["displacements"]), ensure_ascii=False)
            cursor.execute(modal_sql, (
                mode_num,
                project_id,
                mode_shape
            ))

        if "is_real" in message:
            if message["is_real"]:
                modal_sql = """
                            INSERT INTO t_mt_py_test_modal_shape_real (mode_no, pid, point, ux, uy, uz)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """
                for mode in test_modes:
                    mode_num = _safe_int(mode["modal_number"])
                    for node, dis in mode["displacements"].items():
                        real_part = _to_builtin(dis['real'])
                        cursor.execute(modal_sql, (
                            mode_num,
                            project_id,
                            _safe_int(node),
                            _safe_float(real_part[0]), _safe_float(real_part[1]), _safe_float(real_part[2])
                        ))

                cursor.execute("""
                    UPDATE t_mt_work_condition_project
                    SET test_modal_data_type = %s
                    WHERE project_id = %s
                """, ("REAL", project_id))

            else:
                modal_sql = """
                            INSERT INTO t_mt_py_test_modal_shape_imag (mode_no, pid, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """
                for mode in test_modes:
                    mode_num = _safe_int(mode["modal_number"])
                    for node, dis in mode["displacements"].items():
                        real_part = _to_builtin(dis['real'])
                        imag_part = _to_builtin(dis['imag'])
                        cursor.execute(modal_sql, (
                            mode_num,
                            project_id,
                            _safe_int(node),
                            _safe_float(real_part[0]), _safe_float(real_part[1]), _safe_float(real_part[2]),
                            _safe_float(imag_part[0]), _safe_float(imag_part[1]), _safe_float(imag_part[2])
                        ))
                cursor.execute("""
                    UPDATE t_mt_work_condition_project
                    SET test_modal_data_type = %s
                    WHERE project_id = %s
                """, ("IMAG", project_id))

        cursor.execute("""
            UPDATE t_mt_work_condition_project
            SET test_data_status = %s
            WHERE project_id = %s
        """, (1, project_id))

        conn.commit()

        return {
            "file_path": file_path,
            "cleared_before_insert": clear_before_insert,
            "test_node_count": len(test_nodes),
            "test_element_count": len(test_elements),
            "test_mode_count": len(test_modes)
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_modal_shape(project_id):
    """
    获取模态模型
    :return:
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        node_sql = """
                    SELECT nid, x, y, z
                    FROM t_mt_py_test_node
                    WHERE pid = %s
                    ORDER BY nid
                    """
        ele_sql = """
                    SELECT point1,point2
                    FROM t_mt_py_test_element
                    WHERE pid = %s
                    ORDER BY element_no
                    """
        freq_sql = """
                   SELECT f.mode_no, f.frequency,
                   s.modal_shape
                   FROM t_mt_py_test_modal_frequency f
                   LEFT JOIN t_mt_py_test_modal_shape s
                   ON f.pid = s.pid AND f.mode_no = s.mode_no
                   WHERE f.pid = %s
                   ORDER BY f.mode_no
                   """
        """
        读取节点结果
        """
        cursor.execute(node_sql, (project_id,))
        nodes = cursor.fetchall()

        node_ids = np.array(nodes, dtype=int)[:, 0].flatten()
        node2idx = {}
        for ii, nd in enumerate(node_ids):
            node2idx[nd] = ii
        node_coords = np.array(nodes, dtype=float)[:,1:].flatten().tolist()

        """
        读取单元信息
        """
        cursor.execute(ele_sql, (project_id,))
        eles_fetchall = np.array(cursor.fetchall()).flatten()
        eles = [node2idx[ii] for ii in eles_fetchall]

        """
        读取模态信息
        """
        cursor.execute(freq_sql, (project_id,))
        modal = cursor.fetchall()

        modal_shape = []
        for iter_modal in modal:
            shape = json.loads(iter_modal[2])
            iter_modal_shape = []
            real_modal_shape = []
            imag_modal_shape = []
            for n_id in node_ids:
                iter_modal_shape.extend(shape[str(n_id)])
                real_modal_shape.extend(shape[str(n_id)]['real'])
                imag_modal_shape.extend(shape[str(n_id)]['imag'])
            modal_shape.append({"order": iter_modal[0], "frequency": f"{iter_modal[1]}", "unit": "Hz",
                                "position": {"real": real_modal_shape, "imag": imag_modal_shape}})

        """
        组装成json格式
        """
        res_json = {"points": {"ids": node_ids.tolist(),
                               "position": node_coords,
                               "ItemSize": 3},
                    "elements": {"type": 2, "index": eles, "ItemSize": 2},
                    "modal_shape": modal_shape}

    except Exception:
        raise

    finally:
        cursor.close()
        conn.close()

    return res_json


def dump_unv_modal_shapes_to_vtk(project_id, output_path):
    """
    可视化试验模态振型, 生成vtk文件
    :param project_id:
    :@param output_path:
    :return:
    """
    res_json = get_modal_shape(project_id)
    if res_json is not None:
        node_pos = np.reshape(np.array(res_json["points"]["position"], dtype=float), (-1, 3))
        eles = res_json["elements"]["index"]
        all_eles = {"line": np.reshape(np.array(eles, dtype=int), (-1, 2))}
        modal_shape = {}
        for iter_shape in res_json["modal_shape"]:
            name = f"{iter_shape['order']}_{iter_shape['frequency']}"
            modal_pos = np.reshape(np.array(iter_shape["position"], dtype=float), (-1, 3)) - node_pos
            modal_shape["unit"] = "Hz"
            modal_shape[name] = modal_pos
        meshio.write_points_cells(filename=output_path,
                                  points=node_pos,
                                  cells=all_eles,
                                  point_data=modal_shape)


def dump_unv_modal_to_json(project_id, output_path):
    """
    导出文件至json
    :param project_id:
    :param output_path:
    :return:
    """
    res_json = get_modal_shape(project_id)
    if res_json is not None:
        with open(output_path, 'w') as f:
            json.dump(res_json, f, indent=4)
        return {
            "json_path": output_path,
        }
    else:
        return {"message": ""}


if __name__ == "__main__":
    import_unv_data("C:\\FEMtools\\3.7.1\\examples\\updating\\powertrain\\ema24.unv", 20, 20, True)
