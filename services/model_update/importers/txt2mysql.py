import os
import math


# ============================================================
# 文件路径
# ============================================================
NODES_TXT = "nodes.txt"
LINES_TXT = "lines.txt"
MODES_TXT = "modes.txt"

OUT_UNV = "model_real_modes_pure_python.unv"


# ============================================================
# 相位单位
# "deg" = 角度
# "rad" = 弧度
# ============================================================
PHASE_UNIT = "deg"


# ============================================================
# 实数振型导出方式
#
# "real_part" : 使用 A * cos(phase)，推荐
# "amp"       : 只使用幅值 A，忽略相位
# ============================================================
REAL_EXPORT_MODE = "real_part"


# ============================================================
# TXT 读取工具
# ============================================================

def clean_line(line):
    """去掉注释和空行。支持 # 注释。"""
    return line.split("#")[0].strip()


def split_values(line):
    """支持空格、Tab、逗号分隔。"""
    return line.replace(",", " ").split()


def read_nodes(filename):
    """
    读取 nodes.txt

    格式：
        node_id x y z
    """
    nodes = {}

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = clean_line(raw_line)
            if not line:
                continue

            p = split_values(line)

            if len(p) < 4:
                raise ValueError(f"节点文件格式错误：{raw_line}")

            node_id = int(p[0])
            x = float(p[1])
            y = float(p[2])
            z = float(p[3])

            nodes[node_id] = (x, y, z)

    return nodes


def read_lines(filename):
    """
    读取 lines.txt

    格式：
        node1 node2
    """
    lines = []

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = clean_line(raw_line)
            if not line:
                continue

            p = split_values(line)

            if len(p) < 2:
                raise ValueError(f"连线文件格式错误：{raw_line}")

            n1 = int(p[0])
            n2 = int(p[1])

            lines.append((n1, n2))

    return lines


def phase_to_rad(phase):
    """相位转弧度。"""
    if PHASE_UNIT == "deg":
        return math.radians(phase)
    elif PHASE_UNIT == "rad":
        return phase
    else:
        raise ValueError("PHASE_UNIT 只能是 'deg' 或 'rad'")


def amp_phase_to_real(amp, phase):
    """
    幅值 + 相位 转实数振型。

    real_part:
        u = A * cos(phase)

    amp:
        u = A
    """
    if REAL_EXPORT_MODE == "real_part":
        return amp * math.cos(phase_to_rad(phase))

    elif REAL_EXPORT_MODE == "amp":
        return amp

    else:
        raise ValueError("REAL_EXPORT_MODE 只能是 'real_part' 或 'amp'")


def read_modes(filename):
    """
    读取 modes.txt

    格式：
        mode freq damping node_id Ax Px Ay Py Az Pz

    其中：
        Ax, Ay, Az = x/y/z 方向幅值
        Px, Py, Pz = x/y/z 方向相位
    """
    modes = {}

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = clean_line(raw_line)
            if not line:
                continue

            p = split_values(line)

            if len(p) < 10:
                raise ValueError(f"振型文件格式错误：{raw_line}")

            mode_no = int(p[0])
            freq = float(p[1])
            damping = float(p[2])
            node_id = int(p[3])

            Ax = float(p[4])
            Px = float(p[5])
            Ay = float(p[6])
            Py = float(p[7])
            Az = float(p[8])
            Pz = float(p[9])

            ux = amp_phase_to_real(Ax, Px)
            uy = amp_phase_to_real(Ay, Py)
            uz = amp_phase_to_real(Az, Pz)

            if mode_no not in modes:
                modes[mode_no] = {
                    "mode": mode_no,
                    "freq": freq,
                    "damping": damping,
                    "shape": {},
                }

            modes[mode_no]["shape"][node_id] = (ux, uy, uz)

    return [modes[k] for k in sorted(modes.keys())]


# ============================================================
# 数据检查
# ============================================================

def check_data(nodes, lines, modes):
    node_ids = set(nodes.keys())

    for i, (n1, n2) in enumerate(lines, start=1):
        if n1 not in node_ids:
            raise ValueError(f"第 {i} 条连线的起点节点 {n1} 不存在")
        if n2 not in node_ids:
            raise ValueError(f"第 {i} 条连线的终点节点 {n2} 不存在")

    for mode in modes:
        mode_no = mode["mode"]
        shape_nodes = set(mode["shape"].keys())

        missing = node_ids - shape_nodes
        if missing:
            raise ValueError(
                f"第 {mode_no} 阶振型缺少这些节点的数据：{sorted(missing)}"
            )


# ============================================================
# UNV 写入工具
# ============================================================

def write_dataset_start(f, dataset_number):
    """
    UNV dataset 开始标记。
    """
    f.write(f"{-1:6d}\n")
    f.write(f"{dataset_number:6d}{'':74s}\n")


def write_dataset_end(f):
    """
    UNV dataset 结束标记。
    """
    f.write(f"{-1:6d}\n")


def write_2411_nodes(f, nodes):
    """
    Dataset 2411: Nodes - Double Precision

    每个节点两行：
        node_id, def_cs, disp_cs, color
        x, y, z
    """
    write_dataset_start(f, 2411)

    for node_id in sorted(nodes.keys()):
        x, y, z = nodes[node_id]

        def_cs = 0
        disp_cs = 0
        color = 1

        f.write(f"{node_id:10d}{def_cs:10d}{disp_cs:10d}{color:10d}\n")
        f.write(f"{x:25.16e}{y:25.16e}{z:25.16e}\n")

    write_dataset_end(f)


def write_82_trace_lines(f, lines):
    """
    Dataset 82: Trace Lines

    这里每一条线单独写一个 Dataset 82。
    nodes = [0, n1, n2]
    0 表示 move，后面的正节点号表示 draw。
    """
    for i, (n1, n2) in enumerate(lines, start=1):
        trace_num = i
        color = 1
        trace_nodes = [0, n1, n2]
        n_nodes = len(trace_nodes)

        write_dataset_start(f, 82)

        f.write(f"{trace_num:10d}{n_nodes:10d}{color:10d}\n")
        f.write(f"{('Line ' + str(i)):<80s}\n")

        # Dataset 82 每行最多 8 个整数
        for start in range(0, len(trace_nodes), 8):
            block = trace_nodes[start:start + 8]
            f.write("".join(f"{n:10d}" for n in block) + "\n")

        write_dataset_end(f)


def write_55_real_mode(f, mode, node_ids):
    """
    Dataset 55: Data at Nodes，实数普通模态。

    关键参数：
        analysis_type = 2  Normal Mode
        data_ch       = 2  3 DOF Global Translation Vector
        spec_data_type= 8  Displacement
        data_type     = 2  Real
        ndv           = 3  每个节点 3 个数据：Ux, Uy, Uz
    """
    mode_no = int(mode["mode"])
    freq = float(mode["freq"])
    damping = float(mode.get("damping", 0.0))
    shape = mode["shape"]

    write_dataset_start(f, 55)

    # Record 1-5: ID lines，每行 80 字符
    f.write(f"{('Mode ' + str(mode_no)):<80s}\n")
    f.write(f"{('Frequency ' + str(freq) + ' Hz'):<80s}\n")
    f.write(f"{'Real mode shape':<80s}\n")
    f.write(f"{'Generated by pure Python':<80s}\n")
    f.write(f"{'NONE':<80s}\n")

    # Record 6: Data Definition Parameters, FORMAT(6I10)
    model_type = 1       # Structural
    analysis_type = 2    # Normal Mode
    data_ch = 2          # 3 DOF Global Translation Vector
    spec_data_type = 8   # Displacement
    data_type = 2        # Real
    ndv = 3              # Ux, Uy, Uz

    f.write(
        f"{model_type:10d}"
        f"{analysis_type:10d}"
        f"{data_ch:10d}"
        f"{spec_data_type:10d}"
        f"{data_type:10d}"
        f"{ndv:10d}\n"
    )

    # Record 7: Normal Mode integer parameters
    # FORMAT(8I10)，这里只写前 4 个：
    # n_int=2, n_real=4, load_case, mode_number
    load_case = 1
    n_int = 2
    n_real = 4

    f.write(
        f"{n_int:10d}"
        f"{n_real:10d}"
        f"{load_case:10d}"
        f"{mode_no:10d}\n"
    )

    # Record 8: Normal Mode real parameters
    # frequency, modal_mass, viscous_damping, hysteretic_damping
    modal_mass = 0.0
    modal_damp_vis = damping
    modal_damp_his = 0.0

    f.write(
        f"{freq:13.5e}"
        f"{modal_mass:13.5e}"
        f"{modal_damp_vis:13.5e}"
        f"{modal_damp_his:13.5e}\n"
    )

    # Record 9-10 repeated:
    # node_id 一行
    # ux uy uz 一行
    for node_id in node_ids:
        ux, uy, uz = shape[node_id]

        f.write(f"{node_id:10d}\n")
        f.write(f"{ux:13.5e}{uy:13.5e}{uz:13.5e}\n")

    write_dataset_end(f)


def write_unv(filename, nodes, lines, modes):
    """
    写出 UNV 文件。
    """
    node_ids = sorted(nodes.keys())

    with open(filename, "w", encoding="utf-8") as f:
        write_2411_nodes(f, nodes)
        write_82_trace_lines(f, lines)

        for mode in modes:
            write_55_real_mode(f, mode, node_ids)


# ============================================================
# 主程序
# ============================================================

def main():
    if os.path.exists(OUT_UNV):
        os.remove(OUT_UNV)

    nodes = read_nodes(NODES_TXT)
    lines = read_lines(LINES_TXT)
    modes = read_modes(MODES_TXT)

    check_data(nodes, lines, modes)

    write_unv(OUT_UNV, nodes, lines, modes)

    print("UNV 文件已生成：", OUT_UNV)
    print("节点数：", len(nodes))
    print("连线数：", len(lines))
    print("模态数：", len(modes))
    print("实数导出方式：", REAL_EXPORT_MODE)


if __name__ == "__main__":
    main()
