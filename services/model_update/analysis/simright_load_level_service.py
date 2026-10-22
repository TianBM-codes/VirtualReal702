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
    step_row["value"] = selected
    rows = {row["key"]: row for row in payload["table"]}
    names = [sensor["name"] for sensor in sensors]
    curves = []
    indices = {str(option["value"]): index for index, option in enumerate(step_row["selectOptions"])}
    for name in names:
        test, simulation = rows.get("test:" + name), rows.get("simulation:" + name)
        if test is None or simulation is None:
            raise ValidationError("示例文件中不存在指定测点", {"sensor": name})
        values = [None if value is None else simulation["value"][indices[str(value)]] for value in selected]
        curves.extend([_series(name + "-试验", axis_row["value"], test["value"]),
                       _series(name + "-仿真", axis_row["value"], values)])
    payload.update(project_id=project_id, is_example=True, data_source="example", axis_label=axis_row["label"],
                   table=axes + [step_row], curves=curves)
    payload["summary"].update(sensor_count=len(names), curve_count=len(curves))
    return payload


def get_simright_test_load_level_comparison(project_id, simright_base_url=None,
        profile="markdown", axis_label=None, sensors=None, steps=None, shell_layer="bottom", import_sha256=None,
        example_mode=True):
    sensors = sensors or []
    if len({sensor["name"] for sensor in sensors}) != len(sensors):
        raise ValidationError("测点名称不能重复")
    if example_mode:
        return _example_result(project_id, sensors, steps, axis_label)
    config = get_project_config(project_id).get("extra_json", {}).get("simright", {})
    filename = config.get("filename")
    simright_base_url = simright_base_url or config.get("base_url")
    if not simright_base_url or not filename:
        raise ValidationError("请在项目 extra_json.simright 中配置 filename 和 base_url，地址也可通过 simright_base_url 传入")
    metadata, readings = load_test_data(project_id)
    if import_sha256 is not None and import_sha256 != metadata["sha256"]:
        raise ConflictError("试验 TXT 已更新，请刷新表格后重新选择仿真 step")
    count = metadata["sample_count"]
    axis_label = axis_label or metadata["axis_label"]
    axis = next((row["value"] for row in metadata["axis_rows"] if row["label"] == axis_label), None)
    if axis is None:
        raise ValidationError("文件中不存在所选横轴", {"axis_label": axis_label})
    for binding in sensors:
        if binding["name"] not in readings:
            raise ValidationError("TXT 中不存在指定测点", {"sensor": binding["name"]})
    if steps is not None and len(steps) != count:
        raise ValidationError("steps 长度必须与试验有效记录数一致", {"sample_count": count})
    simulated = {item["name"]: [None] * count for item in sensors}
    selected, resolved, issues = [None] * count, [], []
    try:
        with SimRightClient(str(simright_base_url), profile=profile) as client:
            options = _loadcases(client, filename)
            selected = _selected_steps(steps, count, options)
            by_id = {str(step["value"]): step for step in options}
            bindings = _resolve_bindings(project_id, sensors, config) if sensors and any(value is not None for value in selected) else []
            cache = {}
            for index, value in enumerate(selected):
                if value is None:
                    continue
                step = by_id[str(value)]
                for binding in bindings:
                    try:
                        value, identity = _simulation_value(client, filename, binding, step, {}, shell_layer, cache)
                        simulated[binding["name"]][index] = value
                        resolved.append({"sensor": binding["name"], "sample_index": index,
                                         "loadcase_id": step["value"], "part_id": identity[0], "state_id": identity[1]})
                    except (SimRightError, ValueError, TypeError) as exc:
                        issues.append({"sensor": binding["name"], "sample_index": index, "message": str(exc)})
    except SimRightError as exc:
        raise AppError("数巧接口请求失败", code="SIMRIGHT_ERROR", status_code=502,
                       details={"reason": str(exc)}) from exc
    table = [{"key": "axis:%s" % index, "label": row["label"], "value": row["value"]}
             for index, row in enumerate(metadata["axis_rows"])]
    names = [item["name"] for item in sensors]
    table.append({"key": "simright_step", "label": "仿真step", "type": "select",
                  "selectOptions": [{"label": s["label"], "value": s["value"]} for s in options], "value": selected})
    curves = [_series(name + "-试验", axis, readings[name]) for name in names]
    curves.extend(_series(name + "-仿真", axis, values) for name, values in simulated.items())
    return {"project_id": project_id, "axis_label": axis_label, "unit": metadata.get("unit"),
            "import_sha256": metadata["sha256"], "record_indices": list(range(count)),
            "table": table, "curves": curves, "resolved_states": resolved, "issues": issues,
            "summary": {"sample_count": count, "sensor_count": len(names), "curve_count": len(curves)}}
