#!/usr/bin/env python
# -*- coding: utf-8 -*-

class NoSupportDimension(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return "不支持的分析维度:\n{}".format(self.err_msg)


class InputTextFormat(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return "该行导入文件未能解析成功:\n{}".format(self.err_msg)


class NoImplSuchElement(Exception):
    def __init__(self, e_type, e_id):
        self.err_msg = "element_type: {}, element_id: {}".format(e_type, e_id)

    def __str__(self):
        return "未实现该类型的单元:\n{}".format(self.err_msg)


class ElementNoUNV(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return "这类单元没有UNV对应格式:\n{}".format(self.err_msg)


class NoImplSuchMaterialStress(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return "未实现该类型材料的应力计算:\n{}".format(self.err_msg)


class NoSupportOption(Exception):
    def __init__(self, ele_type, opt):
        self.ele_type = ele_type
        self.opt = opt

    def __str__(self):
        return "单元类型{}不支持{}选项".format(self.ele_type, self.opt)


class NoImplSuchMaterial(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return "未实现该材料类型:\n{}".format(self.err_msg)


class NoImplSuchShapeFunction(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return "未实现该单元形函数:\n{}".format(self.err_msg)


class NoImplSuchElasticityModulus(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return "未实现该材料本构:\n{}".format(self.err_msg)


class OtherException(Exception):
    def __init__(self, err_msg):
        self.err_msg = err_msg

    def __str__(self):
        return self.err_msg
