#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
abaqus_dump.py — Layer 1, Phase 1: ODB → npy + JSON 临时格式

运行方式（需 Abaqus license）:
    abaqus python abaqus_dump.py --odb <path.odb> --out <workspace>

依赖: numpy（Abaqus 内置），odbAccess（Abaqus 内置），os/json/sqlite3（标准库）
不依赖: h5py（由 l1_pack.py 负责写 HDF5）

输出: <workspace>/l1_raw/  临时目录，供 l1_pack.py 读取后写入正式 HDF5
退出码: 0=成功, 1=出错
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import traceback

import numpy as np

try:
    import odbAccess
except ImportError:
    print("ERROR: odbAccess not found. Must run under 'abaqus python'.")
    sys.exit(1)


# ─── Constants ────────────────────────────────────────────────────────────────

ELEM_TYPE_CODE = {
    'S3': 0,   'S3R': 0,   'S6': 0,
    'S4': 1,   'S4R': 1,   'S4R5': 1,  'S8R': 1,  'S8R5': 1,
    'C3D4': 2,  'C3D4H': 2,
    'C3D6': 3,  'C3D6H': 3,
    'C3D8': 4,  'C3D8R': 4,  'C3D8H': 4,  'C3D8RH': 4,
    'C3D10': 5, 'C3D10M': 5, 'C3D10H': 5,
    'C3D15': 6, 'C3D15H': 6,
    'C3D20': 7, 'C3D20R': 7, 'C3D20H': 7, 'C3D20RH': 7,
}

ELEM_N_CORNER = {0: 3, 1: 4, 2: 4, 3: 6, 4: 8, 5: 4, 6: 6, 7: 8}

HIGH_ORDER_CODES = {5, 6, 7}

# Face definitions: code → list of face corner-node-index lists (0-based, corner only)
FACE_DEFS = {
    0: [[0, 1, 2]],
    1: [[0, 1, 2, 3]],
    2: [[0, 1, 2], [0, 3, 1], [1, 3, 2], [2, 3, 0]],
    3: [[0, 1, 2], [3, 5, 4], [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5]],
    4: [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
        [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]],
    5: [[0, 1, 2], [0, 3, 1], [1, 3, 2], [2, 3, 0]],
    6: [[0, 1, 2], [3, 5, 4], [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5]],
    7: [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
        [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]],
}

# Mid-node column indices in full connectivity (0-based)
MIDNODE_INDICES = {
    'S6':    [3, 4, 5],
    'S8R':   [4, 5, 6, 7],
    'S8R5':  [4, 5, 6, 7],
    'C3D10': [4, 5, 6, 7, 8, 9],
    'C3D10M':[4, 5, 6, 7, 8, 9],
    'C3D10H':[4, 5, 6, 7, 8, 9],
    'C3D15': [6, 7, 8, 9, 10, 11, 12, 13, 14],
    'C3D15H':[6, 7, 8, 9, 10, 11, 12, 13, 14],
    'C3D20': [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    'C3D20R':[8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    'C3D20H':[8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    'C3D20RH':[8,9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
}

PROCEDURE_MAP = {
    'STATIC_GENERAL':           'STATIC',
    'STATIC_RIKS':              'STATIC',
    'FREQUENCY':                'FREQUENCY',
    'DYNAMIC_IMPLICIT':         'DYNAMIC',
    'DYNAMIC_EXPLICIT':         'DYNAMIC',
    'DYNAMIC_TEMPDISPLACEMENT': 'DYNAMIC',
    'BUCKLE':                   'BUCKLE',
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='ODB → npy dump (Phase 1)')
    p.add_argument('--odb', required=True)
    p.add_argument('--out', required=True, help='workspace directory')
    return p.parse_args()


def safe(name):
    """Sanitize a name for use as a filesystem path component."""
    return name.replace('/', '__').replace('\\', '__').replace(' ', '_')


def mkdirs(path):
    if not os.path.exists(path):
        os.makedirs(path)


def jdump(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2)


def npsave(path, arr):
    np.save(path, arr)


def _pos_str(position_const):
    s = str(position_const)
    return s.split('.')[-1]


# ─── Transform ────────────────────────────────────────────────────────────────

def get_instance_transform(instance):
    """
    4×4 homogeneous transform (local → global).
    ⚠️ Blocker B: attribute names need PoC verification.
    """
    csys = getattr(instance, 'localCsys', None)
    if csys is None:
        return np.eye(4, dtype=np.float64)
    try:
        origin = np.array(csys.origin, dtype=np.float64)
        x_axis = np.array(csys.xAxis,  dtype=np.float64)
        y_axis = np.array(csys.yAxis,  dtype=np.float64)
        z_axis = np.array(csys.zAxis,  dtype=np.float64)
        T = np.eye(4, dtype=np.float64)
        T[:3, 0] = x_axis
        T[:3, 1] = y_axis
        T[:3, 2] = z_axis
        T[:3, 3] = origin
        return T
    except AttributeError:
        try:
            origin = np.array(csys.translation, dtype=np.float64)
            rot    = np.array(csys.rotation, dtype=np.float64).reshape(3, 3)
            T = np.eye(4, dtype=np.float64)
            T[:3, :3] = rot
            T[:3, 3]  = origin
            return T
        except Exception as e:
            print("WARNING: localCsys read failed for {}: {}".format(instance.name, e))
            return np.eye(4, dtype=np.float64)


# ─── Face computation ─────────────────────────────────────────────────────────

def _newell_normal_batch(face_coords):
    """[M, n_fn, 3] → [M, 3] unit normals via Newell's method."""
    M, n_fn, _ = face_coords.shape
    normals = np.zeros((M, 3), dtype=np.float64)
    for i in range(n_fn):
        c  = face_coords[:, i,              :]
        nc = face_coords[:, (i + 1) % n_fn, :]
        normals[:, 0] += (c[:, 1] - nc[:, 1]) * (c[:, 2] + nc[:, 2])
        normals[:, 1] += (c[:, 2] - nc[:, 2]) * (c[:, 0] + nc[:, 0])
        normals[:, 2] += (c[:, 0] - nc[:, 0]) * (c[:, 1] + nc[:, 1])
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.where(norms > 1e-12, norms, 1.0)


def compute_face_data(etype_code, conn_corner, node_coords):
    """
    Vectorized face data for all elements of one type.

    conn_corner : [M, n_corner] int32  row indices into node_coords
    node_coords : [N, 3] float64

    Returns (face_elem_idx, face_seq, face_node_conn, face_normals)
    """
    face_defs = FACE_DEFS.get(etype_code, [])
    if not face_defs:
        return (np.zeros(0, dtype=np.int32),
                np.zeros(0, dtype=np.uint8),
                np.zeros((0, 1), dtype=np.int32),
                np.zeros((0, 3), dtype=np.float32))

    M      = conn_corner.shape[0]
    max_fn = max(len(fd) for fd in face_defs)

    elem_coords    = node_coords[conn_corner]     # [M, n_corner, 3]
    elem_centroids = elem_coords.mean(axis=1)     # [M, 3]

    all_eidx, all_seq, all_fnc, all_nrm = [], [], [], []

    for seq0, fd in enumerate(face_defs):
        n_fn       = len(fd)
        fd_arr     = np.array(fd, dtype=np.int32)
        face_rows  = conn_corner[:, fd_arr]       # [M, n_fn]
        face_coords = node_coords[face_rows]      # [M, n_fn, 3]

        normals       = _newell_normal_batch(face_coords)
        face_centroid = face_coords.mean(axis=1)
        to_face       = face_centroid - elem_centroids
        flip          = (normals * to_face).sum(axis=1) < 0
        normals[flip] = -normals[flip]

        fnc = np.full((M, max_fn), -1, dtype=np.int32)
        fnc[:, :n_fn] = face_rows

        all_eidx.append(np.arange(M, dtype=np.int32))
        all_seq.append(np.full(M, seq0 + 1, dtype=np.uint8))
        all_fnc.append(fnc)
        all_nrm.append(normals.astype(np.float32))

    return (
        np.concatenate(all_eidx),
        np.concatenate(all_seq),
        np.concatenate(all_fnc,  axis=0),
        np.concatenate(all_nrm,  axis=0),
    )


# ─── INTEGRATION_POINT reshape ────────────────────────────────────────────────

def reshape_ip_block(block):
    """
    Reshape flat IP block into structured arrays.

    Returns
    -------
    u_elems  [M]                int32
    u_ips    [n_ip]             int32
    u_sps    [n_sp]             int32  (empty array if no section points)
    data_nd                     float32
        (M, n_ip, ncomp)        solid
        (M, n_sp, n_ip, ncomp)  shell with section points
    """
    labels_flat = np.array(block.elementLabels,          dtype=np.int32)
    ip_flat     = np.array(block.integrationPointLabels, dtype=np.int32)
    data_flat   = np.array(block.data,                   dtype=np.float32)
    ncomp       = data_flat.shape[1]

    # Attempt to read section-point labels (Blocker A)
    sp_flat = None
    for attr in ('sectionPointNumber', 'sectionPoint', 'sectionPointNumbers'):
        val = getattr(block, attr, None)
        if val is not None:
            try:
                arr = np.array(val, dtype=np.int32)
                if arr.shape == labels_flat.shape:
                    sp_flat = arr
            except Exception:
                pass
            break

    u_elems = np.unique(labels_flat)
    u_ips   = np.unique(ip_flat)
    M, n_ip = len(u_elems), len(u_ips)

    if sp_flat is not None:
        u_sps  = np.unique(sp_flat)
        n_sp   = len(u_sps)
        e_idx  = np.searchsorted(u_elems, labels_flat)
        sp_idx = np.searchsorted(u_sps,   sp_flat)
        ip_idx = np.searchsorted(u_ips,   ip_flat)
        data_nd = np.zeros((M, n_sp, n_ip, ncomp), dtype=np.float32)
        data_nd[e_idx, sp_idx, ip_idx] = data_flat
        return u_elems, u_ips, u_sps, data_nd
    else:
        u_sps  = np.array([], dtype=np.int32)
        e_idx  = np.searchsorted(u_elems, labels_flat)
        ip_idx = np.searchsorted(u_ips,   ip_flat)
        data_nd = np.zeros((M, n_ip, ncomp), dtype=np.float32)
        data_nd[e_idx, ip_idx] = data_flat
        return u_elems, u_ips, u_sps, data_nd


def reshape_element_nodal_block(block):
    """flat ELEMENT_NODAL → (u_elems [M], data_nd [M, n_enodes, ncomp])"""
    labels_flat = np.array(block.elementLabels, dtype=np.int32)
    data_flat   = np.array(block.data,          dtype=np.float32)
    ncomp       = data_flat.shape[1]

    u_elems  = np.unique(labels_flat)
    M        = len(u_elems)
    n_enodes = len(labels_flat) // M

    e_idx    = np.searchsorted(u_elems, labels_flat)
    node_idx = np.zeros(len(labels_flat), dtype=np.int32)
    counts   = np.zeros(M, dtype=np.int32)
    for i, ei in enumerate(e_idx):
        node_idx[i] = counts[ei]
        counts[ei] += 1

    data_nd = np.zeros((M, n_enodes, ncomp), dtype=np.float32)
    data_nd[e_idx, node_idx] = data_flat
    return u_elems, data_nd


# ─── Dump phases ──────────────────────────────────────────────────────────────

def dump_assembly(odb, raw_dir, meta):
    """Write assembly data to l1_raw/assembly/"""
    assembly = odb.rootAssembly
    asm_dir  = os.path.join(raw_dir, 'assembly')
    mkdirs(asm_dir)

    print("  Assembly ...")
    inst_meta = {}
    for inst_name, instance in assembly.instances.items():
        s = safe(inst_name)
        d = os.path.join(asm_dir, 'instances', s)
        mkdirs(d)
        npsave(os.path.join(d, 'transform.npy'), get_instance_transform(instance))
        with open(os.path.join(d, 'part_name.txt'), 'w') as f:
            f.write(instance.partName)
        inst_meta[inst_name] = {'part_name': instance.partName}

    meta['instances'] = inst_meta

    # Assembly node sets (per-instance split)
    for set_name, ns in assembly.nodeSets.items():
        by_inst = {}
        for node in ns.nodes:
            by_inst.setdefault(node.instanceName, []).append(node.label)
        for inst_n, labels in by_inst.items():
            d = os.path.join(asm_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            npsave(os.path.join(d, 'node_labels.npy'),
                   np.array(sorted(labels), dtype=np.int32))

    # Assembly element sets (per-instance split)
    for set_name, es in assembly.elementSets.items():
        by_inst = {}
        for elem in es.elements:
            by_inst.setdefault(elem.instanceName, []).append(elem.label)
        for inst_n, labels in by_inst.items():
            d = os.path.join(asm_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            path = os.path.join(d, 'elem_labels.npy')
            if not os.path.exists(path):
                npsave(path, np.array(sorted(labels), dtype=np.int32))

    print("    done.")


def dump_geometry(odb, raw_dir, meta):
    """Write per-instance geometry to l1_raw/geom/<inst>/"""
    assembly = odb.rootAssembly
    geom_dir = os.path.join(raw_dir, 'geom')
    mkdirs(geom_dir)

    geom_meta = {}   # populated into meta['geom']

    for inst_name, instance in assembly.instances.items():
        print("  Geom: {} ...".format(inst_name))
        s  = safe(inst_name)
        d  = os.path.join(geom_dir, s)
        mkdirs(d)

        # Nodes (sorted ascending by label)
        raw_labels = np.array([n.label       for n in instance.nodes], dtype=np.int32)
        raw_coords = np.array([n.coordinates for n in instance.nodes], dtype=np.float64)
        sort_idx   = np.argsort(raw_labels)
        node_labels = raw_labels[sort_idx]
        node_coords = raw_coords[sort_idx]

        npsave(os.path.join(d, 'node_labels.npy'), node_labels)
        npsave(os.path.join(d, 'node_coords.npy'), node_coords)

        # label → row (for conn conversion)
        def label_to_row(lbl_arr):
            return np.searchsorted(node_labels, lbl_arr).astype(np.int32)

        # Elements by type
        elem_by_type = {}
        for elem in instance.elements:
            t = elem.type
            if t not in elem_by_type:
                elem_by_type[t] = {'labels': [], 'conn': []}
            elem_by_type[t]['labels'].append(elem.label)
            elem_by_type[t]['conn'].append(list(elem.connectivity))

        etype_meta = {}
        total_elems = 0
        has_highorder = False

        for etype, edata in elem_by_type.items():
            etype_code = ELEM_TYPE_CODE.get(etype)
            if etype_code is None:
                print("    WARNING: unknown type {}, skipped".format(etype))
                continue

            n_corner = ELEM_N_CORNER[etype_code]
            n_faces  = len(FACE_DEFS.get(etype_code, []))
            is_ho    = etype_code in HIGH_ORDER_CODES

            raw_el = np.array(edata['labels'], dtype=np.int32)
            raw_cn = np.array(edata['conn'],   dtype=np.int32)  # [M, n_total]
            esort  = np.argsort(raw_el)
            elem_labels  = raw_el[esort]
            conn_full    = raw_cn[esort]                         # [M, n_total]
            conn_lbl_corner = conn_full[:, :n_corner]           # [M, n_corner] labels
            conn_rows       = label_to_row(conn_lbl_corner)     # [M, n_corner] row idx

            td = os.path.join(d, 'elems', safe(etype))
            mkdirs(td)
            npsave(os.path.join(td, 'labels.npy'), elem_labels)
            npsave(os.path.join(td, 'conn.npy'),   conn_rows)

            if n_faces > 0:
                fei, fseq, fnc, fnrm = compute_face_data(
                    etype_code, conn_rows, node_coords)
                npsave(os.path.join(td, 'face_elem_idx.npy'),  fei)
                npsave(os.path.join(td, 'face_seq.npy'),       fseq)
                npsave(os.path.join(td, 'face_node_conn.npy'), fnc)
                npsave(os.path.join(td, 'face_normals.npy'),   fnrm)

            if is_ho:
                has_highorder = True
                hod = os.path.join(d, 'highorder', safe(etype))
                mkdirs(hod)
                npsave(os.path.join(hod, 'conn_full.npy'), conn_full.astype(np.int32))
                mid_idx = MIDNODE_INDICES.get(etype, [])
                npsave(os.path.join(hod, 'midnode_indices.npy'),
                       np.array(mid_idx, dtype=np.uint8))

            M_type = len(elem_labels)
            total_elems += M_type
            etype_meta[etype] = {
                'count':          M_type,
                'has_midnodes':   int(is_ho),
                'n_corner_nodes': n_corner,
                'n_faces':        n_faces,
                'etype_code':     etype_code,
            }

        # Instance sets
        isets_node = {}
        for sname, ns in instance.nodeSets.items():
            lbls = np.array(sorted([n.label for n in ns.nodes]), dtype=np.int32)
            isd  = os.path.join(d, 'isets', 'node_sets')
            mkdirs(isd)
            npsave(os.path.join(isd, safe(sname) + '.npy'), lbls)
            isets_node[sname] = len(lbls)

        isets_elem = {}
        for sname, es in instance.elementSets.items():
            lbls = np.array(sorted([e.label for e in es.elements]), dtype=np.int32)
            isd  = os.path.join(d, 'isets', 'elem_sets')
            mkdirs(isd)
            npsave(os.path.join(isd, safe(sname) + '.npy'), lbls)
            isets_elem[sname] = len(lbls)

        # Sections
        sections_info = {}
        try:
            for sa in instance.sectionAssignments:
                sname = sa.sectionName
                entry = {
                    'element_set':   getattr(sa.region, 'name', ''),
                    'material_name': '',
                    'type':          '',
                    'thickness':     None,
                }
                try:
                    sec = odb.sections[sname]
                    entry['type'] = type(sec).__name__
                    if hasattr(sec, 'material'):
                        entry['material_name'] = sec.material
                    if hasattr(sec, 'thickness'):
                        entry['thickness'] = float(sec.thickness)
                except Exception:
                    pass
                sections_info[sname] = entry
        except AttributeError:
            pass

        # Materials
        materials_info = {}
        try:
            for mat_name, mat in odb.materials.items():
                entry = {'type': type(mat).__name__}
                if hasattr(mat, 'elastic'):
                    try:
                        entry['elastic_table'] = [list(row) for row in mat.elastic.table]
                    except Exception:
                        pass
                materials_info[mat_name] = entry
        except AttributeError:
            pass

        jdump(os.path.join(d, 'sections.json'),  sections_info)
        jdump(os.path.join(d, 'materials.json'), materials_info)

        bbox_min = node_coords.min(axis=0).tolist()
        bbox_max = node_coords.max(axis=0).tolist()

        geom_meta[inst_name] = {
            'safe_name':    s,
            'part_name':    instance.partName,
            'node_count':   len(node_labels),
            'elem_count':   total_elems,
            'has_highorder': has_highorder,
            'bbox_min':     bbox_min,
            'bbox_max':     bbox_max,
            'elem_types':   etype_meta,
            'isets_node':   isets_node,
            'isets_elem':   isets_elem,
        }
        print("    {} nodes, {} elems".format(len(node_labels), total_elems))

    meta['geom'] = geom_meta
    print("  Geometry done.")


def dump_sets(odb, raw_dir, meta):
    """Write set arrays to l1_raw/sets/"""
    assembly = odb.rootAssembly
    sets_dir = os.path.join(raw_dir, 'sets')

    print("  Sets ...")

    # Assembly sets (already written partially in dump_assembly for assembly.h5;
    # here we write the canonical copy for sets.h5)
    for set_name, ns in assembly.nodeSets.items():
        by_inst = {}
        for node in ns.nodes:
            by_inst.setdefault(node.instanceName, []).append(node.label)
        for inst_n, labels in by_inst.items():
            d = os.path.join(sets_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            npsave(os.path.join(d, 'node_labels.npy'),
                   np.array(sorted(labels), dtype=np.int32))

    for set_name, es in assembly.elementSets.items():
        by_inst = {}
        for elem in es.elements:
            by_inst.setdefault(elem.instanceName, []).append(elem.label)
        for inst_n, labels in by_inst.items():
            d = os.path.join(sets_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            p = os.path.join(d, 'elem_labels.npy')
            if not os.path.exists(p):
                npsave(p, np.array(sorted(labels), dtype=np.int32))

    # Part sets (via instance node/element sets — one per part)
    seen_parts = set()
    for inst_name, instance in assembly.instances.items():
        part_name = instance.partName
        if part_name in seen_parts:
            continue
        seen_parts.add(part_name)
        for sname, ns in instance.nodeSets.items():
            d = os.path.join(sets_dir, 'partsets', safe(part_name), 'node_sets')
            mkdirs(d)
            lbls = np.array(sorted([n.label for n in ns.nodes]), dtype=np.int32)
            npsave(os.path.join(d, safe(sname) + '.npy'), lbls)
        for sname, es in instance.elementSets.items():
            d = os.path.join(sets_dir, 'partsets', safe(part_name), 'elem_sets')
            mkdirs(d)
            lbls = np.array(sorted([e.label for e in es.elements]), dtype=np.int32)
            npsave(os.path.join(d, safe(sname) + '.npy'), lbls)

    print("    done.")


def dump_results(odb, raw_dir, meta):
    """Write per-frame result arrays to l1_raw/results/<step>__<field>/<inst>/<pos>/[<etype>/]"""
    results_dir = os.path.join(raw_dir, 'results')
    mkdirs(results_dir)

    steps_meta = {}

    for step_num, (step_name, step) in enumerate(odb.steps.items()):
        procedure  = PROCEDURE_MAP.get(step.procedureType, step.procedureType)
        num_frames = len(step.frames)
        print("  Step '{}' ({} frames) ...".format(step_name, num_frames))

        frames_meta = []
        for fi, frame in enumerate(step.frames):
            frames_meta.append({
                'frame_idx':   fi,
                'frame_value': float(frame.frameValue),
                'description': frame.description,
            })

        steps_meta[step_name] = {
            'step_number': step_num,
            'procedure':   procedure,
            'num_frames':  num_frames,
            'frames':      frames_meta,
        }

        # Collect field names across all frames
        all_field_names = set()
        for frame in step.frames:
            all_field_names.update(frame.fieldOutputs.keys())

        for field_name in sorted(all_field_names):
            print("    Field '{}' ...".format(field_name))

            # First frame with this field → discover structure
            first_frame = next(
                (fr for fr in step.frames if field_name in fr.fieldOutputs), None)
            if first_frame is None:
                continue

            first_field = first_frame.fieldOutputs[field_name]
            components  = list(first_field.componentLabels)
            invariants  = [str(i) for i in first_field.validInvariants]

            safe_step  = safe(step_name)
            safe_field = safe(field_name)
            field_dir  = os.path.join(results_dir,
                                      '{}_{}'.format(safe_step, safe_field))
            mkdirs(field_dir)

            # Block structure discovered from first frame
            # key = (inst_name, position, elem_type)
            block_struct = {}   # key → info dict (written to meta.json)

            def get_block_dir(inst_name, position, elem_type):
                parts = [field_dir, safe(inst_name), position]
                if elem_type:
                    parts.append(safe(elem_type))
                d = os.path.join(*parts)
                mkdirs(d)
                return d

            # Discover structure + write canonical labels from first frame
            for block in first_field.bulkDataBlocks:
                if block.instance is None:
                    continue
                inst_name = block.instance.name
                position  = _pos_str(block.position)
                elem_type = getattr(block, 'elementType', None)
                key       = (inst_name, position, elem_type)
                if key in block_struct:
                    continue  # already discovered

                bd = get_block_dir(inst_name, position, elem_type)
                ncomp = np.array(block.data).shape[1]
                info  = {
                    'inst_name': inst_name,
                    'position':  position,
                    'elem_type': elem_type,
                    'ncomp':     ncomp,
                }

                if position == 'NODAL':
                    labels = np.array(block.nodeLabels, dtype=np.int32)
                    npsave(os.path.join(bd, 'labels.npy'), labels)
                    info['n_entities'] = len(labels)

                elif position == 'INTEGRATION_POINT':
                    u_elems, u_ips, u_sps, _ = reshape_ip_block(block)
                    npsave(os.path.join(bd, 'labels.npy'),    u_elems)
                    npsave(os.path.join(bd, 'ip_labels.npy'), u_ips)
                    npsave(os.path.join(bd, 'sp_labels.npy'), u_sps)
                    info['n_entities'] = len(u_elems)
                    info['n_ip']       = len(u_ips)
                    info['n_sp']       = len(u_sps)

                elif position == 'ELEMENT_NODAL':
                    u_elems, data_nd = reshape_element_nodal_block(block)
                    npsave(os.path.join(bd, 'labels.npy'), u_elems)
                    info['n_entities'] = len(u_elems)
                    info['n_enodes']   = data_nd.shape[1]

                else:
                    labels = np.array(block.elementLabels, dtype=np.int32)
                    npsave(os.path.join(bd, 'labels.npy'), labels)
                    info['n_entities'] = len(labels)

                block_struct[key] = info

            # Write per-frame data
            has_section = 0
            for frame_idx, frame in enumerate(step.frames):
                if field_name not in frame.fieldOutputs:
                    continue
                field_out = frame.fieldOutputs[field_name]

                for block in field_out.bulkDataBlocks:
                    if block.instance is None:
                        continue
                    inst_name = block.instance.name
                    position  = _pos_str(block.position)
                    elem_type = getattr(block, 'elementType', None)
                    key       = (inst_name, position, elem_type)
                    if key not in block_struct:
                        continue

                    bd       = get_block_dir(inst_name, position, elem_type)
                    fr_path  = os.path.join(bd, 'f{:04d}.npy'.format(frame_idx))

                    if position == 'NODAL':
                        labels    = np.array(block.nodeLabels, dtype=np.int32)
                        data_flat = np.array(block.data,       dtype=np.float32)
                        canon     = np.load(os.path.join(bd, 'labels.npy'))
                        rows      = np.searchsorted(canon, labels)
                        out       = np.zeros((len(canon), data_flat.shape[1]),
                                            dtype=np.float32)
                        out[rows] = data_flat
                        npsave(fr_path, out)

                    elif position == 'INTEGRATION_POINT':
                        _, _, u_sps, data_nd = reshape_ip_block(block)
                        if len(u_sps) > 0:
                            has_section = 1
                        npsave(fr_path, data_nd)

                    elif position == 'ELEMENT_NODAL':
                        _, data_nd = reshape_element_nodal_block(block)
                        npsave(fr_path, data_nd)

                    else:
                        raw_lbl  = np.array(block.elementLabels, dtype=np.int32)
                        raw_data = np.array(block.data, dtype=np.float32)
                        canon    = np.load(os.path.join(bd, 'labels.npy'))
                        rows     = np.searchsorted(canon, raw_lbl)
                        out      = np.zeros((len(canon), raw_data.shape[1]),
                                           dtype=np.float32)
                        out[rows] = raw_data
                        npsave(fr_path, out)

            # Write field meta.json
            jdump(os.path.join(field_dir, 'meta.json'), {
                'step_name':   step_name,
                'field_name':  field_name,
                'components':  components,
                'invariants':  invariants,
                'has_section': has_section,
                'blocks':      [
                    {
                        'inst_name': k[0],
                        'position':  k[1],
                        'elem_type': k[2],
                        **v,
                    }
                    for k, v in block_struct.items()
                ],
            })
            print("      done ({} blocks)".format(len(block_struct)))

    meta['steps'] = steps_meta
    print("  Results done.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args      = parse_args()
    odb_path  = os.path.abspath(args.odb)
    workspace = os.path.abspath(args.out)
    raw_dir   = os.path.join(workspace, 'l1_raw')

    if not os.path.exists(odb_path):
        print("ERROR: ODB not found: {}".format(odb_path))
        sys.exit(1)

    mkdirs(raw_dir)

    print("=== Layer 1 Phase 1: ODB → npy ===")
    print("  ODB:       {}".format(odb_path))
    print("  Workspace: {}".format(workspace))

    meta = {}  # top-level metadata dict, written to dump_meta.json at end

    print("Opening ODB ...")
    odb = odbAccess.openOdb(path=odb_path, readOnly=True)
    print("  ODB opened.")

    try:
        dump_assembly(odb, raw_dir, meta)
        dump_geometry(odb, raw_dir, meta)
        dump_sets(odb, raw_dir, meta)
        dump_results(odb, raw_dir, meta)
    except Exception:
        print("\n!!! ERROR:")
        traceback.print_exc()
        odb.close()
        sys.exit(1)

    odb.close()

    jdump(os.path.join(raw_dir, 'dump_meta.json'), meta)
    print("=== Phase 1 complete. Run l1_pack.py next. ===")


if __name__ == '__main__':
    main()
