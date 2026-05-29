#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
op2_pack.py — Layer 1 extraction for Nastran OP2 result files.

Usage:
    python src/l1/op2_pack.py --op2 /path/to/result.op2 \\
        --workspace /data/<job_id>/ \\
        [--result-group default_result]

Prerequisites: bdf_pack.py has already run (manifest.db contains instances table).

Output:
    <workspace>/l1/results/<result_group>/
        SUBCASE_<n>__U.h5     — displacement / mode-shape field (NODAL)
    <workspace>/manifest.db   — steps + frames + result_files + result_blocks
                                + result_group_meta
"""

import argparse
import json
import math
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


# ─── SOL → procedure mapping ──────────────────────────────────────────────────

# analysis_code values reported by pyNastran on result objects
_ANALYSIS_CODE_TO_PROC = {
    1: 'STATIC',     # statics
    2: 'FREQUENCY',  # normal modes (SOL 103)
    5: 'DYNAMIC',    # frequency response
    6: 'DYNAMIC',    # transient response
    7: 'BUCKLE',     # pre-buckling
    8: 'BUCKLE',     # post-buckling
    9: 'STATIC',     # nonlinear statics (treat as STATIC for display)
    10: 'DYNAMIC',   # nonlinear transient
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='OP2 → HDF5 packer (L1)')
    p.add_argument('--op2',          required=True,  help='Path to .op2 file')
    p.add_argument('--workspace',    required=True,  help='Workspace directory')
    p.add_argument('--result-group', default='default_result',
                   help='Result group name (default: default_result)')
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


def _open_manifest(workspace):
    db_path = os.path.join(workspace, 'manifest.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(MANIFEST_SCHEMA)  # idempotent CREATE IF NOT EXISTS
    conn.commit()
    return conn


def _get_instance_info(db_conn, workspace):
    """Read inst_name and its geometry H5 path from manifest.db."""
    rows = db_conn.execute("SELECT instance_name, geom_path FROM instances").fetchall()
    if not rows:
        raise RuntimeError("manifest.db has no instances — run bdf_pack.py first")
    # For Nastran BDF there is always exactly one instance
    inst_name = rows[0]['instance_name']
    geom_path = rows[0]['geom_path']
    return inst_name, geom_path


def _load_bdf_node_labels(workspace, geom_path):
    """Load sorted node label array from geometry H5 (written by bdf_pack.py)."""
    h5_abs = os.path.join(workspace, geom_path)
    with h5py.File(h5_abs, 'r') as f:
        return f['nodes/labels'][:]   # int32, sorted ascending


def _infer_procedure(result_obj):
    """Return 'STATIC' / 'FREQUENCY' / 'DYNAMIC' / 'BUCKLE' from pyNastran result."""
    ac = getattr(result_obj, 'analysis_code', None)
    if ac is not None:
        return _ANALYSIS_CODE_TO_PROC.get(int(ac), 'STATIC')
    return 'STATIC'


def _get_frame_values(result_obj, procedure):
    """
    Return (frame_values, descriptions, mode_numbers).

    frame_values  : [n_frames] float64
    descriptions  : [n_frames] str
    mode_numbers  : [n_frames] int or None per frame (None for non-modal)

    For FREQUENCY (eigenvectors): compute Hz from .eigns (confirmed attribute from
    example code).  Fall back to sequential if unavailable.
    For DYNAMIC: use .times.
    For STATIC: use .lsdvmns (load step), or sequential.
    """
    n_frames = result_obj.data.shape[0]

    # --- Modal / FREQUENCY ---
    if procedure == 'FREQUENCY':
        # .eigns: raw eigenvalues λ; freq = sqrt(|λ|) / (2π)
        eigns = getattr(result_obj, 'eigns', None)
        if eigns is not None and len(eigns) == n_frames:
            arr = np.asarray(eigns, dtype=np.float64)
            fvals = np.where(arr > 0, np.sqrt(arr) / (2.0 * math.pi), 0.0)
        else:
            fvals = np.arange(1.0, n_frames + 1.0)

        # .modes: actual mode numbers (e.g. [1,2,3,...] or sparse like [2,4,6,...])
        modes_attr = getattr(result_obj, 'modes', None)
        if modes_attr is not None and len(modes_attr) == n_frames:
            mode_nums = [int(m) for m in modes_attr]
        else:
            mode_nums = list(range(1, n_frames + 1))

        descs = ['Mode {:d}  {:.4f} Hz'.format(mode_nums[i], fvals[i])
                 for i in range(n_frames)]
        return fvals, descs, mode_nums

    # --- Transient / DYNAMIC ---
    times = getattr(result_obj, 'times', None)
    if times is not None and len(times) == n_frames:
        fvals = np.asarray(times, dtype=np.float64)
        descs = ['t={:.6g}'.format(v) for v in fvals]
        return fvals, descs, [None] * n_frames

    # --- Static / generic ---
    lsdvmns = getattr(result_obj, 'lsdvmns', None)
    if lsdvmns is not None and len(lsdvmns) == n_frames:
        fvals = np.asarray(lsdvmns, dtype=np.float64)
    else:
        fvals = np.arange(0.0, float(n_frames))
    descs = ['frame {:d}'.format(i) for i in range(n_frames)]
    return fvals, descs, [None] * n_frames


def _align_data(raw_data, op2_node_ids, bdf_node_labels):
    """
    Align OP2 result data to the BDF node ordering.

    raw_data        : [n_frames, N_op2, n_comp] float32/float64
    op2_node_ids    : [N_op2] int   — node GIDs reported in OP2
    bdf_node_labels : [N_bdf] int32 — sorted node GIDs from bdf_pack

    Returns float32 array [n_frames, N_bdf, n_comp] with NaN where BDF
    has nodes not covered by OP2.
    OP2 nodes absent from BDF are silently ignored (with a warning count).
    """
    n_frames, N_op2, n_comp = raw_data.shape
    N_bdf = len(bdf_node_labels)

    # For each OP2 node, find its position in the sorted BDF label array
    rows = np.searchsorted(bdf_node_labels, op2_node_ids)
    valid = (rows < N_bdf) & (bdf_node_labels[rows] == op2_node_ids)

    n_extra = int((~valid).sum())
    if n_extra:
        print('    WARNING: {} OP2 node(s) not found in BDF, ignored'.format(n_extra))

    out = np.full((n_frames, N_bdf, n_comp), np.nan, dtype=np.float32)
    out[:, rows[valid], :] = raw_data[:, valid, :].astype(np.float32)
    return out



def _write_subcase_u(h5_abs, inst_name, step_name, bdf_node_labels, aligned_data,
                     frame_values, frame_descs):
    """Write displacement/mode-shape HDF5 for one subcase."""
    n_frames, N_bdf, n_comp = aligned_data.shape
    components = ['U1', 'U2', 'U3']

    # Append USUM = sqrt(U1²+U2²+U3²) as 4th component.
    # NaN nodes (not covered by OP2) stay NaN.
    usum = np.sqrt(np.nansum(aligned_data ** 2, axis=-1, keepdims=True)).astype(np.float32)
    # nansum returns 0 where all inputs are NaN; restore NaN for those nodes
    all_nan_mask = ~np.isfinite(aligned_data).any(axis=-1, keepdims=True)
    usum[all_nan_mask] = np.nan
    data_out = np.concatenate([aligned_data, usum], axis=-1)  # [n_frames, N_bdf, 4]
    components_out = components + ['USUM']
    n_comp_out = data_out.shape[-1]

    with h5py.File(h5_abs, 'w') as f:
        # meta
        mg = f.create_group('meta')
        mg.create_dataset('step_name',         data=step_name.encode())
        mg.create_dataset('field_name',        data=b'U')
        mg.create_dataset('field_description', data=b'')
        str_ds(f, 'meta/components', components_out)
        str_ds(f, 'meta/invariants', [])

        # frame_index
        fig = f.create_group('frame_index')
        fig.create_dataset('frame_values',
                           data=np.asarray(frame_values, dtype=np.float64))
        str_ds(f, 'frame_index/descriptions', frame_descs)

        # NODAL/{inst_name}
        ig = f.require_group('NODAL/{}'.format(inst_name))
        ig.create_dataset('labels', data=bdf_node_labels)
        ig.create_dataset(
            'data',
            data=data_out,
            dtype='float32',
            chunks=(1, min(N_bdf, 8192), n_comp_out),
            compression='lzf',
        )


# ─── Main packing logic ───────────────────────────────────────────────────────

def pack(op2_path, workspace, result_group):
    from pyNastran.op2.op2 import OP2

    t_total = time.time()
    op2_basename = os.path.basename(op2_path)
    rg_safe = safe(result_group)

    print('OP2 pack: {} → result_group \'{}\''.format(op2_basename, result_group))

    # ── 1. Open manifest.db and read instance info ────────────────────────────
    db_conn = _open_manifest(workspace)
    inst_name, geom_path = _get_instance_info(db_conn, workspace)
    print('  Instance: {}'.format(inst_name))

    bdf_node_labels = _load_bdf_node_labels(workspace, geom_path)
    N_bdf = len(bdf_node_labels)
    print('  BDF node count: {}'.format(N_bdf))

    # ── 2. Read OP2 ───────────────────────────────────────────────────────────
    print('  Reading OP2 ...')
    t0 = time.time()
    op2 = OP2(debug=False)
    # post=-2 OP2 files hit a benign EOF condition that pyNastran 1.4 treats as
    # FatalError; disabling stop_on_unclosed_file lets reading complete normally.
    op2.stop_on_unclosed_file = False
    op2.read_op2(op2_path)
    print('  Read done. ({})'.format(_fmt_t(time.time() - t0)))

    # ── 3. Setup output dir ───────────────────────────────────────────────────
    results_dir = os.path.join(workspace, 'l1', 'results', rg_safe)
    mkdirs(results_dir)

    # ── 4. Process subcases ───────────────────────────────────────────────────
    # Collect all subcases from both eigenvectors (modal) and displacements (static/dynamic).
    # eigenvectors take priority — if a subcase appears in both, use eigenvectors.
    subcases_to_process = {}  # sc_id → (result_obj, is_modal)

    for sc_id, ev in (op2.eigenvectors or {}).items():
        subcases_to_process[sc_id] = (ev, True)

    for sc_id, disp in (op2.displacements or {}).items():
        if sc_id not in subcases_to_process:
            subcases_to_process[sc_id] = (disp, False)

    if not subcases_to_process:
        print('  WARNING: no displacements or eigenvectors found in OP2.')
        db_conn.close()
        return

    print('  {} subcase(s) found.'.format(len(subcases_to_process)))

    written_steps = []  # [(step_name, procedure, n_frames, frame_vals, frame_descs, h5_rel)]

    for sc_id in sorted(subcases_to_process.keys()):
        result_obj, is_modal = subcases_to_process[sc_id]
        step_name = 'SUBCASE_{}'.format(sc_id)
        procedure = 'FREQUENCY' if is_modal else _infer_procedure(result_obj)

        raw_data = result_obj.data   # [n_frames, N_op2, 6]
        op2_nids = result_obj.node_gridtype[:, 0].astype(np.int32)

        n_frames = raw_data.shape[0]
        frame_values, frame_descs, mode_nums = _get_frame_values(result_obj, procedure)

        print('  {} ({}): {} frame(s), {} op2 nodes'.format(
            step_name, procedure, n_frames, len(op2_nids)))

        # Align to BDF node ordering and take UX/UY/UZ (columns 0-2)
        aligned = _align_data(raw_data[:, :, :3], op2_nids, bdf_node_labels)

        # Write HDF5
        h5_fname = '{}__{}.h5'.format(safe(step_name), 'U')
        h5_rel   = os.path.join('l1', 'results', rg_safe, h5_fname)
        h5_abs   = os.path.join(workspace, h5_rel)

        _write_subcase_u(h5_abs, inst_name, step_name, bdf_node_labels, aligned,
                         frame_values, frame_descs)

        written_steps.append((step_name, procedure, n_frames, frame_values, frame_descs, mode_nums, h5_rel))

    # ── 5. Write manifest.db ──────────────────────────────────────────────────
    print('  Writing manifest.db ...')
    components_json = json.dumps(['U1', 'U2', 'U3', 'USUM'])
    positions_json  = json.dumps(['NODAL'])

    for step_number, (step_name, procedure, n_frames, frame_values, frame_descs, mode_nums, h5_rel) \
            in enumerate(written_steps):

        # steps
        db_conn.execute(
            "INSERT OR REPLACE INTO steps VALUES (?,?,?,?,?,?,?)",
            (result_group, step_name, step_number, procedure, n_frames, None, None),
        )

        # frames — use real mode numbers from result_obj.modes
        for fi, (fval, fdesc, mnum) in enumerate(zip(frame_values, frame_descs, mode_nums)):
            freq_val = float(fval) if procedure == 'FREQUENCY' else None
            db_conn.execute(
                "INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (result_group, step_name,
                 fi, float(fval), fdesc,
                 None,      # domain
                 freq_val,  # frequency
                 mnum,      # mode_number (real mode number or None)
                 None, None, None, None, None),
            )

        # result_files (one row per step+field)
        # Compute global min/max from the H5 we just wrote
        val_min, val_max = None, None
        try:
            with h5py.File(os.path.join(workspace, h5_rel), 'r') as fh:
                dset = fh['NODAL/{}/data'.format(inst_name)]
                finite = dset[:][np.isfinite(dset[:])]
                if finite.size > 0:
                    val_min = float(finite.min())
                    val_max = float(finite.max())
        except Exception:
            pass

        db_conn.execute(
            "INSERT OR REPLACE INTO result_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (result_group, step_name, 'U', h5_rel,
             components_json, json.dumps([]),   # invariants = []
             positions_json,
             0,                                  # has_section
             val_min, val_max, 'nastran'),
        )

        # result_blocks (one row per step+field+inst+position)
        h5_grp_path = '/NODAL/{}'.format(inst_name)
        db_conn.execute(
            "INSERT OR REPLACE INTO result_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (result_group, step_name, 'U',
             inst_name, 'NODAL',
             None,                              # elem_type (None for NODAL)
             h5_grp_path,
             h5_grp_path + '/labels',
             N_bdf,
             None, None),                       # n_ip, n_sp
        )

    db_conn.commit()

    # ── 6. Write result_group_meta ────────────────────────────────────────────
    source_file = op2_basename
    display_name = os.path.splitext(source_file)[0]
    db_conn.execute(
        "INSERT OR IGNORE INTO result_group_meta"
        " (result_group, display_name, source_file, consistency_check, created_at)"
        " VALUES (?,?,?,'count-only',datetime('now'))",
        (result_group, display_name, source_file),
    )
    db_conn.commit()

    # ── 7. Adopt NULL result_group rows (in case written without rg tag) ──────
    for tbl in ('steps', 'frames', 'result_files', 'result_blocks'):
        try:
            db_conn.execute(
                "UPDATE {} SET result_group=? WHERE result_group IS NULL".format(tbl),
                (result_group,),
            )
        except Exception:
            pass
    db_conn.commit()
    db_conn.close()

    print('OP2 pack complete. ({} total)'.format(_fmt_t(time.time() - t_total)))


def main():
    args         = parse_args()
    op2_path     = os.path.abspath(args.op2)
    workspace    = os.path.abspath(args.workspace)
    result_group = args.result_group

    if not os.path.exists(op2_path):
        print('ERROR: OP2 file not found: {}'.format(op2_path), file=sys.stderr)
        sys.exit(1)

    manifest_path = os.path.join(workspace, 'manifest.db')
    if not os.path.exists(manifest_path):
        print('ERROR: manifest.db not found — run bdf_pack.py first: {}'.format(workspace),
              file=sys.stderr)
        sys.exit(1)

    pack(op2_path, workspace, result_group)


if __name__ == '__main__':
    main()
