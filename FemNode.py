#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as np


class FemNode(object):
    def __init__(self, nid, x=None, y=None, z=None, ics=None, ocs=None):
        super().__init__()
        self.id = nid  # 节点在导入文件中的编号

        if z is None:
            self.coord = np.asarray([x, y], dtype=float)
            self.origin_coord = np.asarray([x, y], dtype=float)
        else:
            self.coord = np.asarray([x, y, z], dtype=float)
            self.origin_coord = np.asarray([x, y, z], dtype=float)

        self.ics = ics
        self.ocs = ocs

    def __lt__(self, other):
        return self.id < other.id

    def __eq__(self, other):
        return self.id == other.id

    def GetId(self):
        return self.id

    def GetNodeCoord(self):
        return self.coord
