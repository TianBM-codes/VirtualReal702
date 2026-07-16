#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
rst_pack.py — Layer 1 extraction for Ansys (MAPDL) RST result files.

RST 既有几何也有结果。按用户要求「分开就行，不走叠加那套」：RST 自包含，
从它**自己的 mesh** 提几何 + 从结果集提场量，全部打成一个结果组（默认
default_result），不做 INP/ODB 那种一致性校验+叠加到已有几何的流程。

几何写盘与 CDB 共用 ansys_geom.pack_ansys_geometry；材料常数直接来自
rst.materials（RST 自带 EX/NUXY）。结果写成 l1/results/<rg>/<step>__<field>.h5，
并写 manifest 的 steps/frames/result_files/result_blocks（source='rst'），
格式与 l1_pack.pack_results 的 NODAL 块完全一致，L3 无需感知来源。

Usage:
    python src/l1/rst_pack.py --rst /path/to/file.rst --workspace /data/<id>/ \
        [--result-group default_result] [--display-name 名称]
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

from src.l1.bdf_pack import mkdirs, safe, _fmt_t, str_ds
from src.l1.ansys_geom import pack_ansys_geometry


# ANSYS 分析类型码 kan → manifest procedure
_KAN_PROC = {0: 'STATIC', 1: 'BUCKLE', 2: 'FREQUENCY', 3: 'HARMONIC',
             4: 'DYNAMIC', 6: 'SUBSTRUCTURE', 7: 'SUBSTRUCTURE'}

# 应力/应变分量名（ANSYS 顺序 X,Y,Z,XY,YZ,XZ）→ Abaqus 风格
_TENSOR_COMPS = ['S11', 'S22', 'S33', 'S12', 'S23', 'S13']
_STRAIN_COMPS = ['E11', 'E22', 'E33', 'E12', 'E23', 'E13']


def _von_mises(t):
    """t: [..., 6] (Sx,Sy,Sz,Sxy,Syz,Sxz) → von Mises 标量。"""
    sx, sy, sz, sxy, syz, sxz = (t[..., i] for i in range(6))
    return np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
                   + 3.0 * (sxy ** 2 + syz ** 2 + sxz ** 2))


def _write_field_h5(h5_abs, step_name, field_name, components, invariants,
                    inst_name, labels, data, inv_data, num_frames):
    """写单个场的结果 HDF5（NODAL 块，与 l1_pack.pack_results 对齐）。

    data:     [num_frames, N, ncomp] float32
    inv_data: [num_frames, N, n_inv] float32 或 None
    返回 (grp_path, n_entities, val_min, val_max)。
    """
    with h5py.File(h5_abs, 'w') as f:
        mg = f.create_group('meta')
        mg.create_dataset('step_name',         data=step_name.encode())
        mg.create_dataset('field_name',        data=field_name.encode())
        mg.create_dataset('field_description', data=b'')
        str_ds(f, 'meta/components', components)
        str_ds(f, 'meta/invariants', invariants)

        fig = f.create_group('frame_index')
        fig.create_dataset('frame_values',
                           data=np.arange(num_frames, dtype=np.float64))
        str_ds(f, 'frame_index/descriptions', [''] * num_frames)

        grp_path = '/NODAL/{}'.format(inst_name)
        grp = f.require_group(grp_path)
        grp.create_dataset('labels', data=np.asarray(labels, dtype=np.int32))

        N_ent = data.shape[1]
        ds = grp.create_dataset('data', data=data.astype(np.float32),
                                chunks=(1, min(N_ent, 8192), data.shape[2]),
                                compression='lzf')
        if invariants and inv_data is not None:
            grp.create_dataset('invariants', data=inv_data.astype(np.float32),
                               chunks=(1, min(N_ent, 8192), inv_data.shape[2]),
                               compression='lzf')

    finite = data[np.isfinite(data)]
    vmin = float(finite.min()) if finite.size else None
    vmax = float(finite.max()) if finite.size else None
    return grp_path, N_ent, vmin, vmax


def pack_rst(rst_path, workspace, result_group='default_result', display_name=None):
    from ansys.mapdl import reader as pymapdl_reader

    t_total = time.time()
    basename = os.path.basename(rst_path)
    inst_name = os.path.splitext(basename)[0].upper()
    display_name = display_name or os.path.splitext(basename)[0]

    print("RST pack: {} → instance '{}', result_group '{}'".format(
        basename, inst_name, result_group))
    print("  Reading RST (ansys-mapdl-reader) ...")
    t0 = time.time()
    rst = pymapdl_reader.read_binary(rst_path)
    mesh = rst.mesh
    print("    {} nodes, {} elements, {} result set(s). ({})".format(
        mesh.n_node, mesh.n_elem, rst.nsets, _fmt_t(time.time() - t0)))

    # ── 几何（共用）。材料常数直接来自 rst.materials ───────────────────────────
    materials = {}
    for mid, props in (getattr(rst, 'materials', {}) or {}).items():
        # rst.materials 的键是 ANSYS 物性码（EX/NUXY/DENS/...），直接透传
        materials[int(mid)] = {str(k).upper(): v for k, v in props.items()}

    h5_rel, N_nodes, total_elem = pack_ansys_geometry(
        grid=rst.grid,
        section_arr=getattr(mesh, 'section', 0),
        node_components=getattr(mesh, 'node_components', {}),
        element_components=getattr(mesh, 'element_components', {}),
        materials=materials,
        sections_txt={},
        reals_txt={},
        inst_name=inst_name,
        workspace=workspace,
        shell_thickness_fn=None,
    )

    # ── 结果 ───────────────────────────────────────────────────────────────────
    kan = int(rst._resultheader.get('kan', 0) or 0)
    procedure = _KAN_PROC.get(kan, 'STATIC')
    step_name = procedure.capitalize()
    nsets = rst.nsets
    time_vals = np.asarray(rst.time_values, dtype=np.float64)

    results_out = os.path.join(workspace, 'l1', 'results', safe(result_group))
    mkdirs(results_out)

    db = sqlite3.connect(os.path.join(workspace, 'manifest.db'))
    db.execute('PRAGMA journal_mode=WAL')

    # steps / frames
    db.execute("INSERT OR REPLACE INTO steps"
               " (result_group, step_name, step_number, procedure, num_frames,"
               "  description, nlgeom) VALUES (?,?,?,?,?,?,?)",
               (result_group, step_name, 1, procedure, nsets, display_name, 0))
    for fi in range(nsets):
        is_modal = (procedure == 'FREQUENCY')
        db.execute("INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (result_group, step_name, fi, float(time_vals[fi]),
                    'mode {}'.format(fi + 1) if is_modal else 'frame {}'.format(fi),
                    None, float(time_vals[fi]) if is_modal else None,
                    (fi + 1) if is_modal else None,
                    fi, 0, fi, None, None))
    db.commit()

    # 每个场：collector(set_idx) -> (labels, array[N,ncomp]) 或 None
    def _disp(i):
        nn, d = rst.nodal_solution(i)
        return nn, d  # [N, ndof] (UX..ROTZ)

    def _stress(i):
        try:
            nn, d = rst.nodal_stress(i)
            return nn, d[:, :6]
        except Exception:
            return None

    def _strain(i):
        try:
            nn, d = rst.nodal_elastic_strain(i)
            return nn, d[:, :6]
        except Exception:
            return None

    avail = set(str(c).strip() for c in rst.available_results)
    dof_names = [str(x).upper() for x in rst.result_dof(0)]

    field_specs = []
    if 'NSL' in avail:
        field_specs.append(('U', dof_names, ['Magnitude'], _disp, 'vec'))
    if 'ENS' in avail:
        field_specs.append(('S', _TENSOR_COMPS, ['Mises'], _stress, 'tensor'))
    if 'EEL' in avail:
        field_specs.append(('E', _STRAIN_COMPS, ['Mises'], _strain, 'tensor'))

    for field_name, components, invariants, collector, kind in field_specs:
        # 先收集所有帧，确定 labels 与 ncomp（以第 0 帧为准）
        first = collector(0)
        if first is None:
            continue
        labels, d0 = first
        ncomp = d0.shape[1]
        N_ent = d0.shape[0]
        data = np.full((nsets, N_ent, ncomp), np.nan, dtype=np.float32)
        inv_arr = np.full((nsets, N_ent, len(invariants)), np.nan, dtype=np.float32) \
            if invariants else None
        data[0] = d0
        for fi in range(1, nsets):
            r = collector(fi)
            if r is None:
                continue
            _lab, dd = r
            if dd.shape == (N_ent, ncomp):
                data[fi] = dd
        # invariants
        if invariants:
            for fi in range(nsets):
                fr = data[fi]
                if kind == 'vec':
                    inv_arr[fi, :, 0] = np.linalg.norm(fr[:, :3], axis=1)
                elif kind == 'tensor':
                    inv_arr[fi, :, 0] = _von_mises(fr)

        h5_fname = '{}__{}.h5'.format(safe(step_name), safe(field_name))
        h5_rel_f = os.path.join('l1', 'results', safe(result_group), h5_fname)
        h5_abs_f = os.path.join(workspace, h5_rel_f)

        grp_path, n_ent, vmin, vmax = _write_field_h5(
            h5_abs_f, step_name, field_name, components, invariants,
            inst_name, labels, data, inv_arr, nsets)

        db.execute("INSERT OR REPLACE INTO result_blocks"
                   " (result_group, step_name, field_name, instance_name, position,"
                   "  elem_type, h5_path, label_path, n_entities, n_ip, n_sp)"
                   " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (result_group, step_name, field_name, inst_name, 'NODAL',
                    None, grp_path, grp_path + '/labels', n_ent, None, None))
        db.execute("INSERT OR REPLACE INTO result_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (result_group, step_name, field_name, h5_rel_f,
                    json.dumps(components), json.dumps(invariants),
                    json.dumps(['NODAL']), 0, vmin, vmax, 'rst'))
        db.commit()
        print("    field {} done (N={}, comps={}).".format(field_name, n_ent, len(components)))

    # result_group_meta
    db.execute(
        "INSERT OR REPLACE INTO result_group_meta"
        " (result_group, display_name, source_file, consistency_check, created_at)"
        " VALUES (?,?,?,?,datetime('now'))",
        (result_group, display_name, basename, 'self-contained'))
    db.commit()
    db.close()

    print("RST pack complete: {} nodes, {} elems, {} fields. ({} total)".format(
        N_nodes, total_elem, len(field_specs), _fmt_t(time.time() - t_total)))


def main():
    p = argparse.ArgumentParser(description='Ansys RST → HDF5 packer (L1, geometry + results)')
    p.add_argument('--rst',          required=True, help='Path to .rst file')
    p.add_argument('--workspace',    required=True, help='Workspace directory')
    p.add_argument('--result-group', default='default_result', help='Result group name')
    p.add_argument('--display-name', default=None, help='Display name for the result group')
    args = p.parse_args()

    rst_path  = os.path.abspath(args.rst)
    workspace = os.path.abspath(args.workspace)
    if not os.path.exists(rst_path):
        print('ERROR: RST file not found: {}'.format(rst_path), file=sys.stderr)
        sys.exit(1)
    mkdirs(workspace)
    pack_rst(rst_path, workspace, args.result_group, args.display_name)


if __name__ == '__main__':
    main()
