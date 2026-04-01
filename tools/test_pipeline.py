#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_pipeline.py — 端到端测试脚本（Windows / macOS / Linux 通用）

用法：
    python tools/test_pipeline.py
    python tools/test_pipeline.py --workspace C:/tmp/mock_odb --port 8000
"""

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error

# ─── 颜色输出（Windows 兼容）──────────────────────────────────────────────────
if sys.platform == "win32":
    os.system("color")   # 开启 ANSI 支持

def ok(msg):   print(f"\033[32m[OK]\033[0m  {msg}")
def fail(msg): print(f"\033[31m[FAIL]\033[0m {msg}"); sys.exit(1)
def info(msg): print(f"\033[33m[--]\033[0m  {msg}")


# ─── 参数 ────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", default=os.path.join(os.path.expanduser("~"), "mock_odb_test"))
    p.add_argument("--port",      type=int, default=8000)
    p.add_argument("--instances", type=int, default=3)
    p.add_argument("--nodes-side",type=int, default=10)
    p.add_argument("--frames",    type=int, default=4)
    return p.parse_args()


# ─── 辅助函数 ────────────────────────────────────────────────────────────────
def run(cmd, **kwargs):
    """运行命令，失败直接退出。"""
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        fail(f"命令失败: {' '.join(str(c) for c in cmd)}")


def wait_for_server(url, timeout=15):
    """轮询等待服务就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def http_get(url):
    """简单 GET，返回 (status_code, body_bytes)。"""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def find_repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)   # tools/ 的上级


# ─── 主流程 ──────────────────────────────────────────────────────────────────
def main():
    args     = parse_args()
    ws       = os.path.abspath(args.workspace)
    odb_id   = "mock_odb"
    port     = args.port
    base_url = f"http://127.0.0.1:{port}"
    reg_db   = os.path.join(ws, "registry.db")

    repo = find_repo_root()
    py   = sys.executable   # 当前 Python 解释器路径

    print()
    print("=" * 50)
    print("  ODB Pipeline 端到端测试")
    print(f"  workspace : {ws}")
    print(f"  port      : {port}")
    print("=" * 50)
    print()

    # ── Step 0: 依赖检查 ────────────────────────────────────────────────────
    info("Step 0: 检查依赖...")
    missing = []
    for pkg in ["numpy", "h5py", "fastapi", "uvicorn"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        info(f"安装缺失依赖: {missing}")
        run([py, "-m", "pip", "install"] + missing + ["-q"])
    ok("依赖就绪")

    # ── Step 1: 生成 mock l1_raw ────────────────────────────────────────────
    info("Step 1: 生成 mock l1_raw...")
    import shutil
    if os.path.exists(ws):
        shutil.rmtree(ws)
    run([py, os.path.join(repo, "tools", "gen_mock_l1_raw.py"),
         "--workspace", ws,
         "--instances", str(args.instances),
         "--nodes-side", str(args.nodes_side),
         "--frames",    str(args.frames)])
    ok("l1_raw 生成完毕")

    # ── Step 2: l1_pack ─────────────────────────────────────────────────────
    info("Step 2: l1_pack.py (npy → HDF5)...")
    run([py, os.path.join(repo, "src", "l1", "l1_pack.py"),
         "--workspace", ws])
    for f in ["manifest.db", os.path.join("l1", "assembly.h5")]:
        if not os.path.exists(os.path.join(ws, f)):
            fail(f"文件缺失: {f}")
    ok("L1 打包完毕")

    # ── Step 3: L2 ingest ───────────────────────────────────────────────────
    info("Step 3: ingest.py (L1 → L2)...")
    run([py, os.path.join(repo, "src", "l2", "ingest.py"),
         "--workspace", ws])

    inst_name = "PART-1-1"
    rend_h5   = os.path.join(ws, "l2", "render", f"{inst_name}_render.h5")
    surf_h5   = os.path.join(ws, "l2", "geometry", f"{inst_name}_surface.h5")
    for f in [rend_h5, surf_h5]:
        if not os.path.exists(f):
            fail(f"L2 文件缺失: {f}")

    # 验证关键 dataset
    import h5py
    with h5py.File(rend_h5, "r") as f:
        for ds in ["render/positions", "render/source_elem_row", "render/source_node_rows"]:
            if ds not in f:
                fail(f"L2 render.h5 缺少 dataset: {ds}")
            print(f"    {ds}: {f[ds].shape}")
    ok("L2 预处理完毕")

    # ── Step 4: registry.db ─────────────────────────────────────────────────
    info("Step 4: 创建 registry.db...")
    conn = sqlite3.connect(reg_db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            odb_id TEXT PRIMARY KEY, workspace TEXT, status TEXT
        )
    """)
    conn.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?)",
                 (odb_id, ws, "ready"))
    conn.commit()
    conn.close()
    ok("registry.db 就绪")

    # ── Step 5: 启动 L3 ─────────────────────────────────────────────────────
    info("Step 5: 启动 L3 服务...")
    env = os.environ.copy()
    env["APP_REGISTRY_DB_PATH"] = reg_db
    env["APP_LOG_LEVEL"]        = "WARNING"

    srv = subprocess.Popen(
        [py, "-m", "uvicorn", "src.l3.main:app",
         "--port", str(port), "--host", "127.0.0.1"],
        cwd=repo, env=env,
    )

    try:
        if not wait_for_server(f"{base_url}/health/live"):
            srv.terminate()
            fail("L3 服务启动超时（15s）")
        ok(f"L3 服务已启动 (pid={srv.pid})")

        # ── Step 6: health ──────────────────────────────────────────────────
        info("Step 6: 测试 /health/live ...")
        status, body = http_get(f"{base_url}/health/live")
        assert status == 200, f"HTTP {status}"
        import json
        d = json.loads(body)
        assert d["ok"] is True
        ok("GET /health/live → ok")

        # ── Step 7: meta/overview ───────────────────────────────────────────
        info("Step 7: 测试 meta/overview...")
        status, body = http_get(f"{base_url}/api/odb/{odb_id}/meta/overview")
        assert status == 200, f"HTTP {status}: {body.decode()}"
        d = json.loads(body)
        assert d["ok"] is True
        n_inst = len(d["data"]["instances"])
        n_step = len(d["data"]["steps"])
        print(f"    instances={n_inst}, steps={n_step}")
        ok("GET /meta/overview → ok")

        # ── Step 8: query/pick ──────────────────────────────────────────────
        info("Step 8: 测试 query/pick...")
        url = (f"{base_url}/api/odb/{odb_id}/query/pick"
               f"?instance={inst_name}&render_face_idx=0")
        status, body = http_get(url)
        assert status == 200, f"HTTP {status}: {body.decode()}"
        d = json.loads(body)
        print(f"    elem_label={d['elem_label']}, node_labels={d['node_labels']}")
        ok("GET /query/pick → ok")

        # ── Step 9: frame-colors ────────────────────────────────────────────
        info("Step 9: 测试 results/frame-colors...")
        url = (f"{base_url}/api/odb/{odb_id}/results/frame-colors"
               f"?instance={inst_name}&step=Step-1&frame=0&field=U&component=USUM")
        status, body = http_get(url)
        assert status == 200, f"HTTP {status}: {body.decode()[:200]}"
        assert body[:4] == b"L3BE", f"magic 错误: {body[:4]}"
        print(f"    L3BE payload: {len(body)} bytes，magic OK")
        ok("GET /results/frame-colors → L3BE binary ok")

        # ── 完成 ────────────────────────────────────────────────────────────
        print()
        print("=" * 50)
        print("\033[32m  全部测试通过！\033[0m")
        print("=" * 50)
        print()
        print("交互式查看器：")
        print(f"\033[36m  {base_url}/static/viewer.html\033[0m")
        print()
        print("手动测试示例：")
        print(f"  {base_url}/api/odb/{odb_id}/results/frame-colors"
              f"?instance={inst_name}&step=Step-1&frame=2&field=U&component=U1")
        print(f"  {base_url}/api/odb/{odb_id}/query/pick"
              f"?instance={inst_name}&render_face_idx=5&step=Step-1&field=U&frame_idx=0")
        print()
        input("按 Enter 停止服务...")

    finally:
        srv.terminate()
        srv.wait()
        info("L3 服务已停止")


if __name__ == "__main__":
    main()
