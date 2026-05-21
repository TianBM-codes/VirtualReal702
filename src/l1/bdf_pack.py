#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bdf_pack.py — Layer 1 extraction for Nastran BDF files.

Usage:
    python src/l1/bdf_pack.py --bdf /path/to/model.bdf --workspace /data/<job_id>/

Output:
    <workspace>/l1/assembly.h5
    <workspace>/l1/geometry/<MODEL_NAME>.h5
    <workspace>/l1/sets/sets.h5          (empty stub — instance sets live in geometry h5)
    <workspace>/manifest.db              (instances + element_type_dist + node/element_sets)
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

from src.l1.manifest_schema import MANIFEST_SCHEMA


# ─── Element type mappings ────────────────────────────────────────────────────

# Nastran card → (Abaqus etype name, n_corner_nodes, n_faces_per_elem)
# None entries are dispatched by node count via SOLID_BY_NNODE below.
# n_corner_nodes for high-order elements is the corner-node count only;
# mid-nodes beyond that are discarded (L2 only needs corner geometry).
NASTRAN_TO_ABAQUS = {
    # Shells — linear
    'CQUAD4': ('S4R',    4, 1),
    'CQUADR': ('S4R',    4, 1),   # rotational-DOF variant, same geometry
    'CSHEAR': ('S4R',    4, 1),   # shear panel, rendered as quad shell
    'CTRIA3': ('S3',     3, 1),
    'CTRIAR': ('S3',     3, 1),   # rotational-DOF variant
    # Shells — high-order (conn stores corner nodes only)
    'CQUAD8': ('S8R',    4, 1),
    'CTRIA6': ('STRI65', 3, 1),
    # Beams / rods / tubes
    'CBAR':   ('B31',    2, 0),
    'CBEAM':  ('B31',    2, 0),
    'CBEND':  ('B31',    2, 0),
    'CROD':   ('B31',    2, 0),
    'CONROD': ('B31',    2, 0),   # inline material/section, no pid
    'CTUBE':  ('B31',    2, 0),
    # Springs / connectors
    'CBUSH':  ('SPRING', 2, 0),
    'CBUSH1D':('SPRING', 2, 0),
    'CELAS1': ('SPRING', 2, 0),
    'CELAS2': ('SPRING', 2, 0),   # inline stiffness, no pid
    # Dampers
    'CDAMP1': ('DASHPOT',2, 0),
    'CDAMP2': ('DASHPOT',2, 0),   # inline damping, no pid
    # Concentrated masses (1-node; only G1 is stored in conn)
    'CONM1':  ('MASS',   1, 0),
    'CONM2':  ('MASS',   1, 0),
    'CMASS1': ('MASS',   1, 0),
    'CMASS2': ('MASS',   1, 0),   # inline mass, no pid
    # Solids — dispatched by node count via SOLID_BY_NNODE
    'CHEXA':  None,
    'CPENTA': None,
    'CTETRA': None,
}

SOLID_BY_NNODE = {
    # Linear solids
    ('CHEXA',  8):  ('C3D8R', 8, 6),
    ('CPENTA', 6):  ('C3D6',  6, 5),
    ('CTETRA', 4):  ('C3D4',  4, 4),
    # High-order solids (n_corner = corner nodes only; mid-nodes discarded)
    ('CHEXA',  20): ('C3D20', 8, 6),
    ('CPENTA', 15): ('C3D15', 6, 5),
    ('CTETRA', 10): ('C3D10', 4, 4),
}

# Cards with no GRID-point connectivity (scalar points) or degenerate geometry —
# silently skipped; a warning is printed for any other unrecognised card.
_UNSUPPORTED_SKIP = {'CELAS4', 'CMASS4', 'CQUAD6'}

FACE_DEFS = {
    # Linear shells
    'S4R':    [[0, 1, 2, 3]],
    'S3':     [[0, 1, 2]],
    # High-order shells (face uses corner indices 0..n_corner-1)
    'S8R':    [[0, 1, 2, 3]],
    'STRI65': [[0, 1, 2]],
    # Linear solids
    'C3D8R':  [
        [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 5, 4], [1, 2, 6, 5],
        [2, 3, 7, 6], [3, 0, 4, 7],
    ],
    'C3D6':   [
        [0, 1, 2], [3, 4, 5],
        [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5],
    ],
    'C3D4':   [
        [0, 1, 2], [0, 1, 3],
        [0, 2, 3], [1, 2, 3],
    ],
    # High-order solids (same face topology as their linear counterparts)
    'C3D20':  [
        [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 5, 4], [1, 2, 6, 5],
        [2, 3, 7, 6], [3, 0, 4, 7],
    ],
    'C3D15':  [
        [0, 1, 2], [3, 4, 5],
        [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5],
    ],
    'C3D10':  [
        [0, 1, 2], [0, 1, 3],
        [0, 2, 3], [1, 2, 3],
    ],
    # No-face types
    'B31':    [],
    'SPRING': [],
    'DASHPOT':[],
    'MASS':   [],
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='BDF → HDF5 packer (L1)')
    p.add_argument('--bdf',       required=True, help='Path to .bdf file')
    p.add_argument('--workspace', required=True, help='Workspace directory')
    return p.parse_args()


def mkdirs(path):
    if not os.path.exists(path):
        os.makedirs(path)


def safe(name):
    return name.replace('/', '__').replace('\\', '__').replace(' ', '_')


def _fmt_t(secs):
    if secs >= 60:
        return '{:d}m {:.1f}s'.format(int(secs) // 60, secs % 60)
    return '{:.1f}s'.format(secs)


def str_ds(f, path, strings):
    dt = h5py.special_dtype(vlen=str)
    ds = f.create_dataset(path, shape=(len(strings),), dtype=dt)
    for i, s in enumerate(strings):
        ds[i] = str(s)
    return ds


def init_manifest(workspace):
    db_path = os.path.join(workspace, 'manifest.db')
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(MANIFEST_SCHEMA)
    conn.commit()
    return conn


# ─── Shell section refinement (mirrors l1_pack.py) ───────────────────────────

_SHELL_PREFIXES = ('S3', 'S4', 'S6', 'S8', 'SC', 'STRI', 'M3D')


def _is_shell_etype(name):
    up = name.upper()
    return any(up.startswith(p) for p in _SHELL_PREFIXES)


def _refine_shell_sections(h5_path, angle_thresh_deg=20.0):
    cos_thresh = np.cos(np.radians(angle_thresh_deg))
    with h5py.File(h5_path, 'a') as f:
        if 'elements' not in f or 'nodes/coords' not in f:
            return
        coords = f['nodes/coords'][:]
        shell_etypes = [
            e for e in sorted(f['elements'].keys())
            if _is_shell_etype(e)
            and 'conn' in f['elements/{}'.format(e)]
            and 'section_id' in f['elements/{}'.format(e)]
        ]
        if not shell_etypes:
            return

        max_sid = -1
        for etype_safe in f['elements']:
            grp = f['elements/{}'.format(etype_safe)]
            if 'section_id' in grp:
                arr = grp['section_id'][:]
                valid = arr[arr >= 0]
                if len(valid):
                    max_sid = max(max_sid, int(valid.max()))
        next_id = max_sid + 1

        etype_meta = []
        offset = 0
        for etype_safe in shell_etypes:
            grp   = f['elements/{}'.format(etype_safe)]
            conn  = grp['conn'][:]
            sid   = grp['section_id'][:]
            N, nc = conn.shape
            etype_meta.append({'name': etype_safe, 'conn': conn, 'section_id': sid,
                                'n_corner': nc, 'offset': offset, 'N': N})
            offset += N
        total = offset

        all_sid = np.concatenate([d['section_id'] for d in etype_meta])

        normals_parts = []
        for d in etype_meta:
            conn  = d['conn']
            e0 = coords[conn[:, 0]]
            e1 = coords[conn[:, 1]]
            e2 = coords[conn[:, 2]]
            raw_n = np.cross(e1 - e0, e2 - e0).astype(np.float64)
            nrm = np.linalg.norm(raw_n, axis=1, keepdims=True)
            nrm = np.where(nrm < 1e-12, 1.0, nrm)
            normals_parts.append(raw_n / nrm)
        normals = np.concatenate(normals_parts, axis=0)

        edge_to_elems = {}
        elem_edges    = [None] * total
        for d in etype_meta:
            conn   = d['conn']
            nc     = d['n_corner']
            off    = d['offset']
            N      = d['N']
            rolled = np.roll(conn, -1, axis=1)
            e_min  = np.minimum(conn, rolled).tolist()
            e_max  = np.maximum(conn, rolled).tolist()
            for ei in range(N):
                gi    = ei + off
                edges = [(e_min[ei][j], e_max[ei][j]) for j in range(nc)]
                elem_edges[gi] = edges
                for edge in edges:
                    if edge not in edge_to_elems:
                        edge_to_elems[edge] = []
                    edge_to_elems[edge].append(gi)

        new_sid = np.full(total, -1, dtype=np.int32)
        visited = np.zeros(total, dtype=bool)
        for start in range(total):
            if visited[start]:
                continue
            base_sid = int(all_sid[start])
            comp_id  = next_id
            next_id += 1
            stack = [start]
            visited[start] = True
            new_sid[start] = comp_id
            while stack:
                cur = stack.pop()
                for edge in elem_edges[cur]:
                    for nb in edge_to_elems.get(edge, []):
                        if visited[nb]:
                            continue
                        if int(all_sid[nb]) != base_sid:
                            continue
                        if float(np.dot(normals[cur], normals[nb])) >= cos_thresh:
                            visited[nb] = True
                            new_sid[nb] = comp_id
                            stack.append(nb)

        for d in etype_meta:
            grp = f['elements/{}'.format(d['name'])]
            del grp['section_id']
            grp.create_dataset('section_id',
                               data=new_sid[d['offset']:d['offset'] + d['N']])

        n_before = int(np.unique(all_sid[all_sid >= 0]).size)
        n_after  = int(np.unique(new_sid[new_sid >= 0]).size)
        print('    [shells combined {}] section refine: {} → {} domains'.format(
            '+'.join(shell_etypes), n_before, n_after))


# ─── pyNastran xref helpers ───────────────────────────────────────────────────

def _unwrap_mid(mid_attr):
    """
    xref=True 时 prop.mid 可能是 MAT* 对象而非整数；统一转成 str。
    例：prop.mid → MAT1(mid=3) → '3'
    """
    if mid_attr is None:
        return ''
    if hasattr(mid_attr, 'mid'):
        return str(mid_attr.mid)
    try:
        return str(int(mid_attr))
    except (TypeError, ValueError):
        return str(mid_attr)


def _first_val(v, default=0.0):
    """Handle scalar / ndarray / None safely."""
    if v is None:
        return default
    if hasattr(v, '__len__'):
        return float(v[0]) if len(v) > 0 else default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ─── Main packing logic ───────────────────────────────────────────────────────

def _pack_model(model, inst_name, workspace):
    """Write L1 HDF5 + manifest.db for a pre-loaded pyNastran model (BDF or OP2Geom)."""
    part_name = inst_name
    inst_safe = safe(inst_name)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    print('  Extracting nodes ...')
    t0 = time.time()
    # pyNastran ≥1.3: get_xyz_in_coord returns ndarray ordered by sorted nids
    node_labels = np.array(sorted(model.nodes.keys()), dtype=np.int32)
    node_coords = model.get_xyz_in_coord(cid=0).astype(np.float64)
    N_nodes = len(node_labels)
    print('    {} nodes ({})'.format(N_nodes, _fmt_t(time.time() - t0)))

    # ── Classify and group elements ───────────────────────────────────────────
    print('  Classifying elements ...')
    t0 = time.time()
    # abaqus_etype → {labels, conn_gids, pids, n_faces, n_corner}
    etype_groups = {}
    skipped_counts = {}
    node_label_set = set(node_labels.tolist())  # for GRID-point validation

    for eid, elem in model.elements.items():
        card = elem.type

        if card in _UNSUPPORTED_SKIP:
            skipped_counts[card] = skipped_counts.get(card, 0) + 1
            continue

        entry = NASTRAN_TO_ABAQUS.get(card)
        if entry is None:
            if card in ('CHEXA', 'CPENTA', 'CTETRA'):
                nids_all = list(elem.node_ids)
                nnode = len(nids_all)
                mapped = SOLID_BY_NNODE.get((card, nnode))
                if mapped is None:
                    skipped_counts['{}_{}'.format(card, nnode)] = (
                        skipped_counts.get('{}_{}'.format(card, nnode), 0) + 1)
                    continue
                abaqus_name, n_corner, n_faces = mapped
            else:
                skipped_counts[card] = skipped_counts.get(card, 0) + 1
                continue
        else:
            abaqus_name, n_corner, n_faces = entry

        # Extract up to n_corner GRID node IDs.
        # For MASS elements (n_corner=1) use only the first valid GRID node.
        # For elements with inline properties (CONROD, CELAS2, CDAMP2, CONM*, CMASS2)
        # node_ids may contain None or 0 for absent second nodes — filter those out.
        try:
            raw_nids = [n for n in elem.node_ids if n and n > 0 and n in node_label_set]
        except Exception:
            skipped_counts[card + '_node_err'] = skipped_counts.get(card + '_node_err', 0) + 1
            continue

        nids = raw_nids[:n_corner]
        if len(nids) < n_corner:
            # Not enough valid GRID nodes (e.g. grounded spring, scalar-point ref)
            skipped_counts[card + '_no_grid'] = skipped_counts.get(card + '_no_grid', 0) + 1
            continue

        pid = getattr(elem, 'pid', -1)
        try:
            pid = int(pid) if pid is not None else -1
        except (TypeError, ValueError):
            pid = -1
        if pid == 0:
            pid = -1

        if abaqus_name not in etype_groups:
            etype_groups[abaqus_name] = {
                'labels': [], 'conn_gids': [], 'pids': [],
                'n_faces': n_faces, 'n_corner': n_corner,
            }
        g = etype_groups[abaqus_name]
        g['labels'].append(eid)
        g['conn_gids'].append(nids)
        g['pids'].append(pid)

    for card, cnt in sorted(skipped_counts.items()):
        print('  WARNING: skipped {} {} element(s)'.format(cnt, card))
    print('  {} etype group(s). ({})'.format(len(etype_groups), _fmt_t(time.time() - t0)))

    # ── 4. Global PID → section_id mapping ───────────────────────────────────
    all_pids = sorted(set(
        p for g in etype_groups.values() for p in g['pids']
    ) - {-1})
    pid_to_sid = {pid: i for i, pid in enumerate(all_pids)}
    section_names = [str(p) for p in all_pids]

    # Precompute per-sid material name and section type (used by each etype group)
    sid_to_mat = {}
    sid_to_sty = {}
    for pid in all_pids:
        sid  = pid_to_sid[pid]
        prop = model.properties.get(pid)
        if prop is None:
            continue
        ptype = prop.type
        if ptype == 'PSHELL':
            # pyNastran uses mid1 for PSHELL, not mid
            mid_raw = getattr(prop, 'mid1', None) or getattr(prop, 'mid', None)
            sid_to_mat[sid] = _unwrap_mid(mid_raw)
            sid_to_sty[sid] = 'SHELL'
        elif ptype in ('PCOMP', 'PCOMPG'):
            # Layered composite shell — use first ply material as representative
            mids = getattr(prop, 'mids', None)
            first_mid = (mids[0] if mids and len(mids) > 0 else None)
            sid_to_mat[sid] = _unwrap_mid(first_mid)
            sid_to_sty[sid] = 'SHELL'
        elif ptype == 'PSHEAR':
            mid_raw = getattr(prop, 'mid1', None) or getattr(prop, 'mid', None)
            sid_to_mat[sid] = _unwrap_mid(mid_raw)
            sid_to_sty[sid] = 'SHEAR'
        elif ptype == 'PSOLID':
            sid_to_mat[sid] = _unwrap_mid(getattr(prop, 'mid', None))
            sid_to_sty[sid] = 'SOLID'
        elif ptype in ('PBAR', 'PBEAM', 'PBEND', 'PROD', 'PTUBE', 'PBARL', 'PBEAML'):
            sid_to_mat[sid] = _unwrap_mid(getattr(prop, 'mid', None))
            sid_to_sty[sid] = 'BEAM'
        elif ptype in ('PBUSH', 'PBUSH1D', 'PELAS'):
            sid_to_mat[sid] = ''
            sid_to_sty[sid] = 'SPRING'
        elif ptype == 'PDAMP':
            sid_to_mat[sid] = ''
            sid_to_sty[sid] = 'DAMPER'
        elif ptype == 'PMASS':
            sid_to_mat[sid] = ''
            sid_to_sty[sid] = 'MASS'
        else:
            sid_to_mat[sid] = ''
            sid_to_sty[sid] = ptype

    # ── 5. Setup output directories ───────────────────────────────────────────
    l1_dir   = os.path.join(workspace, 'l1')
    geom_dir = os.path.join(l1_dir, 'geometry')
    sets_dir = os.path.join(l1_dir, 'sets')
    mkdirs(geom_dir)
    mkdirs(sets_dir)

    h5_rel = os.path.join('l1', 'geometry', inst_safe + '.h5')
    h5_abs = os.path.join(workspace, h5_rel)

    # ── 6. Write geometry HDF5 ────────────────────────────────────────────────
    print('  Writing geometry/{}.h5 ...'.format(inst_safe))
    t0 = time.time()

    bbox_min_v = node_coords.min(axis=0).tolist()
    bbox_max_v = node_coords.max(axis=0).tolist()
    total_elem_count = sum(len(g['labels']) for g in etype_groups.values())

    etd_rows = []   # (inst, etype, count, has_midnodes, n_corner, n_faces)
    pairs_nr = []   # for node_to_elements CSR — node row indices
    pairs_el = []   # for node_to_elements CSR — element labels

    with h5py.File(h5_abs, 'w') as f:
        # Nodes
        f.create_dataset('nodes/labels', data=node_labels)
        f.create_dataset('nodes/coords', data=node_coords)

        # Global section_names list (shared index across all etypes)
        if section_names:
            str_ds(f, 'section_names', section_names)

        # Elements
        for abaqus_name, g in etype_groups.items():
            labels_arr = np.array(g['labels'],   dtype=np.int32)
            conn_gids  = np.array(g['conn_gids'], dtype=np.int32)  # [M, n_corner]
            n_corner   = conn_gids.shape[1]
            n_faces_et = g['n_faces']

            # Convert node GIDs → 0-based row indices into sorted node_labels
            conn_rows = np.searchsorted(node_labels, conn_gids).astype(np.int32)

            # section_id from global PID mapping
            sec_ids = np.array([pid_to_sid.get(p, -1) for p in g['pids']], dtype=np.int32)

            # face_* arrays (needed by L2 surface extraction)
            face_def = FACE_DEFS.get(abaqus_name, [])
            fei_list, fseq_list, fnc_list = [], [], []
            for ei, row in enumerate(conn_rows):
                for fi, face_local in enumerate(face_def):
                    fei_list.append(ei)
                    fseq_list.append(fi)
                    fnc_list.append([int(row[k]) for k in face_local])

            grp = f.require_group('elements/{}'.format(abaqus_name))
            grp.create_dataset('labels',     data=labels_arr)
            grp.create_dataset('conn',       data=conn_rows)
            grp.create_dataset('section_id', data=sec_ids)

            if fei_list:
                # Pad to uniform width (some element types e.g. C3D6 mix tri/quad faces)
                max_fn = max(len(r) for r in fnc_list)
                fnc_padded = np.full((len(fnc_list), max_fn), -1, dtype=np.int32)
                for _i, _r in enumerate(fnc_list):
                    fnc_padded[_i, :len(_r)] = _r
                grp.create_dataset('face_elem_idx',
                                   data=np.array(fei_list, dtype=np.int32))
                grp.create_dataset('face_seq',
                                   data=np.array(fseq_list, dtype=np.int32))
                grp.create_dataset('face_node_conn', data=fnc_padded)
            else:
                grp.create_dataset('face_elem_idx',  data=np.array([], dtype=np.int32))
                grp.create_dataset('face_seq',       data=np.array([], dtype=np.int32))
                grp.create_dataset('face_node_conn', data=np.array([], dtype=np.int32))

            # Per-element color-code datasets (mirrors l1_pack.py)
            mat_arr = np.array(
                [sid_to_mat.get(int(s), '').encode('ascii')[:63] for s in sec_ids],
                dtype='S64')
            sec_arr = np.array(
                [sid_to_sty.get(int(s), '').encode('ascii')[:15] for s in sec_ids],
                dtype='S16')
            grp.create_dataset('material_name', data=mat_arr)
            grp.create_dataset('section_type',  data=sec_arr)

            # Accumulate node_to_elements pairs
            for j in range(len(labels_arr)):
                for nr in conn_rows[j]:
                    if int(nr) >= 0:
                        pairs_nr.append(int(nr))
                        pairs_el.append(int(labels_arr[j]))

            etd_rows.append((inst_name, abaqus_name, len(labels_arr), 0, n_corner, n_faces_et))
            print('    {}: {} elem(s)'.format(abaqus_name, len(labels_arr)))

        # Sections
        for pid in all_pids:
            prop = model.properties.get(pid)
            if prop is None:
                continue
            ptype = prop.type
            sg = f.require_group('sections/{}'.format(pid))
            sg.attrs['element_set'] = 'P{}_ELEMS'.format(pid)
            if ptype == 'PSHELL':
                # pyNastran uses mid1 for PSHELL, not mid
                mid_raw = getattr(prop, 'mid1', None) or getattr(prop, 'mid', None)
                sg.attrs['type']          = 'SHELL'
                sg.attrs['thickness']     = _first_val(getattr(prop, 't', None))
                sg.attrs['material_name'] = _unwrap_mid(mid_raw)
            elif ptype in ('PCOMP', 'PCOMPG'):
                # Layered composite — report total thickness and first ply material
                total_t = 0.0
                if hasattr(prop, 'TotalThickness'):
                    try:
                        total_t = float(prop.TotalThickness())
                    except Exception:
                        pass
                elif hasattr(prop, 'thicknesses'):
                    try:
                        total_t = float(sum(prop.thicknesses))
                    except Exception:
                        pass
                mids = getattr(prop, 'mids', None)
                first_mid = _unwrap_mid(mids[0] if mids and len(mids) > 0 else None)
                sg.attrs['type']          = 'SHELL'
                sg.attrs['thickness']     = total_t
                sg.attrs['material_name'] = first_mid
            elif ptype == 'PSOLID':
                mid_raw = getattr(prop, 'mid', None)
                if hasattr(mid_raw, 'mid'):
                    mid_raw = mid_raw.mid
                sg.attrs['type']          = 'SOLID'
                sg.attrs['thickness']     = float('nan')
                sg.attrs['material_name'] = _unwrap_mid(mid_raw)
            elif ptype == 'PSHEAR':
                mid_raw = getattr(prop, 'mid1', None) or getattr(prop, 'mid', None)
                sg.attrs['type']          = 'SHEAR'
                sg.attrs['thickness']     = _first_val(getattr(prop, 't', None))
                sg.attrs['material_name'] = _unwrap_mid(mid_raw)
            elif ptype in ('PBAR', 'PBEAM', 'PBEND', 'PROD', 'PTUBE', 'PBARL', 'PBEAML'):
                sg.attrs['type']          = 'BEAM'
                sg.attrs['thickness']     = float('nan')
                sg.attrs['material_name'] = _unwrap_mid(getattr(prop, 'mid', None))
            elif ptype in ('PBUSH', 'PBUSH1D', 'PELAS'):
                sg.attrs['type']          = 'SPRING'
                sg.attrs['thickness']     = float('nan')
                sg.attrs['material_name'] = ''
            elif ptype == 'PDAMP':
                sg.attrs['type']          = 'DAMPER'
                sg.attrs['thickness']     = float('nan')
                sg.attrs['material_name'] = ''
            elif ptype == 'PMASS':
                sg.attrs['type']          = 'MASS'
                sg.attrs['thickness']     = float('nan')
                sg.attrs['material_name'] = ''
            else:
                sg.attrs['type']          = ptype
                sg.attrs['thickness']     = float('nan')
                sg.attrs['material_name'] = ''

        # Materials
        for mid, mat in model.materials.items():
            mg = f.require_group('materials/{}'.format(mid))
            if mat.type == 'MAT1':
                mg.attrs['type'] = 'ISOTROPIC'
                mg.create_dataset(
                    'elastic_table',
                    data=np.array([[_first_val(mat.e), _first_val(mat.nu)]],
                                  dtype=np.float64))
            elif mat.type == 'MAT8':
                mg.attrs['type'] = 'ORTHOTROPIC'
                mg.create_dataset(
                    'elastic_table',
                    data=np.array([[_first_val(mat.e11), _first_val(mat.e22),
                                    _first_val(mat.nu12), _first_val(mat.g12),
                                    _first_val(getattr(mat, 'g1z', None)),
                                    _first_val(getattr(mat, 'g2z', None))]],
                                  dtype=np.float64))
            elif mat.type == 'MAT9':
                mg.attrs['type'] = 'ANISO3D'
            else:
                mg.attrs['type'] = str(mat.type)

        # Instance sets — SET1 (node sets)
        isets_node_counts = {}
        for sid, s in model.sets.items():
            if getattr(s, 'type', '') == 'SET1' or not hasattr(s, 'type'):
                set_safe = safe('SET1_{}'.format(sid))
                ids_arr = np.array(sorted(s.ids), dtype=np.int32)
                f.create_dataset('instance_sets/node_sets/{}'.format(set_safe), data=ids_arr)
                isets_node_counts[set_safe] = len(ids_arr)

        # Instance sets — SET3 (element sets, stored in bdf.set3s in some pyNastran versions)
        isets_elem_counts = {}
        for sid, s in getattr(model, 'set3s', {}).items():
            set_safe = safe('SET3_{}'.format(sid))
            ids_arr = np.array(sorted(s.ids), dtype=np.int32)
            f.create_dataset('instance_sets/element_sets/{}'.format(set_safe), data=ids_arr)
            isets_elem_counts[set_safe] = len(ids_arr)

        # node_to_elements CSR (used by L3 pick/probe query)
        if pairs_nr:
            pairs_nr_arr = np.array(pairs_nr, dtype=np.int32)
            pairs_el_arr = np.array(pairs_el, dtype=np.int32)
            order        = np.argsort(pairs_nr_arr, kind='stable')
            pairs_nr_arr = pairs_nr_arr[order]
            pairs_el_arr = pairs_el_arr[order]
            offsets = np.zeros(N_nodes + 1, dtype=np.int32)
            np.add.at(offsets[1:], pairs_nr_arr, 1)
            np.cumsum(offsets, out=offsets)
            csr = f.require_group('node_to_elements')
            csr.create_dataset('offsets',         data=offsets,      compression='gzip')
            csr.create_dataset('elem_label_data', data=pairs_el_arr, compression='gzip')
        else:
            csr = f.require_group('node_to_elements')
            csr.create_dataset('offsets',         data=np.zeros(N_nodes + 1, dtype=np.int32))
            csr.create_dataset('elem_label_data', data=np.array([], dtype=np.int32))

    print('    done. ({})'.format(_fmt_t(time.time() - t0)))

    # Post-pass: subdivide shell section_ids by 20° normal connectivity
    _refine_shell_sections(h5_abs)

    # ── 7. Write assembly.h5 ──────────────────────────────────────────────────
    print('  Writing assembly.h5 ...')
    asm_path = os.path.join(l1_dir, 'assembly.h5')
    with h5py.File(asm_path, 'w') as f:
        grp = f.require_group('instances/{}'.format(inst_name))
        grp.create_dataset('transform', data=np.eye(4, dtype=np.float64))
        grp.create_dataset('part_name', data=part_name.encode('utf-8'))

    # ── 8. Write sets/sets.h5 (stub — Nastran has no assembly sets) ───────────
    sets_path = os.path.join(sets_dir, 'sets.h5')
    with h5py.File(sets_path, 'w') as _f:
        pass

    # ── 9. Write manifest.db ──────────────────────────────────────────────────
    print('  Writing manifest.db ...')
    db_conn = init_manifest(workspace)

    db_conn.execute(
        "INSERT OR REPLACE INTO instances VALUES (?,?,?,?,?,?,?,?)",
        (inst_name, part_name, h5_rel, None,
         N_nodes, total_elem_count,
         json.dumps(bbox_min_v), json.dumps(bbox_max_v)),
    )

    for row in etd_rows:
        db_conn.execute(
            "INSERT OR REPLACE INTO element_type_dist VALUES (?,?,?,?,?,?)",
            row,
        )

    for sname, cnt in isets_node_counts.items():
        db_conn.execute(
            "INSERT OR REPLACE INTO node_sets VALUES (?,?,?,?,?)",
            (sname, inst_name, inst_name,
             h5_rel + ':instance_sets/node_sets/' + sname, cnt),
        )

    for sname, cnt in isets_elem_counts.items():
        db_conn.execute(
            "INSERT OR REPLACE INTO element_sets VALUES (?,?,?,?,?)",
            (sname, inst_name, inst_name,
             h5_rel + ':instance_sets/element_sets/' + sname, cnt),
        )

    db_conn.commit()
    db_conn.close()



def pack(bdf_path, workspace):
    from pyNastran.bdf.bdf import BDF
    t_total = time.time()
    bdf_basename = os.path.basename(bdf_path)
    inst_name = os.path.splitext(bdf_basename)[0].upper()
    print('BDF pack: {} → instance \'{}\''.format(bdf_basename, inst_name))
    print('  Reading BDF ...')
    t0 = time.time()
    model = BDF(debug=False)
    model.read_bdf(bdf_path, xref=True)
    print('  Read done. ({})'.format(_fmt_t(time.time() - t0)))
    _pack_model(model, inst_name, workspace)
    print('BDF pack complete. ({} total)'.format(_fmt_t(time.time() - t_total)))


def main():
    args      = parse_args()
    bdf_path  = os.path.abspath(args.bdf)
    workspace = os.path.abspath(args.workspace)
    if not os.path.exists(bdf_path):
        print('ERROR: BDF file not found: {}'.format(bdf_path), file=sys.stderr)
        sys.exit(1)
    mkdirs(workspace)
    pack(bdf_path, workspace)


if __name__ == '__main__':
    main()
