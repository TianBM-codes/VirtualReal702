# -*- coding: utf-8 -*-
"""Abaqus Python 2.7/3 worker for the sensor history API.

Reuses odb_node_history's extraction and transformation rules; never rematches
coordinates. JSON files isolate Abaqus from the web server's Python runtime.
"""
from __future__ import print_function
import io
import json
import math
import sys

import odb_node_history as history


def _native_strings(value):
    # Abaqus 2023's C++ bindings reject Python 2 unicode arguments, including
    # openOdb paths and repository keys loaded from JSON.
    if history.PY2:
        if isinstance(value, dict):
            return dict((_native_strings(key), _native_strings(item)) for key, item in value.items())
        if isinstance(value, list):
            return [_native_strings(item) for item in value]
        if isinstance(value, type(u"")):
            return value.encode("utf-8")
    return value


def _number(value):
    value = float(value)
    return None if math.isnan(value) or math.isinf(value) else value


def extract(odb, request):
    instances = dict((history._canon_inst(name), name) for name in odb.rootAssembly.instances.keys())
    sensors = request["sensors"]
    wanted = set()
    outputs = []
    for sensor in sensors:
        inst = history._canon_inst(sensor["instance_name"])
        node = int(sensor["node_label"])
        if inst not in instances:
            raise ValueError("matched instance not found: " + inst)
        mesh_node = odb.rootAssembly.instances[instances[inst]].getNodeFromLabel(node)
        coords = [_number(value) for value in mesh_node.coordinates]
        coords += [0.0] * (3 - len(coords))
        sensor.update(inst=inst, node=node, dkey=tuple(sensor["direction"]))
        wanted.add((inst, node))
        outputs.append({"name": sensor["name"], "coordinates": sensor["coordinates"],
                        "direction": sensor["direction"],
                        "match": {"instance_name": inst, "part_name": history._part_name(odb, instances[inst]),
                                  "node_label": node, "node_coordinates": coords,
                                  "distance": sensor["distance"]}, "steps": []})
    regions = history.build_regions(odb, sorted(wanted), instances)
    csyses = {}

    def csys_for(direction):
        if direction not in csyses:
            csyses[direction] = history.make_direction_csys(odb.rootAssembly, direction, len(csyses))
        return csyses[direction]

    for step_index, step_name in enumerate(odb.steps.keys()):
        step = odb.steps[step_name]
        step_outputs = []
        for output in outputs:
            item = {"step": step_name, "step_index": step_index,
                    "procedure": str(getattr(step, "procedure", "")), "frames": []}
            output["steps"].append(item)
            step_outputs.append(item)
        for frame_index, frame in enumerate(step.frames):
            frame_outputs = []
            for output in step_outputs:
                item = {"frame_idx": frame_index, "frame_value": _number(frame.frameValue),
                        "frame_desc": frame.description, "fields": []}
                output["frames"].append(item)
                frame_outputs.append(item)
            for requested_field in request["fields"]:
                name = requested_field
                if name == "E" and name not in frame.fieldOutputs.keys() and "LE" in frame.fieldOutputs.keys():
                    name = "LE"
                results, projections, errors, projection_errors = {}, {}, {}, {}
                present = name in frame.fieldOutputs.keys()
                if present:
                    fo = frame.fieldOutputs[name]
                    for region, inst in regions:
                        try:
                            if name in history.TENSOR_FIELDS:
                                sub = history._element_nodal_subset(fo, region)
                                results.update(history.rows_for_element_nodal(fo, sub, inst, wanted))
                                directions = {}
                                for sensor in sensors:
                                    if sensor["inst"] == inst:
                                        directions.setdefault(sensor["dkey"], set()).add(sensor["node"])
                                for direction, nodes in directions.items():
                                    try:
                                        projections.update(history.project_element_nodal(
                                            sub, inst, {direction: nodes}, csys_for))
                                    except Exception as exc:
                                        projection_errors[(inst, direction)] = str(exc)
                            else:
                                results.update(history.rows_for_nodal(fo, region, inst, wanted))
                        except Exception as exc:
                            errors[inst] = str(exc)
                    if name not in history.TENSOR_FIELDS:
                        projections = history.project_nodal(results, sensors)
                for sensor, output in zip(sensors, frame_outputs):
                    inst, node = sensor["inst"], sensor["node"]
                    rows = []
                    for key in sorted(results):
                        if key[:2] != (inst, node):
                            continue
                        labels, data, count = results[key]
                        projected = projections.get((inst, node, key[2], sensor["dkey"]))
                        rows.append({"section_point": key[2],
                                     "components": dict((label, _number(value)) for label, value in zip(labels, data)),
                                     "projection": None if projected is None else _number(projected[0]),
                                     "n_elems": count,
                                     "status": "ok" if projected is not None else "projection_failed"})
                    status = ("missing_field" if not present else "extraction_failed" if inst in errors
                              else "missing_node_data" if not rows else
                              "projection_failed" if any(row["status"] != "ok" for row in rows) else "ok")
                    item = {"requested_field": requested_field, "field": name, "status": status, "values": rows}
                    message = errors.get(inst) or projection_errors.get((inst, sensor["dkey"]))
                    if message:
                        item["message"] = message
                    output["fields"].append(item)
    return {"sensors": outputs}


def main():
    with io.open(sys.argv[1], "r", encoding="utf-8") as stream:
        request = _native_strings(json.load(stream))
    odb = history.odbAccess.openOdb(request["odb"], readOnly=True)
    try:
        payload = extract(odb, request)
    finally:
        odb.close()
    with io.open(sys.argv[2], "w", encoding="utf-8") as stream:
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False)
        stream.write(encoded.decode("ascii") if history.PY2 else encoded)


if __name__ == "__main__":
    main()
