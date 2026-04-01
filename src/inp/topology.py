"""
Topology Hub: element face definitions for Surface resolution.

Face node indices are 0-based and follow Abaqus node ordering within the element.
This table is used by the Resolver to expand Surface entries into
(elem_label, face_id, local_node_indices) tuples.

Reference: Abaqus Analysis User's Guide, Section "Element library"
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple


# face_id -> list of 0-based node indices within the element
# (index 0 = first node in the element's connectivity array)
_FACE_TABLE: Dict[str, Dict[str, List[int]]] = {

    # ---- Tetrahedra ----
    "TET4": {            # C3D4
        "S1": [0, 1, 2],
        "S2": [0, 3, 1],
        "S3": [1, 3, 2],
        "S4": [2, 3, 0],
    },
    "TET10": {           # C3D10
        "S1": [0, 1, 2, 4, 5, 6],
        "S2": [0, 3, 1, 7, 8, 4],
        "S3": [1, 3, 2, 8, 9, 5],
        "S4": [2, 3, 0, 9, 7, 6],
    },

    # ---- Wedge / Prism ----
    "WEDGE6": {          # C3D6
        "S1": [0, 1, 2],
        "S2": [3, 4, 5],
        "S3": [0, 1, 4, 3],
        "S4": [1, 2, 5, 4],
        "S5": [2, 0, 3, 5],
    },
    "WEDGE15": {         # C3D15
        "S1": [0, 1, 2, 6, 7, 8],
        "S2": [3, 4, 5, 9, 10, 11],
        "S3": [0, 1, 4, 3, 6, 13, 9, 12],
        "S4": [1, 2, 5, 4, 7, 14, 10, 13],
        "S5": [2, 0, 3, 5, 8, 12, 11, 14],
    },

    # ---- Hexahedra ----
    "HEX8": {            # C3D8, C3D8R, C3D8I, C3D8H
        "S1": [0, 1, 2, 3],
        "S2": [4, 5, 6, 7],
        "S3": [0, 1, 5, 4],
        "S4": [1, 2, 6, 5],
        "S5": [2, 3, 7, 6],
        "S6": [3, 0, 4, 7],
    },
    "HEX20": {           # C3D20, C3D20R
        "S1": [0, 1, 2, 3, 8, 9, 10, 11],
        "S2": [4, 5, 6, 7, 12, 13, 14, 15],
        "S3": [0, 1, 5, 4, 8, 17, 12, 16],
        "S4": [1, 2, 6, 5, 9, 18, 13, 17],
        "S5": [2, 3, 7, 6, 10, 19, 14, 18],
        "S6": [3, 0, 4, 7, 11, 16, 15, 19],
    },

    # ---- Plane / Axisymmetric (2D, face = edge) ----
    "TRI3": {            # CPS3, CPE3, CAX3, S3, S3R, R3D3, M3D3
        "S1":   [0, 1],
        "S2":   [1, 2],
        "S3":   [2, 0],
        "SPOS": [0, 1, 2],
        "SNEG": [0, 2, 1],
    },
    "TRI6": {            # CPS6, CPE6, CAX6
        "S1": [0, 1, 3],
        "S2": [1, 2, 4],
        "S3": [2, 0, 5],
        "SPOS": [0, 1, 2, 3, 4, 5],
        "SNEG": [0, 2, 1, 5, 4, 3],
    },
    "QUAD4": {           # CPS4, CPE4, CAX4, S4, S4R, R3D4, M3D4
        "S1":   [0, 1],
        "S2":   [1, 2],
        "S3":   [2, 3],
        "S4":   [3, 0],
        "SPOS": [0, 1, 2, 3],
        "SNEG": [0, 3, 2, 1],
    },
    "QUAD8": {           # CPS8, CPE8, CAX8, S8R
        "S1": [0, 1, 4],
        "S2": [1, 2, 5],
        "S3": [2, 3, 6],
        "S4": [3, 0, 7],
        "SPOS": [0, 1, 2, 3, 4, 5, 6, 7],
        "SNEG": [0, 3, 2, 1, 7, 6, 5, 4],
    },

    # ---- Line elements (beam / truss) ----
    "LINE2": {           # B31, T3D2, PIPE31, CONN3D2
        "END1": [0],
        "END2": [1],
    },
    "LINE3": {           # B32, T3D3, PIPE32
        "END1": [0],
        "END2": [2],   # mid-node is index 1, end is index 2
    },

    # ---- Point elements ----
    "POINT": {           # MASS, ROTARYI, SPRING1
        "END1": [0],
    },
}

# Aliases: different factory_type strings that share the same face table
_ALIASES: Dict[str, str] = {
    "COH3D6": "WEDGE6",
    "COH3D8": "HEX8",
    "COH2D4": "QUAD4",
    "SC6R":   "WEDGE6",
    "SC8R":   "HEX8",
}


def get_face_nodes(factory_type: str, face_id: str) -> Optional[List[int]]:
    """
    Return the 0-based local node indices for the given element type and face id.
    Returns None if the type or face_id is not in the table.
    """
    key = _ALIASES.get(factory_type, factory_type)
    table = _FACE_TABLE.get(key)
    if table is None:
        return None
    return table.get(face_id.upper())


def list_faces(factory_type: str) -> List[str]:
    """Return the valid face ids for the given element type."""
    key = _ALIASES.get(factory_type, factory_type)
    table = _FACE_TABLE.get(key)
    if table is None:
        return []
    return list(table.keys())
