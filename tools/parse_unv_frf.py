from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd


def dof_label(node, direction):
    """
    把 node + direction 转成 FEMtools 类似的名字：
    node=3, direction=3 -> +3UZ
    node=29, direction=3 -> +29UZ
    """
    sign = "+" if direction >= 0 else "-"

    comp_map = {
        1: "UX",
        2: "UY",
        3: "UZ",
        4: "RX",
        5: "RY",
        6: "RZ",
    }

    comp = comp_map.get(abs(direction), f"DOF{direction}")

    return f"{sign}{node}{comp}"


def femtools_like_name(index, response_node, response_dir, reference_node, reference_dir):
    rsp = dof_label(response_node, response_dir)
    ref = dof_label(reference_node, reference_dir)
    return f"TEST FRF {index} ({rsp} : {ref})"

def dof_only_label(direction):
    """
    一级表格里用：
    direction=3 -> +UZ
    """
    direction = int(direction)

    if direction == 0:
        return "NONE"

    sign = "+" if direction > 0 else "-"

    comp_map = {
        1: "UX",
        2: "UY",
        3: "UZ",
        4: "RX",
        5: "RY",
        6: "RZ",
    }

    comp = comp_map.get(abs(direction), f"DOF{abs(direction)}")
    return f"{sign}{comp}"


def frf_type_text(y_type, denominator_type):
    """
    根据 UNV Dataset 58 的物理量类型生成 FEMtools 类似的 Type。
    你的文件里：
    y_type = 12
    denominator_type = 13
    所以是 ACCELERANCE (A/F)
    """
    y_type = int(y_type)
    denominator_type = int(denominator_type)

    if y_type == 12 and denominator_type == 13:
        return "ACCELERANCE (A/F)"

    if y_type == 14 and denominator_type == 13:
        return "MOBILITY (V/F)"

    if y_type in (11, 17) and denominator_type == 13:
        return "RECEPTANCE (D/F)"

    return f"TYPE {y_type}/{denominator_type}"


def _to_float(s):
    # UNV 里有时会用 D 作为科学计数法
    return float(s.replace("D", "E").replace("d", "E"))


def _read_unv_blocks(filename):
    """
    把 UNV 文件按 dataset 分块。
    返回: [(dataset_id, records), ...]
    """
    lines = Path(filename).read_text(errors="ignore").splitlines()

    blocks = []
    i = 0
    n = len(lines)

    while i < n:
        if lines[i].strip() != "-1":
            i += 1
            continue

        if i + 1 >= n:
            break

        dataset_id = lines[i + 1].strip()

        # 遇到连续 -1，跳过
        if dataset_id == "-1":
            i += 1
            continue

        start = i + 2
        j = start

        while j < n and lines[j].strip() != "-1":
            j += 1

        blocks.append((dataset_id, lines[start:j]))
        i = j + 1

    return blocks


def parse_unv58(filename):
    """
    解析 UNV / UFF Dataset 58。

    返回一个 list。
    每个元素是一条曲线:
    {
        "freq": ndarray,
        "real": ndarray,
        "imag": ndarray,
        "complex": ndarray,
        "meta": dict
    }
    """
    curves = []

    for dataset_id, records in _read_unv_blocks(filename):
        if dataset_id != "58":
            continue

        # Dataset 58:
        # records[0:5]  是 5 行 ID
        # records[5]    是 record 6
        # records[6]    是 record 7
        # records[7:11] 是 record 8~11
        r6 = records[5].split()
        r7 = records[6].split()
        r8 = records[7].split()
        r9 = records[8].split()
        r10 = records[9].split()
        r11 = records[10].split()

        ordinate_type = int(r7[0])
        n_points = int(r7[1])
        abscissa_spacing = int(r7[2])
        x_min = _to_float(r7[3])
        x_inc = _to_float(r7[4])

        # 数据区从 records[11] 开始
        values = []
        for line in records[11:]:
            for s in line.split():
                values.append(_to_float(s))

        values = np.asarray(values, dtype=float)

        # ordinate_type:
        # 2/4: real
        # 5/6: complex
        #
        # abscissa_spacing = 0:
        #   x 显式给出
        #   complex 数据格式为: x, real, imag, x, real, imag, ...
        #
        # abscissa_spacing != 0:
        #   x 用 x_min + i*x_inc 生成
        #   complex 数据格式为: real, imag, real, imag, ...

        if abscissa_spacing == 0:
            if ordinate_type in (5, 6):
                data = values.reshape(-1, 3)
                freq = data[:, 0]
                real = data[:, 1]
                imag = data[:, 2]

            elif ordinate_type in (2, 4):
                data = values.reshape(-1, 2)
                freq = data[:, 0]
                real = data[:, 1]
                imag = np.zeros_like(real)

            else:
                raise ValueError(f"Unsupported ordinate type: {ordinate_type}")

        else:
            freq = x_min + x_inc * np.arange(n_points)

            if ordinate_type in (5, 6):
                data = values.reshape(-1, 2)
                real = data[:, 0]
                imag = data[:, 1]

            elif ordinate_type in (2, 4):
                real = values[:n_points]
                imag = np.zeros_like(real)

            else:
                raise ValueError(f"Unsupported ordinate type: {ordinate_type}")

        if len(freq) != n_points:
            raise ValueError(
                f"Point count mismatch: header={n_points}, parsed={len(freq)}"
            )

        h = real + 1j * imag

        response_node = int(r6[5])
        response_dir = int(r6[6])
        reference_node = int(r6[8])
        reference_dir = int(r6[9])

        x_type = int(r8[0])
        y_type = int(r9[0])
        denominator_type = int(r10[0])
        z_type = int(r11[0])

        curve_index = len(curves) + 1

        # FEMtools 里 Range 显示为 1，你这个文件 r6[3] 是 0，所以这里 +1
        range_no = int(r6[3]) + 1

        title = records[0].strip()
        if title == "":
            title = "NONE"

        curve_type = frf_type_text(y_type, denominator_type)

        name = femtools_like_name(
            curve_index,
            response_node,
            response_dir,
            reference_node,
            reference_dir
        )

        curves.append({
            "name": name,
            "freq": freq,
            "real": real,
            "imag": imag,
            "complex": h,
            "meta": {
                "index": curve_index,
                "range": range_no,

                "ordinate_type": ordinate_type,
                "n_points": n_points,
                "abscissa_spacing": abscissa_spacing,

                "response_node": response_node,
                "response_dir": response_dir,
                "reference_node": reference_node,
                "reference_dir": reference_dir,

                "x_type": x_type,
                "y_type": y_type,
                "denominator_type": denominator_type,
                "z_type": z_type,

                "type": curve_type,
                "title": title,
            }
        })

    return curves

def plot_frf_curve(c, kind="Magnitude"):
    """
    c 是 parse_unv58 返回的某一条曲线，例如 curves[0]
    kind 可选：
        "Magnitude"
        "Phase"
        "Real"
        "Imaginary"
        "Real / Imaginary"
        "Bode Plot"
        "Nyquist"
    """

    f = c["freq"]
    h = c["complex"]
    real = c["real"]
    imag = c["imag"]

    name = c.get("name", "FRF")

    if kind == "Magnitude":
        plt.figure()
        plt.plot(f, np.abs(h))
        plt.yscale("log")   # FEMtools 这种 magnitude 通常是对数纵轴
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Magnitude")
        plt.title(name)
        plt.grid(True, which="both")
        plt.show()

    elif kind == "Phase":
        phase = np.angle(h, deg=True)

        plt.figure()
        plt.plot(f, phase)
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Phase [deg]")
        plt.title(name)
        plt.grid(True)
        plt.show()

    elif kind == "Real":
        plt.figure()
        plt.plot(f, real)
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Real")
        plt.title(name)
        plt.grid(True)
        plt.show()

    elif kind == "Imaginary":
        plt.figure()
        plt.plot(f, imag)
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Imaginary")
        plt.title(name)
        plt.grid(True)
        plt.show()

    elif kind == "Real / Imaginary":
        plt.figure()
        plt.plot(f, real, label="Real")
        plt.plot(f, imag, label="Imaginary")
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Value")
        plt.title(name)
        plt.legend()
        plt.grid(True)
        plt.show()

    elif kind == "Bode Plot":
        mag = np.abs(h)
        phase = np.angle(h, deg=True)

        plt.figure()

        plt.subplot(2, 1, 1)
        plt.plot(f, mag)
        plt.yscale("log")
        plt.ylabel("Magnitude")
        plt.grid(True, which="both")

        plt.subplot(2, 1, 2)
        plt.plot(f, phase)
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Phase [deg]")
        plt.grid(True)

        plt.suptitle(name)
        plt.tight_layout()
        plt.show()

    elif kind == "Nyquist":
        plt.figure()
        plt.plot(real, imag)
        plt.xlabel("Real")
        plt.ylabel("Imaginary")
        plt.title(name)
        plt.axis("equal")
        plt.grid(True)
        plt.show()

    else:
        raise ValueError(f"Unknown kind: {kind}")

def frf_to_table(c, unwrap_phase=False):
    """
    把一条 FRF 曲线转换成表格数据。

    c: parse_unv58 返回的某一条曲线，比如 curves[0]
    """

    f = c["freq"]
    h = c["complex"]

    if unwrap_phase:
        phase = np.unwrap(np.angle(h)) * 180 / np.pi
    else:
        phase = np.angle(h, deg=True)

    df = pd.DataFrame({
        "Frequency": f,
        "Magnitude": np.abs(h),
        "Phase": phase,
        "Real": h.real,
        "Imaginary": h.imag,
    })

    return df

def get_curve_names(curves):
    """
    返回所有曲线名称列表。
    """
    return [c["name"] for c in curves]


def print_curve_names(curves):
    """
    打印所有曲线名称。
    """
    print("曲线 name 列表：")
    for i, c in enumerate(curves, start=1):
        print(f"{i}: {c['name']}")


def frf_summary_table(curves):
    """
    一级表格：
    每一行是一条 FRF 曲线。
    类似 FEMtools 下面那个总览表。
    """

    rows = []

    for i, c in enumerate(curves, start=1):
        meta = c["meta"]

        rows.append({
            "#": i,
            "Range": meta["range"],
            "Response": meta["response_node"],
            "DOF": dof_only_label(meta["response_dir"]),
            "Excitation": meta["reference_node"],
            "DOF ": dof_only_label(meta["reference_dir"]),
            "Type": meta["type"],
            "Title": meta["title"],
        })

    df = pd.DataFrame(rows)
    return df


def print_frf_summary_table(curves):
    """
    打印一级表格。
    """
    df = frf_summary_table(curves)
    print(df.to_string(index=False))
    return df


def print_frf_table(df, n=20):
    """
    打印二级表格，也就是某条 FRF 的频点数据。
    """
    print(
        df.head(n).to_string(
            index=False,
            formatters={
                "Frequency": lambda x: f"{x:.3f}",
                "Magnitude": lambda x: f"{x:.5E}",
                "Phase": lambda x: f"{x:.5E}",
                "Real": lambda x: f"{x:.5E}",
                "Imaginary": lambda x: f"{x:.5E}",
            }
        )
    )


if __name__ == '__main__':
    curves = parse_unv58(r"C:\FEMtools\3.7.1\examples\updating\disk_frf\frf16.unv")
    # 画图
    curve_no = 48  # 1 表示 TEST FRF 1，2 表示 TEST FRF 2
    #
    # c = curves[curve_no - 1]
    c = curves[0]
    #
    # plot_frf_curve(c, kind="Magnitude")
    # plot_frf_curve(c, kind="Phase")
    # plot_frf_curve(c, kind="Real")
    # plot_frf_curve(c, kind="Imaginary")
    # plot_frf_curve(c, kind="Real / Imaginary")
    # plot_frf_curve(c, kind="Bode Plot")
    # plot_frf_curve(c, kind="Nyquist")



    # table
    # df1 = frf_to_table(curves[1])
    # print(df1.head(20))
    # 1. 打印曲线 name 列表
    # print_curve_names(curves)
    # 2. 打印一级表格
    # summary_df = print_frf_summary_table(curves)
    # 5. 打印二级表格
    df = frf_to_table(c)
    print_frf_table(df, n=20)