# -*- coding: utf-8 -*-
"""
odb_node_history.py — 从 Abaqus ODB 读取指定节点在各 step / frame 的结果，导出 CSV。

临时性的独立脚本（单文件，不依赖本仓库其他模块，拷走即可用），用于和试验数据
各加载级做对比。常量导入、实例名规范化、frame 选择等写法沿用
src/l1/abaqus_dump.py（Abaqus Python 2.7 / 3 都能跑）。

必须用 Abaqus 自带的 Python 运行：

    abaqus python odb_node_history.py --odb Job-1.odb --list
    abaqus python odb_node_history.py --odb Job-1.odb \\
        --nodes PART-1-1.101,PART-1-1.205 --fields U,S,E --out node_history.csv

参数：
  --odb       ODB 文件路径
  --list      只列出 instance / step / 每个 step 的帧数与可用场，然后退出
  --nodes     节点，逗号分隔。"INSTANCE.label"（推荐，节点编号只在 instance 内唯一）
              或纯编号（配合 --instance；模型只有一个 instance 时可省略）
  --instance  纯编号节点所属的 instance
  --nset      也可以直接给节点集名（assembly 级或 "INSTANCE.集合名"），与 --nodes 可同时用
  --fields    要导出的场，默认 U,S,E。应变优先用 E，ODB 里没有 E 时自动改用 LE
  --steps     只导出这些 step（逗号分隔），默认全部
  --frames    all（默认）| last | first_last | 逗号分隔的帧号
  --out       输出 CSV 路径，默认 <odb 名>_node_history.csv

输出 CSV（长表，一行一个数值，用 pandas 一行 pivot 即可转宽表）：
  step, step_index, frame_idx, frame_value, frame_desc,
  instance, node, field, section_point, component, value, n_elems

数值口径：
  - U 是节点量，直接取 U1/U2/U3，另给合位移 Magnitude。
  - S / E / LE 存在积分点上。脚本取外推到节点的值（ELEMENT_NODAL，即 CAE 里的
    "Unique Nodal"），节点周围各单元的外推值取算术平均（不做 75% 阈值），
    n_elems 记录参与平均的单元数；先平均分量再算不变量：S 给 Mises 和三个主应力，
    E/LE 给三个主应变。壳单元按截面点（section_point，如 SNEG/SPOS）分开输出，不混在一起平均。
"""
from __future__ import print_function

import argparse
import csv
import math
import os
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

TENSOR_FIELDS = ("S", "E", "LE")
CSV_HEADER = ["step", "step_index", "frame_idx", "frame_value", "frame_desc",
              "instance", "node", "field", "section_point", "component",
              "value", "n_elems"]


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


# ─── 节点解析 ─────────────────────────────────────────────────────────────────

def resolve_nodes(odb, nodes_text, default_instance, nset_names):
    """→ 有序去重的 [(INSTANCE, label)]。"""
    asm = odb.rootAssembly
    inst_by_upper = dict((_canon_inst(k), k) for k in asm.instances.keys())
    out = []

    for tok in _parse_csv_names(nodes_text):
        if "." in tok:
            inst, lab = tok.rsplit(".", 1)
        else:
            inst, lab = default_instance, tok
            if inst is None:
                if len(inst_by_upper) != 1:
                    raise SystemExit(
                        "ERROR: node '{}' has no instance; use INSTANCE.label or --instance "
                        "(instances: {})".format(tok, ", ".join(sorted(inst_by_upper))))
                inst = list(inst_by_upper)[0]
        inst = _canon_inst(inst)
        if inst not in inst_by_upper:
            raise SystemExit("ERROR: instance '{}' not in ODB (instances: {})".format(
                inst, ", ".join(sorted(inst_by_upper))))
        out.append((inst, int(lab)))

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

    seen = set()
    uniq = []
    for key in out:
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    if not uniq:
        raise SystemExit("ERROR: no nodes given (use --nodes and/or --nset)")
    return uniq, inst_by_upper


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


def rows_for_element_nodal(fo, region, inst, wanted):
    """S/E/LE → 外推到节点后按 (节点, 截面点) 对周围单元取平均。"""
    if _ELEM_NODAL is None:
        raise RuntimeError("ELEMENT_NODAL constant unavailable in this Abaqus version")
    sub = fo.getSubset(region=region, position=_ELEM_NODAL)
    labels = list(fo.componentLabels)
    acc = {}
    for v in sub.values:
        node = getattr(v, "nodeLabel", None)
        if node is None:
            continue
        key = (_canon_inst(v.instance.name), node, _sp_desc(v))
        if key[0] != inst or (key[0], key[1]) not in wanted:
            continue
        data = np.atleast_1d(np.asarray(v.data, dtype=float))
        if key in acc:
            acc[key][0] += data
            acc[key][1] += 1
        else:
            acc[key] = [data.copy(), 1]
    return dict((k, (labels, s / n, n)) for k, (s, n) in acc.items())


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def list_odb(odb):
    print("Instances:")
    for name in sorted(odb.rootAssembly.instances.keys()):
        inst = odb.rootAssembly.instances[name]
        print("  {:<30s} {} nodes".format(name, len(inst.nodes)))
    print("Steps:")
    for si, sname in enumerate(odb.steps.keys()):
        step = odb.steps[sname]
        frames = step.frames
        fields = sorted(frames[-1].fieldOutputs.keys()) if len(frames) else []
        print("  [{}] {:<24s} procedure={:<14s} frames={}".format(
            si, sname, str(getattr(step, "procedure", "")), len(frames)))
        print("       fields (last frame): {}".format(", ".join(fields)))


def export(odb, args):
    nodes, inst_by_upper = resolve_nodes(odb, args.nodes, args.instance, args.nset)
    wanted = set(nodes)
    print("Nodes ({}): {}".format(len(nodes), ", ".join(
        "{}.{}".format(i, n) for i, n in nodes[:20]) + (" ..." if len(nodes) > 20 else "")))
    regions = build_regions(odb, nodes, inst_by_upper)

    req_fields = [f.upper() for f in _parse_csv_names(args.fields)]
    step_filter = set(_parse_csv_names(args.steps))
    frame_spec = _parse_frame_spec(args.frames)

    out_path = args.out or os.path.splitext(os.path.basename(args.odb))[0] + "_node_history.csv"
    n_rows = 0
    missing = set()
    # Py2（Abaqus 2023 及更早）csv 要用二进制模式，否则 Windows 下每行多一个空行
    fh = open(out_path, "wb") if sys.version_info[0] < 3 else \
        open(out_path, "w", newline="", encoding="utf-8-sig")
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
                    for region, inst in regions:
                        try:
                            if tensor:
                                results.update(rows_for_element_nodal(fo, region, inst, wanted))
                            else:
                                results.update(rows_for_nodal(fo, region, inst, wanted))
                        except Exception as e:
                            print("  [warn] {} frame {} field {}: {}".format(sname, fi, name, e))
                    for key in sorted(results):
                        inst, node, sp = key
                        labels, data, n_el = results[key]
                        comps = list(zip(labels, data))
                        if tensor and len(labels) == len(data):
                            comps += tensor_invariants(labels, data, name)
                        elif not tensor and len(data) > 1:
                            comps.append(("Magnitude", float(np.linalg.norm(data[:3]))))
                        for comp, val in comps:
                            w.writerow([sname, si, fi, repr(float(frame.frameValue)),
                                        frame.description.strip(), inst, node, name, sp,
                                        comp, repr(float(val)), n_el])
                            n_rows += 1
                    absent = wanted - set((k[0], k[1]) for k in results)
                    if absent and fi == sel[-1]:
                        print("  [warn] {} {}: no data at {} node(s), e.g. {}".format(
                            sname, name, len(absent),
                            ", ".join("{}.{}".format(*k) for k in sorted(absent)[:5])))
    finally:
        fh.close()

    for sname, field in sorted(missing):
        print("  [info] step '{}' has no field {} (skipped)".format(sname, field))
    print("Wrote {} row(s) → {}".format(n_rows, os.path.abspath(out_path)))


def parse_args():
    p = argparse.ArgumentParser(description="Export node results of every step/frame from an ODB to CSV")
    p.add_argument("--odb", required=True)
    p.add_argument("--list", action="store_true", help="list instances/steps/fields and exit")
    p.add_argument("--nodes", default="", help="INSTANCE.label,... or bare labels with --instance")
    p.add_argument("--instance", default=None)
    p.add_argument("--nset", default="", help="node set name(s), assembly-level or INSTANCE.name")
    p.add_argument("--fields", default="U,S,E")
    p.add_argument("--steps", default="")
    p.add_argument("--frames", default="all", help="all | last | first_last | 0,5,10")
    p.add_argument("--out", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    if not os.path.exists(args.odb):
        raise SystemExit("ERROR: ODB not found: {}".format(args.odb))
    print("Opening {} ...".format(args.odb))
    odb = odbAccess.openOdb(args.odb, readOnly=True)
    try:
        if args.list:
            list_odb(odb)
        else:
            export(odb, args)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
