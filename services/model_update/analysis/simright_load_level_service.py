"""Align explicitly selected SimRight states to ordered test records."""
import math
import json
from pathlib import Path
from decimal import Decimal

from src.l3.core.errors import AppError, ConflictError, ValidationError
from tools.simright import SimRightClient, SimRightError, project_stress, project_strain
from .test_load_level_service import load_test_data


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


def get_simright_test_load_level_comparison(project_id, simright_base_url=None, filename=None,
        profile="markdown", axis_label=None, sensors=None, step_mapping=None, shell_layer="bottom", import_sha256=None,
        example_mode=True):
    if example_mode:
        path = Path(__file__).resolve().parents[3] / "tools" / "examples" / "simright_load_level_comparison.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(project_id=project_id, is_example=True, data_source="example")
        return payload
    if not simright_base_url or not filename:
        raise ValidationError("真实查询需要 simright_base_url 和 filename")
    metadata, readings = load_test_data(project_id)
    if import_sha256 is not None and import_sha256 != metadata["sha256"]:
        raise ConflictError("试验 TXT 已更新，请刷新表格后重新选择仿真 step")
    sensors, step_mapping = sensors or [], step_mapping or []
    count = metadata["sample_count"]
    axis_label = axis_label or metadata["axis_label"]
    axis = next((row["value"] for row in metadata["axis_rows"] if row["label"] == axis_label), None)
    if axis is None:
        raise ValidationError("文件中不存在所选横轴", {"axis_label": axis_label})
    for binding in sensors:
        if binding["name"] not in readings:
            raise ValidationError("TXT 中不存在指定测点", {"sensor": binding["name"]})
    for selection in step_mapping:
        if selection["sample_index"] >= count:
            raise ValidationError("试验记录序号越界")
    # Request models reject duplicate mappings/bindings; keep this service safe
    # for non-HTTP callers as well.
    if len({s["sample_index"] for s in step_mapping}) != len(step_mapping):
        raise ValidationError("同一记录不能重复选择 step")
    if len({s["name"] for s in sensors}) != len(sensors):
        raise ValidationError("测点绑定不能重复")
    simulated = {item["name"]: [None] * count for item in sensors}
    selected, resolved, issues = [None] * count, [], []
    try:
        with SimRightClient(str(simright_base_url), profile=profile) as client:
            steps = _loadcases(client, filename)
            by_id = {str(step["value"]): step for step in steps}
            cache = {}
            for selection in step_mapping:
                step = by_id.get(str(selection["loadcase_id"]))
                if step is None:
                    raise ValidationError("选择的仿真 step 不存在", {"loadcase_id": selection["loadcase_id"]})
                index = selection["sample_index"]
                selected[index] = step["value"]
                for binding in sensors:
                    try:
                        value, identity = _simulation_value(client, filename, binding, step, selection, shell_layer, cache)
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
    table.extend({"key": "test:" + name, "label": name, "value": readings[name]} for name in metadata["sensor_names"])
    table.append({"key": "simright_step", "label": "仿真step", "type": "select",
                  "selectOptions": [{"label": s["label"], "value": s["value"]} for s in steps], "value": selected})
    table.extend({"key": "simulation:" + name, "label": name + "-仿真", "value": values} for name, values in simulated.items())
    names = [item["name"] for item in sensors] if sensors else metadata["sensor_names"]
    curves = [_series(name + "-试验", axis, readings[name]) for name in names]
    curves.extend(_series(name + "-仿真", axis, values) for name, values in simulated.items())
    return {"project_id": project_id, "axis_label": axis_label, "unit": metadata.get("unit"),
            "import_sha256": metadata["sha256"], "record_indices": list(range(count)),
            "table": table, "curves": curves, "resolved_states": resolved, "issues": issues,
            "summary": {"sample_count": count, "sensor_count": len(readings), "curve_count": len(curves)}}
