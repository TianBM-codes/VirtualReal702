#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ansys_geom.py — Ansys 网格 → L1 几何 HDF5 + manifest 的共享逻辑。

CDB（Archive）和 RST（read_binary）拿到的网格对象不同，但都暴露同一套
pyvista UnstructuredGrid（带 ansys_* cell_data），所以几何提取完全一致，
抽到这里共用。产物结构与 bdf_pack / l1_pack 完全一致，L2/L3 无需感知来源。

调用方负责：读文件 → 得到 grid + 每单元 section 数组 + 组件 + 材料常数，
然后调 pack_ansys_geometry() 写几何；结果（仅 RST）由调用方另写。
"""
import json
import os
import time

import h5py
import numpy as np

from src.l1.bdf_pack import (
    mkdirs, safe, _fmt_t, str_ds, init_manifest, _refine_shell_sections,
)
from src.l1.ansys_etype_map import VTK_TO_ABAQUS, FACE_DEFS, family_for_etype


def section_type_code(family):
    """单元族 → 渲染用 section_type 短码（<=15 字符）。"""
    return {
        'SHELL': 'SHELL', 'PLANE': 'SHELL',
        'SOLID': 'SOLID',
        'BEAM': 'BEAM', 'LINK': 'BEAM', 'PIPE': 'BEAM',
        'MASS': 'MASS', 'SPRING': 'SPRING',
    }.get(family, (family[:15] if family else 'OTHER'))


def pack_ansys_geometry(grid, section_arr, node_components, element_components,
                        materials, sections_txt, reals_txt,
                        inst_name, workspace, shell_thickness_fn=None):
    """
    写 l1/geometry/<inst>.h5 + assembly.h5 + sets/sets.h5 + manifest(几何部分)。

    grid               : pyvista UnstructuredGrid（带 ansys_* cell_data / point_data）
    section_arr        : 每单元 section 号数组（长度 n_elem），CDB 来自 arch.section，
                         RST 来自 mesh.section
    node_components     : {name: node_label_array}
    element_components  : {name: elem_label_array}
    materials           : {mat_id: {'EX':..,'NUXY':..,'DENS':..} | {...}}
    sections_txt        : {sec_id: {'type','thickness'}}（CDB 文本解析；RST 传 {}）
    reals_txt           : {real_id: [r1,...]}（CDB；RST 传 {}）
    shell_thickness_fn  : 可选 fn(sec_id, real_id) -> thickness；None 则不写厚度

    返回 (h5_rel, N_nodes, total_elem_count)。
    """
    inst_safe = safe(inst_name)
    part_name = inst_name
    cd = grid.cell_data

    # ── 节点（排序，满足下游 searchsorted 约定）────────────────────────────────
    nnum   = np.asarray(grid.point_data['ansys_node_num'], dtype=np.int64)
    coords = np.asarray(grid.points, dtype=np.float64).reshape(-1, 3)
    order = np.argsort(nnum, kind='stable')
    node_labels = nnum[order].astype(np.int32)
    node_coords = coords[order]
    inv = np.empty(len(order), dtype=np.int64)
    inv[order] = np.arange(len(order))
    N_nodes = len(node_labels)

    # ── 每单元属性（按单元序对齐）──────────────────────────────────────────────
    n_elem    = grid.n_cells
    elem_num  = np.asarray(cd['ansys_elem_num'], dtype=np.int64)
    mat_all   = np.asarray(cd['ansys_material_type'], dtype=np.int64)
    real_all  = np.asarray(cd['ansys_real_constant'], dtype=np.int64)
    etnum_all = np.asarray(cd['ansys_elem_type_num'], dtype=np.int64)
    if np.ndim(section_arr) == 0:
        sec_all = np.full(n_elem, int(section_arr), dtype=np.int64)
    else:
        sec_all = np.asarray(section_arr, dtype=np.int64)
    celltypes = np.asarray(grid.celltypes)

    combos = sorted(set(zip(mat_all.tolist(), real_all.tolist(), sec_all.tolist())))
    combo_to_sid = {c: i for i, c in enumerate(combos)}
    section_names = ['M{}_R{}_S{}'.format(*c) for c in combos]

    l1_dir   = os.path.join(workspace, 'l1')
    geom_dir = os.path.join(l1_dir, 'geometry')
    sets_dir = os.path.join(l1_dir, 'sets')
    mkdirs(geom_dir)
    mkdirs(sets_dir)
    h5_rel = os.path.join('l1', 'geometry', inst_safe + '.h5')
    h5_abs = os.path.join(workspace, h5_rel)

    bbox_min_v = node_coords.min(axis=0).tolist()
    bbox_max_v = node_coords.max(axis=0).tolist()

    etd_rows = []
    pairs_nr, pairs_el = [], []
    skipped = {}
    total_elem_count = 0

    print("  Writing geometry/{}.h5 ...".format(inst_safe))
    t0 = time.time()
    with h5py.File(h5_abs, 'w') as f:
        f.create_dataset('nodes/labels', data=node_labels)
        f.create_dataset('nodes/coords', data=node_coords)
        if section_names:
            str_ds(f, 'section_names', section_names)

        for vtk_t in np.unique(celltypes):
            mapped = VTK_TO_ABAQUS.get(int(vtk_t))
            if mapped is None:
                skipped['vtk{}'.format(int(vtk_t))] = int(np.count_nonzero(celltypes == vtk_t))
                continue
            abaqus_name, n_corner, n_faces_et = mapped

            idx = np.where(celltypes == vtk_t)[0]
            conn_full = np.asarray(grid.cells_dict[int(vtk_t)], dtype=np.int64)
            if conn_full.shape[0] != len(idx):
                conn_full = conn_full[:len(idx)]
            conn_rows = inv[conn_full[:, :n_corner]].astype(np.int32)

            labels_arr = elem_num[idx].astype(np.int32)
            sec_ids = np.array(
                [combo_to_sid[(int(mat_all[i]), int(real_all[i]), int(sec_all[i]))]
                 for i in idx], dtype=np.int32)

            grp = f.require_group('elements/{}'.format(abaqus_name))
            grp.create_dataset('labels',     data=labels_arr)
            grp.create_dataset('conn',       data=conn_rows)
            grp.create_dataset('section_id', data=sec_ids)

            face_def = FACE_DEFS.get(abaqus_name, [])
            fei_list, fseq_list, fnc_list = [], [], []
            for ei, row in enumerate(conn_rows):
                for fi, face_local in enumerate(face_def):
                    fei_list.append(ei)
                    fseq_list.append(fi)
                    fnc_list.append([int(row[k]) for k in face_local])
            if fei_list:
                max_fn = max(len(r) for r in fnc_list)
                fnc_padded = np.full((len(fnc_list), max_fn), -1, dtype=np.int32)
                for _i, _r in enumerate(fnc_list):
                    fnc_padded[_i, :len(_r)] = _r
                grp.create_dataset('face_elem_idx',  data=np.array(fei_list, dtype=np.int32))
                grp.create_dataset('face_seq',       data=np.array(fseq_list, dtype=np.int32))
                grp.create_dataset('face_node_conn', data=fnc_padded)
            else:
                grp.create_dataset('face_elem_idx',  data=np.array([], dtype=np.int32))
                grp.create_dataset('face_seq',       data=np.array([], dtype=np.int32))
                grp.create_dataset('face_node_conn', data=np.array([], dtype=np.int32))

            fam_codes = [section_type_code(family_for_etype(int(etnum_all[i]))) for i in idx]
            mat_arr = np.array(
                [str(int(mat_all[i])).encode('ascii')[:63] for i in idx], dtype='S64')
            sec_arr = np.array([c.encode('ascii')[:15] for c in fam_codes], dtype='S16')
            grp.create_dataset('material_name', data=mat_arr)
            grp.create_dataset('section_type',  data=sec_arr)

            for j in range(len(labels_arr)):
                for nr in conn_rows[j]:
                    if int(nr) >= 0:
                        pairs_nr.append(int(nr))
                        pairs_el.append(int(labels_arr[j]))

            etd_rows.append((inst_name, abaqus_name, len(labels_arr), 0, n_corner, n_faces_et))
            total_elem_count += len(labels_arr)
            print("    {}: {} elem(s)".format(abaqus_name, len(labels_arr)))

        for code, cnt in sorted(skipped.items()):
            print("  WARNING: skipped {} element(s) of unmapped {}".format(cnt, code))

        # sections/ 组
        for combo, sid in combo_to_sid.items():
            mat_id, real_id, sec_id = combo
            sg = f.require_group('sections/{}'.format(sid))
            sample = np.where((mat_all == mat_id) & (real_all == real_id) & (sec_all == sec_id))[0]
            fam = family_for_etype(int(etnum_all[sample[0]])) if len(sample) else 'OTHER'
            sty = section_type_code(fam)
            sec_kind = sections_txt.get(sec_id, {}).get('type', '') if sections_txt else ''
            thick = None
            if sty == 'SHELL' and shell_thickness_fn is not None:
                thick = shell_thickness_fn(sec_id, real_id)
            sg.attrs['type']          = sec_kind or sty
            sg.attrs['thickness']     = float(thick) if thick is not None else float('nan')
            sg.attrs['material_name'] = str(mat_id)
            sg.attrs['element_set']   = 'M{}_R{}_S{}_ELEMS'.format(mat_id, real_id, sec_id)

        # materials/ 组
        for mat_id, props in (materials or {}).items():
            mg = f.require_group('materials/{}'.format(mat_id))
            ex = props.get('EX')
            nu = props.get('NUXY', props.get('PRXY'))
            if ex is not None:
                mg.attrs['type'] = 'ISOTROPIC'
                mg.create_dataset('elastic_table',
                                  data=np.array([[ex, nu if nu is not None else 0.0]],
                                                dtype=np.float64))
            else:
                mg.attrs['type'] = 'THERMAL' if 'KXX' in props else 'UNKNOWN'

        # 组件集合 → instance_sets（过滤 ANSYS 内部 _ 名）
        iset_node_counts, iset_elem_counts = {}, {}
        for cname, labels in (node_components or {}).items():
            if cname.startswith('_'):
                continue
            set_safe = safe(cname)
            arr = np.array(sorted(int(x) for x in np.asarray(labels)), dtype=np.int32)
            f.create_dataset('instance_sets/node_sets/{}'.format(set_safe), data=arr)
            iset_node_counts[set_safe] = len(arr)
        for cname, labels in (element_components or {}).items():
            if cname.startswith('_'):
                continue
            set_safe = safe(cname)
            arr = np.array(sorted(int(x) for x in np.asarray(labels)), dtype=np.int32)
            f.create_dataset('instance_sets/element_sets/{}'.format(set_safe), data=arr)
            iset_elem_counts[set_safe] = len(arr)

        # node_to_elements CSR
        if pairs_nr:
            pairs_nr_arr = np.array(pairs_nr, dtype=np.int32)
            pairs_el_arr = np.array(pairs_el, dtype=np.int32)
            ordr = np.argsort(pairs_nr_arr, kind='stable')
            pairs_nr_arr = pairs_nr_arr[ordr]
            pairs_el_arr = pairs_el_arr[ordr]
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

    print("    done. ({})".format(_fmt_t(time.time() - t0)))
    _refine_shell_sections(h5_abs)

    # assembly.h5
    print("  Writing assembly.h5 ...")
    with h5py.File(os.path.join(l1_dir, 'assembly.h5'), 'w') as f:
        grp = f.require_group('instances/{}'.format(inst_name))
        grp.create_dataset('transform', data=np.eye(4, dtype=np.float64))
        grp.create_dataset('part_name', data=part_name.encode('utf-8'))

    # sets/sets.h5 stub
    with h5py.File(os.path.join(sets_dir, 'sets.h5'), 'w') as _f:
        pass

    # manifest（几何部分）
    print("  Writing manifest.db (geometry) ...")
    db = init_manifest(workspace)
    db.execute(
        "INSERT OR REPLACE INTO instances VALUES (?,?,?,?,?,?,?,?)",
        (inst_name, part_name, h5_rel, None, N_nodes, total_elem_count,
         json.dumps(bbox_min_v), json.dumps(bbox_max_v)),
    )
    for row in etd_rows:
        db.execute("INSERT OR REPLACE INTO element_type_dist VALUES (?,?,?,?,?,?)", row)
    for sname, cnt in iset_node_counts.items():
        db.execute("INSERT OR REPLACE INTO node_sets VALUES (?,?,?,?,?)",
                   (sname, inst_name, inst_name,
                    h5_rel + ':instance_sets/node_sets/' + sname, cnt))
    for sname, cnt in iset_elem_counts.items():
        db.execute("INSERT OR REPLACE INTO element_sets "
                   "(set_name, set_scope, instance_name, h5_path, elem_count) "
                   "VALUES (?,?,?,?,?)",
                   (sname, inst_name, inst_name,
                    h5_rel + ':instance_sets/element_sets/' + sname, cnt))
    db.commit()
    db.close()

    return h5_rel, N_nodes, total_elem_count
