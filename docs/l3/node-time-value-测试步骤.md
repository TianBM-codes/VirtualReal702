# node-time-value / 节点列表 / result_blocks 修复 — 测试步骤

> 2026-07-16 一揽子改动的验证方法。涉及 commit：
> `eb031e5`（demo 字段联动）→ `e567cd0`（result_blocks sp_num 根因修复）→
> `72150f6`（结果 HDF5 路径查 manifest）→ `945dc26`（FREQUENCY 步拒绝插值）→
> `43aba2f`（node-labels 节点列表接口 + demo 预填）。
> 背景与根因分析见 `docs/维护注意事项.md`。

## 0. 前置

```powershell
git pull
# 重启 python app.py（代码改动必须重启；只跑 tools/ 修复脚本不用）
```

存量工程（修复上线前创建的、含壳单元结果的）先修一次索引表：

```powershell
python tools/fix_result_blocks.py "<workspace目录>" --tag-null default_result
```

判别是否需要修：`python tools/diag_manifest.py "<workspace目录>"`，
若 `result_blocks` 行全是 `result_group=None` 而 `steps` 是 `default_result` → 需要修。

## 1. 单元测试（开发机跑）

```powershell
python -m pytest tests/test_manifest_migration.py tests/test_l3_node_time_value.py -v
```

- `test_manifest_migration.py`：sp_num 迁移（含"不迁移直接打标必撞主键"的反向用例）
- `test_l3_node_time_value.py`：新增 FREQUENCY 步两条——interp 落两帧间报 400、exact/prev/next 放行

## 2. demo 页测试（`viewer/node-time-value-demo.html`）

参考工程：project `202607161656`（door.odb，PART-1-1，steps: sag=STATIC 21帧 / eigenfrequency=FREQUENCY 6帧，result_group=default_result）。

| # | 操作 | 预期 |
|---|---|---|
| 1 | 填 Project ID + result_group，点"加载元数据" | instance/step 下拉填充；字段列表自动加载 |
| 2 | 看节点输入框下方 | 显示"共 N 个节点,编号 min ~ max"；输入框自动预填 3 个真实节点号（8 位大数，不是 1,2,3） |
| 3 | 选 step=sag | 字段列表刷新，只剩该组合真实存在的 NODAL 字段（U、UR 等；S_MISES 这类只有 EN/IP 位置的不出现） |
| 4 | step 留空（全局时间） | 字段取各 STATIC/DYNAMIC step 的交集 |
| 5 | sag + U + 预填节点 + time=0.5 + 插值 | 200；正好命中帧 → resolved_mode=exact |
| 6 | 同上 time=0.523 | resolved_mode=interp，两帧权重 ≈0.54/0.46，值为线性插值 |
| 7 | 同上 time=99 + 前一帧 | 取末帧（frame 20） |
| 8 | 同上 time=99 + 插值 | 400 "outside the frame time range" |
| 9 | 选 eigenfrequency + time=0.5 + 插值 | **400**，提示 FREQUENCY 步 frame_value 是模态阶次/频率、振型插值无意义、改用 prev/next |
| 10 | eigenfrequency + time=1 + 插值 | 200，命中第 1 阶（resolved_mode=exact） |
| 11 | 填一个不存在的节点号（如 1） | 该行显示"不在该 instance"（found=false），不报错 |

## 3. 接口级抽查（curl / PowerShell Invoke-RestMethod）

```bash
BASE=http://127.0.0.1:5000/api/odb/202607161656

# 节点列表：总数 + 编号范围 + 前 5 个
curl "$BASE/geometry/PART-1-1/node-labels?limit=5"

# 字段按 step+instance 过滤（修复前返回空数组）
curl "$BASE/fields?instance=PART-1-1&step=sag&result_group=default_result"

# 按时间查值（节点号用上面 node-labels 返回的真实值）
curl -X POST "$BASE/results/node-time-value" -H "Content-Type: application/json" \
  -d '{"instance":"PART-1-1","field":"U","node_labels":[<真实label>],"time":0.523,"step":"sag","result_group":"default_result"}'

# FREQUENCY 步插值防护 → 400
curl -X POST "$BASE/results/node-time-value" -H "Content-Type: application/json" \
  -d '{"instance":"PART-1-1","field":"U","node_labels":[<真实label>],"time":0.5,"step":"eigenfrequency","result_group":"default_result"}'
```

## 4. 回归确认（改动不该影响的东西）

- 主 viewer 云图（frame-colors）、pick、变形叠加照常 —— 它们走 `_manifest_result_h5_path`/独立路径，本来就没坏
- legacy `/api/jobs` 分支不受影响
- `result-catalog` 的每个字段 `instances` 数组：修复后应非空（修复前全空）

## 5. 已验证 / 待验证

**已通过隧道在开发机实测通过（2026-07-16）**：上表 demo 步骤 5–8、11 对应的接口行为；
`/fields`、node-table（U 列 200 / S_MISES 正确 400——它没有 NODAL 位置）、
node-displacements、全局时间口径、result-catalog instances 非空。

**待开发者验证**：第 1 节 pytest 全量；demo 页步骤 1–4、9–10（改动后未重启验证）；
node-labels 接口（`43aba2f` 推送后服务尚未重启，还没实测）。
