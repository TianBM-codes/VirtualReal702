#!/usr/bin/env python3
"""SIPESC UNV -> L1 geometry or result-group packer.

This is deliberately separate from the test-data UNV importers.  SIPESC uses
brace-delimited Node / Element / ResultSet blocks and has no material metadata.
"""
import argparse
import json
import os
import sqlite3
import sys
import time

import h5py
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.l1.bdf_pack import FACE_DEFS, init_manifest, mkdirs, safe, str_ds

STEP_NAME = "SIPESC-Static"
DEFAULT_SECTION = "SIPESC_DEFAULT"

# Derived from tools/MeshElementFactory.py.  The output names are the L1/L2
# element families, not the original SIPESC numeric code.
ELEMENT_TYPES = {
    40600: ("C3D4", 4, 4), 40800: ("C3D4", 4, 4),
    40500: ("S4R", 4, 1), 30500: ("S3", 3, 1),
    20100: ("B31", 2, 0), 188: ("B31", 2, 0),
    100600: ("C3D10", 4, 4), 150600: ("C3D15", 6, 5),
    200600: ("C3D20", 8, 6), 130600: ("C3D13", 5, 5),
}


def _parts(line):
    return [x.strip().rstrip(";") for x in line.strip().strip("()").split(",")]


def _result_header(line):
    attrs = {}
    for key in ("name", "target", "type", "varlabels"):
        token = key + '="'
        start = line.find(token)
        if start >= 0:
            start += len(token)
            end = line.find('"', start)
            attrs[key] = line[start:end] if end >= 0 else ""
    return attrs


def read_geometry(path):
    nodes = []
    groups = {}
    skipped = {}
    mode = None
    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        for raw in fp:
            line = raw.strip()
            if line == "{ Node":
                mode = "nodes"
                continue
            if line == "{ Element":
                mode = "elements"
                continue
            if line == "}":
                mode = None
                continue
            if not line.startswith("("):
                continue
            vals = _parts(line)
            if mode == "nodes":
                if len(vals) >= 4:
                    try:
                        nodes.append((int(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])))
                    except ValueError:
                        pass  # The count line has only one value.
            elif mode == "elements" and len(vals) >= 2:
                try:
                    elem_id, type_id = int(vals[0]), int(vals[1])
                except ValueError:
                    continue
                spec = ELEMENT_TYPES.get(type_id)
                if spec is None:
                    skipped[type_id] = skipped.get(type_id, 0) + 1
                    continue
                etype, ncorner, nfaces = spec
                try:
                    conn = [int(x) for x in vals[-ncorner:]]
                except ValueError:
                    skipped[type_id] = skipped.get(type_id, 0) + 1
                    continue
                if len(set(conn)) != ncorner:
                    skipped[type_id] = skipped.get(type_id, 0) + 1
                    continue
                g = groups.setdefault(etype, {"labels": [], "conn": [], "ncorner": ncorner, "nfaces": nfaces})
                g["labels"].append(elem_id)
                g["conn"].append(conn)
    if not nodes:
        raise ValueError("no SIPESC Node records found")
    return nodes, groups, skipped


def pack_geometry(unv_path, workspace):
    nodes, elem_groups, skipped = read_geometry(unv_path)
    if not elem_groups:
        raise ValueError("no supported SIPESC solid/shell/line elements found")
    nodes.sort(key=lambda r: r[0])
    labels = np.asarray([r[0] for r in nodes], dtype=np.int32)
    coords = np.asarray([r[1:] for r in nodes], dtype=np.float64)
    if len(np.unique(labels)) != len(labels):
        raise ValueError("duplicate node labels in SIPESC UNV")
    inst_name = os.path.splitext(os.path.basename(unv_path))[0].upper()
    l1_dir = os.path.join(workspace, "l1")
    geom_dir = os.path.join(l1_dir, "geometry")
    sets_dir = os.path.join(l1_dir, "sets")
    mkdirs(geom_dir)
    mkdirs(sets_dir)
    h5_rel = os.path.join("l1", "geometry", safe(inst_name) + ".h5")
    h5_abs = os.path.join(workspace, h5_rel)
    all_elem_labels = []
    etd_rows = []
    pairs_nr, pairs_el = [], []

    with h5py.File(h5_abs, "w") as f:
        f.create_dataset("nodes/labels", data=labels)
        f.create_dataset("nodes/coords", data=coords)
        str_ds(f, "section_names", [DEFAULT_SECTION])
        for etype, group in sorted(elem_groups.items()):
            elem_labels = np.asarray(group["labels"], dtype=np.int32)
            conn_labels = np.asarray(group["conn"], dtype=np.int32)
            rows = np.searchsorted(labels, conn_labels).astype(np.int32)
            valid = (rows >= 0) & (rows < len(labels)) & (labels[rows] == conn_labels)
            if not np.all(valid):
                raise ValueError("element references an unknown node label")
            grp = f.require_group("elements/" + etype)
            grp.create_dataset("labels", data=elem_labels)
            grp.create_dataset("conn", data=rows)
            grp.create_dataset("section_id", data=np.zeros(len(elem_labels), dtype=np.int32))
            grp.create_dataset("material_name", data=np.full(len(elem_labels), b"", dtype="S64"))
            grp.create_dataset("section_type", data=np.full(len(elem_labels), b"SOLID", dtype="S16"))
            faces = FACE_DEFS.get(etype, [])
            if faces:
                fei, fseq, fnodes = [], [], []
                for ei, row in enumerate(rows):
                    for fi, face in enumerate(faces):
                        fei.append(ei); fseq.append(fi); fnodes.append([int(row[k]) for k in face])
                width = max(len(row) for row in fnodes)
                padded = np.full((len(fnodes), width), -1, dtype=np.int32)
                for i, row in enumerate(fnodes):
                    padded[i, :len(row)] = row
                grp.create_dataset("face_elem_idx", data=np.asarray(fei, dtype=np.int32))
                grp.create_dataset("face_seq", data=np.asarray(fseq, dtype=np.int32))
                grp.create_dataset("face_node_conn", data=padded)
            all_elem_labels.extend(elem_labels.tolist())
            for elem_label, row in zip(elem_labels, rows):
                pairs_nr.extend(row.tolist())
                pairs_el.extend([int(elem_label)] * len(row))
            etd_rows.append((inst_name, etype, len(elem_labels), 0, group["ncorner"], group["nfaces"]))
        sg = f.require_group("sections/" + DEFAULT_SECTION)
        sg.attrs["element_set"] = DEFAULT_SECTION + "_ELEMS"
        sg.attrs["type"] = "DEFAULT"
        sg.attrs["thickness"] = float("nan")
        sg.attrs["material_name"] = ""
        f.create_dataset("instance_sets/element_sets/" + DEFAULT_SECTION + "_ELEMS",
                         data=np.asarray(sorted(all_elem_labels), dtype=np.int32))
        order = np.argsort(np.asarray(pairs_nr, dtype=np.int32), kind="stable")
        pair_nodes = np.asarray(pairs_nr, dtype=np.int32)[order]
        pair_elems = np.asarray(pairs_el, dtype=np.int32)[order]
        offsets = np.zeros(len(labels) + 1, dtype=np.int32)
        np.add.at(offsets[1:], pair_nodes, 1)
        np.cumsum(offsets, out=offsets)
        csr = f.require_group("node_to_elements")
        csr.create_dataset("offsets", data=offsets, compression="gzip")
        csr.create_dataset("elem_label_data", data=pair_elems, compression="gzip")

    with h5py.File(os.path.join(l1_dir, "assembly.h5"), "w") as f:
        grp = f.require_group("instances/" + inst_name)
        grp.create_dataset("transform", data=np.eye(4, dtype=np.float64))
        grp.create_dataset("part_name", data=inst_name.encode("utf-8"))
    with h5py.File(os.path.join(sets_dir, "sets.h5"), "w"):
        pass
    db = init_manifest(workspace)
    db.execute("INSERT OR REPLACE INTO instances VALUES (?,?,?,?,?,?,?,?)",
               (inst_name, inst_name, h5_rel, None, len(labels), len(all_elem_labels),
                json.dumps(coords.min(axis=0).tolist()), json.dumps(coords.max(axis=0).tolist())))
    db.executemany("INSERT OR REPLACE INTO element_type_dist VALUES (?,?,?,?,?,?)", etd_rows)
    db.commit(); db.close()
    print("SIPESC UNV geometry: {} nodes, {} elements, skipped={}".format(len(labels), len(all_elem_labels), skipped))


def read_results(path):
    displacement, stress = {}, {}
    header = None
    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        for raw in fp:
            line = raw.strip()
            if line == "{ ResultSet":
                header = None
                continue
            if header is None and line.startswith("(") and "name=" in line:
                header = _result_header(line)
                continue
            if line == "}":
                header = None
                continue
            if header is None or not line.startswith("("):
                continue
            values = _parts(line)
            try:
                label = int(values[0])
            except (ValueError, IndexError):
                continue
            kind = header.get("type", "").lower()
            name = header.get("name", "").lower()
            try:
                if kind == "vector" and "rotation" not in name and len(values) >= 4:
                    displacement[label] = [float(values[1]), float(values[2]), float(values[3])]
                elif kind == "tensor6" and len(values) >= 7:
                    sx, sy, sz, sxy, syz, szx = [float(x) for x in values[1:7]]
                    stress[label] = (0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2
                                          + 6.0 * (sxy ** 2 + syz ** 2 + szx ** 2))) ** 0.5
            except ValueError:
                continue
    return displacement, stress


def _write_result_h5(path, step, field, components, inst, labels, data):
    with h5py.File(path, "w") as f:
        meta = f.create_group("meta")
        meta.create_dataset("step_name", data=step.encode())
        meta.create_dataset("field_name", data=field.encode())
        meta.create_dataset("field_description", data=b"")
        str_ds(f, "meta/components", components)
        str_ds(f, "meta/invariants", [])
        fi = f.create_group("frame_index")
        fi.create_dataset("frame_values", data=np.array([0.0], dtype=np.float64))
        str_ds(f, "frame_index/descriptions", ["SIPESC static"])
        grp = f.require_group("NODAL/" + inst)
        grp.create_dataset("labels", data=labels)
        grp.create_dataset("data", data=data, chunks=(1, min(len(labels), 8192), data.shape[2]), compression="lzf")


def pack_results(unv_path, workspace, result_group, display_name=None):
    disp, mises = read_results(unv_path)
    if not disp and not mises:
        raise ValueError("no SIPESC displacement or Tensor6 stress ResultSet found")
    db_path = os.path.join(workspace, "manifest.db")
    db = sqlite3.connect(db_path)
    instances = [r[0] for r in db.execute("SELECT instance_name FROM instances ORDER BY instance_name")]
    if len(instances) != 1:
        raise ValueError("SIPESC result append requires exactly one geometry instance")
    inst = instances[0]
    geom_rel = db.execute("SELECT geom_path FROM instances WHERE instance_name=?", (inst,)).fetchone()[0]
    with h5py.File(os.path.join(workspace, geom_rel), "r") as geom:
        labels = np.asarray(geom["nodes/labels"][:], dtype=np.int32)
    out_dir = os.path.join(workspace, "l1", "results", safe(result_group))
    mkdirs(out_dir)
    fields = []
    for field, components, values in (("U", ["U1", "U2", "U3"], disp), ("S", ["Mises"], mises)):
        if not values:
            continue
        ncomp = len(components)
        data = np.full((1, len(labels), ncomp), np.nan, dtype=np.float32)
        matched = 0
        for row, label in enumerate(labels.tolist()):
            value = values.get(int(label))
            if value is not None:
                data[0, row] = value
                matched += 1
        missing = len(labels) - matched
        extra = len(set(values) - set(labels.tolist()))
        print("SIPESC {}: matched={}, geometry_missing={}, result_extra={}".format(field, matched, missing, extra))
        h5_name = safe(STEP_NAME) + "__" + field + ".h5"
        rel = os.path.join("l1", "results", safe(result_group), h5_name)
        _write_result_h5(os.path.join(workspace, rel), STEP_NAME, field, components, inst, labels, data)
        finite = data[np.isfinite(data)]
        fields.append((field, rel, components, float(finite.min()) if finite.size else None,
                       float(finite.max()) if finite.size else None, matched, missing, extra))
    if not fields:
        raise ValueError("SIPESC result values do not match any geometry node")
    db.execute("INSERT OR REPLACE INTO steps (result_group,step_name,step_number,procedure,num_frames,description,nlgeom) VALUES (?,?,?,?,?,?,?)",
               (result_group, STEP_NAME, 1, "STATIC", 1, display_name or result_group, 0))
    db.execute("INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (result_group, STEP_NAME, 0, 0.0, "SIPESC static", None, None, None, 0, 0, 0, None, None))
    for field, rel, components, vmin, vmax, matched, missing, extra in fields:
        db.execute("INSERT OR REPLACE INTO result_blocks (result_group,step_name,field_name,instance_name,position,elem_type,h5_path,label_path,n_entities,n_ip,n_sp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (result_group, STEP_NAME, field, inst, "NODAL", None, "/NODAL/" + inst, "/NODAL/" + inst + "/labels", len(labels), None, None))
        db.execute("INSERT OR REPLACE INTO result_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (result_group, STEP_NAME, field, rel, json.dumps(components), json.dumps([]), json.dumps(["NODAL"]), 0, vmin, vmax, "sipesc_unv"))
    db.execute("INSERT OR REPLACE INTO result_group_meta (result_group,display_name,source_file,consistency_check,created_at) VALUES (?,?,?,?,datetime('now'))",
               (result_group, display_name or result_group, os.path.basename(unv_path), "node-label-warning"))
    db.commit(); db.close()


def main():
    p = argparse.ArgumentParser(description="SIPESC UNV -> L1 packer")
    p.add_argument("--unv", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--mode", choices=("geometry", "results"), required=True)
    p.add_argument("--result-group")
    p.add_argument("--display-name")
    args = p.parse_args()
    if args.mode == "geometry":
        pack_geometry(args.unv, args.workspace)
    else:
        if not args.result_group:
            p.error("--result-group is required for results mode")
        pack_results(args.unv, args.workspace, args.result_group, args.display_name)


if __name__ == "__main__":
    main()
