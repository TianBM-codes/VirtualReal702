#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
op2_geom_pack.py — Layer 1 extraction for OP2 files that contain embedded geometry
(PARAM,POST,-1 style, with GEOM1/GEOM2 tables).

Uses pyNastran read_op2_geom which returns an object with the same .nodes /
.elements / .properties / .materials interface as BDF, so it reuses bdf_pack._pack_model.

Usage:
    python src/l1/op2_geom_pack.py --op2 /path/to/model.op2 --workspace /data/<job_id>/
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.l1.bdf_pack import _pack_model, mkdirs, _fmt_t, safe


def pack_op2_geom(op2_path, workspace):
    from pyNastran.op2.op2_geom import read_op2_geom

    t_total = time.time()
    op2_basename = os.path.basename(op2_path)
    inst_name = os.path.splitext(op2_basename)[0].upper()

    print('OP2-geom pack: {} → instance \'{}\''.format(op2_basename, inst_name))
    print('  Reading OP2 (with geometry) ...')
    t0 = time.time()
    model = read_op2_geom(op2_path, debug=False)
    # post=-2 OP2 files raise a benign FatalError at EOF; suppress it
    model.stop_on_unclosed_file = False
    print('  Read done. ({})'.format(_fmt_t(time.time() - t0)))

    if not model.nodes:
        print('ERROR: OP2 file has no embedded geometry (GEOM1/GEOM2 tables missing).',
              file=sys.stderr)
        print('       Use bdf_pack.py with the companion .bdf file instead.', file=sys.stderr)
        sys.exit(2)

    _pack_model(model, inst_name, workspace)
    print('OP2-geom pack complete. ({} total)'.format(_fmt_t(time.time() - t_total)))


def main():
    p = argparse.ArgumentParser(description='OP2 (with geometry) → HDF5 packer (L1)')
    p.add_argument('--op2',       required=True, help='Path to .op2 file with embedded geometry')
    p.add_argument('--workspace', required=True, help='Workspace directory')
    args = p.parse_args()

    op2_path  = os.path.abspath(args.op2)
    workspace = os.path.abspath(args.workspace)
    if not os.path.exists(op2_path):
        print('ERROR: OP2 file not found: {}'.format(op2_path), file=sys.stderr)
        sys.exit(1)
    mkdirs(workspace)
    pack_op2_geom(op2_path, workspace)


if __name__ == '__main__':
    main()
