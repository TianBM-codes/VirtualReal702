from MeshElementFactory import MeshElementFactory
from collections import OrderedDict
import meshio
from FemNode import FemNode
from pyNastran.bdf.bdf import BDF
from pyNastran.bdf.cards.materials import MAT1, MAT2, MAT3, MAT4, MAT5, MAT8
import numpy as np


class BDFParser(object):

    def __init__(self, input_path):
        self.inp_path = input_path

        # 节点相关
        self.nodes = None
        self.node_ids = []
        self.global_node_hash = {}
        # 单元相关
        self.ele_count = 0
        self.bar_eles = []
        self.solid_eles = []
        self.solid_nodes = []
        self.bar_nodes = []
        self.bdf = None
        self.unique_edges = []
        self.node_xyz = []
        self.triangles = []  # 三角面：以“点索引”存储 (i0,i1,i2, ...)
        self.all_edges = []  # 边：以“点索引对”平铺存储 (i0,i1,i2,i3, ...)

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

    def GetDatabaseData(self):
        """
        将解析结果写入数据库
        :return:
        """
        parse_results = {}

        """
        写入材料数据表
        """
        materials_overview = []
        isotropic_list = []
        for mat_id, mat in self.bdf.materials.items():
            if isinstance(mat, MAT1):
                materials_overview.append((mat_id, "ISOTROPIC"))
                isotropic_list.append((mat_id, mat.rho, mat.e, mat.nu, mat.ge))

        parse_results["materials_overview"] = materials_overview
        parse_results["isotropic_list"] = isotropic_list

        """
        单元属性表
        """
        property_overview = []
        shell_properties = []
        bar_properties = []
        for pid, prop in self.bdf.properties.items():
            if prop.type == "PSHELL":
                property_overview.append((pid, "SHELL"))
                shell_properties.append((pid, prop.t, prop.nsm, 0))

            elif prop.type == "PBAR":
                property_overview.append((pid, "BAR 3D"))
                bar_properties.append((pid, prop.A, 0, 0, prop.j, prop.i1, prop.i2, 0, 0, 0, prop.nsm))

            elif prop.type == "PBEAM":
                property_overview.append((pid, "BEAM 3D"))
                if isinstance(prop.A, np.ndarray):
                    # TODO: 这里假设 A 是一个列表，取第一个元素。根据实际情况调整。梁是两个节点
                    bar_properties.append((pid, prop.A[0], 0, 0, prop.j[0], prop.i1[0], prop.i2[0], 0, 0, 0, prop.nsm[0]))
                else:
                    bar_properties.append((pid, prop.A, 0, 0, prop.j, prop.i1, prop.i2, 0, 0, 0, prop.nsm))

            else:
                print("other prop type:", prop.type)

        parse_results["property_overview"] = property_overview
        parse_results["shell_properties"] = shell_properties
        parse_results["bar_properties"] = bar_properties

        """
        边界条件
        """
        boundary = []
        spc_cases = self.bdf.spcs.keys()
        for spc_id in spc_cases:
            spc_case = self.bdf.spcs[spc_id]
            for spc in spc_case:
                for node in spc.nodes:
                    enforced = [None, None, None, None, None, None]
                    for com in spc.components:
                        if com == 1:
                            enforced[0] = spc.value
                        elif com == 2:
                            enforced[1] = spc.value
                        elif com == 3:
                            enforced[2] = spc.value
                        elif com == 4:
                            enforced[3] = spc.value
                        elif com == 5:
                            enforced[4] = spc.value
                        elif com == 6:
                            enforced[5] = spc.value
                    boundary.append((node, enforced))

        parse_results["boundary"] = boundary

        return parse_results


if __name__ == '__main__':
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/powertrain2.bdf")
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/dengzi.bdf")
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/mid2.bdf")
    # bdf = BDFParser(r"../PyModel2JsonDataFolder/model/bdf/static_2.bdf")
    bdf = BDFParser("D:\\WorkSpace\\WebThreeJS\\PyModel2JsonDataFolder\\model\\bdf\\powertrain.bdf")
    bdf.parse()
    # bdf.WriteVtuFile(r"../PyModel2JsonDataFolder/output/powertrain.vtu")
    bdf.GetDatabaseData()
