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
import time
import traceback

# Force line-buffered stdout so progress prints appear in real time
# even when piped by the job runner (Abaqus Python 2.7 buffers by default).
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)

import numpy as np

try:
    import odbAccess
except ImportError:
    print("ERROR: odbAccess not found. Must run under 'abaqus python'.")
    sys.exit(1)

# ELEMENT_NODAL constant — needed for getSubset() extrapolation calls.
# Abaqus may expose it via abaqusConstants or directly on odbAccess.
_ELEM_NODAL_CONST = None
try:
    from abaqusConstants import ELEMENT_NODAL as _ELEM_NODAL_CONST  # noqa: F401
except Exception:
    try:
        _ELEM_NODAL_CONST = odbAccess.ELEMENT_NODAL
    except Exception:
        pass  # getSubset() extrapolation will be skipped if constant unavailable


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
    p.add_argument('--mode',
                   choices=['full', 'preflight', 'results-worker',
                            'consistency-check', 'extract'],
                   default='full',
                   help=(
                       'full=serial dump (default); '
                       'preflight=geometry+meta only; '
                       'results-worker=parallel worker; '
                       'consistency-check=preflight validation against INP geometry; '
                       'extract=results-only dump for a named result_group'
                   ))
    # ── new project-grouping params ──────────────────────────────────────────
    p.add_argument('--result-group', default=None,
                   help='Result group name; required for consistency-check and extract modes')
    p.add_argument('--check-mode', choices=['count-only', 'label-only'],
                   default='count-only',
                   help='Consistency check depth (consistency-check mode only)')
    # ── legacy parallel-worker params ────────────────────────────────────────
    p.add_argument('--step',   default=None,
                   help='Step name (results-worker mode only)')
    p.add_argument('--fields', default=None,
                   help='Comma-separated field names (results-worker mode only)')
    p.add_argument('--worker-id', default='0',
                   help='Worker ID shown in log prefix (results-worker mode only)')
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


def _fmt_t(secs):
    """Format elapsed seconds as '1m 23.4s' or '5.2s'."""
    if secs >= 60:
        return "{:d}m {:.1f}s".format(int(secs) // 60, secs % 60)
    return "{:.1f}s".format(secs)


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
        face_rows[flip] = face_rows[flip, ::-1]   # 同步翻转绕序，确保叉积方向朝外

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
    labels_flat = np.array(block.elementLabels, dtype=np.int32)
    data_flat   = np.array(block.data,          dtype=np.float32)

    # Abaqus 2024 可能改名或不暴露 integrationPointLabels，尝试多个属性名
    ip_raw = (getattr(block, 'integrationPointLabels', None)
              or getattr(block, 'ipLabels', None))
    if ip_raw is not None:
        ip_flat = np.array(ip_raw, dtype=np.int32)
    else:
        # 从数据推断：总行数 / 单元数 = 每单元积分点数
        n_total   = len(labels_flat)
        n_u_elems = len(np.unique(labels_flat))
        n_ip_inf  = n_total // n_u_elems if n_u_elems else 1
        ip_flat   = np.tile(np.arange(1, n_ip_inf + 1, dtype=np.int32), n_u_elems)
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


# ─── Assembly set helpers ─────────────────────────────────────────────────────

def _set_nodes_by_inst(ns):
    """
    返回 {inst_name: [label, ...]} 字典。
    兼容两种 Abaqus 版本：
      - 旧版：ns.nodes 是扁平序列，每个 node 有 instanceName 属性
      - 新版：ns.nodes 是按 instance 分组的嵌套序列，需配合 ns.instances 使用
    """
    by_inst = {}
    nodes_seq = ns.nodes
    inst_seq  = getattr(ns, 'instances', None)
    if inst_seq and len(inst_seq) == len(nodes_seq):
        # 新版：node_array 对应 inst_seq 中同位置的 instance
        for inst, node_array in zip(inst_seq, nodes_seq):
            iname = inst.name if hasattr(inst, 'name') else str(inst)
            for node in node_array:
                by_inst.setdefault(iname, []).append(node.label)
    else:
        # 旧版：每个 node 自带 instanceName
        for node in nodes_seq:
            by_inst.setdefault(node.instanceName, []).append(node.label)
    return by_inst


def _set_elems_by_inst(es):
    """同上，针对 element set。"""
    by_inst = {}
    elems_seq = es.elements
    inst_seq  = getattr(es, 'instances', None)
    if inst_seq and len(inst_seq) == len(elems_seq):
        for inst, elem_array in zip(inst_seq, elems_seq):
            iname = inst.name if hasattr(inst, 'name') else str(inst)
            for elem in elem_array:
                by_inst.setdefault(iname, []).append(elem.label)
    else:
        for elem in elems_seq:
            by_inst.setdefault(elem.instanceName, []).append(elem.label)
    return by_inst


# ─── Dump phases ──────────────────────────────────────────────────────────────

def dump_assembly(odb, raw_dir, meta):
    """Write assembly data to l1_raw/assembly/"""
    assembly = odb.rootAssembly
    asm_dir  = os.path.join(raw_dir, 'assembly')
    mkdirs(asm_dir)

    t0 = time.time()
    print("  Assembly ...")
    inst_meta = {}
    for inst_name, instance in assembly.instances.items():
        s = safe(inst_name)
        d = os.path.join(asm_dir, 'instances', s)
        mkdirs(d)
        npsave(os.path.join(d, 'transform.npy'), get_instance_transform(instance))
        part_name = getattr(instance, 'partName', None)
        if part_name is None and hasattr(instance, 'part') and hasattr(instance.part, 'name'):
            part_name = instance.part.name
        if not part_name:
            part_name = inst_name
        with open(os.path.join(d, 'part_name.txt'), 'w') as f:
            f.write(part_name)
        inst_meta[inst_name] = {'part_name': part_name}

    meta['instances'] = inst_meta

    # Assembly node sets (per-instance split)
    for set_name, ns in assembly.nodeSets.items():
        for inst_n, labels in _set_nodes_by_inst(ns).items():
            d = os.path.join(asm_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            npsave(os.path.join(d, 'node_labels.npy'),
                   np.array(sorted(labels), dtype=np.int32))

    # Assembly element sets (per-instance split)
    for set_name, es in assembly.elementSets.items():
        for inst_n, labels in _set_elems_by_inst(es).items():
            d = os.path.join(asm_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            path = os.path.join(d, 'elem_labels.npy')
            if not os.path.exists(path):
                npsave(path, np.array(sorted(labels), dtype=np.int32))

    print("    done. ({})".format(_fmt_t(time.time() - t0)))


def dump_geometry(odb, raw_dir, meta):
    """Write per-instance geometry to l1_raw/geom/<inst>/"""
    assembly = odb.rootAssembly
    geom_dir = os.path.join(raw_dir, 'geom')
    mkdirs(geom_dir)

    geom_meta = {}   # populated into meta['geom']

    t_geom = time.time()
    for inst_name, instance in assembly.instances.items():
        t_inst = time.time()
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

        part_name = getattr(instance, 'partName', None)
        if part_name is None and hasattr(instance, 'part') and hasattr(instance.part, 'name'):
            part_name = instance.part.name
        if not part_name:
            part_name = inst_name

        geom_meta[inst_name] = {
            'safe_name':    s,
            'part_name':    part_name,
            'node_count':   len(node_labels),
            'elem_count':   total_elems,
            'has_highorder': has_highorder,
            'bbox_min':     bbox_min,
            'bbox_max':     bbox_max,
            'elem_types':   etype_meta,
            'isets_node':   isets_node,
            'isets_elem':   isets_elem,
        }
        print("    {} nodes, {} elems ({})".format(
            len(node_labels), total_elems, _fmt_t(time.time() - t_inst)))

    meta['geom'] = geom_meta
    print("  Geometry done. ({} total)".format(_fmt_t(time.time() - t_geom)))


def dump_sets(odb, raw_dir, meta):
    """Write set arrays to l1_raw/sets/"""
    assembly = odb.rootAssembly
    sets_dir = os.path.join(raw_dir, 'sets')

    t0 = time.time()
    print("  Sets ...")

    # Assembly sets (already written partially in dump_assembly for assembly.h5;
    # here we write the canonical copy for sets.h5)
    for set_name, ns in assembly.nodeSets.items():
        for inst_n, labels in _set_nodes_by_inst(ns).items():
            d = os.path.join(sets_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            npsave(os.path.join(d, 'node_labels.npy'),
                   np.array(sorted(labels), dtype=np.int32))

    for set_name, es in assembly.elementSets.items():
        for inst_n, labels in _set_elems_by_inst(es).items():
            d = os.path.join(sets_dir, 'asmsets', safe(set_name), safe(inst_n))
            mkdirs(d)
            p = os.path.join(d, 'elem_labels.npy')
            if not os.path.exists(p):
                npsave(p, np.array(sorted(labels), dtype=np.int32))

    # Part sets (via instance node/element sets — one per part)
    seen_parts = set()
    for inst_name, instance in assembly.instances.items():
        part_name = getattr(instance, 'partName', None)
        if part_name is None and hasattr(instance, 'part') and hasattr(instance.part, 'name'):
            part_name = instance.part.name
        if not part_name:
            part_name = inst_name
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

    print("    done. ({})".format(_fmt_t(time.time() - t0)))


def dump_steps_meta_scan(odb, raw_dir, meta):
    """
    Scan step/frame metadata and collect field names WITHOUT reading any field data.
    Writes l1_raw/fields_manifest.json (used by the parallel launcher).
    Populates meta['steps'] with procedure/frame info only.
    """
    t0 = time.time()
    print("  Scanning steps/fields (no data read) ...")
    steps_meta = {}
    fields_by_step = {}  # step_name -> [field_name, ...]

    for step_num, (step_name, step) in enumerate(odb.steps.items()):
        raw_proc = getattr(step, 'procedureType', None) or getattr(step, 'procedure', '') or ''
        raw_proc_upper = raw_proc.upper().replace('*', '').strip()
        procedure = PROCEDURE_MAP.get(raw_proc_upper)
        if procedure is None:
            for key, val in PROCEDURE_MAP.items():
                if raw_proc_upper.startswith(key.split('_')[0]):
                    procedure = val
                    break
        if procedure is None:
            procedure = raw_proc_upper or 'STATIC'

        num_frames = len(step.frames)
        frames_meta = []
        all_field_names = set()
        for fi, frame in enumerate(step.frames):
            frames_meta.append({
                'frame_idx':   fi,
                'frame_value': float(frame.frameValue),
                'description': frame.description,
            })
            all_field_names.update(frame.fieldOutputs.keys())

        steps_meta[step_name] = {
            'step_number': step_num,
            'procedure':   procedure,
            'num_frames':  num_frames,
            'frames':      frames_meta,
        }
        field_list = sorted(all_field_names)
        fields_by_step[step_name] = field_list
        print("    Step '{}': {} frames, {} fields".format(
            step_name, num_frames, len(field_list)))

    meta['steps'] = steps_meta
    manifest = {'fields_by_step': fields_by_step}
    jdump(os.path.join(raw_dir, 'fields_manifest.json'), manifest)
    print("  Scan done. ({})".format(_fmt_t(time.time() - t0)))
    print("  fields_manifest.json written.")


def dump_results(odb, raw_dir, meta, field_filter=None):
    """Write per-frame result arrays to l1_raw/results/<step>__<field>/<inst>/<pos>/[<etype>/]

    field_filter: optional dict {step_name: set_of_field_names}.
      If provided, only those (step, field) combinations are dumped.
      Steps/fields absent from field_filter are silently skipped.
      If None, all fields in all steps are dumped (original behaviour).
    """
    results_dir = os.path.join(raw_dir, 'results')
    mkdirs(results_dir)

    t_results = time.time()
    steps_meta = {}

    for step_num, (step_name, step) in enumerate(odb.steps.items()):
        # If field_filter given, skip steps not in it entirely
        if field_filter is not None and step_name not in field_filter:
            continue

        # Abaqus 2024+: step.procedure 返回原始关键字字符串如 '*STATIC'
        # 旧版: step.procedureType 返回符号常量如 'STATIC_GENERAL'
        raw_proc = getattr(step, 'procedureType', None) or getattr(step, 'procedure', '') or ''
        raw_proc_upper = raw_proc.upper().replace('*', '').strip()
        # 先按旧 key 查，再按关键字前缀匹配
        procedure = PROCEDURE_MAP.get(raw_proc_upper)
        if procedure is None:
            for key, val in PROCEDURE_MAP.items():
                if raw_proc_upper.startswith(key.split('_')[0]):
                    procedure = val
                    break
        if procedure is None:
            procedure = raw_proc_upper or 'STATIC'
        num_frames = len(step.frames)
        t_step = time.time()
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

        # Apply field_filter within this step
        allowed_fields = field_filter[step_name] if field_filter is not None else None

        for field_name in sorted(all_field_names):
            if allowed_fields is not None and field_name not in allowed_fields:
                continue
            t_field = time.time()
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
                elem_type = (getattr(block, 'elementType', None)
                             or getattr(block, 'baseElementType', None))
                # Abaqus 2024 may return None for elementType; use ncomp+n_entities
                # as a secondary discriminator so different-shaped blocks get separate keys
                if elem_type is None:
                    _d = np.array(block.data)
                    _n = len(getattr(block, 'elementLabels',
                             getattr(block, 'nodeLabels', [])))
                    elem_type = '_auto_{}x{}'.format(_n, _d.shape[1] if _d.ndim > 1 else 1)
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

            # ── Auto-extrapolate ELEMENT_NODAL from INTEGRATION_POINT ────────
            # If the ODB only has INTEGRATION_POINT output, call
            # getSubset(position=ELEMENT_NODAL) to extrapolate integration-point
            # values onto element corner nodes.  This is what Abaqus does
            # internally when drawing contour plots.
            # Instances that already have native ELEMENT_NODAL blocks are skipped.
            _ip_insts = {k[0] for k in block_struct if k[1] == 'INTEGRATION_POINT'}
            _en_insts = {k[0] for k in block_struct if k[1] == 'ELEMENT_NODAL'}
            _extrapolate_en = bool(_ip_insts and _ELEM_NODAL_CONST is not None)
            if _extrapolate_en:
                try:
                    _en_first = first_field.getSubset(position=_ELEM_NODAL_CONST)
                    for block in _en_first.bulkDataBlocks:
                        if block.instance is None:
                            continue
                        inst_name = block.instance.name
                        if inst_name in _en_insts:
                            continue  # native EN already present
                        position  = 'ELEMENT_NODAL'
                        elem_type = (getattr(block, 'elementType', None)
                                     or getattr(block, 'baseElementType', None))
                        if elem_type is None:
                            _d = np.array(block.data)
                            _n = len(getattr(block, 'elementLabels', []))
                            elem_type = '_auto_{}x{}'.format(
                                _n, _d.shape[1] if _d.ndim > 1 else 1)
                        key = (inst_name, position, elem_type)
                        if key in block_struct:
                            continue
                        bd = get_block_dir(inst_name, position, elem_type)
                        ncomp = np.array(block.data).shape[1]
                        u_elems, data_nd = reshape_element_nodal_block(block)
                        npsave(os.path.join(bd, 'labels.npy'), u_elems)
                        block_struct[key] = {
                            'inst_name':  inst_name,
                            'position':   position,
                            'elem_type':  elem_type,
                            'ncomp':      ncomp,
                            'n_entities': len(u_elems),
                            'n_enodes':   data_nd.shape[1],
                        }
                    print("    [EN extrapolation] discovered {} EN block(s)".format(
                        len({k for k in block_struct if k[1] == 'ELEMENT_NODAL'}) - len(_en_insts)))
                except Exception as _e:
                    print("    [warn] getSubset(ELEMENT_NODAL) structure failed: {}".format(_e))
                    _extrapolate_en = False

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
                    elem_type = (getattr(block, 'elementType', None)
                                 or getattr(block, 'baseElementType', None))
                    if elem_type is None:
                        _d = np.array(block.data)
                        _n = len(getattr(block, 'elementLabels',
                                 getattr(block, 'nodeLabels', [])))
                        elem_type = '_auto_{}x{}'.format(_n, _d.shape[1] if _d.ndim > 1 else 1)
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

                # ── Write extrapolated ELEMENT_NODAL data for this frame ──────
                if _extrapolate_en:
                    try:
                        _en_out = field_out.getSubset(position=_ELEM_NODAL_CONST)
                        for block in _en_out.bulkDataBlocks:
                            if block.instance is None:
                                continue
                            inst_name = block.instance.name
                            if inst_name in _en_insts:
                                continue  # native EN already written
                            position  = 'ELEMENT_NODAL'
                            elem_type = (getattr(block, 'elementType', None)
                                         or getattr(block, 'baseElementType', None))
                            if elem_type is None:
                                _d = np.array(block.data)
                                _n = len(getattr(block, 'elementLabels', []))
                                elem_type = '_auto_{}x{}'.format(
                                    _n, _d.shape[1] if _d.ndim > 1 else 1)
                            key = (inst_name, position, elem_type)
                            if key not in block_struct:
                                continue
                            bd      = get_block_dir(inst_name, position, elem_type)
                            fr_path = os.path.join(bd, 'f{:04d}.npy'.format(frame_idx))
                            _, data_nd = reshape_element_nodal_block(block)
                            npsave(fr_path, data_nd)
                    except Exception as _e:
                        pass  # per-frame EN extrapolation failure is non-fatal

            # Write field meta.json
            jdump(os.path.join(field_dir, 'meta.json'), {
                'step_name':   step_name,
                'field_name':  field_name,
                'components':  components,
                'invariants':  invariants,
                'has_section': has_section,
                'blocks':      [
                    dict(
                        {'inst_name': k[0], 'position': k[1], 'elem_type': k[2]},
                        **v
                    )
                    for k, v in block_struct.items()
                ],
            })
            print("      done ({} blocks, {})".format(
                len(block_struct), _fmt_t(time.time() - t_field)))
        print("  Step '{}' done. ({})".format(step_name, _fmt_t(time.time() - t_step)))

    meta['steps'] = steps_meta
    print("  Results done. ({} total)".format(_fmt_t(time.time() - t_results)))


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

    t_total = time.time()
    mode = args.mode

    if mode == 'consistency-check':
        _run_consistency_check(args, odb_path, workspace)
        return

    if mode == 'extract':
        _run_extract(args, odb_path, workspace)
        return

    if mode == 'results-worker':
        # Parallel worker: only dump a subset of fields for one step.
        # Assembly/geom/sets must already exist (written by preflight run).
        if not args.step or not args.fields:
            print("ERROR: --mode results-worker requires --step and --fields")
            sys.exit(1)
        wid         = args.worker_id
        step_name   = args.step
        field_names = set(f.strip() for f in args.fields.split(',') if f.strip())
        field_filter = {step_name: field_names}
        print("=== Worker {} — step='{}' fields={} ===".format(
            wid, step_name, sorted(field_names)))
        t0 = time.time()
        print("Opening ODB (readOnly) ...")
        odb = odbAccess.openOdb(path=odb_path, readOnly=True)
        print("  ODB opened. ({})".format(_fmt_t(time.time() - t0)))
        try:
            meta = {}
            dump_results(odb, raw_dir, meta, field_filter=field_filter)
        except Exception:
            print("\n!!! ERROR (worker {}):".format(wid))
            traceback.print_exc()
            odb.close()
            sys.exit(1)
        odb.close()
        print("=== Worker {} done in {}. ===".format(wid, _fmt_t(time.time() - t_total)))
        return

    # full or preflight: print header and open ODB
    print("=== Layer 1 Phase 1: ODB → npy ({}) ===".format(mode))
    print("  ODB:       {}".format(odb_path))
    print("  Workspace: {}".format(workspace))

    meta = {}

    t0 = time.time()
    print("Opening ODB ...")
    odb = odbAccess.openOdb(path=odb_path, readOnly=True)
    print("  ODB opened. ({})".format(_fmt_t(time.time() - t0)))

    try:
        dump_assembly(odb, raw_dir, meta)
        dump_geometry(odb, raw_dir, meta)
        dump_sets(odb, raw_dir, meta)
        if mode == 'preflight':
            # Scan step/frame metadata + build fields_manifest.json; no field data read.
            dump_steps_meta_scan(odb, raw_dir, meta)
        else:
            # full: serial dump of all results (original behaviour)
            dump_results(odb, raw_dir, meta)
    except Exception:
        print("\n!!! ERROR:")
        traceback.print_exc()
        odb.close()
        sys.exit(1)

    odb.close()

    jdump(os.path.join(raw_dir, 'dump_meta.json'), meta)
    if mode == 'preflight':
        print("=== Preflight complete in {}. "
              "Now run abaqus_dump_parallel.py or launch workers manually. ===".format(
              _fmt_t(time.time() - t_total)))
    else:
        print("=== Phase 1 complete in {}. Run l1_pack.py next. ===".format(
            _fmt_t(time.time() - t_total)))


# ─── New project-grouping modes ───────────────────────────────────────────────

def _run_consistency_check(args, odb_path, workspace):
    """
    --mode consistency-check: preflight 校验，不提取结果。
    输出: <workspace>/l1_raw/consistency/<result_group>/check.json
          (label-only 模式还会写 node_labels.npy 和 <etype>_elem_labels.npy)
    """
    result_group = args.result_group
    check_mode   = args.check_mode  # 'count-only' | 'label-only'

    if not result_group:
        print("ERROR: --result-group required for --mode consistency-check")
        sys.exit(1)

    out_dir = os.path.join(workspace, 'l1_raw', 'consistency', safe(result_group))
    mkdirs(out_dir)

    print("=== consistency-check ({}) result_group='{}' ===".format(
        check_mode, result_group))
    print("  ODB: {}".format(odb_path))

    t0 = time.time()
    odb = odbAccess.openOdb(path=odb_path, readOnly=True)
    print("  ODB opened. ({})".format(_fmt_t(time.time() - t0)))

    assembly = odb.rootAssembly
    instances_out = {}

    for inst_name, instance in assembly.instances.items():
        node_count = len(instance.nodes)
        # count total elements across all types
        elem_count = len(instance.elements)

        inst_entry = {
            'node_count': node_count,
            'elem_count': elem_count,
        }

        if check_mode == 'label-only':
            # node labels (sorted ascending — same order as geometry H5)
            raw_labels = np.array(
                [n.label for n in instance.nodes], dtype=np.int32)
            node_labels = np.sort(raw_labels)
            nl_path = os.path.join(out_dir,
                                   '{}_node_labels.npy'.format(safe(inst_name)))
            npsave(nl_path, node_labels)
            inst_entry['node_labels_path'] = os.path.relpath(nl_path, workspace)

            # element labels per type (sorted ascending)
            elem_by_type = {}
            for elem in instance.elements:
                t = elem.type
                if t not in elem_by_type:
                    elem_by_type[t] = []
                elem_by_type[t].append(elem.label)

            elem_labels_paths = {}
            for etype, labels in elem_by_type.items():
                el = np.array(sorted(labels), dtype=np.int32)
                el_path = os.path.join(
                    out_dir,
                    '{}_{}_elem_labels.npy'.format(safe(inst_name), safe(etype)))
                npsave(el_path, el)
                elem_labels_paths[etype] = os.path.relpath(el_path, workspace)
            inst_entry['element_labels'] = elem_labels_paths

        instances_out[inst_name] = inst_entry
        print("  {}: nodes={} elems={}".format(inst_name, node_count, elem_count))

    odb.close()

    check = {
        'mode':    check_mode,
        'warning': (
            'Count-only validation does not verify label or row mapping. '
            'Mismatched labels with matching counts will not be detected.'
        ),
        'instances': instances_out,
    }
    check_json = os.path.join(out_dir, 'check.json')
    jdump(check_json, check)
    print("  check.json written to {}".format(check_json))
    print("=== consistency-check done in {}. ===".format(
        _fmt_t(time.time() - t0)))


def _run_extract(args, odb_path, workspace):
    """
    --mode extract: 只提取结果，跳过几何/assembly/sets。
    输出: <workspace>/l1_raw/rg_<result_group>/ （供 l1_pack.py --result-group 读取）
    """
    result_group = args.result_group
    if not result_group:
        print("ERROR: --result-group required for --mode extract")
        sys.exit(1)

    # 独立的 raw_dir，l1_pack.py --result-group 会来这里读
    raw_dir = os.path.join(workspace, 'l1_raw', 'rg_{}'.format(safe(result_group)))
    mkdirs(raw_dir)

    print("=== extract result_group='{}' ===".format(result_group))
    print("  ODB:    {}".format(odb_path))
    print("  raw_dir: {}".format(raw_dir))

    t0 = time.time()
    odb = odbAccess.openOdb(path=odb_path, readOnly=True)
    print("  ODB opened. ({})".format(_fmt_t(time.time() - t0)))

    meta = {}
    try:
        dump_results(odb, raw_dir, meta)
    except Exception:
        traceback.print_exc()
        odb.close()
        sys.exit(1)

    odb.close()
    jdump(os.path.join(raw_dir, 'dump_meta.json'), meta)
    print("=== extract done in {}. Run l1_pack.py --result-group next. ===".format(
        _fmt_t(time.time() - t0)))


if __name__ == '__main__':
    main()
