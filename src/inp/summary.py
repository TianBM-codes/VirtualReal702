"""
Compute a model statistics summary from a parsed InpModel.

Returns a flat dict covering all fields shown in the "模型概览" UI.
Fields that cannot be derived from an INP file are set to "--".
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import InpModel

# ── Element type buckets (by Abaqus type string) ──────────────────────────────
# Each set maps to one UI row in the element-type distribution table.

_BEAM_2NODE = frozenset({
    "B31", "B31OS", "PIPE31",
})
_BEAM_3NODE = frozenset({
    "B32", "B32OS", "PIPE32",
})
_SHELL_TRI3 = frozenset({
    "S3", "S3R", "STRI3", "R3D3",
    "M3D3", "CPS3", "CPE3", "CAX3",
})
_SHELL_TRI6 = frozenset({
    "S6", "S6R",
    "CPS6", "CPE6", "CAX6",
})
_SHELL_QUAD4 = frozenset({
    "S4", "S4R", "S4R5", "R3D4",
    "M3D4", "M3D4R",
    "CPS4", "CPS4R", "CPE4", "CPE4R", "CAX4", "CAX4R",
    "COH2D4",
})
_SHELL_QUAD8 = frozenset({
    "S8R", "S8R5",
    "CPS8", "CPS8R", "CPE8", "CPE8R", "CAX8", "CAX8R",
})
_TET4 = frozenset({"C3D4", "C3D4H"})
_TET10 = frozenset({"C3D10", "C3D10H", "C3D10M", "C3D10MH"})
_HEX8 = frozenset({"C3D8", "C3D8R", "C3D8I", "C3D8H", "C3D8RH", "SC8R", "COH3D8"})
_HEX20 = frozenset({"C3D20", "C3D20R", "C3D20H", "C3D20RH"})
_WEDGE6 = frozenset({"C3D6", "C3D6H", "SC6R", "COH3D6"})
_WEDGE15 = frozenset({"C3D15", "C3D15H"})
_POINT = frozenset({"MASS", "SPRING1", "ROTARYI"})

# Types that carry rotational DOF
_ROTATION_DOF_TYPES = _BEAM_2NODE | _BEAM_3NODE | _SHELL_TRI3 | _SHELL_TRI6 | _SHELL_QUAD4 | _SHELL_QUAD8 | frozenset({"R3D3", "R3D4"})

NA = "--"   # placeholder for fields not derivable from INP


def compute_inp_summary(model: "InpModel") -> dict:
    """
    Compute model statistics from a fully-parsed InpModel.

    Bbox is in part-local coordinates (accurate for flat-format files;
    approximate for Part/Assembly files that have instance transforms).
    """
    node_count = 0
    elem_count = 0
    section_count = 0
    nset_count = 0
    elset_count = 0
    has_rotation = False

    xmin = ymin = zmin = math.inf
    xmax = ymax = zmax = -math.inf

    # Element type buckets
    free_point = 0
    beam_2node = 0
    beam_3node = 0
    shell_tri3 = 0
    shell_tri6 = 0
    shell_quad4 = 0
    shell_quad8 = 0
    tet4 = 0
    tet10 = 0
    hex8 = 0
    hex20 = 0
    wedge6 = 0
    wedge15 = 0

    for part in model.parts.values():
        node_count += len(part.nodes)
        elem_count += len(part.elements)
        section_count += len(part.sections)
        nset_count += len(part.nsets)
        elset_count += len(part.elsets)

        for node in part.nodes.values():
            if node.x < xmin: xmin = node.x
            if node.x > xmax: xmax = node.x
            if node.y < ymin: ymin = node.y
            if node.y > ymax: ymax = node.y
            if node.z < zmin: zmin = node.z
            if node.z > zmax: zmax = node.z

        for elem in part.elements.values():
            at = elem.abaqus_type.upper()
            if at in _ROTATION_DOF_TYPES:
                has_rotation = True
            if at in _POINT:        free_point  += 1
            elif at in _BEAM_2NODE: beam_2node  += 1
            elif at in _BEAM_3NODE: beam_3node  += 1
            elif at in _SHELL_TRI3: shell_tri3  += 1
            elif at in _SHELL_TRI6: shell_tri6  += 1
            elif at in _SHELL_QUAD4:shell_quad4 += 1
            elif at in _SHELL_QUAD8:shell_quad8 += 1
            elif at in _TET4:       tet4        += 1
            elif at in _TET10:      tet10       += 1
            elif at in _HEX8:       hex8        += 1
            elif at in _HEX20:      hex20       += 1
            elif at in _WEDGE6:     wedge6      += 1
            elif at in _WEDGE15:    wedge15     += 1

    if model.assembly:
        nset_count += len(model.assembly.nsets)
        elset_count += len(model.assembly.elsets)

    if node_count > 0:
        dx = xmax - xmin
        dy = ymax - ymin
        dz = zmax - zmin
        model_size = round(max(dx, dy, dz), 3)
        dimension = 3 if (zmax - zmin) > 1e-9 else 2
    else:
        xmin = xmax = ymin = ymax = zmin = zmax = 0.0
        model_size = 0.0
        dimension = 0

    dof_per_node = 6 if has_rotation else 3
    dof_count = node_count * dof_per_node

    load_case_count = len(model.steps)
    cload_count = sum(len(s.cloads) for s in model.steps)
    bc_count = sum(len(s.boundary_conditions) for s in model.steps)
    dload_count = sum(len(s.dloads) + len(s.dsloads) for s in model.steps)

    return {
        # ── 基本尺寸 ──
        "model_size":    model_size,
        "xmin": xmin, "xmax": xmax,
        "ymin": ymin, "ymax": ymax,
        "zmin": zmin, "zmax": zmax,
        "dimension":     dimension,

        # ── 数量统计 ──
        "node_count":    node_count,
        "element_count": elem_count,
        "material_count": len(model.materials),
        "section_count": section_count,
        "set_count":     nset_count + elset_count,
        "orientation_count": len(model.orientations),

        # ── 自由度 ──
        "dof_count":     dof_count,
        "dof_per_node":  dof_per_node,
        "node_dofs": {
            "UX": True, "UY": True, "UZ": True,
            "RX": has_rotation, "RY": has_rotation, "RZ": has_rotation,
        },

        # ── 载荷/边界条件 ──
        "load_case_count": load_case_count,
        "cload_count":   cload_count,
        "bc_count":      bc_count,
        "dload_count":   dload_count,

        # ── INP 无法获得的字段（结果文件才有）──
        "mass_matrix_count":      NA,
        "stiffness_matrix_count": NA,
        "mode_count":             NA,
        "work_deformation":       NA,
        "frf_count":              NA,
        "master_dof":             NA,

        # ── 单元类型分布 ──
        "element_type_dist": {
            "free_point":  free_point,
            "beam_2node":  beam_2node,
            "beam_3node":  beam_3node,
            "shell_tri3":  shell_tri3,
            "shell_tri6":  shell_tri6,
            "shell_quad4": shell_quad4,
            "shell_quad8": shell_quad8,
            "tet4":        tet4,
            "tet10":       tet10,
            "hex8":        hex8,
            "hex20":       hex20,
            "wedge6":      wedge6,
            "wedge15":     wedge15,
            "rbe2":        NA,
            "rbe3":        NA,
            "rbar":        NA,
            "mpc":         NA,
        },
    }
