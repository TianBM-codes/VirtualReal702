#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_l1.py — 一键运行 L1 Phase 1 + Phase 2

用法：
    python tools/run_l1.py --odb E:/models/mymodel.odb --out E:/workspace/mymodel

    --odb    ODB 文件路径
    --out    输出 workspace 目录（会自动创建）
    --keep-raw  不删除 l1_raw/ 临时目录（默认删除）
    --python3   Phase 2 使用的 Python 解释器路径（默认：当前解释器）
    --abaqus    abaqus 可执行文件名或完整路径（默认：abaqus）

Windows 示例：
    python tools/run_l1.py --odb "E:\\models\\model.odb" --out "E:\\ws\\model"

Linux/macOS 示例：
    python tools/run_l1.py --odb /data/models/model.odb --out /data/ws/model
"""

import argparse
import os
import subprocess
import sys
import time

# ─── 颜色输出（Windows 兼容）────────────────────────────────────────────────
if sys.platform == "win32":
    os.system("color")   # 开启 ANSI 支持

def ok(msg):    print(f"\033[32m[OK]\033[0m  {msg}")
def fail(msg):  print(f"\033[31m[FAIL]\033[0m {msg}"); sys.exit(1)
def info(msg):  print(f"\033[36m[--]\033[0m  {msg}")
def hr():       print("-" * 60)


def _fmt_t(secs):
    if secs >= 60:
        return f"{int(secs) // 60}m {secs % 60:.1f}s"
    return f"{secs:.1f}s"


def find_repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)   # tools/ 的上级


def parse_args():
    p = argparse.ArgumentParser(description="L1 一键运行脚本 (Phase 1 + Phase 2)")
    p.add_argument("--odb",      required=True,  help="ODB 文件路径")
    p.add_argument("--out",      required=True,  help="输出 workspace 目录")
    p.add_argument("--keep-raw", action="store_true",
                   help="保留 l1_raw/ 临时目录（默认删除）")
    p.add_argument("--python3",  default=sys.executable,
                   help="Phase 2 使用的 Python 解释器（默认：当前 Python）")
    p.add_argument("--abaqus",   default="abaqus",
                   help="abaqus 可执行文件名或路径（默认：abaqus）")
    return p.parse_args()


def run_phase(cmd, label):
    """运行子进程，实时打印输出，返回耗时。失败则退出。"""
    t0 = time.time()
    print()
    hr()
    info(f"{label} 开始")
    hr()
    result = subprocess.run(cmd, text=True)
    elapsed = time.time() - t0
    hr()
    if result.returncode != 0:
        fail(f"{label} 失败（返回码 {result.returncode}）")
    ok(f"{label} 完成，耗时 {_fmt_t(elapsed)}")
    return elapsed


def main():
    args    = parse_args()
    repo    = find_repo_root()
    odb     = os.path.abspath(args.odb)
    out     = os.path.abspath(args.out)

    print()
    print("=" * 60)
    print("  L1 一键流水线")
    print(f"  ODB  : {odb}")
    print(f"  输出 : {out}")
    print("=" * 60)

    # ── 前置检查 ─────────────────────────────────────────────────────────────
    if not os.path.exists(odb):
        fail(f"ODB 文件不存在: {odb}")

    phase1_script = os.path.join(repo, "src", "l1", "abaqus_dump.py")
    phase2_script = os.path.join(repo, "src", "l1", "l1_pack.py")

    for f in [phase1_script, phase2_script]:
        if not os.path.exists(f):
            fail(f"脚本不存在: {f}")

    # ── Phase 1: ODB → npy (abaqus python) ───────────────────────────────────
    cmd1 = [
        args.abaqus, "python", phase1_script,
        "--odb", odb,
        "--out", out,
    ]
    t1 = run_phase(cmd1, "Phase 1: ODB → npy")

    # ── Phase 2: npy → HDF5 (Python 3) ───────────────────────────────────────
    cmd2 = [args.python3, phase2_script, "--workspace", out]
    if args.keep_raw:
        cmd2.append("--keep-raw")
    t2 = run_phase(cmd2, "Phase 2: npy → HDF5")

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("\033[32m  L1 全部完成！\033[0m")
    print(f"  Phase 1 耗时 : {_fmt_t(t1)}")
    print(f"  Phase 2 耗时 : {_fmt_t(t2)}")
    print(f"  总耗时       : {_fmt_t(t1 + t2)}")
    print(f"  workspace    : {out}")
    print("=" * 60)
    print()
    print("下一步：运行 L2 预处理")
    print(f"  python src/l2/ingest.py --workspace {out}")
    print()


if __name__ == "__main__":
    main()
