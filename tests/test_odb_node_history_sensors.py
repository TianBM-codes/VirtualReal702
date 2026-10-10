"""tools/odb_node_history.py 的试验测点功能：测点表解析、坐标匹配最近节点、方向投影。

脚本 import 时需要 Abaqus 的 odbAccess，这里塞一个空模块占位，ODB 用最小的假对象模拟，
只覆盖纯 Python 逻辑。Abaqus 坐标变换（getTransformedField）本身算得对不对，
要在真 ODB 上和 CAE 对照（做法见脚本文档）。
"""
import csv
import importlib
import sys
import types

import numpy as np
import pytest


@pytest.fixture
def onh(monkeypatch):
    monkeypatch.setitem(sys.modules, "odbAccess", types.ModuleType("odbAccess"))
    monkeypatch.delitem(sys.modules, "tools.odb_node_history", raising=False)
    mod = importlib.import_module("tools.odb_node_history")
    monkeypatch.setattr(mod, "_ELEM_NODAL", "ELEMENT_NODAL")
    monkeypatch.setattr(mod, "_CARTESIAN", "CARTESIAN")
    return mod


class Node(object):
    def __init__(self, label, xyz, inst):
        self.label, self.coordinates, self.instanceName = label, tuple(xyz), inst


class Instance(object):
    def __init__(self, name, nodes, n_elems=1):
        self.name = name
        self.nodes = [Node(label, xyz, name) for label, xyz in nodes]
        self.elements = [object()] * n_elems

    def getNodeFromLabel(self, label):
        for n in self.nodes:
            if n.label == label:
                return n
        raise KeyError(label)


class Asm(object):
    def __init__(self, instances):
        self.instances = dict((i.name, i) for i in instances)
        self.nodeSets = {}
        self.datumCsyses = {}
        self.csys_calls = []

    def NodeSetFromNodeLabels(self, name, nodeLabels):
        self.nodeSets[name] = name
        return name

    def DatumCsysByThreePoints(self, **kw):
        self.csys_calls.append(kw)
        self.datumCsyses[kw["name"]] = kw["name"]
        return kw["name"]


class Value(object):
    def __init__(self, node, data, sp=None, inst="P-1"):
        self.nodeLabel, self.data = node, data
        self.instance = types.SimpleNamespace(name=inst)
        self.sectionPoint = None if sp is None else types.SimpleNamespace(description=sp)


def write_table(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "sensors.csv"
    path.write_bytes(text.encode(encoding))
    return str(path)


def two_part_odb():
    return types.SimpleNamespace(
        rootAssembly=Asm([
            Instance("PART-1-1", [(1, (0, 0, 0)), (2, (10, 0, 0))]),
            Instance("PART-2-1", [(7, (10, 0, 0))]),              # 和 PART-1-1.2 重合（tie）
            Instance("RP", [(1, (10, 0, 0.5))], n_elems=0),       # 只有参考点，不参与匹配
        ]),
        parts={"PART-1": None, "PART-2": None})


def test_sensor_table_gbk_tab_comments_and_direction(onh, tmp_path):
    path = write_table(tmp_path, "# 注释\nName\tX\tY\tZ\tDX\tDY\tDZ\tinstance\t备注\n"
                                 "测点1\t1\t2\t3\t3\t4\t0\tpart-1-1\t上表面\n", "gbk")
    (s,) = onh.read_sensor_table(path)
    assert s["name"] == "测点1" and s["line"] == 3
    assert s["xyz"].tolist() == [1.0, 2.0, 3.0]
    assert np.allclose(s["direction"], [0.6, 0.8, 0.0])
    assert s["instance"] == "PART-1-1"


@pytest.mark.parametrize("text", [
    "name,x,y,z,dx,dy,dz\nA,0,0,0,1,,\n",          # 方向缺分量
    "name,x,y,z,dx,dy,dz\nA,0,0,0,0,0,0\n",        # 零向量
    "name,x,y,z\nA,0,0,0\nA,1,1,1\n",              # 重名
    "name,x,y,z,node\nA,,,,\n",                    # 既没坐标也没节点
    "x,y,z\n0,0,0\n",                              # 缺 name 列
])
def test_sensor_table_rejects_bad_rows(onh, tmp_path, text):
    with pytest.raises(SystemExit):
        onh.read_sensor_table(write_table(tmp_path, text))


def test_nearest_node_instance_part_tie_and_given_node(onh, tmp_path):
    odb = two_part_odb()
    sensors = onh.read_sensor_table(write_table(
        tmp_path, "name,x,y,z,node,instance\nA,1,0,0,,\nB,10,0,0.5,,\n"
                  "C,0,0,0,,part-2-1\nD,10,0,1,PART-1-1.2,\n"))
    inst_by_upper = dict((k, k) for k in odb.rootAssembly.instances)
    onh.locate_sensors(odb, sensors, inst_by_upper, None)
    a, b, c, d = sensors

    assert (a["inst"], a["node"], a["part"], a["note"]) == ("PART-1-1", 1, "PART-1", "")
    assert a["distance"] == pytest.approx(1.0)
    # RP 上的节点正好在 B 处，但没有单元的 instance 不参与；两个零件的重合节点要提示
    assert (b["inst"], b["node"]) == ("PART-1-1", 2)
    assert b["note"].startswith("also near PART-2-1.7")
    assert (c["inst"], c["node"]) == ("PART-2-1", 7) and c["distance"] == pytest.approx(10.0)
    assert (d["inst"], d["node"], d["note"]) == ("PART-1-1", 2, "given node")
    assert d["distance"] == pytest.approx(1.0)
    assert all(s["active"] for s in sensors)


def test_match_tol_skips_far_sensors(onh, tmp_path):
    odb = two_part_odb()
    sensors = onh.read_sensor_table(write_table(tmp_path, "name,x,y,z\nA,1,0,0\nB,0.2,0,0\n"))
    onh.locate_sensors(odb, sensors, dict((k, k) for k in odb.rootAssembly.instances), 0.5)
    assert not sensors[0]["active"] and sensors[0]["note"].startswith("too far")
    assert sensors[1]["active"]


def test_direction_csys_axis1_is_direction(onh):
    asm = Asm([])
    assert onh.make_direction_csys(asm, (0.6, 0.8, 0.0), 3) == "NODE_HISTORY_DIR_3"
    kw = asm.csys_calls[0]
    assert kw["coordSysType"] == "CARTESIAN" and kw["origin"] == (0.0, 0.0, 0.0)
    assert kw["point1"] == (0.6, 0.8, 0.0) and kw["point2"] == (0.0, 0.0, 1.0)


def test_tensor_projection_takes_11_and_averages_per_section_point(onh):
    dx, dy = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    transformed = {
        dx: [Value(5, (10.0, 1.0, 2.0), "SNEG"), Value(5, (20.0, 0.0, 0.0), "SNEG"),
             Value(5, (30.0, 0.0, 0.0), "SPOS"), Value(6, (99.0, 0.0, 0.0), "SNEG"),
             Value(5, (77.0, 0.0, 0.0), "SNEG", inst="Q-1")],
        dy: [Value(5, (-4.0, 0.0, 0.0), "SNEG")],
    }
    sub = types.SimpleNamespace(
        getTransformedField=lambda datumCsys: types.SimpleNamespace(values=transformed[datumCsys]))
    out = onh.project_element_nodal(sub, "P-1", {dx: {5}, dy: {5}}, lambda d: d)
    assert out == {("P-1", 5, "SNEG", dx): (15.0, 2), ("P-1", 5, "SPOS", dx): (30.0, 1),
                   ("P-1", 5, "SNEG", dy): (-4.0, 1)}


def test_export_writes_projection_rows_and_match_table(onh, tmp_path):
    def field(labels, values, transformed=None):
        def get_subset(region=None, position=None):
            return types.SimpleNamespace(
                values=values,
                getTransformedField=lambda datumCsys: types.SimpleNamespace(
                    values=transformed[datumCsys]))
        return types.SimpleNamespace(componentLabels=labels, getSubset=get_subset)

    s_labels = ("S11", "S22", "S33", "S12", "S13", "S23")
    frame = types.SimpleNamespace(frameValue=1.0, description="Increment 1: Step Time = 1", fieldOutputs={
        "U": field(("U1", "U2", "U3"), [Value(5, (1.0, 2.0, 3.0))]),
        "S": field(s_labels, [Value(5, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)),
                              Value(5, (3.0, 4.0, 5.0, 6.0, 7.0, 8.0))],
                   {"NODE_HISTORY_DIR_0": [Value(5, (10.0,) + (0.0,) * 5),
                                           Value(5, (20.0,) + (0.0,) * 5)]}),
    })
    odb = types.SimpleNamespace(rootAssembly=Asm([Instance("P-1", [(5, (0, 0, 0))])]), parts={},
                                steps={"Step-1": types.SimpleNamespace(frames=[frame])})
    sensors = onh.read_sensor_table(write_table(tmp_path, "name,node,dx,dy,dz\nSG1,P-1.5,0,3,4\n"))
    out = tmp_path / "out.csv"
    args = types.SimpleNamespace(odb="job.odb", nodes="", instance=None, nset="", fields="U,S",
                                 steps="", frames="all", out=str(out), match_tol=None,
                                 match_only=False)
    onh.export(odb, args, sensors)

    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == onh.CSV_HEADER                   # 列不变，extract_node_results.py 照常能读
    proj = dict((r["field"], r) for r in rows if r["component"] == "proj:SG1")
    assert float(proj["U"]["value"]) == pytest.approx(2.0 * 0.6 + 3.0 * 0.8)
    assert float(proj["S"]["value"]) == pytest.approx(15.0) and proj["S"]["n_elems"] == "2"
    assert (proj["S"]["instance"], proj["S"]["node"]) == ("P-1", "5")
    s11 = [r for r in rows if r["component"] == "S11"]
    assert len(s11) == 1 and float(s11[0]["value"]) == pytest.approx(2.0)

    with (tmp_path / "out_sensors.csv").open(encoding="utf-8-sig", newline="") as fh:
        (match,) = list(csv.DictReader(fh))
    assert (match["sensor"], match["instance"], match["node"], match["note"]) == (
        "SG1", "P-1", "5", "given node")
