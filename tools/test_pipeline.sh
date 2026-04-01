#!/usr/bin/env bash
# test_pipeline.sh — 一键跑完整测试流程
#
# 用法：
#   bash tools/test_pipeline.sh
#   bash tools/test_pipeline.sh --workspace /tmp/my_odb --port 8001
#
# 依赖：python3, pip (numpy/h5py/fastapi/uvicorn/httpx)

set -euo pipefail

# ─── 参数 ──────────────────────────────────────────────────────────────────────
WORKSPACE="/tmp/mock_odb_test"
ODB_ID="mock_odb"
PORT=8000
INSTANCES=3          # 少一点，跑得快
NODES_SIDE=10        # 10×10=100 节点
FRAMES=4

while [[ $# -gt 0 ]]; do
    case $1 in
        --workspace) WORKSPACE="$2"; shift 2 ;;
        --port)      PORT="$2";      shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

REGISTRY_DB="$WORKSPACE/registry.db"
L3_PID=""

# ─── 颜色输出 ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; cleanup; exit 1; }
info() { echo -e "${YELLOW}[--]${NC}  $*"; }

cleanup() {
    if [[ -n "$L3_PID" ]]; then
        info "Stopping L3 server (pid=$L3_PID)..."
        kill "$L3_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo ""
echo "================================================="
echo "  ODB Pipeline 端到端测试"
echo "  workspace : $WORKSPACE"
echo "  odb_id    : $ODB_ID"
echo "  port      : $PORT"
echo "================================================="
echo ""

# ─── Step 0: 依赖检查 ──────────────────────────────────────────────────────────
info "Step 0: 检查 Python 依赖..."
python3 -c "import numpy, h5py, fastapi, uvicorn" 2>/dev/null \
    || { info "安装缺失依赖..."; pip install numpy h5py fastapi uvicorn httpx -q; }
ok "依赖就绪"

# ─── Step 1: 生成 mock l1_raw ──────────────────────────────────────────────────
info "Step 1: 生成 mock l1_raw..."
rm -rf "$WORKSPACE"
python3 tools/gen_mock_l1_raw.py \
    --workspace "$WORKSPACE" \
    --instances $INSTANCES \
    --nodes-side $NODES_SIDE \
    --frames $FRAMES \
    || fail "gen_mock_l1_raw 失败"
ok "l1_raw 生成完毕"

# ─── Step 2: l1_pack ───────────────────────────────────────────────────────────
info "Step 2: 运行 l1_pack.py (npy → HDF5)..."
python3 src/l1/l1_pack.py --workspace "$WORKSPACE" \
    || fail "l1_pack.py 失败"
ok "L1 HDF5 打包完毕"

# 验证关键文件存在
[[ -f "$WORKSPACE/manifest.db" ]]          || fail "manifest.db 不存在"
[[ -f "$WORKSPACE/l1/assembly.h5" ]]       || fail "assembly.h5 不存在"
ok "L1 文件校验通过"

# ─── Step 3: L2 ingest ─────────────────────────────────────────────────────────
info "Step 3: 运行 ingest.py (L1 → L2 渲染缓冲)..."
python3 src/l2/ingest.py --workspace "$WORKSPACE" \
    || fail "ingest.py 失败"
ok "L2 预处理完毕"

# 验证 L2 文件
SURF_H5="$WORKSPACE/l2/geometry/PART-1-1_surface.h5"
REND_H5="$WORKSPACE/l2/render/PART-1-1_render.h5"
[[ -f "$SURF_H5" ]] || fail "L2 surface.h5 不存在: $SURF_H5"
[[ -f "$REND_H5" ]] || fail "L2 render.h5 不存在: $REND_H5"

# 验证关键 dataset
python3 - <<PYEOF
import h5py, sys
with h5py.File("$REND_H5", "r") as f:
    for ds in ["render/positions", "render/source_elem_row", "render/source_node_rows"]:
        if ds not in f:
            print(f"MISSING dataset: {ds}")
            sys.exit(1)
        print(f"  {ds}: {f[ds].shape}")
PYEOF
ok "L2 dataset 校验通过"

# ─── Step 4: 建 registry.db ────────────────────────────────────────────────────
info "Step 4: 创建 registry.db..."
python3 - <<PYEOF
import sqlite3
conn = sqlite3.connect("$REGISTRY_DB")
conn.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        odb_id    TEXT PRIMARY KEY,
        workspace TEXT NOT NULL,
        status    TEXT NOT NULL
    )
""")
conn.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)",
             ("$ODB_ID", "$WORKSPACE", "ready"))
conn.commit()
conn.close()
print("  registry.db ok")
PYEOF
ok "registry.db 就绪"

# ─── Step 5: 启动 L3 ───────────────────────────────────────────────────────────
info "Step 5: 启动 L3 FastAPI 服务..."
APP_REGISTRY_DB_PATH="$REGISTRY_DB" \
APP_LOG_LEVEL="WARNING" \
    python3 -m uvicorn src.l3.main:app --port $PORT --host 127.0.0.1 &
L3_PID=$!

# 等待服务就绪
info "等待服务启动..."
for i in $(seq 1 20); do
    sleep 0.5
    if curl -sf "http://127.0.0.1:$PORT/health/live" >/dev/null 2>&1; then
        break
    fi
    if [[ $i -eq 20 ]]; then
        fail "L3 服务启动超时"
    fi
done
ok "L3 服务已启动 (pid=$L3_PID)"

# ─── Step 6: 接口测试 ──────────────────────────────────────────────────────────
BASE="http://127.0.0.1:$PORT"

info "Step 6: 测试 health 接口..."
RES=$(curl -sf "$BASE/health/live")
echo "$RES" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['ok']==True" \
    || fail "health/live 返回异常: $RES"
ok "GET /health/live → ok"

RES=$(curl -sf "$BASE/health/ready")
echo "$RES" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['ok']==True" \
    || fail "health/ready 返回异常: $RES"
ok "GET /health/ready → ok"

info "Step 7: 测试 meta/overview..."
RES=$(curl -sf "$BASE/api/odb/$ODB_ID/meta/overview")
echo "$RES" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['ok'] == True, f'ok!=True: {d}'
assert len(d['data']['instances']) > 0, 'no instances'
print(f\"  instances: {len(d['data']['instances'])}, steps: {len(d['data']['steps'])}\")
" || fail "meta/overview 异常: $RES"
ok "GET /meta/overview → ok"

info "Step 8: 测试 query/pick..."
RES=$(curl -sf "$BASE/api/odb/$ODB_ID/query/pick?instance=PART-1-1&render_face_idx=0")
echo "$RES" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'elem_label' in d, f'no elem_label: {d}'
print(f\"  elem_label={d['elem_label']}, node_labels={d['node_labels']}\")
" || fail "query/pick 异常: $RES"
ok "GET /query/pick → ok"

info "Step 9: 测试 results/frame-colors..."
HTTP_CODE=$(curl -s -o /tmp/frame_colors.bin -w "%{http_code}" \
    "$BASE/api/odb/$ODB_ID/results/frame-colors?instance=PART-1-1&step=Step-1&frame=0&field=U&component=USUM")
[[ "$HTTP_CODE" == "200" ]] || fail "frame-colors 返回 HTTP $HTTP_CODE"

# 验证 L3BE magic
python3 - <<PYEOF
with open("/tmp/frame_colors.bin", "rb") as f:
    magic = f.read(4)
assert magic == b"L3BE", f"bad magic: {magic}"
size = $(wc -c < /tmp/frame_colors.bin)
print(f"  L3BE payload: {size} bytes, magic OK")
PYEOF
ok "GET /results/frame-colors → L3BE binary ok"

# ─── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo "================================================="
echo -e "${GREEN}  全部测试通过！${NC}"
echo "================================================="
echo ""
echo "手动继续测试："
echo "  curl '$BASE/api/odb/$ODB_ID/results/frame-colors?instance=PART-1-1&step=Step-1&frame=2&field=U&component=U1'"
echo "  curl '$BASE/api/odb/$ODB_ID/query/pick?instance=PART-1-1&render_face_idx=5&step=Step-1&field=U&frame_idx=0'"
echo ""
