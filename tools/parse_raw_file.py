import os
import sys
import argparse

import imctermite
import matplotlib.pyplot as plt


def load_raw(filename):
    """
    读取 IMC RAW 文件
    """
    if not os.path.isfile(filename):
        raise FileNotFoundError(f"File not found: {filename}")

    print(f"Opening RAW file: {filename}")

    try:
        # IMCtermite 构造函数需要 bytes 路径
        imc = imctermite.imctermite(os.fsencode(filename))
    except RuntimeError as e:
        raise RuntimeError(f"Failed to parse RAW file: {e}")

    # True = 同时读取实际波形数据
    channels = imc.get_channels(True)

    if not channels:
        raise RuntimeError("No channels found in RAW file.")

    return channels


def print_channels(channels):
    """
    显示 RAW 文件中的全部通道
    """
    print()
    print("=" * 70)
    print("Channels")
    print("=" * 70)

    for i, ch in enumerate(channels):
        name = ch.get("name", "Unknown")
        xunit = ch.get("xunit", "")
        yunit = ch.get("yunit", "")
        samples = len(ch.get("ydata", []))

        print(
            f"[{i:3d}] "
            f"{name:<30} "
            f"Samples: {samples:<10} "
            f"X unit: {xunit:<8} "
            f"Y unit: {yunit}"
        )

    print("=" * 70)
    print()


def plot_channel(channel, index):
    """
    绘制单个测量通道
    """
    x = channel.get("xdata", [])
    y = channel.get("ydata", [])

    if not x or not y:
        print(f"Channel {index} has no waveform data.")
        return

    name = channel.get("name", f"Channel {index}")

    xname = channel.get("xname", "X")
    yname = channel.get("yname", name)

    xunit = channel.get("xunit", "")
    yunit = channel.get("yunit", "")

    print(f"Plotting channel {index}: {name}")
    print(f"Samples: {len(y)}")

    plt.figure(figsize=(12, 6))

    plt.plot(x, y, linewidth=0.8)

    plt.title(name)

    if xunit:
        plt.xlabel(f"{xname} [{xunit}]")
    else:
        plt.xlabel(xname)

    if yunit:
        plt.ylabel(f"{yname} [{yunit}]")
    else:
        plt.ylabel(yname)

    plt.grid(True)
    plt.tight_layout()


def main():
    parser = argparse.ArgumentParser(
        description="Read IMC RAW file and plot measurement channels."
    )

    parser.add_argument(
        "rawfile",
        help="Path to IMC .raw file"
    )

    parser.add_argument(
        "-c",
        "--channel",
        type=int,
        default=0,
        help="Channel index to plot (default: 0)"
    )

    parser.add_argument(
        "--all",
        action="store_true",
        dest="plot_all",
        help="Plot all channels"
    )

    args = parser.parse_args()

    try:
        channels = load_raw(args.rawfile)

        # 打印全部通道
        print_channels(channels)

        if args.plot_all:

            for i, channel in enumerate(channels):
                plot_channel(channel, i)

        else:

            index = args.channel

            if index < 0 or index >= len(channels):
                print(
                    f"Invalid channel index: {index}\n"
                    f"Valid range: 0 - {len(channels) - 1}"
                )
                sys.exit(1)

            plot_channel(channels[index], index)

        plt.show()

    except Exception as e:
        print()
        print("ERROR:")
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
