# L3 API Quick Reference

更新时间：2026-05-28（新增 vertex-displacements、deformed-normals 接口）

本文以当前分支 `src/l3/api/routes/*` 的实现为准，面向前端和上层服务调用方。服务地址示例：

```text
http://<host>:18765
```

## 1. 通用约定

### 1.1 JSON 成功响应

多数 JSON 接口使用统一包体：

```json
{
  "code": 200,
  "data": {},
  "message": ""
}
```

部分创建接口的 HTTP status 是 `201`，但包体里的 `code` 仍为 `200`。

### 1.2 错误响应

业务异常一般由 `AppError` 处理，常见结构：

```json
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "ODB 'xxx' not found",
    "details": {}
  },
  "meta": {
    "request_id": "uuid"
  }
}
```

少数接口仍可能返回 FastAPI 默认错误格式：

```json
{
  "detail": "..."
}
```

### 1.3 L3BE 二进制响应

以下接口返回 `application/octet-stream`，正文为 L3BE：

- Geometry：`render-buffers`、`render-buffers-subset`、`element-mesh-edges`、`feature-edges`
- Results：`frame-colors`、`frame-scalars`、`deformed-positions`、`vertex-displacements`、`deformed-normals`、`raw-values?format=l3be`、`section-mesh`
- Table：`results/node-table`
- User field：`user-field-colors`
- Color code：`color-code/{instance}`

常见 L3BE dtype code：

| code | dtype |
|---|---|
| `2` | `uint8` |
| `5` | `int32` |
| `9` | `float32` |
| `10` | `float64` |

### 1.4 Project 模式

Project 模式下，前端当前数据上下文是：

```text
project_id + result_group
```

读取 ODB 数据时 URL path 使用 `project_id`：

```text
/api/odb/{project_id}/...
```

结果相关接口必须带 `result_group`，否则 `result_group=None` 表示 legacy/独立 ODB 的空结果组，不会自动选择一个结果组。

当前明确使用 `result_group` 的接口：

- `GET /api/odb/{odb_id}/meta/overview?result_group=...`
- `GET /api/odb/{odb_id}/results/frame-colors?result_group=...`
- `GET /api/odb/{odb_id}/results/frame-scalars?result_group=...`
- `GET /api/odb/{odb_id}/results/deformed-positions?result_group=...`
- `GET /api/odb/{odb_id}/results/vertex-displacements?result_group=...`
- `GET /api/odb/{odb_id}/results/deformed-normals?result_group=...`
- `GET /api/odb/{odb_id}/results/raw-values?result_group=...`
- `GET /api/odb/{odb_id}/fields?result_group=...`
- `POST /api/odb/{odb_id}/results/node-table` body 中的 `result_group`
- `GET /api/odb/{odb_id}/query/pick?result_group=...`
- `POST /api/odb/{odb_id}/query/ray-pick` body 中的 `result_group`
- `POST /api/odb/{odb_id}/results/external-field` body 中的 `result_group`

几何、截面、颜色编码、bbox、render-faces、surface-patch、nearest-face 当前主要读几何或用户集合，不按 `result_group` 过滤。

## 2. Endpoint 总览

### 2.1 Health

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/health/live` | 进程存活 |
| GET | `/api/health/ready` | 服务就绪 |

### 2.2 Legacy ODB Jobs

> **⚠️ 旧接口，仅保留兼容性。** 当前前端主流程走 [2.3 Projects](#23-projects)，新功能不在此分支迭代。

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/jobs` | 提交独立 ODB 作业 |
| GET | `/api/jobs` | 列出作业 |
| GET | `/api/jobs/{odb_id}` | 查询单个作业 |
| GET | `/api/jobs/{odb_id}/logs` | 查询解析进度日志，支持增量轮询 |
| DELETE | `/api/jobs/{odb_id}?hard=false` | 删除作业，可选删除 workspace |
| POST | `/api/jobs/{odb_id}/retry` | 重试 error 状态作业 |

### 2.3 Projects

> **✅ 当前主要入口。** 前端通过 project 模式提交 ODB/INP，所有查询带 `project_id`。详见 [1.4 Project 模式](#14-project-模式)。

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/projects` | 列出 project 及 result groups |
| POST | `/api/projects` | 创建 project，提交 `.inp` 几何解析或 `.odb` 全量解析 |
| POST | `/api/projects/{source_project_id}/clone` | 克隆 project workspace 和 registry 记录 |
| POST | `/api/projects/{project_id}/results` | 给 project 追加 ODB 结果组 |
| POST | `/api/projects/{project_id}/rerun-l2` | 重新运行 L2 预处理（刷新几何缓存） |
| GET | `/api/projects/{project_id}/logs` | 查询解析进度日志，支持增量轮询 |
| POST | `/api/projects/{project_id}/logs/clear` | 清空该 project 的全部日志 |
| GET | `/api/projects/{project_id}` | 查询 project 详情 |
| GET | `/api/projects/{project_id}/summary` | 读取 `model_summary.json` |
| PATCH | `/api/projects/{project_id}/results/{result_group}` | 修改结果组显示名 |
| DELETE | `/api/projects/{project_id}` | 删除 project 和 workspace |

### 2.4 ODB Data

| 分组 | Method | Path |
|---|---|---|
| meta | GET | `/api/odb/{odb_id}/meta/overview` |
| meta | GET | `/api/odb/{odb_id}/steps` |
| meta | GET | `/api/odb/{odb_id}/steps/{step_name}/frames` |
| meta | GET | `/api/odb/{odb_id}/steps/{step_name}/frames/{frame_idx}` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/render-buffers` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/element-mesh-edges` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/feature-edges` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/lines` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/points` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/couplings` |
| geometry | GET | `/api/odb/{odb_id}/geometry/orientations` |
| geometry | POST | `/api/odb/{odb_id}/geometry/{instance}/render-buffers-subset` |
| results | GET | `/api/odb/{odb_id}/results/frame-colors` |
| results | GET | `/api/odb/{odb_id}/results/frame-scalars` |
| results | GET | `/api/odb/{odb_id}/results/deformed-positions` |
| results | GET | `/api/odb/{odb_id}/results/vertex-displacements` |
| results | GET | `/api/odb/{odb_id}/results/deformed-normals` |
| results | GET | `/api/odb/{odb_id}/results/raw-values` |
| results | GET | `/api/odb/{odb_id}/results/section-mesh` |
| node table | GET | `/api/odb/{odb_id}/fields` |
| node table | POST | `/api/odb/{odb_id}/results/node-table` |
| user field | POST | `/api/odb/{odb_id}/results/user-field` |
| user field | GET | `/api/odb/{odb_id}/results/user-fields` |
| user field | GET | `/api/odb/{odb_id}/results/user-field-colors` |
| user field | DELETE | `/api/odb/{odb_id}/results/user-field` |
| color code | GET | `/api/odb/{odb_id}/color-code/{instance}/schemes` |
| color code | GET | `/api/odb/{odb_id}/color-code/{instance}` |
| color code | GET | `/api/odb/{odb_id}/color-code/{instance}/legend` |
| color code | GET | `/api/odb/{odb_id}/color-code/legend-entries` |
| color code | GET | `/api/odb/{odb_id}/color-code/{instance}/legend-entries` |
| color code | POST | `/api/odb/{odb_id}/color-code/{instance}/legend-entries` |
| color code | GET | `/api/odb/{odb_id}/color-code/{instance}/display-names` |
| color code | POST | `/api/odb/{odb_id}/color-code/{instance}/display-names` |
| color code | GET | `/api/odb/{odb_id}/color-code/{instance}/region-mesh-edges` |
| color code | GET | `/api/odb/{odb_id}/color-code/{instance}/region-outline` |
| query | GET | `/api/odb/{odb_id}/query/pick` |
| query | POST | `/api/odb/{odb_id}/query/ray-pick` |
| query | POST | `/api/odb/{odb_id}/query/bbox` |
| query | POST | `/api/odb/{odb_id}/query/render-faces` |
| query | POST | `/api/odb/{odb_id}/query/surface-patch` |
| query | GET | `/api/odb/{odb_id}/query/nearest-face` |
| query | POST | `/api/odb/{odb_id}/query/node-displacements` |
| external | POST | `/api/odb/{odb_id}/results/external-field` |

## 3. Jobs

### `POST /api/jobs`

请求：

```json
{
  "odb_path": "/data/raw/model.odb",
  "display_name": "model-v1"
}
```

`odb_path` 支持两种格式：
- **本地路径**：`/data/raw/model.odb`（服务端文件系统路径，必须已存在）
- **内网 HTTP URL**：`http://192.168.1.100:9000/files/model.odb`（job runner 在 L1 启动前自动下载到 workspace）

兼容旧字段：`name` 可作为 `display_name` 别名。

响应：

```json
{
  "code": 200,
  "data": {
    "id": "uuid",
    "odb_id": "uuid",
    "display_name": "model-v1",
    "status": "submitted",
    "runner_alive": true,
    "warning": null
  },
  "message": ""
}
```

### `GET /api/jobs`

返回作业数组，每项包含：

- `odb_id`
- `display_name`
- `odb_path`
- `workspace`
- `status`
- `is_render_ready`
- `error_msg`
- `created_at`
- `l1_started_at` / `l1_done_at`
- `l2_started_at` / `l2_done_at`
- `node_count`
- `instance_count`

### `DELETE /api/jobs/{odb_id}`

查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `hard` | `false` | `true` 时同时删除 workspace |

运行中作业不能删除。

### `POST /api/jobs/{odb_id}/retry`

仅允许 `status == "error"` 的作业重试。响应：

```json
{
  "code": 200,
  "data": {
    "id": "uuid",
    "odb_id": "uuid",
    "status": "submitted"
  },
  "message": ""
}
```

### `GET /api/jobs/{odb_id}/logs`

查询解析进度日志。支持增量轮询——每次只返回 `id > since_id` 的新行，前端轮询时将上次响应的 `next_since_id` 传回即可。

查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `since_id` | `0` | 只返回 `id > since_id` 的行 |
| `limit` | `200` | 最多返回行数，上限 1000 |

响应：

```json
{
  "code": 200,
  "data": {
    "logs": [
      {
        "id": 1,
        "ts": "2026-05-07T10:00:01.123456+00:00",
        "level": "step",
        "stage": "l1_dump",
        "message": "<span class=\"kw\">L1 阶段 1</span>：Abaqus 导出（abaqus_dump.py）启动",
        "percent": 5
      },
      {
        "id": 2,
        "ts": "2026-05-07T10:00:01.456789+00:00",
        "level": "info",
        "stage": "l1_dump",
        "message": "=== Layer 1 Phase 1: ODB → npy (full) ===",
        "percent": null
      },
      {
        "id": 3,
        "ts": "2026-05-07T10:00:04.789012+00:00",
        "level": "step",
        "stage": "l1_pack",
        "message": "<span class=\"kw\">L1 阶段 1 完成</span>，开始打包 HDF5（l1_pack.py）",
        "percent": 40
      }
    ],
    "next_since_id": 3,
    "current_percent": 40
  },
  "message": ""
}
```

`level` 取值说明：

| level | 含义 | 建议渲染颜色 |
|---|---|---|
| `step` | 流水线阶段切换，消息含 HTML span | — |
| `info` | 子进程原始输出（纯文本） | 默认 |
| `warn` | 警告，消息含 HTML span | — |
| `error` | 失败行，消息含 HTML span | — |

`message` 格式说明：

- `step` / `warn` / `error` 级别的消息是 **HTML 片段**，含 `<span class="kw/num/good/warn/bad">` 标签，前端需用 `v-html` 渲染在 `<pre>` 内。
- `info` 级别的消息是**纯文本**（来自 Abaqus、l1_pack、ingest 等子进程的原始输出），直接显示即可。
- 元素类型摘要块（INP/ODB/BDF/OP2 L1 完成后自动写入）是一条含换行的 `info` 消息，含 HTML span，同样需 `v-html` 渲染。

`percent` 字段说明：

- 每条日志行的 `percent`：`step` 级别的关键节点写入进度值（0–100），其余行为 `null`。
- 顶层 `current_percent`：该 job 所有日志中最新一条非 null 的 `percent`，用于进度条，不受 `since_id` 限制（始终返回全局最新值）。

`stage` 取值说明：

| stage | 对应阶段 |
|---|---|
| `l1_dump` | `abaqus_dump.py` —— Abaqus Python 2.7 导出 npy |
| `l1_pack` | `l1_pack.py` —— 打包 npy → HDF5 + manifest.db |
| `l1_inp` | INP 路径：解析 INP 文件并导出 L1 数据 |
| `l1_done` | L1 完成的汇总事件（节点数、实例数） |
| `l2_ingest` | `ingest.py` —— 三角面提取、特征边、Octree |
| `l2_done` | L2 完成，作业状态置为 `ready` |

**前端轮询示例：**

```javascript
let sinceId = 0;

async function pollLogs(odbId) {
  while (true) {
    const res = await fetch(`/api/jobs/${odbId}/logs?since_id=${sinceId}&limit=200`);
    const { data } = await res.json();
    // data.current_percent → 进度条（0-100，null 表示尚未开始）
    updateProgressBar(data.current_percent);
    // message 含 HTML span，用 v-html 渲染在 <pre> 内
    data.logs.forEach(row => appendToUI(row));
    sinceId = data.next_since_id;

    const job = await fetchJobStatus(odbId);
    if (job.status === 'ready' || job.status === 'error') break;

    await sleep(2000);
  }
}
```

注意事项：
- `job_logs` 在删除作业时级联清除（`DELETE /api/jobs/{odb_id}`）。
- 作业完成后日志永久保留，可随时查询历史。

## 4. Projects

### `GET /api/projects`

响应：

```json
{
  "code": 200,
  "data": [
    {
      "project_id": "proj-001",
      "geom_status": "ready",
      "result_groups": [
        {
          "result_group": "case1",
          "display_name": "工况一",
          "status": "ready",
          "consistency_check": "count-only",
          "error_message": null,
          "steps": ["Step-1"]
        }
      ]
    }
  ],
  "message": ""
}
```

### `POST /api/projects`

请求：

```json
{
  "project_id": "proj-001",
  "source_path": "http://内网地址/files/model.bdf",
  "source_type": "bdf"
}
```

本地文件用 `local_path`：

```json
{
  "project_id": "proj-002",
  "local_path": "D:/models/door.inp"
}
```

说明：

- `source_path` 现在专门表示内网 HTTP URL。
- `local_path` 专门表示后端机器本地可访问的文件路径。
- 两者必须二选一，不能同时传，也不能都不传。
- `source_type` 仍可显式指定；不传时会按 `source_path` 或 `local_path` 的扩展名自动推断。

`source_path` / `local_path` 支持的类型与触发流程：

| source_type | 触发流程 | 说明 |
|---|---|---|
| `inp` | INP 几何解析 → L2 | 保留原有 INP 解析行为，同时触发 INP catalog / model-update 导入 |
| `odb` | `abaqus_dump → l1_pack → ingest` | 全量 ODB 解析，不触发 model-update 导入 |
| `bdf` | `bdf_pack → ingest → model_update BDF 导入` | Nastran BDF 几何，解析完自动导入 model_update |
| `op2` | `op2_geom_pack → ingest`（仅含几何 OP2）或 `op2_geom_pack → ingest → op2_pack`（含几何+结果）| 自动检测 OP2 是否含 GEOM1/GEOM2；不含几何时拒绝，应改用追加结果组接口 |

**BDF+OP2 典型流程：**

1. 先用 BDF 建项目（几何）：`source_type: "bdf"`
2. 轮询 `GET /api/projects/{project_id}` 直到 `geom_status: "ready"`
3. 再通过 `POST /api/projects/{project_id}/results` 追加 OP2 结果组

重复提交同一 `project_id`：
- `geom_status` 为 `error` → 清空 workspace，重新解析
- 其他状态（`pending`/`running`/`ready`）→ 409

返回：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-001",
    "source_type": "bdf",
    "geom_status": "pending"
  },
  "message": ""
}
```

### `POST /api/projects/{source_project_id}/clone`

请求：

```json
{
  "new_project_id": "proj-001-copy"
}
```

说明：

- 克隆源 project workspace 到新 project。
- 克隆 registry 中的 project/result_groups 记录。
- 如果源 project 有 pending/running 几何或结果任务，会返回冲突错误。
- 源 workspace 中存在 symlink 时拒绝复制。

响应：

```json
{
  "code": 200,
  "data": {
    "source_project_id": "proj-001",
    "project_id": "proj-001-copy",
    "geom_status": "ready",
    "result_group_count": 2
  },
  "message": ""
}
```

### `POST /api/projects/{project_id}/results`

给已有 project 追加结果组，支持 ODB 和 OP2 两种格式。

**重复提交行为：**
- `result_group` 同名且 `status` 为 `error` 或 `ready` → 允许覆盖，重新解析（旧数据清除）
- `status` 为 `running`（正在处理中）→ 409

#### ODB 结果组

```json
{
  “source_path”: “/data/case1.odb”,
  “result_group”: “case1”,
  “display_name”: “工况一”,
  “parse_options”: {
    “consistency_check”: “count-only”
  }
}
```

`parse_options` 字段说明（ODB）：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `consistency_check` | string | `count-only` | 一致性校验策略，可选 `count-only` / `label-only` |
| `steps` | string[] | `null` | 只解析这些 step；`null` 表示全部 |
| `frames` | string \| int[] | `all` | 帧过滤；支持 `all`、`first_last`、或 0-based 帧号数组如 `[0, 3, 8]` |
| `field_prefix` | string | `null` | 只解析字段名前缀匹配的结果，例如 `d_U_` |
| `invariants` | string | `none` | 是否提取 invariant；可选 `none` / `full` |

注意：`frames` 过滤后帧会在该结果组内重新编号为连续 `frame_idx`，例如传 `[5]` 时查询用 `frame_idx=0`。

灵敏度结果场景示例：

```json
{
  “source_path”: “/data/sensitivity/case_dU.odb”,
  “result_group”: “sens-dU”,
  “display_name”: “灵敏度 dU”,
  “parse_options”: {
    “consistency_check”: “count-only”,
    “steps”: [“Step-1”],
    “frames”: [5],
    “field_prefix”: “d_U_”
  }
}
```

#### OP2 结果组

适用于已用 BDF 建好几何的 project，追加 Nastran OP2 结果。

**最简调用（只解析，不导入模态）：**

```json
{
  “source_path”: “http://内网地址/files/result.op2”,
  “result_group”: “run_01”,
  “display_name”: “工况一”
}
```

**带模态自动导入（解析完同时写入 model_update 数据库）：**

```json
{
  “source_path”: “http://内网地址/files/result.op2”,
  “result_group”: “run_01”,
  “display_name”: “工况一”,
  “parse_options”: {
    “modal_import”: {}
  }
}
```

`modal_import` 传空对象 `{}` 即可，BDF 路径自动取该 project 的 `inp_path`，OP2 路径使用当前上传的文件，无需再传。

`parse_options.modal_import` 可选细化字段：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `subcase_id` | int | `null` | 指定 OP2 subcase 编号，不传取第一个 |
| `mode_numbers` | int[] | `null` | 只导入指定阶次，不传则全导入 |
| `instance_name` | string | `null` | 多实例模型时指定 instance |
| `part_name` | string | `null` | 指定 part 名 |
| `bdf_path` | string | 自动取 project inp_path | 显式指定 BDF 路径 |
| `overwrite` | bool | `true` | 是否覆盖已有模态数据 |
| `async_submit` | bool | `true` | 异步写库（不阻塞解析流程） |

返回：

```json
{
  “code”: 200,
  “data”: {
    “project_id”: “proj-001”,
    “result_group”: “run_01”,
    “status”: “pending”
  },
  “message”: “”
}
```

### `POST /api/projects/{project_id}/rerun-l2`

触发 L2 预处理（`ingest.py`）重新运行，用于后端 L2 逻辑升级后刷新几何缓存（例如新增了线单元渲染支持）。

**前提条件**：`geom_status` 必须为 `ready` 或 `error`；其他状态返回 409。

**运行期间**：`geom_status` 置为 `l2_running`，所有几何接口（`render-buffers`、`feature-edges`、`lines` 等）返回 503，避免读到写一半的 HDF5 文件。

响应（HTTP 202）：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-001",
    "geom_status": "l2_pending"
  },
  "message": ""
}
```

进度通过 `GET /api/projects/{project_id}/logs` 实时查询，`stage` 为 `l2_ingest` 或 `l2_done`。重跑前可先调 `POST .../logs/clear` 清空旧日志。

### `GET /api/projects/{project_id}`

返回结构同列表中的单个 project。

### `GET /api/projects/{project_id}/summary`

读取 project workspace 下的 `model_summary.json`。几何尚未解析完成或文件不存在时返回 404。

### `PATCH /api/projects/{project_id}/results/{result_group}`

请求：

```json
{
  "display_name": "新显示名"
}
```

响应：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-001",
    "result_group": "case1",
    "display_name": "新显示名"
  },
  "message": ""
}
```

### `DELETE /api/projects/{project_id}`

删除 project registry 记录、result group 记录和 project workspace。

响应：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-001",
    "deleted": true
  },
  "message": ""
}
```

### `GET /api/projects/{project_id}/logs`

查询 project 解析进度日志，支持增量轮询。`message` 格式、`percent`/`current_percent` 字段含义与 `GET /api/jobs/{odb_id}/logs` 完全一致，参见上节。

**覆盖范围**：几何管道（INP/ODB/BDF/OP2 L1+L2）+ 所有结果组（result_group）的解析日志，统一按 `project_id` 索引。

查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `since_id` | `0` | 只返回 `id > since_id` 的行 |
| `limit` | `200` | 最多返回行数，上限 1000 |

响应示例：

```json
{
  "code": 200,
  "data": {
    "logs": [
      {"id": 1,  "ts": "...", "level": "step", "stage": "l1_inp",       "message": "<span class=\"kw\">几何解析开始</span>：解析 INP 文件 model.inp", "percent": 5},
      {"id": 2,  "ts": "...", "level": "info", "stage": "l1_inp",       "message": "<span class=\"kw\">READING</span> : FEM\n...",                      "percent": null},
      {"id": 10, "ts": "...", "level": "step", "stage": "l1_inp",       "message": "<span class=\"kw\">INP 解析完成</span>，导出几何 HDF5",             "percent": 30},
      {"id": 20, "ts": "...", "level": "step", "stage": "l2_done",      "message": "<span class=\"good\">几何解析完成，已就绪</span>",                   "percent": 100},
      {"id": 21, "ts": "...", "level": "step", "stage": "rg_extract",   "message": "[<span class=\"kw\">case1</span>] 结果组解析：一致性校验 + 提取",     "percent": null},
      {"id": 35, "ts": "...", "level": "step", "stage": "rg_done",      "message": "[<span class=\"kw\">case1</span>] <span class=\"good\">结果组解析完成，已就绪</span>", "percent": null}
    ],
    "next_since_id": 35,
    "current_percent": 100
  },
  "message": ""
}
```

`stage` 取值说明（Project 模式）：

| stage | 对应阶段 | percent |
|---|---|---|
| `l1_inp` | INP 路径：解析 INP + 导出几何 HDF5 | 5 → 30 |
| `l1_bdf` | BDF 路径：`bdf_pack.py` 提取几何 | 5 → 50 |
| `l1_geom` | OP2 路径：`op2_geom_pack.py` 提取几何 | 5 → 50 |
| `l1_dump` | ODB 路径：`abaqus_dump.py` 导出 npy | 5 → 40 |
| `l1_pack` | ODB 路径：`l1_pack.py` 打包 HDF5 | 40 → 62 |
| `l1_done` | L1 完成汇总（节点数、实例数） | 60–62 |
| `catalog` | INP/ODB catalog 导入（测点/参数，非致命） | 35–67 |
| `l2_ingest` | `ingest.py`：三角面提取、特征边、Octree | 45–70 |
| `l2_done` | 几何管道完成，状态置 `ready` | 75–100 |
| `mu_import_bdf` | BDF 自动导入 model_update | 90 → 100 |
| `rg_preflight` | 结果组文件检查 | null |
| `rg_extract` | 结果组数据提取（abaqus_dump） | null |
| `rg_l1_pack` | 结果组打包 HDF5 | null |
| `rg_op2` | OP2 结果组打包 | null |
| `rg_rerun_l2` | 结果组完成后重跑 L2 重建平均域 | null |
| `rg_done` | 结果组解析完成 | null |
| `mu_import_op2_modal` | OP2 模态结果自动导入 model_update | null |

注意事项：
- 几何管道和所有结果组的日志都写入同一个 `project_id` 下，不需要分别查询。
- 结果组相关行的 `message` 中包含 `[<span class="kw">结果组名</span>]` 前缀，可按此过滤。
- 几何管道完成（`l2_done`）后 `current_percent` 固定为 100，结果组解析期间不更新进度。
- 删除 project 时日志随 `job_logs` 记录一并删除。

## 5. Metadata

### `GET /api/odb/{odb_id}/meta/overview`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `result_group` | 否 | Project 模式下指定结果组 |

响应：

```json
{
  "code": 200,
  "data": {
    "instances": [
      {"instance_name": "PART-1-1"}
    ],
    "steps": [
      {"step_name": "Step-1", "procedure": "STATIC", "num_frames": 3}
    ],
    "fields": [
      {
        "field_name": "U",
        "components": "[\"U1\",\"U2\",\"U3\"]",
        "positions": "[\"NODAL\"]",
        "source": "odb"
      }
    ]
  },
  "message": ""
}
```

说明：

- `instances` 来自几何 manifest，不按 `result_group` 过滤。
- `steps` / `fields` 会按 `result_group` 过滤。
- `fields[*].source` 可能是 `odb` 或 `external`。
- `components` / `positions` 当前通常是 JSON 字符串，前端需要解析。

### `GET /api/odb/{odb_id}/steps`

列出该 ODB 的所有分析步。

响应：

```json
{
  "code": 200,
  "data": [
    {
      "step_name": "sag",
      "step_number": 1,
      "procedure": "STATIC",
      "num_frames": 20,
      "description": "Static sag load"
    },
    {
      "step_name": "eigenfrequency",
      "step_number": 2,
      "procedure": "FREQUENCY",
      "num_frames": 5,
      "description": null
    }
  ],
  "message": ""
}
```

说明：
- 按 `step_number` 排序（即 ODB 中定义顺序）。
- `description` 来自 ODB `step.description` 属性；旧版 manifest.db 中该列不存在时返回 `null`，不会报错。

---

### `GET /api/odb/{odb_id}/steps/{step_name}/frames`

列出指定步骤的所有帧（轻量，供下拉列表使用）。

路径参数：

| 参数 | 说明 |
|---|---|
| `step_name` | 步骤名，如 `sag` |

响应：

```json
{
  "code": 200,
  "data": [
    {"frame_idx": 0, "frame_value": 0.05, "description": "Increment 1: Step Time = 0.05"},
    {"frame_idx": 19, "frame_value": 1.0,  "description": "Increment 20: Step Time = 1.000"}
  ],
  "message": ""
}
```

模态步示例：

```json
{
  "code": 200,
  "data": [
    {"frame_idx": 0, "frame_value": 1.0, "description": "Mode 1: Value = 12345.  Freq = 17.69  (cycles/time)"},
    {"frame_idx": 4, "frame_value": 5.0, "description": "Mode 5: Value = 85255.  Freq = 46.47  (cycles/time)"}
  ],
  "message": ""
}
```

---

### `GET /api/odb/{odb_id}/steps/{step_name}/frames/{frame_idx}`

获取单帧完整元数据。

路径参数：

| 参数 | 说明 |
|---|---|
| `step_name` | 步骤名 |
| `frame_idx` | 帧索引（0-based，重索引后的序号） |

响应：

```json
{
  "code": 200,
  "data": {
    "frame_idx": 4,
    "frame_value": 5.0,
    "description": "Mode 5: Value = 85255.  Freq = 46.471  (cycles/time)",
    "domain": "MODAL",
    "frequency": 46.471,
    "mode_number": 5,
    "increment_number": 1,
    "is_imaginary": 0,
    "frame_id": 1,
    "cyclic_mode_number": null,
    "load_case": null
  },
  "message": ""
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `frame_idx` | 重索引后的 0-based 序号（与结果查询接口的 `frame` 参数对应） |
| `frame_value` | 时间轴值（静力步为时间，模态步为模态阶次） |
| `description` | ODB 帧描述文本 |
| `domain` | `TIME` / `MODAL` / `FREQUENCY`；旧库返回 `null` |
| `frequency` | 模态频率（Hz）；非模态步为 `null` |
| `mode_number` | 模态阶次；非模态步为 `null` |
| `increment_number` | 增量步号；旧库返回 `null` |
| `is_imaginary` | 是否虚部帧（复数频响），`0` 或 `1`；旧库返回 `null` |
| `frame_id` | ODB 原始帧 ID（可能与 `frame_idx` 不同）；旧库返回 `null` |
| `cyclic_mode_number` | 循环对称模态号，通常为 `null` |
| `load_case` | 载荷工况，通常为 `null` |

步骤或帧不存在时返回 404。

---

## 6. Geometry

### `GET /api/odb/{odb_id}/geometry/{instance}/render-buffers`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `set` | 否 | user set 名；找不到 user set 时 fallback 到 element set |

响应头：

```text
X-Face-Count: <Nt>
```

L3BE sections：

| 名称 | 形状 | 类型 | 说明 |
|---|---|---|---|
| `positions` | `[Nv, 3]` | `float32` | indexed vertex positions |
| `indices` | `[Nt, 3]` | `int32` | triangle index buffer，可能缺省 |

说明：

- 路由注释提到 normals 可选，但当前实现只返回 `positions` 和 `indices`。
- 前端如需法线，应在本地 `computeVertexNormals()`。
- `set` 过滤会压缩 vertex buffer，只保留被选三角面使用的顶点。

### `GET /api/odb/{odb_id}/geometry/{instance}/element-mesh-edges`

响应头：

```text
X-Edge-Count: <E>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `edge_positions` | `[E*2, 3]` | `float32` |

### `GET /api/odb/{odb_id}/geometry/{instance}/feature-edges`

返回格式与 `element-mesh-edges` 相同，但只包含边界和折痕等 feature edges。

### `GET /api/odb/{odb_id}/geometry/orientations`

返回模型级别的命名坐标系（来自 INP 的 `*ORIENTATION` 或 ODB 的 `datumCsyses`）。

响应 JSON：

```json
{
  "orientations": [
    {
      "name":   "ORI-1",
      "system": "RECTANGULAR",
      "origin": [0.0, 0.0, 0.0],
      "axes":   [[1,0,0], [0,1,0], [0,0,1]]
    }
  ]
}
```

- `axes[0]` = 局部 1 轴（X，前端显示为红色）
- `axes[1]` = 局部 2 轴（Y，前端显示为绿色）
- `axes[2]` = 局部 3 轴（Z，前端显示为蓝色）
- 无命名坐标系时返回 `{"orientations": []}`，不报错。
- 前端渲染：每个坐标系在 origin 处画三条 RGB 彩线，线长 = bbox 对角线的 5%。

### `GET /api/odb/{odb_id}/geometry/{instance}/lines`

返回梁/桁架等**线单元**的端点线段数据（B31、B32、T3D2、T3D3、PIPE31、PIPE32 等）。

线单元没有面，不包含在 `render-buffers` 的三角面片中，需要单独获取并用 `THREE.LineSegments` 渲染。

L3BE sections：

| 名称 | 形状 | 类型 | 说明 |
|---|---|---|---|
| `line_positions` | `[N*2, 3]` | `float32` | 交错端点对：第 2i 行和第 2i+1 行是第 i 条线段的两个端点 |
| `elem_labels` | `[N]` | `int32` | 每条线段对应的 Abaqus 单元 label（用于拾取） |

说明：
- 无独立响应头；线段数 N 可由 `line_positions.shape[0] / 2` 计算得到（前端直接读 payload shape）。
- 该实例无线单元时返回空 payload（`line_positions` shape `[0, 3]`），不报错。
- `line_positions` 格式与 `THREE.LineSegments` 的 `BufferGeometry` 直接兼容。

### `POST /api/odb/{odb_id}/geometry/{instance}/render-buffers-subset`

请求：

```json
{
  "elem_labels": [1001, 1002, 1003]
}
```

响应同 `render-buffers`。无匹配时返回空几何，并带：

```text
X-Face-Count: 0
```

## 7. Results

### `GET /api/odb/{odb_id}/results/frame-colors`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | step name |
| `field` | 是 | field name |
| `frame` | 否 | 默认 `0` |
| `component` | 否 | `U1` / `U2` / `U3` / `USUM`，默认 `USUM` |
| `mode` | 否 | `smooth` / `flat`，默认 `smooth` |
| `result_group` | 否 | Project 模式结果组 |
| `set` | 否 | user set / element set 过滤 |

响应头：

```text
X-Payload-Type: frame_colors_v1
X-Layout-Version: 1
X-Val-Min: <float>
X-Val-Max: <float>
X-Component: <component>
X-Frame: <frame>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `color_per_vertex` | `[Rf*3, 4]` | `uint8` |
| `legend_range` | `[2]` | `float32` |

### `GET /api/odb/{odb_id}/results/frame-scalars`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | step name |
| `field` | 是 | field name |
| `frame` | 否 | 默认 `0` |
| `component_idx` | 否 | 0-based component index；省略时取 magnitude |
| `mode` | 否 | `smooth` / `flat`，默认 `smooth` |
| `result_group` | 否 | Project 模式结果组 |
| `set` | 否 | user set / element set 过滤 |
| `feature_angle` | 否 | shell/membrane 几何分域角度，默认 `20.0` |
| `average_threshold` | 否 | 条件平均阈值，默认 `0.75` |
| `use_geometry_split` | 否 | 是否使用几何分裂，默认 `true` |

响应头：

```text
X-Payload-Type: frame_scalars_v1
X-Result-Position: NODAL | ELEMENT_NODAL | ELEMENT_NODAL_FLAT | INTEGRATION_POINT_FLAT
X-Normalization-Scope: instance
X-Val-Min: <float>
X-Val-Max: <float>
X-Component-Idx: <idx>|mag
X-Frame: <frame>
X-Feature-Angle: <float>|none
X-Average-Threshold: <float>
X-Use-Geometry-Split: true|false
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `u_per_vertex` | `[Nv]` 或 `[Rf*3]` | `float32` |
| `legend_range` | `[2]` | `float32` |

说明：

- indexed geometry 下通常返回 `[Nv]`。
- Triangle Soup fallback 下可能返回 `[Rf*3]`。
- 前端当前用这个接口拿标量，再自行应用 colormap。

### `GET /api/odb/{odb_id}/results/deformed-positions`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | step name；读取该 step 下的 `U` NODAL 位移 |
| `frame` | 否 | 默认 `0` |
| `scale` | 否 | 变形放大系数，默认 `1.0` |
| `result_group` | 否 | Project 模式结果组 |

响应头：

```text
X-Vertex-Count: <Nv>
X-Frame: <frame>
X-Scale: <scale>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `positions` | `[Nv, 3]` | `float32` |
| `normals` | `[Nv, 3]` | `float32` |

说明：

- `positions = original_positions + scale * U_per_vertex`
- 需要 indexed geometry，即后端 `ModelIndex` 中存在 `vtx_node_row`。

### `GET /api/odb/{odb_id}/results/vertex-displacements`

返回 U 场在每个渲染顶点上的原始位移值，不乘 scale、不加原始坐标。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | step name；读取该 step 下的 `U` NODAL 位移 |
| `frame` | 否 | 默认 `0` |
| `result_group` | 否 | Project 模式结果组 |

响应头：

```text
X-Vertex-Count: <Nv>
X-Frame: <frame>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `displacements` | `[Nv, 3]` | `float32` |

说明：

- 返回值 = U NODAL 数据，按 `vtx_node_row` 映射到渲染顶点坐标系后的结果（UX/UY/UZ）。
- 前端可自行乘以 scale 后加上原始坐标，或用于计算位移幅值云图。
- 需要 indexed geometry。

### `GET /api/odb/{odb_id}/results/deformed-normals`

返回变形后几何的逐顶点法向量，参数和 scale 与 `deformed-positions` 完全一致。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | step name；读取该 step 下的 `U` NODAL 位移 |
| `frame` | 否 | 默认 `0` |
| `scale` | 否 | 变形放大系数，默认 `1.0` |
| `result_group` | 否 | Project 模式结果组 |

响应头：

```text
X-Vertex-Count: <Nv>
X-Frame: <frame>
X-Scale: <scale>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `normals` | `[Nv, 3]` | `float32` |

说明：

- 法向量基于 `original_positions + scale * U` 重新计算，与 `deformed-positions` 返回的 normals 完全一致。
- 适合前端已拿到 deformed positions，只需更新法向量用于光照的场景，节省带宽。
- 需要 indexed geometry。

### `GET /api/odb/{odb_id}/results/modal-shape`

返回指定模态阶次的顶点位移向量（不乘 scale），供前端 GPU Shader 模式上传为 vertex attribute。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | FREQUENCY step name |
| `frame` | 否 | 模态阶次索引，默认 `0` |
| `result_group` | 否 | Project 模式结果组 |

响应头：`X-Vertex-Count`, `X-Frame`

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `displacement` | `[Nv, 3]` | `float32` |

---

### `GET /api/odb/{odb_id}/results/modal-animation`

预计算 N 帧谐波动画坐标，一次性返回。每帧 = `positions + scale * sin(2π*i/N) * displacement`。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | FREQUENCY step name |
| `frame` | 否 | 模态阶次索引，默认 `0` |
| `scale` | 否 | 变形放大系数，默认 `1.0` |
| `n_frames` | 否 | 帧数，4–120，默认 `20` |
| `result_group` | 否 | Project 模式结果组 |

响应头：`X-N-Frames`, `X-Frame`, `X-Scale`

响应体（原始二进制，非 L3BE）：

```text
[n_frames: uint32][n_verts: uint32][n_frames × n_verts × 3 × float32]
```

前端解析：
```js
const nF  = dv.getUint32(0, true)
const nV  = dv.getUint32(4, true)
const raw = new Float32Array(ab, 8, nF * nV * 3)
```

---

### `GET /api/odb/{odb_id}/results/raw-values`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | step name |
| `field` | 是 | field name |
| `frame` | 否 | 默认 `0` |
| `position` | 是 | `NODAL` / `ELEMENT_NODAL` / `INTEGRATION_POINT` |
| `format` | 否 | `json` / `l3be`，默认 `json` |
| `result_group` | 否 | Project 模式结果组 |

公共响应头：

```text
X-Position: <position>
X-Frame: <frame>
X-Components: ["S11","S22",...]
X-Etype-Groups: ["C3D8R","C3D4",...]
```

`format=json` 时，`NODAL` 示例：

```json
{
  "code": 200,
  "data": {
    "odb_id": "xxx",
    "instance": "PART-1-1",
    "step": "Step-1",
    "field": "U",
    "frame": 0,
    "position": "NODAL",
    "components": ["U1", "U2", "U3"],
    "node_labels": [1, 2, 3],
    "values": [[0.0, 0.1, 0.2]]
  },
  "message": ""
}
```

`format=l3be` sections：

| position | section | 形状 | 类型 |
|---|---|---|---|
| `NODAL` | `node_labels` | `[N]` | `int32` |
| `NODAL` | `values` | `[N, ncomp]` | `float32` |
| element-like | `el_{etype}` | `[M]` | `int32` |
| element-like | `v_{etype}` | `[M, ...]` | `float32` |

### `GET /api/odb/{odb_id}/results/section-mesh`

查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `instance` | 必填 | instance name |
| `axis` | `Z` | `X` / `Y` / `Z` |
| `position` | `0.0` | 截面位置 |

响应头：

```text
X-Payload-Type: section_mesh_v1
X-Layout-Version: 1
X-Tri-Count: <T>
X-Edge-Count: <E>
X-Axis: X|Y|Z
X-Position: <float>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `vertices` | `[T*3, 3]` | `float32` |
| `edge_verts` | `[E*2, 3]` | `float32` |

## 8. Node Table

### `GET /api/odb/{odb_id}/fields`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `step` | 是 | step name |
| `result_group` | 否 | Project 模式结果组 |

响应：

```json
{
  "code": 200,
  "data": {
    "instance": "PART-1-1",
    "step": "Step-1",
    "fields": [
      {
        "name": "U",
        "positions": ["NODAL"],
        "components": ["U1", "U2", "U3"]
      }
    ]
  },
  "message": ""
}
```

### `POST /api/odb/{odb_id}/results/node-table`

请求：

```json
{
  "instance": "PART-1-1",
  "step": "Step-1",
  "frame_idx": 0,
  "node_labels": [1, 2, 3],
  "items": [
    {"field": "U", "component": "U1"},
    {"field": "RF", "component": ""}
  ],
  "result_group": "case1"
}
```

响应头：

```text
X-Payload-Type: node_table_v1
X-Layout-Version: 1
X-Node-Count: <N>
X-Col-Count: <M>
X-Columns: [...]
X-Field-Coverage: step
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `node_labels` | `[N]` | `int32` |
| `values` | `[N, M]` | `float32` |

## 9. User Field

### `POST /api/odb/{odb_id}/results/user-field`

请求：

```json
{
  "name": "fatigue_damage",
  "instance": "PART-1-1",
  "value": 0.85,
  "element_labels": [1001, 1002, 1003]
}
```

响应 `data`：

```json
{
  "id": 1,
  "name": "fatigue_damage",
  "instance": "PART-1-1",
  "value": 0.85,
  "elem_count": 3
}
```

### `GET /api/odb/{odb_id}/results/user-fields`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 否 | 仅查询某个 instance |

### `GET /api/odb/{odb_id}/results/user-field-colors`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | user field name |
| `instance` | 是 | instance name |
| `val_min` | 否 | 归一化最小值 |
| `val_max` | 否 | 归一化最大值 |

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `color_per_vertex` | `[Rf*3, 4]` | `uint8` |
| `legend_range` | `[2]` | `float32` |

### `DELETE /api/odb/{odb_id}/results/user-field`

查询参数：

| 参数 | 必填 |
|---|---|
| `name` | 是 |
| `instance` | 是 |

成功响应：

```json
{
  "code": 200,
  "data": {
    "deleted": true,
    "name": "fatigue_damage",
    "instance": "PART-1-1"
  },
  "message": ""
}
```

## 10. Color Code

### `GET /api/odb/{odb_id}/color-code/{instance}/schemes`

响应：

```json
{
  "code": 200,
  "data": {
    "schemes": ["etype", "section", "material", "section_type", "elset"],
    "elsets": ["SET_A", "SET_B"]
  },
  "message": ""
}
```

`schemes` 可能值说明：

| 值 | 说明 | 可用条件 |
|---|---|---|
| `etype` | 按单元类型上色 | 始终可用 |
| `section` | 按平均域（Averaging Region）上色 | 模型有截面分区时可用（ODB 和 INP+ODB 均支持） |
| `material` | 按材料名上色 | L1 包含材料信息时可用 |
| `section_type` | 按截面类型（SOLID/SHELL/…）上色 | L1 包含截面类型信息时可用 |
| `elset` | 高亮指定单元集 | L1 包含 element set 时可用，需同时传 `set_names` |

### `GET /api/odb/{odb_id}/color-code/{instance}`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 是 | `etype` / `section` / `material` / `section_type` / `elset` |
| `set_names` | 否 | 逗号分隔，仅 `scheme=elset` 使用 |

响应 body 为 L3BE 二进制，legend 嵌入 L3BE section 中（非响应头）：

L3BE sections：

| 名称 | 形状 | 类型 | 说明 |
|---|---|---|---|
| `color_per_vertex` | `[Nv, 3]` 或 `[Rf*3, 3]` | `float32` | 逐顶点 RGB |
| `legend` | `[N]` | `uint8` | JSON bytes，解析后为 `[{id, name, r, g, b}, ...]` |

响应头：

```text
X-Face-Count: <Rf>
```

legend 中 `name` 字段若用户已通过 display-names 接口设置过自定义名称，则返回自定义名称。

### `GET /api/odb/{odb_id}/color-code/{instance}/display-names`

查询用户自定义的 legend 显示名称。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 否 | `etype` / `section` / `material` / `section_type` / `elset`；省略时返回所有 scheme |

**传 `scheme` 时**，返回该 scheme 的映射：

```json
{
  "code": 200,
  "data": {
    "Region 1": "顶板",
    "Region 2": "腹板"
  },
  "message": ""
}
```

**不传 `scheme` 时**，返回所有 scheme 的映射（按 scheme 分组）：

```json
{
  "code": 200,
  "data": {
    "section": {"Region 1": "顶板", "Region 2": "腹板"},
    "elset": {"SET_A": "左翼缘"}
  },
  "message": ""
}
```

未设置过时返回空对象 `{}`。

### `POST /api/odb/{odb_id}/color-code/{instance}/display-names`

保存 legend 显示名称。`legend_key` 为 legend 中原始 `name` 值（即 `GET color-code` 返回的 legend 里未替换前的内部名称）。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 是 | 同上 |

请求 body（`application/json`）：

```json
{
  "Region 1": "顶板",
  "Region 2": "腹板"
}
```

- 只需传需要修改的条目，不会清除其他已有映射。
- 重复 PUT 同一个 key 会覆盖旧值。
- 存储在 workspace 的 `manifest.db` `display_names` 表，重启服务后保留。

响应：

```json
{
  "code": 200,
  "data": {},
  "message": ""
}
```

### `GET /api/odb/{odb_id}/color-code/{instance}/legend`

轻量接口：只返回颜色映射表，不构建顶点色数组。适合仅需要图例信息的场景（面板展示、颜色选择器）。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 是 | etype \| material \| section_type \| section \| elset |
| `set_names` | elset 时必填 | 逗号分隔的集合名称 |

响应 `data.legend` 数组，每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 调色板索引（0-based） |
| `legend_key` | string | 原始内部 key（用于 region-outline / region-mesh-edges API 调用） |
| `name` | string | 显示名称（已合并用户自定义名称覆盖） |
| `r / g / b` | float | 0–1 范围颜色（已合并用户自定义颜色覆盖） |

前端调用示例（`useOdbApi.js`）：
```js
const res = await api.fetchLegend(instance, scheme)
const legend = res.data?.legend ?? []
```

### `GET /api/odb/{odb_id}/color-code/legend-entries`

返回所有 instance 的 legend 条目（聚合版），与单 instance 接口格式相同，每项多一个 `instance` 字段。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 是 | etype \| material \| section_type \| section \| elset |
| `set_names` | elset 时必填 | 逗号分隔的集合名称 |

响应 `data.entries` 数组，每项比单 instance 接口多：

| 字段 | 类型 | 说明 |
|---|---|---|
| `instance` | string | 该条目所属的 instance 名称 |

其余字段同下方单 instance 接口。

---

### `GET /api/odb/{odb_id}/color-code/{instance}/legend-entries`

返回当前 scheme 所有 legend 条目，供 LegendEditor 浮窗使用。包含面数统计、默认 title（section scheme 自动推断材料名）、用户自定义名称和颜色覆盖。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 是 | etype \| material \| section_type \| section \| elset |
| `set_names` | elset 时必填 | 逗号分隔的集合名称 |

响应 `data.entries` 数组，每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `legend_key` | string | 原始内部 key（用于 API 调用） |
| `default_title` | string | 自动推断的默认标题（section 方案为材料名） |
| `display_name` | string \| null | 用户自定义名称，null 表示未设置 |
| `color_r/g/b` | float | 当前实际颜色（已合并用户覆盖或调色板自动分配） |
| `user_color` | bool | true = 颜色来自用户覆盖 |
| `user_name` | bool | true = 名称来自用户覆盖 |
| `face_count` | int | 该条目对应的渲染面数 |

### `POST /api/odb/{odb_id}/color-code/{instance}/legend-entries`

批量保存 legend 条目的名称和颜色覆盖。传 null 表示清除该覆盖（恢复自动分配）。

查询参数：`scheme`（必填）

请求 body（`application/json`，数组）：

```json
[
  {"legend_key": "Region 1", "display_name": "顶板", "color_r": 0.27, "color_g": 0.52, "color_b": 0.95},
  {"legend_key": "Region 2", "display_name": null, "color_r": null, "color_g": null, "color_b": null}
]
```

存储在 `manifest.db` `display_names` 表（新增 `color_r/g/b` 列），重启保留。

### `GET /api/odb/{odb_id}/color-code/{instance}/region-mesh-edges`

返回指定区域的**单元网格边**（每条有限元单元的真实边界，四边形仍显示为四边形，不是三角面片对角线）。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 是 | `section` 或 `etype` |
| `region` | 是 | 区域标签，与 legend 中的 `name` 一致，例如 `Region 1` |

响应：L3BE 二进制，`Content-Type: application/octet-stream`

| Section | dtype | shape | 说明 |
|---|---|---|---|
| `edge_positions` | float32 | `[E*2, 3]` | 每两行为一条边的起点/终点 XYZ |

Header：`X-Edge-Count: E`（边的数量，即行数 / 2）

### `GET /api/odb/{odb_id}/color-code/{instance}/region-outline`

返回指定区域的**外轮廓边**：只保留恰好属于一个区域三角面片的边（即区域边界）。

实现说明：渲染顶点**不跨单元共享**（相邻单元在共享边处有不同 vertex index，但相同 vtx_node_row），因此用节点行号而非顶点 index 识别边：相邻两个单元的公共边对应相同节点行号对，出现 2 次被排除；区域边界边出现 1 次被保留。四边形三角化对角线也被正确排除（两个子三角形的对角线节点行号相同，出现 2 次）。

查询参数同 `region-mesh-edges`。

响应格式与 `region-mesh-edges` 完全相同（L3BE `edge_positions [E*2, 3] float32`，Header `X-Edge-Count`）。

**两个接口对比：**

| 接口 | 效果 | 类比 |
|---|---|---|
| `region-mesh-edges` | 区域内所有单元的边框，包括单元之间的内部分界线 | Abaqus 的"显示单元边"模式 |
| `region-outline` | 仅区域外轮廓，内部单元边不显示 | Abaqus 的"显示外边界"模式 |

## 11. Query / Pick

### `GET /api/odb/{odb_id}/query/pick`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | instance name |
| `render_face_idx` | 是 | render triangle index |
| `pick_mode` | 否 | `element` / `node`，默认 `element` |
| `step` | 否 | 查询结果值时使用 |
| `field` | 否 | 查询结果值时使用 |
| `frame_idx` | 否 | 查询结果值时使用 |
| `component` | 否 | component name |
| `component_idx` | 否 | 显式 component index，优先于 component name |
| `node_idx` | 否 | node 模式下三角面候选点 0/1/2 |
| `include_coords` | 否 | node 模式是否返回 `orig_coords` / `def_coords` |
| `deform_scale` | 否 | 变形放大系数，默认 `1.0` |
| `result_group` | 否 | Project 模式结果组 |

响应核心字段：

- `pick_mode`
- `instance`
- `render_face_idx`
- `render_face_indices`
- `odb.elem_label`
- `odb.elem_type`
- `odb.elem_node_labels`
- `odb.node_label`
- `odb.candidate_node_labels`
- `odb.orig_coords`
- `odb.def_coords`
- `odb.attached_elem_labels`
- `result.field`
- `result.position`
- `result.raw_value`
- `result.raw_values`
- `result.display_value`
- `result.source_elem_label`
- `mises`

### `POST /api/odb/{odb_id}/query/ray-pick`

请求：

```json
{
  "instance": "PART-1-1",
  "screen_x": 100,
  "screen_y": 200,
  "viewport_width": 1920,
  "viewport_height": 1080,
  "view_projection_matrix": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  "pick_mode": "element",
  "node_idx": null,
  "step": "Step-1",
  "field": "S",
  "frame_idx": 0,
  "component_idx": 0,
  "include_coords": true,
  "deform_scale": 1.0,
  "result_group": "case1"
}
```

说明：

- 返回结构与 `GET /query/pick` 相同。
- `result_group` 是 body 字段，Project 模式下应传。
- node 模式下如果未传 `node_idx`，后端会根据屏幕点击位置从命中三角面的三个候选节点中选最近的节点。

### `POST /api/odb/{odb_id}/query/bbox`

请求：

```json
{
  "instance": "PART-1-1",
  "bbox_min": [0, 0, 0],
  "bbox_max": [10, 10, 10],
  "mode": "intersect",
  "set_name": "MyRegion"
}
```

响应字段：

- `set_name`
- `elem_count`
- `render_face_count`
- `elem_labels`，仅 `elem_count <= 2000` 时返回

### `POST /api/odb/{odb_id}/query/render-faces`

请求：

```json
{
  "instance": "PART-1-1",
  "render_face_indices": [1, 2, 3],
  "mode": "element"
}
```

element mode 响应字段：

- `elem_count`
- `elem_labels`
- `elem_face_indices`
- `elem_ids_per_face`

node mode 响应字段：

- `node_count`
- `node_labels`
- `node_positions`

### `POST /api/odb/{odb_id}/query/surface-patch`

请求：

```json
{
  "instance": "PART-1-1",
  "center": [0, 0, 0],
  "normal": [0, 0, 1],
  "width": 10.0,
  "height": 5.0,
  "up_hint": [0, 1, 0],
  "depth": 2.0
}
```

响应字段：

- `face_count`
- `elem_count`
- `node_count`
- `render_face_indices`
- `elem_labels`
- `node_labels`
- `node_positions`

### `GET /api/odb/{odb_id}/query/nearest-face`

查询参数：

| 参数 | 必填 |
|---|---|
| `instance` | 是 |
| `x` | 是 |
| `y` | 是 |
| `z` | 是 |

响应字段：

- `instance`
- `render_face_idx`
- `elem_label`
- `elem_type`
- `normal`
- `closest_point`
- `distance`

### `POST /api/odb/{odb_id}/query/node-displacements`

按节点编号批量查询位移结果（U1、U2、U3、USUM）。step 和 frame 可选，默认最后一个 step 的最后一帧。

请求 body（`application/json`）：

```json
{
  "nodes": ["PART-1-1::5", "PART-1-1::8", "PART-2-1::12"],
  "step": "Step-1",
  "frame": 5,
  "result_group": null
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `nodes` | 是 | 节点标识符列表，格式 `INSTANCE_NAME::NODE_LABEL` |
| `step` | 否 | Step 名称；省略时取最后一个 step |
| `frame` | 否 | 帧索引（0-based）；省略时取该 step 最后一帧 |
| `result_group` | 否 | project 模式下的 result_group |

响应 `data`：

```json
{
  "step": "Step-1",
  "frame": 5,
  "frame_value": 1.0,
  "results": [
    {"node_id": "PART-1-1::5", "instance": "PART-1-1", "label": 5,
     "U1": 0.0012, "U2": -0.0003, "U3": 0.0045, "USUM": 0.00472},
    {"node_id": "PART-1-1::8", "instance": "PART-1-1", "label": 8,
     "U1": null, "U2": null, "U3": null, "USUM": null,
     "error": "node label 8 not found"}
  ]
}
```

- 结果顺序与输入 `nodes` 顺序一致
- 找不到的节点返回 null 值并附 `error` 字段
- 同一 instance 的节点在一次 H5 IO 内批量读取，性能好

## 12. External Result Write

### `POST /api/odb/{odb_id}/results/external-field`

向已有 ODB/project workspace 写入外部结果字段。写入后会注册到 `manifest.db.result_files/result_blocks`，刷新 `meta/overview?result_group=...` 后可在 field 下拉中看到。

请求：

```json
{
  "step_name": "opt_step",
  "field_name": "RHO",
  "components": ["RHO"],
  "result_group": "run1",
  "type": "nodal",
  "instances": [
    {
      "instance": "PART-1-1",
      "frames": [
        {
          "frame_idx": 0,
          "frame_value": 0.0,
          "data": [
            {"label": 1, "values": [0.8]}
          ]
        }
      ]
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `result_group` | 必填，写入哪个结果组 |
| `type` | `nodal` 或 `element` |
| `instances[].frames[].data[].label` | node label 或 element label |
| `values` | 与 `components` 对齐 |

响应：

```json
{
  "code": 200,
  "data": {
    "field_name": "RHO",
    "step_name": "opt_step",
    "instances_written": 1,
    "frames_written": 1,
    "source": "external"
  },
  "message": ""
}
```

## 13. 试验模态网格（testMesh）

> 接口前缀：`/api/model/testMesh`。数据来源：MySQL `t_mt_py_test_*` 表（通过其他接口写入）。

### 接口总览

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/model/testMesh/geometry` | 节点坐标、单元索引、变形位置、云图数据 |
| POST | `/api/model/testMesh/modelSelect` | 模态阶次下拉列表 |
| POST | `/api/model/testMesh/animation` | 实部/虚部 flat 数组（供前端做谐波动画） |
| POST | `/api/model/testMesh/colormap` | 指定分量的云图数据 |

### 公共请求字段

所有接口均接受 JSON 请求体，`project_id`（int 或 str）为必填，兼容旧字段名 `project`。

### `POST /api/model/testMesh/geometry`

请求：

```json
{
  "project_id": 1,
  "order": 1,
  "max_scalar_size": 1.0,
  "coefficient": 1.0,
  "component": "usum",
  "animation": false,
  "flip": false
}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `order` | int | `0` | 模态阶次；0 = 未变形 |
| `max_scalar_size` | float | `1.0` | 变形幅度参考尺寸（后端自动覆盖为包围盒最大尺寸） |
| `coefficient` | float | `1.0` | 用户放大系数 |
| `component` | string | `"usum"` | `usum` \| `ux` \| `uy` \| `uz` |
| `animation` | bool | `false` | `true` 时响应中附带 `real`/`imag` 数组（供动画使用） |
| `flip` | bool | `false` | `true` 时对实部/虚部取反，翻转振型符号 |

响应 `data`：

```json
{
  "ids":           [1, 2, 3],
  "componentData": [0.0, 0.12, 0.08],
  "maxValue":      0.12,
  "minValue":      0.0,
  "scaleFactor":   45.3,
  "originPos":     [0,0,0, 1,0,0, ...],
  "newPos":        [0,0,0.1, 1,0,0.2, ...],
  "elementsIndex": [0,1, 1,2, ...],
  "real":          [],
  "imag":          []
}
```

| 字段 | 说明 |
|---|---|
| `ids` | 节点 ID 列表 |
| `componentData` | 每节点选定分量幅值（云图数据） |
| `scaleFactor` | 后端计算的变形放大系数 = `包围盒尺寸 / coefficient / max_amplitude` |
| `originPos` | 原始坐标 flat 数组（`[x0,y0,z0, x1,y1,z1, ...]`） |
| `newPos` | 变形后坐标 flat 数组（`originPos + real * scaleFactor`） |
| `elementsIndex` | 线单元连接索引 flat 数组（两节点一对） |
| `real` / `imag` | 仅 `animation=true` 时非空，振型实部/虚部 flat 数组 |

`order=0` 时返回原始未变形坐标，`componentData` 全零。

### `POST /api/model/testMesh/modelSelect`

请求：

```json
{ "project_id": 1 }
```

响应 `data`（下拉列表数组）：

```json
[
  { "label": "Undeformed",      "value": 0, "show_name": "" },
  { "label": "EMA 1 - 12.3 Hz", "value": 1, "show_name": "Mode 1" },
  { "label": "EMA 2 - 25.7 Hz", "value": 2, "show_name": "Mode 2" }
]
```

### `POST /api/model/testMesh/animation`

返回振型实部/虚部 flat 数组，前端自行做 `cos(ωt)·real + sin(ωt)·imag` 谐波动画。

请求：

```json
{
  "project_id": 1,
  "order": 1,
  "flip": false
}
```

响应 `data`：

```json
{
  "real": [0.0, 0.01, -0.02, ...],
  "imag": [0.0, 0.0,  0.0,  ...]
}
```

`order=0` 时返回全零数组。`flip=true` 时实部/虚部均取反。

### `POST /api/model/testMesh/colormap`

请求：

```json
{
  "project_id": 1,
  "order": 1,
  "component": "usum",
  "max_scalar_size": 1.0,
  "coefficient": 1.0,
  "flip": false
}
```

响应结构与 `/geometry` 完全相同，但不含 `newPos`（`animation` 固定为 `false`）。云图分量可单独指定，不影响变形方向。

---

> **`flip` 参数说明**：振型（特征向量）符号任意，乘以 −1 仍是有效振型。`flip=true` 对 `real`/`imag` 取反，使变形方向与 FEM 侧一致。符号是否需要翻转由上层计算逻辑决定后由前端传入，默认不传（`false`）时行为不变。

## 14. Simright Compatibility

### `POST /applications/3dlite/api/v1/query`

请求：

```json
{
  "name": "loadcases",
  "args": {
    "filename": "xxx.odb"
  }
}
```

支持的 `name` 包括：

- `loadcases`
- `variables`
- `assemble`
- `extremeValue`
- `nodeInfo`
- `elementInfo`
- `XYCurveData1`
- `freqValue`
- `hitEntities`
- `measureValue`
- `nodeId`
- `probeGroupByPos`
- `deleteModelFile`
- `nearestFace`

说明：

- 这是兼容层，不走 `/api/odb/{odb_id}` 风格。
- 目标模型通过 `args.filename` 解析。

## 15. L3BE 解析示例

```python
import struct
import numpy as np

DTYPE_MAP = {
    1: np.int8, 2: np.uint8, 3: np.int16, 4: np.uint16,
    5: np.int32, 6: np.uint32, 7: np.int64, 8: np.uint64,
    9: np.float32, 10: np.float64,
}

def decode_l3be(data: bytes) -> dict:
    magic, version, flags, hdr_size, n_sections, tbl_off, pay_off = \
        struct.unpack_from("<4sHHIIII", data, 0)
    assert magic == b"L3BE"

    result = {}
    for i in range(n_sections):
        entry_off = tbl_off + i * 80
        name_b, dtype_code, ndim, s0, s1, s2, s3, offset, nbytes, _ = \
            struct.unpack_from("<32sHH4IQQI", data, entry_off)
        name = name_b.rstrip(b"\x00").decode("ascii")
        shape = [s0, s1, s2, s3][:ndim]
        dtype = DTYPE_MAP[dtype_code]
        arr = np.frombuffer(
            data,
            dtype=dtype,
            count=nbytes // np.dtype(dtype).itemsize,
            offset=offset,
        ).reshape(shape).copy()
        result[name] = arr
    return result
```
