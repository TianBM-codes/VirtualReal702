#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
odb_explore.py — ODB 交互式探索工具（开发调试用）

用法（需 Abaqus 2024+，内置 Python 3）:
    abaqus python odb_explore.py /path/to/your.odb

ODB 只打开一次，然后可以反复输入命令查询，不用每次重启。
输入 help 查看所有命令，输入 quit 退出。
"""

import sys
import os

try:
    import odbAccess
except ImportError:
    print("ERROR: 必须用 'abaqus python odb_explore.py' 运行，不能用普通 python3")
    sys.exit(1)


# ─── 打开 ODB ─────────────────────────────────────────────────────────────────

def open_odb(path):
    if not os.path.exists(path):
        print("ERROR: 找不到文件: {}".format(path))
        sys.exit(1)
    print("正在打开 ODB，大文件可能需要一会儿...")
    odb = odbAccess.openOdb(path, readOnly=True)
    print("OK: {}".format(os.path.basename(path)))
    return odb


# ─── 命令实现 ─────────────────────────────────────────────────────────────────

def cmd_info(odb, _args):
    """显示 ODB 基本信息"""
    root = odb.rootAssembly
    instances = list(root.instances.keys())
    steps = list(odb.steps.keys())
    print("\n=== ODB 概览 ===")
    print("Instance 数量 : {}".format(len(instances)))
    print("Step 数量     : {}".format(len(steps)))
    for inst_name in instances:
        inst = root.instances[inst_name]
        print("  Instance: {}  节点={} 单元={}".format(
            inst_name, len(inst.nodes), len(inst.elements)))
    for step_name in steps:
        step = odb.steps[step_name]
        print("  Step: {}  帧数={}  类型={}".format(
            step_name, len(step.frames), step.procedure))
    print()


def cmd_instances(odb, _args):
    """列出所有 Instance"""
    root = odb.rootAssembly
    print("\n=== Instances ===")
    for name, inst in root.instances.items():
        elem_types = {}
        for e in inst.elements:
            t = e.type
            elem_types[t] = elem_types.get(t, 0) + 1
        type_str = "  ".join("{}:{}".format(k, v) for k, v in sorted(elem_types.items()))
        print("  {:<30} 节点={:<8} 单元={:<8} 类型: {}".format(
            name, len(inst.nodes), len(inst.elements), type_str))
    print()


def cmd_steps(odb, _args):
    """列出所有 Step 和帧信息"""
    print("\n=== Steps ===")
    for step_name, step in odb.steps.items():
        frames = step.frames
        print("  {} ({})  {} 帧".format(step_name, step.procedure, len(frames)))
        if len(frames) > 0:
            f0 = frames[0]
            fl = frames[-1]
            print("    frame[0]: id={} value={:.4g}  {}".format(
                f0.frameId, f0.frameValue, f0.description))
            if len(frames) > 1:
                print("    frame[{}]: id={} value={:.4g}  {}".format(
                    len(frames)-1, fl.frameId, fl.frameValue, fl.description))
    print()


def cmd_fields(odb, args):
    """列出某个 Step 的所有结果场。用法: fields <step_name>"""
    if not args:
        print("用法: fields <step_name>")
        print("可用 Step:", list(odb.steps.keys()))
        return
    step_name = args[0]
    if step_name not in odb.steps:
        print("找不到 Step: {}  可用: {}".format(step_name, list(odb.steps.keys())))
        return
    step = odb.steps[step_name]
    # 从最后一帧取字段列表（通常最完整）
    frame = step.frames[-1]
    print("\n=== Step '{}' 的结果场（最后一帧）===".format(step_name))
    for fname, fo in frame.fieldOutputs.items():
        comps = list(fo.componentLabels) if fo.componentLabels else []
        invs  = list(fo.validInvariants) if fo.validInvariants else []
        print("  {:<10} 位置={:<20} 分量={} 不变量={}".format(
            fname,
            str(fo.locations[0].position) if fo.locations else "?",
            comps, [str(i) for i in invs]))
    print()


def cmd_sets(odb, args):
    """列出某个 Instance 的集合。用法: sets <instance_name>"""
    root = odb.rootAssembly
    if not args:
        # 列出 assembly 级集合
        node_sets = list(root.nodeSets.keys())
        elem_sets = list(root.elementSets.keys())
        print("\n=== Assembly 级集合 ===")
        print("  节点集 ({})  : {}".format(len(node_sets), node_sets[:20]))
        print("  单元集 ({})  : {}".format(len(elem_sets), elem_sets[:20]))
        print("提示: sets <instance_name> 查看 Instance 级集合")
        print("可用 Instance:", list(root.instances.keys()))
        return
    inst_name = args[0]
    if inst_name not in root.instances:
        print("找不到 Instance: {}".format(inst_name))
        return
    inst = root.instances[inst_name]
    node_sets = list(inst.nodeSets.keys())
    elem_sets = list(inst.elementSets.keys())
    print("\n=== Instance '{}' 的集合 ===".format(inst_name))
    print("  节点集 ({})  : {}".format(len(node_sets), node_sets[:30]))
    print("  单元集 ({})  : {}".format(len(elem_sets), elem_sets[:30]))
    print()


def cmd_nodes(odb, args):
    """查看某个 Instance 的节点。用法: nodes <instance_name> [前N个，默认10]"""
    root = odb.rootAssembly
    if not args:
        print("用法: nodes <instance_name> [n]")
        print("可用 Instance:", list(root.instances.keys()))
        return
    inst_name = args[0]
    n = int(args[1]) if len(args) > 1 else 10
    if inst_name not in root.instances:
        print("找不到 Instance: {}".format(inst_name))
        return
    inst = root.instances[inst_name]
    nodes = inst.nodes
    print("\n=== Instance '{}' 节点（前{}个，共{}个）===".format(inst_name, n, len(nodes)))
    for node in list(nodes)[:n]:
        print("  label={:<8} coords=({:.4g}, {:.4g}, {:.4g})".format(
            node.label, node.coordinates[0], node.coordinates[1], node.coordinates[2]))
    print()


def cmd_elems(odb, args):
    """查看某个 Instance 的单元类型分布。用法: elems <instance_name>"""
    root = odb.rootAssembly
    if not args:
        print("用法: elems <instance_name>")
        print("可用 Instance:", list(root.instances.keys()))
        return
    inst_name = args[0]
    if inst_name not in root.instances:
        print("找不到 Instance: {}".format(inst_name))
        return
    inst = root.instances[inst_name]
    type_dist = {}
    for e in inst.elements:
        type_dist[e.type] = type_dist.get(e.type, 0) + 1
    print("\n=== Instance '{}' 单元类型分布 ===".format(inst_name))
    for etype, count in sorted(type_dist.items(), key=lambda x: -x[1]):
        print("  {:<15} {}个".format(etype, count))
    # 抽查第一个单元的连接关系
    first = list(inst.elements)[0]
    print("  抽查第一个单元: label={} type={} 节点={}".format(
        first.label, first.type, list(first.connectivity)))
    print()


def cmd_result(odb, args):
    """查看某个结果场的一个节点值。用法: result <step> <field> <frame_idx> <instance>"""
    if len(args) < 4:
        print("用法: result <step_name> <field_name> <frame_idx> <instance_name>")
        return
    step_name, field_name, frame_idx_str, inst_name = args[0], args[1], args[2], args[3]
    frame_idx = int(frame_idx_str)
    if step_name not in odb.steps:
        print("找不到 Step: {}".format(step_name))
        return
    step = odb.steps[step_name]
    frame = step.frames[frame_idx]
    if field_name not in frame.fieldOutputs:
        print("找不到字段: {}  可用: {}".format(field_name, list(frame.fieldOutputs.keys())))
        return
    fo = frame.fieldOutputs[field_name]
    # 取该 instance 的子集
    root = odb.rootAssembly
    if inst_name not in root.instances:
        print("找不到 Instance: {}".format(inst_name))
        return
    inst = root.instances[inst_name]
    subset = fo.getSubset(region=inst)
    values = subset.values
    print("\n=== {}/{} frame[{}] instance={} ===".format(
        step_name, field_name, frame_idx, inst_name))
    print("  共 {} 个值，前5个:".format(len(values)))
    for v in list(values)[:5]:
        print("  nodeLabel={} data={}".format(v.nodeLabel, v.data))
    print()


COMMANDS = {
    'info'      : (cmd_info,      "ODB 整体概览"),
    'instances' : (cmd_instances, "列出所有 Instance 及单元类型"),
    'steps'     : (cmd_steps,     "列出所有 Step 和帧信息"),
    'fields'    : (cmd_fields,    "列出某个 Step 的结果场  用法: fields <step>"),
    'sets'      : (cmd_sets,      "列出集合  用法: sets [instance]"),
    'nodes'     : (cmd_nodes,     "查看节点  用法: nodes <instance> [n]"),
    'elems'     : (cmd_elems,     "查看单元类型分布  用法: elems <instance>"),
    'result'    : (cmd_result,    "查看结果值  用法: result <step> <field> <frame_idx> <instance>"),
    'py'        : (None,          "执行任意 Python 表达式，odb/root/steps 直接可用  用法: py <表达式>"),
}


def cmd_help(_odb, _args):
    print("\n=== 可用命令 ===")
    for name, (_, desc) in COMMANDS.items():
        print("  {:<12} {}".format(name, desc))
    print("  {:<12} 退出".format("quit / exit"))
    print()


# ─── 主循环 ───────────────────────────────────────────────────────────────────

def repl(odb):
    cmd_info(odb, [])
    print("输入 help 查看命令，quit 退出\n")

    # py 命令可用的变量
    py_env = {
        'odb'   : odb,
        'root'  : odb.rootAssembly,
        'steps' : odb.steps,
    }

    while True:
        try:
            line = input("odb> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出")
            break
        if not line:
            continue
        if line in ("quit", "exit", "q"):
            print("退出")
            break
        if line in ("help", "h", "?"):
            cmd_help(odb, [])
            continue

        # py 命令：执行任意表达式，支持多行（空行结束）
        if line.startswith("py ") or line == "py":
            expr = line[3:].strip()
            if not expr:
                # 多行模式：连续输入，空行执行
                print("  多行模式，空行执行，Ctrl+C 取消")
                lines = []
                while True:
                    try:
                        l = input("... ")
                    except (EOFError, KeyboardInterrupt):
                        print("\n已取消")
                        break
                    if l == "":
                        break
                    lines.append(l)
                expr = "\n".join(lines)
            if not expr:
                continue
            try:
                # 先尝试 eval（表达式，有返回值）
                result = eval(expr, py_env)
                if result is not None:
                    print(result)
            except SyntaxError:
                # 是语句（赋值、for、print 等），用 exec
                try:
                    exec(expr, py_env)
                except Exception as e:
                    print("出错: {}".format(e))
            except Exception as e:
                print("出错: {}".format(e))
            continue

        parts = line.split()
        cmd_name = parts[0]
        cmd_args = parts[1:]
        if cmd_name not in COMMANDS:
            print("未知命令: {}  输入 help 查看可用命令".format(cmd_name))
            continue
        try:
            COMMANDS[cmd_name][0](odb, cmd_args)
        except Exception as e:
            print("出错: {}".format(e))


def main():
    if len(sys.argv) < 2:
        print("用法: abaqus python odb_explore.py /path/to/your.odb")
        sys.exit(1)
    odb_path = sys.argv[1]
    odb = open_odb(odb_path)
    try:
        repl(odb)
    finally:
        odb.close()
        print("ODB 已关闭")


if __name__ == "__main__":
    main()
