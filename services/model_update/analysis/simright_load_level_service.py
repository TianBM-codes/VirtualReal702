"""Align explicitly selected SimRight states to ordered test records."""
import math
import json
from pathlib import Path
from decimal import Decimal

from src.l3.core.errors import AppError, ConflictError, ValidationError
from tools.simright import SimRightClient, SimRightError, project_stress, project_strain
from .test_load_level_service import load_test_data
from .project_config_service import get_project_config
from db import get_connection
import numpy as np
import sys
import requests


def _series(label, axis, values):
    x = [format(Decimal(str(value)), "f") for value in axis]
    x = [value.rstrip("0").rstrip(".") if "." in value else value for value in x]
    return {"label": label, "xaxis": x, "data": [[key, value] for key, value in zip(x, values)]}


def _loadcases(client, filename):
    raw = client.loadcases(filename)
    records = raw.get("value") if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise SimRightError("loadcases must return an array or {value:[...]}")
    result, ids = [], set()
    for index, row in enumerate(records):
        if not isinstance(row, dict) or not isinstance(row.get("id"), (str, int)) or not row.get("name"):
            raise SimRightError("loadcases is missing id/name")
        key = str(row["id"])
        if key in ids:
            raise SimRightError("loadcases contains duplicate IDs")
        ids.add(key)
        result.append({"label": row["name"], "value": row["id"], "index": index})
    return result


def _node_value(client, filename, binding, variable, step, selection, shell_layer, cache):
    key = (binding["node_id"], binding.get("part_id"), binding.get("part_ori_id"),
           variable, step["index"], selection.get("state_id"), shell_layer)
    if key in cache:
        return cache[key]
    state = selection.get("state_id")
    options = {"loadcaseIndex": step["index"], "stateIds": [] if state is None else [state],
               "coordSystemId": "global", "coordSystemType": 1,
               "nodeAverage": 1., "avgMethod": "simple", "avgRegion": "part",
               "avgOrder": "pre", "useCornerData": 0, "shellLayer": shell_layer}
    if binding.get("part_ori_id") is not None:
        options.update(entityType="Part", entitiesIds=[binding["part_ori_id"]])
    records = client.node_info(filename, [binding["node_id"]], variable, **options)
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise SimRightError("nodeInfo must return records")
    hits = [row for row in records if row.get("nodeId") == binding["node_id"] and
            (binding.get("part_id") is None or row.get("partId") == binding["part_id"])]
    if not hits or len({row.get("partId") for row in hits}) != 1:
        raise SimRightError("Missing or ambiguous node/part result")
    if any(not isinstance(row.get("stateId"), int) or isinstance(row["stateId"], bool) for row in hits):
        raise SimRightError("nodeInfo must identify every frame with stateId")
    chosen = max(row["stateId"] for row in hits) if state is None else state
    hits = [row for row in hits if row["stateId"] == chosen]
    if len(hits) != 1:
        raise SimRightError("Missing or ambiguous result for selected frame")
    row = hits[0]
    values = row.get("values")
    if not isinstance(values, list) or len(values) != 1:
        raise SimRightError("Expected one averaged scalar; corner/layer values must not be silently averaged")
    if values[0] is None or isinstance(values[0], bool):
        raise SimRightError("Missing or invalid scalar result")
    value = float(values[0])
    if not math.isfinite(value):
        raise SimRightError("SimRight result is not finite")
    cache[key] = value, (row.get("partId"), chosen)
    return cache[key]


def _simulation_value(client, filename, binding, step, selection, shell_layer, cache):
    quantity = binding.get("quantity", "scalar")
    if quantity == "scalar":
        value, identity = _node_value(client, filename, binding, binding["var_name"], step, selection, shell_layer, cache)
    else:
        prefix = "S.EL." if quantity == "stress" else "E.EL."
        variables = binding.get("component_vars") or {key: prefix + key for key in ("11", "22", "33", "12", "13", "23")}
        components, identities = {}, set()
        for component, variable in variables.items():
            component_value, identity = _node_value(client, filename, binding, variable, step, selection, shell_layer, cache)
            components[component] = component_value
            identities.add(identity)
        if len(identities) != 1:
            raise SimRightError("Tensor components have different part/frame identities")
        if quantity == "stress":
            value = project_stress(components, binding["direction"])["normal_stress"]
        else:
            value = project_strain(components, binding["direction"],
                                   shear_convention=binding["shear_convention"])["axial_strain"]
    scaled = value * binding.get("simulation_scale", 1.)
    if not math.isfinite(scaled):
        raise SimRightError("Scaled result is not finite")
    return scaled, identity


def _resolve_bindings(project_id, sensors, config):
    """Resolve server-side result settings and persisted node matches by name."""
    from webapi.load_level_models import SimrightSensorBinding

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        bindings = []
        for sensor in sensors:
            name = sensor["name"]
            options = dict(config.get("sensor_bindings", {}).get(name, {}))
            if "node_id" not in options:
                cursor.execute("""SELECT instance_name, fem_node_label FROM t_mt_py_fem_node_match
                    WHERE pid=%s AND test_node_id=%s LIMIT 2""", (project_id, name))
                rows = cursor.fetchall() or []
                if len(rows) != 1:
                    raise ValidationError("测点没有唯一的已保存节点匹配", {"sensor": name})
                options = dict(config.get("instance_parts", {}).get(rows[0]["instance_name"], {}), **options)
                options["node_id"] = rows[0]["fem_node_label"]
            try:
                bindings.append(SimrightSensorBinding(**dict(options, name=name)).model_dump())
            except ValueError as exc:
                raise ValidationError("测点的数巧结果配置不完整或无效", {"sensor": name, "reason": str(exc)}) from exc
        return bindings
    finally:
        cursor.close()
        connection.close()


def _selected_steps(steps, count, options):
    selected = [None] * count if steps is None else list(steps)
    if len(selected) != count:
        raise ValidationError("steps 长度必须与试验有效记录数一致", {"sample_count": count})
    by_id = {str(item["value"]): item for item in options}
    for index, value in enumerate(selected):
        if value is not None:
            if str(value) not in by_id:
                raise ValidationError("选择的仿真 step 不存在", {"sample_index": index, "loadcase_id": value})
            selected[index] = by_id[str(value)]["value"]
    return selected


def _example_result(project_id, sensors, steps, axis_label):
    path = Path(__file__).resolve().parents[3] / "tools" / "examples" / "simright_load_level_comparison.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    axes = [row for row in payload["table"] if row["key"].startswith("axis:")]
    axis_row = next((row for row in axes if row["label"] == (axis_label or payload["axis_label"])), None)
    if axis_row is None:
        raise ValidationError("文件中不存在所选横轴")
    step_row = next(row for row in payload["table"] if row["key"] == "simright_step")
    count = len(payload["record_indices"])
    selected = _selected_steps(steps, count, step_row["selectOptions"])
    step_row["value"] = [0,1,2,3]
    rows = {row["key"]: row for row in payload["table"]}
    names = [sensor["name"] for sensor in sensors]
    curves = []
    indices = {str(option["value"]): index for index, option in enumerate(step_row["selectOptions"])}
    for name in names:
        test, simulation = rows.get("test:" + name), rows.get("simulation:" + name)
        if test is None or simulation is None:
            raise ValidationError("示例文件中不存在指定测点", {"sensor": name})
        values = [15 if value is None else simulation["value"][indices[str(value)]] for value in selected]
        curves.extend([_series(name + "-试验", axis_row["value"], test["value"]),
                       _series(name + "-仿真", axis_row["value"], values)])
    payload.update(project_id=project_id, is_example=True, data_source="example", axis_label=axis_row["label"],
                   table=axes + [step_row], curves=curves)
    payload["summary"].update(sensor_count=len(names), curve_count=len(curves))
    return payload


def get_result_path(work_condition_id):
    sql = f"SELECT id FROM `t_simulation_file` WHERE work_condition_id = {work_condition_id} AND result_name is not NULL AND is_use='1';"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        path = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
    return str(path[0])+"-0"


def check_wheather_sensor_paired(sensor_id, work_condition_id):
    sql = f"SELECT measuring_point_name, node FROM `db_test_simu_real_test`.`t_measuring_point_info` where test_work_condition_id={work_condition_id} and is_from_test_data ='0' and measuring_point_name = (SELECT measuring_point_name FROM `db_test_simu_real_test`.`t_measuring_point_info` where id={sensor_id});"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        res = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)

    return res==None


def get_node_id_by_sensor_name(sensor_id, work_condition_id):
    # sensor_id = 78888888888
    sql = f"SELECT measuring_point_name, node FROM `db_test_simu_real_test`.`t_measuring_point_info` where test_work_condition_id={work_condition_id} and is_from_test_data ='0' and measuring_point_name = (SELECT measuring_point_name FROM `db_test_simu_real_test`.`t_measuring_point_info` where id={sensor_id});"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        res = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
    print(f"get_node_id_by_sensor_name:{res}")
    if res:
        sensor_name, pair_node = res
    else:
        sensor_name, pair_node = None, None
    print(f"sensor_name:{sensor_name}, pair_node:{pair_node}")
    return sensor_name, pair_node


def get_load_cases(res_path):
    url = "http://192.168.1.7:5000/sq/applications/3dlite/api/v1/query"
    payload = {
        "name": "loadcases",
        "args": {
            "filename": res_path
        }
    }
    try:
        res = requests.post(url, json=payload, timeout=100)
        res.raise_for_status()
        result = res.json()
        # print(len(result['data'][0]['data']))
        load_cases = result['data']['value']
        print(f"loadcase count:{len(load_cases)}")
        odb_load_cases = []
        for load_case in load_cases:
            print(f"    load_case:{load_case['name']}")
            odb_load_cases.append(load_case['name'])
    except requests.exceptions.RequestException as err:
        print(f"loadcases exception:{err}")
    return odb_load_cases


def get_second_pk_stress_projection(sensor_id, node_id, res_path, load_case_name, measure_id, selected_steps, sensor_name, work_condition_id):
    """
    """
    print("enter get_second_pk_stress_projection function")
    # step 1: 先看是否已经存储了结果
    # sql = f"SELECT extra_json FROM `t_measuring_point_info` WHERE id = {sensor_id}"
    sql = f"SELECT extra_json FROM `db_test_simu_real_test`.`t_measuring_point_info` where test_work_condition_id={work_condition_id} and is_from_test_data ='0' and measuring_point_name = (SELECT measuring_point_name FROM `db_test_simu_real_test`.`t_measuring_point_info` where id={sensor_id});"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        step_res = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)

    print(f"step result map:{step_res}")
    if step_res[0] is not None:
        step_res_json = json.loads(step_res[0])
        if step_res_json:
            print(f"step_res_json:{step_res_json}")
            vals = []
            for selecte_step in selected_steps:
                if selecte_step:
                    val = step_res_json[selecte_step]
                else:
                    val = None
                vals.append(val)
            print(f"selected vals:{vals}")
            return vals, None

    # step 2: 遍历所有 loadcase，然后提取最后一个结果
    # 测试使用不同的 load_case_name 得到的结果都一样，应该是数巧的问题，所以都使用 load_case_name
    url = "http://192.168.1.7:5000/sq/applications/3dlite/api/v1/query"
    var_list = ["S.EL.11", "S.EL.12", "S.EL.13", "S.EL.22", "S.EL.23", "S.EL.33"]

    payload = {
        "name": "XYCurveData1",
        "args": {
            "filename": res_path,
            "nodeIds": [
                node_id
            ],
            "loadcaseName": load_case_name,
            "xVarName": "var_time",
            "yVarName": "S.EL.12",
            "coordSystemType": 1,
            "stateIds": []
        }
    }
    # print(f"payload:\n{payload}")

    pks = []
    for ii in range(len(load_case_name)):
        pks.append(np.zeros((3,3), dtype=float))

    stress = {"S.EL.11":{}, "S.EL.12":{}, "S.EL.13":{}, "S.EL.22":{}, "S.EL.23":{}, "S.EL.33":{}}

    for iter_idx_load_case in range(len(load_case_name)):
        for iter_var_idx in range(len(var_list)):
            payload['args']['yVarName'] = var_list[iter_var_idx]
            payload['args']['loadcaseName'] = load_case_name[iter_idx_load_case]
            # print(f"{var_list[iter_var_idx]} payload:\n{payload}")
            res = requests.post(url, json=payload, timeout=1000)
            res.raise_for_status()
            result = res.json()
            last_res = result['data'][0]['data'][-1][1]
            # sys.exit(100)
            stress[var_list[iter_var_idx]][iter_idx_load_case] = last_res

    print(f"collection stress: \n{stress}")

    # step 3: 将应力结果分配给各个 pk 应力
    for idx in range(len(load_case_name)):
        pks[idx][0,0] = stress["S.EL.11"][idx]
        pks[idx][0,1] = stress["S.EL.12"][idx]
        pks[idx][0,2] = stress["S.EL.13"][idx]
        pks[idx][1,0] = stress["S.EL.12"][idx]
        pks[idx][1,1] = stress["S.EL.22"][idx]
        pks[idx][1,2] = stress["S.EL.23"][idx]
        pks[idx][2,0] = stress["S.EL.13"][idx]
        pks[idx][2,1] = stress["S.EL.23"][idx]
        pks[idx][2,2] = stress["S.EL.33"][idx]

    print(f"pks: \n{pks}")

    # step 4: 提取传感器测量方向
    sql = f"SELECT x_angle,y_angle,z_angle FROM t_measuring_point_info WHERE measuring_point_name = '{sensor_name}' and is_from_test_data=0;"
    print(sql)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        res = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)

    step_result_map = {}
    x_angle, y_angle, z_angle = res
    sensor_vector = np.asarray([x_angle, y_angle, z_angle], dtype=float)
    print(f"sesor_vector:{sensor_vector}")

    # step 5: 投影到制定方向
    for idx in range(len(pks)):
        iter_pk = pks[idx]
        cal_val = sensor_vector@iter_pk@sensor_vector.T
        step_result_map[load_case_name[idx]] = cal_val
        # print(f"calculate value:{cal_val}")
        # save result
        # result_dict[step_name] = cal_val
    vals = []
    for selecte_step in selected_steps:
        if selecte_step:
            val = step_result_map[selecte_step]
        else:
            val = None
        vals.append(val)
    return vals, step_result_map


def save_calculate_result_to_db(result, sensor_id):
    """
    """
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    res_map = json.dumps(result)
    try:
        sql = f"UPDATE t_measuring_point_info SET extra_json = %s WHERE id=%s;"
        cursor.execute(sql,(res_map, sensor_id))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def get_test_curve(sensor_id, measure_id, sensor_name):
    sql = f"SELECT measuring_point_name FROM `db_test_simu_real_test`.`t_measuring_point_info` where id={sensor_id};"
    print(sql)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        sensor_name = cursor.fetchone()[0]
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)

    # print(f"sensor_name:{sensor_name}")
    wrapper_sensor_name = '"' + str(sensor_name) + '"'
    sql = f"SELECT load_level, reading_value FROM `db_test_simu_real_test`.`t_mt_py_test_load_level_data` where project_id={measure_id} and sensor={wrapper_sensor_name};"
    print(sql)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        lines = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
    lines = np.asarray(lines, dtype=float)
    # print(lines)

    res = {}
    step = lines[:,0].tolist()
    sensor_value = lines[:,1].tolist()
    res['curves'] = []
    xaxis = [str(ii) for ii in step]
    points = [[xaxis[ii], sensor_value[ii]] for ii in range(len(xaxis))]
    res['curves'].append({"label":sensor_name,"xaxis":xaxis,"data":points})

    print("===========curves===========")
    print(res)
    return res


def get_table_content(odb_load_case,measure_id):
    sql = f"SELECT sensor,count(*) FROM `t_mt_py_test_load_level_data` where project_id={measure_id} GROUP BY sensor limit 1;"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        res = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)

    sensor_name, step_count = res

    sql = f"SELECT extra_json FROM `t_mt_py_project_config` where pid={measure_id};"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        extra_json = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
    json_data = json.loads(extra_json[0])
    axis_rows = json_data["test_load_level_import"]["axis_rows"]
    wrapper_sensor_name = '"' + str(sensor_name) + '"'
    sql = f"SELECT load_level, reading_value FROM `db_test_simu_real_test`.`t_mt_py_test_load_level_data` where project_id={measure_id} and sensor={wrapper_sensor_name};"
    print(sql)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        lines = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
    lines = np.asarray(lines, dtype=float)
    res = {}
    step = lines[:,0].tolist()
    sensor_value = lines[:,1].tolist()
    content = [{"label":odb_load_case[ii], "value":ii} for ii in range(len(odb_load_case))]
    select_index = []
    for ii in range(len(step)):
        if ii < len(content):
            select_index.append(ii)
        else:
            select_index.append(None)
    axis_rows.append({"label":"odb-step", "type":"select", "selectOptions":content, "value":select_index})
    res['table'] = axis_rows
    print(res)
    return res


def get_simright_test_load_level_comparison(project_id, simright_base_url=None,
        profile="markdown", axis_label=None, sensors=None, steps=None, shell_layer="bottom", import_sha256=None,
        example_mode=False, work_condition_id=None, measurement_id=None):
    example_mode = False
    sensors = sensors or []
    if len({sensor["name"] for sensor in sensors}) != len(sensors):
        raise ValidationError("测点名称不能重复")

    print(f"work_condition_id:{work_condition_id}, measurement_id:{measurement_id}, project_id:{project_id}")
    if example_mode:
        return _example_result(project_id, sensors, steps, axis_label)

    print("sensors:", sensors)
    if len(sensors) != 0:
        measure_id = measurement_id
        res = {}
        for sensor in sensors:
            sensor_id = int(sensor['name'])
            sensor_name, pair_node = get_node_id_by_sensor_name(sensor_id, work_condition_id)
            sensor_iter_res = get_test_curve(sensor_id, measure_id, sensor_name)
            if sensor_iter_res is None:
                continue
            if len(res) == 0:
                res = sensor_iter_res
            else:
                res['curves'].extend(sensor_iter_res['curves'])
            if pair_node is not None:
                odb_path = get_result_path(work_condition_id)
                odb_load_case = get_load_cases(odb_path)
                # selected_steps = [odb_load_case[step] for step in steps]
                selected_steps = []
                for step in steps:
                    if step is None:
                        selected_steps.append(None)
                    else:
                        selected_steps.append(odb_load_case[step])
                result, step_result_map = get_second_pk_stress_projection(sensor_id, pair_node, odb_path, odb_load_case, measure_id, selected_steps, sensor_name, work_condition_id)
                if step_result_map:
                    save_calculate_result_to_db(step_result_map, sensor_id)
                    print(f"saved extra_json: {step_result_map}")
                test_curve = sensor_iter_res['curves'][0]
                test_curve_xaxis = test_curve["xaxis"]
                cal_points = [[test_curve_xaxis[ii],result[ii]] for ii in range(len(test_curve_xaxis))]
                cal_curve = {"label":sensor_name+" cal", "xaxis":test_curve["xaxis"], "data":cal_points}
                res['curves'].append(cal_curve)
    else:
        print("get content 111")
        measure_id = measurement_id
        odb_path = get_result_path(work_condition_id)
        odb_load_case = get_load_cases(odb_path)
        res = get_table_content(odb_load_case, measure_id)

    print(f"load level curves:{res}")
    return res


