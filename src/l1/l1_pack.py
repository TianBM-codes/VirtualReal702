#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
l1_pack.py — Layer 1, Phase 2: npy + JSON → HDF5 + manifest.db

运行方式（普通 Python 3，需 h5py）:
    python l1_pack.py --workspace <workspace>

前提: abaqus_dump.py 已成功运行，<workspace>/l1_raw/ 目录存在。

输出:
    <workspace>/
        l1/assembly.h5
        l1/geometry/<inst>.h5  （及 _highorder.h5）
        l1/sets/sets.h5
        l1/results/<step>__<field>.h5
        manifest.db

完成后 l1_raw/ 可以删除（可选）。
"""

import argparse
import json
import os
import sqlite3
import sys
import time

import h5py
import numpy as np

from manifest_schema import MANIFEST_SCHEMA


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='npy → HDF5 packer (Phase 2)')
    p.add_argument('--workspace', required=True)
    p.add_argument('--keep-raw', action='store_true',
                   help='Do not delete l1_raw/ after packing')
    # ── project-grouping params ──────────────────────────────────────────────
    p.add_argument('--result-group', default=None,
                   help='Result group name; enables append mode (only pack results, '
                        'skip geometry/sets, output to l1/results/<result_group>/)')
    p.add_argument('--display-name', default=None,
                   help='Display name written to result_group_meta; '
                        'defaults to --result-group value if not provided')
    p.add_argument('--consistency-check', default='count-only',
                   choices=['count-only', 'label-only'],
                   help='Consistency check mode used for this result_group (recorded in meta)')
    p.add_argument('--source-file', default=None,
                   help='Original ODB filename (for audit/display, not execution)')
    return p.parse_args()


def mkdirs(path):
    if not os.path.exists(path):
        os.makedirs(path)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def nload(path):
    return np.load(path)


def str_ds(f, path, strings):
    """Write variable-length string dataset."""
    dt = h5py.special_dtype(vlen=str)
    ds = f.create_dataset(path, shape=(len(strings),), dtype=dt)
    for i, s in enumerate(strings):
        ds[i] = str(s)
    return ds


def safe(name):
    return name.replace('/', '__').replace('\\', '__').replace(' ', '_')


def _fmt_t(secs):
    """Format elapsed seconds as '1m 23.4s' or '5.2s'."""
    if secs >= 60:
        return f"{int(secs) // 60}m {secs % 60:.1f}s"
    return f"{secs:.1f}s"


# ─── manifest.db ──────────────────────────────────────────────────────────────

def _migrate_manifest(conn):
    """给已存在的旧 manifest.db 补新增列（CREATE TABLE IF NOT EXISTS 对旧表不生效）。"""
    migrations = {
        'steps': [('total_time', 'REAL'), ('time_period', 'REAL')],
    }
    for table, columns in migrations.items():
        existing = {r[1] for r in conn.execute(
            "PRAGMA table_info({})".format(table)).fetchall()}
        for col, coltype in columns:
            if col not in existing:
                conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, col, coltype))


def init_manifest(workspace):
    db_path = os.path.join(workspace, 'manifest.db')
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(MANIFEST_SCHEMA)
    _migrate_manifest(conn)
    conn.commit()
    return conn


# ─── Pack assembly ────────────────────────────────────────────────────────────

def pack_assembly(raw_dir, workspace, meta):
    h5_path = os.path.join(workspace, 'l1', 'assembly.h5')
    asm_raw = os.path.join(raw_dir, 'assembly')

    t0 = time.time()
    print("  assembly.h5 ...")
    with h5py.File(h5_path, 'w') as f:
        # Instances
        inst_dir = os.path.join(asm_raw, 'instances')
        for inst_name in (meta.get('instances') or {}).keys():
            s = safe(inst_name)
            d = os.path.join(inst_dir, s)
            grp = f.require_group('instances/{}'.format(inst_name))
            grp.create_dataset('transform', data=nload(os.path.join(d, 'transform.npy')))
            with open(os.path.join(d, 'part_name.txt')) as fp:
                grp.create_dataset('part_name', data=fp.read().encode('utf-8'))

        # Datum coordinate systems (from ODB datumCsyses)
        datum_csys_path = os.path.join(asm_raw, 'datum_csyses.json')
        if os.path.exists(datum_csys_path):
            with open(datum_csys_path) as fp:
                import json as _json
                datum_csyses = _json.load(fp)
            for name, dc in datum_csyses.items():
                grp = f.require_group('orientations/{}'.format(safe(name)))
                grp.create_dataset('origin',  data=np.array(dc['origin'],  dtype=np.float64))
                grp.create_dataset('point_a', data=np.array(dc['point_a'], dtype=np.float64))
                grp.create_dataset('point_b', data=np.array(dc['point_b'], dtype=np.float64))
                grp.attrs['system']        = dc.get('system', 'RECTANGULAR')
                grp.attrs['original_name'] = name

        # Assembly sets
        asmsets_raw = os.path.join(asm_raw, 'asmsets')
        if os.path.exists(asmsets_raw):
            for set_safe in os.listdir(asmsets_raw):
                set_dir = os.path.join(asmsets_raw, set_safe)
                # Reconstruct original name (best effort — safe() is not reversible)
                # Use safe name as the HDF5 key; consumers look up via manifest.db anyway
                for inst_safe in os.listdir(set_dir):
                    inst_dir2 = os.path.join(set_dir, inst_safe)
                    grp = f.require_group('assembly_sets/{}/{}'.format(set_safe, inst_safe))
                    for fname, dsname in [('node_labels.npy', 'node_labels'),
                                          ('elem_labels.npy', 'elem_labels')]:
                        p = os.path.join(inst_dir2, fname)
                        if os.path.exists(p):
                            grp.create_dataset(dsname, data=nload(p))

    print(f"    done. ({_fmt_t(time.time() - t0)})")


# ─── Shell section refinement ─────────────────────────────────────────────────

# Element type names whose surface normal defines an averaging domain.
# Solid elements (C3D*) are intentionally excluded.
_SHELL_PREFIXES = ('S3', 'S4', 'S6', 'S8', 'SC', 'STRI', 'M3D')


def _is_shell_etype(name):
    up = name.upper()
    return any(up.startswith(p) for p in _SHELL_PREFIXES)


def _refine_shell_sections(h5_path, angle_thresh_deg=20.0):
    """
    Post-pass on a geometry H5 file: across ALL shell element types combined,
    subdivide section_ids into connected components where adjacent elements
    (sharing an edge) have surface-normal angle ≤ angle_thresh_deg.

    Shell etypes are processed together so that S3R/S4R elements sharing an
    edge are considered neighbours.  Solid elements are left unchanged.
    """
    cos_thresh = np.cos(np.radians(angle_thresh_deg))

    with h5py.File(h5_path, 'a') as f:
        if 'elements' not in f or 'nodes/coords' not in f:
            return

        coords = f['nodes/coords'][:]   # [N_nodes, 3]

        # Collect all shell etypes that have both conn and section_id.
        shell_etypes = [
            e for e in sorted(f['elements'].keys())
            if _is_shell_etype(e)
            and 'conn' in f['elements/{}'.format(e)]
            and 'section_id' in f['elements/{}'.format(e)]
        ]
        if not shell_etypes:
            return

        # Starting ID for new sub-domain IDs — must not collide with any existing ID.
        max_sid = -1
        for etype_safe in f['elements']:
            grp = f['elements/{}'.format(etype_safe)]
            if 'section_id' in grp:
                arr = grp['section_id'][:]
                valid = arr[arr >= 0]
                if len(valid):
                    max_sid = max(max_sid, int(valid.max()))
        next_id = max_sid + 1

        # Build combined per-etype metadata (global element indices).
        etype_meta = []
        offset = 0
        for etype_safe in shell_etypes:
            grp   = f['elements/{}'.format(etype_safe)]
            conn  = grp['conn'][:]
            sid   = grp['section_id'][:]
            N, nc = conn.shape
            etype_meta.append({
                'name':       etype_safe,
                'conn':       conn,
                'section_id': sid,
                'n_corner':   nc,
                'offset':     offset,
                'N':          N,
            })
            offset += N
        total = offset

        # Combined section_id and normals arrays.
        all_sid = np.concatenate([d['section_id'] for d in etype_meta])

        normals_parts = []
        for d in etype_meta:
            conn = d['conn']
            e0 = coords[conn[:, 0]]
            e1 = coords[conn[:, 1]]
            e2 = coords[conn[:, 2]]
            raw_n = np.cross(e1 - e0, e2 - e0).astype(np.float64)
            nrm   = np.linalg.norm(raw_n, axis=1, keepdims=True)
            nrm   = np.where(nrm < 1e-12, 1.0, nrm)
            normals_parts.append(raw_n / nrm)
        normals = np.concatenate(normals_parts, axis=0)   # [total, 3]

        # Build unified edge → [global_elem_idx, ...] adjacency across all etypes.
        # Also pre-build per-element edge list for fast BFS access.
        edge_to_elems = {}
        elem_edges    = [None] * total   # elem_edges[gi] = list of (min,max) edge tuples

        for d in etype_meta:
            conn     = d['conn']
            nc       = d['n_corner']
            off      = d['offset']
            N        = d['N']
            rolled   = np.roll(conn, -1, axis=1)
            e_min    = np.minimum(conn, rolled).tolist()
            e_max    = np.maximum(conn, rolled).tolist()
            for ei in range(N):
                gi    = ei + off
                edges = [(e_min[ei][j], e_max[ei][j]) for j in range(nc)]
                elem_edges[gi] = edges
                for edge in edges:
                    if edge not in edge_to_elems:
                        edge_to_elems[edge] = []
                    edge_to_elems[edge].append(gi)

        # BFS across all shell elements combined.
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

        # Write refined section_ids back to each etype.
        for d in etype_meta:
            grp = f['elements/{}'.format(d['name'])]
            del grp['section_id']
            grp.create_dataset('section_id',
                               data=new_sid[d['offset']:d['offset'] + d['N']])

        n_before = int(np.unique(all_sid[all_sid >= 0]).size)
        n_after  = int(np.unique(new_sid[new_sid >= 0]).size)
        print("      [shells combined {}] section refine: {} assignment(s) → {} domain(s)".format(
            '+'.join(shell_etypes), n_before, n_after))


# ─── Pack geometry ────────────────────────────────────────────────────────────

def pack_geometry(raw_dir, workspace, meta, db_conn):
    geom_raw = os.path.join(raw_dir, 'geom')
    geom_dir = os.path.join(workspace, 'l1', 'geometry')

    t_geom = time.time()
    print("  geometry/ ...")
    for inst_name, gm in (meta.get('geom') or {}).items():
        s   = gm['safe_name']
        d   = os.path.join(geom_raw, s)

        h5_rel = os.path.join('l1', 'geometry', s + '.h5')
        h5_abs = os.path.join(workspace, h5_rel)

        t_inst = time.time()
        print("    {} ...".format(inst_name))
        with h5py.File(h5_abs, 'w') as f:
            # Nodes
            f.create_dataset('nodes/labels', data=nload(os.path.join(d, 'node_labels.npy')))
            f.create_dataset('nodes/coords', data=nload(os.path.join(d, 'node_coords.npy')))

            # Elements
            elems_raw = os.path.join(d, 'elems')
            if os.path.exists(elems_raw):
                # Build section-id → material_name / section_type lookup for color-code datasets
                _sec_names_list = []
                _si_raw         = {}
                _sn_path = os.path.join(d, 'section_names.json')
                _si_path = os.path.join(d, 'sections.json')
                if os.path.exists(_sn_path) and os.path.exists(_si_path):
                    _sec_names_list = load_json(_sn_path)
                    _si_raw         = load_json(_si_path)
                # sections.json is a list (new) or dict (old) — handle both
                if isinstance(_si_raw, list):
                    _sid_to_mat = {i: _si_raw[i].get('material_name', '')
                                   for i in range(min(len(_si_raw), len(_sec_names_list)))}
                    _sid_to_sty = {i: _si_raw[i].get('type', '')
                                   for i in range(min(len(_si_raw), len(_sec_names_list)))}
                else:
                    _sid_to_mat = {i: _si_raw.get(n, {}).get('material_name', '')
                                   for i, n in enumerate(_sec_names_list)}
                    _sid_to_sty = {i: _si_raw.get(n, {}).get('type', '')
                                   for i, n in enumerate(_sec_names_list)}

                for etype_safe in os.listdir(elems_raw):
                    td  = os.path.join(elems_raw, etype_safe)
                    grp = f.require_group('elements/{}'.format(etype_safe))
                    for fname, dsname in [
                        ('labels.npy',        'labels'),
                        ('conn.npy',          'conn'),
                        ('section_id.npy',    'section_id'),
                        ('face_elem_idx.npy', 'face_elem_idx'),
                        ('face_seq.npy',      'face_seq'),
                        ('face_node_conn.npy','face_node_conn'),
                    ]:
                        p = os.path.join(td, fname)
                        if os.path.exists(p):
                            grp.create_dataset(dsname, data=nload(p))
                    # Per-element color-code datasets (mirrors INP exporter.py)
                    sid_p = os.path.join(td, 'section_id.npy')
                    if os.path.exists(sid_p) and _sec_names_list:
                        _sid_arr = nload(sid_p).astype(np.int32)
                        mat_arr = np.array(
                            [_sid_to_mat.get(int(s), '').encode('ascii')[:63]
                             for s in _sid_arr], dtype='S64')
                        sec_arr = np.array(
                            [_sid_to_sty.get(int(s), '').encode('ascii')[:15]
                             for s in _sid_arr], dtype='S16')
                        grp.create_dataset('material_name', data=mat_arr)
                        grp.create_dataset('section_type',  data=sec_arr)

            # Coupling spider lines (DCOUP3D / CONN3D / SPRING2 ... expanded into
            # ref→leaf segments by abaqus_dump). Same dataset key the INP exporter
            # writes, so L2 collect_couplings + L3 /couplings + the front end's
            # RBE2-spider display all work unchanged for the ODB path too.
            cpl_path = os.path.join(d, 'couplings_positions.npy')
            if os.path.exists(cpl_path):
                f.create_dataset('couplings/positions', data=nload(cpl_path))
            # Parallel [N*2] node rows for deform (per-node U lookup); L2
            # collect_couplings passes it through to surface.h5 unchanged.
            cpl_rows_path = os.path.join(d, 'couplings_node_rows.npy')
            if os.path.exists(cpl_rows_path):
                f.create_dataset('couplings/node_rows', data=nload(cpl_rows_path))

            # Special (non-surface) elements: raw connectivity kept for the future
            # (picking / results on couplings, connectors, masses, ...). Not read
            # by L2/L3 yet — purely archival so nothing is lost.
            special_raw = os.path.join(d, 'special')
            if os.path.exists(special_raw):
                for etype_safe in os.listdir(special_raw):
                    std = os.path.join(special_raw, etype_safe)
                    sgrp = f.require_group('elements_special/{}'.format(etype_safe))
                    for fname, dsname in [
                        ('labels.npy',       'labels'),
                        ('conn_flat.npy',    'conn_flat'),
                        ('conn_offsets.npy', 'conn_offsets'),
                    ]:
                        p = os.path.join(std, fname)
                        if os.path.exists(p):
                            sgrp.create_dataset(dsname, data=nload(p))

            # Sections
            sec_path = os.path.join(d, 'sections.json')
            if os.path.exists(sec_path):
                sec_data = load_json(sec_path)
                # sections.json is a list (new) or dict (old) — handle both.
                # New: each entry is one sectionAssignment; key = "{i}__{safe(name)}"
                # Old: keyed by sectionName (one entry per unique name).
                # print("[DBG] sections.json type={} len={}".format(
                #     type(sec_data).__name__, len(sec_data) if sec_data else 0))
                if isinstance(sec_data, list):
                    sec_items = []
                    for i, se in enumerate(sec_data):
                        # print("[DBG] item[{}] type={} val={}".format(i, type(se).__name__, repr(se)[:120]))
                        grp_key = '{}__{}'.format(i, safe(se.get('section_name', str(i))))
                        sec_items.append((grp_key, se))
                else:
                    sec_items = list(sec_data.items())
                for grp_key, sinfo in sec_items:
                    sg = f.require_group('sections/{}'.format(grp_key))
                    sg.attrs['element_set']   = sinfo.get('element_set', '')
                    sg.attrs['material_name'] = sinfo.get('material_name', '')
                    sg.attrs['type']          = sinfo.get('type', '')
                    sg.attrs['thickness']     = float(sinfo.get('thickness') or float('nan'))

            sec_names_path = os.path.join(d, 'section_names.json')
            if os.path.exists(sec_names_path):
                names = load_json(sec_names_path)
                if names:
                    f.create_dataset('section_names',
                                     data=np.array(names, dtype=object),
                                     dtype=h5py.special_dtype(vlen=str))

            # Materials
            mat_path = os.path.join(d, 'materials.json')
            if os.path.exists(mat_path):
                for mname, minfo in load_json(mat_path).items():
                    mg = f.require_group('materials/{}'.format(mname))
                    mg.attrs['type'] = minfo.get('type', '')
                    if 'elastic_table' in minfo:
                        mg.create_dataset('elastic_table',
                                          data=np.array(minfo['elastic_table'], dtype=np.float64))

            # Instance sets
            isets_node_dir = os.path.join(d, 'isets', 'node_sets')
            if os.path.exists(isets_node_dir):
                for fname in os.listdir(isets_node_dir):
                    sname = fname[:-4]  # strip .npy (safe name)
                    arr   = nload(os.path.join(isets_node_dir, fname))
                    f.create_dataset('instance_sets/node_sets/{}'.format(sname), data=arr)

            isets_elem_dir = os.path.join(d, 'isets', 'elem_sets')
            if os.path.exists(isets_elem_dir):
                for fname in os.listdir(isets_elem_dir):
                    sname = fname[:-4]
                    arr   = nload(os.path.join(isets_elem_dir, fname))
                    f.create_dataset('instance_sets/element_sets/{}'.format(sname), data=arr)

            # node_to_elements CSR: node_row → attached elem labels
            # Used by L3 pick query (Attached Elements column in Probe table).
            # Layout:
            #   /node_to_elements/offsets        [N+1] int32  — CSR row-pointer
            #   /node_to_elements/elem_label_data [M]   int32  — flattened elem labels
            if 'elements' in f and 'nodes/labels' in f:
                n_nodes = f['nodes/labels'].shape[0]
                # Collect (node_row, elem_label) pairs across all etype groups
                pairs_nr = []   # node rows
                pairs_el = []   # elem labels
                for etype_safe in f['elements']:
                    grp = f['elements/{}'.format(etype_safe)]
                    if 'labels' not in grp or 'conn' not in grp:
                        continue
                    elem_labels = grp['labels'][:]          # [num_elems]
                    conn        = grp['conn'][:]             # [num_elems, n_corner_nodes]
                    for j in range(len(elem_labels)):
                        for nr in conn[j]:
                            if nr >= 0:
                                pairs_nr.append(int(nr))
                                pairs_el.append(int(elem_labels[j]))

                if pairs_nr:
                    pairs_nr = np.array(pairs_nr, dtype=np.int32)
                    pairs_el = np.array(pairs_el, dtype=np.int32)
                    # Sort by node_row for CSR ordering
                    order    = np.argsort(pairs_nr, kind='stable')
                    pairs_nr = pairs_nr[order]
                    pairs_el = pairs_el[order]
                    # Build CSR offsets
                    offsets = np.zeros(n_nodes + 1, dtype=np.int32)
                    np.add.at(offsets[1:], pairs_nr, 1)
                    np.cumsum(offsets, out=offsets)
                    g = f.require_group('node_to_elements')
                    g.create_dataset('offsets',         data=offsets,   compression='gzip')
                    g.create_dataset('elem_label_data', data=pairs_el,  compression='gzip')
                else:
                    # No elements — write empty CSR so readers don't have to handle missing group
                    g = f.require_group('node_to_elements')
                    g.create_dataset('offsets',         data=np.zeros(n_nodes + 1, dtype=np.int32))
                    g.create_dataset('elem_label_data', data=np.array([], dtype=np.int32))

        # Post-pass: subdivide shell section_ids by 20° normal connectivity
        _refine_shell_sections(h5_abs)

        # High-order file
        ho_rel = None
        ho_raw = os.path.join(d, 'highorder')
        if os.path.exists(ho_raw) and os.listdir(ho_raw):
            ho_rel = os.path.join('l1', 'geometry', s + '_highorder.h5')
            ho_abs = os.path.join(workspace, ho_rel)
            with h5py.File(ho_abs, 'w') as f:
                for etype_safe in os.listdir(ho_raw):
                    hod = os.path.join(ho_raw, etype_safe)
                    grp = f.require_group('elements/{}'.format(etype_safe))
                    for fname, dsname in [
                        ('conn_full.npy',       'conn_full'),
                        ('midnode_indices.npy',  'midnode_indices'),
                    ]:
                        p = os.path.join(hod, fname)
                        if os.path.exists(p):
                            grp.create_dataset(dsname, data=nload(p))

        # manifest.db: instances
        db_conn.execute(
            "INSERT OR REPLACE INTO instances VALUES (?,?,?,?,?,?,?,?)",
            (inst_name, gm['part_name'], h5_rel, ho_rel,
             gm['node_count'], gm['elem_count'],
             json.dumps(gm['bbox_min']), json.dumps(gm['bbox_max']))
        )
        for etype, em in gm.get('elem_types', {}).items():
            db_conn.execute(
                "INSERT OR REPLACE INTO element_type_dist VALUES (?,?,?,?,?,?)",
                (inst_name, etype, em['count'], em['has_midnodes'],
                 em['n_corner_nodes'], em['n_faces'])
            )

        # manifest.db: instance node/element sets
        for sname, cnt in gm.get('isets_node', {}).items():
            db_conn.execute(
                "INSERT OR REPLACE INTO node_sets VALUES (?,?,?,?,?)",
                (sname, inst_name, inst_name,
                 h5_rel + ':instance_sets/node_sets/' + safe(sname), cnt)
            )
        for sname, cnt in gm.get('isets_elem', {}).items():
            db_conn.execute(
                "INSERT OR REPLACE INTO element_sets "
                "(set_name, set_scope, instance_name, h5_path, elem_count) "
                "VALUES (?,?,?,?,?)",
                (sname, inst_name, inst_name,
                 h5_rel + ':instance_sets/element_sets/' + safe(sname), cnt)
            )

        db_conn.commit()
        print(f"      done. ({_fmt_t(time.time() - t_inst)})")

    print(f"  Geometry done. ({_fmt_t(time.time() - t_geom)} total)")


# ─── Pack sets ────────────────────────────────────────────────────────────────

def pack_sets(raw_dir, workspace, meta, db_conn):
    sets_raw = os.path.join(raw_dir, 'sets')
    h5_rel   = os.path.join('l1', 'sets', 'sets.h5')
    h5_abs   = os.path.join(workspace, h5_rel)

    t0 = time.time()
    print("  sets.h5 ...")
    with h5py.File(h5_abs, 'w') as f:
        # Assembly sets
        asmsets_raw = os.path.join(sets_raw, 'asmsets')
        if os.path.exists(asmsets_raw):
            for set_safe in os.listdir(asmsets_raw):
                set_dir = os.path.join(asmsets_raw, set_safe)
                for inst_safe in os.listdir(set_dir):
                    inst_dir = os.path.join(set_dir, inst_safe)
                    grp = f.require_group('assembly_sets/{}/{}'.format(set_safe, inst_safe))
                    for fname, dsname in [('node_labels.npy', 'node_labels'),
                                          ('elem_labels.npy', 'elem_labels')]:
                        p = os.path.join(inst_dir, fname)
                        if os.path.exists(p):
                            arr = nload(p)
                            grp.create_dataset(dsname, data=arr)
                            cnt = len(arr)
                            # Restore original names from safe names is impractical;
                            # use safe name as set_name key in manifest (consistent with
                            # what abaqus_dump.py wrote).
                            if dsname == 'node_labels':
                                db_conn.execute(
                                    "INSERT OR REPLACE INTO node_sets VALUES (?,?,?,?,?)",
                                    (set_safe, 'assembly', inst_safe,
                                     h5_rel + ':assembly_sets/{}/{}/node_labels'.format(
                                         set_safe, inst_safe),
                                     cnt)
                                )
                            else:
                                db_conn.execute(
                                    "INSERT OR REPLACE INTO element_sets "
                                    "(set_name, set_scope, instance_name, h5_path, elem_count) "
                                    "VALUES (?,?,?,?,?)",
                                    (set_safe, 'assembly', inst_safe,
                                     h5_rel + ':assembly_sets/{}/{}/elem_labels'.format(
                                         set_safe, inst_safe),
                                     cnt)
                                )

        # Part sets
        partsets_raw = os.path.join(sets_raw, 'partsets')
        if os.path.exists(partsets_raw):
            for part_safe in os.listdir(partsets_raw):
                pd = os.path.join(partsets_raw, part_safe)
                for kind, set_dir_name, table, scope_prefix, ds_key in [
                    ('node_sets',  'node_sets',  'node_sets',    'part',  'node_count'),
                    ('element_sets','elem_sets', 'element_sets', 'part',  'elem_count'),
                ]:
                    kind_dir = os.path.join(pd, set_dir_name.replace('element_', 'elem_'))
                    if not os.path.exists(kind_dir):
                        continue
                    for fname in os.listdir(kind_dir):
                        sname = fname[:-4]
                        arr   = nload(os.path.join(kind_dir, fname))
                        f.create_dataset(
                            'part_sets/{}/{}/{}'.format(part_safe, kind, sname),
                            data=arr)
                        if kind == 'node_sets':
                            db_conn.execute(
                                "INSERT OR IGNORE INTO node_sets VALUES (?,?,?,?,?)",
                                (sname, 'part:{}'.format(part_safe), part_safe,
                                 h5_rel + ':part_sets/{}/node_sets/{}'.format(part_safe, sname),
                                 len(arr))
                            )
                        else:
                            db_conn.execute(
                                "INSERT OR IGNORE INTO element_sets "
                                "(set_name, set_scope, instance_name, h5_path, elem_count) "
                                "VALUES (?,?,?,?,?)",
                                (sname, 'part:{}'.format(part_safe), part_safe,
                                 h5_rel + ':part_sets/{}/element_sets/{}'.format(part_safe, sname),
                                 len(arr))
                            )

    db_conn.commit()
    print(f"    done. ({_fmt_t(time.time() - t0)})")


# ─── Pack results ─────────────────────────────────────────────────────────────

def pack_results(raw_dir, workspace, meta, db_conn, result_group=None):
    """
    Pack result npy files into HDF5 and write manifest rows.

    result_group: if provided, output goes to l1/results/<result_group>/ and
                  all manifest rows include result_group. None = legacy flat layout.
    """
    results_raw = os.path.join(raw_dir, 'results')
    if result_group:
        results_out = os.path.join(workspace, 'l1', 'results', safe(result_group))
    else:
        results_out = os.path.join(workspace, 'l1', 'results')
    mkdirs(results_out)

    t_results = time.time()
    print("  results/ ...")

    steps_meta = meta.get('steps', {})

    # Write steps and frames to manifest.db
    for step_name, sm in steps_meta.items():
        db_conn.execute(
            "INSERT OR REPLACE INTO steps"
            " (result_group, step_name, step_number, procedure, num_frames,"
            "  description, nlgeom, total_time, time_period)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (result_group, step_name, sm['step_number'], sm['procedure'], sm['num_frames'],
             sm.get('description'), sm.get('nlgeom', 0),
             sm.get('total_time'), sm.get('time_period'))
        )
        for fm in sm['frames']:
            db_conn.execute(
                "INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (result_group, step_name,
                 fm['frame_idx'], fm['frame_value'], fm['description'],
                 fm.get('domain'), fm.get('frequency'), fm.get('mode_number'),
                 fm.get('increment_number'), fm.get('is_imaginary'),
                 fm.get('frame_id'), fm.get('cyclic_mode_number'), fm.get('load_case'))
            )
    db_conn.commit()

    if not os.path.exists(results_raw):
        print("  (no results directory)")
        return

    for field_dir_name in sorted(os.listdir(results_raw)):
        field_dir = os.path.join(results_raw, field_dir_name)
        meta_path = os.path.join(field_dir, 'meta.json')
        if not os.path.isdir(field_dir) or not os.path.exists(meta_path):
            continue

        fm = load_json(meta_path)
        step_name  = fm['step_name']
        field_name = fm['field_name']
        components = fm['components']
        invariants = fm['invariants']
        has_section = fm.get('has_section', 0)

        t_field = time.time()
        print("    {}/{} ...".format(step_name, field_name))

        sm = steps_meta.get(step_name, {})
        num_frames = sm.get('num_frames', 0)

        safe_step  = safe(step_name)
        safe_field = safe(field_name)
        h5_fname   = '{}__{}.h5'.format(safe_step, safe_field)
        if result_group:
            h5_rel = os.path.join('l1', 'results', safe(result_group), h5_fname)
        else:
            h5_rel = os.path.join('l1', 'results', h5_fname)
        h5_abs     = os.path.join(workspace, h5_rel)

        positions_found = set()
        global_min =  float('inf')
        global_max = -float('inf')

        with h5py.File(h5_abs, 'w') as f:
            # Metadata
            mg = f.create_group('meta')
            mg.create_dataset('step_name',         data=step_name.encode())
            mg.create_dataset('field_name',        data=field_name.encode())
            mg.create_dataset('field_description', data=b'')
            str_ds(f, 'meta/components', components)
            str_ds(f, 'meta/invariants', invariants)

            # Frame index
            frame_values = [fr['frame_value']  for fr in sm.get('frames', [])]
            frame_descs  = [fr['description']   for fr in sm.get('frames', [])]
            fig = f.create_group('frame_index')
            fig.create_dataset('frame_values',
                               data=np.array(frame_values, dtype=np.float64))
            str_ds(f, 'frame_index/descriptions', frame_descs)

            # Each block
            for binfo in fm.get('blocks', []):
                inst_name = binfo['inst_name']
                position  = binfo['position']
                elem_type = binfo.get('elem_type')
                ncomp     = binfo['ncomp']
                N_ent     = binfo['n_entities']

                positions_found.add(position)

                sp_num = binfo.get('sp_num')  # int or None

                # HDF5 group path (no /data suffix — matches manifest.db convention)
                grp_path = '/{}/{}'.format(position, inst_name)
                if elem_type:
                    grp_path += '/{}'.format(elem_type)
                if sp_num is not None:
                    grp_path += '/sp{}'.format(sp_num)
                grp = f.require_group(grp_path)

                # Block directory
                bd_parts = [field_dir, safe(inst_name), position]
                if elem_type:
                    bd_parts.append(safe(elem_type))
                if sp_num is not None:
                    bd_parts.append('sp{}'.format(sp_num))
                bd = os.path.join(*bd_parts)

                # Labels + aux arrays
                labels = nload(os.path.join(bd, 'labels.npy'))
                grp.create_dataset('labels', data=labels)

                n_ip = binfo.get('n_ip')
                n_sp = binfo.get('n_sp', 0) or 0

                if position == 'INTEGRATION_POINT':
                    ip_labels = nload(os.path.join(bd, 'ip_labels.npy'))
                    sp_labels = nload(os.path.join(bd, 'sp_labels.npy'))
                    grp.create_dataset('ip_labels', data=ip_labels)
                    if len(sp_labels) > 0:
                        grp.create_dataset('sp_labels', data=sp_labels)

                # Create result dataset (shape determined by position + dims)
                if position == 'NODAL':
                    ds = grp.create_dataset(
                        'data',
                        shape=(num_frames, N_ent, ncomp),
                        dtype='float32',
                        chunks=(1, min(N_ent, 8192), ncomp),
                        compression='lzf',
                    )
                    if invariants:
                        n_inv = len(invariants)
                        grp.create_dataset(
                            'invariants',
                            shape=(num_frames, N_ent, n_inv),
                            dtype='float32',
                            chunks=(1, min(N_ent, 8192), n_inv),
                            compression='lzf',
                        )

                elif position == 'INTEGRATION_POINT':
                    if n_sp > 0:
                        ds = grp.create_dataset(
                            'data',
                            shape=(num_frames, N_ent, n_sp, n_ip, ncomp),
                            dtype='float32',
                            chunks=(1, min(N_ent, 512), n_sp, n_ip, ncomp),
                            compression='lzf',
                        )
                    else:
                        ds = grp.create_dataset(
                            'data',
                            shape=(num_frames, N_ent, n_ip, ncomp),
                            dtype='float32',
                            chunks=(1, min(N_ent, 4096), n_ip, ncomp),
                            compression='lzf',
                        )

                elif position == 'ELEMENT_NODAL':
                    n_en = binfo['n_enodes']
                    ds = grp.create_dataset(
                        'data',
                        shape=(num_frames, N_ent, n_en, ncomp),
                        dtype='float32',
                        chunks=(1, min(N_ent, 4096), n_en, ncomp),
                        compression='lzf',
                    )
                else:
                    ds = grp.create_dataset(
                        'data',
                        shape=(num_frames, N_ent, ncomp),
                        dtype='float32',
                        chunks=(1, min(N_ent, 8192), ncomp),
                        compression='lzf',
                    )

                # Write per-frame data
                expected_shape = ds.shape[1:]
                for fi in range(num_frames):
                    fr_path = os.path.join(bd, 'f{:04d}.npy'.format(fi))
                    if not os.path.exists(fr_path):
                        continue
                    frame_data = nload(fr_path)
                    if frame_data.shape != expected_shape:
                        print("    WARNING: frame {} shape {} != expected {}, skipped".format(
                            fi, frame_data.shape, expected_shape))
                        continue
                    ds[fi] = frame_data

                    finite = frame_data[np.isfinite(frame_data)]
                    if finite.size > 0:
                        global_min = min(global_min, float(finite.min()))
                        global_max = max(global_max, float(finite.max()))

                # manifest.db: result_blocks
                db_conn.execute(
                    "INSERT OR REPLACE INTO result_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (result_group, step_name, field_name, inst_name, position,
                     elem_type,
                     grp_path,
                     grp_path + '/labels',
                     N_ent,
                     n_ip if n_ip else None,
                     n_sp if n_sp else None)
                )

        # Sanitize global range
        if global_min == float('inf'):
            global_min = global_max = None

        # manifest.db: result_files
        db_conn.execute(
            "INSERT OR REPLACE INTO result_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (result_group, step_name, field_name, h5_rel,
             json.dumps(components), json.dumps(invariants),
             json.dumps(sorted(positions_found)),
             has_section, global_min, global_max, 'odb')
        )
        db_conn.commit()
        print(f"      done. ({_fmt_t(time.time() - t_field)})")

    print(f"  Results done. ({_fmt_t(time.time() - t_results)} total)")


# ─── Meta table ───────────────────────────────────────────────────────────────

def write_meta(workspace, db_conn):
    """
    Write summary statistics into manifest.db `meta` table.
    Called after all packing is complete so instances table is fully populated.

    node_count    = sum of node_count across all instances
    instance_count = total rows in instances table
    """
    rows = db_conn.execute(
        "SELECT node_count FROM instances"
    ).fetchall()
    instance_count = len(rows)
    node_count     = sum(r[0] for r in rows if r[0] is not None)

    db_conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db_conn.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?,?)",
        [("node_count",      str(node_count)),
         ("instance_count",  str(instance_count))],
    )
    db_conn.commit()
    print(f"  meta: node_count={node_count}, instance_count={instance_count}")


# ─── Sections patch (INP+ODB mode) ───────────────────────────────────────────

def patch_sections_into_geom(raw_dir, workspace):
    """
    Patch sections data extracted by abaqus_dump into INP-derived geometry H5 files.

    Reads from raw_dir/sections/<inst_safe>/:
      sections.json, section_names.json, isets/elem_sets/<safe>.npy
    Writes into workspace/l1/geometry/<inst_safe>.h5:
      sections/<name>  (group attrs: element_set, material_name, type, thickness)
      section_names    (dataset)
      instance_sets/element_sets/<safe>  (dataset, only if not already present)

    Returns True if at least one H5 was patched (caller should re-run L2).
    Skips any instance whose geometry H5 already has a 'sections' group.
    """
    sections_root = os.path.join(raw_dir, 'sections')
    if not os.path.exists(sections_root):
        return False

    geom_dir = os.path.join(workspace, 'l1', 'geometry')
    patched_any = False

    for inst_safe in os.listdir(sections_root):
        d = os.path.join(sections_root, inst_safe)
        if not os.path.isdir(d):
            continue
        sec_path = os.path.join(d, 'sections.json')
        if not os.path.exists(sec_path):
            continue

        h5_path = os.path.join(geom_dir, inst_safe + '.h5')
        if not os.path.exists(h5_path):
            print("  WARNING: geometry H5 not found for {}, skipping".format(inst_safe))
            continue

        with h5py.File(h5_path, 'r') as _rf:
            if 'sections' in _rf:
                continue  # already present, skip

        sec_data        = load_json(sec_path)
        snames_path     = os.path.join(d, 'section_names.json')
        section_names   = load_json(snames_path) if os.path.exists(snames_path) else []
        isets_elem_dir  = os.path.join(d, 'isets', 'elem_sets')

        # print("  Patching sections → {}".format(inst_safe + '.h5'))
        # print("[DBG2] sections.json type={} len={}".format(
            # type(sec_data).__name__, len(sec_data) if sec_data else 0))
        with h5py.File(h5_path, 'a') as f:
            if isinstance(sec_data, list):
                sec_items = []
                for i, s in enumerate(sec_data):
                    # print("[DBG2] item[{}] type={} val={}".format(i, type(s).__name__, repr(s)[:120]))
                    grp_key = '{}__{}'.format(i, safe(s.get('section_name', str(i))))
                    sec_items.append((grp_key, s))
            else:
                # print("[DBG2] dict keys={}".format(list(sec_data.keys())[:5]))
                sec_items = list(sec_data.items())
            for grp_key, sinfo in sec_items:
                sg = f.require_group('sections/{}'.format(grp_key))
                sg.attrs['element_set']   = sinfo.get('element_set', '')
                sg.attrs['material_name'] = sinfo.get('material_name', '')
                sg.attrs['type']          = sinfo.get('type', '')
                sg.attrs['thickness']     = float(sinfo.get('thickness') or float('nan'))

            if section_names:
                if 'section_names' in f:
                    del f['section_names']
                f.create_dataset('section_names',
                                 data=np.array(section_names, dtype=object),
                                 dtype=h5py.special_dtype(vlen=str))

            if os.path.exists(isets_elem_dir):
                for fname in os.listdir(isets_elem_dir):
                    if not fname.endswith('.npy'):
                        continue
                    eset_safe = fname[:-4]
                    key = 'instance_sets/element_sets/{}'.format(eset_safe)
                    if key not in f:
                        f.create_dataset(key, data=nload(os.path.join(isets_elem_dir, fname)))

        patched_any = True

    return patched_any


# ─── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup_raw(raw_dir):
    """Remove l1_raw/ recursively."""
    import shutil
    shutil.rmtree(raw_dir)
    print("  l1_raw/ deleted.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args      = parse_args()
    workspace = os.path.abspath(args.workspace)
    result_group = args.result_group

    t_total = time.time()

    if result_group:
        # ── Append mode: pack results for one result_group, skip geometry ──
        rg_safe = safe(result_group)
        raw_dir = os.path.join(workspace, 'l1_raw', 'rg_{}'.format(rg_safe))
        if not os.path.exists(raw_dir):
            print("ERROR: {} not found. Run abaqus_dump.py --mode extract first.".format(raw_dir))
            sys.exit(1)
        meta_path = os.path.join(raw_dir, 'dump_meta.json')
        if not os.path.exists(meta_path):
            print("ERROR: dump_meta.json not found in {}.".format(raw_dir))
            sys.exit(1)

        display_name      = args.display_name or result_group
        consistency_check = args.consistency_check
        source_file       = args.source_file or ''

        print("=== Layer 1 Phase 2 (append): result_group='{}' ===".format(result_group))
        print("  Workspace: {}".format(workspace))

        mkdirs(os.path.join(workspace, 'l1', 'results', rg_safe))

        meta    = load_json(meta_path)
        db_conn = init_manifest(workspace)

        try:
            pack_results(raw_dir, workspace, meta, db_conn, result_group=result_group)
            # Upsert result_group_meta
            import datetime as _dt
            now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
            db_conn.execute(
                "INSERT OR REPLACE INTO result_group_meta"
                " (result_group, display_name, source_file, consistency_check, created_at)"
                " VALUES (?,?,?,?,?)",
                (result_group, display_name, source_file, consistency_check, now_iso),
            )
            db_conn.commit()
        except Exception:
            import traceback
            print("\n!!! ERROR:")
            traceback.print_exc()
            db_conn.close()
            sys.exit(1)

        db_conn.close()

        # Patch sections into geometry H5 if abaqus_dump wrote them (INP+ODB mode).
        # Writes a marker so job_runner knows to re-run L2.
        try:
            if patch_sections_into_geom(raw_dir, workspace):
                marker = os.path.join(workspace, 'l1', 'geometry', '.sections_patched')
                open(marker, 'w').close()
                print("  sections patched — L2 re-run marker written")
        except Exception:
            import traceback as _tb
            print("  WARNING: sections patch failed (non-fatal):")
            _tb.print_exc()

        if not args.keep_raw:
            print("Cleaning up {} ...".format(raw_dir))
            cleanup_raw(raw_dir)

        print("=== Append complete in {} ===".format(_fmt_t(time.time() - t_total)))
        print("    result_group: {}".format(result_group))
        return

    # ── Full mode: geometry + sets + results (legacy flow) ──────────────────
    raw_dir = os.path.join(workspace, 'l1_raw')

    if not os.path.exists(raw_dir):
        print("ERROR: l1_raw/ not found. Run abaqus_dump.py first.")
        sys.exit(1)

    meta_path = os.path.join(raw_dir, 'dump_meta.json')
    if not os.path.exists(meta_path):
        print("ERROR: dump_meta.json not found in l1_raw/.")
        sys.exit(1)

    print("=== Layer 1 Phase 2: npy → HDF5 ===")
    print("  Workspace: {}".format(workspace))

    for d in ['l1/geometry', 'l1/sets', 'l1/results']:
        mkdirs(os.path.join(workspace, d))

    meta    = load_json(meta_path)
    db_conn = init_manifest(workspace)

    try:
        pack_assembly(raw_dir, workspace, meta)
        pack_geometry(raw_dir, workspace, meta, db_conn)
        pack_sets(raw_dir, workspace, meta, db_conn)
        pack_results(raw_dir, workspace, meta, db_conn)
        write_meta(workspace, db_conn)
    except Exception:
        import traceback
        print("\n!!! ERROR:")
        traceback.print_exc()
        db_conn.close()
        sys.exit(1)

    db_conn.close()

    if not args.keep_raw:
        print("Cleaning up l1_raw/ ...")
        cleanup_raw(raw_dir)

    print("=== Layer 1 Phase 2 complete in {} ===".format(_fmt_t(time.time() - t_total)))
    print("    workspace: {}".format(workspace))


if __name__ == '__main__':
    main()
