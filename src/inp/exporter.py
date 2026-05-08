"""
Layer 4: INP → L1-compatible HDF5 Exporter

Converts an InpModel (fully resolved) into the same L1 directory layout that
abaqus_dump.py + l1_pack.py produce, so that L2 ingest.py can process INP
files without modification.

Output layout (under <workspace>/):
    l1/
        assembly.h5                  instances/<inst_name>/transform  [4,4] float64
        geometry/<inst_name>.h5      nodes/labels [N] int32
                                     nodes/coords [N,3] float64
                                     elements/<etype_str>/face_node_conn [Mf,w] int32
                                     elements/<etype_str>/face_elem_idx  [Mf]   int32
                                     elements/<etype_str>/face_seq       [Mf]   uint8
    manifest.db                      instances table (minimal subset)

Usage:
    from src.inp import parse_inp
    from src.inp.exporter import export_l1

    model = parse_inp("model.inp")
    export_l1(model, workspace="/data/my_model")

Then run:
    python src/l2/ingest.py --workspace /data/my_model
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional, Tuple

import h5py
import numpy as np

from .model import InpModel, Instance, Part
from src.l1.manifest_schema import MANIFEST_SCHEMA


def _safe(name: str) -> str:
    """Sanitize a name for use as an HDF5 key (mirrors l1_pack.safe())."""
    return name.replace("/", "__").replace("\\", "__").replace(" ", "_")

# ---------------------------------------------------------------------------
# Face definitions  (mirrors FACE_DEFS in abaqus_dump.py exactly)
#
# Key  = ELEM_TYPE_CODE integer (same as L2's ELEM_TYPE_CODE dict)
# Value = list of faces; each face = list of 0-based *corner node row* indices
#
# "row index" means: index into the element's corner-node list (not a label).
# For high-order types (C3D10, C3D15, C3D20) only the corner nodes are used;
# mid-nodes are never included here.
# ---------------------------------------------------------------------------

_ELEM_TYPE_CODE: Dict[str, int] = {
    # shells / membranes mapped to S3 / S4 codes
    "S3": 0,  "S3R": 0, "STRI3": 0,
    "S4": 1,  "S4R": 1, "S4R5": 1,
    # solids
    "C3D4": 2,  "C3D4H": 2,
    "C3D6": 3,  "C3D6H": 3,
    "C3D8": 4,  "C3D8R": 4, "C3D8I": 4, "C3D8H": 4, "C3D8RH": 4,
    "C3D10": 5, "C3D10M": 5, "C3D10H": 5, "C3D10MH": 5,
    "C3D15": 6, "C3D15H": 6,
    "C3D20": 7, "C3D20R": 7, "C3D20H": 7, "C3D20RH": 7,
    # plane / axisymmetric treated as shell faces
    "CPS3": 0,  "CPS4": 1,  "CPS4R": 1,
    "CPE3": 0,  "CPE4": 1,  "CPE4R": 1,
    "CAX3": 0,  "CAX4": 1,  "CAX4R": 1,
    "M3D3": 0,  "M3D4": 1,  "M3D4R": 1,
}

# Number of *corner* nodes per type code (used to slice connectivity)
_CORNER_COUNTS: Dict[int, int] = {
    0: 3,   # S3 / TRI3
    1: 4,   # S4 / QUAD4
    2: 4,   # C3D4 / TET4
    3: 6,   # C3D6 / WEDGE6
    4: 8,   # C3D8 / HEX8
    5: 4,   # C3D10 → 4 corner nodes
    6: 6,   # C3D15 → 6 corner nodes
    7: 8,   # C3D20 → 8 corner nodes
}

# Face connectivity (0-based local corner-node index lists, 1-based face_seq)
_FACE_DEFS: Dict[int, List[List[int]]] = {
    0: [[0, 1, 2]],                               # S3 / TRI3: 1 face
    1: [[0, 1, 2, 3]],                            # S4 / QUAD4: 1 face
    2: [[0, 1, 2], [0, 3, 1], [1, 3, 2],
        [2, 3, 0]],                               # C3D4
    3: [[0, 1, 2], [3, 5, 4],                    # C3D6
        [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5]],
    4: [[0, 1, 2, 3], [4, 5, 6, 7],             # C3D8
        [0, 1, 5, 4], [1, 2, 6, 5],
        [2, 3, 7, 6], [3, 0, 4, 7]],
    5: [[0, 1, 2], [0, 3, 1], [1, 3, 2],
        [2, 3, 0]],                               # C3D10 (same corner topology as C3D4)
    6: [[0, 1, 2], [3, 5, 4],                    # C3D15 (same corner topology as C3D6)
        [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5]],
    7: [[0, 1, 2, 3], [4, 5, 6, 7],             # C3D20 (same corner topology as C3D8)
        [0, 1, 5, 4], [1, 2, 6, 5],
        [2, 3, 7, 6], [3, 0, 4, 7]],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_l1(model: InpModel, workspace: str) -> None:
    """
    Write L1-compatible HDF5 files + manifest.db for *model* under *workspace*.

    After this call, run:
        python src/l2/ingest.py --workspace <workspace>
    """
    os.makedirs(os.path.join(workspace, "l1", "geometry"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "l1", "sets"), exist_ok=True)

    # --- assembly.h5 ---
    _write_assembly_h5(model, workspace)

    # --- geometry/<inst>.h5 + sets.h5 + manifest.db ---
    db_conn = _init_manifest(workspace)
    try:
        if model.assembly is not None:
            inst_iter = model.assembly.instances.items()
        elif "PART-1-1" in model.parts:
            # Flat-format INP: synthesize a single instance with identity transform
            _synthetic = Instance(name="PART-1-1", part_name="PART-1-1")
            inst_iter = [("PART-1-1", _synthetic)]
        else:
            inst_iter = []

        for inst_name, inst in inst_iter:
            part = model.parts.get(inst.part_name)
            if part is None:
                continue
            geom_rel = os.path.join("l1", "geometry",
                                    _safe(inst_name) + ".h5")
            geom_abs = os.path.join(workspace, geom_rel)
            bbox_min, bbox_max = _write_geometry_h5(part, geom_abs)
            _insert_instance(db_conn, inst_name, inst.part_name,
                             geom_rel, part, bbox_min, bbox_max)
            _write_sets(part, inst_name, workspace, db_conn)
        db_conn.commit()
    finally:
        db_conn.close()


# ---------------------------------------------------------------------------
# assembly.h5
# ---------------------------------------------------------------------------

def _write_assembly_h5(model: InpModel, workspace: str) -> None:
    h5_path = os.path.join(workspace, "l1", "assembly.h5")
    with h5py.File(h5_path, "w") as f:
        asm = model.assembly
        if asm is not None:
            for inst_name, inst in asm.instances.items():
                grp = f.require_group("instances/{}".format(inst_name))
                mat = _build_transform_matrix(inst)
                grp.create_dataset("transform", data=mat)
        elif "PART-1-1" in model.parts:
            # Flat-format INP: no Assembly — write identity transform for the synthetic instance
            grp = f.require_group("instances/PART-1-1")
            grp.create_dataset("transform", data=np.eye(4, dtype=np.float64))


def _build_transform_matrix(inst) -> np.ndarray:
    """
    Build a 4×4 homogeneous transform matrix from Instance.translation + .rotation.

    Abaqus instance placement:
      1. Apply rotation  R  (axis-angle, axis given as two points)
      2. Apply translation T

    Combined: P_global = R @ P_local + T
    As a 4×4 matrix:
        | R  T |
        | 0  1 |
    """
    mat = np.eye(4, dtype=np.float64)

    tx, ty, tz = inst.translation
    T = np.array([tx, ty, tz], dtype=np.float64)

    # Abaqus instance positioning order:
    #   1. Translate part by T
    #   2. Rotate about the axis defined by (center, axis_point), angle_deg
    #
    # Combined: P_global = R @ (P_local + T - center) + center
    #                     = R @ P_local  +  R @ (T - center) + center
    #
    # 4×4 matrix:
    #   top-left 3×3  = R
    #   top-right 3×1 = R @ (T - center) + center
    #
    # When there is no rotation, R = I, so offset = T. ✓
    # When center == T (typical Bolt pattern), offset = R@0 + T = T. ✓

    rot = inst.rotation
    if rot is not None:
        cx, cy, cz = rot.center
        ax, ay, az = rot.axis
        center = np.array([cx, cy, cz], dtype=np.float64)

        dx, dy, dz = ax - cx, ay - cy, az - cz
        length = (dx*dx + dy*dy + dz*dz) ** 0.5
        if length > 1e-12:
            dx, dy, dz = dx/length, dy/length, dz/length

        angle_rad = rot.angle_deg * (3.141592653589793 / 180.0)
        c, s = np.cos(angle_rad), np.sin(angle_rad)
        t = 1.0 - c
        R = np.array([
            [t*dx*dx + c,    t*dx*dy - s*dz, t*dx*dz + s*dy],
            [t*dx*dy + s*dz, t*dy*dy + c,    t*dy*dz - s*dx],
            [t*dx*dz - s*dy, t*dy*dz + s*dx, t*dz*dz + c   ],
        ], dtype=np.float64)

        mat[:3, :3] = R
        mat[:3,  3] = R @ (T - center) + center
    else:
        mat[:3, 3] = T

    return mat


# ---------------------------------------------------------------------------
# geometry/<inst>.h5
# ---------------------------------------------------------------------------

def _write_geometry_h5(
    part: Part, h5_path: str
) -> Tuple[List[float], List[float]]:
    """
    Write nodes and face-expanded element arrays.  Returns (bbox_min, bbox_max).
    """
    # ---- Build sorted node arrays ----------------------------------------
    if not part.nodes:
        # Empty part — write minimal valid file
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("nodes/labels", data=np.array([], dtype=np.int32))
            f.create_dataset("nodes/coords",
                             data=np.zeros((0, 3), dtype=np.float64))
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]

    sorted_labels = sorted(part.nodes.keys())
    label_to_row: Dict[int, int] = {lbl: i for i, lbl in enumerate(sorted_labels)}

    labels_arr = np.array(sorted_labels, dtype=np.int32)
    coords_arr = np.array(
        [[n.x, n.y, n.z] for n in (part.nodes[lbl] for lbl in sorted_labels)],
        dtype=np.float64,
    )

    bbox_min = coords_arr.min(axis=0).tolist()
    bbox_max = coords_arr.max(axis=0).tolist()

    # ---- Group elements by Abaqus type string ----------------------------
    # Each group: list of (elem_label, [node_labels...])
    type_groups: Dict[str, List[Tuple[int, List[int]]]] = {}
    for elem_label, elem in part.elements.items():
        atype = elem.abaqus_type.upper()
        type_groups.setdefault(atype, []).append((elem_label, elem.node_labels))

    # ---- Build per-element attribute maps from sections ------------------
    # Section: elset_name → material_name, section_type
    mat_map: Dict[int, str] = {}   # elem_label → material name
    sec_map: Dict[int, str] = {}   # elem_label → section type (short)
    for sec in part.sections:
        elset = part.elsets.get(sec.elset_name)
        if elset is None:
            continue
        mat  = sec.material_name or ""
        # Normalize "SOLID SECTION" → "SOLID" etc.
        stype = sec.section_type.upper().replace(" SECTION", "").strip()
        for lbl in elset.elem_labels:
            mat_map[lbl] = mat
            sec_map[lbl] = stype

    # ---- Write HDF5 --------------------------------------------------
    with h5py.File(h5_path, "w") as f:
        f.create_dataset("nodes/labels", data=labels_arr)
        f.create_dataset("nodes/coords",  data=coords_arr)

        for etype_str, elems in type_groups.items():
            etype_code = _ELEM_TYPE_CODE.get(etype_str, -1)
            if etype_code < 0:
                continue
            face_defs  = _FACE_DEFS.get(etype_code, [])
            corner_cnt = _CORNER_COUNTS.get(etype_code, len(face_defs[0]) if face_defs else 0)
            if not face_defs:
                continue

            elem_labels_arr, conn_arr, fni_rows, fei_rows, fsq_rows = _compute_elem_and_faces(
                elems, label_to_row, face_defs, corner_cnt
            )

            grp = f.require_group("elements/{}".format(etype_str))
            # Per-element arrays (L3 pick reads these)
            grp.create_dataset("labels", data=elem_labels_arr)
            grp.create_dataset("conn",   data=conn_arr)
            # Per-element color-code attributes (L3 color-code endpoint reads these)
            mat_arr = np.array(
                [mat_map.get(int(lbl), "").encode("ascii")[:63] for lbl in elem_labels_arr],
                dtype="S64",
            )
            sec_arr = np.array(
                [sec_map.get(int(lbl), "").encode("ascii")[:15] for lbl in elem_labels_arr],
                dtype="S16",
            )
            grp.create_dataset("material_name", data=mat_arr)
            grp.create_dataset("section_type",  data=sec_arr)
            # Per-face arrays (L2 ingest reads these)
            if fni_rows.shape[0] > 0:
                grp.create_dataset("face_node_conn", data=fni_rows)
                grp.create_dataset("face_elem_idx",  data=fei_rows)
                grp.create_dataset("face_seq",       data=fsq_rows)

    return bbox_min, bbox_max


def _compute_elem_and_faces(
    elems: List[Tuple[int, List[int]]],
    label_to_row: Dict[int, int],
    face_defs: List[List[int]],
    corner_cnt: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Build both per-element and per-face arrays from a list of elements.

    Returns:
        elem_labels     [M]              int32   element label per row
        conn            [M, corner_cnt]  int32   corner node row indices per element
        face_node_conn  [Mf, max_width]  int32   node row indices (-1=unused)
        face_elem_idx   [Mf]             int32   element row index (0-based)
        face_seq        [Mf]             uint8   face number (1-based)

    L3 reads elem_labels + conn for pick queries.
    L2 reads face_node_conn + face_elem_idx + face_seq for surface extraction.
    """
    M = len(elems)
    max_width = max(len(fd) for fd in face_defs)
    n_faces_per_elem = len(face_defs)

    elem_labels = np.empty(M, dtype=np.int32)
    conn        = np.full((M, corner_cnt), -1, dtype=np.int32)
    fnc = np.full((M * n_faces_per_elem, max_width), -1, dtype=np.int32)
    fei = np.empty(M * n_faces_per_elem, dtype=np.int32)
    fsq = np.empty(M * n_faces_per_elem, dtype=np.uint8)

    out = 0
    for elem_row, (elem_label, node_labels) in enumerate(elems):
        elem_labels[elem_row] = elem_label

        # Corner nodes only (drop mid-nodes for high-order types)
        corners = node_labels[:corner_cnt]
        for col, nl in enumerate(corners):
            conn[elem_row, col] = label_to_row.get(nl, -1)

        for face_seq_0, face_def in enumerate(face_defs):
            for col, local_idx in enumerate(face_def):
                if local_idx < len(corners):
                    fnc[out, col] = label_to_row.get(corners[local_idx], -1)
            fei[out] = elem_row
            fsq[out] = face_seq_0 + 1   # 1-based, matching abaqus_dump.py
            out += 1

    return elem_labels, conn, fnc[:out], fei[:out], fsq[:out]


# ---------------------------------------------------------------------------
# sets.h5  (element sets for color-code endpoint)
# ---------------------------------------------------------------------------

def _write_sets(
    part: Part, inst_name: str, workspace: str, db_conn: sqlite3.Connection
) -> None:
    """
    Write part-level Elsets to l1/sets/sets.h5 and manifest.db element_sets.

    HDF5 layout:  element_sets/{inst_name}/{set_name}  →  sorted int32 labels
    """
    if not part.elsets:
        return

    sets_h5 = os.path.join(workspace, "l1", "sets", "sets.h5")
    sets_rel = os.path.join("l1", "sets", "sets.h5")

    with h5py.File(sets_h5, "a") as f:
        for set_name, elset in part.elsets.items():
            if not elset.elem_labels:
                continue
            safe_name = _safe(set_name)
            key = "element_sets/{}/{}".format(inst_name, safe_name)
            if key in f:
                del f[key]
            arr = np.array(sorted(elset.elem_labels), dtype=np.int32)
            f.create_dataset(key, data=arr)

            db_conn.execute(
                "INSERT OR REPLACE INTO element_sets VALUES (?,?,?,?,?)",
                (safe_name, "PART", inst_name, sets_rel, len(elset.elem_labels)),
            )


# ---------------------------------------------------------------------------
# manifest.db  (minimal schema — only `instances` table needed by L2)
# ---------------------------------------------------------------------------

def _init_manifest(workspace: str) -> sqlite3.Connection:
    db_path = os.path.join(workspace, "manifest.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(MANIFEST_SCHEMA)
    conn.commit()
    return conn


def _insert_instance(
    conn: sqlite3.Connection,
    inst_name: str,
    part_name: str,
    geom_rel: str,
    part: Part,
    bbox_min: List[float],
    bbox_max: List[float],
) -> None:
    import json as _json
    node_count = len(part.nodes)
    elem_count = len(part.elements)
    conn.execute(
        "INSERT OR REPLACE INTO instances VALUES (?,?,?,?,?,?,?,?)",
        (
            inst_name,
            part_name,
            geom_rel,
            None,       # highorder_path — not exported
            node_count,
            elem_count,
            _json.dumps(bbox_min),
            _json.dumps(bbox_max),
        ),
    )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    """Sanitize a name for use as a filename component."""
    return name.replace("/", "__").replace("\\", "__").replace(" ", "_")
