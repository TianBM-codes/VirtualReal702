import json
from MeshElementFactory import MeshElementFactory
from collections import OrderedDict
from MeshCleaner import MarkSurface, CalculateUniqueEdge
from VTUWriter import WriteFaceAndEdgeToVTU
import meshio
from FemNode import FemNode
from pyNastran.bdf.bdf import BDF
from pyNastran.bdf.cards.materials import MAT1, MAT8, MAT9
import numpy as np


class BDFParser(object):

    def __init__(self, input_path):
        self.inp_path = input_path

        # pyNastran模型
        self.bdf = None

        # 统一解析结果
        self.parse_results = {}

        # 节点相关
        self.nodes = []
        self.node_ids = []
        self.global_node_hash = {}

        # 单元相关
        self.ele_count = 0
        self.bar_eles = []
        self.solid_eles = []
        self.solid_nodes = []
        self.bar_nodes = []

        # 几何输出相关
        self.unique_edges = []
        self.node_xyz = []
        self.triangles = []
        self.all_edges = []

    def reset_parse_results(self):
        self.parse_results = {
            "coordinate_systems": [],
            "materials_overview": [],
            "isotropic_list": [],
            "ortho2d_list": [],
            "aniso3d_list": [],
            "property_overview": [],
            "shell_properties": [],
            "bar_properties": [],
            "solid_properties": [],
            "layered_properties": [],
            "layered_plies": [],
            "boundary": []
        }

    def parse(self):
        self.read_bdf()
        self.parse_nodes()
        self.parse_elements()

    def read_bdf(self):
        self.bdf = BDF(debug=False)
        # xref=False：不做交叉引用，速度快，数据更“原始”
        self.bdf.read_bdf(self.inp_path, xref=True)

    def parse_nodes(self):
        # 真实节点号（排序）
        self.node_ids = []
        self.nodes = []
        self.global_node_hash = {}

        for idx, nid in enumerate(sorted(self.bdf.nodes.keys())):
            try:
                x, y, z = self.bdf.nodes[nid].get_position()
            except AttributeError as e:
                print(f"node {nid}: {e}")
                continue

            n_id = int(nid)
            self.nodes.append(FemNode(n_id, float(x), float(y), float(z)))
            self.node_ids.append(n_id)
            # self.global_node_hash[n_id] = len(self.nodes) - 1
            self.global_node_hash[n_id] = idx

    def parse_elements(self):
        for eid in sorted(self.bdf.elements.keys()):
            elem_obj = self.bdf.elements[eid]
            e_type = elem_obj.type
            ele_node_list = list(elem_obj.node_ids)
            ele_node_list = list(OrderedDict.fromkeys(ele_node_list))
            iter_ele, is_solid = MeshElementFactory.CreateElement(
                e_type=e_type,
                e_id=eid,
                opt=len(ele_node_list),
                fem_software="NASTRAN"
            )

            try:
                iter_ele.setFaces(ele_node_list)
            except:
                print(f"setFaces error: {e_type} {eid} {ele_node_list}")

            if is_solid:
                self.solid_nodes.extend(ele_node_list)  # 为每个单元所包含节点的节点编号组合起来，例如[1324, 1322, 1321, 1326]
                self.solid_eles.append(iter_ele)  # 每个单元的实例，[MeshCQUAD4(10),MeshCQUAD4(11).....]
            else:
                self.bar_nodes.extend(ele_node_list)
                self.bar_eles.append(iter_ele)

    def build_geometry_data(self, only_surface=False):
        """
        统一生成几何数据:
        - node_xyz: [x0,y0,z0, x1,y1,z1, ...] (按 node_ids 顺序)
        - triangles: [i0,i1,i2, ...] (solid 三角面索引)
        - unique_edges: [i0,i1, i2,i3, ...] (solid + bar 的边索引，已去重)
        """
        self.node_xyz = []
        self.triangles = []
        self.all_edges = []
        self.unique_edges = []
        # 1) 节点坐标
        for nid in self.node_ids:
            idx = self.global_node_hash[nid]
            self.node_xyz.extend(self.nodes[idx].coord.tolist())

        # 2) solid: triangles + edges
        if self.solid_eles:
            # 建议：只在需要 only_surface=True 时再 MarkSurface，避免不必要开销
            # 但如果 ele.getAllTriangles / getEdges 依赖 MarkSurface 标记，那就保留
            MarkSurface(self.solid_eles)

            for ele in self.solid_eles:
                tris = ele.getAllTriangles(only_surface=only_surface)
                try:
                    for (n0, n1, n2) in tris:
                        self.triangles.extend([
                            self.global_node_hash[n0],
                            self.global_node_hash[n1],
                            self.global_node_hash[n2],
                        ])
                except TypeError as e:
                    print(ele.id)

                # 注意：这里假设 ele.getEdges() 返回的是节点号序列 [n0,n1,n2,n3,...]（成对）
                # 你的原逻辑是直接把它们映射成 index 并 append
                edges_nid = ele.getEdges()
                if edges_nid:
                    self.all_edges.extend([self.global_node_hash[nid] for nid in edges_nid])

        # 3) bar: edges
        if self.bar_eles:
            for ele in self.bar_eles:
                edges_nid = ele.getEdges()
                if not edges_nid:
                    continue

                # edges_nid 期望为 [n1,n2,n2,n3,...]
                for k in range(0, len(edges_nid), 2):
                    n0 = edges_nid[k]
                    n1 = edges_nid[k + 1]
                    self.all_edges.extend([self.global_node_hash[n0], self.global_node_hash[n1]])

        self.unique_edges = CalculateUniqueEdge(self.all_edges)

    def WriteToVTU(self, output_path):
        """
        写出 VTU:
        - points: self.node_xyz
        - faces : self.triangles
        - edges : self.unique_edges
        """

        # 2) 调用已有的 VTU写函数
        WriteFaceAndEdgeToVTU(
            self.node_xyz,
            self.unique_edges,
            self.triangles,
            output_path
        )

    def WriteToJson(self, output_path):
        """
        输出 BufferGeometry JSON（包含 position / index / edgeIndex）
        - solid_eles: 三角面（index）
        - bar_eles  : 梁边（edgeIndex）
        only_surface=True 时：solid_eles 只输出外表面三角面
        """

        # 2) 组装 JSON
        json_data = {
            "data": {
                "attributes": {
                    "position": {
                        "array": self.node_xyz,
                        "itemSize": 3,
                        "type": "Float32Array"
                    }
                },
                "index": {
                    "array": self.triangles,
                    "itemSize": 1,
                    "type": "Uint32Array"
                },
                "edgeIndex": self.unique_edges,

                "interleavedBuffers": {
                    "Result": {
                        "buffer": "1CE1796E-3F05-2251-2DCC-2C2D14F13B3F",
                        "stride": 0,
                        "type": "Float32Array",
                        "uuid": "FF275754-4711-AC0A-21EA-69246C54D02C"
                    }
                },
                "arrayBuffers": {
                    "1CE1796E-3F05-2251-2DCC-2C2D14F13B3F": []
                },
                "exceptValue": 0,
                "content": ""
            },
            "metadata": {"type": "BufferGeometry", "version": 4},
            "name": "XiGuiChe_Shell",
            "type": "BufferGeometry",
            "userData": {},
            "uuid": "946A1C8F-19EF-1CA6-8B63-B4745CB83B4B"
        }

        # 3) 写文件
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(json_data, fp, ensure_ascii=False, indent=4)

    def WriteVtuFile(self, vtu_path):
        """
        将模型写入vtu文件，使用paraview显示
        :param vtu_path:
        :return:
        """
        coords = []
        for nd in self.nodes:
            coords.append(nd.coord)
        node_coords = np.asarray(coords)
        all_eles = {}

        for iter_ele in self.solid_eles:
            iter_relation = [self.global_node_hash[ii] for ii in iter_ele.nodeIds]
            ele_type = iter_ele.vtu_type
            if all_eles.__contains__(ele_type):
                all_eles[ele_type].append(iter_relation)
            else:
                all_eles[ele_type] = [iter_relation]

        for iter_ele in self.bar_eles:
            iter_relation = [self.global_node_hash[ii] for ii in iter_ele.nodeIds]
            ele_type = iter_ele.vtu_type
            if all_eles.__contains__(ele_type):
                all_eles[ele_type].append(iter_relation)
            else:
                all_eles[ele_type] = [iter_relation]

        meshio.write_points_cells(
            filename=vtu_path,
            points=node_coords,
            cells=all_eles,
            # point_data=node_res,
            # cell_data=cell_data,
            # field_data=field_data
        )

    def _parse_coordinate_systems(self):
        coord_rows = []

        for cid, coord in sorted(self.bdf.coords.items()):
            if cid == 0:
                continue

            try:
                fields = coord.raw_fields()
            except Exception as e:
                print(f"read coord raw_fields failed: cid={cid}, err={e}")
                continue

            if not fields:
                continue

            card_type = str(fields[0]).upper()
            if card_type not in ("CORD2R", "CORD2C", "CORD2S", "CORD1R", "CORD1C", "CORD1S"):
                continue

            row = {
                "ID": int(fields[1]) if len(fields) > 1 and fields[1] is not None else None,
                "RID": int(fields[2]) if len(fields) > 2 and fields[2] not in (None, '') else 0,
                "Type": "RECTANGULAR" if card_type.endswith("R") else
                "CYLINDRICAL" if card_type.endswith("C") else
                "SPHERICAL" if card_type.endswith("S") else card_type,
                "X1": 0.0, "X2": 0.0, "X3": 0.0,
                "X4": 0.0, "X5": 0.0, "X6": 0.0,
                "X7": 0.0, "X8": 0.0, "X9": 0.0,
            }

            value_keys = ["X1", "X2", "X3", "X4", "X5", "X6", "X7", "X8", "X9"]
            values = list(fields[3:12])

            for i, key in enumerate(value_keys):
                if i >= len(values):
                    break
                v = values[i]
                if v in (None, ''):
                    row[key] = 0.0
                else:
                    try:
                        row[key] = float(v)
                    except Exception:
                        row[key] = v

            coord_rows.append(row)

        self.parse_results["coordinate_systems"] = coord_rows
        return coord_rows

    def _parse_materials(self):
        """
        材料分三部分：
        1. materials_overview  : 总览 (Id, Type)
        2. isotropic_list      : 各向同性材料（MAT1）
        3. ortho2d_list        : 正交各向异性2D材料（MAT8）
        4. aniso3d_list        : 各向异性3D材料（MAT9）
        """
        materials_overview = []
        isotropic_list = []
        ortho2d_list = []
        aniso3d_list = []


        for mat_id, mat in sorted(self.bdf.materials.items()):
            # =========================
            # MAT1 -> ISOTROPIC
            # =========================
            if isinstance(mat, MAT1):
                materials_overview.append((mat_id, "ISOTROPIC"))
                isotropic_list.append((
                    mat_id,
                    mat.rho,  # RHO
                    mat.e,  # E
                    mat.nu,  # NU
                    mat.ge  # GE
                ))

            # =========================
            # MAT8 -> ORTHO2D
            # =========================
            elif isinstance(mat, MAT8):
                fields = mat.repr_fields()

                # MAT8:
                # [0] MAT8
                # [1] MID
                # [2] E1
                # [3] E2
                # [4] NU12
                # [5] G12
                # [6] G1Z
                # [7] G2Z
                # [8] RHO
                g1z = 0.0 if fields[6] in (None, '') else float(fields[6])
                g2z = 0.0 if fields[7] in (None, '') else float(fields[7])

                materials_overview.append((mat_id, "ORTHO2D"))
                ortho2d_list.append((
                    mat_id,
                    mat.rho,  # RHO
                    mat.e11,  # EX
                    mat.e22,  # EY
                    mat.g12,  # GXY
                    mat.nu12,  # NUXY
                    g1z,  # GXZ
                    g2z,  # GYZ
                    mat.ge  # GE
                ))

            # =========================
            # MAT9 -> ANISO3D
            # =========================
            elif isinstance(mat, MAT9):
                # 你的界面要的是：
                # Id, RHO, D11, D12, D13, D14, D15, D16,
                # D22, D23, D24, D25, D26, D33

                # pyNastran里 MAT9 的刚度项通常放在 Gij / H 等字段里
                # 这里统一按 raw_fields 取，最接近原卡片
                fields = mat.raw_fields()

                # MAT9 常见顺序大致是：
                # MAT9, MID,
                # G11, G12, G13, G14, G15, G16,
                # G22, G23, G24, G25, G26,
                # G33, G34, G35, G36,
                # G44, G45, G46,
                # G55, G56,
                # G66, RHO, ...
                #
                # 你当前界面只需要：
                # D11 D12 D13 D14 D15 D16 D22 D23 D24 D25 D26 D33

                def _to_float(v):
                    if v in (None, ''):
                        return 0.0
                    return float(v)

                d11 = _to_float(fields[2]) if len(fields) > 2 else 0.0
                d12 = _to_float(fields[3]) if len(fields) > 3 else 0.0
                d13 = _to_float(fields[4]) if len(fields) > 4 else 0.0
                d14 = _to_float(fields[5]) if len(fields) > 5 else 0.0
                d15 = _to_float(fields[6]) if len(fields) > 6 else 0.0
                d16 = _to_float(fields[7]) if len(fields) > 7 else 0.0

                d22 = _to_float(fields[8]) if len(fields) > 8 else 0.0
                d23 = _to_float(fields[9]) if len(fields) > 9 else 0.0
                d24 = _to_float(fields[10]) if len(fields) > 10 else 0.0
                d25 = _to_float(fields[11]) if len(fields) > 11 else 0.0
                d26 = _to_float(fields[12]) if len(fields) > 12 else 0.0

                d33 = _to_float(fields[13]) if len(fields) > 13 else 0.0
                d34 = _to_float(fields[14]) if len(fields) > 14 else 0.0
                d35 = _to_float(fields[15]) if len(fields) > 15 else 0.0
                d36 = _to_float(fields[16]) if len(fields) > 16 else 0.0

                d44 = _to_float(fields[17]) if len(fields) > 17 else 0.0
                d45 = _to_float(fields[18]) if len(fields) > 18 else 0.0
                d46 = _to_float(fields[19]) if len(fields) > 19 else 0.0

                d55 = _to_float(fields[20]) if len(fields) > 20 else 0.0
                d56 = _to_float(fields[21]) if len(fields) > 21 else 0.0

                d66 = _to_float(fields[22]) if len(fields) > 22 else 0.0

                # RHO、GE 优先直接取属性，更稳
                rho = _to_float(getattr(mat, "rho", 0.0))
                ge = _to_float(getattr(mat, "ge", 0.0))

                materials_overview.append((mat_id, "ANISO3D"))
                aniso3d_list.append((
                    mat_id,
                    rho,
                    d11, d12, d13, d14, d15, d16,
                    d22, d23, d24, d25, d26,
                    d33, d34, d35, d36,
                    d44, d45, d46,
                    d55, d56,
                    d66,
                    ge
                ))

            else:
                print("other material type:", mat.type)

        self.parse_results["materials_overview"] = materials_overview
        self.parse_results["isotropic_list"] = isotropic_list
        self.parse_results["ortho2d_list"] = ortho2d_list
        self.parse_results["aniso3d_list"] = aniso3d_list

        return {
            "materials_overview": materials_overview,
            "isotropic_list": isotropic_list,
            "ortho2d_list": ortho2d_list,
            "aniso3d_list": aniso3d_list
        }

    def _parse_properties(self):
        property_overview = []
        shell_properties = []
        layered_properties = []
        bar_properties = []
        solid_properties = []

        def _first_value(v, default=0.0):
            if isinstance(v, np.ndarray):
                if len(v) == 0:
                    return default
                v = v[0]
            if v is None:
                return default
            try:
                return float(v)
            except Exception:
                return default

        def _to_int(v, default=0):
            if v is None:
                return default
            try:
                return int(v)
            except Exception:
                return default

        def _unwrap_id(obj, attr_name):
            """
            兼容 xref=True 场景:
            - mid 可能是 int
            - 也可能是 MAT1/MAT8/... 对象
            - cid 也可能是对象
            """
            if obj is None:
                return 0
            if hasattr(obj, attr_name):
                try:
                    return int(getattr(obj, attr_name))
                except Exception:
                    return 0
            try:
                return int(obj)
            except Exception:
                return 0

        def _get_bar_ay_az(prop, area):
            def _val(v):
                if isinstance(v, np.ndarray):
                    v = v[0] if len(v) > 0 else None
                try:
                    return float(v)
                except:
                    return None

            # 1️⃣ 显式 AY/AZ
            ay = None
            az = None

            for name in ("ay", "Ay", "AY", "a_y", "A_y"):
                if hasattr(prop, name):
                    ay = _val(getattr(prop, name))
                    break

            for name in ("az", "Az", "AZ", "a_z", "A_z"):
                if hasattr(prop, name):
                    az = _val(getattr(prop, name))
                    break

            if ay is not None and az is not None:
                return ay, az

            # 2️⃣ k1/k2 推导
            k1 = _val(getattr(prop, "k1", None))
            k2 = _val(getattr(prop, "k2", None))

            if k1 is not None and 0 < k1 <= 5:
                ay = area * k1
            if k2 is not None and 0 < k2 <= 5:
                az = area * k2

            if ay is not None and az is not None:
                return ay, az

            # 3️⃣ femtools fallback
            big = area * 1e20
            return big, big

        for pid, prop in sorted(self.bdf.properties.items()):
            ptype = str(prop.type).upper()

            # -------------------------------------------------
            # 1) 壳单元
            # -------------------------------------------------
            if ptype == "PSHELL":
                property_overview.append((pid, "SHELL"))

                t = _first_value(getattr(prop, "t", 0.0), 0.0)
                nsm = _first_value(getattr(prop, "nsm", 0.0), 0.0)

                shell_properties.append((
                    pid,
                    t,
                    nsm,
                    0
                ))

            # -----------------------------
            # 复合铺层壳
            # -----------------------------
            elif ptype in ("PCOMP", "PCOMPG"):
                property_overview.append((pid, "LAYERED"))

                # 1) 基本字段
                z0 = _first_value(getattr(prop, "z0", 0.0), 0.0)
                theta = _first_value(getattr(prop, "theta", 0.0), 0.0)
                ge = _first_value(getattr(prop, "ge", 0.0), 0.0)
                nsm = _first_value(getattr(prop, "nsm", 0.0), 0.0)

                # 2) 总厚度
                total_t = 0.0
                if hasattr(prop, "TotalThickness"):
                    try:
                        total_t = float(prop.TotalThickness())
                    except Exception:
                        total_t = 0.0
                elif hasattr(prop, "Thickness"):
                    try:
                        total_t = float(prop.Thickness())
                    except Exception:
                        total_t = 0.0
                else:
                    # 兜底：自己累加每层厚度
                    if hasattr(prop, "thicknesses"):
                        try:
                            total_t = float(sum(prop.thicknesses))
                        except Exception:
                            total_t = 0.0
                    elif hasattr(prop, "t"):
                        try:
                            total_t = float(sum(prop.t))
                        except Exception:
                            total_t = 0.0

                # 3) femtools 里的 Offset 更像这个，不是直接 z0
                offset = z0 + 0.5 * total_t

                # 4) Layers：优先取原始铺层定义数，不要再手动 /2
                layers = 0
                if hasattr(prop, "mids"):
                    try:
                        layers = len(prop.mids)
                    except Exception:
                        layers = 0
                elif hasattr(prop, "plies"):
                    try:
                        layers = len(prop.plies)
                    except Exception:
                        layers = 0
                elif hasattr(prop, "material_ids"):
                    try:
                        layers = len(prop.material_ids)
                    except Exception:
                        layers = 0
                elif hasattr(prop, "nplies"):
                    layers = _to_int(getattr(prop, "nplies", 0), 0)

                layered_properties.append((
                    pid,
                    offset,  # Offset
                    theta,  # Theta
                    ge,  # GE
                    nsm,  # NSM
                    layers  # Layers
                ))

            # -------------------------------------------------
            # 2) 梁 / 杆 / 管 / 截面梁
            # 统一进 BAR 3D / BEAM 3D
            # bar_properties tuple:
            # (pid, AX, AY, AZ, IX, IY, IZ, CW, YN, ZN, NSM)
            # -------------------------------------------------
            elif ptype == "PBAR":
                property_overview.append((pid, "BAR 3D"))

                ax = _first_value(getattr(prop, "A", 0.0), 0.0)
                ay, az = _get_bar_ay_az(prop, ax)

                ix = _first_value(getattr(prop, "j", 0.0), 0.0)
                iy = _first_value(getattr(prop, "i1", 0.0), 0.0)
                iz = _first_value(getattr(prop, "i2", 0.0), 0.0)
                nsm = _first_value(getattr(prop, "nsm", 0.0), 0.0)

                bar_properties.append((
                    pid, ax, ay, az,
                    ix, iy, iz,
                    0.0, 0.0, 0.0,
                    nsm
                ))

            elif ptype == "PBEAM":
                property_overview.append((pid, "BEAM 3D"))

                ax = _first_value(getattr(prop, "A", 0.0), 0.0)
                ay, az = _get_bar_ay_az(prop, ax)

                ix = _first_value(getattr(prop, "j", 0.0), 0.0)
                iy = _first_value(getattr(prop, "i1", 0.0), 0.0)
                iz = _first_value(getattr(prop, "i2", 0.0), 0.0)
                nsm = _first_value(getattr(prop, "nsm", 0.0), 0.0)

                bar_properties.append((
                    pid, ax, ay, az,
                    ix, iy, iz,
                    0.0, 0.0, 0.0,
                    nsm
                ))

            elif ptype == "PROD":
                # CROD / CONROD 常配 PROD
                property_overview.append((pid, "BAR 3D"))

                ax = _first_value(getattr(prop, "A", 0.0), 0.0)
                ix = _first_value(getattr(prop, "j", 0.0), 0.0)
                nsm = _first_value(getattr(prop, "nsm", 0.0), 0.0)

                # rod 通常没有单独 ay/az/i1/i2，按 femtools 风格可给默认大剪切面积
                ay, az = _get_bar_ay_az(prop, ax)

                bar_properties.append((
                    pid, ax, ay, az,
                    ix, 0.0, 0.0,
                    0.0, 0.0, 0.0,
                    nsm
                ))

            elif ptype == "PTUBE":
                property_overview.append((pid, "BAR 3D"))

                # pyNastran 中 PTUBE 往往可直接 Area()
                ax = 0.0
                if hasattr(prop, "Area"):
                    try:
                        ax = float(prop.Area())
                    except Exception:
                        ax = 0.0
                if ax == 0.0:
                    ax = _first_value(getattr(prop, "A", 0.0), 0.0)

                ix = 0.0
                if hasattr(prop, "J"):
                    try:
                        ix = float(prop.J())
                    except Exception:
                        ix = 0.0
                if ix == 0.0:
                    ix = _first_value(getattr(prop, "j", 0.0), 0.0)

                iy = 0.0
                iz = 0.0
                if hasattr(prop, "I11"):
                    try:
                        iy = float(prop.I11())
                    except Exception:
                        iy = 0.0
                if hasattr(prop, "I22"):
                    try:
                        iz = float(prop.I22())
                    except Exception:
                        iz = 0.0

                nsm = _first_value(getattr(prop, "nsm", 0.0), 0.0)
                ay, az = _get_bar_ay_az(prop, ax)

                bar_properties.append((
                    pid, ax, ay, az,
                    ix, iy, iz,
                    0.0, 0.0, 0.0,
                    nsm
                ))

            elif ptype in ("PBARL", "PBEAML"):
                # 参数化截面梁，最好尝试调用对象方法拿截面属性
                property_overview.append((pid, "BEAM 3D"))

                ax = 0.0
                ix = 0.0
                iy = 0.0
                iz = 0.0

                if hasattr(prop, "Area"):
                    try:
                        ax = float(prop.Area())
                    except Exception:
                        pass
                if ax == 0.0:
                    ax = _first_value(getattr(prop, "A", 0.0), 0.0)

                if hasattr(prop, "J"):
                    try:
                        ix = float(prop.J())
                    except Exception:
                        pass
                if ix == 0.0:
                    ix = _first_value(getattr(prop, "j", 0.0), 0.0)

                if hasattr(prop, "I11"):
                    try:
                        iy = float(prop.I11())
                    except Exception:
                        pass
                if hasattr(prop, "I22"):
                    try:
                        iz = float(prop.I22())
                    except Exception:
                        pass

                if iy == 0.0:
                    iy = _first_value(getattr(prop, "i1", 0.0), 0.0)
                if iz == 0.0:
                    iz = _first_value(getattr(prop, "i2", 0.0), 0.0)

                nsm = _first_value(getattr(prop, "nsm", 0.0), 0.0)
                ay, az = _get_bar_ay_az(prop, ax)

                bar_properties.append((
                    pid, ax, ay, az,
                    ix, iy, iz,
                    0.0, 0.0, 0.0,
                    nsm
                ))

            # -------------------------------------------------
            # 3) 实体单元
            # solid_properties tuple:
            # (pid, mid, cid)
            # -------------------------------------------------
            elif ptype == "PSOLID":
                property_overview.append((pid, "SOLID"))

                mid = getattr(prop, "mid", None)
                if hasattr(mid, "mid"):
                    mid = mid.mid

                cid = getattr(prop, "cid", None)
                if hasattr(cid, "cid"):
                    cid = cid.cid

                mid = _to_int(mid, 0)
                cid = _to_int(cid, 0)

                # cs = "GLOBAL" if cid == 0 else str(cid)

                solid_properties.append((
                    pid,
                    mid,
                    cid
                ))


            elif ptype in ("PLSOLID", "PIHEX", "PCOMPS"):
                property_overview.append((pid, "SOLID"))
                mid = getattr(prop, "mid", None)
                if hasattr(mid, "mid"):
                    mid = mid.mid
                cid = getattr(prop, "cid", None)
                if hasattr(cid, "cid"):
                    cid = cid.cid
                cid = _to_int(cid, 0)
                solid_properties.append((
                    pid,
                    mid,
                    cid
                ))

            # -------------------------------------------------
            # 4) 其他类型
            # -------------------------------------------------
            else:
                property_overview.append((pid, ptype))
                print("other prop type:", ptype)

        self.parse_results["property_overview"] = property_overview
        self.parse_results["shell_properties"] = shell_properties
        self.parse_results["layered_properties"] = layered_properties
        self.parse_results["bar_properties"] = bar_properties
        self.parse_results["solid_properties"] = solid_properties

        return {
            "property_overview": property_overview,
            "shell_properties": shell_properties,
            "layered_properties": layered_properties,
            "bar_properties": bar_properties,
            "solid_properties": solid_properties
        }

    def _parse_boundaries(self):
        boundary = []

        for spc_id, spc_case in self.bdf.spcs.items():
            for spc in spc_case:
                spc_type = getattr(spc, "type", "")

                # -------------------------------------------------
                # SPC / SPCD 这类：有 value
                # -------------------------------------------------
                if spc_type in ("SPC", "SPCD"):
                    for node in spc.nodes:
                        enforced = [None, None, None, None, None, None]
                        value = float(spc.value) if spc.value is not None else 0.0

                        for com in str(spc.components):
                            if com == '1':
                                enforced[0] = value
                            elif com == '2':
                                enforced[1] = value
                            elif com == '3':
                                enforced[2] = value
                            elif com == '4':
                                enforced[3] = value
                            elif com == '5':
                                enforced[4] = value
                            elif com == '6':
                                enforced[5] = value

                        boundary.append((node, enforced))

                # -------------------------------------------------
                # SPC1：没有 value，表示对应自由度固定为 0
                # -------------------------------------------------
                elif spc_type == "SPC1":
                    for node in spc.nodes:
                        enforced = [None, None, None, None, None, None]

                        for com in str(spc.components):
                            if com == '1':
                                enforced[0] = 0.0
                            elif com == '2':
                                enforced[1] = 0.0
                            elif com == '3':
                                enforced[2] = 0.0
                            elif com == '4':
                                enforced[3] = 0.0
                            elif com == '5':
                                enforced[4] = 0.0
                            elif com == '6':
                                enforced[5] = 0.0

                        boundary.append((node, enforced))

                else:
                    print(f"other spc type: {spc_type}")

        self.parse_results["boundary"] = boundary
        return boundary

    # =========================
    # 对外函数
    # =========================

    def GetCoordData(self):
        """
        只解析坐标系，并写入 self.parse_results
        """
        if not self.parse_results:
            self.reset_parse_results()
        return self._parse_coordinate_systems()

    def GetDatabaseData(self):
        """
        统一解析数据库相关数据，并写入 self.parse_results
        """
        self.reset_parse_results()

        self._parse_coordinate_systems()
        self._parse_materials()
        self._parse_properties()
        self._parse_boundaries()

        return self.parse_results


if __name__ == '__main__':
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/powertrain2.bdf")
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/dengzi.bdf")
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/mid2.bdf")
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/static_2.bdf")
    # bdf = BDFParser(r"C:\FEMtools\3.7.1\examples\updating\powertrain\fem24.bdf")
    # bdf = BDFParser(r"D:\SiPESC_yuan\project\702_force_verify\model\static_lam_force.bdf")
    bdf = BDFParser(r"D:\SiPESC_yuan\project\702_force_verify\model\227.bdf")
    bdf.parse()
    # bdf.WriteVtuFile(r"../PyModel2JsonDataFolder/output/powertrain.vtu")
    # bdf.GetDatabaseData()
    bdf.GetDatabaseData()
