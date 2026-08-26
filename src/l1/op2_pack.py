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
from src.l1.pynastran_op2_compat import make_op2


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
    p.add_argument('--bdf',          default=None,
                   help='Companion BDF (for CD→global displacement transform). '
                        'Optional; if omitted, displacements are written in their '
                        'nodal output (CD) coordinate system as-is.')
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


# ─── Nodal output coordinate-system (CD) → global transform ────────────────────

# Result tables already stored in the basic (cid=0) frame — must NOT be re-rotated.
_BASIC_FRAME_TABLES = {'BOUGV1', 'BOPHIG', 'TOUGV1'}


def _lookup_source_bdf(db_conn):
    """Return the BDF path recorded by bdf_pack in manifest.db (l1_meta), or None.

    This lets op2_pack rotate nodal displacements back to global even when the
    caller did not pass --bdf, as long as the geometry was packed from a BDF that
    still exists on disk.
    """
    try:
        row = db_conn.execute(
            "SELECT value FROM l1_meta WHERE key='source_bdf_path'").fetchone()
    except Exception:
        return None
    if not row:
        return None
    path = row[0] if not hasattr(row, 'keys') else row['value']
    if path and os.path.exists(path):
        return path
    if path:
        print('    NOTE: manifest source_bdf_path no longer exists ({}).'.format(path))
    return None


def _build_coord_context(bdf_path):
    """
    Build the data needed to rotate nodal results from each node's output (CD)
    coordinate system back to the global frame.

    Nastran writes DISPLACEMENT / eigenvector components in each node's CD frame
    (GRID field 7, or a GRDSET default). When any node has CD≠0, applying the raw
    components as if they were global tears the deformed mesh. We read the companion
    BDF to learn every node's CD and each coordinate system's definition.

    Returns (coords, cd_of, xyz_of) or None if no BDF / no local CD is present.
      coords : dict{cid: pyNastran Coord}   — passed straight to pyNastran's rotator
      cd_of  : dict{nid: cd}
      xyz_of : dict{nid: (x, y, z)} in global — only needed for cylindrical CD
    """
    if not bdf_path or not os.path.exists(bdf_path):
        return None

    from src.l1.bdf_read import read_bdf_safe
    model = read_bdf_safe(bdf_path, xref=True)

    # nid_cp_cd: [N, 3] = (node id, CP, CD), sorted by node id
    icd_t, icp_t, xyz_cp, nid_cp_cd = model.get_displacement_index_xyz_cp_cd(sort_ids=True)
    nids = nid_cp_cd[:, 0]
    cds  = nid_cp_cd[:, 2]

    if not np.any(cds != 0):
        # All nodes output in the basic frame already — nothing to rotate.
        print('    CD transform: all nodes CD=0 (global), skip.')
        return None

    # Global coordinates — derived from the SAME (nids, xyz_cp, icp) so the row
    # order is guaranteed aligned with nid_cp_cd above (used only for cylindrical
    # CD systems, which need per-node position to build the local basis).
    xyz_cid0 = model.transform_xyzcp_to_xyz_cid(xyz_cp, nids, icp_t, cid=0)

    cd_of  = {int(n): int(c) for n, c in zip(nids.tolist(), cds.tolist())}
    xyz_of = {int(n): (float(x), float(y), float(z))
              for n, (x, y, z) in zip(nids.tolist(), xyz_cid0.tolist())}

    n_local = int(np.count_nonzero(cds != 0))
    n_frames_cd = len(set(int(c) for c in cds.tolist()) - {0})
    print('    CD transform: {} node(s) in {} local coord frame(s) → global'.format(
        n_local, n_frames_cd))
    return model.coords, cd_of, xyz_of


def _transform_result_to_global(result_obj, sc_id, coord_ctx, log):
    """
    Rotate one displacement/eigenvector result's translations (and rotations)
    from each node's CD frame into global, in place on result_obj.data.

    Indices are matched by NODE ID against this result's own node ordering, so it
    stays correct even if the result covers a subset of nodes or a different order
    than BDF.point_ids (pyNastran's built-in transform indexes positionally and
    only works when *all* nodes are present in the same order).
    """
    from pyNastran.op2.op2 import transform_displacement_to_global

    coords, cd_of, xyz_of = coord_ctx

    tname = getattr(result_obj, 'table_name', None)
    if isinstance(tname, bytes):
        tname = tname.decode('ascii', 'ignore')
    if tname in _BASIC_FRAME_TABLES:
        return 0  # already in basic/global frame

    nids = result_obj.node_gridtype[:, 0]
    cds  = np.array([cd_of.get(int(n), 0) for n in nids], dtype=np.int64)

    uniq = [int(c) for c in np.unique(cds) if int(c) not in (0, -1)]
    if not uniq:
        return 0

    icd_transform = {c: np.where(cds == c)[0].astype(np.int64) for c in uniq}
    xyz_cid0 = np.array([xyz_of.get(int(n), (0.0, 0.0, 0.0)) for n in nids],
                        dtype=np.float64)

    transform_displacement_to_global(sc_id, result_obj, icd_transform,
                                     coords, xyz_cid0, log)
    return int(np.count_nonzero(cds != 0))


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

    # For each OP2 node, find its position in the sorted BDF label array.
    # searchsorted returns N_bdf for OP2 node ids larger than every BDF label,
    # which is out of bounds. numpy's & is NOT short-circuit, so we must clip
    # the indices BEFORE indexing bdf_node_labels, otherwise it raises IndexError.
    rows = np.searchsorted(bdf_node_labels, op2_node_ids)
    if N_bdf:
        safe_rows = np.clip(rows, 0, N_bdf - 1)
        valid = (rows < N_bdf) & (bdf_node_labels[safe_rows] == op2_node_ids)
    else:
        valid = np.zeros(N_op2, dtype=bool)

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


_STRESS_COMPONENTS = ['MISES']
_STRESS_HEADER_INDEX = {
    'oxx': 0, 'sxx': 0, 'xx': 0,
    'oyy': 1, 'syy': 1, 'yy': 1,
    'ozz': 2, 'szz': 2, 'zz': 2,
    'txy': 3, 'sxy': 3, 'xy': 3,
    'txz': 4, 'tzx': 4, 'sxz': 4, 'szx': 4, 'xz': 4, 'zx': 4,
    'tyz': 5, 'syz': 5, 'yz': 5,
}
_MISES_HEADERS = {'ovm', 'von_mises', 'mises'}


def _von_mises(stress):
    s11, s22, s33, s12, s13, s23 = [stress[..., i] for i in range(6)]
    return np.sqrt(np.maximum(
        0.0,
        0.5 * ((s11 - s22) ** 2 + (s22 - s33) ** 2 + (s33 - s11) ** 2)
        + 3.0 * (s12 ** 2 + s13 ** 2 + s23 ** 2),
    ))


def _collect_subcase_stress(op2, subcase_id, n_frames):
    """Extract OES Mises samples, preserving element-node values when available."""
    result_sets = getattr(getattr(op2, 'op2_results', None), 'stress', None)
    if result_sets is None:
        return None

    records = []
    for table_by_case in vars(result_sets).values():
        if not isinstance(table_by_case, dict):
            continue
        result_obj = table_by_case.get(subcase_id)
        if result_obj is None or not hasattr(result_obj, 'data'):
            continue
        element_node = getattr(result_obj, 'element_node', None)
        if element_node is None:
            continue
        data = np.asarray(result_obj.data)
        if data.ndim != 3 or data.shape[0] != n_frames or data.shape[2] == 0:
            continue
        try:
            headers = [str(item).lower() for item in result_obj.get_headers()]
        except Exception:
            continue
        if len(headers) != data.shape[2]:
            continue
        tensor = np.zeros((n_frames, data.shape[1], 6), dtype=np.float32)
        found_tensor = False
        for data_index, header in enumerate(headers):
            target_index = _STRESS_HEADER_INDEX.get(header)
            if target_index is not None:
                tensor[:, :, target_index] = np.real(data[:, :, data_index])
                found_tensor = True
        if not found_tensor:
            continue
        mises_index = next((i for i, name in enumerate(headers) if name in _MISES_HEADERS), None)
        mises = (np.abs(np.real(data[:, :, mises_index])) if mises_index is not None
                 else _von_mises(tensor))
        eids = np.asarray(element_node)[:, 0].astype(np.int32)
        nids = np.asarray(element_node)[:, 1].astype(np.int32)
        records.extend(
            (int(eid), int(nid), mises[:, row].astype(np.float32))
            for row, (eid, nid) in enumerate(zip(eids, nids))
        )

    if not records:
        return None

    def _max_samples(samples):
        stacked = np.stack(samples, axis=1)
        selection = np.argmax(
            np.where(np.isfinite(stacked), stacked, -np.inf), axis=1)
        return stacked[np.arange(n_frames), selection]

    by_element_node = {}
    for eid, nid, values in records:
        by_element_node.setdefault((eid, nid), []).append(values)

    direct_records = [
        (eid, nid, _max_samples(samples))
        for (eid, nid), samples in by_element_node.items()
        if nid > 0
    ]
    direct_eids = {eid for eid, _, _ in direct_records}
    fallback_by_element = {}
    for eid, _nid, values in records:
        if eid not in direct_eids:
            fallback_by_element.setdefault(eid, []).append(values)

    return {
        'node_labels': np.asarray([item[1] for item in direct_records], dtype=np.int32),
        'node_data': np.asarray([item[2] for item in direct_records], dtype=np.float32).T[..., np.newaxis]
        if direct_records else np.empty((n_frames, 0, 1), dtype=np.float32),
        'element_labels': np.asarray(sorted(fallback_by_element), dtype=np.int32),
        'element_data': np.asarray(
            [_max_samples(fallback_by_element[eid]) for eid in sorted(fallback_by_element)],
            dtype=np.float32,
        ).T[..., np.newaxis] if fallback_by_element else np.empty((n_frames, 0, 1), dtype=np.float32),
    }


def _load_element_layout(workspace, geom_path):
    with h5py.File(os.path.join(workspace, geom_path), 'r') as f:
        return {
            etype: {
                'labels': f['elements/{}/labels'.format(etype)][:],
                'conn': f['elements/{}/conn'.format(etype)][:],
            }
            for etype in f.get('elements', {})
        }


def _write_subcase_s(h5_abs, inst_name, step_name, frame_values, frame_descs,
                     bdf_node_labels, element_layout, stress_samples):
    """Write global nodal Mises, preferring direct OES element-node samples."""
    n_frames = stress_samples['node_data'].shape[0]
    node_sums = np.zeros((n_frames, len(bdf_node_labels)), dtype=np.float64)
    node_counts = np.zeros(len(bdf_node_labels), dtype=np.int32)
    direct_labels = stress_samples['node_labels']
    if len(direct_labels):
        direct_rows = np.searchsorted(bdf_node_labels, direct_labels)
        direct_valid = ((direct_rows < len(bdf_node_labels)) &
                        (bdf_node_labels[np.clip(direct_rows, 0, len(bdf_node_labels) - 1)] == direct_labels))
        for frame_index in range(n_frames):
            np.add.at(node_sums[frame_index], direct_rows[direct_valid],
                      stress_samples['node_data'][frame_index, direct_valid, 0])
        np.add.at(node_counts, direct_rows[direct_valid], 1)

    stress_eids = stress_samples['element_labels']
    stress_data = stress_samples['element_data']
    for layout in element_layout.values():
        labels = layout['labels']
        if len(labels) == 0:
            continue
        rows = np.searchsorted(labels, stress_eids)
        valid = (rows < len(labels)) & (labels[np.clip(rows, 0, len(labels) - 1)] == stress_eids)
        if not np.any(valid):
            continue
        element_nodes = layout['conn'][rows[valid]]
        values = stress_data[:, valid, 0]
        for local_node in range(element_nodes.shape[1]):
            node_rows = element_nodes[:, local_node]
            node_valid = node_rows >= 0
            if not np.any(node_valid):
                continue
            node_rows = node_rows[node_valid]
            node_counts[node_rows] += 1
            for frame_index in range(n_frames):
                np.add.at(node_sums[frame_index], node_rows, values[frame_index, node_valid])

    has_stress = node_counts > 0
    if not np.any(has_stress):
        return [], None, None
    data = np.full((n_frames, len(bdf_node_labels), 1), np.nan, dtype=np.float32)
    data[:, has_stress, 0] = (node_sums[:, has_stress] / node_counts[has_stress]).astype(np.float32)
    finite = data[np.isfinite(data)]
    value_min = float(finite.min())
    value_max = float(finite.max())
    with h5py.File(h5_abs, 'w') as f:
        mg = f.create_group('meta')
        mg.create_dataset('step_name', data=step_name.encode())
        mg.create_dataset('field_name', data=b'S')
        mg.create_dataset('field_description', data=b'OP2 element stress (max MISES sample)')
        str_ds(f, 'meta/components', _STRESS_COMPONENTS)
        str_ds(f, 'meta/invariants', [])
        fig = f.create_group('frame_index')
        fig.create_dataset('frame_values', data=np.asarray(frame_values, dtype=np.float64))
        str_ds(f, 'frame_index/descriptions', frame_descs)

        grp_path = '/NODAL/{}'.format(inst_name)
        grp = f.require_group(grp_path)
        grp.create_dataset('labels', data=bdf_node_labels)
        grp.create_dataset('data', data=data, compression='lzf',
                           chunks=(1, min(len(bdf_node_labels), 8192), 1))
    return [(None, '/NODAL/{}'.format(inst_name), len(bdf_node_labels))], value_min, value_max


# ─── Main packing logic ───────────────────────────────────────────────────────

def pack(op2_path, workspace, result_group, bdf_path=None):
    t_total = time.time()
    op2_basename = os.path.basename(op2_path)
    rg_safe = safe(result_group)

    print('OP2 pack: {} → result_group \'{}\''.format(op2_basename, result_group))

    # ── 1. Open manifest.db and read instance info ────────────────────────────
    db_conn = _open_manifest(workspace)
    inst_name, geom_path = _get_instance_info(db_conn, workspace)
    print('  Instance: {}'.format(inst_name))

    bdf_node_labels = _load_bdf_node_labels(workspace, geom_path)
    element_layout = _load_element_layout(workspace, geom_path)
    N_bdf = len(bdf_node_labels)
    print('  BDF node count: {}'.format(N_bdf))

    # ── 2. Read OP2 ───────────────────────────────────────────────────────────
    print('  Reading OP2 ...')
    t0 = time.time()
    op2 = make_op2(debug=False)
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

    # ── 4b. Rotate displacements from nodal CD frames back to global ──────────
    # Must run BEFORE _align_data / column slicing, while results still hold the
    # full 6-DOF data pyNastran's rotator expects.
    #
    # BDF source priority: explicit --bdf first, then the path bdf_pack recorded
    # in manifest.db. The manifest fallback makes the transform self-sufficient —
    # it no longer depends on the caller (job_runner) re-resolving and passing the
    # BDF path, which was the silent failure mode behind "displacements not rotated".
    if bdf_path and os.path.exists(bdf_path):
        print('  CD transform: using BDF from --bdf ({}).'.format(bdf_path))
    else:
        manifest_bdf = _lookup_source_bdf(db_conn)
        if manifest_bdf:
            bdf_path = manifest_bdf
            print('  CD transform: using BDF from manifest ({}).'.format(bdf_path))
        else:
            print('  CD transform: no BDF available (neither --bdf nor manifest); '
                  'displacements left in nodal (CD) frame — deformed mesh may tear '
                  'if any node has CD != 0.')

    coord_ctx = None
    try:
        coord_ctx = _build_coord_context(bdf_path)
    except Exception as exc:
        print('    WARNING: CD transform setup failed ({}); '
              'displacements left in nodal (CD) frame.'.format(exc))
    if coord_ctx is not None:
        n_rot_total = 0
        for sc_id, (result_obj, _is_modal) in subcases_to_process.items():
            try:
                n_rot_total += _transform_result_to_global(
                    result_obj, sc_id, coord_ctx, op2.log)
            except Exception as exc:
                print('    WARNING: CD transform failed for SUBCASE {} ({}); '
                      'left in CD frame.'.format(sc_id, exc))
        print('    CD transform applied to {} node-row(s) across subcases.'.format(
            n_rot_total))

    written_steps = []  # [(step_name, procedure, n_frames, frame_vals, frame_descs, mode_nums, u_h5, s_h5, s_blocks, s_min, s_max)]

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

        stress_samples = (
            _collect_subcase_stress(op2, sc_id, n_frames)
            if len(subcases_to_process) > 1
            else None
        )
        stress_h5_rel = None
        stress_blocks = []
        stress_min = stress_max = None
        if stress_samples is not None:
            stress_h5_fname = '{}__{}.h5'.format(safe(step_name), 'S')
            stress_h5_rel = os.path.join('l1', 'results', rg_safe, stress_h5_fname)
            stress_h5_abs = os.path.join(workspace, stress_h5_rel)
            stress_blocks, stress_min, stress_max = _write_subcase_s(
                stress_h5_abs, inst_name, step_name, frame_values, frame_descs,
                bdf_node_labels, element_layout, stress_samples)
            if not stress_blocks:
                os.remove(stress_h5_abs)
                stress_h5_rel = None
            else:
                print('    stress: {} element(s) across {} type(s)'.format(
                    len(stress_samples['element_labels']), len(stress_blocks)))

        written_steps.append((step_name, procedure, n_frames, frame_values, frame_descs,
                              mode_nums, h5_rel, stress_h5_rel, stress_blocks,
                              stress_min, stress_max))

    # ── 5. Write manifest.db ──────────────────────────────────────────────────
    print('  Writing manifest.db ...')
    components_json = json.dumps(['U1', 'U2', 'U3', 'USUM'])
    positions_json  = json.dumps(['NODAL'])

    for step_number, (step_name, procedure, n_frames, frame_values, frame_descs, mode_nums,
                      h5_rel, stress_h5_rel, stress_blocks, stress_min, stress_max) \
            in enumerate(written_steps):

        # steps
        db_conn.execute(
            "INSERT OR REPLACE INTO steps"
            " (result_group, step_name, step_number, procedure, num_frames,"
            "  description, nlgeom) VALUES (?,?,?,?,?,?,?)",
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
            "INSERT OR REPLACE INTO result_blocks"
            " (result_group, step_name, field_name, instance_name, position,"
            "  elem_type, h5_path, label_path, n_entities, n_ip, n_sp)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (result_group, step_name, 'U',
             inst_name, 'NODAL',
             None,                              # elem_type (None for NODAL)
             h5_grp_path,
             h5_grp_path + '/labels',
             N_bdf,
             None, None),                       # n_ip, n_sp
        )

        if stress_h5_rel:
            db_conn.execute(
                "DELETE FROM result_blocks WHERE result_group=? AND step_name=? AND field_name='S'",
                (result_group, step_name),
            )
            db_conn.execute(
                "INSERT OR REPLACE INTO result_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (result_group, step_name, 'S', stress_h5_rel,
                 json.dumps(_STRESS_COMPONENTS), json.dumps([]),
                 json.dumps(['NODAL']),
                 0, stress_min, stress_max, 'nastran'),
            )
            for etype, h5_grp_path, n_entities in stress_blocks:
                db_conn.execute(
                    "INSERT OR REPLACE INTO result_blocks"
                    " (result_group, step_name, field_name, instance_name, position,"
                    "  elem_type, h5_path, label_path, n_entities, n_ip, n_sp)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (result_group, step_name, 'S', inst_name, 'NODAL',
                     etype, h5_grp_path, h5_grp_path + '/labels', n_entities,
                     None, None),
                )
        else:
            db_conn.execute(
                "DELETE FROM result_blocks WHERE result_group=? AND step_name=? AND field_name='S'",
                (result_group, step_name),
            )
            db_conn.execute(
                "DELETE FROM result_files WHERE result_group=? AND step_name=? AND field_name='S'",
                (result_group, step_name),
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
    bdf_path     = os.path.abspath(args.bdf) if args.bdf else None

    if not os.path.exists(op2_path):
        print('ERROR: OP2 file not found: {}'.format(op2_path), file=sys.stderr)
        sys.exit(1)

    manifest_path = os.path.join(workspace, 'manifest.db')
    if not os.path.exists(manifest_path):
        print('ERROR: manifest.db not found — run bdf_pack.py first: {}'.format(workspace),
              file=sys.stderr)
        sys.exit(1)

    if bdf_path and not os.path.exists(bdf_path):
        print('WARNING: --bdf not found ({}); '
              'displacements left in nodal (CD) frame.'.format(bdf_path), file=sys.stderr)
        bdf_path = None

    pack(op2_path, workspace, result_group, bdf_path=bdf_path)


if __name__ == '__main__':
    main()
