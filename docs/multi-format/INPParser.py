#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as np
import sys
import meshio
from FEMDataBase import *
from GlobalEnum import *
from FemNode import FemNode
from MeshElementFactory import MeshElementFactory

"""
文件解析类是和Domain一样同样包含Element和数据库的类
"""


class EleSet(object):
    def __init__(self, e_name, eles_ids):
        """
        如果used是False, 那么这个集合是没有被用到过的. 如果某些单元集合被赋予过属性, 那么used为True
        """
        self.name = e_name
        self.eles_ids = np.asarray(eles_ids, dtype=np.int32)

    def __str__(self):
        return "name: {}, from {} to {}, count is: {}".format(
            self.name, self.eles_ids[0], self.eles_ids[-1], len(self.eles_ids))

    def GetName(self):
        return self.name

    def GetEleIds(self):
        return self.eles_ids

    def ExtendElesIds(self, new_ele_ids):
        self.eles_ids = np.concatenate((self.eles_ids, np.asarray(new_ele_ids, dtype=np.int32)))


class Section(object):

    def __init__(self, ele_set_name, mat_name, cha_dict):
        self.ele_set_name = ele_set_name
        self.mat_name = mat_name
        self.cha_dict = cha_dict


def ReadSectionLine(line):
    """
    多处用到的方法，提出来作为一个函数: 读取Inp文件的Section行, 返回一个字典
    """
    ret_diction = {}
    sps = line.split(",")
    for i in range(1, len(sps)):
        if "=" in sps[i]:
            keyval = sps[i].strip().split("=")
            ret_diction[keyval[0].lower()] = keyval[1]
        else:
            sys.exit(1)
    return ret_diction

def ParsePartAndId(text: str):
    """
    解析出Part的名字和Id, 例如Part-1.0解析出Part-1和0, Part-2.3解析出Part-2和3
    :param text:
    :return:
    """
    text = text.strip()
    if '.' not in text:
        raise ValueError("Invalid format, no '.' found")
    part_name, part_id = text.split('.', 1)
    return part_name, part_id


def ReadPartNameLine(line):
    """
    多处用到的方法，提出来作为一个函数: 读取Inp文件的Part行, 返回一个字典
    """
    ret_diction = {}
    sps = line.split(",")
    for i in range(1, len(sps)):
        if "=" in sps[i]:
            keyval = sps[i].strip().split("=")
            ret_diction[keyval[0].lower()] = keyval[1]
        else:
            sys.exit(1)
    return ret_diction


class InpParser(object):
    """
    功能为解析Inp文件, 初始化数据库单元节点及相关信息, 只负责解析和传递, 其他功能均在其他类中,
    同样的, 其他类也不会包含任何解析输入文件的功能或者函数
    TODO: http://130.149.89.49:2080/v6.14/ ABAQUS帮助文档
    """

    def __init__(self, input_path, check_model=False):
        self.fem_db = FEMDataBase()
        self.inp_path = input_path
        self.ele_count = 0
        self.iter_line = ""
        self.sections = []
        self.materials = {}
        self.ele_sets = {}
        self.eleId2Idx = {}
        self.nodes = []
        self.elements = []
        self.global_node_hash = {}
        self.part_node_hash = {}
        self.node_set = {}
        self.check_model = check_model
        self.node_search_ids_list = []

    def ParseFileAndInitFEMDB(self):
        """
        逐行读取数据，ReadPart ReadLoad等都是这样, 具体程序细节参见具体函数的注释
        由于对Inp文件的不熟悉, 现有如下假设或者限制：
        1. 只支持单个Part, 即忽略Assembly词条
        2. 所有相同类型的单元都在一起, 例如模型中所有的C3D8单元都在*Element, type=C3D8下, 即使被分为很多个ElSet, 每个ElSet中单元材料不同, 这样在
           Domain类中的ele_grp_hash的键就是唯一的, 带来的好处就是输出vtp文件时可以对ele_grp_hash进行循环.
        3. 不在读取Section的时候就赋予相关集合包含单元的材料属性等信息，因为有些材料可能还未定义, 等全部信息解析完后再赋予属性
        4. 默认Section定义的Eleset都在定义Section之前定义了, 默认定义的Section都对应Eleset, 即Eleset中的单元不为空, 这样就可以对属性进行循环来赋
          材料进而计算D阵

        Reference:
            1. http://wufengyun.com:888/books/usi/default.htm
            2. ABAQUS keyword browser table & Keyword support from the input file readers
            3.《ABAQUS有限元分析实例详解》P26  图2-30 数据库的结构示意图
        """
        with open(self.inp_path, 'r') as inp_f:
            self.iter_line = inp_f.readline()
            while True:
                # Part中的Nset和Eset一般都是该Part内部节点和单元的集合
                if self.iter_line.lower().startswith("*node"):
                    self.ReadNode(inp_f, "NoPart")

                elif self.iter_line.lower().startswith("*element,"):
                    self.ReadElement(inp_f)

                elif self.iter_line.lower().startswith("*part,"):
                    self.ReadPart(inp_f)

                elif self.iter_line.lower().startswith("*material,"):
                    self.ReadMaterial(inp_f)

                # 在Part外也会可有Nset和Elset, 比如设置约束或力的时候
                elif self.iter_line.lower().startswith("*nset,"):
                    self.ReadNSet(inp_f)

                elif self.iter_line.lower().startswith("*elset,"):
                    self.ReadElset(inp_f)

                elif self.iter_line.lower().startswith("*step,"):
                    self.ReadLoadCase(inp_f)

                elif self.iter_line.lower().startswith("*boundary"):
                    self.ReadBoundary(inp_f)

                elif self.iter_line.lower().startswith("*amplitude"):
                    self.ReadAmplitude(inp_f)

                elif "section" in self.iter_line.lower():
                    self.ReadSection(inp_f)

                else:
                    if not self.iter_line:
                        """
                        只允许一个空行
                        """
                        self.iter_line = inp_f.readline()
                        if not self.iter_line:
                            break
                    else:
                        self.iter_line = inp_f.readline().strip()

        """
        单元属性、材料、厚度等信息在文件解析中完成, 而不是在FEMDataBase中完成
        """
        iter_color_value = 0
        for section in self.sections:
            ele_set_name = section.ele_set_name
            mat_name = section.mat_name
            mat_cha_dict = self.materials[mat_name]
            sec_cha_dict = section.cha_dict
            ele_cha_dict = {**mat_cha_dict, **sec_cha_dict}
            ele_set = self.ele_sets[ele_set_name]
            ele_ids = ele_set.GetEleIds()
            for ele_id in ele_ids:
                idx = self.eleId2Idx[ele_id]
                self.elements[idx].cha_dict = ele_cha_dict
                self.elements[idx].color_value = iter_color_value
            iter_color_value += 1

        """
        计算每个节点有多少个单元相连, 目的是在计算应力平均的时候直接除以N
        """
        _, node_connected_element_count = np.unique(self.node_search_ids_list, return_counts=True)
        self.fem_db.node_connected_element_count = node_connected_element_count

    def ReadNode(self, f_handle, part_name=None):
        """
        读取节点信息, 目前只支持二维和三维的节点, 其他维度的节点暂不支持
        @param f_handle:
        @return:
        """
        node_index = 0
        self.iter_line = f_handle.readline().strip()
        while not self.iter_line.startswith("*"):
            n_data = self.iter_line.split(",")
            n_id = int(n_data[0])
            x = float(n_data[1])
            y = float(n_data[2])  # 最小是二维的, 即不会先让y=0.0
            if len(n_data) == 4:
                z = float(n_data[3])
                self.nodes.append(FemNode(n_id, x, y, z))
            elif len(n_data) == 3:
                self.nodes.append(FemNode(n_id, x, y))

            self.global_node_hash[n_id] = node_index
            self.part_node_hash[f"{part_name}_{n_id}"] = node_index
            node_index += 1
            self.iter_line = f_handle.readline().strip()

    def ReadElement(self, f_handle):
        """
        相同单元类型不同属性的话, 通过Section中的Set来区分
        解析单元类型关键字, 如果出现某些单元, 那么整个分析将变为2D分析
        @param f_handle:
        @return:
        """
        splits = self.iter_line.split(",")
        e_type = splits[1].split("=")[-1]
        """
        针对有些情况下, 可能会出现*Element, type=C3D8, elset=Set-1的情况, 这种情况下需要解析出Set-1, 作为单元集合的名字, 以便后续赋予属性
        """
        if len(splits) > 2:
            set_name = splits[2].split("=")[-1]
        else:
            set_name = None
        ele_ids = []

        """
        开始读取单元数据, 直到遇到下一个*号
        """
        self.iter_line = f_handle.readline().strip()

        while not self.iter_line.startswith("*"):
            sp_line = self.iter_line.split(",")
            if sp_line[-1] == "":
                sp_line.pop()
            first_line_node_count = len(sp_line) - 1
            eleId = int(sp_line[0])
            iter_ele, _, n_cnt = MeshElementFactory.CreateElement(e_type.strip(), fem_software="ABAQUS", use_low_order=True, e_id=eleId)
            nds = np.zeros(n_cnt, dtype=np.uint32)
            ele_ids.append(int(sp_line[0]))
            sp_line[-1] = sp_line[-1].strip()  # 去掉\n换行符
            if len(sp_line) > n_cnt + 1:
                for i in range(1, n_cnt + 1):
                    nds[i - 1] = int(sp_line[i])
            else:
                for i in range(1, len(sp_line)):
                    try:
                        nds[i - 1] = int(sp_line[i])
                    except ValueError as _:
                        part_name,ParsePartAndId(sp_line[i])

            """
            第一行的节点数不够, 第二行还有节点需要添加
            """
            if first_line_node_count < n_cnt:
                self.iter_line = f_handle.readline().strip()
                sp_line = self.iter_line.split(",")
                for j in range(first_line_node_count, n_cnt):
                    nds[j] = int(sp_line[j - first_line_node_count])

            """
            读取数据完毕，首先设置单元包括的节点的搜索id
            """
            if iter_ele:
                iter_ele.setFaces(nds)
            search_ids = np.array([self.global_node_hash[ii] for ii in nds], dtype=np.uint32)
            # iter_ele.SetNodeSearchIndex(search_ids)
            self.node_search_ids_list.extend(search_ids.tolist())

            """
            需要进行深拷贝, 否则是一个单元重复了单元个数次
            """
            self.eleId2Idx[eleId] = len(self.elements)
            if iter_ele:
                self.elements.append(iter_ele)
            self.ele_count += 1
            self.iter_line = f_handle.readline().strip()

        """
        如果是element set, 那么添加到数据库
        """
        if set_name:
            if set_name in self.ele_sets:
                self.ele_sets[set_name].ExtendElesIds(ele_ids)
            else:
                self.ele_sets[set_name] = EleSet(set_name, ele_ids)

    def ReadSection(self, f_handle):
        """
        读取Section信息, 目前只支持Solid Section, Beam Section和Shell Section, 其他类型的Section暂不支持
        :param f_handle:
        :return:
        """
        if self.iter_line.lower().startswith("*solid section"):
            """
            在当前程序解析属性的时候, 如果用到某个EleSet, 那么这个EleSet就是有用的
            """
            ret_dict = ReadSectionLine(self.iter_line)
            els_name = ret_dict["elset"]
            mat_name = ret_dict["material"]

            self.iter_line = f_handle.readline().strip()
            keywords = self.iter_line.split(",")
            pars = {}
            for par in keywords:
                if par:
                    if par != "**":
                        pars[MaterialKey.Area] = float(par)
            solid_sec = Section(els_name, mat_name, pars)
            self.sections.append(solid_sec)
            self.iter_line = f_handle.readline().strip()

        elif self.iter_line.lower().startswith("*beam section"):
            """
            在当前程序解析属性的时候, 如果用到某个EleSet, 那么这个EleSet就是有用的
            Beam需要指定界面类型, 以及该类型的尺寸参数, 以及法线方向
            """
            ret_dict = ReadSectionLine(self.iter_line)
            els_name = ret_dict["elset"]
            mat_name = ret_dict["material"]
            section = ret_dict["section"]
            self.fem_db.GetSpecificFEMObject(FEMObject.EleSet, els_name).SetUsed(True)
            self.iter_line = f_handle.readline().strip()
            features = [float(iter_v) for iter_v in self.iter_line.split(",")]
            self.iter_line = f_handle.readline().strip()
            normal_dir = [float(di) for di in self.iter_line.split(",")]
            assert len(normal_dir) == 3
            self.iter_line = f_handle.readline().strip()

        elif self.iter_line.lower().startswith("*shell section"):
            """
            在当前程序解析属性的时候, 如果用到某个EleSet, 那么这个EleSet就是有用的
            """
            ret_dict = ReadSectionLine(self.iter_line)
            if "elset" in ret_dict:
                els_name = ret_dict["elset"]
            else:
                raise KeyError("Elset doesn't in ret_dict")

            if "material" in ret_dict:
                mat_name = ret_dict["material"]
            else:
                raise KeyError("material doesn't in ret_dict")

            self.iter_line = f_handle.readline().strip()
            shell_cha_dict = {MaterialKey.Thickness: float(self.iter_line.split(",")[0])}
            shell_sec = Section(els_name, mat_name, shell_cha_dict)
            self.sections.append(shell_sec)

        else:
            self.iter_line = f_handle.readline()

    def ReadPart(self, f_handle):
        """
        读取part中的单元和节点, Part部分是以*End Part结束的, 先不读取Part中的*Elset, Part中包含节点、单元、节点集、单元集
        属性(Section)等
        :param f_handle: 文件句柄
        """
        ret_dict = ReadPartNameLine(self.iter_line)
        self.iter_line = f_handle.readline().strip()
        while self.iter_line.lower() != "*end part":
            if self.iter_line.lower() == "*node":
                self.ReadNode(f_handle, ret_dict["name"])

            elif self.iter_line.lower().startswith("*element,"):
                self.ReadElement(f_handle)

            elif self.iter_line.lower().startswith("*nset,"):
                self.ReadNSet(f_handle)

            elif self.iter_line.lower().startswith("*elset"):
                self.ReadElset(f_handle)

            elif "section" in self.iter_line.lower():
                self.ReadSection(f_handle)

            else:
                """
                对于暂不支持的内容直接读取下一行, 文件的结尾, 读取结束
                """
                self.iter_line = f_handle.readline().strip()
                if not self.iter_line:
                    return

        self.iter_line = f_handle.readline().strip()

    def ReadMaterial(self, f_handle):
        """
        读取材料信息
        """
        mat_name = self.iter_line.split(",")[1].strip().split("=")[1]
        pars_dict = {}
        self.iter_line = f_handle.readline().strip()
        new_material = False
        while True:
            if self.iter_line.lower() == "*density":
                self.iter_line = f_handle.readline().strip()
                pars_dict[MaterialKey.Density] = float(self.iter_line.split(",")[0])
                self.iter_line = f_handle.readline().strip()
            elif self.iter_line.lower().startswith("*elastic"):
                self.iter_line = f_handle.readline().strip()
                pars_dict[MaterialKey.E] = float(self.iter_line.split(",")[0])
                pars_dict[MaterialKey.Niu] = float(self.iter_line.split(",")[1])
                self.iter_line = f_handle.readline().strip()
            elif self.iter_line.lower() == "*conductivity":
                self.iter_line = f_handle.readline().strip()
                pars_dict[MaterialKey.Conductivity] = float(self.iter_line.split(",")[0])
                self.iter_line = f_handle.readline().strip()
            elif self.iter_line.lower() == "*expansion":
                self.iter_line = f_handle.readline().strip()
                pars_dict[MaterialKey.Expansion] = float(self.iter_line.split(",")[0])
                self.iter_line = f_handle.readline().strip()
            elif self.iter_line.lower() == "*specific heat":
                self.iter_line = f_handle.readline().strip()
                pars_dict[MaterialKey.SpecificHeat] = float(self.iter_line.split(",")[0])
                self.iter_line = f_handle.readline().strip()
            elif self.iter_line.lower().startswith("*material,"):
                new_material = True
                self.materials[mat_name] = pars_dict
                break
            elif self.iter_line.startswith("**"):
                self.materials[mat_name] = pars_dict
                break
            elif self.iter_line.lower().startswith("*connector behavior"):
                self.iter_line = f_handle.readline().strip()
            elif self.iter_line.lower().startswith("*connector elasticity"):
                self.iter_line = f_handle.readline().strip()
                self.iter_line = f_handle.readline().strip()
            else:
                mlogger.fatal("UnSupport Material Para Line:{}".format(self.iter_line))
                sys.exit(1)

        if new_material:
            self.ReadMaterial(f_handle)
        else:
            self.iter_line = f_handle.readline().strip()

    def ReadNSet(self, f_handle):
        """
        读取节点集合
        """
        keywords = self.iter_line.strip().split(",")
        set_name = keywords[1].split("=")[1]
        is_generate = False
        for kw in keywords:
            if kw.strip() == "generate":
                is_generate = True
                break
            if kw.strip() == "internal":
                self.iter_line = f_handle.readline().strip()
                return
        self.iter_line = f_handle.readline().strip()
        nodes = []

        """
        有两种形式, 如果是generate, 那么是start, end, inc形式, 否则都按照罗列法
        """
        if is_generate:
            begin_idx, end_idx, inc, = self.iter_line.split(",")
            for i in range(int(begin_idx), int(end_idx) + 1, int(inc)):
                nodes.append(i)
            self.iter_line = f_handle.readline().strip()
        else:
            while not self.iter_line.startswith("*"):
                for nd in self.iter_line.split(","):
                    if nd:
                        nodes.append(int(nd))
                self.iter_line = f_handle.readline().strip()
        self.node_set[set_name] = nodes

        """
        对于很多个nset并列的, 利于调试debug
        """
        if self.iter_line.lower().startswith("*nset,"):
            self.ReadNSet(f_handle)

    def ReadSurface(self, f_handle):
        """
        读取表面
        :param f_handle:
        :return:
        """
        self.iter_line = f_handle.readline().strip()
        self.iter_line = f_handle.readline().strip()
        if self.iter_line.lower().startswith("*surface"):
            self.ReadSurface(f_handle)

    def ReadElset(self, f_handle):
        """
        读取单元集合
        """
        keywords = self.iter_line.strip().split(",")
        set_name = keywords[1].split("=")[1]
        is_generate = False
        for kw in keywords:
            if "generate" in kw.strip():
                is_generate = True
                break
        self.iter_line = f_handle.readline().strip()

        """
        有两种形式, 如果是generate, 那么是start, end, inc形式, 只占一行, 否则都按照罗列法
        """
        eles = []
        if is_generate:
            begin_idx, end_idx, inc, = self.iter_line.split(",")
            for i in range(int(begin_idx), int(end_idx) + 1, int(inc)):
                eles.append(i)
            self.iter_line = f_handle.readline().strip()
        else:
            while not self.iter_line.startswith("*"):
                for ele_id in self.iter_line.split(","):
                    if ele_id:
                        try:
                            ele_id = int(ele_id)
                            eles.append(int(ele_id))
                        except ValueError:
                            pass
                self.iter_line = f_handle.readline().strip()
                if self.iter_line == '':
                    break

        if len(eles) > 0:
            self.ele_sets[set_name] = EleSet(set_name, eles)

        """
        便于调试, 对于多个set放置在一块的
        """
        if self.iter_line.lower().startswith('*elset'):
            self.ReadElset(f_handle)

    def ReadLoadCase(self, f_handle):
        """
        读取工况信息, 作为一次求解的信息, 包括约束、外力以及输出
        """
        self.iter_line = f_handle.readline().strip()
        while self.iter_line.lower() != "*end step":
            if self.iter_line == "*AbaqusBoundary":
                self.ReadBoundary(f_handle)
            elif "*cload" in self.iter_line.lower():
                if "amplitude" in self.iter_line.lower():
                    GlobalInfor[GlobalVariant.AnaType] = AnalyseType.Transient
                    rt_dict = ReadSectionLine(self.iter_line)
                    amp_group_name = rt_dict["amplitude"]
                    vals = self.fem_db.amplitudes[amp_group_name]
                    splits = f_handle.readline().split(",")
                    node_set_name = splits[0]
                    nodes = self.node_set[node_set_name]
                    direction = int(splits[1])
                    scale = float(splits[2])
                    for node in nodes:
                        self.fem_db.load_case.AddHistoryLoad(node, direction, scale, vals)
                else:
                    self.iter_line = f_handle.readline().strip()
                    keywords = self.iter_line.split(",")
                    c_nodes = self.node_set[keywords[0].strip()]
                    for iter_node in c_nodes:
                        self.fem_db.load_case.AddConcentratedLoad(iter_node, int(keywords[1]) - 1, float(keywords[2]))

                self.iter_line = f_handle.readline().strip()
            elif "*boundary" in self.iter_line.lower():
                self.ReadBoundary(f_handle)
            else:
                # 其他情况先读取下一行, 直到遇到 *End Step为止
                self.iter_line = f_handle.readline().strip()
                if not self.iter_line:
                    return
        self.iter_line = f_handle.readline().strip()

    def ReadBoundary(self, f_handle):
        """
        读取边界条件
        """
        self.iter_line = f_handle.readline().strip()
        self.iter_line = f_handle.readline().strip()

    def ReadAmplitude(self, f_handle):
        """
        解析时程载荷数据
        :param f_handle:
        :return:
        """
        amp_name = ReadSectionLine(self.iter_line)["name"]
        self.iter_line = f_handle.readline()
        times = []
        amps = []
        while not self.iter_line.startswith("*"):
            splits = [float(ii) for ii in self.iter_line.split(",")]
            if len(splits) == 8:
                times.extend([splits[0], splits[2], splits[4], splits[6]])
                amps.extend([splits[1], splits[3], splits[5], splits[7]])
            elif len(splits) == 6:
                times.extend([splits[0], splits[2], splits[4]])
                amps.extend([splits[1], splits[3], splits[5]])
            elif len(splits) == 4:
                times.extend([splits[0], splits[2]])
                amps.extend([splits[1], splits[3]])
            elif len(splits) == 2:
                times.append(splits[0])
                amps.append(splits[1])
            else:
                raise ValueError(f"Read Amplitude Error: {self.iter_line}")
            self.iter_line = f_handle.readline()
        self.fem_db.amplitudes[amp_name] = np.asarray([times, amps], dtype=float)

        if self.check_model:
            plt.plot(times, amps, label=amp_name)
            plt.legend()
            plt.show()

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
        cell_data_dict = {}

        for iter_ele in self.elements:
            iter_relation = [self.global_node_hash[ii] for ii in iter_ele.nodeIds]
            ele_type = iter_ele.vtu_type
            """
            将所有单元进行分类, 例如所有的C3D8单元都放在一起, 
            这样在paraview中就可以对不同类型的单元进行不同的显示设置, 例如不同的颜色或者不同的显示方式
            """
            if all_eles.__contains__(ele_type):
                all_eles[ele_type].append(iter_relation)
            else:
                all_eles[ele_type] = [iter_relation]

            """
            对所有单元进行幅值, 让所有属性相同的单元具有相同的颜色, 这样在paraview中就可以通过颜色来区分
            不同属性的单元, 例如不同的材料或者不同的Section, 对于不属于任何分组的, 比如RBE2单元, 直接
            设置为-1, 这样在paraview中最蓝的就是无属性的一组单元
            """
            if ele_type not in cell_data_dict:
                cell_data_dict[ele_type] = []

            if iter_ele.color_value is None:
                cell_data_dict[ele_type].append(-1)
            else:
                cell_data_dict[ele_type].append(iter_ele.color_value)

        cell_data = {}
        if cell_data_dict:
            cell_data["property"] = []
            for ele_type in all_eles.keys():
                if ele_type in cell_data_dict:
                    cell_data["property"].append(np.array(cell_data_dict[ele_type]))
                else:
                    cell_data["property"].append(np.array([]))

        meshio.write_points_cells(
            filename=vtu_path,
            points=node_coords,
            cells=all_eles,
            # point_data=node_res,
            cell_data=cell_data,
            # field_data=field_data
        )

    def GetDatabaseData(self):
        """
        将数据打包至结果中，为后续插入数据库做准备
        :return:
        """
        parse_results = {}

        """
        写入材料表
        """
        materials_overview = []
        for mat_name, mat_cha_dict in self.materials.items():
            materials_overview.append((mat_name, "ISOTROPIC"))
        parse_results["materials_overview"] = materials_overview

        return parse_results


if __name__ == "__main__":
    # print(ReadSectionLine("*Beam Section, elset=_PickedSet8, material=Material-1, temperature=GRADIENTS, section=PIPE\n"))
    # input_file = r"../numerical example/ABAQUS/Job-1.inp"
    # input_file = f"D:/WorkSpace/FEM/NumericalCases/examples/ABAQUS/static/linear/Plane/aircraft-wing.inp"
    # input_file = r"D:\WorkSpace\WebThreeJS\PyModelToJson\model\inp\door.inp"
    # input_file = r"../PyModel2JsonDataFolder/model/inp/engine.inp"
    input_file = r"D:\WorkSpace\WebThreeJS\PyModel2JsonDataFolder\model\inp\door.inp"
    # input_file = r"model/inp/s4b.inp"
    npp = InpParser(input_path=input_file)
    npp.ParseFileAndInitFEMDB()
    npp.WriteVtuFile("../PyModel2JsonDataFolder/output/s4b.vtu")
    npp.GetDatabaseData()
