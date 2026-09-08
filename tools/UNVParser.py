import os
import time
import json
import re

from FemNode import FemNode
from MeshElementFactory import MeshElementFactory
from BaseParser import *

from TransformModel import *
from PySide2.QtGui import QVector3D


def default_converter(o):
    if isinstance(o, np.integer):
        return int(o)
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def parse_document(text):
    # 定义正则表达式模式
    pattern = r'name="([^"]*)", target="([^"]*)", type="([^"]*)", varlabels="([^"]*)"'

    # 使用正则表达式查找匹配的内容
    match = re.search(pattern, text)
    if match:
        # 提取所需的值并转换为小写
        name = match.group(1).lower()
        target = match.group(2).lower()
        type_ = match.group(3).lower()
        varlabels = match.group(4).lower()
        return name, target, type_, varlabels
    else:
        return None, None, None, None


class UNVParser(BaseParser):
    def __init__(self, input_path):
        super().__init__()
        self.unv_path = input_path
        self.global_node_hash = {}
        self.solid_nodes = []
        self.solid_eles = []
        self.bar_nodes = []
        self.bar_eles = []
        self.solid_node_hash = {}
        self.bar_node_hash = {}
        self.stress = {}
        self.MisesStressFatigueUsageNumber = {}
        self.MisesStressFatigueUsageFactor = {}
        self.MaxShearStressFatigueUsageNumber = {}
        self.displacement = {}
        self.result_set = []
        self.result_set_name = []

    def ParseFileWithBar(self):
        if not os.path.exists(self.unv_path):
            print("unv file doesn't exist")
            return

        with open(self.unv_path, "r") as unv_file:
            node_index = 0
            line = unv_file.readline()

            while line:
                line = line.strip()
                if line.startswith("{ Node"):
                    iter_line = unv_file.readline().strip()
                    iter_lines = iter_line[1:-2].split(",")
                    node_count = int(iter_lines[0])
                    print(f"node count is: {node_count}")
                    line = unv_file.readline().strip()

                    while not line.startswith("}"):
                        line_data = line[1:-2].split(",")
                        grid_id = int(line_data[0])
                        self.global_node_hash[grid_id] = node_index
                        node_index += 1
                        x = float(line_data[1])
                        y = float(line_data[2])
                        z = float(line_data[3])
                        nd = FemNode(grid_id, x, y, z)
                        self.nodes.append(nd)
                        line = unv_file.readline().strip()

                elif line.startswith("{ Element"):
                    element_count = int(unv_file.readline().strip()[1:-2])
                    print(f"element count is: {element_count}")
                    line = unv_file.readline().strip()

                    while not line.startswith("}"):
                        line_data = line[1:-1].split(",")
                        if line_data[-1].__contains__(";"):
                            line_data[-1] = line_data[-1][:-1]
                        ele_id = int(line_data[0])
                        ele_type = int(line_data[1])
                        nodes = []
                        iter_ele, is_solid = MeshElementFactory.CreateElement(e_type=ele_type)

                        if iter_ele:
                            for i in range(iter_ele.nodesCount, 0, -1):
                                index = len(line_data) - i
                                node_id = int(line_data[index])
                                nodes.append(node_id)

                            if len(set(nodes)) != iter_ele.nodesCount:
                                degrade_type = iter_ele.degrade_ele
                                degrade_indices = iter_ele.degrade_rv_index
                                iter_ele = MeshElementFactory.CreateElement(degrade_type, ele_id)

                                for idx in degrade_indices:
                                    nodes.pop(idx)

                            iter_ele.setFaces(nodes)
                            if is_solid:
                                self.solid_nodes.extend(nodes)
                                self.solid_eles.append(iter_ele)
                            else:
                                self.bar_nodes.extend(nodes)
                                self.bar_eles.append(iter_ele)

                        line = unv_file.readline().strip()

                elif line.__contains__("{ ResultSet"):
                    line_str = unv_file.readline()
                    name, target, type_, varlabels = parse_document(line_str)
                    if not name.__contains__("rotation"):
                        if type_.lower() == "vector":
                            line = unv_file.readline().strip()
                            varlabels = varlabels.split("|")
                            iter_set_x = {}
                            iter_set_y = {}
                            iter_set_z = {}
                            iter_set_sum = {}
                            while not line.startswith("}"):
                                line_data = line[1:-1].split(",")
                                sum_dis = (
                                                  float(line_data[1]) ** 2
                                                  + float(line_data[2]) ** 2
                                                  + float(line_data[3]) ** 2
                                          ) ** 0.5
                                iter_set_x[int(line_data[0])] = float(line_data[1])
                                iter_set_y[int(line_data[0])] = float(line_data[2])
                                iter_set_z[int(line_data[0])] = float(line_data[3])
                                iter_set_sum[int(line_data[0])] = sum_dis
                                line = unv_file.readline().strip()

                            if len(iter_set_x) != 0:
                                # self.result_set.extend([iter_set_x, iter_set_y, iter_set_z, iter_set_sum])
                                # self.result_set_name.extend([name + " " + ii for ii in varlabels])
                                self.result_set.extend([iter_set_sum])
                                self.result_set_name.append(name + " sum")
                                self.except_value.append(sum_dis)
                                break

                    elif type_.lower() == "tensor6":
                        line = unv_file.readline().strip()

                        iter_set = {}
                        while not line.startswith("}"):
                            line_data = line[1:-1].split(",")
                            node_res = [
                                float(line_data[1]),
                                float(line_data[2]),
                                float(line_data[3]),
                                float(line_data[4]),
                                float(line_data[5]),
                                float(line_data[6]),
                            ]
                            sum_stress = (
                                    (node_res[0] - node_res[1]) ** 2
                                    + (node_res[1] - node_res[2]) ** 2
                                    + (node_res[2] - node_res[0]) ** 2
                                    + 6
                                    * (
                                            node_res[3] ** 2
                                            + node_res[4] ** 2
                                            + node_res[5] ** 2
                                    )
                            )
                            iter_set[int(line_data[0])] = (0.5 * sum_stress) ** 0.5
                            self.except_value.append((0.5 * sum_stress) ** 0.5)

                            line = unv_file.readline().strip()

                        if len(iter_set) != 0:
                            self.result_set.append(iter_set)
                            self.result_set_name.append(name)

                    elif type_.lower() == "scalar":
                        line = unv_file.readline().strip()

                        iter_set = {}
                        while not line.startswith("}"):
                            line_data = line[1:-1].split(",")
                            iter_set[int(line_data[0])] = float(line_data[1])
                            self.except_value.append(float(line_data[1]))

                            line = unv_file.readline().strip()

                        if len(iter_set) != 0:
                            self.result_set.append(iter_set)
                            self.result_set_name.append(name)

                    else:
                        line = unv_file.readline().strip()

                else:
                    line = unv_file.readline().strip()

            """
            重新排序编号
            """
            self.solid_nodes = np.sort(list(set(self.solid_nodes)))
            self.bar_nodes = np.sort(list(set(self.bar_nodes)))

            """
            创建字典, 用于写入json
            """
            self.solid_node_hash = {key: value for value, key in enumerate(self.solid_nodes)}
            self.bar_node_hash = {key: value for value, key in enumerate(self.bar_nodes)}

            """
            计算剔除值, 将该值替换为null, 希望简单的加1不会超过int的最大范围
            """
            self.except_value = self.CalculateExceptValue()

    def WriteSolidPart(self, file_path):
        json_data = {"data": {},
                     "metadata": {"type": "BufferGeometry", "version": 4},
                     "name": "XiGuiChe_Shell",
                     "type": "BufferGeometry",
                     "userData": {},
                     "uuid": "946A1C8F-19EF-1CA6-8B63-B4745CB83B4B"}

        json_data["data"]["attributes"] = {}
        json_data["data"]["index"] = {}

        json_data["data"]["interleavedBuffers"] = {"Result": {"buffer": "1CE1796E-3F05-2251-2DCC-2C2D14F13B3F",
                                                              "stride": 4,
                                                              "type": "Float32Array",
                                                              "uuid": "FF275754-4711-AC0A-21EA-69246C54D02C"}}

        json_data["data"]["arrayBuffers"] = {"1CE1796E-3F05-2251-2DCC-2C2D14F13B3F": []}

        res_list = []
        for n_id in self.solid_nodes:
            try:
                res_list.extend([self.displacement[n_id][-1],
                                 self.stress[n_id],
                                 # self.MisesStressFatigueUsageNumber[n_id],
                                 # self.MisesStressFatigueUsageFactor[n_id],
                                 0,
                                 0,
                                 0
                                 ])
            except KeyError as e:
                print(e)
                res_list.extend([0, 0, 0, 0, 0])
        json_data["data"]["arrayBuffers"]["1CE1796E-3F05-2251-2DCC-2C2D14F13B3F"] = res_list
        json_data["data"]["attributes"]["position"] = {"array": [], "itemSize": 3, "type": "Float32Array"}
        json_data["data"]["index"] = {"array": [], "itemSize": 1, "type": "Uint32Array"}
        json_data["data"]["content"] = ["U", "Stress", "MisesStressFatigueUsageNumber", "MisesStressFatigueUsageFactor"]

        node_xyz = []
        for node_id in self.solid_nodes:
            node = self.nodes[self.global_node_hash[node_id]]
            node_coord = node.GetNodeCoord()
            if True:
                n_vector = QVector3D(*[198.905, 20.899, 0.033])
                point0 = QVector3D(*[-9136.244, -12021.603, 68499.99])
                node_coord = RotateByAxis(n_vector, point0, 40, node_coord)
            node_xyz.extend(node_coord)
        json_data["data"]["attributes"]["position"]["array"] = node_xyz

        tri_angles = []
        for ele in self.solid_eles:
            triangles = ele.getAllTriangles()
            for tri in triangles:
                tri_angles.append(self.solid_node_hash[tri[0]])
                tri_angles.append(self.solid_node_hash[tri[1]])
                tri_angles.append(self.solid_node_hash[tri[2]])

        json_data["data"]["index"]["array"] = tri_angles
        with open(file_path, 'w') as fp:
            json.dump(json_data, fp, indent=4)

    def WriteBarPart(self, file_path):
        json_data = {"data": {},
                     "metadata": {"type": "BufferGeometry", "version": 4},
                     "name": "XiGuiChe_Line",
                     "type": "BufferGeometry",
                     "userData": {},
                     "uuid": "2D8ED466-1128-7C3E-8331-C2DB73EBE990"}

        json_data["data"]["attributes"] = {}
        json_data["data"]["index"] = {}

        json_data["data"]["interleavedBuffers"] = {"Result": {"buffer": "89496831-850B-2380-7E58-76C449018E5F",
                                                              "stride": 5,
                                                              "type": "Float32Array",
                                                              "uuid": "CC9420C2-46C6-4A83-4F16-E5EB97941861"}}

        json_data["data"]["arrayBuffers"] = {"89496831-850B-2380-7E58-76C449018E5F": []}

        res_list = []
        for _ in self.bar_nodes:
            res_list.extend([0, 0, 0, 0, 0])
        json_data["data"]["arrayBuffers"]["89496831-850B-2380-7E58-76C449018E5F"] = res_list
        json_data["data"]["attributes"]["center"] = {"array": [0, 0, 0], "type": "Float32Array"}
        json_data["data"]["attributes"]["position"] = {"array": [], "itemSize": 3, "type": "Float32Array"}
        json_data["data"]["attributes"]["quaternion"] = {"array": [1, 0, 0, 0], "type": "Float32Array"}
        json_data["data"]["index"] = {"array": [], "itemSize": 1, "type": "Uint32Array"}

        node_xyz = []
        for node_id in self.bar_nodes:
            node = self.nodes[self.global_node_hash[node_id]]
            node_xyz.extend(node.GetNodeCoord())

        json_data["data"]["attributes"]["position"]["array"] = node_xyz

        lines = []
        for bar in self.bar_eles:
            points = bar.getAllTriangles()
            lines.extend([self.bar_node_hash[points[0]], self.bar_node_hash[points[1]]])

        json_data["data"]["index"]["array"] = lines
        with open(file_path, 'w') as fp:
            json.dump(json_data, fp, indent=4)

    def ParseFileWithoutBar(self):
        if not os.path.exists(self.unv_path):
            print("unv file doesn't exist")
            return

        with open(self.unv_path, "r") as unv_file:
            node_index = 1
            instance = {}
            line = unv_file.readline()

            while line:
                line = line.strip()

                if line.startswith("{ Node"):
                    iter_line = unv_file.readline().strip()
                    iter_lines = iter_line[1:-2].split(",")
                    node_count = int(iter_lines[0])
                    print(f"node count is: {node_count}")
                    line = unv_file.readline().strip()

                    while not line.startswith("}"):
                        line_data = line[1:-2].split(",")
                        grid_id = int(line_data[0])
                        self.global_node_hash[grid_id] = node_index
                        node_index += 1
                        x = float(line_data[1])
                        y = float(line_data[2])
                        z = float(line_data[3])
                        grid = {"id": grid_id, "x": x, "y": y, "z": z}
                        instance.setdefault("grids", []).append(grid)
                        line = unv_file.readline().strip()

                elif line.startswith("{ Element"):
                    element_count = int(unv_file.readline().strip()[1:-2])
                    print(f"element count is: {element_count}")
                    line = unv_file.readline().strip()

                    while not line.startswith("}"):
                        line_data = line[1:-1].split(",")
                        ele_id = int(line_data[0])

                        ele_type = int(line_data[1])
                        nodes = []
                        iter_ele, is_solid = MeshElementFactory.CreateElement(e_type=ele_type)

                        for i in range(iter_ele.nodesCount, 0, -1):
                            index = len(line_data) - i
                            node_id = int(line_data[index])
                            nodes.append(self.global_node_hash[node_id])

                        if len(set(nodes)) != iter_ele.nodesCount:
                            degrade_type = iter_ele.degrade_ele
                            degrade_indices = iter_ele.degrade_rv_index
                            iter_ele, is_solid = MeshElementFactory.CreateElement(degrade_type, ele_id)

                            for idx in degrade_indices:
                                nodes.pop(idx)

                        iter_ele.set_faces(nodes)
                        instance.setdefault("elements", []).append(iter_ele)
                        line = unv_file.readline().strip()

                elif line.startswith("{ ResultSet"):
                    line_data = unv_file.readline().strip()[1:-2].split(",")

                    if "Static Displacement" in line_data[0]:
                        print("***************")
                        print(line_data)
                        line = unv_file.readline().strip()

                        while not line.startswith("}"):
                            line_data = line[1:-2].split(",")
                            node_res = [
                                float(line_data[1]),
                                float(line_data[2]),
                                float(line_data[3]),
                                (
                                        float(line_data[1]) ** 2
                                        + float(line_data[2]) ** 2
                                        + float(line_data[3]) ** 2
                                ) ** 0.5,
                            ]
                            instance.setdefault("static_displacement", []).append(node_res)
                            line = unv_file.readline().strip()

                    elif "Static Stress" in line_data[0]:
                        print("***************")
                        print(line_data)
                        line = unv_file.readline().strip()

                        while not line.startswith("}"):
                            line_data = line[1:-2].split(",")
                            node_res = list([
                                float(line_data[1]),
                                float(line_data[2]),
                                float(line_data[3]),
                                float(line_data[4]),
                                float(line_data[5]),
                                float(line_data[6]),
                            ])
                            sum_stress = (
                                    (node_res[0] - node_res[1]) ** 2
                                    + (node_res[1] - node_res[2]) ** 2
                                    + (node_res[2] - node_res[0]) ** 2
                                    + 6
                                    * (
                                            node_res[3] ** 2
                                            + node_res[4] ** 2
                                            + node_res[5] ** 2
                                    )
                            )
                            node_res.append((0.5 * sum_stress) ** 0.5)

                            line = unv_file.readline().strip()

                else:
                    line = unv_file.readline().strip()

    def WriteWRZJFormat(self, file_path, file_name):
        """
        无人智境
        """
        displacement_res = []
        stress_res = []
        for n_id in self.solid_nodes:
            displacement_res.append(self.displacement[n_id][-1])
            stress_res.append(self.stress[n_id])

        node_xyz = []
        for node_id in self.solid_nodes:
            node = self.nodes[self.global_node_hash[node_id]]
            node_coord = node.GetNodeCoord()
            if True:
                n_vector = QVector3D(*[198.905, 20.899, 0.033])
                point0 = QVector3D(*[-9136.244, -12021.603, 68499.99])
                node_coord = RotateByAxis(n_vector, point0, 270, node_coord)
            node_xyz.extend(node_coord)

        tri_angles = []
        for ele in self.solid_eles:
            triangles = ele.getAllTriangles()
            for tri in triangles:
                tri_angles.append(self.solid_node_hash[tri[0]])
                tri_angles.append(self.solid_node_hash[tri[1]])
                tri_angles.append(self.solid_node_hash[tri[2]])

        json_data = {"filename": file_name,
                     "data": {},
                     "metadata": {"type": "BufferGeometry", "version": 4},
                     "type": "BufferGeometry",
                     "uuid": "AF2ADB07-FBC5-4BAE-AD60-123456789ABC"}

        json_data["data"]["index"] = tri_angles
        json_data["data"]["render"] = 1
        json_data["data"]["fresh"] = 1
        json_data["data"]["attributes"] = {
            "displacement": {"array": displacement_res, "itemSize": 1, "type": "Float32Array"},
            "mises": {"array": stress_res, "itemSize": 1, "type": "Float32Array"},
            "position": {"array": node_xyz, "itemSize": 3, "type": "Float32Array"},
            "rotate": {"origin": [580.918, 2050.0, 25322.7],
                       "type": "Float32Array",
                       "rotateaxis": [0, 0, 1],
                       "rotatetheta": 180},
            "translate": [0, 0, 200]
        }

        with open(file_path, 'w') as fp:
            json.dump(json_data, fp)


if __name__ == "__main__":
    time_begin = time.time()
    # up = UNVParser("./model/unv/xiguiche/xiguiche_ele.unv")
    up = UNVParser("./model/unv/guodian/B5_TRY_REGID.unv")
    # up = UNVParser("./model/unv/meishuai/2300t.unv")
    # up = UNVParser("./model/unv/zhaopengqiang/lingjian.unv")
    up.ParseFileWithBar()
    # up.WriteBarPart("modelLine.json")
    # up.WriteSolidPart("D:/WorkSpace/WebThreeJS/WebFEM/LiveServer/user/models/modelShellTest.json")
    up.WriteWRZJFormat("./output/B5_f270.json", "B5")
    # up = UNVParser("")
    # RotateByAxis(1, 120, np.array([1, 2, 3]))
    print("{:.3f} seconds".format(time.time() - time_begin))
