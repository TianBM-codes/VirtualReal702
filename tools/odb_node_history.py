# -*- coding: utf-8 -*-
"""
odb_node_history.py — 从 Abaqus ODB 读取指定节点在各 step / frame 的结果，导出 CSV；
也可以按试验测点坐标找最近节点，并给出沿测点测量方向的投影值，直接和试验数据对比。

临时性的独立脚本（单文件，不依赖本仓库其他模块，拷走即可用），用于和试验数据
各加载级做对比。常量导入、实例名规范化、frame 选择等写法沿用
src/l1/abaqus_dump.py（Abaqus Python 2.7 / 3 都能跑）。

必须用 Abaqus 自带的 Python 运行：

    abaqus python odb_node_history.py --odb Job-1.odb --list
    abaqus python odb_node_history.py --odb Job-1.odb \\
        --nodes PART-1-1.101,PART-1-1.205 --fields U,S,E --out node_history.csv

    # 试验测点：只按坐标找最近节点（所属 instance / part 和节点号），不导结果
    abaqus python odb_node_history.py --odb Job-1.odb --sensors sensors.csv --match-only
    # 试验测点：找节点 + 导出结果 + 沿测点方向的投影值
    abaqus python odb_node_history.py --odb Job-1.odb --sensors sensors.csv --out sg.csv

参数：
  --odb         ODB 文件路径
  --list        只列出 instance（及所属 part）/ step / 每个 step 的帧数与可用场，然后退出
  --nodes       节点，逗号分隔。"INSTANCE.label"（推荐，节点编号只在 instance 内唯一）
                或纯编号（配合 --instance；模型只有一个 instance 时可省略）
  --instance    纯编号节点所属的 instance
  --nset        也可以直接给节点集名（assembly 级或 "INSTANCE.集合名"），与 --nodes 可同时用
  --sensors     试验测点表 CSV（格式见下），可与 --nodes / --nset 同时用
  --match-only  只做测点 → 节点匹配，写出匹配表后退出
  --match-tol   最近节点距离超过此值的测点不导结果（匹配表 note 标 too far），默认不限
  --fields      要导出的场，默认 U,S,E。应变优先用 E，ODB 里没有 E 时自动改用 LE
  --steps       只导出这些 step（逗号分隔），默认全部
  --frames      all（默认）| last | first_last | 逗号分隔的帧号
  --out         输出 CSV 路径，默认 <odb 名>_node_history.csv

测点表（--sensors）：首行是列名（大小写不限，多余的列忽略），# 开头的行是注释；
UTF-8 或 Excel 默认的 GBK 编码都可以，逗号或 Tab 分隔。
  name        测点名，必填且不能重复
  x, y, z     测点坐标（ODB 全局坐标系、与模型同单位）→ 自动找距离最近的节点
  node        或者直接给节点（INSTANCE.label 或纯编号），给了就不按坐标找
  instance    可选：只在这个 instance 里找（测点贴在哪个零件上就填哪个，免得匹配到
              相邻零件的重合节点）；也是 node 列纯编号所属的 instance
  dx, dy, dz  可选：测量方向（不必是单位向量），填了才输出投影值
  例：
    name,x,y,z,dx,dy,dz
    SG1,120.0,35.5,0.0,1,0,0
    SG2,120.0,35.5,0.0,0.7071,0.7071,0
    D1,300.0,0.0,50.0,0,0,1

输出：
  1) 结果长表 CSV（--out，一行一个数值，用 pandas 一行 pivot 即可转宽表）：
       step, step_index, frame_idx, frame_value, frame_desc,
       instance, node, field, section_point, component, value, n_elems
     带方向的测点另有 component = "proj:<测点名>" 的行（field 仍是 U / S / E），即投影值。
  2) 用了 --sensors 时另写匹配表 <--out 去掉 .csv>_sensors.csv：
       sensor, x, y, z, dx, dy, dz, instance, part, node,
       node_x, node_y, node_z, distance, note

数值口径：
  - U 是节点量，直接取 U1/U2/U3，另给合位移 Magnitude。
  - S / E / LE 存在积分点上。脚本取外推到节点的值（ELEMENT_NODAL，即 CAE 里的
    "Unique Nodal"），节点周围各单元的外推值取算术平均（不做 75% 阈值），
    n_elems 记录参与平均的单元数；先平均分量再算不变量：S 给 Mises 和三个主应力，
    E/LE 给三个主应变。壳单元按截面点（section_point，如 SNEG/SPOS）分开输出，不混在一起平均。
  - 测点匹配：在候选 instance 的全部节点里找与测点直线距离最近的一个（默认跳过没有单元的
    instance，比如只放参考点的）。节点号只在 instance 内唯一（同一个 part 装配两次，两个
    instance 的节点号完全相同），所以结果报 instance + 节点号，part 名附带参考。
    distance 和单元尺寸相当甚至更大时，多半是坐标系或单位没对齐；别的 instance 里有
    差不多近的节点（如 tie 两侧的重合节点）时 note 会提示。
  - 方向投影（n 为单位化后的测量方向）：
      U       u·n，沿 n 的位移
      S/E/LE  nᵀ·T·n，沿 n 的正应力 / 正应变，应变片测到的就是这个量
    张量用 Abaqus 自己的坐标变换（getTransformedField）：建一个 1 轴 = n 的直角坐标系，
    把每个单元的外推值转过去取 11 分量，再按节点平均（截面点同样分开）。壳单元和带
    *Orientation 的单元的分量写在各自的局部坐标系里、应变的剪切分量是工程剪应变（2 倍关系），
    这些都交给 Abaqus 处理，不会拿全局方向去乘局部分量。壳只有面内应力，结果是 n 投影到
    壳面内那个方向上的值。
    在 CAE 里核对：按运行时打印的三个点建同样的直角坐标系，Result Options → Transformation
    选 User-specified 并选中它，平均阈值设 100%，查该节点的 S11 / E11（Unique Nodal）。
"""
from __future__ import print_function

import argparse
import codecs
import csv
import math
import os
import re
import sys

import numpy as np

try:
    import odbAccess
except ImportError:
    print("ERROR: odbAccess not found. Must run under 'abaqus python'.")
    sys.exit(1)

# ELEMENT_NODAL 常量：abaqusConstants 或 odbAccess 上取（同 abaqus_dump.py）
_ELEM_NODAL = None
try:
    from abaqusConstants import ELEMENT_NODAL as _ELEM_NODAL  # noqa: F401
except Exception:
    try:
        _ELEM_NODAL = odbAccess.ELEMENT_NODAL
    except Exception:
        pass

# 方向投影建直角坐标系用的常量，取法同上
_CARTESIAN = None
try:
    from abaqusConstants import CARTESIAN as _CARTESIAN  # noqa: F401
except Exception:
    _CARTESIAN = getattr(odbAccess, "CARTESIAN", None)

PY2 = sys.version_info[0] < 3
TENSOR_FIELDS = ("S", "E", "LE")
CSV_HEADER = ["step", "step_index", "frame_idx", "frame_value", "frame_desc",
              "instance", "node", "field", "section_point", "component",
              "value", "n_elems"]
MATCH_HEADER = ["sensor", "x", "y", "z", "dx", "dy", "dz", "instance", "part", "node",
                "node_x", "node_y", "node_z", "distance", "note"]
PROJ_PREFIX = "proj:"


# ─── 小工具（沿用 abaqus_dump.py） ────────────────────────────────────────────

def _canon_inst(name):
    """Instance 名统一大写（与 L1 流水线一致）。"""
    return name.upper() if name is not None else name


def _parse_csv_names(text):
    return [s.strip() for s in (text or "").split(",") if s.strip()]


def _parse_frame_spec(text):
    """all → None；last / first_last 原样返回；否则解析成升序帧号列表。"""
    spec = (text or "all").strip()
    if spec in ("", "all"):
        return None
    if spec in ("last", "first_last"):
        return spec
    frames = sorted(set(int(p) for p in spec.split(",") if p.strip()))
    if any(i < 0 for i in frames):
        raise ValueError("frame index must be >= 0")
    return frames or None


def _select_frames(n_frames, spec):
    if spec is None:
        return list(range(n_frames))
    if spec == "last":
        return [n_frames - 1] if n_frames else []
    if spec == "first_last":
        return sorted(set([0, n_frames - 1])) if n_frames else []
    return [i for i in spec if i < n_frames]


def _sp_desc(value):
    sp = getattr(value, "sectionPoint", None)
    if sp is None:
        return ""
    return str(getattr(sp, "description", sp)).strip()


def _open_csv(path):
    """Py2（Abaqus 2023 及更早）csv 要用二进制模式，否则 Windows 下每行多一个空行。
    两边都带 UTF-8 BOM，测点名有中文时 Excel 也能直接打开。"""
    if PY2:
        fh = open(path, "wb")
        fh.write(codecs.BOM_UTF8)
        return fh
    return open(path, "w", newline="", encoding="utf-8-sig")


def _console(text):
    """Py2 下字符串是 UTF-8 字节，中文 Windows 控制台是 GBK，打印前转一下免得乱码。"""
    if PY2:
        enc = getattr(sys.stdout, "encoding", None)
        if enc and enc.lower().replace("-", "") != "utf8":
            try:
                return text.decode("utf-8").encode(enc, "replace")
            except Exception:
                pass
    return text


def _fmt_num(x):
    return "" if x is None else repr(float(x))


def _dedupe(keys):
    seen = set()
    uniq = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    return uniq


def _part_name(odb, inst_name):
    """instance 对应的 part 名。属性名随 Abaqus 版本不同（同 abaqus_dump.py），都取不到时
    按 CAE 默认命名 "<part>-<序号>" 反推，且要求该 part 在 odb.parts 里确实存在。"""
    inst = odb.rootAssembly.instances[inst_name]
    name = getattr(inst, "partName", None)
    if not name:
        try:
            name = inst.part.name
        except Exception:
            name = None
    if not name:
        m = re.match(r"^(.+)-\d+$", inst_name)
        if m:
            parts = dict((_canon_inst(k), k) for k in odb.parts.keys())
            name = parts.get(_canon_inst(m.group(1)))
    return name or ""


# ─── 节点解析 ─────────────────────────────────────────────────────────────────

def _resolve_node_token(tok, default_instance, inst_by_upper, hint="--instance"):
    """"INSTANCE.label" 或纯编号 → (INSTANCE, label)。"""
    if "." in tok:
        inst, lab = tok.rsplit(".", 1)
    else:
        inst, lab = default_instance, tok
        if inst is None:
            if len(inst_by_upper) != 1:
                raise SystemExit(
                    "ERROR: node '{}' has no instance; use INSTANCE.label or {} "
                    "(instances: {})".format(tok, hint, ", ".join(sorted(inst_by_upper))))
            inst = list(inst_by_upper)[0]
    inst = _canon_inst(inst)
    if inst not in inst_by_upper:
        raise SystemExit("ERROR: instance '{}' not in ODB (instances: {})".format(
            inst, ", ".join(sorted(inst_by_upper))))
    try:
        return inst, int(lab)
    except ValueError:
        raise SystemExit("ERROR: bad node label in '{}'".format(tok))


def resolve_nodes(odb, nodes_text, default_instance, nset_names):
    """→ ([(INSTANCE, label)]（未去重，可能为空）, {INSTANCE: ODB 里的原名})。"""
    asm = odb.rootAssembly
    inst_by_upper = dict((_canon_inst(k), k) for k in asm.instances.keys())
    out = [_resolve_node_token(tok, default_instance, inst_by_upper)
           for tok in _parse_csv_names(nodes_text)]

    for name in _parse_csv_names(nset_names):
        ns = None
        if name in asm.nodeSets.keys():
            ns = asm.nodeSets[name]
        elif "." in name:
            inst, local = name.split(".", 1)
            real = inst_by_upper.get(_canon_inst(inst))
            if real is not None and local in asm.instances[real].nodeSets.keys():
                ns = asm.instances[real].nodeSets[local]
        if ns is None:
            raise SystemExit("ERROR: node set '{}' not found".format(name))
        # assembly 级集合的 nodes 是按 instance 分组的二维序列；instance 级是一维
        groups = ns.nodes
        if len(groups) and not hasattr(groups[0], "label"):
            for g in groups:
                for n in g:
                    out.append((_canon_inst(n.instanceName), n.label))
        else:
            for n in groups:
                out.append((_canon_inst(n.instanceName), n.label))
    return out, inst_by_upper


def build_regions(odb, nodes, inst_by_upper):
    """每个 instance 一个查询区域：优先建临时节点集（一次 getSubset 取全部节点），
    建不了时退回逐节点 OdbMeshNode。返回 [(region, INSTANCE)]。"""
    asm = odb.rootAssembly
    by_inst = {}
    for inst, lab in nodes:
        by_inst.setdefault(inst, []).append(lab)

    regions = []
    for inst, labels in sorted(by_inst.items()):
        real = inst_by_upper[inst]
        set_name = "_NODE_HISTORY_{}".format(len(regions))
        try:
            if set_name in asm.nodeSets.keys():
                ns = asm.nodeSets[set_name]
            else:
                ns = asm.NodeSetFromNodeLabels(name=set_name,
                                               nodeLabels=((real, tuple(labels)),))
            regions.append((ns, inst))
            continue
        except Exception as e:
            print("  [info] temporary node set failed ({}), querying node by node".format(e))
        for lab in labels:
            try:
                regions.append((asm.instances[real].getNodeFromLabel(lab), inst))
            except Exception:
                print("  [warn] node {}.{} not found, skipped".format(inst, lab))
    return regions


# ─── 试验测点：读表 + 坐标匹配最近节点 ─────────────────────────────────────────

def read_sensor_table(path):
    """读测点表 → [dict]。坐标 / 方向转成 numpy 数组，方向单位化；格式错误直接退出。"""
    if not os.path.exists(path):
        raise SystemExit("ERROR: sensor table not found: {}".format(path))
    with open(path, "rb") as fh:
        raw = fh.read()
    text = None
    for enc in ("utf-8-sig", "gbk"):          # Excel 另存的 CSV 默认是 GBK
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise SystemExit("ERROR: sensor table {} is neither UTF-8 nor GBK encoded".format(path))
    lines = [(no, line) for no, line in enumerate(text.splitlines(), 1)
             if line.strip() and not line.lstrip().startswith("#")]
    if len(lines) < 2:
        raise SystemExit("ERROR: sensor table {} has no data rows".format(path))
    delim = "\t" if ("\t" in lines[0][1] and "," not in lines[0][1]) else ","
    # Py2 的 csv 只认字节串：转回 UTF-8 再解析，之后字符串一律是 UTF-8 字节
    rows = csv.reader([line.encode("utf-8") if PY2 else line for _, line in lines],
                      delimiter=delim)
    rows = [[c.strip() for c in r] for r in rows]

    header = [h.lower() for h in rows[0]]
    col = dict((("name" if h == "sensor" else h), i) for i, h in enumerate(header) if h)
    if "name" not in col:
        raise SystemExit("ERROR: sensor table needs a 'name' column (got: {})".format(
            ", ".join(rows[0])))
    if "node" not in col and not all(k in col for k in ("x", "y", "z")):
        raise SystemExit("ERROR: sensor table needs x,y,z columns or a node column")

    sensors = []
    names = set()
    for (no, _), row in zip(lines[1:], rows[1:]):
        def cell(key):
            i = col.get(key)
            return row[i] if i is not None and i < len(row) else ""

        def vec(keys, what):
            given = [cell(k) for k in keys]
            if not any(given):
                return None
            if not all(given):
                raise SystemExit("ERROR: sensor table line {}: {} needs all of {}".format(
                    no, what, ",".join(keys)))
            try:
                return np.array([float(v) for v in given])
            except ValueError:
                raise SystemExit("ERROR: sensor table line {}: bad {} {}".format(
                    no, what, ",".join(given)))

        name = cell("name")
        if not name:
            raise SystemExit("ERROR: sensor table line {}: empty name".format(no))
        if name in names:
            raise SystemExit(_console("ERROR: sensor table line {}: duplicate name '{}'".format(
                no, name)))
        names.add(name)
        xyz = vec(("x", "y", "z"), "coordinates")
        node_tok = cell("node")
        if xyz is None and not node_tok:
            raise SystemExit("ERROR: sensor table line {}: needs x,y,z or node".format(no))
        direction = vec(("dx", "dy", "dz"), "direction")
        if direction is not None:
            norm = float(np.linalg.norm(direction))
            if norm == 0.0:
                raise SystemExit("ERROR: sensor table line {}: zero direction".format(no))
            direction = direction / norm
        inst = cell("instance")
        sensors.append({
            "name": name, "line": no, "xyz": xyz, "node_tok": node_tok,
            "instance": _canon_inst(inst) if inst else None, "direction": direction,
            # 以下由 locate_sensors 填
            "inst": None, "node": None, "part": "", "node_xyz": None,
            "distance": None, "note": "", "active": False,
        })
    return sensors


def locate_sensors(odb, sensors, inst_by_upper, match_tol):
    """给每个测点定节点：node 列直接解析；否则在候选 instance 的全部节点里找最近的。
    结果写回 sensor dict（inst / node / part / node_xyz / distance / note / active）。"""
    asm = odb.rootAssembly
    tables = {}
    parts = {}

    def node_table(inst):
        if inst not in tables:
            nodes = asm.instances[inst_by_upper[inst]].nodes
            print("  reading coordinates of {} node(s) in {} ...".format(len(nodes), inst))
            labels = np.array([n.label for n in nodes], dtype=np.int64)
            xyz = np.zeros((len(labels), 3))
            if len(labels):
                raw = np.array([n.coordinates for n in nodes], dtype=np.float64)
                k = min(3, raw.shape[1])
                xyz[:, :k] = raw[:, :k]           # 二维模型 z 保持 0
            tables[inst] = (labels, xyz)
        return tables[inst]

    # 默认候选：有单元的 instance（只放参考点之类的 instance 不参与）
    default_cands = [i for i in sorted(inst_by_upper)
                     if len(asm.instances[inst_by_upper[i]].elements)]

    for s in sensors:
        if s["instance"] is not None and s["instance"] not in inst_by_upper:
            raise SystemExit(_console("ERROR: sensor '{}': instance '{}' not in ODB "
                                      "(instances: {})".format(s["name"], s["instance"],
                                                               ", ".join(sorted(inst_by_upper)))))
        if s["node_tok"]:
            inst, lab = _resolve_node_token(s["node_tok"], s["instance"], inst_by_upper,
                                            hint="the instance column")
            try:
                c = asm.instances[inst_by_upper[inst]].getNodeFromLabel(lab).coordinates
            except Exception:
                raise SystemExit(_console("ERROR: sensor '{}': node {}.{} not found".format(
                    s["name"], inst, lab)))
            node_xyz = np.zeros(3)
            node_xyz[:len(c)] = [float(v) for v in c][:3]
            dist = None if s["xyz"] is None else float(np.linalg.norm(node_xyz - s["xyz"]))
            note = "given node"
        else:
            hits = []
            for inst in ([s["instance"]] if s["instance"] else default_cands):
                labels, xyz = node_table(inst)
                if not len(labels):
                    continue
                diff = xyz - s["xyz"]
                d2 = (diff * diff).sum(axis=1)
                k = int(np.argmin(d2))
                hits.append((math.sqrt(float(d2[k])), inst, int(labels[k]), xyz[k].copy()))
            if not hits:
                raise SystemExit(_console("ERROR: sensor '{}': no candidate nodes".format(
                    s["name"])))
            hits.sort(key=lambda h: h[0])
            dist, inst, lab, node_xyz = hits[0]
            note = ""
            # 别的 instance 里有差不多近的节点：典型是 tie / 共节点处两侧零件的重合节点
            eps = 1e-9 * (1.0 + float(np.abs(s["xyz"]).max()))
            if len(hits) > 1 and hits[1][0] <= dist * 1.1 + eps:
                note = "also near {}.{} (d={:.6g})".format(hits[1][1], hits[1][2], hits[1][0])
        if inst not in parts:
            parts[inst] = _part_name(odb, inst_by_upper[inst])
        s.update(inst=inst, node=lab, part=parts[inst], node_xyz=node_xyz,
                 distance=dist, note=note, active=True)
        if match_tol is not None and not s["node_tok"] and dist > match_tol:
            s["active"] = False
            s["note"] = "too far (> {:g})".format(match_tol) + ("; " + note if note else "")


def print_sensor_matches(sensors):
    print("Sensors ({}):".format(len(sensors)))
    width = max(len(s["name"]) for s in sensors)
    for s in sensors:
        line = "  {}  -> {}.{}".format(s["name"].ljust(width), s["inst"], s["node"])
        if s["distance"] is not None:
            line += "  d={:.6g}".format(s["distance"])
        if s["part"]:
            line += "  part={}".format(s["part"])
        if s["note"]:
            line += "  [{}]".format(s["note"])
        print(_console(line))


def write_sensor_matches(path, sensors):
    fh = _open_csv(path)
    try:
        w = csv.writer(fh)
        w.writerow(MATCH_HEADER)
        for s in sensors:
            xyz = s["xyz"] if s["xyz"] is not None else [None] * 3
            d = s["direction"] if s["direction"] is not None else [None] * 3
            w.writerow([s["name"]] + [_fmt_num(v) for v in xyz] + [_fmt_num(v) for v in d]
                       + [s["inst"], s["part"], s["node"]]
                       + [_fmt_num(v) for v in s["node_xyz"]]
                       + [_fmt_num(s["distance"]), s["note"]])
    finally:
        fh.close()


# ─── 数值计算 ─────────────────────────────────────────────────────────────────

def _tensor_from_components(labels, comps, field):
    """按分量名（S11、E12、LE23……）组装 3x3 对称张量，缺的分量补 0（平面应力等）。

    Abaqus 输出的剪切应变 E12 等是工程剪应变 γ，张量分量 = γ/2。
    """
    t = np.zeros((3, 3))
    for name, v in zip(labels, comps):
        suffix = name[len(field):]
        if len(suffix) != 2 or not suffix.isdigit():
            continue
        i, j = int(suffix[0]) - 1, int(suffix[1]) - 1
        if not (0 <= i < 3 and 0 <= j < 3):
            continue
        if i != j and field in ("E", "LE"):
            v = v / 2.0
        t[i, j] = t[j, i] = v
    return t


def tensor_invariants(labels, comps, field):
    t = _tensor_from_components(labels, comps, field)
    p = sorted(np.linalg.eigvalsh(t), reverse=True)
    out = []
    if field == "S":
        s = t - np.eye(3) * np.trace(t) / 3.0
        out.append(("Mises", math.sqrt(1.5 * float(np.sum(s * s)))))
    out += [("Max_Principal", float(p[0])), ("Mid_Principal", float(p[1])),
            ("Min_Principal", float(p[2]))]
    return out


def rows_for_nodal(fo, region, inst, wanted):
    """U 等节点量 → {(inst, node, ''): (labels, values, 1)}。"""
    sub = fo.getSubset(region=region)
    labels = list(fo.componentLabels)
    out = {}
    for v in sub.values:
        key = (_canon_inst(v.instance.name), v.nodeLabel, "")
        if key[0] != inst or (key[0], key[1]) not in wanted:
            continue
        data = np.atleast_1d(np.asarray(v.data, dtype=float))
        out[key] = (labels, data, 1)
    return out


def _element_nodal_subset(fo, region):
    if _ELEM_NODAL is None:
        raise RuntimeError("ELEMENT_NODAL constant unavailable in this Abaqus version")
    return fo.getSubset(region=region, position=_ELEM_NODAL)


def _average_element_nodal(values, inst, wanted, pick):
    """ELEMENT_NODAL 值按 (instance, 节点, 截面点) 对周围单元取算术平均。
    pick(value) 取出要平均的量 → {key: (平均值, 单元数)}。"""
    acc = {}
    for v in values:
        node = getattr(v, "nodeLabel", None)
        if node is None:
            continue
        key = (_canon_inst(v.instance.name), node, _sp_desc(v))
        if key[0] != inst or (key[0], key[1]) not in wanted:
            continue
        data = pick(v)
        if key in acc:
            acc[key][0] = acc[key][0] + data
            acc[key][1] += 1
        else:
            acc[key] = [data, 1]
    return dict((k, (s / n, n)) for k, (s, n) in acc.items())


def rows_for_element_nodal(fo, sub, inst, wanted):
    """S/E/LE → 外推到节点后按 (节点, 截面点) 对周围单元取平均。"""
    labels = list(fo.componentLabels)
    avg = _average_element_nodal(sub.values, inst, wanted,
                                 lambda v: np.atleast_1d(np.asarray(v.data, dtype=float)))
    return dict((k, (labels, data, n)) for k, (data, n) in avg.items())


# ─── 方向投影 ─────────────────────────────────────────────────────────────────

def make_direction_csys(asm, dkey, idx):
    """建 1 轴 = 方向 n 的直角坐标系（只在内存里，ODB 是只读打开的，不会写回）。"""
    if _CARTESIAN is None:
        raise RuntimeError("CARTESIAN constant unavailable in this Abaqus version")
    n = [float(c) for c in dkey]
    e = [0.0, 0.0, 0.0]
    e[int(np.argmin(np.abs(n)))] = 1.0       # 与 n 最不平行的坐标轴，只用来定 1-2 平面
    name = "NODE_HISTORY_DIR_{}".format(idx)
    existing = getattr(asm, "datumCsyses", None)
    if existing is not None and name in existing.keys():
        return existing[name]
    csys = asm.DatumCsysByThreePoints(name=name, coordSysType=_CARTESIAN,
                                      origin=(0.0, 0.0, 0.0), point1=tuple(n), point2=tuple(e))
    print("  [info] direction ({:.6g}, {:.6g}, {:.6g}) -> csys {}: origin (0,0,0), "
          "point on axis 1 = direction, point in 1-2 plane = ({:g}, {:g}, {:g})".format(
              n[0], n[1], n[2], name, e[0], e[1], e[2]))
    return csys


def project_element_nodal(sub, inst, nodes_by_dir, csys_for):
    """S/E/LE：外推值转到 1 轴 = n 的坐标系，取 11 分量（沿 n 的正应力 / 正应变），
    再和原始分量一样按 (节点, 截面点) 对周围单元取平均。
    nodes_by_dir: {方向: 该方向测点所在节点}  → {(inst, node, 截面点, 方向): (值, 单元数)}"""
    out = {}
    for dkey, nodes in nodes_by_dir.items():
        tsub = sub.getTransformedField(datumCsys=csys_for(dkey))
        wanted = set((inst, n) for n in nodes)
        avg = _average_element_nodal(tsub.values, inst, wanted,
                                     lambda v: float(np.atleast_1d(v.data)[0]))
        for (i, node, sp), (val, n_el) in avg.items():
            out[(i, node, sp, dkey)] = (val, n_el)
    return out


def project_nodal(results, sensors):
    """U 等节点向量：u·n。→ {(inst, node, '', 方向): (值, 1)}"""
    out = {}
    for s in sensors:
        hit = results.get((s["inst"], s["node"], ""))
        if hit is None:
            continue
        data = hit[1]
        k = min(3, len(data))
        if k < 2:                             # 标量场没有方向可言
            continue
        out[(s["inst"], s["node"], "", s["dkey"])] = (
            float(np.dot(data[:k], s["direction"][:k])), 1)
    return out


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def list_odb(odb):
    print("Instances:")
    for name in sorted(odb.rootAssembly.instances.keys()):
        inst = odb.rootAssembly.instances[name]
        part = _part_name(odb, name)
        print("  {:<30s} {} nodes{}".format(
            name, len(inst.nodes), "  (part {})".format(part) if part else ""))
    print("Steps:")
    for si, sname in enumerate(odb.steps.keys()):
        step = odb.steps[sname]
        frames = step.frames
        fields = sorted(frames[-1].fieldOutputs.keys()) if len(frames) else []
        print("  [{}] {:<24s} procedure={:<14s} frames={}".format(
            si, sname, str(getattr(step, "procedure", "")), len(frames)))
        print("       fields (last frame): {}".format(", ".join(fields)))


def export(odb, args, sensors):
    nodes, inst_by_upper = resolve_nodes(odb, args.nodes, args.instance, args.nset)
    out_path = args.out or os.path.splitext(os.path.basename(args.odb))[0] + "_node_history.csv"

    if sensors:
        locate_sensors(odb, sensors, inst_by_upper, args.match_tol)
        print_sensor_matches(sensors)
        match_path = os.path.splitext(out_path)[0] + "_sensors.csv"
        write_sensor_matches(match_path, sensors)
        print("Wrote sensor matches → {}".format(os.path.abspath(match_path)))
        if args.match_only:
            return
        nodes += [(s["inst"], s["node"]) for s in sensors if s["active"]]

    nodes = _dedupe(nodes)
    if not nodes:
        raise SystemExit("ERROR: no nodes given (use --nodes, --nset and/or --sensors)")
    wanted = set(nodes)
    print("Nodes ({}): {}".format(len(nodes), ", ".join(
        "{}.{}".format(i, n) for i, n in nodes[:20]) + (" ..." if len(nodes) > 20 else "")))
    regions = build_regions(odb, nodes, inst_by_upper)

    # 带方向的测点：同方向共用一个坐标系；张量投影按 instance → 方向 → 节点 分组
    proj = [s for s in sensors if s["active"] and s["direction"] is not None]
    dir_index = {}
    proj_nodes = {}
    for s in proj:
        s["dkey"] = tuple(round(float(c), 12) for c in s["direction"])
        dir_index.setdefault(s["dkey"], len(dir_index))
        proj_nodes.setdefault(s["inst"], {}).setdefault(s["dkey"], set()).add(s["node"])
    csys_cache = {}

    def csys_for(dkey):
        if dkey not in csys_cache:
            csys_cache[dkey] = make_direction_csys(odb.rootAssembly, dkey, dir_index[dkey])
        return csys_cache[dkey]

    req_fields = [f.upper() for f in _parse_csv_names(args.fields)]
    step_filter = set(_parse_csv_names(args.steps))
    frame_spec = _parse_frame_spec(args.frames)

    n_rows = 0
    missing = set()
    warned = set()
    proj_fields = set()      # 尝试过投影的场
    projected = set()        # 实际写出投影值的 (测点, 场)
    fh = _open_csv(out_path)
    try:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        for si, sname in enumerate(odb.steps.keys()):
            if step_filter and sname not in step_filter:
                continue
            frames = odb.steps[sname].frames
            sel = _select_frames(len(frames), frame_spec)
            print("Step [{}] {}: {} of {} frame(s)".format(si, sname, len(sel), len(frames)))
            for fi in sel:
                frame = frames[fi]
                fval = repr(float(frame.frameValue))
                fdesc = frame.description.strip()
                fos = frame.fieldOutputs
                for field in req_fields:
                    name = field
                    if name == "E" and "E" not in fos.keys() and "LE" in fos.keys():
                        name = "LE"          # 没有小应变 E 时用对数应变 LE
                    if name not in fos.keys():
                        missing.add((sname, field))
                        continue
                    fo = fos[name]
                    tensor = name in TENSOR_FIELDS
                    results = {}
                    proj_vals = {}
                    for region, inst in regions:
                        sub = None
                        try:
                            if tensor:
                                sub = _element_nodal_subset(fo, region)
                                results.update(rows_for_element_nodal(fo, sub, inst, wanted))
                            else:
                                results.update(rows_for_nodal(fo, region, inst, wanted))
                        except Exception as e:
                            print("  [warn] {} frame {} field {}: {}".format(sname, fi, name, e))
                        if tensor and inst in proj_nodes:
                            try:
                                if sub is None:
                                    sub = _element_nodal_subset(fo, region)
                                proj_vals.update(project_element_nodal(
                                    sub, inst, proj_nodes[inst], csys_for))
                            except Exception as e:
                                if (name, str(e)) not in warned:
                                    warned.add((name, str(e)))
                                    print("  [warn] {} direction projection failed: {}".format(
                                        name, e))
                    if not tensor:
                        proj_vals = project_nodal(results, proj)
                    if proj and (tensor or any(len(r[1]) >= 2 for r in results.values())):
                        proj_fields.add(name)

                    for key in sorted(results):
                        inst, node, sp = key
                        labels, data, n_el = results[key]
                        comps = list(zip(labels, data))
                        if tensor and len(labels) == len(data):
                            comps += tensor_invariants(labels, data, name)
                        elif not tensor and len(data) > 1:
                            comps.append(("Magnitude", float(np.linalg.norm(data[:3]))))
                        for comp, val in comps:
                            w.writerow([sname, si, fi, fval, fdesc, inst, node, name, sp,
                                        comp, repr(float(val)), n_el])
                            n_rows += 1
                    for s in proj:
                        hits = sorted((k[2], v) for k, v in proj_vals.items()
                                      if k[:2] == (s["inst"], s["node"]) and k[3] == s["dkey"])
                        for sp, (val, n_el) in hits:
                            w.writerow([sname, si, fi, fval, fdesc, s["inst"], s["node"], name,
                                        sp, PROJ_PREFIX + s["name"], repr(float(val)), n_el])
                            n_rows += 1
                            projected.add((s["name"], name))
                    absent = wanted - set((k[0], k[1]) for k in results)
                    if absent and fi == sel[-1]:
                        print("  [warn] {} {}: no data at {} node(s), e.g. {}".format(
                            sname, name, len(absent),
                            ", ".join("{}.{}".format(*k) for k in sorted(absent)[:5])))
    finally:
        fh.close()

    for sname, field in sorted(missing):
        print("  [info] step '{}' has no field {} (skipped)".format(sname, field))
    for s in proj:
        lacking = [f for f in sorted(proj_fields) if (s["name"], f) not in projected]
        if lacking:
            print(_console("  [warn] sensor {} ({}.{}): no projection for {}".format(
                s["name"], s["inst"], s["node"], ", ".join(lacking))))
    print("Wrote {} row(s) → {}".format(n_rows, os.path.abspath(out_path)))


def parse_args():
    p = argparse.ArgumentParser(description="Export node results of every step/frame from an ODB to CSV")
    p.add_argument("--odb", required=True)
    p.add_argument("--list", action="store_true", help="list instances/steps/fields and exit")
    p.add_argument("--nodes", default="", help="INSTANCE.label,... or bare labels with --instance")
    p.add_argument("--instance", default=None)
    p.add_argument("--nset", default="", help="node set name(s), assembly-level or INSTANCE.name")
    p.add_argument("--sensors", default=None,
                   help="sensor table CSV: name, x,y,z or node, optional dx,dy,dz / instance")
    p.add_argument("--match-only", action="store_true",
                   help="only match sensors to nearest nodes, write the match table and exit")
    p.add_argument("--match-tol", type=float, default=None,
                   help="skip sensors whose nearest node is farther than this distance")
    p.add_argument("--fields", default="U,S,E")
    p.add_argument("--steps", default="")
    p.add_argument("--frames", default="all", help="all | last | first_last | 0,5,10")
    p.add_argument("--out", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    if not os.path.exists(args.odb):
        raise SystemExit("ERROR: ODB not found: {}".format(args.odb))
    if args.match_only and not args.sensors:
        raise SystemExit("ERROR: --match-only needs --sensors")
    # 先读测点表：格式有错立刻报，不用等 ODB 打开
    sensors = read_sensor_table(args.sensors) if args.sensors else []
    if sensors:
        print("Sensor table: {} sensor(s), {} with a direction".format(
            len(sensors), sum(1 for s in sensors if s["direction"] is not None)))
    print("Opening {} ...".format(args.odb))
    odb = odbAccess.openOdb(args.odb, readOnly=True)
    try:
        if args.list:
            list_odb(odb)
        else:
            export(odb, args, sensors)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
