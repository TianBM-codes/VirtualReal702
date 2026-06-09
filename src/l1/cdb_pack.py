#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cdb_pack.py — Layer 1 extraction for Ansys (MAPDL) CDB archive files.

CDB 只有几何（节点、单元、单元属性号），没有结果。流程与 bdf_pack 平行：
解析 → 几何 HDF5 + manifest.db，产物结构与 ODB/BDF 完全一致，L2/L3 无需感知来源。

解析用 ansys-mapdl-reader（最全、最快，且自动处理退化单元 → VTK 拓扑）。
材料常数 / 截面厚度 Archive 不解析，由 cdb_text.py 从原始文本补出。
几何写盘逻辑与 RST 共用 ansys_geom.pack_ansys_geometry。

Usage:
    python src/l1/cdb_pack.py --cdb /path/to/model.cdb --workspace /data/<job_id>/
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.l1.bdf_pack import mkdirs, _fmt_t
from src.l1.ansys_geom import pack_ansys_geometry
from src.l1 import cdb_text


def pack_cdb(cdb_path, workspace):
    from ansys.mapdl import reader as pymapdl_reader

    t_total = time.time()
    basename = os.path.basename(cdb_path)
    inst_name = os.path.splitext(basename)[0].upper()

    print("CDB pack: {} → instance '{}'".format(basename, inst_name))
    print("  Reading CDB (ansys-mapdl-reader) ...")
    t0 = time.time()
    arch = pymapdl_reader.Archive(cdb_path)
    print("    {} nodes, {} elements. ({})".format(
        arch.n_node, arch.n_elem, _fmt_t(time.time() - t0)))

    # 文本补充：材料常数 / 截面 / 实常数（Archive 不解析这块）
    materials = cdb_text.parse_materials(cdb_path)
    sections  = cdb_text.parse_sections(cdb_path)
    reals     = cdb_text.parse_real_constants(cdb_path)

    def shell_thk(sec_id, real_id):
        return cdb_text.shell_thickness(sec_id, real_id, sections, reals)

    h5_rel, N, E = pack_ansys_geometry(
        grid=arch.grid,
        section_arr=arch.section,
        node_components=arch.node_components,
        element_components=arch.element_components,
        materials=materials,
        sections_txt=sections,
        reals_txt=reals,
        inst_name=inst_name,
        workspace=workspace,
        shell_thickness_fn=shell_thk,
    )
    print("CDB pack complete: {} nodes, {} elems. ({} total)".format(
        N, E, _fmt_t(time.time() - t_total)))


def main():
    p = argparse.ArgumentParser(description='Ansys CDB → HDF5 packer (L1)')
    p.add_argument('--cdb',       required=True, help='Path to .cdb file')
    p.add_argument('--workspace', required=True, help='Workspace directory')
    args = p.parse_args()

    cdb_path  = os.path.abspath(args.cdb)
    workspace = os.path.abspath(args.workspace)
    if not os.path.exists(cdb_path):
        print('ERROR: CDB file not found: {}'.format(cdb_path), file=sys.stderr)
        sys.exit(1)
    mkdirs(workspace)
    pack_cdb(cdb_path, workspace)


if __name__ == '__main__':
    main()
