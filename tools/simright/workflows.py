"""Auditable coordinate matching and strictly aligned six-component histories."""

import math

from .client import SimRightError
from .projection import COMPONENTS, project_stress, project_strain, vector3


def _records(value, label):
    # DATA-API examples return arrays directly, unlike some parameter tables.
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise SimRightError("%s data must be an array of objects" % label)
    return value


def match_sensors(client, filename, sensors, *, max_distance, **options):
    """One nodeId request per sensor: the supplied API has no input-index contract.

    sensors: [{sensorId, position:[x,y,z], ...}]. Match in the coordinate/frame
    system returned as nodePos, never silently mix nodePosBase and nodePos.
    A hit without partId is retained but must be resolved before ambiguous queries.
    """
    if not math.isfinite(max_distance) or max_distance < 0:
        raise ValueError("max_distance must be finite and nonnegative")
    sensors = list(sensors)
    ids = [s["sensorId"] for s in sensors]
    if len(set(ids)) != len(ids):
        raise ValueError("sensorId must be unique")
    positions = [vector3(s["position"], "sensor position") for s in sensors]
    matches = []
    for sensor, pos in zip(sensors, positions):
        candidates = _records(client.node_ids(filename, [list(pos)], **options), "nodeId")
        row = {"sensorId": sensor["sensorId"], "position": list(pos),
               "maxDistance": max_distance, "matched": False,
               "matchOptions": dict(options)}
        if not candidates:
            row["reason"] = "no_candidate"
        elif len(candidates) != 1:
            row["reason"] = "ambiguous_candidates"
            row["candidates"] = candidates
        else:
            hit = candidates[0]
            if "nodeId" not in hit or "nodePos" not in hit:
                raise SimRightError("nodeId hit is missing nodeId/nodePos")
            node_pos = vector3(hit["nodePos"], "nodePos")
            distance = math.dist(pos, node_pos)
            row.update({"nodeId": hit["nodeId"], "partId": hit.get("partId"),
                        "nodePos": list(node_pos), "distance": distance, "raw": hit,
                        "matched": distance <= max_distance})
            if distance > max_distance:
                row["reason"] = "distance_exceeded"
        matches.append(row)
    return matches


def _scoped_options(match, part_ori_ids, options):
    result = dict(options)
    part_id = match.get("partId")
    if part_id is not None:
        # partId is the internal id. entitiesIds expects oriId; never substitute it.
        if part_ori_ids is None or part_id not in part_ori_ids:
            raise ValueError("Resolve partId -> oriId from assemble.parts first")
        result.update(entityType="Part", entitiesIds=[part_ori_ids[part_id]])
    return result


def sensor_history(client, filename, matches, y_var_name, *, part_ori_ids=None,
                   allow_undocumented_part_scope=False, **options):
    """Join matches to curves by nodeId/partId, preserving rejected sensor rows.

    When nodeId omitted partId, more than one returned curve is rejected rather
    than selecting a part arbitrarily. Does not perform spatial interpolation.
    """
    result = []
    for match in matches:
        row = dict(match)
        if match["matched"]:
            if match.get("partId") is not None and not allow_undocumented_part_scope:
                raise ValueError("XYCurveData1 does not document part filtering; use sensor_values "
                                 "or confirm vendor support with allow_undocumented_part_scope=True")
            scoped = _scoped_options(match, part_ori_ids, options)
            curves = _records(client.xy_curves(filename, [match["nodeId"]], y_var_name,
                                               **scoped), "XYCurveData1")
            curves = [c for c in curves if c.get("nodeId") == match["nodeId"]
                      and (match.get("partId") is None or c.get("partId") is None
                           or c["partId"] == match["partId"])]
            if len(curves) != 1:
                raise SimRightError("Missing or ambiguous curve for sensor %r" % match["sensorId"])
            row["curve"] = curves[0]
        result.append(row)
    return result


def sensor_values(client, filename, matches, var_name, *, part_ori_ids=None, **options):
    """Fetch matched node values with the documented nodeInfo part scope.

    Preserve every state record and multi-valued corner result. Never silently
    average corner values or identify nodes by response-array position.
    """
    result = []
    for match in matches:
        row = dict(match)
        if match["matched"]:
            scoped = _scoped_options(match, part_ori_ids, options)
            records = _records(client.node_info(filename, [match["nodeId"]], var_name,
                                                **scoped), "nodeInfo")
            records = [r for r in records if r.get("nodeId") == match["nodeId"]
                       and (match.get("partId") is None or r.get("partId") == match["partId"])]
            if not records or len({r.get("partId") for r in records}) != 1:
                raise SimRightError("Missing or ambiguous nodeInfo result for sensor %r" % match["sensorId"])
            row["results"] = records
        result.append(row)
    return result


def _curve_map(raw, component, node_ids):
    indexed = {}
    for curve in _records(raw, "XYCurveData1/%s" % component):
        if "nodeId" not in curve or not isinstance(curve.get("data"), list):
            raise SimRightError("Curve must contain nodeId and data")
        key = (curve.get("partId"), curve["nodeId"])
        if key in indexed:
            raise SimRightError("Duplicate node/part curve: %r" % (key,))
        pairs = []
        for pair in curve["data"]:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise SimRightError("Curve sample must be [x,y]")
            try:
                x, y = float(pair[0]), float(pair[1])
            except (TypeError, ValueError) as exc:
                raise SimRightError("Curve sample is not real numeric data") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise SimRightError("Curve sample must be finite")
            pairs.append((x, y))
        if not pairs:
            raise SimRightError("Empty component history")
        if any(b[0] <= a[0] for a, b in zip(pairs, pairs[1:])):
            raise SimRightError("x coordinates must be strictly increasing; repeated x cannot identify frames")
        indexed[key] = {"pairs": pairs, "xVarName": curve.get("xVarName")}
    if set(k[1] for k in indexed) != set(node_ids):
        raise SimRightError("Returned node set differs from requested nodes")
    # When partId is absent duplicate local node labels cannot be distinguished.
    return indexed


def project_node_history(client, filename, node_ids, direction, *, kind="stress",
                         component_vars=None, zero_components=(), shear_convention=None,
                         local_to_global=None, **options):
    """Fetch six XY histories and project only matching node/part/x samples.

    component_vars overrides all nonzero variable names. Explicit zero_components
    permits a caller-declared 2D assumption. No interpolation or silent truncation.
    The same loadcase/state/averaging/layer/coordinate options go to every request.
    """
    if kind not in ("stress", "strain"):
        raise ValueError("kind must be stress or strain")
    if kind == "strain" and shear_convention not in ("tensor", "engineering"):
        raise ValueError("strain requires shear_convention=tensor or engineering")
    node_ids = list(node_ids)
    if not node_ids or len(set(node_ids)) != len(node_ids):
        raise ValueError("node_ids must be nonempty and unique")
    zero = set(zero_components)
    if not zero <= set(COMPONENTS) or len(zero) == 6:
        raise ValueError("zero_components must be a proper subset of the six components")
    prefix = "S.EL." if kind == "stress" else "E.EL."
    variables = (dict(component_vars) if component_vars is not None else
                 {c: prefix + c for c in COMPONENTS})
    if not (set(COMPONENTS) - zero) <= set(variables):
        raise ValueError("component_vars is missing nonzero components")
    # Validate the tensor direction/frame before any HTTP call.
    projector = project_stress if kind == "stress" else project_strain
    kwargs = {"local_to_global": local_to_global}
    if kind == "strain":
        kwargs["shear_convention"] = shear_convention
    projector(dict.fromkeys(COMPONENTS, 0.), direction, **kwargs)
    fetched = {}
    for c in COMPONENTS:
        if c not in zero:
            fetched[c] = _curve_map(client.xy_curves(filename, node_ids, variables[c], **options),
                                    c, node_ids)
    reference = next(iter(fetched.values()))
    keys = set(reference)
    for curves in fetched.values():
        if set(curves) != keys:
            raise SimRightError("Component histories have different node/part identities")
    output = []
    for key, ref in reference.items():
        xs = [p[0] for p in ref["pairs"]]
        for curves in fetched.values():
            record = curves[key]
            if record["xVarName"] != ref["xVarName"] or [p[0] for p in record["pairs"]] != xs:
                raise SimRightError("Component x axes differ; projection would mix states")
        samples = []
        for i, x in enumerate(xs):
            components = {c: 0. if c in zero else fetched[c][key]["pairs"][i][1]
                          for c in COMPONENTS}
            samples.append({"x": x, "components": components,
                            **projector(components, direction, **kwargs)})
        output.append({"partId": key[0], "nodeId": key[1], "kind": kind,
                       "xVarName": ref["xVarName"], "samples": samples,
                       "componentVariables": {c: variables[c] for c in COMPONENTS if c not in zero},
                       "assumedZeroComponents": sorted(zero), "requestOptions": dict(options),
                       "localToGlobal": local_to_global})
    return output
