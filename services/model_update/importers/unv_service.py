import json

import meshio
import numpy as np

from db import get_connection, ensure_tables_exist, clear_unv_tables
from FemToolsUNVParser import parse_unv
from services.model_update.importers.unv_frf_service import (
    get_project_frf_curve,
    get_project_frf_names,
    import_unv_frf_data,
    list_dataset_ids,
)
from src.l3.core.errors import NotFoundError, ValidationError
from services.model_update.analysis.console_log_service import safe_write_console_event
from services.model_update.analysis.project_config_service import (
    get_test_data_mode,
    save_test_data_mode,
    save_test_model_dimensions,
)


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


def _classify_unv_result(message, test_modes):
    static_types = {1}
    dynamic_types = {2, 3}
    mode_types = {
        int(mode["analysis_type"])
        for mode in test_modes
        if mode.get("analysis_type") is not None
    }

    has_static = bool(mode_types & static_types)
    has_dynamic = bool(mode_types & dynamic_types)
    if has_static and has_dynamic:
        raise ValueError("一次 UNV 导入中不支持同时包含静态和动态的 dataset 55 结果")

    if "is_static" in message:
        message_is_static = bool(message["is_static"])
        if message_is_static and has_dynamic:
            raise ValueError("解析器消息显示为静态数据，但模态内容实际为动态数据")
        if (not message_is_static) and has_static:
            raise ValueError("解析器消息显示为动态数据，但模态内容实际为静态数据")
        return "static" if message_is_static else "dynamic"

    if has_static:
        return "static"
    return "dynamic"


def _update_project_test_state(cursor, project_id, data_type):
    cursor.execute("""
        UPDATE t_mt_work_condition_project
        SET test_modal_data_type = %s
        WHERE project_id = %s
    """, (data_type, project_id))

    cursor.execute("""
        UPDATE t_mt_work_condition_project
        SET test_data_status = %s
        WHERE project_id = %s
    """, (1, project_id))


def _insert_dynamic_modal_data(cursor, project_id, file_id, test_modes, message):
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

    if "is_real" not in message:
        _update_project_test_state(cursor, project_id, None)
        return

    if message["is_real"]:
        modal_real_sql = """
                        INSERT INTO t_mt_py_test_modal_shape_real (mode_no, pid, point, ux, uy, uz)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """
        for mode in test_modes:
            mode_num = _safe_int(mode["modal_number"])
            for node, dis in mode["displacements"].items():
                real_part = _to_builtin(dis['real'])
                cursor.execute(modal_real_sql, (
                    mode_num,
                    project_id,
                    _safe_int(node),
                    _safe_float(real_part[0]), _safe_float(real_part[1]), _safe_float(real_part[2])
                ))
        data_type = "REAL"
    else:
        modal_imag_sql = """
                        INSERT INTO t_mt_py_test_modal_shape_imag (mode_no, pid, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
        for mode in test_modes:
            mode_num = _safe_int(mode["modal_number"])
            for node, dis in mode["displacements"].items():
                real_part = _to_builtin(dis['real'])
                imag_part = _to_builtin(dis['imag'])
                cursor.execute(modal_imag_sql, (
                    mode_num,
                    project_id,
                    _safe_int(node),
                    _safe_float(real_part[0]), _safe_float(real_part[1]), _safe_float(real_part[2]),
                    _safe_float(imag_part[0]), _safe_float(imag_part[1]), _safe_float(imag_part[2])
                ))
        data_type = "IMAG"

    _update_project_test_state(cursor, project_id, data_type)


def _insert_static_results(cursor, project_id, file_id, test_modes):
    static_sql = """
    INSERT INTO t_mt_py_test_static_result
    (pid, fid, load_case_no, result_no, point, ux, uy, uz, rx, ry, rz, load_factor, extra_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    row_count = 0
    for mode in test_modes:
        load_case_no = _safe_int(mode.get("load_case")) or 0
        result_no = _safe_int(mode.get("modal_number")) or 0
        load_factor = _safe_float(mode.get("load_factor"))
        extra_json = {
            "analysis_type": _safe_int(mode.get("analysis_type")),
            "data_type": _safe_int(mode.get("data_type")),
            "ndv": _safe_int(mode.get("ndv")),
        }

        for node, dis in mode["displacements"].items():
            real_part = list(_to_builtin(dis.get("real", (0.0, 0.0, 0.0))))
            while len(real_part) < 3:
                real_part.append(0.0)

            rotate_part = _to_builtin(dis.get("rotate"))
            if rotate_part is None:
                rotate_part = [None, None, None]
            else:
                rotate_part = list(rotate_part)
                while len(rotate_part) < 3:
                    rotate_part.append(None)

            row_extra_json = dict(extra_json)
            imag_part = _to_builtin(dis.get("imag"))
            if imag_part is not None:
                row_extra_json["imag"] = list(imag_part)

            cursor.execute(static_sql, (
                project_id,
                file_id,
                load_case_no,
                result_no,
                _safe_int(node),
                _safe_float(real_part[0]),
                _safe_float(real_part[1]),
                _safe_float(real_part[2]),
                _safe_float(rotate_part[0]),
                _safe_float(rotate_part[1]),
                _safe_float(rotate_part[2]),
                load_factor,
                json.dumps(row_extra_json, ensure_ascii=False),
            ))
            row_count += 1

    _update_project_test_state(cursor, project_id, "STATIC")
    return row_count


def _clear_measuring_points(cursor, project_id):
    cursor.execute(
        "DELETE FROM t_mt_measuring_point_info WHERE project_id = %s",
        (project_id,),
    )


def _insert_measuring_points(cursor, project_id, test_nodes):
    insert_sql = """
    INSERT INTO t_mt_measuring_point_info
    (measuring_point_name, project_id, sensor_type_id, x_position, y_position, z_position, data_source)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    update_name_sql = """
    UPDATE t_mt_measuring_point_info
    SET measuring_point_name = %s
    WHERE id = %s
    """

    row_count = 0
    for node in test_nodes:
        cursor.execute(insert_sql, (
            f"WY_PENDING_{_safe_int(node['nid'])}",
            project_id,
            21,
            _safe_float(node["x"]),
            _safe_float(node["y"]),
            _safe_float(node["z"]),
            "LOCAL",
        ))
        inserted_id = getattr(cursor, "lastrowid", None)
        if inserted_id is None:
            raise ValueError("无法获取新插入测点的 id")
        cursor.execute(update_name_sql, (f"WY{int(inserted_id)}", int(inserted_id)))
        row_count += 1
    return row_count


def _sensor_position_item(row):
    return {
        "sensor_label": str(row["measuring_point_name"]),
        "sensor_type": "位移",
        "sensor_pos": [
            _safe_float(row["x_position"]),
            _safe_float(row["y_position"]),
            _safe_float(row["z_position"]),
        ],
    }


def _sensor_position_item_from_test_node(row):
    return {
        "sensor_label": str(row["test_node_id"]),
        "sensor_type": "浣嶇Щ",
        "sensor_pos": [
            _safe_float(row["x_position"]),
            _safe_float(row["y_position"]),
            _safe_float(row["z_position"]),
        ],
    }


_ERROR_COMPONENT_PRIORITY = {
    "UY": 0,
    "UX": 1,
    "UZ": 2,
    "RY": 3,
    "RX": 4,
    "RZ": 5,
}


def _analysis_error_value(row):
    updated = row.get("updated_relative_error")
    if updated is not None:
        return _safe_float(updated), "updated"
    initial = row.get("initial_relative_error")
    if initial is not None:
        return _safe_float(initial), "initial"
    return None, None


def _analysis_error_sort_key(row):
    component_name = str(row.get("component_name") or "").strip().upper()
    component_priority = _ERROR_COMPONENT_PRIORITY.get(component_name, 999)
    value, value_source = _analysis_error_value(row)
    return (
        _safe_int(row.get("load_case_no")) or -1,
        _safe_int(row.get("result_no")) or -1,
        1 if value_source == "updated" else 0,
        -component_priority,
        1 if value is not None else 0,
    )


def _build_analysis_error_lookup(rows):
    grouped = {}
    for row in rows:
        point_no = str(row.get("point_no") or "").strip()
        if not point_no:
            continue
        value, _ = _analysis_error_value(row)
        if value is None:
            continue
        existing = grouped.get(point_no)
        if existing is None or _analysis_error_sort_key(row) > _analysis_error_sort_key(existing):
            grouped[point_no] = row
    return grouped


def _sensor_error_keys(row):
    keys = []
    measuring_point_name = str(row.get("measuring_point_name") or "").strip()
    if measuring_point_name:
        keys.append(measuring_point_name)
    point_id = _safe_int(row.get("id"))
    if point_id is not None:
        keys.append(str(point_id))
    return keys


def _resolve_deform_rows(cursor, project_id, static_result_id=None, load_case_no=None, result_no=None):
    if static_result_id is not None:
        cursor.execute(
            """
            SELECT id, point, ux, uy, uz
            FROM t_mt_py_test_static_result
            WHERE pid = %s AND id = %s
            """,
            (project_id, int(static_result_id)),
        )
        rows = cursor.fetchall()
        if not rows:
            raise NotFoundError(
                "未找到试验静态结果记录",
                {"project_id": project_id, "static_result_id": int(static_result_id)},
            )
        return rows

    if load_case_no is None or result_no is None:
        cursor.execute(
            """
            SELECT load_case_no, result_no
            FROM t_mt_py_test_static_result
            WHERE pid = %s
            ORDER BY load_case_no, result_no, id
            LIMIT 1
            """,
            (project_id,),
        )
        pair = cursor.fetchone()
        if not pair:
            raise NotFoundError(
                "未找到试验静态结果数据",
                {"project_id": project_id},
            )
        load_case_no = int(pair["load_case_no"])
        result_no = int(pair["result_no"])

    cursor.execute(
        """
        SELECT id, point, ux, uy, uz
        FROM t_mt_py_test_static_result
        WHERE pid = %s AND load_case_no = %s AND result_no = %s
        ORDER BY point, id
        """,
        (project_id, int(load_case_no), int(result_no)),
    )
    rows = cursor.fetchall()
    if not rows:
        raise NotFoundError(
            "所选工况下未找到试验静态结果数据",
            {
                "project_id": project_id,
                "load_case_no": int(load_case_no),
                "result_no": int(result_no),
            },
        )
    return rows


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


def _summarize_imported_sections(*, has_dataset55: bool, has_dataset58: bool) -> tuple[list[str], list[str]]:
    imported_sections = []
    result_kinds = []
    if has_dataset55:
        imported_sections.append("dataset55")
    if has_dataset58:
        imported_sections.append("dataset58")
    if has_dataset55:
        result_kinds.append("modal_or_static")
    if has_dataset58:
        result_kinds.append("frf")
    return imported_sections, result_kinds


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
    dataset_ids = set(list_dataset_ids(file_path))
    has_dataset55 = "55" in dataset_ids
    has_dataset58 = "58" in dataset_ids
    if not has_dataset55 and not has_dataset58:
        raise ValidationError("UNV file does not contain supported dataset 55/58 content", {"file_path": file_path})

    test_nodes = []
    test_elements = []
    test_modes = []
    message = {}
    result_kind = None
    if has_dataset55:
        try:
            test_nodes, test_elements, test_modes, message = parse_unv_file(file_path)
        except KeyError as e:
            raise e
        result_kind = _classify_unv_result(message, test_modes)

    conn = get_connection()
    cursor = conn.cursor()

    try:
        if has_dataset55 and clear_before_insert:
            clear_unv_tables(cursor, project_id)
            _clear_measuring_points(cursor, project_id)

        static_result_count = 0
        measuring_point_count = 0
        project_config = None
        if has_dataset55:
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

            measuring_point_count = _insert_measuring_points(cursor, project_id, test_nodes)

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

            if result_kind == "static":
                static_result_count = _insert_static_results(cursor, project_id, file_id, test_modes)
            else:
                _insert_dynamic_modal_data(cursor, project_id, file_id, test_modes, message)

            project_config = save_test_model_dimensions(
                project_id=project_id,
                points=[
                    (
                        _safe_float(node["x"]) or 0.0,
                        _safe_float(node["y"]) or 0.0,
                        _safe_float(node["z"]) or 0.0,
                    )
                    for node in test_nodes
                ],
                cursor=cursor,
            )
            project_config = save_test_data_mode(
                project_id=project_id,
                test_data_mode="modal_unv" if result_kind == "dynamic" else "static_unv",
                test_data_source="unv",
                cursor=cursor,
            )

        conn.commit()
        frf_result = import_unv_frf_data(file_path, project_id) if has_dataset58 else {
            "project_id": int(project_id),
            "frf_curve_count": 0,
            "frf_point_count": 0,
            "curve_names": [],
        }
        imported_sections, result_kinds = _summarize_imported_sections(
            has_dataset55=has_dataset55,
            has_dataset58=has_dataset58,
        )

        result = {
            "file_path": file_path,
            "result_kind": result_kind,
            "imported_sections": imported_sections,
            "result_kinds": result_kinds,
            "cleared_before_insert": clear_before_insert,
            "test_node_count": len(test_nodes),
            "measuring_point_count": measuring_point_count,
            "test_element_count": len(test_elements),
            "test_mode_count": len(test_modes),
            "test_static_result_count": static_result_count,
            "project_config": project_config,
            "frf_curve_count": int(frf_result.get("frf_curve_count") or 0),
            "frf_point_count": int(frf_result.get("frf_point_count") or 0),
            "frf_curve_names": list(frf_result.get("curve_names") or []),
        }
        safe_write_console_event(
            int(project_id),
            "UNV导入完成",
            [
                f"文件: {file_path}",
                f"导入段落: {', '.join(imported_sections) or 'NONE'}",
                f"结果类型: {result_kind or 'NONE'}",
                f"测点数: {len(test_nodes)}",
                f"测点表记录数: {measuring_point_count}",
                f"单元数: {len(test_elements)}",
                f"模态/结果数: {len(test_modes)}",
                f"FRF曲线数: {int(frf_result.get('frf_curve_count') or 0)}",
            ],
        )
        return result

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
        node_coords = np.array(nodes, dtype=float)[:, 1:].flatten().tolist()

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
        return res_json

    except Exception:
        raise

    finally:
        cursor.close()
        conn.close()


def get_frf_names(project_id: int) -> dict:
    return get_project_frf_names(int(project_id))


def get_frf_curve(project_id: int, name: str, index: int) -> dict:
    return get_project_frf_curve(int(project_id), name, int(index))


def get_sensor_relative_error(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id
            """,
            (project_id,),
        )
        sensor_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT load_case_no, result_no, point_no, component_name,
                   initial_relative_error, updated_relative_error
            FROM t_mt_py_fem_analysis_error
            WHERE pid = %s
            ORDER BY load_case_no DESC, result_no DESC, point_no, component_name
            """,
            (project_id,),
        )
        error_lookup = _build_analysis_error_lookup(cursor.fetchall())
        res = []
        for row in sensor_rows:
            iter_dict = _sensor_position_item(row)
            matched_rows = [
                error_lookup[key]
                for key in _sensor_error_keys(row)
                if key in error_lookup
            ]
            if matched_rows:
                chosen_row = max(matched_rows, key=_analysis_error_sort_key)
                iter_dict["e_value"] = _analysis_error_value(chosen_row)[0]
            else:
                iter_dict["e_value"] = None
            res.append(iter_dict)
        return res
    finally:
        cursor.close()
        conn.close()


def get_sensor_positions(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        test_data_mode = str(get_test_data_mode(int(project_id), cursor=cursor) or "").strip().lower()
        if test_data_mode == "modal_unv":
            cursor.execute(
                """
                SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position
                FROM t_mt_py_test_node
                WHERE pid = %s
                ORDER BY nid
                """,
                (project_id,),
            )
            rows = cursor.fetchall()
            return [_sensor_position_item_from_test_node(row) for row in rows]

        cursor.execute(
            """
            SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id
            """,
            (project_id,),
        )
        rows = cursor.fetchall()
        return [_sensor_position_item(row) for row in rows]
    finally:
        cursor.close()
        conn.close()


def get_deform_sensor_positions(project_id, scale=1.0, static_result_id=None, load_case_no=None, result_no=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        deform_rows = _resolve_deform_rows(
            cursor,
            project_id,
            static_result_id=static_result_id,
            load_case_no=load_case_no,
            result_no=result_no,
        )
        result = []
        for deform_row in deform_rows:
            point_id = str(deform_row["point"])
            cursor.execute(
                """
                SELECT nid, x, y, z
                FROM t_mt_py_test_node
                WHERE pid = %s AND nid = %s
                ORDER BY fid
                LIMIT 1
                """,
                (project_id, point_id),
            )
            node_row = cursor.fetchone()
            if not node_row:
                raise NotFoundError(
                    "test node not found for static result point",
                    {
                        "project_id": project_id,
                        "point": point_id,
                        "static_result_id": int(deform_row["id"]),
                    },
                )

            cursor.execute(
                """
                SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position
                FROM t_mt_measuring_point_info
                WHERE project_id = %s
                  AND x_position = %s
                  AND y_position = %s
                  AND z_position = %s
                ORDER BY id
                LIMIT 1
                """,
                (
                    project_id,
                    _safe_float(node_row["x"]),
                    _safe_float(node_row["y"]),
                    _safe_float(node_row["z"]),
                ),
            )
            measuring_row = cursor.fetchone()
            if measuring_row is None:
                measuring_row = {
                    "measuring_point_name": f"WY{point_id}",
                    "x_position": node_row["x"],
                    "y_position": node_row["y"],
                    "z_position": node_row["z"],
                }

            result.append(
                {
                    "sensor_label": str(measuring_row["measuring_point_name"]),
                    "sensor_type": "位移",
                    "sensor_pos": [
                        _safe_float(node_row["x"]) + _safe_float(deform_row["ux"] or 0.0) * float(scale),
                        _safe_float(node_row["y"]) + _safe_float(deform_row["uy"] or 0.0) * float(scale),
                        _safe_float(node_row["z"]) + _safe_float(deform_row["uz"] or 0.0) * float(scale),
                    ],
                }
            )
        return result
    finally:
        cursor.close()
        conn.close()


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
