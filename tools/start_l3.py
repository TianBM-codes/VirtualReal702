#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
start_l3.py — 启动 L3 FastAPI 服务（Windows / macOS / Linux 通用）

用法：
    python tools/start_l3.py --workspace E:\\code\\...\\tmp
    python tools/start_l3.py --workspace /data/mymodel --odb-id mymodel --port 8001
    python tools/start_l3.py --workspace /data/mymodel --prod --workers 4

参数：
    --workspace   必填，ODB 工作目录路径（包含 l1/ l2/ manifest.db）
    --odb-id      服务里这个模型的名字，默认取目录名
    --port        监听端口，默认 8000
    --prod        生产模式（用 gunicorn，不自动重载），默认 dev 模式（uvicorn --reload）
    --workers     生产模式 worker 数，默认 4
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="启动 L3 ODB 服务")
    parser.add_argument("--workspace", required=True, help="ODB 工作目录路径")
    parser.add_argument("--odb-id", default="", help="模型 ID（默认取目录名）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--prod", action="store_true", help="生产模式（gunicorn）")
    parser.add_argument("--workers", type=int, default=4, help="生产模式 worker 数（默认 4）")
    return parser.parse_args()


def main():
    args = parse_args()

    workspace = str(Path(args.workspace).resolve())
    if not Path(workspace).is_dir():
        print(f"错误：workspace 目录不存在: {workspace}")
        sys.exit(1)

    odb_id = args.odb_id or Path(workspace).name

    # 切换到项目根目录（tools/ 的上一级），确保 src.l3.main 能被找到
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    env = os.environ.copy()
    env["APP_ODB_WORKSPACE"] = workspace
    env["APP_ODB_ID"] = odb_id

    print("=" * 50)
    print(f"  工作目录  : {workspace}")
    print(f"  模型 ID   : {odb_id}")
    print(f"  端口      : {args.port}")
    print(f"  模式      : {'生产 (gunicorn)' if args.prod else '开发 (uvicorn --reload)'}")
    print("=" * 50)
    print(f"  Health   : http://127.0.0.1:{args.port}/health/live")
    print(f"  Overview : http://127.0.0.1:{args.port}/api/odb/{odb_id}/meta/overview")
    print(f"  Viewer   : http://127.0.0.1:{args.port}/static/viewer.html")
    print("=" * 50)

    if args.prod:
        # 生产模式：gunicorn（Linux / macOS 专用，Windows 不支持 gunicorn）
        if sys.platform == "win32":
            print("错误：gunicorn 不支持 Windows，请去掉 --prod 用 uvicorn 开发模式，")
            print("      或在 Linux/macOS 上用生产模式。")
            sys.exit(1)
        cmd = [
            sys.executable, "-m", "gunicorn",
            "src.l3.main:app",
            "-w", str(args.workers),
            "-k", "uvicorn.workers.UvicornWorker",
            "--bind", f"0.0.0.0:{args.port}",
        ]
    else:
        # 开发模式：uvicorn --reload（跨平台）
        cmd = [
            sys.executable, "-m", "uvicorn",
            "src.l3.main:app",
            "--reload",
            "--host", "0.0.0.0",
            "--port", str(args.port),
        ]

    try:
        subprocess.run(cmd, env=env)
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
