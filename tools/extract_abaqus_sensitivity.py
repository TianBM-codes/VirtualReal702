"""Abaqus Python entry point for nodal design-response sensitivity extraction."""
import argparse
import json
import os

import numpy as np
from abaqusConstants import NODAL
from odbAccess import openOdb


def nodal_arrays(field, instance_name):
    labels, values = [], []
    for block in field.bulkDataBlocks:
        if str(block.position) != str(NODAL):
            continue
        if block.instance is None or block.instance.name != instance_name:
            continue
        try:
            data = np.asarray(block.data, dtype=np.float32)
        except Exception:
            data = np.asarray(block.dataDouble, dtype=np.float32)
        node_labels = np.asarray(block.nodeLabels, dtype=np.int32)
        if len(node_labels):
            labels.append(node_labels)
            values.append(data.reshape(len(node_labels), -1))
    if not labels:
        return None
    labels = np.concatenate(labels)
    values = np.concatenate(values)
    order = np.argsort(labels, kind="stable")
    labels, values = labels[order], values[order]
    repeated = np.flatnonzero(labels[1:] == labels[:-1])
    if len(repeated) and not np.array_equal(
            values[repeated], values[repeated + 1], equal_nan=True):
        raise ValueError("Conflicting nodal values for " + instance_name)
    unique = np.r_[True, labels[1:] != labels[:-1]]
    return labels[unique], values[unique]


def extract(request, output_dir):
    odb = openOdb(request["odb_path"], readOnly=True)
    try:
        prefix = request["field_prefix"]
        step_name = request.get("step")
        if not step_name:
            candidates = [
                name for name in odb.steps.keys()
                if len(odb.steps[name].frames) and any(
                    field.startswith(prefix)
                    for field in odb.steps[name].frames[-1].fieldOutputs.keys())
            ]
            if len(candidates) != 1:
                raise ValueError("Select one sensitivity step explicitly: " + str(candidates))
            step_name = candidates[0]
        step = odb.steps[step_name]
        index = request.get("frame")
        index = len(step.frames) - 1 if index is None else int(index)
        if index < 0 or index >= len(step.frames):
            raise ValueError("Sensitivity frame is out of range: " + str(index))
        frame = step.frames[index]
        derivatives = sorted(name for name in frame.fieldOutputs.keys() if name.startswith(prefix))
        if not derivatives:
            raise ValueError("No sensitivity derivatives for " + prefix + " in " + step_name)
        response = request["response_field"]
        names = [response] + derivatives
        fields = []
        for field_index, name in enumerate(names):
            field = frame.fieldOutputs[name]
            blocks = []
            for instance_index, instance in enumerate(request["instances"]):
                arrays = nodal_arrays(field, instance)
                if arrays is None:
                    continue
                labels, data = arrays
                stem = "{}_{}".format(field_index, instance_index)
                np.save(os.path.join(output_dir, stem + "_labels.npy"), labels)
                np.save(os.path.join(output_dir, stem + "_values.npy"), data)
                blocks.append({"instance": instance, "labels": stem + "_labels.npy",
                               "values": stem + "_values.npy"})
            if not blocks:
                raise ValueError("No supported nodal data for " + name)
            fields.append({"name": name, "components": list(field.componentLabels) or ["value"],
                           "blocks": blocks})
        result = {"step": step_name, "original_frame": index,
                  "frame_value": float(frame.frameValue),
                  "description": str(frame.description),
                  "procedure": str(getattr(step, "procedureType", None) or
                                   getattr(step, "procedure", "")).lstrip("*"),
                  "fields": fields}
        with open(os.path.join(output_dir, "metadata.json"), "w") as stream:
            json.dump(result, stream)
        print("Sensitivity extraction complete: {} fields, step {}, frame {}".format(
            len(fields), step_name, index))
    finally:
        odb.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with open(args.request) as stream:
        extract(json.load(stream), args.out)
