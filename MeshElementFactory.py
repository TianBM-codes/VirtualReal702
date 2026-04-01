#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
from abc import ABCMeta

from CustomException import *
import numpy as np
import abc


def QuaFace2Triangles(nodes, id_order):
    triangles = [[nodes[id_order[0]], nodes[id_order[1]], nodes[id_order[3]]],
                 [nodes[id_order[1]], nodes[id_order[2]], nodes[id_order[4]]],
                 [nodes[id_order[1]], nodes[id_order[6]], nodes[id_order[3]]],
                 [nodes[id_order[1]], nodes[id_order[4]], nodes[id_order[6]]],
                 [nodes[id_order[3]], nodes[id_order[6]], nodes[id_order[5]]],
                 [nodes[id_order[4]], nodes[id_order[7]], nodes[id_order[6]]]]
    return triangles


class MeshElementFactory:
    """
    Reference:
    1. https://abaqus-docs.mit.edu/2017/English/SIMACAEELMRefMap/simaelm-c-shellelem.htm
    """

    @staticmethod
    def CreateElement(e_type, e_id=-1, opt=None, use_low_order=False,
                      except_ele_type=None,
                      except_ele_id=None,
                      fem_software="ANSYS"):
        """
        静态函数, 用于返回
        @param e_type: 单元类型，这里包含了Abaqus、Nastran和Ansys的
        @param e_id: 初始化单元需要单元ID
        @param opt: 附加参数, 比如181可能是3节点壳也可能是4节点壳, solid45可能是8节点也可能是4节点
        @param use_low_order: 是否直接使用低阶单元，而不用高阶单元
        @param except_ele_type:
        @param except_ele_id:
        @param fem_software:
        :return: 单元和节点个数
        """
        if fem_software == "ANSYS":
            if except_ele_id is None:
                except_ele_id = []
            if except_ele_type is None:
                except_ele_type = []

            if e_type in except_ele_type:
                return None, None
            if e_id in except_ele_id:
                return None, None

            if e_type in [185, 45]:
                if opt == 8:
                    return MeshC3D8(e_id), True
                elif opt == 6:
                    return MeshC3D6(e_id), True
                elif opt == 4:
                    return MeshTetra(e_id), True
                elif opt == 5:
                    return MeshC3D5(e_id), True
                else:
                    raise NoImplSuchElement(e_type, e_id)
            elif e_type in [40600, 40800]:
                return MeshTetra(e_id), True
            elif e_type in [40500]:
                return MeshCQUAD4(e_id), True
            elif e_type in [20100, 188, 10, 4, 39, 14, 180]:
                return MeshTruss(e_id), False
            elif e_type in [30500, "triangle"]:
                return MeshTRIA3(e_id), True
            elif e_type in [181, 131, 63]:
                if opt == 4:
                    return MeshCQUAD4(e_id), True
                elif opt == 3:
                    return MeshTRIA3(e_id), True
                else:
                    raise KeyError("shell 181 or 63 don't support {}".format(e_type))
            elif e_type in [10000]:
                return None, False
            elif e_type in [100600, 187]:
                if use_low_order:
                    return MeshTetra(e_id), True
                else:
                    return MeshC3D10(e_id), True
            elif e_type in [150600]:
                if use_low_order:
                    return MeshC3D6(e_id), True
                else:
                    return MeshC3D15(e_id), True
            elif e_type in [200600, 186, 80600]:
                if opt == 15:
                    if use_low_order:
                        return MeshC3D6(e_id), True
                    else:
                        return MeshC3D15(e_id), True
                elif opt == 13:
                    if use_low_order:
                        return MeshC3D5(e_id), True
                    else:
                        return MeshC3D13(e_id), True
                elif opt == 20:
                    if use_low_order:
                        return MeshC3D8(e_id), True
                    else:
                        return MeshC3D20(e_id), True
                elif opt is None:
                    return MeshC3D8(e_id), True
                else:
                    raise NoImplSuchElement(e_type, opt)
            elif e_type in [130600]:
                if use_low_order:
                    return MeshC3D5(e_id), True
                else:
                    return MeshC3D13(e_id), True
            elif e_type in [174, 170, 21]:
                return None, None
            else:
                raise NoImplSuchElement(e_type, e_id)

        elif fem_software == "ABAQUS":
            if "S3" in e_type:
                return MeshTRIA3(e_id), True, 3
            elif "S4" in e_type:
                return MeshCQUAD4(e_id), True, 4
            elif "C3D4" in e_type:
                return MeshTetra(e_id), True, 4
            elif "C3D6" in e_type:
                return MeshC3D6(e_id), True, 6
            elif "C3D10" in e_type:
                if use_low_order:
                    return MeshTetra(e_id), True, 4
                else:
                    return MeshC3D10(e_id), True, 10
            elif "S8" in e_type:
                return MeshS8(e_id), True, 8
            elif "STRI" in e_type:
                return MeshTRIA3(e_id), True, 3
            elif "3D8" in e_type:
                return MeshC3D8(e_id), True, 8
            elif "B21" in e_type or "B31" in e_type or "B22" in e_type or "B32" in e_type:
                return None, True, 2
            elif "MASS" in e_type:
                return None, True, 1
            elif "ROTARYI" in e_type:
                return None, True, 1
            elif "CONN3D2" in e_type:
                return MeshTruss(e_id), True, 2
            else:
                raise NoImplSuchElement(e_type, e_id)

        elif fem_software == "NASTRAN":
            if "CBAR" in e_type or "CBEAM" in e_type:
                return MeshTruss(e_id), True
            elif "CQUAD4" in e_type:
                return MeshCQUAD4(e_id), True
            elif "CTRIA3" in e_type:
                return MeshTRIA3(e_id), True
            elif "CELAS" in e_type:
                return MeshTruss(e_id), True
            elif "CTETRA" in e_type:
                return MeshTetra(e_id), True
            elif "CHEXA" in e_type:
                return MeshC3D8(e_id), True
            elif "CPENTA" in e_type:
                return MeshC3D6(e_id), True
            else:
                raise NoImplSuchElement(e_type, e_id)

        else:
            raise NoImplSuchElement(e_type, e_id)


VTK_VERTEX = 1
VTK_POLY_VERTEX = 2
VTK_LINE = 3
VTK_POLY_LINE = 4
VTK_TRIANGLE = 5
VTK_TRIANGLE_STRIP = 6
VTK_POLYGON = 7
VTK_PIXEL = 8
VTK_QUAD = 9
VTK_TETRA = 10
VTK_VOXEL = 11
VTK_HEXAHEDRON = 12
VTK_WEDGE = 13
VTK_PYRAMID = 14

if sys.version_info[0] == 3:
    class MeshElementMeta(abc.ABCMeta):
        pass
else:
    class MeshElementMeta(abc.ABCMeta):
        __metaclass__ = abc.ABCMeta


class MeshElement(object):
    __metaclass__ = MeshElementMeta if sys.version_info[0] == 2 else ABCMeta

    def __init__(self):
        self.id = -1
        self.nodesCount = 0
        self.degradeEle = None  # Assuming an integer or similar
        self.upgradeNodeIndex = None  #
        self.degradeRvIndex = []
        self.property_id = None
        self.pts = []
        self.triangles = []
        self.nodeStr = ""
        self.triFaces = []
        self.quaFaces = []
        self.nodeIds = []
        self.type_code = None
        self.edges = []
        self.vtu_type = None
        self.cell_type = None
        self.color_value = None  # 为了生成vtu的时候按照组来区分颜色.
        self.property = None  # 将单元属性值存储到这里

    def SetId(self, _id):
        self.id = _id

    @abc.abstractmethod
    def setFaces(self, _nodeIds):
        pass  # This method is meant to be overridden by derived classes

    def getQuaFaces(self):
        return self.quaFaces

    def getTriFaces(self):
        return self.triFaces

    @abc.abstractmethod
    def getAllTriangles(self, only_surface=False):
        pass

    def getEdges(self):
        # if len(self.edges) == 0:
        #     print(f"Inner Element: {self.id}")
        return self.edges

    def __eq__(self, other):
        return self.id == other.id

    def __lt__(self, other):
        return self.id < other.id


class TriFace:
    def __init__(self, node_list):
        self.nodes = node_list
        self.unique_key = ",".join([str(ii) for ii in np.sort(node_list)])
        self.is_surface = True
        self.nodeIds = []

    def calculateNodeIds(self, map_):
        self.nodeIds = [map_[ii] for ii in self.nodes]


class QuaFace:
    def __init__(self, node_list):
        self.nodes = node_list  # 文件中的原编号
        self.unique_key = ",".join([str(ii) for ii in np.sort(node_list)])
        self.is_surface = True
        self.nodeIds = []  # 文件中经过hash后的编号

    def calculateNodeIds(self, map_):
        self.nodeIds = [map_[ii] for ii in self.nodes]


class MeshC3D6(MeshElement):
    """
    三棱柱单元
    """

    def __init__(self, _id):
        super(MeshC3D6, self).__init__()
        self.SetId(_id)
        self.nodesCount = 6
        self.upgradeNodeIndex = [0, 1, 2, 3, 4, 5]
        self.vtu_type = "wedge"
        self.cell_type = VTK_WEDGE

    def setFaces(self, nodeIds):
        self.nodeIds = nodeIds
        f1 = QuaFace([nodeIds[3], nodeIds[5], nodeIds[2], nodeIds[0]])
        f2 = QuaFace([nodeIds[1], nodeIds[2], nodeIds[5], nodeIds[4]])
        f3 = QuaFace([nodeIds[0], nodeIds[1], nodeIds[4], nodeIds[3]])
        f4 = TriFace([nodeIds[0], nodeIds[2], nodeIds[1]])
        f5 = TriFace([nodeIds[3], nodeIds[4], nodeIds[5]])
        self.quaFaces = [f1, f2, f3]
        self.triFaces = [f4, f5]

    def getAllTriangles(self, only_surface=False):
        del self.triangles[:]
        if (only_surface and self.quaFaces[0].is_surface) or not only_surface:
            triangle1 = [self.quaFaces[0].nodes[0], self.quaFaces[0].nodes[1], self.quaFaces[0].nodes[2]]
            self.triangles.append(triangle1)
            triangle2 = [self.quaFaces[0].nodes[0], self.quaFaces[0].nodes[2], self.quaFaces[0].nodes[3]]
            self.triangles.append(triangle2)
            self.edges.extend([self.quaFaces[0].nodes[0], self.quaFaces[0].nodes[1]])
            self.edges.extend([self.quaFaces[0].nodes[1], self.quaFaces[0].nodes[2]])
            self.edges.extend([self.quaFaces[0].nodes[2], self.quaFaces[0].nodes[3]])
            self.edges.extend([self.quaFaces[0].nodes[3], self.quaFaces[0].nodes[0]])

        if (only_surface and self.quaFaces[1].is_surface) or not only_surface:
            triangle1 = [self.quaFaces[1].nodes[0], self.quaFaces[1].nodes[1], self.quaFaces[1].nodes[2]]
            self.triangles.append(triangle1)
            triangle2 = [self.quaFaces[1].nodes[0], self.quaFaces[1].nodes[2], self.quaFaces[1].nodes[3]]
            self.triangles.append(triangle2)
            self.edges.extend([self.quaFaces[1].nodes[0], self.quaFaces[1].nodes[1]])
            self.edges.extend([self.quaFaces[1].nodes[1], self.quaFaces[1].nodes[2]])
            self.edges.extend([self.quaFaces[1].nodes[2], self.quaFaces[1].nodes[3]])
            self.edges.extend([self.quaFaces[1].nodes[3], self.quaFaces[1].nodes[0]])

        if (only_surface and self.quaFaces[2].is_surface) or not only_surface:
            triangle1 = [self.quaFaces[2].nodes[0], self.quaFaces[2].nodes[1], self.quaFaces[2].nodes[2]]
            self.triangles.append(triangle1)
            triangle2 = [self.quaFaces[2].nodes[0], self.quaFaces[2].nodes[2], self.quaFaces[2].nodes[3]]
            self.triangles.append(triangle2)
            self.edges.extend([self.quaFaces[2].nodes[0], self.quaFaces[2].nodes[1]])
            self.edges.extend([self.quaFaces[2].nodes[1], self.quaFaces[2].nodes[2]])
            self.edges.extend([self.quaFaces[2].nodes[2], self.quaFaces[2].nodes[3]])
            self.edges.extend([self.quaFaces[2].nodes[3], self.quaFaces[2].nodes[0]])

        if (only_surface and self.triFaces[0].is_surface) or not only_surface:
            self.triangles.append(self.triFaces[0].nodes)
            self.edges.extend([self.triFaces[0].nodes[0], self.triFaces[0].nodes[1]])
            self.edges.extend([self.triFaces[0].nodes[1], self.triFaces[0].nodes[2]])
            self.edges.extend([self.triFaces[0].nodes[2], self.triFaces[0].nodes[0]])

        if (only_surface and self.triFaces[1].is_surface) or not only_surface:
            self.triangles.append(self.triFaces[1].nodes)
            self.edges.extend([self.triFaces[1].nodes[0], self.triFaces[1].nodes[1]])
            self.edges.extend([self.triFaces[1].nodes[1], self.triFaces[1].nodes[2]])
            self.edges.extend([self.triFaces[1].nodes[2], self.triFaces[1].nodes[0]])

        return self.triangles


class MeshTruss(MeshElement):
    """
    杆单元
    """

    def __init__(self, _id):
        super(MeshTruss, self).__init__()
        self.id = _id
        self.nodesCount = 2
        self.triangles = []
        self.upgradeNodeIndex = [0, 1]
        self.vtu_type = "line"
        self.cell_type = VTK_LINE

    def setFaces(self, node_ids):
        self.nodeIds = node_ids
        self.triangles = [node_ids[0], node_ids[1]]
        self.edges = [node_ids[0], node_ids[1]]

    def getAllTriangles(self, only_surface=False):
        return self.triangles


class MeshC3D13(MeshElement):
    """
    13节点高阶金字塔单元
    """

    def __init__(self, _id):
        super(MeshC3D13, self).__init__()
        self.id = _id
        self.nodesCount = 13
        self.triangles = []
        self.cell_type = VTK_PYRAMID

    def setFaces(self, _nodeIds):
        self.nodeIds = _nodeIds
        self.quaFaces.append(QuaFace([_nodeIds[3], _nodeIds[0], _nodeIds[1], _nodeIds[2]]))
        self.triFaces.append(TriFace([_nodeIds[4], _nodeIds[3], _nodeIds[0]]))
        self.triFaces.append(TriFace([_nodeIds[4], _nodeIds[0], _nodeIds[1]]))
        self.triFaces.append(TriFace([_nodeIds[4], _nodeIds[1], _nodeIds[2]]))
        self.triFaces.append(TriFace([_nodeIds[4], _nodeIds[2], _nodeIds[3]]))

    def getAllTriangles(self, only_surface=False):
        del self.triangles[:]
        if (only_surface and self.quaFaces[0].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[3], self.nodeIds[8], self.nodeIds[7]])
            self.triangles.append([self.nodeIds[7], self.nodeIds[8], self.nodeIds[5]])
            self.triangles.append([self.nodeIds[7], self.nodeIds[5], self.nodeIds[6]])
            self.triangles.append([self.nodeIds[7], self.nodeIds[6], self.nodeIds[2]])
            self.triangles.append([self.nodeIds[8], self.nodeIds[0], self.nodeIds[5]])
            self.triangles.append([self.nodeIds[6], self.nodeIds[5], self.nodeIds[1]])

            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.triFaces[0].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[4], self.nodeIds[12], self.nodeIds[9]])
            self.triangles.append([self.nodeIds[12], self.nodeIds[3], self.nodeIds[8]])
            self.triangles.append([self.nodeIds[12], self.nodeIds[8], self.nodeIds[9]])
            self.triangles.append([self.nodeIds[9], self.nodeIds[8], self.nodeIds[0]])

            self.edges.extend([self.nodeIds[0], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[4]])

        if (only_surface and self.triFaces[1].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[4], self.nodeIds[9], self.nodeIds[10]])
            self.triangles.append([self.nodeIds[9], self.nodeIds[0], self.nodeIds[5]])
            self.triangles.append([self.nodeIds[9], self.nodeIds[5], self.nodeIds[10]])
            self.triangles.append([self.nodeIds[10], self.nodeIds[5], self.nodeIds[1]])

            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[4]])

        if (only_surface and self.triFaces[2].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[4], self.nodeIds[10], self.nodeIds[11]])
            self.triangles.append([self.nodeIds[10], self.nodeIds[1], self.nodeIds[6]])
            self.triangles.append([self.nodeIds[10], self.nodeIds[6], self.nodeIds[11]])
            self.triangles.append([self.nodeIds[11], self.nodeIds[6], self.nodeIds[2]])

            self.edges.extend([self.nodeIds[4], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])

        if (only_surface and self.triFaces[3].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[4], self.nodeIds[11], self.nodeIds[12]])
            self.triangles.append([self.nodeIds[11], self.nodeIds[2], self.nodeIds[7]])
            self.triangles.append([self.nodeIds[11], self.nodeIds[7], self.nodeIds[12]])
            self.triangles.append([self.nodeIds[12], self.nodeIds[7], self.nodeIds[3]])

            self.edges.extend([self.nodeIds[4], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])

        return self.triangles


class MeshC3D5(MeshElement):
    """
    金字塔单元
    """

    def __init__(self, _id):
        super(MeshC3D5, self).__init__()
        self.id = _id
        self.nodesCount = 5
        self.triangles = []
        self.upgradeNodeIndex = [0, 1, 2, 3, 4]
        self.vtu_type = "pyramid"
        self.cell_type = VTK_PYRAMID

    def setFaces(self, node_ids):
        self.nodeIds = node_ids
        del self.triangles[:]
        self.quaFaces.append(QuaFace([node_ids[0], node_ids[1], node_ids[2], node_ids[3]]))
        self.triFaces.append(TriFace([node_ids[0], node_ids[1], node_ids[4]]))
        self.triFaces.append(TriFace([node_ids[4], node_ids[1], node_ids[2]]))
        self.triFaces.append(TriFace([node_ids[4], node_ids[2], node_ids[3]]))
        self.triFaces.append(TriFace([node_ids[4], node_ids[3], node_ids[0]]))

    def getAllTriangles(self, only_surface=False):
        del self.triangles[:]
        if (only_surface and self.quaFaces[0].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[1], self.nodeIds[0], self.nodeIds[3]])
            self.triangles.append([self.nodeIds[1], self.nodeIds[3], self.nodeIds[2]])

            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.triFaces[0].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[0], self.nodeIds[1], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[4]])

        if (only_surface and self.triFaces[1].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[4], self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])

        if (only_surface and self.triFaces[2].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[4], self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])

        if (only_surface and self.triFaces[3].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[4], self.nodeIds[3], self.nodeIds[0]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[4]])

        return self.triangles


class MeshC3D20(MeshElement):
    """
    20节点六面体
    """

    def __init__(self, _id):
        super(MeshC3D20, self).__init__()
        self.id = _id
        self.nodesCount = 20
        self.triangles = []
        self.cell_type = VTK_HEXAHEDRON

    def setFaces(self, _nodeIds):
        self.nodeIds = _nodeIds
        f1 = QuaFace([_nodeIds[0], _nodeIds[3], _nodeIds[2], _nodeIds[1]])
        f2 = QuaFace([_nodeIds[4], _nodeIds[5], _nodeIds[6], _nodeIds[7]])
        f3 = QuaFace([_nodeIds[5], _nodeIds[4], _nodeIds[0], _nodeIds[1]])
        f4 = QuaFace([_nodeIds[6], _nodeIds[2], _nodeIds[3], _nodeIds[7]])
        f5 = QuaFace([_nodeIds[1], _nodeIds[2], _nodeIds[6], _nodeIds[5]])
        f6 = QuaFace([_nodeIds[4], _nodeIds[7], _nodeIds[3], _nodeIds[0]])
        self.quaFaces = [f1, f2, f3, f4, f5, f6]

    def getAllTriangles(self, only_surface=False):
        del self.triangles[:]

        if (only_surface and self.quaFaces[0].is_surface) or not only_surface:
            self.triangles.extend(QuaFace2Triangles(self.nodeIds, [0, 8, 1, 11, 9, 3, 10, 2]))
            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.quaFaces[1].is_surface) or not only_surface:
            self.triangles.extend(QuaFace2Triangles(self.nodeIds, [5, 16, 4, 17, 19, 6, 18, 7]))
            self.edges.extend([self.nodeIds[4], self.nodeIds[5]])
            self.edges.extend([self.nodeIds[5], self.nodeIds[6]])
            self.edges.extend([self.nodeIds[6], self.nodeIds[7]])
            self.edges.extend([self.nodeIds[7], self.nodeIds[4]])

        if (only_surface and self.quaFaces[2].is_surface) or not only_surface:
            self.triangles.extend(QuaFace2Triangles(self.nodeIds, [0, 12, 4, 8, 16, 1, 13, 5]))
            self.edges.extend([self.nodeIds[0], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[5]])
            self.edges.extend([self.nodeIds[5], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[0]])

        if (only_surface and self.quaFaces[3].is_surface) or not only_surface:
            self.triangles.extend(QuaFace2Triangles(self.nodeIds, [2, 14, 6, 10, 18, 3, 15, 7]))
            self.edges.extend([self.nodeIds[2], self.nodeIds[6]])
            self.edges.extend([self.nodeIds[6], self.nodeIds[7]])
            self.edges.extend([self.nodeIds[7], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[2]])

        if (only_surface and self.quaFaces[4].is_surface) or not only_surface:
            self.triangles.extend(QuaFace2Triangles(self.nodeIds, [1, 13, 5, 9, 17, 2, 14, 6]))
            self.edges.extend([self.nodeIds[1], self.nodeIds[5]])
            self.edges.extend([self.nodeIds[5], self.nodeIds[6]])
            self.edges.extend([self.nodeIds[6], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[1]])

        if (only_surface and self.quaFaces[5].is_surface) or not only_surface:
            self.triangles.extend(QuaFace2Triangles(self.nodeIds, [4, 12, 0, 19, 11, 7, 15, 3]))
            self.edges.extend([self.nodeIds[0], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[7]])
            self.edges.extend([self.nodeIds[7], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[0]])

        return self.triangles


class MeshCQUAD4(MeshElement):
    """
    四节点壳单元, 退化的单元为三节点壳单元, 默认壳单元全是表面单元
    """

    def __init__(self, _id):
        super(MeshCQUAD4, self).__init__()
        self.id = _id
        self.nodesCount = 4
        self.degrade_ele = 30500
        self.degrade_rv_index = [2]
        self.triangles = []
        self.upgradeNodeIndex = [0, 1, 2, 3]
        self.vtu_type = "quad"
        self.cell_type = VTK_QUAD

    def setFaces(self, node_ids):
        self.nodeIds = node_ids
        del self.triangles[:]

        triangle = [node_ids[0], node_ids[1], node_ids[2]]
        self.triangles.append(triangle)

        triangle = [node_ids[0], node_ids[2], node_ids[3]]
        self.triangles.append(triangle)

        self.edges = [node_ids[0], node_ids[1], node_ids[1], node_ids[2], node_ids[2], node_ids[3], node_ids[3], node_ids[0]]

    def getAllTriangles(self, only_surface=False):
        return self.triangles


class MeshS8(MeshElement):
    """
    八节点壳单元
    """

    def __init__(self, _id):
        super(MeshS8, self).__init__()
        self.id = _id
        self.nodesCount = 8
        self.degrade_ele = 30500
        self.degrade_rv_index = [2]
        self.triangles = []
        self.vtu_type = "quad"
        self.cell_type = VTK_QUAD

    def setFaces(self, node_ids):
        self.nodeIds = node_ids
        del self.triangles[:]

        triangle = [node_ids[0], node_ids[1], node_ids[2]]
        self.triangles.append(triangle)

        triangle = [node_ids[0], node_ids[2], node_ids[3]]
        self.triangles.append(triangle)

        self.edges = [node_ids[0], node_ids[1], node_ids[1], node_ids[2], node_ids[2], node_ids[3], node_ids[3], node_ids[0]]

    def getAllTriangles(self, only_surface=False):
        return self.triangles


class MeshC3D8(MeshElement):
    def __init__(self, _id):
        super(MeshC3D8, self).__init__()
        self.SetId(_id)
        self.nodesCount = 8
        self.degradeEle = 60600
        self.degradeRvIndex = [4, 0]
        self.upgradeNodeIndex = [0, 1, 2, 3, 4, 5, 6, 7]
        self.vtu_type = "hexahedron"
        self.cell_type = VTK_HEXAHEDRON

    def setFaces(self, _nodeIds):
        self.nodeIds = _nodeIds
        f1 = QuaFace([_nodeIds[0], _nodeIds[3], _nodeIds[2], _nodeIds[1]])
        f2 = QuaFace([_nodeIds[4], _nodeIds[5], _nodeIds[6], _nodeIds[7]])
        f3 = QuaFace([_nodeIds[5], _nodeIds[4], _nodeIds[0], _nodeIds[1]])
        f4 = QuaFace([_nodeIds[6], _nodeIds[2], _nodeIds[3], _nodeIds[7]])
        f5 = QuaFace([_nodeIds[1], _nodeIds[2], _nodeIds[6], _nodeIds[5]])
        f6 = QuaFace([_nodeIds[4], _nodeIds[7], _nodeIds[3], _nodeIds[0]])
        self.quaFaces = [f1, f2, f3, f4, f5, f6]

    def getAllTriangles(self, only_surface=False):
        self.triangles = []  # Reset triangles to an empty list
        for quaFace in self.quaFaces:
            # First triangle from the quadrilateral face
            if (only_surface and quaFace.is_surface) or not only_surface:
                triangle1 = [quaFace.nodes[0], quaFace.nodes[1], quaFace.nodes[2]]
                self.triangles.append(triangle1)
                # Second triangle from the quadrilateral face
                triangle2 = [quaFace.nodes[0], quaFace.nodes[2], quaFace.nodes[3]]
                self.triangles.append(triangle2)

                self.edges.extend([quaFace.nodes[0], quaFace.nodes[1]])
                self.edges.extend([quaFace.nodes[1], quaFace.nodes[2]])
                self.edges.extend([quaFace.nodes[2], quaFace.nodes[3]])
                self.edges.extend([quaFace.nodes[3], quaFace.nodes[0]])
        return self.triangles


class MeshC3D10(MeshElement):
    """
    ABAQUS十节点四面体, triangles是为了web端显示做的全部小面片
    """

    def __init__(self, _id):
        super(MeshC3D10, self).__init__()
        self.SetId(_id)
        self.nodesCount = 10
        self.cell_type = VTK_TETRA

    def setFaces(self, _nodeIds):
        self.nodeIds = _nodeIds
        self.triFaces.append(TriFace([_nodeIds[0], _nodeIds[1], _nodeIds[2]]))
        self.triFaces.append(TriFace([_nodeIds[0], _nodeIds[2], _nodeIds[3]]))
        self.triFaces.append(TriFace([_nodeIds[0], _nodeIds[1], _nodeIds[3]]))
        self.triFaces.append(TriFace([_nodeIds[1], _nodeIds[2], _nodeIds[3]]))

    def getAllTriangles(self, only_surface=False):
        del self.triangles[:]
        if (only_surface and self.triFaces[0].is_surface) or not only_surface:
            fe = [self.nodeIds[0], self.nodeIds[6], self.nodeIds[4]]
            ff = [self.nodeIds[6], self.nodeIds[2], self.nodeIds[5]]
            fg = [self.nodeIds[6], self.nodeIds[5], self.nodeIds[4]]
            fh = [self.nodeIds[4], self.nodeIds[5], self.nodeIds[1]]
            self.triangles.extend([fe, ff, fg, fh])

            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[0]])

        if (only_surface and self.triFaces[1].is_surface) or not only_surface:
            fa = [self.nodeIds[9], self.nodeIds[2], self.nodeIds[6]]
            fb = [self.nodeIds[3], self.nodeIds[9], self.nodeIds[7]]
            fc = [self.nodeIds[7], self.nodeIds[9], self.nodeIds[6]]
            fd = [self.nodeIds[0], self.nodeIds[7], self.nodeIds[6]]
            self.triangles.extend([fa, fb, fc, fd])

            self.edges.extend([self.nodeIds[0], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.triFaces[2].is_surface) or not only_surface:
            f1 = [self.nodeIds[0], self.nodeIds[4], self.nodeIds[7]]
            f2 = [self.nodeIds[4], self.nodeIds[1], self.nodeIds[8]]
            f3 = [self.nodeIds[4], self.nodeIds[8], self.nodeIds[7]]
            f4 = [self.nodeIds[8], self.nodeIds[3], self.nodeIds[7]]
            self.triangles.extend([f1, f2, f3, f4])

            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.triFaces[3].is_surface) or not only_surface:
            f5 = [self.nodeIds[1], self.nodeIds[5], self.nodeIds[8]]
            f6 = [self.nodeIds[5], self.nodeIds[2], self.nodeIds[9]]
            f7 = [self.nodeIds[5], self.nodeIds[9], self.nodeIds[8]]
            f8 = [self.nodeIds[9], self.nodeIds[3], self.nodeIds[8]]
            self.triangles.extend([f5, f6, f7, f8])

            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[1]])

        return self.triangles


class MeshTRIA3(MeshElement):
    """
    三角形壳单元, 默认壳单元全是表面单元
    """

    def __init__(self, _id):
        super(MeshTRIA3, self).__init__()
        self.SetId(_id)
        self.nodesCount = 3
        self.vtu_type = "triangle"
        self.upgradeNodeIndex = [0, 1, 2]
        self.cell_type = VTK_TRIANGLE

    def setFaces(self, nodeIds):
        self.nodeIds = nodeIds
        self.triangles = [[nodeIds[0], nodeIds[1], nodeIds[2]]]
        self.edges = [nodeIds[0], nodeIds[1], nodeIds[1], nodeIds[2], nodeIds[2], nodeIds[0]]

    def getAllTriangles(self, only_surface=False):
        return self.triangles


class MeshTetra(MeshElement):
    """
    四面体单元
    """

    def __init__(self, _id):
        super(MeshTetra, self).__init__()
        self.SetId(_id)
        self.nodesCount = 4
        self.upgradeNodeIndex = [0, 1, 2, 3]
        self.vtu_type = "tetra"
        self.cell_type = VTK_TETRA

    def setFaces(self, _nodeIds):
        self.nodeIds = _nodeIds
        self.triFaces.append(TriFace([_nodeIds[0], _nodeIds[1], _nodeIds[2]]))
        self.triFaces.append(TriFace([_nodeIds[0], _nodeIds[1], _nodeIds[3]]))
        self.triFaces.append(TriFace([_nodeIds[0], _nodeIds[2], _nodeIds[3]]))
        self.triFaces.append(TriFace([_nodeIds[1], _nodeIds[2], _nodeIds[3]]))

    def getAllTriangles(self, only_surface=False):
        del self.triangles[:]
        if (only_surface and self.triFaces[0].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[0], self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[0]])

        if (only_surface and self.triFaces[1].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[0], self.nodeIds[1], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.triFaces[2].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[0], self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[0], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.triFaces[3].is_surface) or not only_surface:
            self.triangles.append([self.nodeIds[1], self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[1]])

        return self.triangles


class MeshC3D15(MeshElement):
    """
    15节点三棱柱
    """

    def __init__(self, _id):
        super(MeshC3D15, self).__init__()
        self.SetId(_id)
        self.nodesCount = 15
        self.cell_type = VTK_WEDGE

    def setFaces(self, _nodeIds):
        self.nodeIds = _nodeIds
        self.triFaces.append(TriFace([_nodeIds[0], _nodeIds[1], _nodeIds[2]]))
        self.triFaces.append(TriFace([_nodeIds[3], _nodeIds[4], _nodeIds[5]]))
        self.quaFaces.append(QuaFace([_nodeIds[0], _nodeIds[1], _nodeIds[4], _nodeIds[3]]))
        self.quaFaces.append(QuaFace([_nodeIds[1], _nodeIds[4], _nodeIds[5], _nodeIds[2]]))
        self.quaFaces.append(QuaFace([_nodeIds[0], _nodeIds[2], _nodeIds[5], _nodeIds[3]]))

    def getAllTriangles(self, only_surface=False):
        del self.triangles[:]
        if (only_surface and self.triFaces[0].is_surface) or not only_surface:
            f1 = [self.nodeIds[0], self.nodeIds[8], self.nodeIds[6]]
            f2 = [self.nodeIds[1], self.nodeIds[6], self.nodeIds[7]]
            f3 = [self.nodeIds[6], self.nodeIds[8], self.nodeIds[7]]
            f4 = [self.nodeIds[2], self.nodeIds[8], self.nodeIds[7]]
            self.triangles.extend([f1, f2, f3, f4])
            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[0]])

        if (only_surface and self.triFaces[1].is_surface) or not only_surface:
            f1 = [self.nodeIds[5], self.nodeIds[11], self.nodeIds[10]]
            f2 = [self.nodeIds[11], self.nodeIds[3], self.nodeIds[9]]
            f3 = [self.nodeIds[10], self.nodeIds[9], self.nodeIds[4]]
            f4 = [self.nodeIds[10], self.nodeIds[11], self.nodeIds[9]]
            self.triangles.extend([f1, f2, f3, f4])
            self.edges.extend([self.nodeIds[3], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[5]])
            self.edges.extend([self.nodeIds[5], self.nodeIds[3]])

        if (only_surface and self.quaFaces[0].is_surface) or not only_surface:
            f1 = [self.nodeIds[9], self.nodeIds[3], self.nodeIds[12]]
            f2 = [self.nodeIds[4], self.nodeIds[9], self.nodeIds[13]]
            f3 = [self.nodeIds[9], self.nodeIds[12], self.nodeIds[6]]
            f4 = [self.nodeIds[9], self.nodeIds[6], self.nodeIds[13]]
            f5 = [self.nodeIds[12], self.nodeIds[0], self.nodeIds[6]]
            f6 = [self.nodeIds[13], self.nodeIds[6], self.nodeIds[1]]
            self.triangles.extend([f1, f2, f3, f4, f5, f6])
            self.edges.extend([self.nodeIds[0], self.nodeIds[1]])
            self.edges.extend([self.nodeIds[1], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[0]])

        if (only_surface and self.quaFaces[1].is_surface) or not only_surface:
            f1 = [self.nodeIds[11], self.nodeIds[5], self.nodeIds[14]]
            f2 = [self.nodeIds[3], self.nodeIds[11], self.nodeIds[12]]
            f3 = [self.nodeIds[11], self.nodeIds[14], self.nodeIds[8]]
            f4 = [self.nodeIds[11], self.nodeIds[8], self.nodeIds[12]]
            f5 = [self.nodeIds[14], self.nodeIds[2], self.nodeIds[8]]
            f6 = [self.nodeIds[12], self.nodeIds[8], self.nodeIds[0]]
            self.triangles.extend([f1, f2, f3, f4, f5, f6])
            self.edges.extend([self.nodeIds[0], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[5]])
            self.edges.extend([self.nodeIds[5], self.nodeIds[3]])
            self.edges.extend([self.nodeIds[3], self.nodeIds[0]])

        if (only_surface and self.quaFaces[1].is_surface) or not only_surface:
            f1 = [self.nodeIds[10], self.nodeIds[4], self.nodeIds[13]]
            f2 = [self.nodeIds[5], self.nodeIds[10], self.nodeIds[14]]
            f3 = [self.nodeIds[10], self.nodeIds[13], self.nodeIds[7]]
            f4 = [self.nodeIds[10], self.nodeIds[7], self.nodeIds[14]]
            f5 = [self.nodeIds[13], self.nodeIds[1], self.nodeIds[7]]
            f6 = [self.nodeIds[14], self.nodeIds[7], self.nodeIds[2]]
            self.triangles.extend([f1, f2, f3, f4, f5, f6])
            self.edges.extend([self.nodeIds[1], self.nodeIds[2]])
            self.edges.extend([self.nodeIds[2], self.nodeIds[5]])
            self.edges.extend([self.nodeIds[5], self.nodeIds[4]])
            self.edges.extend([self.nodeIds[4], self.nodeIds[1]])

        return self.triangles
