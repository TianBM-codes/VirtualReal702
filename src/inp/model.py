"""
Data model for a parsed Abaqus INP file.

Key design decisions (per INP-Parser-Design.md):
- Original node/element labels are preserved (not renumbered)
- Instance coordinates stay in local (Part) space; transforms stored separately
- Element keeps both factory_type (for visualization) and abaqus_type (for analysis)
- Sets at Part level and Assembly level are stored separately (different scopes)
- Diagnostics are collected on InpModel, not raised
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Element type mapping  (Abaqus type string → canonical geometry name)
# The colleague should map these strings to their MeshElementFactory enum.
# Variants like C3D8R / C3D8I / C3D8H all share the same geometry ("HEX8")
# but differ in integration / formulation — abaqus_type preserves that info.
# ---------------------------------------------------------------------------

ABAQUS_TO_FACTORY: Dict[str, str] = {
    # ---- solid: tetrahedra ----
    "C3D4":    "TET4",
    "C3D4H":   "TET4",
    "C3D10":   "TET10",
    "C3D10H":  "TET10",
    "C3D10M":  "TET10",
    "C3D10MH": "TET10",
    # ---- solid: wedge / prism ----
    "C3D6":    "WEDGE6",
    "C3D6H":   "WEDGE6",
    "C3D15":   "WEDGE15",
    "C3D15H":  "WEDGE15",
    # ---- solid: hexahedron ----
    "C3D8":    "HEX8",
    "C3D8R":   "HEX8",
    "C3D8I":   "HEX8",
    "C3D8H":   "HEX8",
    "C3D8RH":  "HEX8",
    "C3D20":   "HEX20",
    "C3D20R":  "HEX20",
    "C3D20H":  "HEX20",
    "C3D20RH": "HEX20",
    # ---- plane stress ----
    "CPS3":    "TRI3",
    "CPS4":    "QUAD4",
    "CPS4R":   "QUAD4",
    "CPS6":    "TRI6",
    "CPS8":    "QUAD8",
    "CPS8R":   "QUAD8",
    # ---- plane strain ----
    "CPE3":    "TRI3",
    "CPE4":    "QUAD4",
    "CPE4R":   "QUAD4",
    "CPE6":    "TRI6",
    "CPE8":    "QUAD8",
    "CPE8R":   "QUAD8",
    # ---- axisymmetric ----
    "CAX3":    "TRI3",
    "CAX4":    "QUAD4",
    "CAX4R":   "QUAD4",
    "CAX6":    "TRI6",
    "CAX8":    "QUAD8",
    "CAX8R":   "QUAD8",
    # ---- shell ----
    "S3":      "TRI3",
    "S3R":     "TRI3",
    "S6":      "TRI6",
    "STRI3":   "TRI3",
    "STRI65":  "TRI6",
    "S4":      "QUAD4",
    "S4R":     "QUAD4",
    "S4R5":    "QUAD4",
    "S8R":     "QUAD8",
    "S8R5":    "QUAD8",
    "SC6R":    "WEDGE6",
    "SC8R":    "HEX8",
    # ---- membrane ----
    "M3D3":    "TRI3",
    "M3D4":    "QUAD4",
    "M3D4R":   "QUAD4",
    # ---- truss ----
    "T3D2":    "LINE2",
    "T3D3":    "LINE3",
    # ---- beam ----
    "B31":     "LINE2",
    "B32":     "LINE3",
    "B31OS":   "LINE2",
    "B32OS":   "LINE3",
    "PIPE31":  "LINE2",
    "PIPE32":  "LINE3",
    # ---- rigid ----
    "R3D3":    "TRI3",
    "R3D4":    "QUAD4",
    # ---- cohesive ----
    "COH2D4":  "QUAD4",
    "COH3D6":  "WEDGE6",
    "COH3D8":  "HEX8",
    # ---- spring / mass (zero-dimensional or 1D) ----
    "SPRING1":  "POINT",
    "SPRING2":  "LINE2",
    "SPRINGA":  "LINE2",
    "MASS":     "POINT",
    "ROTARYI":  "POINT",
    "CONN3D2":  "LINE2",
}

# Ordered longest-first so multi-char suffixes (OS, RH, MH, RT, R5) are
# matched before their single-char components.
_ABAQUS_VARIANT_SUFFIXES = ('OS', 'RH', 'MH', 'R5', 'R', 'H', 'I', 'M', 'T', '5')


def map_element_type(abaqus_type: str) -> Optional[str]:
    """Return canonical geometry name, or None if unknown.

    Strips Abaqus variant suffixes (R, H, I, T, M, OS, RH, …) one at a time
    until a known base type is found, so e.g. S8RT → S8R → QUAD8.
    """
    s = abaqus_type.upper()
    while True:
        result = ABAQUS_TO_FACTORY.get(s)
        if result is not None:
            return result
        for suf in _ABAQUS_VARIANT_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf):
                s = s[:-len(suf)]
                break
        else:
            return None


# ---------------------------------------------------------------------------
# Node / Element
# ---------------------------------------------------------------------------

@dataclass
class Node:
    label: int
    x: float
    y: float
    z: float = 0.0


@dataclass
class Element:
    label: int
    abaqus_type: str   # original string, e.g. "C3D8R" — for analysis use
    factory_type: str  # canonical geometry, e.g. "HEX8" — for visualization
    node_labels: List[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sets  (Part-level)
# ---------------------------------------------------------------------------

@dataclass
class Nset:
    """Node set at Part scope."""
    name: str
    node_labels: List[int] = field(default_factory=list)
    set_refs: List[str] = field(default_factory=list)   # names of other nsets to include


@dataclass
class Elset:
    """Element set at Part scope."""
    name: str
    elem_labels: List[int] = field(default_factory=list)
    set_refs: List[str] = field(default_factory=list)   # names of other elsets to include


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------

@dataclass
class SurfaceEntry:
    ref_name: str    # elset name (ELEMENT type) or nset name (NODE type)
    face_id: str     # "S1", "SPOS", "SNEG", "END1", etc.  ("" for NODE type)


@dataclass
class Surface:
    name: str
    surface_type: str = "ELEMENT"   # "ELEMENT" or "NODE"
    entries: List[SurfaceEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

@dataclass
class Section:
    section_type: str           # "SOLID", "SHELL", "BEAM", "MEMBRANE", etc.
    elset_name: str
    material_name: str
    thickness: Optional[float] = None        # shell thickness
    thickness_expression: Optional[str] = None
    thickness_parameter: Optional[str] = None
    orientation_name: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)  # section-type-specific data


# ---------------------------------------------------------------------------
# Part
# ---------------------------------------------------------------------------

@dataclass
class Part:
    name: str
    nodes:    Dict[int, Node]    = field(default_factory=dict)
    elements: Dict[int, Element] = field(default_factory=dict)
    nsets:    Dict[str, Nset]    = field(default_factory=dict)
    elsets:   Dict[str, Elset]   = field(default_factory=dict)
    surfaces: Dict[str, Surface] = field(default_factory=dict)
    sections: List[Section]      = field(default_factory=list)


# ---------------------------------------------------------------------------
# Assembly / Instance
# ---------------------------------------------------------------------------

@dataclass
class Rotation:
    """
    Rotation from Abaqus Instance transform.

    Abaqus stores the rotation axis as two points on the axis line:
    - center: first point
    - axis:   second point on the same axis line
    """
    center: Tuple[float, float, float]
    axis:   Tuple[float, float, float]
    angle_deg: float


@dataclass
class Instance:
    name: str
    part_name: str
    translation: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: Optional[Rotation] = None


@dataclass
class AssemblyNset:
    """Node set at Assembly scope (may reference a specific instance)."""
    name: str
    instance_name: Optional[str]            # None = assembly-wide
    node_labels: List[int] = field(default_factory=list)
    set_refs: List[str] = field(default_factory=list)


@dataclass
class AssemblyElset:
    """Element set at Assembly scope (may reference a specific instance)."""
    name: str
    instance_name: Optional[str]
    elem_labels: List[int] = field(default_factory=list)
    set_refs: List[str] = field(default_factory=list)


@dataclass
class Assembly:
    name: str = "Assembly"
    instances: Dict[str, Instance]          = field(default_factory=dict)
    nsets:     Dict[str, AssemblyNset]      = field(default_factory=dict)
    elsets:    Dict[str, AssemblyElset]     = field(default_factory=dict)
    surfaces:  Dict[str, Surface]           = field(default_factory=dict)
    ties:      List[TieConstraint]          = field(default_factory=list)
    couplings: List[CouplingConstraint]     = field(default_factory=list)


# ---------------------------------------------------------------------------
# Materials  (parametric storage; no behaviour evaluation)
# ---------------------------------------------------------------------------

@dataclass
class ElasticData:
    """
    Isotropic: rows = [(E, nu)] or [(E, nu, T), ...] for temperature-dependent.
    ENGINEERING CONSTANTS: rows = [(E1,E2,E3,nu12,nu13,nu23,G12,G13,G23)] or + T.
    ANISOTROPIC: rows = [21 constants] or + T.
    """
    elastic_type: str = "ISOTROPIC"   # ISOTROPIC | ENGINEERING CONSTANTS | ANISOTROPIC | TRACTION
    data: List[Tuple] = field(default_factory=list)


@dataclass
class PlasticData:
    hardening: str = "ISOTROPIC"   # ISOTROPIC | KINEMATIC | COMBINED
    rate_dependent: bool = False
    data: List[Tuple] = field(default_factory=list)   # [(stress, plastic_strain[, strain_rate][, T]), ...]


@dataclass
class HyperelasticData:
    model: str = "NEO HOOKE"   # NEO HOOKE | MOONEY-RIVLIN | OGDEN | YEOH | ...
    data: List[Tuple] = field(default_factory=list)


@dataclass
class DamageData:
    criterion: str = "DUCTILE"
    data: List[Tuple] = field(default_factory=list)


@dataclass
class CreepData:
    law: str = "STRAIN"   # STRAIN | TIME | HYPERBOLIC-SINE
    data: List[Tuple] = field(default_factory=list)


@dataclass
class Material:
    name: str
    density_data:     List[Tuple] = field(default_factory=list)  # [(rho[, T]), ...]
    elastic:          Optional[ElasticData]     = None
    plastic:          Optional[PlasticData]     = None
    hyperelastic:     Optional[HyperelasticData] = None
    damage_initiation: Optional[DamageData]     = None
    damage_evolution:  Optional[DamageData]     = None
    creep:            Optional[CreepData]       = None
    conductivity_data: List[Tuple] = field(default_factory=list)
    expansion_data:    List[Tuple] = field(default_factory=list)
    specific_heat_data: List[Tuple] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loads — Distributed Surface Load
# ---------------------------------------------------------------------------

@dataclass
class DsloadDeclaration:
    """分布面载荷（*Dsload）——基于 Surface 的分布力。"""
    surface_name: str
    load_type: str          # "P", "TRSHR", "TRSHRNU", "VP" 等
    magnitude: float
    amplitude_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

@dataclass
class TieConstraint:
    """绑定约束（*Tie）——主面与从面完全绑定。"""
    name: str
    master_surface: str = ""
    slave_surface:  str = ""
    adjust:   bool = True
    tie_type: str  = "SURFACE TO SURFACE"   # 或 "NODE TO SURFACE"


@dataclass
class CouplingConstraint:
    """
    耦合约束（*Coupling + *Kinematic / *Distributing）。
    coupling_type 由紧随其后的子关键字决定，Parser 阶段填入。
    dof_ranges 仅 KINEMATIC 使用；空列表表示约束所有 6 个 DOF。
    """
    name: str
    ref_node: str           # 参考节点所在 nset 名
    surface:  str           # 耦合面名
    coupling_type: str = ""              # "KINEMATIC" 或 "DISTRIBUTING"
    dof_ranges: List[Tuple[int, int]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time Points
# ---------------------------------------------------------------------------

@dataclass
class TimePoints:
    """时间点列表（*Time Points）——历程输出或增量步控制用。"""
    name: str
    times: List[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Amplitude
# ---------------------------------------------------------------------------

@dataclass
class Amplitude:
    name: str
    times:  List[float] = field(default_factory=list)
    values: List[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------

@dataclass
class Orientation:
    name: str
    system: str = "RECTANGULAR"   # RECTANGULAR | CYLINDRICAL | SPHERICAL
    data: List[float] = field(default_factory=list)   # axis definition (6 floats)


# ---------------------------------------------------------------------------
# Step  (declaration only — no inheritance expansion here)
# ---------------------------------------------------------------------------

@dataclass
class BCDeclaration:
    nset_name: str
    dof_start: int
    dof_end:   int
    value:     float = 0.0
    op:        str = "MOD"           # "MOD" (default) or "NEW"
    amplitude_name: Optional[str] = None


@dataclass
class CLoadDeclaration:
    nset_name: str
    dof:   int
    value: float
    amplitude_name: Optional[str] = None


@dataclass
class DLoadDeclaration:
    elset_name: str
    load_type: str    # "P", "BX", "BY", "BZ", "CENTRIF", etc.
    magnitude: float
    amplitude_name: Optional[str] = None


@dataclass
class StepDeclaration:
    name: str
    step_type: str = "STATIC"    # STATIC | DYNAMIC | FREQUENCY | BUCKLE | HEAT TRANSFER | ...
    nlgeom: bool = False
    boundary_conditions: List[BCDeclaration]  = field(default_factory=list)
    cloads:  List[CLoadDeclaration]           = field(default_factory=list)
    dloads:  List[DLoadDeclaration]           = field(default_factory=list)
    dsloads: List[DsloadDeclaration]          = field(default_factory=list)


@dataclass
class ParameterDefinition:
    name: str
    expression: Optional[str] = None
    scalar_value: Optional[float] = None
    referenced_parameters: List[str] = field(default_factory=list)


@dataclass
class DesignParameter:
    name: str
    order: int


@dataclass
class DesignResponseRequest:
    region_type: str
    set_name: str
    variables: List[str] = field(default_factory=list)


@dataclass
class DesignResponse:
    step_name: Optional[str] = None
    frequency: int = 1
    requests: List[DesignResponseRequest] = field(default_factory=list)
    extra: Dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------

@dataclass
class InpModel:
    """
    The complete parsed (but not yet resolved) INP model.
    After parsing: set_refs contain names, not expanded label lists.
    After resolving: set_refs are cleared and labels are fully populated.
    """
    parts:        Dict[str, Part]          = field(default_factory=dict)
    assembly:     Optional[Assembly]       = None
    materials:    Dict[str, Material]      = field(default_factory=dict)
    amplitudes:   Dict[str, Amplitude]     = field(default_factory=dict)
    orientations: Dict[str, Orientation]   = field(default_factory=dict)
    steps:        List[StepDeclaration]    = field(default_factory=list)
    time_points:  Dict[str, TimePoints]    = field(default_factory=dict)
    parameters:   Dict[str, ParameterDefinition] = field(default_factory=dict)
    design_parameters: List[DesignParameter] = field(default_factory=list)
    design_responses: List[DesignResponse] = field(default_factory=list)
    diagnostics:  List                     = field(default_factory=list)
