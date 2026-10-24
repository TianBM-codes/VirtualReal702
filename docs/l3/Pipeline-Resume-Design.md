# 导入流水线断点续做（设计备忘，未实施）

状态：**待定**。2026-10-22 讨论后决定先留文档，需要时再实施。

## 1. 问题

导入（project / 追加 result_group）的最后一步是全量缓存准备（`readiness_service.prepare_workspace`，见
`L3-Full-Precompute-Cache-Proposal.md`）。大模型这一步可能要数小时。目前在这一步中断时：

| 情况 | 现在的行为 | 代价 |
|---|---|---|
| 进程被杀（重启服务、断电、崩溃） | `job_runner._recover_stuck_running_states()` 启动时把 `geom_status='running'` 的项目、`status='running'` 的结果组重置为 `pending`，随后**整条流水线从头重跑**：ODB 项目重新 `abaqus_dump.py` → `l1_pack.py` → catalog → `ingest.py` → 缓存准备 | L1 导出要调 Abaqus，大模型数小时；`ingest.py`（phase all/core）开始时 `shutil.rmtree(<workspace>/l3_cache)`，已算好的缓存全部作废 |
| 追加结果组时进程被杀 | 结果组重置为 `pending` 后重新解析；`_cleanup_result_group` 先删该组的 manifest 行，并调用 `cache_store.clear_bucket` 删除 `l3_cache/groups/<result_group>/` | 重新解析 + 该组缓存全部重算 |
| 缓存准备抛异常（磁盘满、权限、`PreparationError`） | `_update_project_geom_status(..., "error")` / `_update_result_group_status(..., "error")`，项目或结果组标为 error | 没有"只重试缓存准备"的入口；只能重新导入，或调 `rerun-l2`（同样会清空 `l3_cache`） |

## 2. 可利用的现有性质

缓存准备本身天然可续：严格模式（`cache_store.require_persistent_cache`）下内存层不命中，但**磁盘层照常命中**
（`arrays_get` / `json_get` / `payload_get` 命中时把条目登记进本轮必需集合）。已落盘的条目只读一次文件就跳过，
只有缺的才计算；结束时统一核验所有条目都在磁盘上。所以只要不重跑 L2、不清空 `l3_cache`，再执行一次
`prepare_workspace` 就等于从断点接着算。

缓存 key 都带结果文件 `(mtime_ns, size)` 与 render.h5 签名，源数据变了旧条目自然不命中，续做不会用到过期数据。

## 3. 方案

### 3.1 阶段标记

在工作区写 `<workspace>/.pipeline_state.json`（原子写：先写临时文件再 `os.replace`）：

```json
{
  "kind": "project",
  "source_path": "E:/.../door.odb",
  "source_sig": [mtime_ns, size],
  "stage": "l2_done",
  "updated_at": "2026-10-22T13:55:20+08:00"
}
```

- `stage` 取值：`l1_done`（`l1_pack.py` 成功）→ `l2_done`（`ingest.py` 成功，进入缓存准备前）→ `prepared`（缓存准备成功）。
- 结果组单独一份：`<workspace>/.pipeline_state.<result_group>.json`，阶段 `parsed`（结果写入 manifest 成功）→ `prepared`。
- 写入点：`_run_odb_project` 等各 `_run_*_project` 在对应步骤成功后写；`_update_project_geom_status("ready")`
  与 `_update_result_group_status("ready")` 在 `_prepare_registered` 成功后写 `prepared`。

### 3.2 恢复时按标记跳步

`_run_project` / `_run_result_group` 开头读标记，**源文件路径与签名都一致**时：

| 标记 | 恢复动作 |
|---|---|
| `l2_done` | 跳过 L1、catalog、L2，直接 `_update_project_geom_status(project_id, "ready")`（内部执行缓存准备，磁盘已有条目直接复用） |
| `l1_done` | 跳过 L1，从 L2 开始（L2 会清缓存，此时本来也没有缓存） |
| 无标记 / 签名不一致 / 读失败 | 照旧从头开始，并删除旧标记 |
| 结果组 `parsed` | 跳过 `_cleanup_result_group` 与解析，直接 `_update_result_group_status(..., "ready")` |

注意：

- ODB 项目的 `_adopt_odb_result_group` 在 L2 之后、ready 之前执行，跳步时要确认它是幂等的或同样被跳过/补做。
- catalog 导入（模型修正测点/参数）是非致命步骤，跳步时不重做；必要时单独记录其完成状态。
- 各数据源（ODB / BDF / OP2 / CDB / RST / SIPESC UNV / INP）的 `_run_*_project` 结构不同，标记写入点要逐一补齐；
  先做 ODB（耗时最长），其他源从头重跑的代价较小，可后补。
- L2 重跑（`_run_l2_rerun`）与结果组重新解析本身就要求清缓存，不走续做逻辑，开始时删除标记。

### 3.3 缓存准备出错后的重试入口（需要新增接口，待确认）

`POST /api/projects/{project_id}/retry-prepare`，可选 body `{"result_group": "..."}`：

- 仅当项目 `geom_status='error'`（或结果组 `status='error'`）且标记为 `l2_done` / `parsed`（即出错发生在缓存准备阶段）时接受，
  否则返回业务错误说明原因。
- 接受后把状态置回 `pending`，runner 认领后按 3.2 直接进入缓存准备。
- 接口只用 POST（项目约定），新增后同步 `docs/l3/L3-API-Quick-Reference.md`。

不加这个接口时，缓存准备出错只能重新导入。

## 4. 验证要点（实施时）

1. 导入大模型，在 `cache_prepare` 进度到一半时重启服务：重启后日志应直接出现 `cache_prepare`，不再出现 `l1_dump`；
   进度起点明显高于 0（已落盘条目秒过）；最终 ready。
2. 用 `tools/snapshot_results.py --cache disk --compare` 核对续做后的缓存与一次跑完的结果一致。
3. 中断后替换源 ODB 文件再重启：签名不一致，应从头重跑。
4. L1 阶段中断：仍从头重跑（L1 无续做）。
5. 追加结果组在缓存准备阶段中断：重启后不重新解析，直接续做缓存准备。
6. 人为制造缓存准备失败（如临时把 `APP_SCALAR_DISK_CACHE_MB` 设得很小）：项目 error → 调 `retry-prepare` → 恢复配置后 ready。
