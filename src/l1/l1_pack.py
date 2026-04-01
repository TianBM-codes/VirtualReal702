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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='npy → HDF5 packer (Phase 2)')
    p.add_argument('--workspace', required=True)
    p.add_argument('--keep-raw', action='store_true',
                   help='Do not delete l1_raw/ after packing')
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

def init_manifest(workspace):
    db_path = os.path.join(workspace, 'manifest.db')
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS instances (
            instance_name  TEXT PRIMARY KEY,
            part_name      TEXT,
            geom_path      TEXT,
            highorder_path TEXT,
            node_count     INTEGER,
            elem_count     INTEGER,
            bbox_min       TEXT,
            bbox_max       TEXT
        );
        CREATE TABLE IF NOT EXISTS element_type_dist (
            instance_name  TEXT,
            elem_type      TEXT,
            count          INTEGER,
            has_midnodes   INTEGER,
            n_corner_nodes INTEGER,
            n_faces        INTEGER,
            PRIMARY KEY (instance_name, elem_type)
        );
        CREATE TABLE IF NOT EXISTS steps (
            step_name   TEXT PRIMARY KEY,
            step_number INTEGER,
            procedure   TEXT,
            num_frames  INTEGER
        );
        CREATE TABLE IF NOT EXISTS frames (
            step_name    TEXT,
            frame_idx    INTEGER,
            frame_value  REAL,
            description  TEXT,
            PRIMARY KEY (step_name, frame_idx)
        );
        CREATE TABLE IF NOT EXISTS result_files (
            step_name    TEXT,
            field_name   TEXT,
            file_path    TEXT,
            components   TEXT,
            invariants   TEXT,
            positions    TEXT,
            has_section  INTEGER,
            val_min      REAL,
            val_max      REAL,
            PRIMARY KEY (step_name, field_name)
        );
        CREATE TABLE IF NOT EXISTS result_blocks (
            step_name     TEXT,
            field_name    TEXT,
            instance_name TEXT,
            position      TEXT,
            elem_type     TEXT,
            h5_path       TEXT,
            label_path    TEXT,
            n_entities    INTEGER,
            n_ip          INTEGER,
            n_sp          INTEGER,
            PRIMARY KEY (step_name, field_name, instance_name, position, elem_type)
        );
        CREATE TABLE IF NOT EXISTS node_sets (
            set_name      TEXT,
            set_scope     TEXT,
            instance_name TEXT,
            h5_path       TEXT,
            node_count    INTEGER,
            PRIMARY KEY (set_name, instance_name)
        );
        CREATE TABLE IF NOT EXISTS element_sets (
            set_name      TEXT,
            set_scope     TEXT,
            instance_name TEXT,
            h5_path       TEXT,
            elem_count    INTEGER,
            PRIMARY KEY (set_name, instance_name)
        );
    """)
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
                for etype_safe in os.listdir(elems_raw):
                    td  = os.path.join(elems_raw, etype_safe)
                    grp = f.require_group('elements/{}'.format(etype_safe))
                    for fname, dsname in [
                        ('labels.npy',        'labels'),
                        ('conn.npy',          'conn'),
                        ('face_elem_idx.npy', 'face_elem_idx'),
                        ('face_seq.npy',      'face_seq'),
                        ('face_node_conn.npy','face_node_conn'),
                        ('face_normals.npy',  'face_normals'),
                    ]:
                        p = os.path.join(td, fname)
                        if os.path.exists(p):
                            grp.create_dataset(dsname, data=nload(p))

            # Sections
            sec_path = os.path.join(d, 'sections.json')
            if os.path.exists(sec_path):
                for sname, sinfo in load_json(sec_path).items():
                    sg = f.require_group('sections/{}'.format(sname))
                    sg.attrs['element_set']   = sinfo.get('element_set', '')
                    sg.attrs['material_name'] = sinfo.get('material_name', '')
                    sg.attrs['type']          = sinfo.get('type', '')
                    sg.attrs['thickness']     = float(sinfo.get('thickness') or float('nan'))

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
                "INSERT OR REPLACE INTO element_sets VALUES (?,?,?,?,?)",
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
                                    "INSERT OR REPLACE INTO element_sets VALUES (?,?,?,?,?)",
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
                                "INSERT OR IGNORE INTO element_sets VALUES (?,?,?,?,?)",
                                (sname, 'part:{}'.format(part_safe), part_safe,
                                 h5_rel + ':part_sets/{}/element_sets/{}'.format(part_safe, sname),
                                 len(arr))
                            )

    db_conn.commit()
    print(f"    done. ({_fmt_t(time.time() - t0)})")


# ─── Pack results ─────────────────────────────────────────────────────────────

def pack_results(raw_dir, workspace, meta, db_conn):
    results_raw = os.path.join(raw_dir, 'results')
    results_dir = os.path.join(workspace, 'l1', 'results')

    t_results = time.time()
    print("  results/ ...")

    steps_meta = meta.get('steps', {})

    # Write steps and frames to manifest.db
    for step_name, sm in steps_meta.items():
        db_conn.execute(
            "INSERT OR REPLACE INTO steps VALUES (?,?,?,?)",
            (step_name, sm['step_number'], sm['procedure'], sm['num_frames'])
        )
        for fm in sm['frames']:
            db_conn.execute(
                "INSERT OR REPLACE INTO frames VALUES (?,?,?,?)",
                (step_name, fm['frame_idx'], fm['frame_value'], fm['description'])
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
        h5_rel     = os.path.join('l1', 'results', h5_fname)
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

                # HDF5 group path (no /data suffix — matches manifest.db convention)
                grp_path = '/{}/{}'.format(position, inst_name)
                if elem_type:
                    grp_path += '/{}'.format(elem_type)
                grp = f.require_group(grp_path)

                # Block directory
                bd_parts = [field_dir, safe(inst_name), position]
                if elem_type:
                    bd_parts.append(safe(elem_type))
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
                    "INSERT OR REPLACE INTO result_blocks VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (step_name, field_name, inst_name, position,
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
            "INSERT OR REPLACE INTO result_files VALUES (?,?,?,?,?,?,?,?,?)",
            (step_name, field_name, h5_rel,
             json.dumps(components), json.dumps(invariants),
             json.dumps(sorted(positions_found)),
             has_section, global_min, global_max)
        )
        db_conn.commit()
        print(f"      done. ({_fmt_t(time.time() - t_field)})")

    print(f"  Results done. ({_fmt_t(time.time() - t_results)} total)")


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
    raw_dir   = os.path.join(workspace, 'l1_raw')

    if not os.path.exists(raw_dir):
        print("ERROR: l1_raw/ not found. Run abaqus_dump.py first.")
        sys.exit(1)

    meta_path = os.path.join(raw_dir, 'dump_meta.json')
    if not os.path.exists(meta_path):
        print("ERROR: dump_meta.json not found in l1_raw/.")
        sys.exit(1)

    t_total = time.time()
    print("=== Layer 1 Phase 2: npy → HDF5 ===")
    print("  Workspace: {}".format(workspace))

    # Create output directories
    for d in ['l1/geometry', 'l1/sets', 'l1/results']:
        mkdirs(os.path.join(workspace, d))

    meta    = load_json(meta_path)
    db_conn = init_manifest(workspace)

    try:
        pack_assembly(raw_dir, workspace, meta)
        pack_geometry(raw_dir, workspace, meta, db_conn)
        pack_sets(raw_dir, workspace, meta, db_conn)
        pack_results(raw_dir, workspace, meta, db_conn)
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

    print(f"=== Layer 1 Phase 2 complete in {_fmt_t(time.time() - t_total)} ===")
    print("    workspace: {}".format(workspace))


if __name__ == '__main__':
    main()
