# -*- coding: utf-8 -*-
import json
import numpy as np
from pyNastran.op2.op2_geom import read_op2_geom
from MeshElementFactory import MeshElementFactory
from FemNode import FemNode
from MeshCleaner import RotateByAxisScipy
import meshio


def default_converter(o):
    """用于 json.dump 的 numpy 类型转换"""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


class Op2Parser(object):
    """
    解析OP2文件
    """

    def __init__(self, op2_path):
        self.nodes = []
        self.elements = []
        self.node_hash = {}
        self.op2_path = op2_path

    def ParseFile(self):
        op2 = read_op2_geom(self.op2_path, debug=False)
        if not op2.nodes:
            raise ValueError("OP2 文件中未包含几何节点信息（nodes 为空）")

        node_ids = op2.nodes.keys()
        for nid in node_ids:
            node = op2.nodes[nid]
            if hasattr(node, "get_position"):
                x, y, z = node.get_position()
            else:
                x, y, z = node.xyz
            self.node_hash[nid] = len(self.nodes)
            self.nodes.append(FemNode(nid, x, y, z))

        for eid, elem in op2.elements.items():
            elem_type = elem.type
            elem_node_ids = elem.node_ids

            element, _ = MeshElementFactory.CreateElement(
                e_type=elem_type,
                e_id=eid,
                fem_software="NASTRAN",
            )
            if element is not None:
                element.setFaces(elem_node_ids)
                self.elements.append(element)

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

        for iter_ele in self.elements:
            iter_relation = [self.node_hash[ii] for ii in iter_ele.nodeIds]
            ele_type = iter_ele.vtu_type
            if all_eles.__contains__(ele_type):
                all_eles[str(ele_type)].append(iter_relation)
            else:
                all_eles[str(ele_type)] = [iter_relation]

        meshio.write_points_cells(
            filename=vtu_path,
            points=node_coords,
            cells=all_eles,
            # point_data=node_res,
            # cell_data=cell_data,
            # field_data=field_data
        )

    def WriteSpecialModal(self, vtu_path, mode_number):
        """
        将模型写入vtu文件，使用paraview显示
        :param vtu_path:
        :param mode_number:
        :return:
        """
        op2 = read_op2_geom(self.op2_path, debug=False)
        coords = []
        for nd in self.nodes:
            coords.append(nd.coord)
        node_coords = np.asarray(coords)
        all_eles = {}

        for iter_ele in self.elements:
            iter_relation = [self.node_hash[ii] for ii in iter_ele.nodeIds]
            ele_type = iter_ele.vtu_type
            if all_eles.__contains__(ele_type):
                all_eles[str(ele_type)].append(iter_relation)
            else:
                all_eles[str(ele_type)] = [iter_relation]

        node_num = len(self.nodes)

        if hasattr(op2, "eigenvectors") and op2.eigenvectors:
            isubcase = sorted(op2.eigenvectors.keys())[0]
            eigen_data = op2.eigenvectors[isubcase]

            modes = eigen_data.modes  # 模态号数组，如 [1,2,3,...]
            idx_candidates = np.where(modes == mode_number)[0]
            if len(idx_candidates) == 0:
                print(f"警告：找不到第 {mode_number} 阶模态，改用第 1 个模态")
                mode_idx = 0
            else:
                mode_idx = int(idx_candidates[0])

            mode_shapes_full = eigen_data.data[mode_idx, :, :]  # 当前模态的所有节点 6 自由度
            node_coords += mode_shapes_full[:, :3] * 100

            # 频率这里不强求，一些版本只知道特征值（eigns）
            freq = None
            try:
                if hasattr(eigen_data, "eigns"):
                    eigvals = eigen_data.eigns
                    if len(eigvals) > mode_idx:
                        eig = eigvals[mode_idx]
                        if eig > 0.0:
                            freq = float(np.sqrt(eig) / (2.0 * np.pi))
            except Exception:
                pass

            if freq is None:
                print(f"使用第{mode_number}阶模态（内部索引 {mode_idx}）")
            else:
                print(f"使用第{mode_number}阶模态（内部索引 {mode_idx}），频率约 {freq:.4f} Hz")
        else:
            print("警告：未找到模态结果，使用零位移")

        if vtu_path != "":
            meshio.write_points_cells(
                filename=vtu_path,
                points=node_coords,
                cells=all_eles,
                # point_data=node_res,
                # cell_data=cell_data,
                # field_data=field_data
            )
        return self.nodes, node_coords, self.elements


def ParseOP2AndWriteToJson(
        op2_path,
        output_json_path,
        mode_number=1,
        vtk_path=None,
        transform=False,
        deform=False,
        no_output_json=True
):
    """
    解析 Nastran OP2 文件，输出：
      1）Three.js BufferGeometry JSON；
      2）VTK 网格（使用 meshio），便于用 ParaView 等检查结果。

    :param op2_path: OP2 文件路径
    :param output_json_path: 输出 JSON 文件路径
    :param mode_number: 模态阶数（从 1 开始）
    :param vtk_path: 输出 VTK 文件路径（不传则与 JSON 同名、后缀改为 .vtk）
    :param transform: 是否进行坐标变换
    :param deform: 是否变相
    :param no_output_json: 不输出json, 返回节点和单元
    """

    # 使用 read_op2_geom 读取几何 + 结果
    op2 = read_op2_geom(op2_path, debug=False)
    print("开始解析OP2文件...")

    # ===========================
    # 1. 解析节点坐标
    # ===========================
    if not op2.nodes:
        # OP2 里如果没有显式 GEOM1/GPDT 等，nodes 可能为空
        raise ValueError("OP2 文件中未包含几何节点信息（nodes 为空）")

    # 按 nid 排序，保证节点顺序稳定
    node_ids = sorted(op2.nodes.keys())
    node_coords = []
    for nid in node_ids:
        node = op2.nodes[nid]
        # BDF Grid 通常有 get_position() 方法；没有的话用 xyz
        if hasattr(node, "get_position"):
            x, y, z = node.get_position()
        else:
            x, y, z = node.xyz
        node_coords.append([x, y, z])
    node_coords = np.array(node_coords, dtype=float)

    node_num = len(node_coords)
    node_id_to_index = {node_id: i for i, node_id in enumerate(node_ids)}
    print(f"解析到 {node_num} 个节点")

    # ===========================
    # 2. 解析单元信息
    # ===========================
    all_elements = []
    if hasattr(op2, "elements") and op2.elements:
        element_count = 0
        for eid, elem in op2.elements.items():
            elem_type = elem.type  # 如 'CQUAD4', 'CTRIA3', 'CTETRA' 等
            elem_node_ids = elem.node_ids  # 节点 ID 列表

            element, _ = MeshElementFactory.CreateElement(
                e_type=elem_type,
                e_id=eid,
                fem_software="NASTRAN",
            )
            if element is not None:
                element.setFaces(elem_node_ids)
                all_elements.append(element)
                element_count += 1

        print(f"解析到 {element_count} 个单元")
    else:
        print("警告：未找到单元信息")

    # ===========================
    # 3. 解析模态结果（位移）
    # ===========================
    displacement = np.zeros((node_num,), dtype=float)
    modal_dx = np.zeros((node_num,), dtype=float)
    modal_dy = np.zeros((node_num,), dtype=float)
    modal_dz = np.zeros((node_num,), dtype=float)
    deform_xyz = np.zeros((node_num * 3,), dtype=float)
    mises = np.zeros((node_num,), dtype=float)  # 模态分析通常无应力，这里统一设 0

    if hasattr(op2, "eigenvectors") and op2.eigenvectors:
        # 一般只有一个子工况，取第一个 isubcase
        isubcase = sorted(op2.eigenvectors.keys())[0]
        eigen_data = op2.eigenvectors[isubcase]

        modes = eigen_data.modes  # 模态号数组，如 [1,2,3,...]
        # 先按模态号查找
        idx_candidates = np.where(modes == mode_number)[0]
        if len(idx_candidates) == 0:
            print(f"警告：找不到第 {mode_number} 阶模态，改用第 1 个模态")
            mode_idx = 0
        else:
            mode_idx = int(idx_candidates[0])

        # data 结构：[nmode, nnodes_res, 6] -> [tx, ty, tz, rx, ry, rz]
        mode_shapes_full = eigen_data.data[mode_idx, :, :]  # 当前模态的所有节点 6 自由度
        # 与结果对应的节点 ID（顺序与 data 第二维一致）
        eigen_node_ids = eigen_data.node_gridtype[:, 0]
        nid_to_res_index = {int(nid): i for i, nid in enumerate(eigen_node_ids)}

        # 将结果映射到我们自己的 node_ids 顺序上
        for i, nid in enumerate(node_ids):
            j = nid_to_res_index.get(int(nid))
            if j is None:
                # 某些结果里可能有 SPOINT 等，不在几何节点中；跳过即可
                continue
            modal_dx[i] = mode_shapes_full[j, 0]
            modal_dy[i] = mode_shapes_full[j, 1]
            modal_dz[i] = mode_shapes_full[j, 2]
            deform_xyz[i * 3] = modal_dx[i]
            deform_xyz[i * 3 + 1] = modal_dy[i]
            deform_xyz[i * 3 + 2] = modal_dz[i]

        displacement = np.sqrt(modal_dx ** 2 + modal_dy ** 2 + modal_dz ** 2)

        # 频率这里不强求，一些版本只知道特征值（eigns）
        freq = None
        try:
            if hasattr(eigen_data, "eigns"):
                eigvals = eigen_data.eigns
                if len(eigvals) > mode_idx:
                    eig = eigvals[mode_idx]
                    if eig > 0.0:
                        freq = float(np.sqrt(eig) / (2.0 * np.pi))
        except Exception:
            pass

        if freq is None:
            print(f"使用第{mode_number}阶模态（内部索引 {mode_idx}）")
        else:
            print(f"使用第{mode_number}阶模态（内部索引 {mode_idx}），频率约 {freq:.4f} Hz")
    else:
        print("警告：未找到模态结果，使用零位移")

    if no_output_json:
        return node_coords, all_elements

    # ===========================
    # 4. 组装 Three.js BufferGeometry JSON
    # ===========================
    json_data = {
        "data": {},
        "metadata": {"type": "BufferGeometry", "version": 4},
        "name": "XiGuiChe_Shell",
        "type": "BufferGeometry",
        "userData": {},
        "uuid": "946A1C8F-19EF-1CA6-8B63-B4745CB83B4B",
    }

    json_data["data"]["attributes"] = {}
    json_data["data"]["index"] = {}

    # 结果使用 interleaved buffer：dx,dy,dz, |u|, mises
    json_data["data"]["interleavedBuffers"] = {
        "Result": {
            "buffer": "1CE1796E-3F05-2251-2DCC-2C2D14F13B3F",
            "stride": 1,
            "type": "Float32Array",
            "uuid": "FF275754-4711-AC0A-21EA-69246C54D02C",
        }
    }

    json_data["data"]["arrayBuffers"] = {
        "1CE1796E-3F05-2251-2DCC-2C2D14F13B3F": []
    }

    # 填充结果数组
    res_list = []
    for ii in range(node_num):
        res_list.extend(
            [
                # float(modal_dx[ii]),
                # float(modal_dy[ii]),
                # float(modal_dz[ii]),
                float(displacement[ii]),
                # float(mises[ii]),
            ]
        )

    json_data["data"]["arrayBuffers"]["1CE1796E-3F05-2251-2DCC-2C2D14F13B3F"] = res_list
    json_data["data"]["max_min"] = {"maxValue": np.max(displacement), "minValue": np.min(displacement)}
    json_data["data"]["content"] = ["displacement"]

    # 节点坐标属性
    if transform:
        node_coords = RotateByAxisScipy(n_vector=[1, 0, 0],
                                        point_o=(0, 0, 0),
                                        angle_degrees=180,
                                        xyz=node_coords,
                                        shift_v=np.array([0.35, 0, -0.4]))

    node_xyz = []
    for coord in node_coords:
        node_xyz.extend([float(coord[0]), float(coord[1]), float(coord[2])])

    if not deform:
        json_data["data"]["attributes"]["position"] = {
            "array": node_xyz,
            "itemSize": 3,
            "type": "Float32Array",
        }
    else:
        node_xyz = (node_xyz - deform_xyz / 11.63).tolist()
        json_data["data"]["attributes"]["position"] = {
            "array": node_xyz,
            "itemSize": 3,
            "type": "Float32Array",
        }

    # ===========================
    # 5. 构建三角形索引（同时为 meshio 准备 cells）
    # ===========================
    tri_indices = []  # 给 Three.js，用扁平一维数组
    tri_cells = []  # 给 meshio，用 [ [i0,i1,i2], ... ]
    all_edges = []

    for ele in all_elements:
        triangles = ele.getAllTriangles()  # 由 MeshElementFactory 定义
        all_edges.extend(ele.getEdges())
        for tri in triangles:
            cell = []
            for nid in tri:
                idx = node_id_to_index.get(nid)
                if idx is None:
                    # 兜底：假设节点 ID 从 1 开始，ID-1 作索引
                    idx = int(nid) - 1
                idx = int(idx)
                cell.append(idx)
                tri_indices.append(idx)
            tri_cells.append(cell)

    json_data["data"]["index"] = {
        "array": tri_indices,
        "itemSize": 1,
        "type": "Uint32Array",
    }
    json_data["userData"]["edgeIndex"] = [node_id_to_index[ii] for ii in all_edges]

    # ===========================
    # 6. 写入 JSON 文件
    # ===========================
    with open(output_json_path, "w", encoding="utf-8") as fp:
        json.dump(json_data, fp, indent=4, default=default_converter, ensure_ascii=False)

    print(f"结果已保存到: {output_json_path}")
    print(f"节点数: {node_num}")
    print(f"三角形面片数: {len(tri_indices) // 3}")

    # ===========================
    # 7. 使用 meshio 写 VTK 网格
    # ===========================
    if tri_cells:
        tri_cells_np = np.array(tri_cells, dtype=np.int32)
        cells = [("triangle", tri_cells_np)]

        # 点数据：模态位移分量 & 合位移 & （占位）mises
        displacement_array = np.array([modal_dx, modal_dy, modal_dz]).T
        point_data = {
            "disp": displacement_array,
            "mises": mises,
        }

        vtk_mesh = meshio.Mesh(
            points=node_coords,
            cells=cells,
            point_data=point_data,
        )

        if vtk_path is None:
            # 如果没有指定 vtk 路径，就用 json 同名改后缀
            if "." in output_json_path:
                base = output_json_path.rsplit(".", 1)[0]
            else:
                base = output_json_path
            vtk_path = base + ".vtk"

        meshio.write(vtk_path, vtk_mesh)
        print(f"VTK 网格已保存到: {vtk_path}")
    else:
        print("警告：没有三角形单元，未输出 VTK 文件")


def ParseOP2WithMultipleModes(op2_path, output_base_path, num_modes=3):
    """
    解析多个模态，分别输出多个 JSON + VTK 文件

    :param op2_path: OP2 文件路径
    :param output_base_path: 输出文件基础路径（不带扩展名）
    :param num_modes: 要解析的模态数量
    """
    for mode in range(1, num_modes + 1):
        output_json_path = f"{output_base_path}_mode_{mode}.json"
        output_vtk_path = f"{output_base_path}_mode_{mode}.vtk"
        try:
            ParseOP2AndWriteToJson(
                op2_path,
                output_json_path,
                mode_number=mode,
                vtk_path=output_vtk_path,
            )
            print(f"第{mode}阶模态解析完成")
        except Exception as e:
            print(f"解析第{mode}阶模态时出错: {e}")


if __name__ == "__main__":
    # op2_file = "model/op2/modal_sol103.op2"
    # op2_file = "model/op2/fem24.op2"
    # op2_file = "model/bdf/out-final.op2"
    op2_file = fr"D:\WorkSpace\ThreeJS\PyModel2JsonDataFolder\model\nastran\op2\powertrain.op2"
    output_json = fr"D:\WorkSpace\ThreeJS\PyModel2JsonDataFolder\model\nastran\op2\fem_undeform.json"
    output_vtk = fr"D:\WorkSpace\ThreeJS\PyModel2JsonDataFolder\model\nastran\op2\nastran_model-7.vtk"
    output_vtu = fr"D:\WorkSpace\ThreeJS\PyModel2JsonDataFolder\model\nastran\op2\powertrain.vtu"

    # ParseOP2ToVTU(op2_file, output_vtu)
    # ParseOP2AndWriteToJson(
    #     op2_file,
    #     output_json_path=output_json,
    #     mode_number=8,
    #     vtk_path=output_vtk,
    #     # transform=True,
    #     # deform=True
    #     no_output_json=False
    # )
    op2_parser = Op2Parser(op2_file)
    op2_parser.ParseFile()
    # op2_parser.WriteVtuFile(output_vtu)
    op2_parser.WriteSpecialModal(output_vtu, 2)
