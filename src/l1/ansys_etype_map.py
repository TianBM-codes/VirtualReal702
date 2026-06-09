#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ansys_etype_map.py — Ansys (MAPDL) 单元类型 → 渲染/分类映射表。

被 cdb_pack.py / rst_pack.py 共用。两条信息分两张表：

1. VTK_TO_ABAQUS —— 几何提取用。
   ansys-mapdl-reader 已经把每个单元解析成一个 VTK 拓扑类型（celltype），
   并且**自动处理退化单元**（例如 SOLID185 当成 8 节点 hex，但若用户按
   4/6/5 个唯一节点录入，会被识别成 VTK_TETRA / VTK_WEDGE / VTK_PYRAMID）。
   所以几何这边按 VTK 拓扑映射到 Abaqus 单元名最稳，不用按 ANSYS 单元号
   逐个判角点数。映射目标是 Abaqus 名字，因为下游 L2 的面提取只认 Abaqus 名。

2. ANSYS_FAMILY —— model_update 分类用。
   ANSYS 单元号(ET 的第二个参数，如 181/185/188) → 单元族
   (SHELL / SOLID / BEAM / LINK / PLANE / MASS / SPRING / TARGET / OTHER)。
   VTK 拓扑分不清「壳 vs 2D 平面」（都是 quad），这张表用来补充语义，
   同时给 model_update 的 element_family 字段用。

缺的单元后续往这两张表里补即可（VTK 表通常不用补，ANSYS_FAMILY 可能要补冷门单元）。
"""

# ─── VTK cell type 常量（vtkCellType.h）────────────────────────────────────────
VTK_VERTEX                = 1
VTK_POLY_VERTEX           = 2
VTK_LINE                  = 3
VTK_TRIANGLE              = 5
VTK_QUAD                  = 9
VTK_TETRA                 = 10
VTK_HEXAHEDRON            = 12
VTK_WEDGE                 = 13
VTK_PYRAMID               = 14
VTK_QUADRATIC_EDGE        = 21
VTK_QUADRATIC_TRIANGLE    = 22
VTK_QUADRATIC_QUAD        = 23
VTK_QUADRATIC_TETRA       = 24
VTK_QUADRATIC_HEXAHEDRON  = 25
VTK_QUADRATIC_WEDGE       = 26
VTK_QUADRATIC_PYRAMID     = 27


# ─── VTK 拓扑 → (Abaqus 名, 角节点数, 每单元面数) ──────────────────────────────
# 高阶 VTK 单元只取角节点（VTK 节点序约定：角点在前、中点在后），中点丢弃，
# n_corner 即角点数，与 bdf_pack 的约定一致。
VTK_TO_ABAQUS = {
    # 点 / 集中质量
    VTK_VERTEX:               ('MASS',   1, 0),
    VTK_POLY_VERTEX:          ('MASS',   1, 0),
    # 线 / 梁 / 杆
    VTK_LINE:                 ('B31',    2, 0),
    VTK_QUADRATIC_EDGE:       ('B32',    2, 0),   # 3 节点梁，取 2 角点
    # 三角 / 四边壳（或 2D 平面，拓扑相同）
    VTK_TRIANGLE:             ('S3',     3, 1),
    VTK_QUAD:                 ('S4R',    4, 1),
    VTK_QUADRATIC_TRIANGLE:   ('STRI65', 3, 1),   # 6 节点三角壳，取 3 角点
    VTK_QUADRATIC_QUAD:       ('S8R',    4, 1),   # 8 节点四边壳，取 4 角点
    # 实体
    VTK_TETRA:                ('C3D4',   4, 4),
    VTK_PYRAMID:              ('C3D5',   5, 5),
    VTK_WEDGE:                ('C3D6',   6, 5),
    VTK_HEXAHEDRON:           ('C3D8R',  8, 6),
    VTK_QUADRATIC_TETRA:      ('C3D10',  4, 4),   # 10 节点四面体，取 4 角点
    VTK_QUADRATIC_PYRAMID:    ('C3D13',  5, 5),   # 13 节点金字塔，取 5 角点
    VTK_QUADRATIC_WEDGE:      ('C3D15',  6, 5),   # 15 节点五面体，取 6 角点
    VTK_QUADRATIC_HEXAHEDRON: ('C3D20',  8, 6),   # 20 节点六面体，取 8 角点
}


# ─── Abaqus 名 → 角点面定义（FACE_DEFS）────────────────────────────────────────
# 与 bdf_pack.FACE_DEFS 对齐，并补充 ANSYS 会用到的金字塔(C3D5/C3D13)与 3 节点梁(B32)。
# 面用角点局部索引 0..n_corner-1 表示；高阶单元与其线性对应面拓扑相同。
FACE_DEFS = {
    # 壳 —— 线性
    'S4R':    [[0, 1, 2, 3]],
    'S3':     [[0, 1, 2]],
    # 壳 —— 高阶（面用角点索引）
    'S8R':    [[0, 1, 2, 3]],
    'STRI65': [[0, 1, 2]],
    # 实体 —— 六面体
    'C3D8R':  [
        [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 5, 4], [1, 2, 6, 5],
        [2, 3, 7, 6], [3, 0, 4, 7],
    ],
    'C3D20':  [
        [0, 1, 2, 3], [4, 5, 6, 7],
        [0, 1, 5, 4], [1, 2, 6, 5],
        [2, 3, 7, 6], [3, 0, 4, 7],
    ],
    # 实体 —— 五面体（楔形）
    'C3D6':   [
        [0, 1, 2], [3, 4, 5],
        [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5],
    ],
    'C3D15':  [
        [0, 1, 2], [3, 4, 5],
        [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5],
    ],
    # 实体 —— 四面体
    'C3D4':   [
        [0, 1, 2], [0, 1, 3],
        [0, 2, 3], [1, 2, 3],
    ],
    'C3D10':  [
        [0, 1, 2], [0, 1, 3],
        [0, 2, 3], [1, 2, 3],
    ],
    # 实体 —— 金字塔（4 底 + 1 顶）：1 个四边底面 + 4 个三角侧面
    'C3D5':   [
        [0, 1, 2, 3],
        [0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4],
    ],
    'C3D13':  [
        [0, 1, 2, 3],
        [0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4],
    ],
    # 无面类型
    'B31':    [],
    'B32':    [],
    'SPRING': [],
    'DASHPOT':[],
    'MASS':   [],
}


# ─── ANSYS 单元号 → 单元族 ─────────────────────────────────────────────────────
# 第二列是 ANSYS ET 命令里的单元号（KEYOPT 之外的主编号）。
# 族用于 model_update 的 element_family 分类，以及壳/实体/梁的语义判别。
# 说明：很多旧单元（legacy）几何上等价于新单元，这里只关心族归类。
ANSYS_FAMILY = {
    # ── 壳 SHELL ───────────────────────────────────────────────────────────────
    28:  'SHELL',   # SHELL28  shear/twist panel
    41:  'SHELL',   # SHELL41  membrane
    43:  'SHELL',   # SHELL43  plastic large strain (legacy of 181)
    63:  'SHELL',   # SHELL63  elastic 4-node
    91:  'SHELL',   # SHELL91  16-layer composite
    93:  'SHELL',   # SHELL93  8-node
    99:  'SHELL',   # SHELL99  100-layer composite
    131: 'SHELL',   # SHELL131 4-node thermal shell
    132: 'SHELL',   # SHELL132 8-node thermal shell
    143: 'SHELL',   # SHELL143 plastic small strain
    150: 'SHELL',   # SHELL150 8-node structural (p-element)
    157: 'SHELL',   # SHELL157 thermal-electric
    181: 'SHELL',   # SHELL181 4-node finite strain（最常用）
    208: 'SHELL',   # SHELL208 axisymmetric 2-node
    209: 'SHELL',   # SHELL209 axisymmetric 3-node
    281: 'SHELL',   # SHELL281 8-node finite strain

    # ── 实体 SOLID ─────────────────────────────────────────────────────────────
    5:   'SOLID',   # SOLID5   coupled-field hex
    45:  'SOLID',   # SOLID45  8-node (legacy of 185)
    62:  'SOLID',   # SOLID62  magneto-structural hex
    64:  'SOLID',   # SOLID64  anisotropic hex
    65:  'SOLID',   # SOLID65  reinforced concrete hex
    69:  'SOLID',   # SOLID69  thermal-electric hex
    70:  'SOLID',   # SOLID70  8-node thermal hex
    72:  'SOLID',   # SOLID72  tet w/ rotations
    73:  'SOLID',   # SOLID73  8-node w/ rotations
    87:  'SOLID',   # SOLID87  10-node thermal tet
    90:  'SOLID',   # SOLID90  20-node thermal hex
    92:  'SOLID',   # SOLID92  10-node tet (legacy of 187)
    95:  'SOLID',   # SOLID95  20-node hex (legacy of 186)
    96:  'SOLID',   # SOLID96  magnetic scalar hex
    97:  'SOLID',   # SOLID97  magnetic vector hex
    98:  'SOLID',   # SOLID98  coupled-field tet
    122: 'SOLID',   # SOLID122 20-node electrostatic
    123: 'SOLID',   # SOLID123 10-node electrostatic tet
    164: 'SOLID',   # SOLID164 explicit hex
    168: 'SOLID',   # SOLID168 explicit 10-node tet
    185: 'SOLID',   # SOLID185 8-node finite strain（最常用）
    186: 'SOLID',   # SOLID186 20-node
    187: 'SOLID',   # SOLID187 10-node tet
    226: 'SOLID',   # SOLID226 20-node coupled-field
    227: 'SOLID',   # SOLID227 10-node coupled-field tet
    231: 'SOLID',   # SOLID231 20-node electric
    232: 'SOLID',   # SOLID232 10-node electric tet
    236: 'SOLID',   # SOLID236 20-node electromagnetic
    237: 'SOLID',   # SOLID237 10-node electromagnetic tet
    272: 'SOLID',   # SOLID272 general axisymmetric (4-node base)
    273: 'SOLID',   # SOLID273 general axisymmetric (8-node base)
    278: 'SOLID',   # SOLID278 8-node thermal
    279: 'SOLID',   # SOLID279 20-node thermal
    285: 'SOLID',   # SOLID285 4-node tet (linear, w/ hydrostatic)

    # ── 2D 平面 PLANE（拓扑同壳，语义为 2D 连续体）────────────────────────────
    2:   'PLANE',   # PLANE2   6-node tri (legacy)
    13:  'PLANE',   # PLANE13  coupled-field quad
    25:  'PLANE',   # PLANE25  axisymmetric harmonic
    35:  'PLANE',   # PLANE35  6-node thermal tri
    42:  'PLANE',   # PLANE42  4-node (legacy of 182)
    53:  'PLANE',   # PLANE53  magnetic quad
    55:  'PLANE',   # PLANE55  4-node thermal
    75:  'PLANE',   # PLANE75  axisymmetric harmonic thermal
    77:  'PLANE',   # PLANE77  8-node thermal
    78:  'PLANE',   # PLANE78  axisymmetric harmonic thermal
    82:  'PLANE',   # PLANE82  8-node (legacy of 183)
    83:  'PLANE',   # PLANE83  axisymmetric harmonic 8-node
    121: 'PLANE',   # PLANE121 8-node electrostatic
    145: 'PLANE',   # PLANE145 p-element quad
    146: 'PLANE',   # PLANE146 p-element tri
    162: 'PLANE',   # PLANE162 explicit quad
    182: 'PLANE',   # PLANE182 4-node（最常用）
    183: 'PLANE',   # PLANE183 8-node/6-node
    223: 'PLANE',   # PLANE223 8-node coupled-field
    230: 'PLANE',   # PLANE230 8-node electric
    233: 'PLANE',   # PLANE233 8-node electromagnetic
    238: 'PLANE',   # PLANE238 8-node diffusion

    # ── 梁 BEAM ────────────────────────────────────────────────────────────────
    3:   'BEAM',    # BEAM3   2D elastic
    4:   'BEAM',    # BEAM4   3D elastic
    23:  'BEAM',    # BEAM23  2D plastic
    24:  'BEAM',    # BEAM24  3D thin-walled
    44:  'BEAM',    # BEAM44  3D tapered
    54:  'BEAM',    # BEAM54  2D tapered
    161: 'BEAM',    # BEAM161 explicit
    188: 'BEAM',    # BEAM188 2-node finite strain（最常用）
    189: 'BEAM',    # BEAM189 3-node finite strain

    # ── 杆/链/管 LINK / PIPE（按梁渲染为线）──────────────────────────────────
    1:   'LINK',    # LINK1   2D spar
    8:   'LINK',    # LINK8   3D spar
    10:  'LINK',    # LINK10  tension/compression only
    11:  'LINK',    # LINK11  linear actuator
    31:  'LINK',    # LINK31  radiation
    32:  'LINK',    # LINK32  2D thermal
    33:  'LINK',    # LINK33  3D thermal
    34:  'LINK',    # LINK34  convection
    68:  'LINK',    # LINK68  thermal-electric
    160: 'LINK',    # LINK160 explicit spar
    180: 'LINK',    # LINK180 3D finite strain spar（最常用）
    16:  'PIPE',    # PIPE16  elastic straight pipe
    17:  'PIPE',    # PIPE17  elastic pipe tee
    18:  'PIPE',    # PIPE18  elastic curved pipe
    20:  'PIPE',    # PIPE20  plastic straight pipe
    59:  'PIPE',    # PIPE59  immersed pipe
    60:  'PIPE',    # PIPE60  plastic curved pipe
    288: 'PIPE',    # PIPE288 2-node finite strain pipe
    289: 'PIPE',    # PIPE289 3-node finite strain pipe
    290: 'PIPE',    # ELBOW290 3-node elbow

    # ── 集中质量 MASS ─────────────────────────────────────────────────────────
    21:  'MASS',    # MASS21  structural mass
    71:  'MASS',    # MASS71  thermal mass
    166: 'MASS',    # MASS166 explicit mass

    # ── 弹簧/阻尼/连接 SPRING ──────────────────────────────────────────────────
    14:  'SPRING',  # COMBIN14 spring-damper
    37:  'SPRING',  # COMBIN37 control
    39:  'SPRING',  # COMBIN39 nonlinear spring
    40:  'SPRING',  # COMBIN40 combination
    7:   'SPRING',  # COMBIN7  revolute joint
    250: 'SPRING',  # COMBI250 (bushing)
    214: 'SPRING',  # COMBI214 2D bearing

    # ── 多点约束 / 刚体 ────────────────────────────────────────────────────────
    184: 'MPC',     # MPC184  multipoint constraint (rigid link/beam/joint)

    # ── 接触 / 目标（渲染一般跳过）─────────────────────────────────────────────
    169: 'TARGET',  # TARGE169 2D target
    170: 'TARGET',  # TARGE170 3D target
    171: 'CONTACT', # CONTA171 2D surf-surf
    172: 'CONTACT', # CONTA172 2D surf-surf
    173: 'CONTACT', # CONTA173 3D surf-surf
    174: 'CONTACT', # CONTA174 3D surf-surf（最常用）
    175: 'CONTACT', # CONTA175 node-surf
    176: 'CONTACT', # CONTA176 3D line-line
    177: 'CONTACT', # CONTA177 3D line-surf
    178: 'CONTACT', # CONTA178 node-node

    # ── 表面效应单元 SURF（渲染一般跳过或当壳）─────────────────────────────────
    151: 'SURF',    # SURF151 2D thermal surface
    152: 'SURF',    # SURF152 3D thermal surface
    153: 'SURF',    # SURF153 2D structural surface
    154: 'SURF',    # SURF154 3D structural surface
    155: 'SURF',    # SURF155 3D thermal
    156: 'SURF',    # SURF156 3D structural line load
    159: 'SURF',    # SURF159 general axisymmetric surface

    # ── 其它 ───────────────────────────────────────────────────────────────────
    200: 'MESH',    # MESH200 meshing-only（无物理，渲染可跳过）
}

# 渲染时默认跳过的单元族（无几何面或非实体几何）。
# 注意：MASS/SPRING/B31 仍写入几何（点/线），只是没有三角面，由 FACE_DEFS 决定。
SKIP_FAMILIES_FOR_RENDER = {'TARGET', 'CONTACT', 'SURF', 'MESH'}


def family_for_etype(ansys_etype):
    """ANSYS 单元号 → 族字符串；未知返回 'OTHER'。"""
    try:
        return ANSYS_FAMILY.get(int(ansys_etype), 'OTHER')
    except (TypeError, ValueError):
        return 'OTHER'


def abaqus_for_vtk(vtk_celltype):
    """VTK celltype → (abaqus_name, n_corner, n_faces)；未知返回 None。"""
    return VTK_TO_ABAQUS.get(int(vtk_celltype))


# model_update element_family 归一：把渲染族收敛到 model_update 认的 4 类。
_MU_FAMILY = {
    'SHELL': 'SHELL', 'PLANE': 'SHELL',          # 2D 连续体当壳处理（带厚度参数）
    'SOLID': 'SOLID',
    'BEAM': 'BEAM', 'LINK': 'BEAM', 'PIPE': 'BEAM',
}


def model_update_family(ansys_etype):
    """ANSYS 单元号 → model_update 的 element_family（SHELL/SOLID/BEAM/OTHER）。"""
    return _MU_FAMILY.get(family_for_etype(ansys_etype), 'OTHER')
