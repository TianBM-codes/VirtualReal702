# L3 API Quick Reference

更新时间：2026-04-23

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
- Results：`frame-colors`、`frame-scalars`、`deformed-positions`、`raw-values?format=l3be`、`section-mesh`
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

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/jobs` | 提交独立 ODB 作业 |
| GET | `/api/jobs` | 列出作业 |
| GET | `/api/jobs/{odb_id}` | 查询单个作业 |
| DELETE | `/api/jobs/{odb_id}?hard=false` | 删除作业，可选删除 workspace |
| POST | `/api/jobs/{odb_id}/retry` | 重试 error 状态作业 |

### 2.3 Projects

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/projects` | 列出 project 及 result groups |
| POST | `/api/projects` | 创建 project，提交 INP 几何解析 |
| POST | `/api/projects/{source_project_id}/clone` | 克隆 project workspace 和 registry 记录 |
| POST | `/api/projects/{project_id}/results` | 给 project 追加 ODB 结果组 |
| GET | `/api/projects/{project_id}` | 查询 project 详情 |
| GET | `/api/projects/{project_id}/summary` | 读取 `model_summary.json` |
| PATCH | `/api/projects/{project_id}/results/{result_group}` | 修改结果组显示名 |
| DELETE | `/api/projects/{project_id}` | 删除 project 和 workspace |

### 2.4 ODB Data

| 分组 | Method | Path |
|---|---|---|
| meta | GET | `/api/odb/{odb_id}/meta/overview` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/render-buffers` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/element-mesh-edges` |
| geometry | GET | `/api/odb/{odb_id}/geometry/{instance}/feature-edges` |
| geometry | POST | `/api/odb/{odb_id}/geometry/{instance}/render-buffers-subset` |
| results | GET | `/api/odb/{odb_id}/results/frame-colors` |
| results | GET | `/api/odb/{odb_id}/results/frame-scalars` |
| results | GET | `/api/odb/{odb_id}/results/deformed-positions` |
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
| query | GET | `/api/odb/{odb_id}/query/pick` |
| query | POST | `/api/odb/{odb_id}/query/ray-pick` |
| query | POST | `/api/odb/{odb_id}/query/bbox` |
| query | POST | `/api/odb/{odb_id}/query/render-faces` |
| query | POST | `/api/odb/{odb_id}/query/surface-patch` |
| query | GET | `/api/odb/{odb_id}/query/nearest-face` |
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

兼容字段：`name` 可作为 `display_name` 别名。

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
  "source_path": "/data/model.inp"
}
```

说明：

- `project_id` 由调用方提供。
- 服务端会校验 workspace id，避免路径穿越。
- 创建后 `geom_status=pending`，由 runner 解析 INP 并生成 L1/L2 几何。

响应：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-001",
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

请求：

```json
{
  "source_path": "/data/case1.odb",
  "result_group": "case1",
  "display_name": "工况一",
  "parse_options": {
    "consistency_check": "count-only"
  }
}
```

说明：

- `source_path` 是服务端可访问的 ODB 文件路径。
- `result_group` 是结果组 key，也用于结果文件目录。
- `display_name` 是展示名，可后续修改。
- 同名结果组如果处于 `ready/running` 会返回 409；如果处于 `error`，会重置为 `pending` 作为重试。

响应：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-001",
    "result_group": "case1",
    "status": "pending"
  },
  "message": ""
}
```

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
    "schemes": ["etype", "material", "section_type", "elset"],
    "elsets": ["SET_A", "SET_B"]
  },
  "message": ""
}
```

### `GET /api/odb/{odb_id}/color-code/{instance}`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `scheme` | 是 | `etype` / `material` / `section_type` / `elset` |
| `set_names` | 否 | 逗号分隔，仅 `scheme=elset` 使用 |

响应头：

```text
X-Face-Count: <Rf>
X-Color-Legend: [{"id":0,"name":"C3D8R","r":1.0,"g":0.0,"b":0.0}]
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `color_per_vertex` | `[Nv, 3]` 或 `[Rf*3, 3]` | `float32` |

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

## 13. Modal

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/modal/load` | 注册 modal JSON 文件 |
| GET | `/api/modal/{model_id}/geometry` | 几何 |
| GET | `/api/modal/{model_id}/modes` | 模态列表 |
| GET | `/api/modal/{model_id}/components` | 分量列表 |
| GET | `/api/modal/{model_id}/deformed` | 单阶变形 |
| GET | `/api/modal/{model_id}/animation` | 实部/虚部动画数据 |
| GET | `/api/modal/{model_id}/colormap` | 模态云图 |

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
