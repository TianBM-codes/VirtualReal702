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

# NODAL constant — needed for getSubset(position=NODAL) invariant extraction.
_NODAL_CONST = None
try:
    from abaqusConstants import NODAL as _NODAL_CONST  # noqa: F401
except Exception:
    try:
        _NODAL_CONST = odbAccess.NODAL
    except Exception:
        pass

# INTEGRATION_POINT constant — needed to fetch the IP tensor for invariants that
# have no getScalarField constant (e.g. MAX_INPLANE_PRINCIPAL_ABS), which must be
# computed by numpy at the integration point too.
_IP_CONST = None
try:
    from abaqusConstants import INTEGRATION_POINT as _IP_CONST  # noqa: F401
except Exception:
    try:
        _IP_CONST = odbAccess.INTEGRATION_POINT
    except Exception:
        pass

# Invariant constants for getScalarField(invariant=...) API calls.
# Maps our internal key → Abaqus constant. Loaded defensively because older
# Abaqus versions may not expose all of these.
_INV_CONSTANTS = {}
try:
    from abaqusConstants import (  # noqa: F401
        MISES, TRESCA, PRESS, INV3,
        MAX_PRINCIPAL, MID_PRINCIPAL, MIN_PRINCIPAL,
        MAX_INPLANE_PRINCIPAL, MIN_INPLANE_PRINCIPAL, OUTOFPLANE_PRINCIPAL,
        MAGNITUDE,
    )
    # 键名用 Abaqus validInvariants 实际吐出的形式(MAX_INPLANE_PRINCIPAL, 无下划线),
    # 否则 'inv in INV_ATTR_MAP / _INV_CONSTANTS' 永远匹配不上 → 面内不变量不会生成。
    _INV_CONSTANTS = {
        'MISES': MISES, 'TRESCA': TRESCA, 'PRESS': PRESS, 'INV3': INV3,
        'MAX_PRINCIPAL': MAX_PRINCIPAL, 'MID_PRINCIPAL': MID_PRINCIPAL,
        'MIN_PRINCIPAL': MIN_PRINCIPAL,
        'MAX_INPLANE_PRINCIPAL': MAX_INPLANE_PRINCIPAL,
        'MIN_INPLANE_PRINCIPAL': MIN_INPLANE_PRINCIPAL,
        'OUTOFPLANE_PRINCIPAL': OUTOFPLANE_PRINCIPAL,
        'MAGNITUDE': MAGNITUDE,
    }
    # Max.Principal(Abs): 较老 Abaqus 可能没有此常量, 单独防御导入。
    try:
        from abaqusConstants import MAX_PRINCIPAL_ABS  # noqa: F401
        _INV_CONSTANTS['MAX_PRINCIPAL_ABS'] = MAX_PRINCIPAL_ABS
    except Exception:
        pass
except Exception:
    pass


# ─── Constants ────────────────────────────────────────────────────────────────

ELEM_TYPE_CODE = {
    # shells (triangle: code 0, quad: code 1)
    'S3': 0,   'S3R': 0,   'S6': 0,   'STRI3': 0,
    'S4': 1,   'S4R': 1,   'S4R5': 1, 'S8R': 1,  'S8R5': 1,
    # solid tet (code 2), wedge (3), hex (4)
    'C3D4': 2,  'C3D4H': 2,
    'C3D6': 3,  'C3D6H': 3,
    'C3D8': 4,  'C3D8R': 4,  'C3D8H': 4,  'C3D8I': 4,  'C3D8RH': 4,
    # high-order solids
    'C3D10': 5,  'C3D10M': 5,  'C3D10H': 5,  'C3D10MH': 5,
    'C3D15': 6,  'C3D15H': 6,
    'C3D20': 7,  'C3D20R': 7,  'C3D20H': 7,  'C3D20RH': 7,
    # high-order curved triangle shell (code 8)
    'STRI65': 8,
    # line elements: truss / beam (codes 9, 10 — no surface faces)
    'T3D2':   9, 'B31':   9, 'B31OS': 9, 'PIPE31': 9,
    'T3D3':  10, 'B32':  10, 'B32OS':10, 'PIPE32':10,
    # 2-node connector / spring / dashpot elements (code 9 — line segments)
    'CONN3D2': 9, 'SPRING2': 9, 'SPRINGA': 9, 'DASHPOT2': 9, 'DASHPOTA': 9,
    # point elements: concentrated mass / rotary inertia / grounded spring /
    # grounded dashpot (code 11 — single node, no faces). Kept in sync with
    # src/l2/ingest.py POINT_ELEM_CODES so L2 collect_points() renders them.
    'MASS':  11, 'ROTARYI': 11, 'SPRING1': 11, 'DASHPOT1': 11,
}

ELEM_N_CORNER = {0: 3, 1: 4, 2: 4, 3: 6, 4: 8, 5: 4, 6: 6, 7: 8,
                 8: 3,   # STRI65 corner count
                 9: 2,   # LINE2
                 10: 2,  # LINE3 (2 corners + 1 mid-node)
                 11: 1}  # MASS / ROTARYI (single node)

HIGH_ORDER_CODES = {5, 6, 7, 8, 10}  # 8=STRI65, 10=LINE3 have mid-nodes

# Face definitions: code → list of face corner-node-index lists (0-based, corner only)
# Codes 9, 10 (line elements) have no faces and are omitted intentionally.
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
    8: [[0, 1, 2]],  # STRI65: single triangle face (corner nodes 0-2)
}

# Mid-node column indices in full connectivity (0-based)
MIDNODE_INDICES = {
    'S6':      [3, 4, 5],
    'S8R':     [4, 5, 6, 7],
    'S8R5':    [4, 5, 6, 7],
    'STRI65':  [3, 4, 5],
    'C3D10':   [4, 5, 6, 7, 8, 9],
    'C3D10M':  [4, 5, 6, 7, 8, 9],
    'C3D10H':  [4, 5, 6, 7, 8, 9],
    'C3D10MH': [4, 5, 6, 7, 8, 9],
    'C3D15':   [6, 7, 8, 9, 10, 11, 12, 13, 14],
    'C3D15H':  [6, 7, 8, 9, 10, 11, 12, 13, 14],
    'C3D20':   [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    'C3D20R':  [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    'C3D20H':  [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    'C3D20RH': [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    'T3D3':    [2],
    'B32':     [2],
    'B32OS':   [2],
    'PIPE32':  [2],
}

# Abaqus variant suffixes ordered longest-first so multi-char tokens (OS, RH,
# MH, RT, R5) are stripped before their single-char components.
_ABAQUS_VARIANT_SUFFIXES = ('OS', 'RH', 'MH', 'R5', 'R', 'H', 'I', 'M', 'T', '5')


def _resolve_elem_code(etype_str):
    """Return ELEM_TYPE_CODE for etype_str, stripping variant suffixes if needed."""
    s = etype_str.upper()
    while True:
        code = ELEM_TYPE_CODE.get(s)
        if code is not None:
            return code
        for suf in _ABAQUS_VARIANT_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf):
                s = s[:-len(suf)]
                break
        else:
            return None


def _resolve_midnode_indices(etype_str):
    """Return MIDNODE_INDICES for etype_str, with suffix-strip fallback."""
    s = etype_str.upper()
    while True:
        result = MIDNODE_INDICES.get(s)
        if result is not None:
            return result
        for suf in _ABAQUS_VARIANT_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf):
                s = s[:-len(suf)]
                break
        else:
            return []

PROCEDURE_MAP = {
    'STATIC_GENERAL':           'STATIC',
    'STATIC_RIKS':              'STATIC',
    'FREQUENCY':                'FREQUENCY',
    'DYNAMIC_IMPLICIT':         'DYNAMIC',
    'DYNAMIC_EXPLICIT':         'DYNAMIC',
    'DYNAMIC_TEMPDISPLACEMENT': 'DYNAMIC',
    'BUCKLE':                   'BUCKLE',
}

# Mapping from Abaqus validInvariants string → FieldValue attribute name.
# Naming convention for synthetic invariant fields: {field_name}_{INV_KEY}
# e.g. field "S" + "MISES" → synthetic field "S_MISES"
# Component fields (if ever split) use full componentLabel: "S_S11", "E_E11"
INV_ATTR_MAP = {
    'MISES':                     'mises',
    'TRESCA':                    'tresca',
    'PRESS':                     'press',
    'INV3':                      'inv3',
    'MAX_PRINCIPAL':             'maxPrincipal',
    'MID_PRINCIPAL':             'midPrincipal',
    'MIN_PRINCIPAL':             'minPrincipal',
    'MAX_INPLANE_PRINCIPAL':     'maxInPlanePrincipal',
    'MIN_INPLANE_PRINCIPAL':     'minInPlanePrincipal',
    'OUTOFPLANE_PRINCIPAL':      'outOfPlanePrincipal',
    'MAX_PRINCIPAL_ABS':         'maxPrincipalAbs',
    'MAX_INPLANE_PRINCIPAL_ABS': 'maxInPlanePrincipalAbs',
    'MAGNITUDE':                 'magnitude',
}

# Abaqus invariant name → _compute_invariants_numpy() name. Used to recompute
# ELEMENT_NODAL / NODAL invariants from the extrapolated tensor (method B),
# which stays physically valid (e.g. Mises ≥ 0) and matches Abaqus
# block.<invariant>; extrapolating the IP-computed scalar invariant instead
# overshoots (negative Mises). See docs/l2/HighOrder-Midside-Subdivision-Design.md.
_NUMPY_INV_NAME = {
    'MISES':                     'MISES',
    'TRESCA':                    'TRESCA',
    'PRESS':                     'PRESS',
    'INV3':                      'INV3',
    'MAX_PRINCIPAL':             'MAX_PRINCIPAL',
    'MID_PRINCIPAL':             'MID_PRINCIPAL',
    'MIN_PRINCIPAL':             'MIN_PRINCIPAL',
    'MAX_INPLANE_PRINCIPAL':     'MAX_INPLANE_PRINCIPAL',
    'MIN_INPLANE_PRINCIPAL':     'MIN_INPLANE_PRINCIPAL',
    'OUTOFPLANE_PRINCIPAL':      'OUTOFPLANE_PRINCIPAL',
    'MAX_PRINCIPAL_ABS':         'MAX_PRINCIPAL_ABS',
    'MAX_INPLANE_PRINCIPAL_ABS': 'MAX_INPLANE_PRINCIPAL_ABS',
    'MAGNITUDE':                 'MAGNITUDE',
}

# Invariant suffixes skipped even when --invariants full is passed.
# MAGNITUDE excluded — L3 computes it on-the-fly from components (identical result).
_HIDDEN_INV_SUFFIXES = frozenset({
    'MAGNITUDE',
})

# 仅对壳/膜单元有效的不变量。实体单元块写 NaN(前端置灰), 与 Abaqus 行为一致:
# 实体没有面内/面外之分, 选这些分量时 Abaqus 把实体显示为灰色。
_SHELL_ONLY_INVS = frozenset({
    'MAX_INPLANE_PRINCIPAL', 'MIN_INPLANE_PRINCIPAL',
    'OUTOFPLANE_PRINCIPAL', 'MAX_INPLANE_PRINCIPAL_ABS',
})

# 用 numpy 从张量自算的不变量(IP 位置也走 numpy, 不走 getScalarField):
#   - MAX_INPLANE_PRINCIPAL_ABS: 无 getScalarField 常量;
#   - MAX_PRINCIPAL_ABS: Abaqus getScalarField 该量为"无符号幅值", 与 viewer 云图
#     (带符号, 可为负)不一致 → 统一用我方带符号公式自算, 三个位置口径一致。
_NUMPY_ONLY_INVS = frozenset({
    'MAX_INPLANE_PRINCIPAL_ABS',
    'MAX_PRINCIPAL_ABS',
})

# 应变类场: Abaqus 输出的剪切分量是工程剪应变 γ = 2ε, 算主值/不变量前须 ÷2 还原成
# 张量剪应变 ε。应力场不在此列(应力剪切本就是真张量分量)。清单先按 Abaqus 常见
# 张量应变场全列, 后续按需校对。
_STRAIN_FIELDS = frozenset({
    'E', 'LE', 'NE', 'PE', 'EE', 'IE', 'THE', 'CE', 'VE', 'SE',
})


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
    p.add_argument('--geom-source', choices=['odb', 'inp'], default='odb',
                   help='How project geometry was extracted: odb=hard-fail on count mismatch, '
                        'inp=warn only (INP parser may miss connector/special elements)')
    p.add_argument('--invariants', choices=['none', 'full'], default='none',
                   help=('none=skip invariants (fast, default); '
                         'full=extract all validInvariants via getScalarField '
                         '(Abaqus-computed, creates synthetic scalar fields)'))
    # ── legacy parallel-worker params ────────────────────────────────────────
    p.add_argument('--step',   default=None,
                   help='Step name (results-worker mode only)')
    p.add_argument('--fields', default=None,
                   help='Comma-separated field names (results-worker mode only)')
    p.add_argument('--steps', default=None,
                   help='Comma-separated step names to extract (extract mode only)')
    p.add_argument('--frames', default=None,
                   help=('Frame filter for extract mode: "all" (default), '
                         '"first_last", or comma-separated 0-based indices'))
    p.add_argument('--field-prefix', default=None,
                   help='Only extract fields whose names start with this prefix (extract mode only)')
    p.add_argument('--worker-id', default='0',
                   help='Worker ID shown in log prefix (results-worker mode only)')
    return p.parse_args()


def safe(name):
    """Sanitize a name for use as a filesystem path component."""
    return name.replace('/', '__').replace('\\', '__').replace(' ', '_')


def split_region_field(name):
    """Split a region-qualified field name into (base_name, region).

    Contact outputs are stored per contact pair with the surface names baked
    into the field name after whitespace, e.g.
        'CPRESS   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1'
        -> ('CPRESS', 'ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1')
    A suffix counts as a region only if it contains '/' (surface pair) or
    starts with 'ASSEMBLY'. Plain fields return (name, None) unchanged.
    """
    parts = name.split(None, 1)
    if len(parts) == 2:
        suffix = parts[1].strip()
        if '/' in suffix or suffix.startswith('ASSEMBLY'):
            return parts[0], suffix
    return name, None


def _section_material_name(sec):
    """从 ODB section 对象取材料名。

    均质 section（实体/壳）直接有 ``.material``（材料名字符串）。
    复合材料壳铺层 section（如碳纤维 T700）没有 ``.material``，材料藏在
    ``.layup`` 里——每个铺层（SectionLayer）各自带 ``.material``。此时把铺层
    里去重后的材料名拼成 'T700/EPOXY'（纯单材料铺层就是 'T700'），否则按
    材料配色时这些单元会全部落到 '(none)'。
    Python 2/3 兼容。
    """
    mat = getattr(sec, 'material', '') or ''
    if mat:
        return mat
    layup = getattr(sec, 'layup', None)
    if layup:
        mats = []
        for layer in layup:
            m = getattr(layer, 'material', '') or ''
            if m and m not in mats:
                mats.append(m)
        if mats:
            return '/'.join(mats)
    return ''


def _canon_inst(name):
    """Instance 名规范化：统一大写。

    与 src/l1/manifest_schema.canon_instance 保持一致；此处单独定义一份，
    因为本文件在 Abaqus Python 2.7 下运行，不依赖 src 包的可导入性。
    凡 ODB 实例名进入流水线（原始 .npy 目录名 / meta / result_block key /
    assembly.h5 group）的源头都过这里，保证与 INP 侧（同样大写）对齐。
    """
    if name is None:
        return name
    return name.upper()


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


def _block_sp_num(blk):
    """Return the section-point number for a bulkDataBlock, or None.

    Module-level so both dump_results and _extract_ip_invariants can use it
    (it was previously only a nested def inside dump_results, which raised
    NameError when called from _extract_ip_invariants).
    """
    sp_obj = getattr(blk, 'sectionPoint', None)
    return int(sp_obj.number) if sp_obj is not None else None


def _fmt_t(secs):
    """Format elapsed seconds as '1m 23.4s' or '5.2s'."""
    if secs >= 60:
        return "{:d}m {:.1f}s".format(int(secs) // 60, secs % 60)
    return "{:.1f}s".format(secs)


def _parse_csv_names(text):
    if not text:
        return None
    names = [item.strip() for item in text.split(',') if item.strip()]
    return names or None


def _parse_frame_spec(text):
    if not text:
        return None
    spec = text.strip()
    if not spec or spec == 'all':
        return None
    if spec == 'first_last':
        return spec

    frames = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part)
        except ValueError:
            raise ValueError("Invalid frame index '{}'".format(part))
        if idx < 0:
            raise ValueError("Frame index must be >= 0, got {}".format(idx))
        frames.append(idx)

    if not frames:
        return None
    return sorted(set(frames))


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

def compute_face_data(etype_code, conn_corner):
    """
    Build face index/seq/conn arrays from FACE_DEFS.

    conn_corner : [M, n_corner] int32  row indices into node array

    Returns (face_elem_idx, face_seq, face_node_conn)
    Node ordering follows FACE_DEFS directly — no flip needed because
    FACE_DEFS already encodes the correct winding, and getNormal returns
    normals consistent with that ordering.
    """
    face_defs = FACE_DEFS.get(etype_code, [])
    if not face_defs:
        return (np.zeros(0, dtype=np.int32),
                np.zeros(0, dtype=np.uint8),
                np.zeros((0, 1), dtype=np.int32))

    M      = conn_corner.shape[0]
    max_fn = max(len(fd) for fd in face_defs)

    all_eidx, all_seq, all_fnc = [], [], []

    for seq0, fd in enumerate(face_defs):
        n_fn   = len(fd)
        fd_arr = np.array(fd, dtype=np.int32)
        fnc    = np.full((M, max_fn), -1, dtype=np.int32)
        fnc[:, :n_fn] = conn_corner[:, fd_arr]

        all_eidx.append(np.arange(M, dtype=np.int32))
        all_seq.append(np.full(M, seq0 + 1, dtype=np.uint8))
        all_fnc.append(fnc)

    return (
        np.concatenate(all_eidx),
        np.concatenate(all_seq),
        np.concatenate(all_fnc, axis=0),
    )


# ─── INTEGRATION_POINT reshape ────────────────────────────────────────────────

def _block_data_2d(block):
    """Return block.data as a 2D float32 array [N, ncomp].

    Scalar fields (e.g. STATUS) may return a 1D array [N] in some Abaqus
    versions.  Always promote to [N, 1] so callers can use .shape[1] safely.
    """
    d = np.array(block.data, dtype=np.float32)
    if d.ndim == 1:
        d = d.reshape(-1, 1)
    return d


def reshape_ip_block(block):
    """
    Reshape flat IP block into structured arrays.

    Abaqus FieldBulkData API (correct attributes):
      block.integrationPoints  — int array [total_rows], IP number per row
      block.sectionPoint       — single SectionPoint object (or None) for the
                                  entire block; .number gives the SP number

    Each bulkDataBlock covers exactly ONE section point (or no SP for solids).
    Shell fields produce multiple blocks per (instance, etype): one per SP.

    Returns
    -------
    u_elems  [M]           int32   unique element labels
    u_ips    [n_ip]        int32   unique IP numbers in this block
    sp_num   int | None            section-point number (None = solid / no SP)
    data_nd  [M, n_ip, ncomp]  float32
    """
    labels_flat = np.array(block.elementLabels, dtype=np.int32)
    data_flat   = _block_data_2d(block)
    ncomp       = data_flat.shape[1]

    # integrationPoints: per-row IP number array (correct attribute name)
    ip_raw = getattr(block, 'integrationPoints', None)
    if ip_raw is not None:
        ip_flat = np.array(ip_raw, dtype=np.int32)
    else:
        # Fallback: infer from total rows / unique elements
        n_total   = len(labels_flat)
        n_u_elems = len(np.unique(labels_flat))
        n_ip_inf  = n_total // n_u_elems if n_u_elems else 1
        ip_flat   = np.tile(np.arange(1, n_ip_inf + 1, dtype=np.int32), n_u_elems)

    # sectionPoint: single SectionPoint object for the whole block (or None)
    sp_obj = getattr(block, 'sectionPoint', None)
    sp_num = int(sp_obj.number) if sp_obj is not None else None

    u_elems = np.unique(labels_flat)
    u_ips   = np.unique(ip_flat)
    M, n_ip = len(u_elems), len(u_ips)

    e_idx  = np.searchsorted(u_elems, labels_flat)
    ip_idx = np.searchsorted(u_ips,   ip_flat)
    data_nd = np.zeros((M, n_ip, ncomp), dtype=np.float32)
    data_nd[e_idx, ip_idx] = data_flat

    return u_elems, u_ips, sp_num, data_nd


def reshape_element_nodal_block(block):
    """flat ELEMENT_NODAL → (u_elems [M], data_nd [M, n_enodes, ncomp])"""
    labels_flat = np.array(block.elementLabels, dtype=np.int32)
    data_flat   = _block_data_2d(block)
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
            iname = _canon_inst(inst.name if hasattr(inst, 'name') else str(inst))
            for node in node_array:
                by_inst.setdefault(iname, []).append(node.label)
    else:
        # 旧版：每个 node 自带 instanceName
        for node in nodes_seq:
            by_inst.setdefault(_canon_inst(node.instanceName), []).append(node.label)
    return by_inst


def _set_elems_by_inst(es):
    """同上，针对 element set。"""
    by_inst = {}
    elems_seq = es.elements
    inst_seq  = getattr(es, 'instances', None)
    if inst_seq and len(inst_seq) == len(elems_seq):
        for inst, elem_array in zip(inst_seq, elems_seq):
            iname = _canon_inst(inst.name if hasattr(inst, 'name') else str(inst))
            for elem in elem_array:
                by_inst.setdefault(iname, []).append(elem.label)
    else:
        for elem in elems_seq:
            by_inst.setdefault(_canon_inst(elem.instanceName), []).append(elem.label)
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
        inst_name = _canon_inst(inst_name)
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

    # Datum coordinate systems — written to datum_csyses.json for l1_pack.py
    datum_csyses = {}
    if hasattr(assembly, 'datumCsyses'):
        for dc_name, csys in assembly.datumCsyses.items():
            try:
                origin = [float(x) for x in csys.origin]
                # coordSysType: CARTESIAN / CYLINDRICAL / SPHERICAL (Abaqus version dependent)
                sys_type = 'RECTANGULAR'
                if hasattr(csys, 'coordSysType'):
                    t = str(csys.coordSysType).upper()
                    if 'CYL' in t:
                        sys_type = 'CYLINDRICAL'
                    elif 'SPH' in t:
                        sys_type = 'SPHERICAL'

                e1 = e2 = None
                # Try known attribute name variants across Abaqus versions
                for attr1, attr2 in [('xAxis', 'yAxis'), ('axis1', 'axis2'),
                                      ('axis_1', 'axis_2'), ('X', 'Y')]:
                    if hasattr(csys, attr1) and hasattr(csys, attr2):
                        e1 = [float(x) for x in getattr(csys, attr1)]
                        e2 = [float(x) for x in getattr(csys, attr2)]
                        break

                if e1 is None:
                    print("WARNING: datumCsys '{}' - could not read axes. "
                          "Available attrs: {}".format(dc_name,
                          [a for a in dir(csys) if not a.startswith('_')]))
                    continue
                datum_csyses[dc_name] = {
                    'system':  sys_type,
                    'origin':  origin,
                    'point_a': [origin[i] + e1[i] for i in range(3)],
                    'point_b': [origin[i] + e2[i] for i in range(3)],
                }
            except Exception as ex:
                print("WARNING: datumCsys '{}' read failed: {}".format(dc_name, ex))
    if datum_csyses:
        import json as _json
        with open(os.path.join(asm_dir, 'datum_csyses.json'), 'w') as fp:
            _json.dump(datum_csyses, fp)
        print("  {} datum CSYS written".format(len(datum_csyses)))

    print("    done. ({})".format(_fmt_t(time.time() - t0)))


def dump_geometry(odb, raw_dir, meta):
    """Write per-instance geometry to l1_raw/geom/<inst>/"""
    assembly = odb.rootAssembly
    geom_dir = os.path.join(raw_dir, 'geom')
    mkdirs(geom_dir)

    geom_meta = {}   # populated into meta['geom']

    t_geom = time.time()
    for inst_name, instance in assembly.instances.items():
        inst_name = _canon_inst(inst_name)
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

        # Pre-collect only the element-set names referenced by sectionAssignments
        # (fast: attribute access only, no element iteration yet).
        _sec_region_names = set()
        try:
            for _sa in instance.sectionAssignments:
                _rname = getattr(getattr(_sa, 'region', None), 'name', '')
                if _rname:
                    _sec_region_names.add(_rname)
        except AttributeError:
            pass

        etype_meta = {}
        total_elems = 0
        has_highorder = False

        # Remaining non-surface / "special" elements not in the type table above
        # — primarily distributing couplings (DCOUP3D / DCOUP2D) with variable
        # 1-ref-to-N-leaf connectivity, plus exotic connectors/dashpots. We still
        # parse them instead of silently dropping: keep raw connectivity, and
        # expand the star-shaped ones into (ref, leaf) line segments so the front
        # end's existing RBE2-spider display (couplings/positions) can draw them.
        # (2-node connectors/springs and MASS points are now recognised line/
        # point types and flow through the normal elements/ path above.)
        special_elems = {}    # etype -> {'labels': ndarray, 'conn': [list-of-labels, ...]}
        coupling_segs = []    # flat list of [3]-coord points; every 2 = one segment
        coupling_rows = []    # flat list of node rows, parallel to coupling_segs

        for etype, edata in elem_by_type.items():
            etype_code = _resolve_elem_code(etype)
            if etype_code is None:
                _sp_lbls = np.array(edata['labels'], dtype=np.int32)
                special_elems[etype] = {'labels': _sp_lbls, 'conn': edata['conn']}
                # Star expansion: conn[0] = reference/control node, conn[1:] = leaves.
                # Coords come from THIS instance's node table; nodes belonging to
                # another instance (e.g. an assembly-level ref point) are skipped,
                # mirroring the INP exporter's label_to_row.get() behaviour.
                for _conn in edata['conn']:
                    if len(_conn) < 2:
                        continue
                    _carr   = np.array(_conn, dtype=np.int32)
                    _rows   = np.searchsorted(node_labels, _carr)
                    _rows_c = np.clip(_rows, 0, len(node_labels) - 1)
                    _exact  = node_labels[_rows_c] == _carr      # searchsorted hit?
                    if not _exact[0]:
                        continue                                  # ref node not local
                    _ref_xyz = node_coords[_rows_c[0]]
                    _ref_row = _rows_c[0]
                    for _k in range(1, len(_carr)):
                        if not _exact[_k]:
                            continue
                        coupling_segs.append(_ref_xyz)
                        coupling_segs.append(node_coords[_rows_c[_k]])
                        coupling_rows.append(_ref_row)
                        coupling_rows.append(_rows_c[_k])
                print("    special type {} ({} elems): parsed (non-surface)".format(
                    etype, len(_sp_lbls)))
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
                fei, fseq, fnc = compute_face_data(etype_code, conn_rows)
                npsave(os.path.join(td, 'face_elem_idx.npy'),  fei)
                npsave(os.path.join(td, 'face_seq.npy'),       fseq)
                npsave(os.path.join(td, 'face_node_conn.npy'), fnc)

            if is_ho:
                has_highorder = True
                hod = os.path.join(d, 'highorder', safe(etype))
                mkdirs(hod)
                npsave(os.path.join(hod, 'conn_full.npy'), conn_full.astype(np.int32))
                mid_idx = _resolve_midnode_indices(etype)
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

        # Special (non-surface) elements: raw connectivity as flat array + CSR
        # offsets (per-element node count varies, so no fixed [M,k] matrix).
        # Node labels are stored as-is (not row-mapped) — row mapping is deferred
        # to whoever consumes them later (display only needs couplings_positions).
        special_meta = {}
        if special_elems:
            sp_dir = os.path.join(d, 'special')
            mkdirs(sp_dir)
            for _etype, _sd in special_elems.items():
                _std = os.path.join(sp_dir, safe(_etype))
                mkdirs(_std)
                _flat = []
                _off  = [0]
                for _c in _sd['conn']:
                    _flat.extend(_c)
                    _off.append(len(_flat))
                npsave(os.path.join(_std, 'labels.npy'),       _sd['labels'])
                npsave(os.path.join(_std, 'conn_flat.npy'),    np.array(_flat, dtype=np.int32))
                npsave(os.path.join(_std, 'conn_offsets.npy'), np.array(_off,  dtype=np.int32))
                special_meta[_etype] = {'count': int(len(_sd['labels']))}

        # Coupling spider lines [N*2, 3] float32 — same contract as the INP
        # exporter's couplings/positions (interleaved ref/leaf endpoint pairs).
        n_coupling_segs = len(coupling_segs) // 2
        if coupling_segs:
            npsave(os.path.join(d, 'couplings_positions.npy'),
                   np.array(coupling_segs, dtype=np.float32))
            # Parallel [N*2] geometry node rows — for deform (per-node U lookup)
            npsave(os.path.join(d, 'couplings_node_rows.npy'),
                   np.array(coupling_rows, dtype=np.int32))

        # Instance sets
        isets_node = {}
        for sname, ns in instance.nodeSets.items():
            lbls = np.array(sorted([n.label for n in ns.nodes]), dtype=np.int32)
            isd  = os.path.join(d, 'isets', 'node_sets')
            mkdirs(isd)
            npsave(os.path.join(isd, safe(sname) + '.npy'), lbls)
            isets_node[sname] = len(lbls)

        isets_elem = {}
        # Collect labels for sets referenced by sectionAssignments (reuse this
        # iteration instead of calling sa.region.elements separately — much faster).
        _isets_sec_labels = {}   # set_name → sorted labels array (only the ones we need)
        for sname, es in instance.elementSets.items():
            lbls = np.array(sorted([e.label for e in es.elements]), dtype=np.int32)
            isd  = os.path.join(d, 'isets', 'elem_sets')
            mkdirs(isd)
            npsave(os.path.join(isd, safe(sname) + '.npy'), lbls)
            isets_elem[sname] = len(lbls)
            if sname in _sec_region_names:
                _isets_sec_labels[sname] = lbls

        # isInternal=True element sets are absent from instance.elementSets, so
        # the loop above misses them.  Fall back to sa.region.elements directly.
        for _sa in instance.sectionAssignments:
            _rname = getattr(getattr(_sa, 'region', None), 'name', '')
            if not _rname or _rname in _isets_sec_labels:
                continue
            try:
                _lbls = np.array(
                    sorted([e.label for e in _sa.region.elements]),
                    dtype=np.int32)
                if len(_lbls) == 0:
                    continue
                isd = os.path.join(d, 'isets', 'elem_sets')
                mkdirs(isd)
                npsave(os.path.join(isd, safe(_rname) + '.npy'), _lbls)
                isets_elem[_rname] = len(_lbls)
                _isets_sec_labels[_rname] = _lbls
            except Exception as _e:
                print("    [warn] internal set '{}': {}".format(_rname, _e))

        # Build section_id per etype: each sectionAssignment is its own domain.
        # Two assignments with the same sectionName are still separate domains.
        # section_id = index of the assignment in sectionAssignments order.
        section_names  = []   # one entry per assignment (index == section_id)
        _sec_lbl_parts = []
        _sec_sid_parts = []
        try:
            for _i, _sa in enumerate(instance.sectionAssignments):
                _sname = _sa.sectionName
                section_names.append(_sname)
                _sid   = _i
                _rname = getattr(getattr(_sa, 'region', None), 'name', '')
                _lbls  = _isets_sec_labels.get(_rname)
                if _lbls is not None and len(_lbls) > 0:
                    _sec_lbl_parts.append(_lbls)
                    _sec_sid_parts.append(np.full(len(_lbls), _sid, dtype=np.int32))
        except AttributeError:
            pass

        if _sec_lbl_parts:
            _sc_lbls = np.concatenate(_sec_lbl_parts)
            _sc_sids = np.concatenate(_sec_sid_parts)
            _srt = np.argsort(_sc_lbls, kind='stable')
            _sc_lbls = _sc_lbls[_srt]
            _sc_sids = _sc_sids[_srt]
        else:
            _sc_lbls = np.array([], dtype=np.int32)
            _sc_sids = np.array([], dtype=np.int32)

        for etype, edata in elem_by_type.items():
            if _resolve_elem_code(etype) is None:
                continue
            _elblsrt = np.array(edata['labels'], dtype=np.int32)
            _elblsrt = _elblsrt[np.argsort(_elblsrt)]
            td = os.path.join(d, 'elems', safe(etype))
            if _sc_lbls.size > 0:
                _ii = np.searchsorted(_sc_lbls, _elblsrt)
                _ii = np.clip(_ii, 0, len(_sc_lbls) - 1)
                _found = _sc_lbls[_ii] == _elblsrt
                _sid_arr = np.where(_found, _sc_sids[_ii], np.int32(-1)).astype(np.int32)
            else:
                _sid_arr = np.full(len(_elblsrt), -1, dtype=np.int32)
            npsave(os.path.join(td, 'section_id.npy'), _sid_arr)

        # Sections metadata (no element access, just names/types/thickness).
        # Stored as a list — one entry per sectionAssignment — because the same
        # sectionName can be assigned to multiple different element sets and a
        # dict would silently overwrite earlier entries (last-wins).
        sections_info = []
        try:
            for sa in instance.sectionAssignments:
                sname = sa.sectionName
                entry = {
                    'section_name':  sname,
                    'element_set':   getattr(sa.region, 'name', ''),
                    'material_name': '',
                    'type':          '',
                    'thickness':     None,
                }
                try:
                    sec = odb.sections[sname]
                    entry['type'] = type(sec).__name__
                    entry['material_name'] = _section_material_name(sec)
                    if hasattr(sec, 'thickness'):
                        entry['thickness'] = float(sec.thickness)
                except Exception:
                    pass
                sections_info.append(entry)
        except AttributeError:
            pass

        jdump(os.path.join(d, 'section_names.json'), section_names)

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
            'special_types': special_meta,
            'coupling_segments': n_coupling_segs,
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
        inst_name = _canon_inst(inst_name)
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
            _domain = getattr(frame, 'domain', None)
            _lc = getattr(frame, 'loadCase', None)
            frames_meta.append({
                'frame_idx':          fi,
                'frame_value':        float(frame.frameValue),
                'description':        frame.description,
                'domain':             str(_domain) if _domain is not None else None,
                'frequency':          getattr(frame, 'frequency', None),
                'mode_number':        getattr(frame, 'mode', None),
                'increment_number':   getattr(frame, 'incrementNumber', None),
                'is_imaginary':       int(getattr(frame, 'isImaginary', False) or False),
                'frame_id':           getattr(frame, 'frameId', None),
                'cyclic_mode_number': getattr(frame, 'cyclicModeNumber', None),
                'load_case':          str(_lc) if _lc is not None else None,
            })
            all_field_names.update(frame.fieldOutputs.keys())

        _tt = getattr(step, 'totalTime', None)
        _tp = getattr(step, 'timePeriod', None)
        steps_meta[step_name] = {
            'step_number': step_num,
            'procedure':   procedure,
            'num_frames':  num_frames,
            'description': getattr(step, 'description', None),
            'nlgeom':      int(bool(getattr(step, 'nlgeom', False))),
            'total_time':  float(_tt) if _tt is not None else None,
            'time_period': float(_tp) if _tp is not None else None,
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


def _compute_invariants_numpy(comp, inv_name, is_strain=False):
    """
    Compute a scalar invariant from a component array.

    comp      : [..., ncomp] float32/float64
    inv_name  : e.g. 'MISES', 'MAX_PRINCIPAL', 'PRESS', ...
    is_strain : True 时, 把剪切分量当工程剪应变 γ=2ε 处理, 组张量前 ÷2 还原成
                张量剪应变 ε(应力场为 False)。
    Returns   : [..., 1] float32, or None if ncomp < 4.

    Supports Voigt 6-component (full 3-D tensor: 11,22,33,12,13,23) and
    4-component (in-plane shell: 11,22,33,12, treats 13=23=0).
    """
    ncomp = comp.shape[-1]

    if inv_name == 'MAGNITUDE':
        result = np.sqrt(np.sum(comp.astype(np.float64) ** 2, axis=-1))
        return result[..., np.newaxis].astype(np.float32)

    if ncomp < 4:
        return None

    c11 = comp[..., 0].astype(np.float64)
    c22 = comp[..., 1].astype(np.float64)
    c33 = comp[..., 2].astype(np.float64)
    c12 = comp[..., 3].astype(np.float64)
    c13 = comp[..., 4].astype(np.float64) if ncomp >= 6 else np.zeros_like(c11)
    c23 = comp[..., 5].astype(np.float64) if ncomp >= 6 else np.zeros_like(c11)

    # 应变场: 剪切分量是工程剪应变 γ=2ε, ÷2 还原成张量剪应变后再参与主值/不变量计算。
    if is_strain:
        c12 = c12 * 0.5
        c13 = c13 * 0.5
        c23 = c23 * 0.5

    if inv_name == 'MISES':
        result = np.sqrt(np.maximum(0.0,
            0.5 * ((c11-c22)**2 + (c22-c33)**2 + (c33-c11)**2
                   + 6*(c12**2 + c13**2 + c23**2))))

    elif inv_name == 'PRESS':
        result = -(c11 + c22 + c33) / 3.0

    elif inv_name in ('MAX_PRINCIPAL', 'MID_PRINCIPAL', 'MIN_PRINCIPAL',
                      'TRESCA', 'MAX_PRINCIPAL_ABS'):
        shape = c11.shape
        N = max(int(np.prod(shape)), 1)
        T = np.empty((N, 3, 3), dtype=np.float64)
        T[:, 0, 0] = c11.ravel(); T[:, 1, 1] = c22.ravel(); T[:, 2, 2] = c33.ravel()
        T[:, 0, 1] = T[:, 1, 0] = c12.ravel()
        T[:, 0, 2] = T[:, 2, 0] = c13.ravel()
        T[:, 1, 2] = T[:, 2, 1] = c23.ravel()
        eigs = np.linalg.eigvalsh(T).reshape(shape + (3,))  # ascending
        if   inv_name == 'MAX_PRINCIPAL': result = eigs[..., 2]
        elif inv_name == 'MID_PRINCIPAL': result = eigs[..., 1]
        elif inv_name == 'MIN_PRINCIPAL': result = eigs[..., 0]
        elif inv_name == 'TRESCA':        result = eigs[..., 2] - eigs[..., 0]
        else:
            # MAX_PRINCIPAL_ABS: 三主应力中绝对值最大者, 保留其正负号(可为负)。
            # 与 Abaqus viewer "Max. Principal (Abs)" 云图一致(压应力主导区为负值)。
            lo = eigs[..., 0]; hi = eigs[..., 2]
            result = np.where(np.abs(hi) >= np.abs(lo), hi, lo)

    elif inv_name == 'INV3':
        # Abaqus "Third Invariant": r = (9/2 · S·S·S)^(1/3) = (27/2 · J3)^(1/3),
        # J3 = det(偏量)。np.cbrt 处理 J3<0 (保留符号 → 结果可为负)。
        p    = (c11 + c22 + c33) / 3.0
        d11  = c11 - p;  d22 = c22 - p;  d33 = c33 - p
        J3 = (d11*(d22*d33 - c23**2)
              - c12*(c12*d33 - c23*c13)
              + c13*(c12*c23 - d22*c13))
        result = np.cbrt(13.5 * J3)

    elif inv_name == 'MAX_INPLANE_PRINCIPAL':
        avg = (c11 + c22) * 0.5
        result = avg + np.sqrt(np.maximum(0.0, ((c11-c22)*0.5)**2 + c12**2))

    elif inv_name == 'MIN_INPLANE_PRINCIPAL':
        avg = (c11 + c22) * 0.5
        result = avg - np.sqrt(np.maximum(0.0, ((c11-c22)*0.5)**2 + c12**2))

    elif inv_name == 'MAX_INPLANE_PRINCIPAL_ABS':
        # 面内两主应力中绝对值最大者, 保留其正负号(可为负)。壳/膜专属;
        # 实体单元由调用方按 _SHELL_ONLY_INVS 置 NaN(置灰), 不会走到这里出值。
        avg = (c11 + c22) * 0.5
        rad = np.sqrt(np.maximum(0.0, ((c11-c22)*0.5)**2 + c12**2))
        ipmax = avg + rad; ipmin = avg - rad
        result = np.where(np.abs(ipmax) >= np.abs(ipmin), ipmax, ipmin)

    elif inv_name == 'OUTOFPLANE_PRINCIPAL':
        result = c33.copy()

    else:
        return None

    return result[..., np.newaxis].astype(np.float32)


def _extract_ip_invariants(step, step_name, field_name, first_field,
                           invariants, results_dir, safe_step, safe_field,
                           block_struct, odb_instances, selected_frames=None,
                           odb_field_names=None):
    """Extract scalar invariant fields via Abaqus getScalarField(invariant=...).

    Uses the Abaqus API to compute each invariant (guaranteed accuracy),
    then reads bulkDataBlocks for vectorized output. For element types where
    an invariant is not valid, the corresponding entries are NaN (rendered grey
    by the frontend).

    ELEMENT_NODAL path (preferred): stores [N_elem, n_local_node, 1] per block.
    INTEGRATION_POINT path: stores [N_elem, n_ip, 1] per block.
    NODAL path: stores [N_nodes, 1] per block.
    """
    is_strain = field_name in _STRAIN_FIELDS

    active_invs = [
        (inv, INV_ATTR_MAP[inv]) for inv in invariants
        if inv in INV_ATTR_MAP and inv not in _HIDDEN_INV_SUFFIXES
        and inv in _NUMPY_INV_NAME
    ]
    # Mises 只对应力场算; 应变场不需要 Mises(等效应变定义另说), 直接剔除不生成。
    if is_strain:
        active_invs = [(n, a) for (n, a) in active_invs if n != 'MISES']
    # Abs 变体不在 Abaqus validInvariants 里(viewer 端口径), 按需补上:
    #   MAX_PRINCIPAL_ABS         —— 任何张量场(有 3D 主应力)都加, 实体也有效;
    #   MAX_INPLANE_PRINCIPAL_ABS —— 仅当该场有面内主应力(=模型含壳/膜)时加。
    _inv_set  = set(invariants)
    _have_inv = set(n for n, _ in active_invs)
    if 'MAX_PRINCIPAL' in _inv_set and 'MAX_PRINCIPAL_ABS' not in _have_inv:
        active_invs.append(('MAX_PRINCIPAL_ABS', INV_ATTR_MAP['MAX_PRINCIPAL_ABS']))
    if 'MAX_INPLANE_PRINCIPAL' in _inv_set and 'MAX_INPLANE_PRINCIPAL_ABS' not in _have_inv:
        active_invs.append(
            ('MAX_INPLANE_PRINCIPAL_ABS', INV_ATTR_MAP['MAX_INPLANE_PRINCIPAL_ABS']))
    if not active_invs:
        return

    parent_field_dir = os.path.join(results_dir, '{}_{}'.format(safe_step, safe_field))

    # ── Discover blocks from parent block_struct (used for canonical labels) ──
    en_blocks = {}   # {(iname, etype, sp_num_key): {'labels': arr, 'n_enodes': int}}
    ip_blocks = {}   # {(iname, etype, sp_num_key): {'labels': arr, 'ip_labels': arr}}
    nodal_blocks = {}  # {(iname, etype, sp_num_key): {'labels': arr}}

    for (iname, pos, etype, sp_num_key), info in block_struct.items():
        if pos == 'ELEMENT_NODAL':
            n_enodes = info.get('n_enodes', 0)
            if n_enodes == 0:
                continue
            bd_parts = [parent_field_dir, safe(iname), 'ELEMENT_NODAL']
            if etype:
                bd_parts.append(safe(etype))
            if sp_num_key is not None:
                bd_parts.append('sp{}'.format(sp_num_key))
            lbl_path = os.path.join(*(bd_parts + ['labels.npy']))
            if not os.path.exists(lbl_path):
                continue
            en_blocks[(iname, etype, sp_num_key)] = {
                'labels': np.load(lbl_path), 'n_enodes': n_enodes}

        elif pos == 'INTEGRATION_POINT':
            bd_parts = [parent_field_dir, safe(iname), 'INTEGRATION_POINT']
            if etype:
                bd_parts.append(safe(etype))
            if sp_num_key is not None:
                bd_parts.append('sp{}'.format(sp_num_key))
            bd = os.path.join(*bd_parts)
            lbl_path = os.path.join(bd, 'labels.npy')
            if not os.path.exists(lbl_path):
                continue
            ip_lbl_path = os.path.join(bd, 'ip_labels.npy')
            ip_labels = (np.load(ip_lbl_path) if os.path.exists(ip_lbl_path)
                         else np.array([1], dtype=np.int32))
            ip_blocks[(iname, etype, sp_num_key)] = {
                'labels': np.load(lbl_path), 'ip_labels': ip_labels}

        elif pos == 'NODAL':
            bd_parts = [parent_field_dir, safe(iname), 'NODAL']
            if etype:
                bd_parts.append(safe(etype))
            if sp_num_key is not None:
                bd_parts.append('sp{}'.format(sp_num_key))
            lbl_path = os.path.join(*(bd_parts + ['labels.npy']))
            if not os.path.exists(lbl_path):
                continue
            nodal_blocks[(iname, etype, sp_num_key)] = {'labels': np.load(lbl_path)}

    if not en_blocks and not ip_blocks and not nodal_blocks:
        print("    [inv] no EN, IP or NODAL blocks found, skipping invariant extraction")
        return

    # ── Create synthetic field dirs and write static index files ─────────────
    inv_field_dirs = {}
    for inv_name, _ in active_invs:
        syn_field = '{}_{}'.format(field_name, inv_name)
        fdir = os.path.join(results_dir, '{}_{}'.format(safe_step, safe(syn_field)))
        mkdirs(fdir)
        inv_field_dirs[inv_name] = fdir

    for (iname, etype, sp_num_key), info in en_blocks.items():
        for inv_name in inv_field_dirs:
            bd_parts = [inv_field_dirs[inv_name], safe(iname), 'ELEMENT_NODAL']
            if etype:
                bd_parts.append(safe(etype))
            if sp_num_key is not None:
                bd_parts.append('sp{}'.format(sp_num_key))
            bd = os.path.join(*bd_parts)
            mkdirs(bd)
            npsave(os.path.join(bd, 'labels.npy'), info['labels'])
            sp_arr = (np.array([sp_num_key], dtype=np.int32)
                      if sp_num_key is not None else np.array([], dtype=np.int32))
            npsave(os.path.join(bd, 'sp_labels.npy'), sp_arr)

    for (iname, etype, sp_num_key), info in ip_blocks.items():
        for inv_name in inv_field_dirs:
            bd_parts = [inv_field_dirs[inv_name], safe(iname), 'INTEGRATION_POINT']
            if etype:
                bd_parts.append(safe(etype))
            if sp_num_key is not None:
                bd_parts.append('sp{}'.format(sp_num_key))
            bd = os.path.join(*bd_parts)
            mkdirs(bd)
            npsave(os.path.join(bd, 'labels.npy'),    info['labels'])
            npsave(os.path.join(bd, 'ip_labels.npy'), info['ip_labels'])
            sp_arr = (np.array([sp_num_key], dtype=np.int32)
                      if sp_num_key is not None else np.array([], dtype=np.int32))
            npsave(os.path.join(bd, 'sp_labels.npy'), sp_arr)

    for (iname, etype, sp_num_key), info in nodal_blocks.items():
        for inv_name in inv_field_dirs:
            bd_parts = [inv_field_dirs[inv_name], safe(iname), 'NODAL']
            if etype:
                bd_parts.append(safe(etype))
            if sp_num_key is not None:
                bd_parts.append('sp{}'.format(sp_num_key))
            bd = os.path.join(*bd_parts)
            mkdirs(bd)
            npsave(os.path.join(bd, 'labels.npy'), info['labels'])

    # ── Per-frame: call getScalarField then read bulkDataBlocks ──────────────
    if selected_frames is None:
        selected_frames = list(enumerate(step.frames))
    num_frames = len(selected_frames)

    # field_name may be a normalized logical name (region suffix stripped);
    # odb_field_names carries the raw ODB keys to look up in fieldOutputs.
    _lookup_names = odb_field_names or [field_name]

    for frame_idx, (_, frame) in enumerate(selected_frames):
        _present = [n for n in _lookup_names if n in frame.fieldOutputs]
        if not _present:
            continue
        field_out = frame.fieldOutputs[_present[0]]

        # ── Method B precompute: extrapolated tensor at EN / NODAL, ONCE per ──
        # frame. EN/NODAL invariants are computed from these tensor components
        # (via _compute_invariants_numpy), matching Abaqus block.<invariant> and
        # staying physically valid (Mises ≥ 0). Extrapolating the IP-computed
        # scalar invariant instead overshoots (negative Mises) — see
        # docs/l2/HighOrder-Midside-Subdivision-Design.md.
        tensor_en = {}     # (iname, etype, sp) → [(u_elems, tensor_nd[N,nnode,ncomp]), ...]
        if en_blocks and _ELEM_NODAL_CONST is not None:
            try:
                ten_en_field = field_out.getSubset(position=_ELEM_NODAL_CONST)
                for b in ten_en_field.bulkDataBlocks:
                    if b.instance is None:
                        continue
                    et = (getattr(b, 'elementType', None)
                          or getattr(b, 'baseElementType', None))
                    key = (_canon_inst(b.instance.name), et, _block_sp_num(b))
                    u_e, ten_nd = reshape_element_nodal_block(b)
                    tensor_en.setdefault(key, []).append((u_e, ten_nd))
            except Exception as exc:
                if frame_idx == 0:
                    print("    [inv] getSubset(ELEMENT_NODAL) tensor failed: {}".format(exc))

        tensor_nodal = {}  # (iname, etype, sp) → [(nodeLabels, tensor_nd[N,ncomp]), ...]
        if nodal_blocks and _NODAL_CONST is not None:
            try:
                ten_nodal_field = field_out.getSubset(position=_NODAL_CONST)
                for b in ten_nodal_field.bulkDataBlocks:
                    if b.instance is None:
                        continue
                    et = (getattr(b, 'elementType', None)
                          or getattr(b, 'baseElementType', None))
                    key = (_canon_inst(b.instance.name), et, _block_sp_num(b))
                    lbls = np.array(b.nodeLabels, dtype=np.int32)
                    tensor_nodal.setdefault(key, []).append((lbls, _block_data_2d(b)))
            except Exception as exc:
                if frame_idx == 0:
                    print("    [inv] getSubset(NODAL) tensor failed: {}".format(exc))

        # ── 壳/膜单元集合 (iname, etype): 面内类不变量只在这些块出值, 实体置灰。 ──
        # 用 Abaqus getScalarField(MAX_INPLANE_PRINCIPAL) 命中的块判定, 与 Abaqus
        # 的有效范围逐块一致(实体块对面内不变量本就无数据)。
        shell_keys = set()
        if 'MAX_INPLANE_PRINCIPAL' in _INV_CONSTANTS:
            try:
                _ipf = field_out.getScalarField(
                    invariant=_INV_CONSTANTS['MAX_INPLANE_PRINCIPAL'])
                for b in _ipf.bulkDataBlocks:
                    if b.instance is None:
                        continue
                    et = (getattr(b, 'elementType', None)
                          or getattr(b, 'baseElementType', None))
                    shell_keys.add((_canon_inst(b.instance.name), et))
            except Exception:
                pass

        # ── IP 张量: 仅当存在 numpy-only 不变量(无 getScalarField 常量, 如 ──
        # MAX_INPLANE_PRINCIPAL_ABS)时才取, 供其 IP 位置从张量算。
        tensor_ip = {}   # (iname, etype, sp) → [(u_elems, data_nd[N,n_ip,ncomp]), ...]
        _need_ip_tensor = any(n in _NUMPY_ONLY_INVS for n, _ in active_invs)
        if _need_ip_tensor and _IP_CONST is not None:
            try:
                ten_ip_field = field_out.getSubset(position=_IP_CONST)
                for b in ten_ip_field.bulkDataBlocks:
                    if b.instance is None:
                        continue
                    et = (getattr(b, 'elementType', None)
                          or getattr(b, 'baseElementType', None))
                    key = (_canon_inst(b.instance.name), et, _block_sp_num(b))
                    u_e, _u_i, _sp, data_nd = reshape_ip_block(b)
                    tensor_ip.setdefault(key, []).append((u_e, data_nd))
            except Exception as exc:
                if frame_idx == 0:
                    print("    [inv] getSubset(INTEGRATION_POINT) tensor failed: {}".format(exc))

        for inv_name, _ in active_invs:
            inv_const  = _INV_CONSTANTS.get(inv_name)
            numpy_name = _NUMPY_INV_NAME.get(inv_name, inv_name)
            shell_only = inv_name in _SHELL_ONLY_INVS

            # IP: getScalarField is correct at integration points (no extrapolation,
            # so no overshoot). EN/NODAL use method B below. numpy-only invariants
            # (no getScalarField constant) compute IP from tensor_ip further down.
            ip_scalar_blocks = {}  # (iname, etype, sp) → list of blocks
            if inv_const is not None and inv_name not in _NUMPY_ONLY_INVS:
                try:
                    scalar_field = field_out.getScalarField(invariant=inv_const)
                    for block in scalar_field.bulkDataBlocks:
                        if block.instance is None:
                            continue
                        if _pos_str(block.position) != 'INTEGRATION_POINT':
                            continue
                        et = (getattr(block, 'elementType', None)
                              or getattr(block, 'baseElementType', None))
                        ip_scalar_blocks.setdefault(
                            (_canon_inst(block.instance.name), et, _block_sp_num(block)), []
                        ).append(block)
                except Exception as exc:
                    if frame_idx == 0:
                        print("    [inv] getScalarField({}) failed: {}".format(inv_name, exc))

            # ── Write ELEMENT_NODAL invariant data (method B: from EN tensor) ─
            for (iname, etype, sp_num_key), info in en_blocks.items():
                canon    = info['labels']
                n_enodes = info['n_enodes']
                M_c      = len(canon)

                bd_inv_parts = [inv_field_dirs[inv_name], safe(iname), 'ELEMENT_NODAL']
                if etype:
                    bd_inv_parts.append(safe(etype))
                if sp_num_key is not None:
                    bd_inv_parts.append('sp{}'.format(sp_num_key))
                fr_path = os.path.join(*(bd_inv_parts + ['f{:04d}.npy'.format(frame_idx)]))

                out = np.full((M_c, n_enodes, 1), np.nan, dtype=np.float32)
                # 面内/面外类不变量: 非壳/膜块保持 NaN(实体置灰), 与 Abaqus 一致。
                if not (shell_only and (iname, etype) not in shell_keys):
                    for (u_e, ten_nd) in tensor_en.get((iname, etype, sp_num_key), []):
                        inv_nd = _compute_invariants_numpy(ten_nd, numpy_name, is_strain)  # [N,nnode,1]
                        if inv_nd is None:
                            continue
                        rows  = np.searchsorted(canon, u_e)
                        valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == u_e)
                        out[rows[valid]] = inv_nd[valid]
                npsave(fr_path, out)

            # ── Write INTEGRATION_POINT invariant data (getScalarField) ──────
            for (iname, etype, sp_num_key), info in ip_blocks.items():
                canon     = info['labels']
                ip_labels = info['ip_labels']
                n_ip      = len(ip_labels)
                M_c       = len(canon)

                bd_parts = [inv_field_dirs[inv_name], safe(iname), 'INTEGRATION_POINT']
                if etype:
                    bd_parts.append(safe(etype))
                if sp_num_key is not None:
                    bd_parts.append('sp{}'.format(sp_num_key))
                fr_path = os.path.join(*(bd_parts + ['f{:04d}.npy'.format(frame_idx)]))

                out = np.full((M_c, n_ip, 1), np.nan, dtype=np.float32)

                if inv_name in _NUMPY_ONLY_INVS:
                    # 无 getScalarField 常量: IP 也从张量自算(壳/膜专属, 实体保持 NaN 置灰)
                    if not (shell_only and (iname, etype) not in shell_keys):
                        for (u_e, ip_nd) in tensor_ip.get((iname, etype, sp_num_key), []):
                            inv_ip = _compute_invariants_numpy(ip_nd, numpy_name, is_strain)  # [N,n_ip,1]
                            if inv_ip is None:
                                continue
                            rows  = np.searchsorted(canon, u_e)
                            valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == u_e)
                            out[rows[valid]] = inv_ip[valid]
                    npsave(fr_path, out)
                    continue

                # getScalarField 口径: 实体块对面内不变量本就无数据 → 留 NaN 置灰。
                fr_blocks = ip_scalar_blocks.get((iname, etype, sp_num_key), [])
                if not fr_blocks:
                    npsave(fr_path, out)
                    continue
                for b in fr_blocks:
                    u_e, _, sp, data_nd = reshape_ip_block(b)
                    # data_nd shape: [N_elem, n_ip, 1] (scalar)
                    if data_nd.ndim == 2:
                        data_nd = data_nd[:, :, np.newaxis]
                    rows  = np.searchsorted(canon, u_e)
                    valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == u_e)
                    out[rows[valid]] = data_nd[valid]
                npsave(fr_path, out)

            # ── Write NODAL invariant data (method B: from NODAL tensor) ─────
            for (iname, etype, sp_num_key), info in nodal_blocks.items():
                canon = info['labels']
                M_c   = len(canon)

                bd_inv_parts = [inv_field_dirs[inv_name], safe(iname), 'NODAL']
                if etype:
                    bd_inv_parts.append(safe(etype))
                if sp_num_key is not None:
                    bd_inv_parts.append('sp{}'.format(sp_num_key))
                fr_path = os.path.join(*(bd_inv_parts + ['f{:04d}.npy'.format(frame_idx)]))

                out = np.full((M_c, 1), np.nan, dtype=np.float32)
                # 面内/面外类不变量: 非壳/膜块保持 NaN(实体置灰), 与 Abaqus 一致。
                if not (shell_only and (iname, etype) not in shell_keys):
                    for (lbls, ten2d) in tensor_nodal.get((iname, etype, sp_num_key), []):
                        inv2d = _compute_invariants_numpy(ten2d, numpy_name, is_strain)  # [N,1]
                        if inv2d is None:
                            continue
                        rows  = np.searchsorted(canon, lbls)
                        valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == lbls)
                        out[rows[valid]] = inv2d[valid]
                npsave(fr_path, out)

    # ── Write meta.json for each synthetic invariant field ────────────────────
    for inv_name, _ in active_invs:
        fdir      = inv_field_dirs[inv_name]
        syn_field = '{}_{}'.format(field_name, inv_name)
        blocks    = []
        for (iname, etype, sp_num_key), info in en_blocks.items():
            blocks.append({
                'inst_name':  iname,
                'position':   'ELEMENT_NODAL',
                'elem_type':  etype,
                'sp_num':     sp_num_key,
                'ncomp':      1,
                'n_entities': len(info['labels']),
                'n_enodes':   info['n_enodes'],
            })
        for (iname, etype, sp_num_key), info in ip_blocks.items():
            blocks.append({
                'inst_name':  iname,
                'position':   'INTEGRATION_POINT',
                'elem_type':  etype,
                'sp_num':     sp_num_key,
                'ncomp':      1,
                'n_entities': len(info['labels']),
                'n_ip':       len(info['ip_labels']),
                'n_sp':       0,
            })
        for (iname, etype, sp_num_key), info in nodal_blocks.items():
            blocks.append({
                'inst_name':  iname,
                'position':   'NODAL',
                'elem_type':  etype,
                'sp_num':     sp_num_key,
                'ncomp':      1,
                'n_entities': len(info['labels']),
            })
        jdump(os.path.join(fdir, 'meta.json'), {
            'step_name':   step_name,
            'field_name':  syn_field,
            'components':  [],
            'invariants':  [],
            'has_section': 0,
            'blocks':      blocks,
        })

    print("    [inv] extracted {} invariant(s): {}".format(
        len(active_invs), ', '.join(inv for inv, _ in active_invs)))


def dump_results(odb, raw_dir, meta, field_filter=None, frame_filter=None,
                 field_prefix=None, extract_invariants=False):
    """Write per-frame result arrays to l1_raw/results/<step>__<field>/<inst>/<pos>/[<etype>/]

    field_filter: optional dict {step_name: set_of_field_names}.
      If provided, only those (step, field) combinations are dumped.
      Steps/fields absent from field_filter are silently skipped.
      If None, all fields in all steps are dumped (original behaviour).

    frame_filter: optional dict {step_name: None | 'first_last' | [orig_frame_idx, ...]}.
      Selected frames are re-indexed densely from 0 within the extracted output.

    field_prefix: optional string. If provided, only fields starting with this prefix
      are dumped.

    extract_invariants: if True, also extract validInvariants as synthetic scalar
      fields named {field}_{INV_NAME} (e.g. S_MISES, S_MAX_PRINCIPAL).
      Uses Abaqus getScalarField API for guaranteed accuracy.
    """
    results_dir = os.path.join(raw_dir, 'results')
    mkdirs(results_dir)

    t_results = time.time()
    steps_meta = {}

    for step_num, (step_name, step) in enumerate(odb.steps.items()):
        # If field_filter given, skip steps not in it entirely
        if field_filter is not None and step_name not in field_filter:
            continue

        selected_frames = list(enumerate(step.frames))
        if frame_filter is not None and step_name in frame_filter:
            spec = frame_filter[step_name]
            if spec == 'first_last':
                if len(step.frames) == 0:
                    selected_frames = []
                elif len(step.frames) == 1:
                    selected_frames = [(0, step.frames[0])]
                else:
                    last_idx = len(step.frames) - 1
                    selected_frames = [(0, step.frames[0]), (last_idx, step.frames[last_idx])]
            elif spec is not None:
                selected_frames = []
                for orig_idx in spec:
                    if orig_idx >= len(step.frames):
                        raise ValueError(
                            "Requested frame {} out of range for step '{}' (num_frames={})".format(
                                orig_idx, step_name, len(step.frames)
                            )
                        )
                    selected_frames.append((orig_idx, step.frames[orig_idx]))

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
        num_frames = len(selected_frames)
        t_step = time.time()
        print("  Step '{}' ({} frames) ...".format(step_name, num_frames))

        frames_meta = []
        for fi, (_, frame) in enumerate(selected_frames):
            _domain = getattr(frame, 'domain', None)
            _lc = getattr(frame, 'loadCase', None)
            frames_meta.append({
                'frame_idx':          fi,
                'frame_value':        float(frame.frameValue),
                'description':        frame.description,
                'domain':             str(_domain) if _domain is not None else None,
                'frequency':          getattr(frame, 'frequency', None),
                'mode_number':        getattr(frame, 'mode', None),
                'increment_number':   getattr(frame, 'incrementNumber', None),
                'is_imaginary':       int(getattr(frame, 'isImaginary', False) or False),
                'frame_id':           getattr(frame, 'frameId', None),
                'cyclic_mode_number': getattr(frame, 'cyclicModeNumber', None),
                'load_case':          str(_lc) if _lc is not None else None,
            })

        _tt = getattr(step, 'totalTime', None)
        _tp = getattr(step, 'timePeriod', None)
        steps_meta[step_name] = {
            'step_number': step_num,
            'procedure':   procedure,
            'num_frames':  num_frames,
            'description': getattr(step, 'description', None),
            'nlgeom':      int(bool(getattr(step, 'nlgeom', False))),
            'total_time':  float(_tt) if _tt is not None else None,
            'time_period': float(_tp) if _tp is not None else None,
            'frames':      frames_meta,
        }

        # Collect field names across all frames
        all_field_names = set()
        for _, frame in selected_frames:
            all_field_names.update(frame.fieldOutputs.keys())

        # Apply field_filter within this step
        allowed_fields = field_filter[step_name] if field_filter is not None else None

        # ── Group region-qualified fields by base name ────────────────────────
        # Contact outputs (CPRESS/CSHEAR/COPEN/CSLIP...) are stored per contact
        # pair as e.g. 'CPRESS   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1'.
        # All pairs sharing a base name are dumped as ONE logical field (block
        # labels are unioned), matching the aggregated entry CAE shows.
        # Plain fields form single-member groups — identical to old behaviour.
        field_groups = {}   # base name -> [raw odb field name, ...]
        for raw_name in sorted(all_field_names):
            if (allowed_fields is not None and raw_name not in allowed_fields
                    and split_region_field(raw_name)[0] not in allowed_fields):
                continue
            if field_prefix is not None and not raw_name.startswith(field_prefix):
                continue
            field_groups.setdefault(split_region_field(raw_name)[0], []).append(raw_name)

        for field_name in sorted(field_groups.keys()):
            member_names = field_groups[field_name]
            t_field = time.time()
            if member_names == [field_name]:
                print("    Field '{}' ...".format(field_name))
            else:
                print("    Field '{}' (merged from {} region-qualified field(s)) ...".format(
                    field_name, len(member_names)))

            # First frame (per member) with data → discover structure
            member_first_fields = []
            for m_name in member_names:
                _ff = next(
                    (fr.fieldOutputs[m_name] for _, fr in selected_frames
                     if m_name in fr.fieldOutputs), None)
                if _ff is not None:
                    member_first_fields.append(_ff)
            if not member_first_fields:
                continue

            first_field = member_first_fields[0]
            # Build component list as union across all block componentLabels.
            # first_field.componentLabels returns the intersection across element
            # types, which drops S13/S23 for mixed shell+solid models.
            _comp_union = []
            _comp_set   = set()
            for _mf in member_first_fields:
                for _blk in _mf.bulkDataBlocks:
                    for _c in list(getattr(_blk, 'componentLabels', None) or []):
                        if _c not in _comp_set:
                            _comp_union.append(_c)
                            _comp_set.add(_c)
            components  = _comp_union if _comp_union else list(first_field.componentLabels)
            invariants  = [str(i) for i in first_field.validInvariants]
            # 混合 solid+shell 模型: first_field.validInvariants 返回的是跨单元类型的
            # "交集"(与 componentLabels 同理)。实体不支持面内/面外主应力, 交集会把
            # MAX/MIN_INPLANE_PRINCIPAL、OUTOFPLANE_PRINCIPAL 全部剔除 → 它们永远进不了
            # active_invs, 面内/面外不变量场不会生成(纯壳模型则正常)。这里用 Abaqus
            # getScalarField(MAX_INPLANE_PRINCIPAL) 探测: 若该场存在面内可算的块(=含壳/膜),
            # 就把三个面内/面外不变量并回 invariants。下游对实体块按 _SHELL_ONLY_INVS
            # 置 NaN(前端置灰), 与纯壳模型行为完全一致。
            _shell_invs = ('MAX_INPLANE_PRINCIPAL', 'MIN_INPLANE_PRINCIPAL',
                           'OUTOFPLANE_PRINCIPAL')
            if (any(si not in invariants for si in _shell_invs)
                    and 'MAX_INPLANE_PRINCIPAL' in _INV_CONSTANTS):
                try:
                    _probe = first_field.getScalarField(
                        invariant=_INV_CONSTANTS['MAX_INPLANE_PRINCIPAL'])
                    if any(b.instance is not None for b in _probe.bulkDataBlocks):
                        for si in _shell_invs:
                            if si not in invariants:
                                invariants.append(si)
                        print("    [inv] mixed model: in-plane block detected, adding in/out-of-plane invariants")
                except Exception:
                    pass

            safe_step  = safe(step_name)
            safe_field = safe(field_name)
            field_dir  = os.path.join(results_dir,
                                      '{}_{}'.format(safe_step, safe_field))
            mkdirs(field_dir)

            # Block structure discovered from first frame
            # key = (inst_name, position, elem_type)
            block_struct = {}   # key → info dict (written to meta.json)

            def get_block_dir(inst_name, position, elem_type, sp_num=None):
                parts = [field_dir, safe(inst_name), position]
                if elem_type:
                    parts.append(safe(elem_type))
                if sp_num is not None:
                    parts.append('sp{}'.format(sp_num))
                d = os.path.join(*parts)
                mkdirs(d)
                return d

            # ── Discover structure: group blocks by key, merge labels ────────
            # Abaqus may split one (instance, elem_type, position, sp) group
            # across multiple bulkDataBlocks (parallel chunking).  Collect
            # ALL blocks per key first, then take the union of their labels
            # so the canonical labels.npy is always the full superset.
            key_to_disc_blocks = {}
            for block in [b for _mf in member_first_fields
                          for b in _mf.bulkDataBlocks]:
                if block.instance is None:
                    continue
                inst_name = _canon_inst(block.instance.name)
                position  = _pos_str(block.position)
                elem_type = (getattr(block, 'elementType', None)
                             or getattr(block, 'baseElementType', None))
                if position == 'NODAL':
                    # NODAL data is per-node: elem_type must stay empty so the
                    # HDF5 path is always /NODAL/<inst> (the only path L3
                    # reads). Never fall back to _auto naming here.
                    elem_type = elem_type or ''
                elif elem_type is None:
                    _d = np.array(block.data)
                    _n = len(getattr(block, 'elementLabels',
                             getattr(block, 'nodeLabels', [])))
                    elem_type = '_auto_{}x{}'.format(_n, _d.shape[1] if _d.ndim > 1 else 1)
                sp_num = _block_sp_num(block)
                key    = (inst_name, position, elem_type, sp_num)
                key_to_disc_blocks.setdefault(key, []).append(block)

            for key, key_blocks in key_to_disc_blocks.items():
                inst_name, position, elem_type, sp_num = key
                bd    = get_block_dir(inst_name, position, elem_type, sp_num)
                ncomp = _block_data_2d(key_blocks[0]).shape[1]
                info  = {
                    'inst_name': inst_name,
                    'position':  position,
                    'elem_type': elem_type,
                    'ncomp':     ncomp,
                }

                if position == 'NODAL':
                    all_lbls = [np.array(b.nodeLabels, dtype=np.int32)
                                for b in key_blocks]
                    labels = np.unique(np.concatenate(all_lbls))
                    npsave(os.path.join(bd, 'labels.npy'), labels)
                    info['n_entities'] = len(labels)

                elif position == 'INTEGRATION_POINT':
                    all_u_elems = []
                    all_u_ips   = []
                    blk_sp_num  = None
                    for b in key_blocks:
                        u_e, u_i, sp, _ = reshape_ip_block(b)
                        all_u_elems.append(u_e)
                        all_u_ips.append(u_i)
                        if sp is not None:
                            blk_sp_num = sp
                    u_elems = np.unique(np.concatenate(all_u_elems))
                    u_ips   = np.unique(np.concatenate(all_u_ips))
                    sp_arr  = (np.array([blk_sp_num], dtype=np.int32)
                               if blk_sp_num is not None
                               else np.array([], dtype=np.int32))
                    npsave(os.path.join(bd, 'labels.npy'),    u_elems)
                    npsave(os.path.join(bd, 'ip_labels.npy'), u_ips)
                    npsave(os.path.join(bd, 'sp_labels.npy'), sp_arr)
                    info['n_entities'] = len(u_elems)
                    info['n_ip']       = len(u_ips)
                    info['n_sp']       = 0
                    info['sp_num']     = blk_sp_num

                elif position == 'ELEMENT_NODAL':
                    all_u_elems = []
                    n_enodes    = None
                    for b in key_blocks:
                        u_e, d_nd = reshape_element_nodal_block(b)
                        all_u_elems.append(u_e)
                        if n_enodes is None:
                            n_enodes = d_nd.shape[1]
                    u_elems = np.unique(np.concatenate(all_u_elems))
                    npsave(os.path.join(bd, 'labels.npy'), u_elems)
                    info['n_entities'] = len(u_elems)
                    info['n_enodes']   = n_enodes

                else:
                    all_lbls = [np.array(b.elementLabels, dtype=np.int32)
                                for b in key_blocks]
                    labels = np.unique(np.concatenate(all_lbls))
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
                    # Group by key first so chunks from same elem_type are merged
                    _en_disc_blocks = {}
                    for block in _en_first.bulkDataBlocks:
                        if block.instance is None:
                            continue
                        inst_name = _canon_inst(block.instance.name)
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
                        sp_num = _block_sp_num(block)
                        key = (inst_name, position, elem_type, sp_num)
                        if key in block_struct:
                            continue
                        _en_disc_blocks.setdefault(key, []).append(block)
                    for key, en_blocks in _en_disc_blocks.items():
                        inst_name, position, elem_type, sp_num = key
                        bd    = get_block_dir(inst_name, position, elem_type, sp_num)
                        ncomp = _block_data_2d(en_blocks[0]).shape[1]
                        all_u_elems = []
                        n_enodes    = None
                        for b in en_blocks:
                            u_e, d_nd = reshape_element_nodal_block(b)
                            all_u_elems.append(u_e)
                            if n_enodes is None:
                                n_enodes = d_nd.shape[1]
                        u_elems = np.unique(np.concatenate(all_u_elems))
                        npsave(os.path.join(bd, 'labels.npy'), u_elems)
                        block_struct[key] = {
                            'inst_name':  inst_name,
                            'position':   position,
                            'elem_type':  elem_type,
                            'ncomp':      ncomp,
                            'n_entities': len(u_elems),
                            'n_enodes':   n_enodes,
                        }
                    print("    [EN extrapolation] discovered {} EN block(s)".format(
                        len({k for k in block_struct if k[1] == 'ELEMENT_NODAL'}) - len(_en_insts)))
                except Exception as _e:
                    print("    [warn] getSubset(ELEMENT_NODAL) structure failed: {}".format(_e))
                    _extrapolate_en = False

            # ── Per-frame data ────────────────────────────────────────────────
            # Group blocks by key before writing so that multiple chunks for the
            # same (instance, elem_type, position, sp) are merged into one array,
            # and every frame file has exactly the same shape as labels.npy.
            has_section = 0
            for frame_idx, (_, frame) in enumerate(selected_frames):
                fr_members = [frame.fieldOutputs[m] for m in member_names
                              if m in frame.fieldOutputs]
                if not fr_members:
                    continue
                field_out = fr_members[0]   # for getSubset(EN) extrapolation below

                # Group this frame's blocks by key (across all member fields)
                key_to_fr_blocks = {}
                for block in [b for _fo in fr_members
                              for b in _fo.bulkDataBlocks]:
                    if block.instance is None:
                        continue
                    inst_name = _canon_inst(block.instance.name)
                    position  = _pos_str(block.position)
                    elem_type = (getattr(block, 'elementType', None)
                                 or getattr(block, 'baseElementType', None))
                    if position == 'NODAL':
                        elem_type = elem_type or ''   # keep /NODAL/<inst> path
                    elif elem_type is None:
                        _d = np.array(block.data)
                        _n = len(getattr(block, 'elementLabels',
                                 getattr(block, 'nodeLabels', [])))
                        elem_type = '_auto_{}x{}'.format(_n, _d.shape[1] if _d.ndim > 1 else 1)
                    fr_sp_num = _block_sp_num(block)
                    key       = (inst_name, position, elem_type, fr_sp_num)
                    if key not in block_struct:
                        continue
                    key_to_fr_blocks.setdefault(key, []).append(block)

                # Merge + write once per key
                for key, fr_blocks in key_to_fr_blocks.items():
                    inst_name, position, elem_type, fr_sp_num = key
                    bd      = get_block_dir(inst_name, position, elem_type, fr_sp_num)
                    fr_path = os.path.join(bd, 'f{:04d}.npy'.format(frame_idx))
                    canon   = np.load(os.path.join(bd, 'labels.npy'))
                    M_c     = len(canon)

                    if position == 'NODAL':
                        ncomp_d = _block_data_2d(fr_blocks[0]).shape[1]
                        out = np.zeros((M_c, ncomp_d), dtype=np.float32)
                        for b in fr_blocks:
                            lbls  = np.array(b.nodeLabels, dtype=np.int32)
                            dflat = _block_data_2d(b)
                            rows  = np.searchsorted(canon, lbls)
                            valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == lbls)
                            out[rows[valid]] = dflat[valid]
                        npsave(fr_path, out)

                    elif position == 'INTEGRATION_POINT':
                        n_ip_d = ncomp_d = None
                        pieces = []
                        for b in fr_blocks:
                            u_e, _, sp, data_nd = reshape_ip_block(b)
                            if sp is not None:
                                has_section = 1
                            if n_ip_d is None:
                                n_ip_d, ncomp_d = data_nd.shape[1], data_nd.shape[2]
                            pieces.append((u_e, data_nd))
                        out = np.zeros((M_c, n_ip_d, ncomp_d), dtype=np.float32)
                        for u_e, data_nd in pieces:
                            rows  = np.searchsorted(canon, u_e)
                            valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == u_e)
                            out[rows[valid]] = data_nd[valid]
                        npsave(fr_path, out)

                    elif position == 'ELEMENT_NODAL':
                        n_en_d = ncomp_d = None
                        pieces = []
                        for b in fr_blocks:
                            u_e, data_nd = reshape_element_nodal_block(b)
                            if n_en_d is None:
                                n_en_d, ncomp_d = data_nd.shape[1], data_nd.shape[2]
                            pieces.append((u_e, data_nd))
                        out = np.zeros((M_c, n_en_d, ncomp_d), dtype=np.float32)
                        for u_e, data_nd in pieces:
                            rows  = np.searchsorted(canon, u_e)
                            valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == u_e)
                            out[rows[valid]] = data_nd[valid]
                        npsave(fr_path, out)

                    else:  # WHOLE_ELEMENT
                        ncomp_d = _block_data_2d(fr_blocks[0]).shape[1]
                        out = np.zeros((M_c, ncomp_d), dtype=np.float32)
                        for b in fr_blocks:
                            raw_lbl  = np.array(b.elementLabels, dtype=np.int32)
                            raw_data = _block_data_2d(b)
                            rows  = np.searchsorted(canon, raw_lbl)
                            valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == raw_lbl)
                            out[rows[valid]] = raw_data[valid]
                        npsave(fr_path, out)

                # ── Write extrapolated ELEMENT_NODAL data for this frame ──────
                if _extrapolate_en:
                    try:
                        _en_out = field_out.getSubset(position=_ELEM_NODAL_CONST)
                        key_to_en_fr_blocks = {}
                        for block in _en_out.bulkDataBlocks:
                            if block.instance is None:
                                continue
                            inst_name = _canon_inst(block.instance.name)
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
                            sp_num = _block_sp_num(block)
                            key = (inst_name, position, elem_type, sp_num)
                            if key not in block_struct:
                                continue
                            key_to_en_fr_blocks.setdefault(key, []).append(block)
                        for key, en_blocks in key_to_en_fr_blocks.items():
                            inst_name, position, elem_type, sp_num = key
                            bd      = get_block_dir(inst_name, position, elem_type, sp_num)
                            fr_path = os.path.join(bd, 'f{:04d}.npy'.format(frame_idx))
                            canon   = np.load(os.path.join(bd, 'labels.npy'))
                            M_c     = len(canon)
                            n_en_d = ncomp_d = None
                            pieces  = []
                            for b in en_blocks:
                                u_e, data_nd = reshape_element_nodal_block(b)
                                if n_en_d is None:
                                    n_en_d, ncomp_d = data_nd.shape[1], data_nd.shape[2]
                                pieces.append((u_e, data_nd))
                            out = np.zeros((M_c, n_en_d, ncomp_d), dtype=np.float32)
                            for u_e, data_nd in pieces:
                                rows  = np.searchsorted(canon, u_e)
                                valid = (rows < M_c) & (canon[np.minimum(rows, M_c - 1)] == u_e)
                                out[rows[valid]] = data_nd[valid]
                            npsave(fr_path, out)
                    except Exception as _e:
                        pass  # per-frame EN extrapolation failure is non-fatal

            # Write field meta.json
            jdump(os.path.join(field_dir, 'meta.json'), {
                'step_name':   step_name,
                'field_name':  field_name,
                'components':  components,
                'invariants':  invariants,
                'has_section': has_section,
                # Raw ODB field names merged into this logical field (contact
                # outputs carry the surface pair as a region suffix).
                'source_fields': [
                    {'name': m, 'region': split_region_field(m)[1]}
                    for m in member_names
                ],
                'blocks':      [
                    dict(
                        {'inst_name': k[0], 'position': k[1],
                         'elem_type': k[2], 'sp_num': k[3]},
                        **v
                    )
                    for k, v in block_struct.items()
                ],
            })
            print("      done ({} blocks, {})".format(
                len(block_struct), _fmt_t(time.time() - t_field)))

            # ── Invariant extraction (--invariants full only) ──────────────────
            if extract_invariants and invariants:
                _extract_ip_invariants(
                    step, step_name, field_name, first_field,
                    invariants, results_dir, safe_step, safe_field,
                    block_struct, odb.rootAssembly.instances,
                    selected_frames=selected_frames,
                    odb_field_names=member_names,
                )
        print("  Step '{}' done. ({})".format(step_name, _fmt_t(time.time() - t_step)))

    meta['steps'] = steps_meta
    print("  Results done. ({} total)".format(_fmt_t(time.time() - t_results)))


# ─── ODB open helper ──────────────────────────────────────────────────────────

# Exit code 2 signals job_runner to run 'abaqus -upgrade' and retry.
_ODB_VERSION_EXIT_CODE = 2

def _open_odb(odb_path, read_only=True):
    """
    Open an ODB file, exiting with code 2 on version mismatch.
    job_runner treats exit code 2 as 'upgrade needed' and retries after upgrade.
    """
    try:
        return odbAccess.openOdb(path=odb_path, readOnly=read_only)
    except Exception as exc:
        err = str(exc)
        if 'previous release' in err or 'upgrade' in err.lower():
            print("ODB_VERSION_ERROR: {}".format(err))
            sys.exit(_ODB_VERSION_EXIT_CODE)
        raise


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
        print("=== Worker {} -- step='{}' fields={} ===".format(
            wid, step_name, sorted(field_names)))
        t0 = time.time()
        print("Opening ODB (readOnly) ...")
        odb = _open_odb(odb_path)
        print("  ODB opened. ({})".format(_fmt_t(time.time() - t0)))
        try:
            meta = {}
            dump_results(odb, raw_dir, meta, field_filter=field_filter,
                         extract_invariants=(args.invariants == 'full'))
        except Exception:
            print("\n!!! ERROR (worker {}):".format(wid))
            traceback.print_exc()
            odb.close()
            sys.exit(1)
        odb.close()
        print("=== Worker {} done in {}. ===".format(wid, _fmt_t(time.time() - t_total)))
        return

    # full or preflight: print header and open ODB
    print("=== Layer 1 Phase 1: ODB -> npy ({}) ===".format(mode))
    print("  ODB:       {}".format(odb_path))
    print("  Workspace: {}".format(workspace))

    meta = {}

    t0 = time.time()
    print("Opening ODB ...")
    odb = _open_odb(odb_path)
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
            dump_results(odb, raw_dir, meta,
                         extract_invariants=(args.invariants == 'full'))
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

def _extract_sections_to_dir(odb, out_dir):
    """
    Extract section assignments + element-set labels for all instances.
    Called from _run_extract so we piggyback on the already-open ODB.
    Output layout (under out_dir/sections/<inst_safe>/):
      sections.json          { sectionName: {element_set, material_name, type, thickness} }
      section_names.json     [ sectionName, ... ]  (ordered by sectionAssignments index)
      isets/elem_sets/<safe_eset>.npy   sorted int32 element labels
    Used by l1_pack.py to patch sections into INP-derived geometry H5 files.
    Python 2/3 compatible (no f-strings, no walrus).
    """
    for iname, instance in odb.rootAssembly.instances.items():
        iname = _canon_inst(iname)
        inst_safe = safe(iname)
        d = os.path.join(out_dir, 'sections', inst_safe)

        _sec_region_names = set()
        section_names_list = []
        sections_info = []
        try:
            for sa in instance.sectionAssignments:
                sname = sa.sectionName
                section_names_list.append(sname)
                rname = getattr(getattr(sa, 'region', None), 'name', '') or ''
                if rname:
                    _sec_region_names.add(rname)
                entry = {
                    'section_name':  sname,
                    'element_set':   rname,
                    'material_name': '',
                    'type':          '',
                    'thickness':     None,
                }
                try:
                    sec = odb.sections[sname]
                    entry['type'] = type(sec).__name__
                    entry['material_name'] = _section_material_name(sec)
                    if hasattr(sec, 'thickness'):
                        entry['thickness'] = float(sec.thickness)
                except Exception:
                    pass
                sections_info.append(entry)
        except AttributeError:
            pass

        if not sections_info:
            continue

        mkdirs(d)
        isd = os.path.join(d, 'isets', 'elem_sets')
        for eset_name, es in instance.elementSets.items():
            if eset_name in _sec_region_names:
                mkdirs(isd)
                lbls = np.array(sorted([e.label for e in es.elements]), dtype=np.int32)
                npsave(os.path.join(isd, safe(eset_name) + '.npy'), lbls)

        jdump(os.path.join(d, 'sections.json'), sections_info)
        jdump(os.path.join(d, 'section_names.json'), section_names_list)

    print("  sections extracted.")


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
    odb = _open_odb(odb_path)
    print("  ODB opened. ({})".format(_fmt_t(time.time() - t0)))

    assembly = odb.rootAssembly
    instances_out = {}

    for inst_name, instance in assembly.instances.items():
        inst_name = _canon_inst(inst_name)
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


def _consistency_check_inline(odb, workspace, result_group, check_mode, geom_source='odb'):
    """
    Consistency check merged into extract: counts nodes/elements per instance,
    writes check.json, then validates against manifest.db.
    Calls sys.exit(1) on mismatch so the caller can odb.close() first.
    Python 2/3 compatible (sqlite3 is stdlib in both).
    """
    import sqlite3 as _sqlite3

    out_dir = os.path.join(workspace, 'l1_raw', 'consistency', safe(result_group))
    mkdirs(out_dir)

    assembly = odb.rootAssembly
    instances_out = {}

    for inst_name, instance in assembly.instances.items():
        inst_name = _canon_inst(inst_name)
        node_count = len(instance.nodes)
        elem_count = len(instance.elements)
        inst_entry = {'node_count': node_count, 'elem_count': elem_count}

        if check_mode == 'label-only':
            raw_labels = np.array([n.label for n in instance.nodes], dtype=np.int32)
            nl_path = os.path.join(out_dir, '{}_node_labels.npy'.format(safe(inst_name)))
            npsave(nl_path, np.sort(raw_labels))
            inst_entry['node_labels_path'] = os.path.relpath(nl_path, workspace)

            elem_by_type = {}
            for elem in instance.elements:
                t = elem.type
                if t not in elem_by_type:
                    elem_by_type[t] = []
                elem_by_type[t].append(elem.label)

            elem_labels_paths = {}
            for etype, labels in elem_by_type.items():
                el_path = os.path.join(
                    out_dir, '{}_{}_elem_labels.npy'.format(safe(inst_name), safe(etype)))
                npsave(el_path, np.array(sorted(labels), dtype=np.int32))
                elem_labels_paths[etype] = os.path.relpath(el_path, workspace)
            inst_entry['element_labels'] = elem_labels_paths

        instances_out[inst_name] = inst_entry
        print("  {}: nodes={} elems={}".format(inst_name, node_count, elem_count))

    jdump(os.path.join(out_dir, 'check.json'), {
        'mode': check_mode,
        'warning': (
            'Count-only validation does not verify label or row mapping. '
            'Mismatched labels with matching counts will not be detected.'
        ),
        'instances': instances_out,
    })

    # Validate against manifest.db (node/elem counts must match geometry)
    manifest = os.path.join(workspace, 'manifest.db')
    if not os.path.exists(manifest):
        print("  WARNING: manifest.db not found, skipping count validation")
        return

    try:
        _conn = _sqlite3.connect(manifest, timeout=5.0)
        _rows = _conn.execute(
            "SELECT instance_name, node_count, elem_count FROM instances"
        ).fetchall()
        _conn.close()
    except Exception as _e:
        print("  WARNING: cannot read manifest.db ({}), skipping validation".format(_e))
        return

    if not _rows:
        print("  WARNING: no instances in manifest.db, skipping validation")
        return

    # 安全网：manifest 里的 instance 名规范化为大写后再比，兼容遗留数据。
    # 正常情况下 INP 侧（exporter.canon_instance）和 ODB 侧（_canon_inst）
    # 都已写入大写，这里只是兜底，避免漏网的大小写差异误报 mismatch。
    geom_inst = {_canon_inst(r[0]): (r[1], r[2]) for r in _rows}

    if set(geom_inst) != set(instances_out):
        print("ERROR: instance list mismatch: geom={} odb={}".format(
            sorted(geom_inst), sorted(instances_out)))
        sys.exit(1)

    mismatches = []
    for inst, (g_nodes, g_elems) in geom_inst.items():
        o = instances_out[inst]
        if g_nodes is not None and g_nodes != o['node_count']:
            mismatches.append(
                "{}: node_count geom={} odb={}".format(inst, g_nodes, o['node_count']))
        if g_elems is not None and g_elems != o['elem_count']:
            mismatches.append(
                "{}: elem_count geom={} odb={}".format(inst, g_elems, o['elem_count']))

    if mismatches:
        if geom_source == 'inp':
            # INP parser and ODB may count nodes/elements differently (e.g. connector
            # or special elements not parsed from INP). Warn but do not block.
            print("WARNING: consistency check mismatch (INP vs ODB, non-fatal):")
            for m in mismatches:
                print("  " + m)
        else:
            print("ERROR: consistency check failed:")
            for m in mismatches:
                print("  " + m)
            print("ODB_CONSISTENCY_FAIL: " + " | ".join(mismatches))
            sys.exit(1)
    else:
        print("  consistency check passed ({}).".format(check_mode))


def _run_extract(args, odb_path, workspace):
    """
    --mode extract: consistency check + 结果提取 + sections 提取，一次 ODB 打开完成。
    输出: <workspace>/l1_raw/rg_<result_group>/ （供 l1_pack.py --result-group 读取）
          <workspace>/l1_raw/consistency/<result_group>/check.json
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
    odb = _open_odb(odb_path)
    print("  ODB opened. ({})".format(_fmt_t(time.time() - t0)))

    # Inline consistency check (replaces separate --mode consistency-check call)
    try:
        _consistency_check_inline(odb, workspace, result_group,
                                   args.check_mode,
                                   geom_source=getattr(args, 'geom_source', 'odb'))
    except SystemExit:
        odb.close()
        raise
    except Exception:
        print("ERROR during consistency check:")
        traceback.print_exc()
        odb.close()
        sys.exit(1)

    try:
        step_names = _parse_csv_names(args.steps)
        frame_spec = _parse_frame_spec(args.frames)
    except ValueError as exc:
        print("ERROR: {}".format(exc))
        odb.close()
        sys.exit(1)

    field_filter = None
    frame_filter = None

    if step_names is not None:
        missing = [name for name in step_names if name not in odb.steps]
        if missing:
            print("ERROR: step(s) not found: {}".format(', '.join(missing)))
            odb.close()
            sys.exit(1)
        field_filter = dict((name, None) for name in step_names)
        frame_filter = dict((name, frame_spec) for name in step_names)
    elif frame_spec is not None:
        field_filter = dict((name, None) for name in odb.steps.keys())
        frame_filter = dict((name, frame_spec) for name in odb.steps.keys())

    meta = {}
    try:
        dump_results(odb, raw_dir, meta,
                     field_filter=field_filter,
                     frame_filter=frame_filter,
                     field_prefix=args.field_prefix,
                     extract_invariants=(args.invariants == 'full'))
    except ValueError as exc:
        print("ERROR: {}".format(exc))
        odb.close()
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        odb.close()
        sys.exit(1)

    # Piggyback: extract section assignments while ODB is open.
    # l1_pack will use this to patch sections into INP-derived geometry H5 files.
    try:
        _extract_sections_to_dir(odb, raw_dir)
    except Exception:
        print("  WARNING: sections extraction failed (non-fatal):")
        traceback.print_exc()

    odb.close()
    jdump(os.path.join(raw_dir, 'dump_meta.json'), meta)
    print("=== extract done in {}. Run l1_pack.py --result-group next. ===".format(
        _fmt_t(time.time() - t0)))


if __name__ == '__main__':
    main()
