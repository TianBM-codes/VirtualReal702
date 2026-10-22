# -*- coding: utf-8 -*-
"""
定宽试验数据解析与测点曲线绘制

根据提供的两张 Python 脚本照片整理。
支持 GB18030、Windows CRLF、换页符 \\x0c、定宽测点列和多页数据。

常用命令：
    python DumpTestDataToDB.py
    python DumpTestDataToDB.py --list
    python DumpTestDataToDB.py --csv result.csv
    python DumpTestDataToDB.py --plot 310004a,310004b
    python DumpTestDataToDB.py --plot 310004a,310004b --save chart.png
    python DumpTestDataToDB.py --interactive

注意：原照片展示的是读取和绘图，不包含实际数据库连接或写入代码。
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class Gauge:
    """保留照片中的 Gauge 类名称，便于在现有项目中沿用。"""
    name: Optional[str] = None
    data: List[float] = field(default_factory=list)


@dataclass
class TestData:
    gauge_data: Dict[str, List[float]] = field(default_factory=dict)
    broken_gauge: List[str] = field(default_factory=list)
    except_gauges: List[dict] = field(default_factory=list)
    table_count: int = 0
    start_time: Optional[str] = None
    frequency: int = 40
    start_datetime: Optional[datetime] = None
    ax_infors: Dict[str, List[float]] = field(default_factory=dict)
    page_records: List[int] = field(default_factory=list)
    page_headers: List[List[str]] = field(default_factory=list)
    page_axes: List[Dict[str, List[float]]] = field(default_factory=list)


def detective_begin_num(table_lines: Sequence[str]) -> int:
    """从每页中找出第一个“序号”位于前 6 字符的测点数据行。"""
    for num, line in enumerate(table_lines):
        try:
            int(line[0:6])
            return num
        except ValueError:
            continue
    raise ValueError("页面中未找到以数值序号开头的测点数据行")


def get_step_information(table_lines: Sequence[str]) -> Dict[str, List[float]]:
    """提取“级别”“系数”等横轴或加载阶段信息。"""
    ax_tags: Dict[str, List[float]] = {}
    for line in table_lines:
        if not line.strip():
            continue
        tag = line[:19].replace(" ", "").strip()
        if not tag:
            continue
        try:
            values = [float(value) for value in line[19:].split()]
        except ValueError:
            # 非加载信息行（可能是备注、设备状态说明等），跳过。
            continue
        if values:
            ax_tags[tag] = values
    return ax_tags


def _parse_gauge_line(line: str) -> Tuple[int, str, List[float]]:
    """
    照片中的定宽格式：
      [0:6] 序号、[6:19] 测点名、[19:34] 第 1 个值，
      [34:43] 第 2 个值，此后各占 9 个字符。
    """
    if len(line) < 34:
        raise ValueError(f"测点行不足 34 字符：{line!r}")
    if (len(line) - 34) % 9 != 0:
        raise ValueError(
            f"测点数据列宽不匹配（总宽度 {len(line)}）："
            f"{line[:30]!r}..."
        )

    num = int(line[:6])
    gauge_name = line[6:19].strip()
    if not gauge_name:
        raise ValueError(f"序号 {num} 缺少测点名称")

    num_count = (len(line) - 34) // 9
    raw_values = [line[19:34]]
    raw_values += [
        line[34 + j * 9:43 + j * 9] for j in range(num_count)
    ]
    try:
        gauge_values = [float(value.strip()) for value in raw_values]
    except ValueError as exc:
        raise ValueError(
            f"测点 {gauge_name} 有无法解析的数值：{line!r}"
        ) from exc
    return num, gauge_name, gauge_values


def parse_table(part: bytes, test_data: TestData) -> None:
    """解析一页数据，并合并到 TestData 中。"""
    table = part.decode("gb18030")
    table_lines = table.replace("\r\n", "\n").split("\n")
    table_lines = [line for line in table_lines if line != ""]

    if not table_lines:
        return

    headline = table_lines[0]
    head_split = headline.split()
    if len(head_split) < 5:
        raise ValueError(f"不完整的表头：{headline!r}")

    begin_num = detective_begin_num(table_lines)

    if test_data.start_time is None:
        test_data.start_time = head_split[4]
    if test_data.start_datetime is None and len(head_split) >= 5:
        try:
            test_data.start_datetime = datetime.strptime(
                head_split[3] + " " + head_split[4],
                "%Y.%m.%d %H:%M:%S",
            )
        except ValueError:
            pass

    # 对应原脚本的 table_lines[2:begin_num-1]，跳过虚线和“序号”表头。
    ax_tags = get_step_information(table_lines[2:begin_num - 1])
    test_data.page_headers.append(table_lines[:begin_num])
    test_data.page_axes.append(ax_tags)
    if not test_data.ax_infors:
        test_data.ax_infors = ax_tags

    parsed_count = 0
    for line in table_lines[begin_num:]:
        if not line.strip():
            continue
        try:
            _, gauge_name, values = _parse_gauge_line(line)
        except (ValueError, IndexError) as exc:
            test_data.except_gauges.append(
                {"line_data": line, "error": str(exc)}
            )
            raise ValueError(
                f"第 {test_data.table_count + 1} 页解析失败：{exc}"
            ) from exc

        test_data.gauge_data.setdefault(gauge_name, []).extend(values)
        parsed_count += 1

    test_data.table_count += 1
    test_data.page_records.append(parsed_count)


def parse_txt(file_path: str | Path) -> TestData:
    """读取完整试验数据文件；原格式按 ASCII 换页符 0x0C 分页。"""
    test_data = TestData()
    data = Path(file_path).read_bytes()
    parts = data.split(b"\x0c")
    for part in parts:
        part = part.strip(b"\r\n")
        if part:
            parse_table(part, test_data)
    if not test_data.table_count:
        raise ValueError("文件中未找到试验数据页")
    return test_data


def get_plot_axis(test_data: TestData) -> Tuple[str, List[float]]:
    """优先选择“级别”，不存在时退回到其他可用的横轴。"""
    if not test_data.ax_infors:
        raise ValueError("未能解析到“级别”或其他加载信息")

    label = "级别" if "级别" in test_data.ax_infors else next(iter(test_data.ax_infors))
    ax = list(test_data.ax_infors[label])

    # 修正原照片中的零点截取问题：
    # 只在最末项为 0 且此前存在非零数值时，删除末项 0。
    # 不能按“最后一个 0 的位置”截取，否则 0,1,...,17 会得到空数组。
    if len(ax) > 1 and ax[-1] == 0 and any(v != 0 for v in ax[:-1]):
        ax.pop()
    return label, ax


def print_summary(file_path: str | Path, test_data: TestData) -> None:
    """输出与照片一致的核心汇总信息。"""
    print("Summary:")
    print(f"    文件: {file_path}")
    print(f"    表单个数: {test_data.table_count}")
    print(f"    各页测点行数: {test_data.page_records}")
    print(f"    解析的传感器个数: {len(test_data.gauge_data)}")
    print(f"    无法解析的传感器行数: {len(test_data.except_gauges)}")
    print(f"    解析横轴信息: {', '.join(test_data.ax_infors.keys())}")
    if test_data.start_datetime:
        print(f"    起始时间: {test_data.start_datetime:%Y-%m-%d %H:%M:%S}")


def export_csv(test_data: TestData, destination: str | Path) -> None:
    """
    每个测点占一行，保留所有已解析读数。
    Excel 可直接打开 UTF-8 BOM 编码的 CSV。
    """
    max_count = max(map(len, test_data.gauge_data.values()), default=0)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["测点名称"] + [f"读数{i}" for i in range(1, max_count + 1)]
        )
        for name, values in test_data.gauge_data.items():
            writer.writerow([
                name,
                *(int(x) if x.is_integer() else x for x in values),
            ])


def plot_gauges(
    test_data: TestData,
    gauge_names: Sequence[str],
    save_path: Optional[str | Path] = None,
    backend: Optional[str] = None,
) -> None:
    """
    按加载级别绘制测点曲线。

    修复 Windows 下 Qt 的 backend_qt.py / WinError 10038 问题：
    - 弹窗绘图默认选用 TkAgg，不依赖 Qt 的套接字事件循环；
    - 保存 PNG 默认选用 Agg，不创建任何 GUI 窗口；
    - 显式等待窗口关闭，并在 finally 中释放 Figure。
    """
    try:
        import matplotlib
    except ImportError as exc:
        raise RuntimeError(
            "绘图需要 matplotlib，请先运行：python -m pip install matplotlib"
        ) from exc

    backend_name = backend or ("Agg" if save_path else "TkAgg")
    if not save_path and backend_name.lower() == "agg":
        raise RuntimeError(
            "Agg 后端无法弹出绘图窗口。请使用 --save 保存图片，"
            "或者改为 --backend TkAgg。"
        )

    # 必须先选 backend，后导入 pyplot；不能在 import pyplot 之后再调用。
    matplotlib.use(backend_name, force=True)
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            f"绘图后端 {backend_name} 无法启动；"
            "可以尝试运行 python -m tkinter 检查 Tkinter，"
            "或添加 --save chart.png 以绕过图形界面。"
        ) from exc

    plt.rcParams["font.sans-serif"] = [
        "SimHei", "Microsoft YaHei", "Noto Sans CJK SC", "DejaVu Sans"
    ]
    plt.rcParams["axes.unicode_minus"] = False

    ax_label, x_values = get_plot_axis(test_data)
    fig = None
    try:
        try:
            fig, plot_ax = plt.subplots(figsize=(11, 5.5))
        except (ImportError, RuntimeError) as exc:
            raise RuntimeError(
                f"无法创建 {backend_name} 绘图窗口：{exc}。"
                "如果 TkAgg 不可用，可使用 --save chart.png 保存图像。"
            ) from exc

        plotted = 0
        for name in gauge_names:
            name = name.strip()
            if not name:
                continue
            values = test_data.gauge_data.get(name)
            if values is None:
                print(f"不存在传感器：{name}")
                continue
            count = min(len(x_values), len(values))
            plot_ax.plot(
                x_values[:count], values[:count],
                label=name, marker="o", markersize=3, linewidth=1.5
            )
            plotted += 1

        if not plotted:
            raise ValueError("所选传感器均不存在，无法绘图")

        plot_ax.set_xlabel(ax_label)
        plot_ax.set_ylabel("数值 (με)")
        plot_ax.set_title("测点数据曲线")
        plot_ax.grid(alpha=0.25)
        plot_ax.legend()
        fig.tight_layout()

        if save_path:
            destination = Path(save_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(destination, dpi=160)
            print(f"已保存曲线图：{destination}")
        else:
            # TkAgg 下会等待关闭图表，再回到 input() 询问下一个测点。
            plt.show(block=True)
    finally:
        if fig is not None:
            plt.close(fig)



def main(file_path: str | Path) -> TestData:
    """兼容原照片中的 main(file_path) 调用方式。"""
    result = parse_txt(file_path)
    print_summary(file_path, result)
    return result


def cli() -> None:
    default_file = Path(__file__).resolve().with_name("AT-2_lire.txt")
    parser = argparse.ArgumentParser(
        description="解析定宽试验数据，并可导出 CSV 或绘制测点曲线"
    )
    parser.add_argument(
        "file", nargs="?", type=Path, default=default_file,
        help="GB18030 试验数据文件（默认同目录 AT-2_lire.txt）"
    )
    parser.add_argument("--list", action="store_true", help="列出所有测点名称")
    parser.add_argument(
        "--csv", type=Path, metavar="FILE", help="导出所有测点数据到 CSV"
    )
    parser.add_argument(
        "--plot", metavar="NAMES", help="测点名称，逗号分隔，例如 310004a,310004b"
    )
    parser.add_argument(
        "--save", type=Path, metavar="PNG", help="配合 --plot 保存曲线图"
    )
    parser.add_argument(
        "--interactive", action="store_true", help="进入交互绘图模式，输入 exit 退出"
    )
    parser.add_argument(
        "--backend", choices=("TkAgg", "QtAgg", "Agg"),
        default=None,
        help="选择绘图后端（默认弹窗 TkAgg、保存图片 Agg）"
    )
    args = parser.parse_args()

    if args.save is not None and not args.plot:
        parser.error("--save 必须和 --plot 一起使用")
    t_data = main(args.file)

    if args.list:
        print("测点名称：")
        print(", ".join(t_data.gauge_data))
    if args.csv is not None:
        export_csv(t_data, args.csv)
        print(f"已导出 CSV：{args.csv}")
    if args.plot:
        plot_gauges(t_data, args.plot.split(","), args.save, args.backend)
    if args.interactive:
        while True:
            try:
                names = input("input gauge name (exit to quit): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\\n已退出交互模式")
                break
            if names.lower() == "exit":
                break
            if names:
                try:
                    plot_gauges(t_data, names.split(","), backend=args.backend)
                except (ValueError, RuntimeError) as exc:
                    print(f"绘图提示：{exc}")


if __name__ == "__main__":
    cli()
