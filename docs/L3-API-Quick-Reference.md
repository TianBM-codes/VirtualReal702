# ODB 服务 L3 接口速查手册

> 本文以 `src/l3/api/routes/*` 当前实现为准，面向调用方，重点回答：
> 1. 现在到底有哪些接口
> 2. 参数怎么传
> 3. 返回 JSON 还是二进制
> 4. 哪些接口支持 `result_group`

服务地址示例：`http://<host>:18765`

---

## 1. 通用约定

### 1.1 标准 JSON 成功包

大多数 JSON 成功响应统一为：

```json
{
  "code": 200,
  "data": {},
  "message": ""
}
```

说明：
- 包体里的 `code` 目前固定表示业务成功，通常为 `200`
- 某些接口虽然 HTTP 状态码是 `201`，包体仍然是 `{"code": 200, ...}`
- 因此调用方如果既关心 REST 语义也关心业务包体，需要同时看 HTTP status 和 JSON body

### 1.2 当前实际存在的错误格式

1. `HTTPException` 直接抛出时，FastAPI 默认格式：

```json
{
  "detail": "Job 'xxx' not found"
}
```

2. `AppError` / 未捕获异常：

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

3. 少数路由会直接返回标准包体错误，而不是走上面两套框架异常：

```json
{
  "code": 404,
  "data": null,
  "message": "User field 'xxx' not found for instance 'PART-1-1'"
}
```

当前可确认的兼容旧成功包体接口主要是：
- `/api/odb/{odb_id}/results/external-field`

### 1.3 二进制响应

以下接口返回 `application/octet-stream`，载荷格式为 L3BE：

- 几何：`render-buffers`、`render-buffers-subset`、`element-mesh-edges`、`feature-edges`
- 结果：`frame-colors`、`frame-scalars`、`deformed-positions`、`raw-values?format=l3be`、`section-mesh`
- 表格：`results/node-table`
- 用户场：`user-field-colors`
- 颜色编码：`color-code/{instance}`

### 1.4 Project 模式

以下接口支持 `result_group`：

- `GET /api/odb/{odb_id}/meta/overview`
- `GET /api/odb/{odb_id}/results/frame-colors`
- `GET /api/odb/{odb_id}/results/frame-scalars`
- `GET /api/odb/{odb_id}/results/deformed-positions`
- `GET /api/odb/{odb_id}/results/raw-values`
- `GET /api/odb/{odb_id}/fields`
- `POST /api/odb/{odb_id}/results/node-table`
- `GET /api/odb/{odb_id}/query/pick`

不支持 `result_group` 的接口即使在 Project 模式下也只读几何或与结果组无关的数据。

---

## 2. 接口总览

### 2.1 健康检查

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/health/live` | 进程存活 |
| GET | `/api/health/ready` | 服务就绪 |

### 2.2 作业管理

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/jobs` | 提交 ODB 作业 |
| GET | `/api/jobs` | 列出所有作业 |
| GET | `/api/jobs/{odb_id}` | 查询单个作业 |
| DELETE | `/api/jobs/{odb_id}` | 删除作业，支持 `?hard=true` |
| POST | `/api/jobs/{odb_id}/retry` | 重试失败作业 |

### 2.3 Project 分组

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/projects` | 列出所有 project |
| POST | `/api/projects` | 创建 project（提交 INP） |
| POST | `/api/projects/{project_id}/results` | 追加结果组 |
| GET | `/api/projects/{project_id}` | 查询 project 详情 |
| PATCH | `/api/projects/{project_id}/results/{result_group}` | 修改结果组显示名 |
| DELETE | `/api/projects/{project_id}` | 删除 project |

### 2.4 ODB 主接口

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
| results | GET | `/api/odb/{odb_id}/fields` |
| results | POST | `/api/odb/{odb_id}/results/node-table` |
| user-field | POST | `/api/odb/{odb_id}/results/user-field` |
| user-field | GET | `/api/odb/{odb_id}/results/user-fields` |
| user-field | GET | `/api/odb/{odb_id}/results/user-field-colors` |
| user-field | DELETE | `/api/odb/{odb_id}/results/user-field` |
| color-code | GET | `/api/odb/{odb_id}/color-code/{instance}/schemes` |
| color-code | GET | `/api/odb/{odb_id}/color-code/{instance}` |
| query | GET | `/api/odb/{odb_id}/query/pick` |
| query | POST | `/api/odb/{odb_id}/query/ray-pick` |
| query | POST | `/api/odb/{odb_id}/query/bbox` |
| query | POST | `/api/odb/{odb_id}/query/render-faces` |
| query | POST | `/api/odb/{odb_id}/query/surface-patch` |
| query | GET | `/api/odb/{odb_id}/query/nearest-face` |
| external | POST | `/api/odb/{odb_id}/results/external-field` |

### 2.5 模态分析

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/modal/load` | 注册 JSON 文件 |
| GET | `/api/modal/{model_id}/geometry` | 几何 |
| GET | `/api/modal/{model_id}/modes` | 模态阶次列表 |
| GET | `/api/modal/{model_id}/components` | 分量列表 |
| GET | `/api/modal/{model_id}/deformed` | 变形振型 |
| GET | `/api/modal/{model_id}/animation` | 实部/虚部动画数据 |
| GET | `/api/modal/{model_id}/colormap` | 指定分量云图 |

### 2.6 Simright 兼容层

| Method | Path | 说明 |
|---|---|---|
| POST | `/applications/3dlite/api/v1/query` | 通用兼容入口 |

---

## 3. 健康检查

### `GET /api/health/live`
返回：

```json
{
  "code": 200,
  "data": {"status": "live"},
  "message": ""
}
```

### `GET /api/health/ready`
返回：

```json
{
  "code": 200,
  "data": {"status": "ready"},
  "message": ""
}
```

---

## 4. 作业管理 `/api/jobs`

### 4.1 `POST /api/jobs`

请求体：

```json
{
  "odb_path": "/data/raw/model.odb",
  "display_name": "车身模型-v3"
}
```

兼容旧字段：`name` 可作为 `display_name` 别名。

成功响应：

```json
{
  "code": 200,
  "data": {
    "id": "uuid",
    "odb_id": "uuid",
    "display_name": "车身模型-v3",
    "status": "submitted",
    "runner_alive": true,
    "warning": null
  },
  "message": ""
}
```

### 4.2 `GET /api/jobs`

返回作业数组；每项额外包含：

- `is_render_ready`
- `node_count`
- `instance_count`
- `created_at`
- `l1_started_at`
- `l1_done_at`
- `l2_started_at`
- `l2_done_at`
- `error_msg`

### 4.3 `GET /api/jobs/{odb_id}`

返回单个作业详情，结构同列表单项。

### 4.4 `DELETE /api/jobs/{odb_id}`

查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `hard` | `false` | `true` 时同时删除 workspace 目录 |

成功返回：

```json
{
  "code": 200,
  "data": {"hard": false},
  "message": ""
}
```

### 4.5 `POST /api/jobs/{odb_id}/retry`

只允许 `status == error` 的作业重试。

返回：

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

---

## 5. Project 分组 `/api/projects`

### 5.1 `GET /api/projects`

返回：

```json
{
  "code": 200,
  "data": [
    {
      "project_id": "proj-1",
      "geom_status": "ready",
      "result_groups": []
    }
  ],
  "message": ""
}
```

### 5.2 `POST /api/projects`

请求体：

```json
{
  "project_id": "proj-uuid-1234",
  "source_path": "/data/model.inp"
}
```

返回：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-uuid-1234",
    "geom_status": "pending"
  },
  "message": ""
}
```

### 5.3 `POST /api/projects/{project_id}/results`

请求体：

```json
{
  "source_path": "/data/case1.odb",
  "result_group": "case1",
  "display_name": "工况一",
  "parse_options": {
    "consistency_check": "full"
  }
}
```

返回：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-uuid-1234",
    "result_group": "case1",
    "status": "pending"
  },
  "message": ""
}
```

### 5.4 `GET /api/projects/{project_id}`

返回：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-uuid-1234",
    "geom_status": "ready",
    "result_groups": [
      {
        "result_group": "case1",
        "display_name": "工况一",
        "status": "ready",
        "consistency_check": "full",
        "error_message": null,
        "steps": ["Step-1"]
      }
    ]
  },
  "message": ""
}
```

### 5.5 `PATCH /api/projects/{project_id}/results/{result_group}`

请求体：

```json
{
  "display_name": "新名称"
}
```

### 5.6 `DELETE /api/projects/{project_id}`

返回：

```json
{
  "code": 200,
  "data": {
    "project_id": "proj-uuid-1234",
    "deleted": true
  },
  "message": ""
}
```

---

## 6. 元数据接口

### `GET /api/odb/{odb_id}/meta/overview`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `result_group` | 否 | Project 模式结果组 |

返回结构：

```json
{
  "code": 200,
  "data": {
    "instances": [
      {"instance_name": "PART-1-1", "...": "..."}
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
- `instances` / `steps` / `fields` 都来自 `manifest.db`
- `fields[*].source` 可能为 `odb` 或 `external`
- `components`、`positions` 当前通常是 JSON 字符串，调用方需要自行解析

---

## 7. 几何接口

### 7.1 `GET /api/odb/{odb_id}/geometry/{instance}/render-buffers`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `set` | 否 | user set 名；查不到时 fallback 到 element set |

响应头：

```text
X-Face-Count: <Nt>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `positions` | `[Nv, 3]` | `float32` |
| `indices` | `[Nt, 3]` | `int32` |

注意：当前**不返回 `normals`**，前端需要自己 `computeVertexNormals()`。

### 7.2 `GET /api/odb/{odb_id}/geometry/{instance}/element-mesh-edges`

响应头：

```text
X-Edge-Count: <E>
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `edge_positions` | `[E*2, 3]` | `float32` |

### 7.3 `GET /api/odb/{odb_id}/geometry/{instance}/feature-edges`

返回格式与 `element-mesh-edges` 相同，但只包含特征边。

### 7.4 `POST /api/odb/{odb_id}/geometry/{instance}/render-buffers-subset`

请求体：

```json
{
  "elem_labels": [1001, 1002, 1003]
}
```

返回与 `render-buffers` 相同；无匹配时返回空几何，`X-Face-Count: 0`。

---

## 8. 结果接口

### 8.1 `GET /api/odb/{odb_id}/results/frame-colors`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | 实例名 |
| `step` | 是 | 步骤名 |
| `field` | 是 | 结果场名 |
| `frame` | 否 | 默认 `0` |
| `component` | 否 | 仅允许 `U1` / `U2` / `U3` / `USUM` |
| `mode` | 否 | `smooth` / `flat`，默认 `smooth` |
| `result_group` | 否 | Project 模式 |
| `set` | 否 | user set / element set |

响应头：

```text
X-Payload-Type: frame_colors_v1
X-Layout-Version: 1
X-Val-Min: <float>
X-Val-Max: <float>
X-Component: USUM
X-Frame: 0
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `color_per_vertex` | `[Rf*3, 4]` | `uint8` |
| `legend_range` | `[2]` | `float32` |

### 8.2 `GET /api/odb/{odb_id}/results/frame-scalars`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | 实例名 |
| `step` | 是 | 步骤名 |
| `field` | 是 | 结果场名 |
| `frame` | 否 | 默认 `0` |
| `component_idx` | 否 | 0-based 分量索引；省略时取 magnitude |
| `mode` | 否 | `smooth` / `flat` |
| `result_group` | 否 | Project 模式 |
| `set` | 否 | user set / element set |
| `feature_angle` | 否 | 默认 `20.0` |
| `average_threshold` | 否 | 默认 `0.75` |
| `use_geometry_split` | 否 | 默认 `true` |

响应头：

```text
X-Payload-Type: frame_scalars_v1
X-Result-Position: NODAL | ELEMENT_NODAL_FLAT | INTEGRATION_POINT_FLAT
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
| `u_per_vertex` | `[Nv]` | `float32` |
| `legend_range` | `[2]` | `float32` |

### 8.3 `GET /api/odb/{odb_id}/results/deformed-positions`

按指定帧的 `U` 位移结果返回变形后的 indexed geometry 顶点坐标。该接口用于前端变形显示和动画播放。

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | 实例名 |
| `step` | 是 | 步骤名；服务端读取该 step 下的 `U` NODAL 位移 |
| `frame` | 否 | 默认 `0` |
| `scale` | 否 | 变形放大系数，默认 `1.0` |
| `result_group` | 否 | Project 模式 |

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
- `normals` 由后端基于变形后的 `positions` 和 `indices` 重新计算，前端动画时可直接使用，避免每帧 `computeVertexNormals()`
- 当前要求 indexed geometry，即 `ModelIndex` 中需要有 `vtx_node_row`

### 8.4 `GET /api/odb/{odb_id}/results/raw-values`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | 实例名 |
| `step` | 是 | 步骤名 |
| `field` | 是 | 场名 |
| `frame` | 否 | 默认 `0` |
| `position` | 是 | `NODAL` / `ELEMENT_NODAL` / `INTEGRATION_POINT` |
| `format` | 否 | `json` / `l3be`，默认 `json` |
| `result_group` | 否 | Project 模式 |

公共响应头：

```text
X-Position: <position>
X-Frame: <frame>
X-Components: ["S11","S22",...]
X-Etype-Groups: ["C3D8R","C3D4",...]
```

`format=json` 时，`data` 结构为：

- `position == NODAL`

```json
{
  "odb_id": "xxx",
  "instance": "PART-1-1",
  "step": "Step-1",
  "field": "U",
  "frame": 0,
  "position": "NODAL",
  "components": ["U1","U2","U3"],
  "node_labels": [1, 2, 3],
  "values": [[0.0, 0.1, 0.2]]
}
```

- `position != NODAL`

```json
{
  "etype_groups": [
    {
      "etype": "C3D8R",
      "elem_labels": [1001, 1002],
      "values": [[[1.0, 2.0]]]
    }
  ]
}
```

`format=l3be` 时，sections 为：

- `NODAL`

| 名称 | 形状 | 类型 |
|---|---|---|
| `node_labels` | `[N]` | `int32` |
| `values` | `[N, ncomp]` | `float32` |

- `ELEMENT_NODAL` / `INTEGRATION_POINT`

| 名称 | 形状 | 类型 |
|---|---|---|
| `el_{etype}` | `[M]` | `int32` |
| `v_{etype}` | `[M, ...]` | `float32` |

### 8.5 `GET /api/odb/{odb_id}/results/section-mesh`

查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `instance` | — | 实例名 |
| `axis` | `Z` | `X` / `Y` / `Z` |
| `position` | `0.0` | 切面位置 |

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

---

## 9. 节点表接口

### 9.1 `GET /api/odb/{odb_id}/fields`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | 实例名 |
| `step` | 是 | 步骤名 |
| `result_group` | 否 | Project 模式 |

返回：

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
        "components": ["U1","U2","U3"]
      }
    ]
  },
  "message": ""
}
```

### 9.2 `POST /api/odb/{odb_id}/results/node-table`

请求体：

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

---

## 10. 用户自定义场

### 10.1 `POST /api/odb/{odb_id}/results/user-field`

请求体：

```json
{
  "name": "fatigue_damage",
  "instance": "PART-1-1",
  "value": 0.85,
  "element_labels": [1001, 1002, 1003]
}
```

返回的 `data`：

```json
{
  "id": 1,
  "name": "fatigue_damage",
  "instance": "PART-1-1",
  "value": 0.85,
  "elem_count": 3
}
```

### 10.2 `GET /api/odb/{odb_id}/results/user-fields`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 否 | 仅查询某个实例 |

返回 `fields` 数组，元素来自 `manifest.db.user_fields`，包含：
- `id`
- `name`
- `instance_name`
- `value`
- `created_at`

### 10.3 `GET /api/odb/{odb_id}/results/user-field-colors`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 字段名 |
| `instance` | 是 | 实例名 |
| `val_min` | 否 | 归一化最小值 |
| `val_max` | 否 | 归一化最大值 |

响应头：

```text
X-Payload-Type: user_field_colors_v1
X-Layout-Version: 1
X-Val-Min: <float>
X-Val-Max: <float>
X-Field-Name: fatigue_damage
```

L3BE sections：

| 名称 | 形状 | 类型 |
|---|---|---|
| `color_per_vertex` | `[Rf*3, 4]` | `uint8` |
| `legend_range` | `[2]` | `float32` |

### 10.4 `DELETE /api/odb/{odb_id}/results/user-field`

查询参数：

| 参数 | 必填 |
|---|---|
| `name` | 是 |
| `instance` | 是 |

成功时：

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

未找到时返回：

```json
{
  "code": 404,
  "data": null,
  "message": "User field 'fatigue_damage' not found for instance 'PART-1-1'"
}
```

---

## 11. 颜色编码接口

### 11.1 `GET /api/odb/{odb_id}/color-code/{instance}/schemes`

返回：

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

### 11.2 `GET /api/odb/{odb_id}/color-code/{instance}`

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
| `color_per_vertex` | `[Nv, 3]` | `float32` |

说明：
- `Nv` 是 indexed geometry 的顶点数，与 `render-buffers` 的 `positions` 对齐。
- `X-Face-Count` 仍表示源三角面数 `Rf`，用于统计面片数量，不表示 `color_per_vertex` 的行数。
- 旧 Triangle Soup 数据缺少 indexed vertex 映射时，服务会 fallback 为 `[Rf*3, 3]`。

---

## 12. 查询接口

### 12.1 `GET /api/odb/{odb_id}/query/pick`

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | 实例名 |
| `render_face_idx` | 是 | 三角面索引 |
| `pick_mode` | 否 | `element` / `node`，默认 `element` |
| `step` | 否 | 查询结果值时使用 |
| `field` | 否 | 查询结果值时使用 |
| `frame_idx` | 否 | 查询结果值时使用 |
| `component` | 否 | 分量名 |
| `component_idx` | 否 | 显式分量索引 |
| `node_idx` | 否 | node 模式下 0/1/2 |
| `include_coords` | 否 | node 模式时是否返回坐标 |
| `deform_scale` | 否 | 变形放大系数 |
| `result_group` | 否 | Project 模式 |

成功时 `data` 为 `PickResponse`，核心字段：

- `pick_mode`
- `instance`
- `render_face_idx`
- `render_face_indices`
- `odb.elem_label` / `odb.elem_type` / `odb.elem_node_labels`
- `odb.node_label` / `odb.candidate_node_labels` / `odb.orig_coords` / `odb.def_coords`
- `result.field` / `result.position` / `result.display_value`
- `mises`

### 12.2 `POST /api/odb/{odb_id}/query/ray-pick`

请求体：

```json
{
  "instance": "PART-1-1",
  "screen_x": 100,
  "screen_y": 200,
  "viewport_width": 1920,
  "viewport_height": 1080,
  "view_projection_matrix": [16 floats],
  "pick_mode": "element"
}
```

说明：
- 返回结构与 `GET /query/pick` 相同
- 当前**不支持 `result_group`**

### 12.3 `POST /api/odb/{odb_id}/query/bbox`

请求体：

```json
{
  "instance": "PART-1-1",
  "bbox_min": [0, 0, 0],
  "bbox_max": [10, 10, 10],
  "mode": "intersect",
  "set_name": "MyRegion"
}
```

返回字段：

- `set_name`
- `elem_count`
- `render_face_count`
- `elem_labels`（仅 `elem_count <= 2000`）

### 12.4 `POST /api/odb/{odb_id}/query/render-faces`

请求体：

```json
{
  "instance": "PART-1-1",
  "render_face_indices": [1, 2, 3],
  "mode": "element"
}
```

返回：

- element 模式：
  - `elem_count`
  - `elem_labels`
  - `elem_face_indices`
  - `elem_ids_per_face`
- node 模式：
  - `node_count`
  - `node_labels`
  - `node_positions`

### 12.5 `POST /api/odb/{odb_id}/query/surface-patch`

请求体：

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

返回字段：

- `face_count`
- `elem_count`
- `node_count`
- `render_face_indices`
- `elem_labels`
- `node_labels`
- `node_positions`

### 12.6 `GET /api/odb/{odb_id}/query/nearest-face`

查询参数：

| 参数 | 必填 |
|---|---|
| `instance` | 是 |
| `x` | 是 |
| `y` | 是 |
| `z` | 是 |

返回字段：

- `instance`
- `render_face_idx`
- `elem_label`
- `elem_type`
- `normal`
- `closest_point`
- `distance`

---

## 13. 外部结果写入

### `POST /api/odb/{odb_id}/results/external-field`

当前实现已经支持**一次请求写多个 instance**。

请求体：

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

返回格式目前仍是**专用旧格式**，没有统一到 `message` 字段：

```json
{
  "code": 0,
  "data": {
    "field_name": "RHO",
    "step_name": "opt_step",
    "instances_written": 1,
    "frames_written": 1,
    "source": "external"
  }
}
```

---

## 14. 模态分析 `/api/modal`

### 14.1 `POST /api/modal/load`

请求体：

```json
{"path": "/data/modal/bridge_modal.json"}
```

返回：

```json
{
  "code": 200,
  "data": {"model_id": "bridge_modal"},
  "message": ""
}
```

### 14.2 `GET /api/modal/{model_id}/geometry`

`data` 结构：

```json
{
  "originPos": [x0, y0, z0, ...],
  "index": [0, 1, 2, ...]
}
```

### 14.3 `GET /api/modal/{model_id}/modes`

`data` 为：

```json
[
  {"label": "EMA 1 - 12.3 Hz", "value": 1}
]
```

### 14.4 `GET /api/modal/{model_id}/components`

`data` 为固定组件列表，如：

```json
["U-Modulus:usum", "DOF UX", "DOF UY", "DOF UZ"]
```

### 14.5 `GET /api/modal/{model_id}/deformed`

查询参数：

| 参数 | 必填 |
|---|---|
| `order` | 是 |
| `max_scalar_size` | 否 |
| `coefficient` | 否 |

`data` 字段：

- `componentData`
- `maxValue`
- `minValue`
- `scaleFactor`
- `newPos`

### 14.6 `GET /api/modal/{model_id}/animation`

查询参数：`order`

返回：

```json
{
  "real": [dx0, dy0, dz0, ...],
  "imag": [dx0, dy0, dz0, ...]
}
```

### 14.7 `GET /api/modal/{model_id}/colormap`

查询参数：

| 参数 | 必填 |
|---|---|
| `order` | 是 |
| `component` | 否，默认 `usum` |
| `max_scalar_size` | 否 |
| `coefficient` | 否 |

返回结构与 `/deformed` 相同。

---

## 15. Simright 兼容层

### `POST /applications/3dlite/api/v1/query`

请求体：

```json
{
  "name": "loadcases",
  "args": {
    "filename": "xxx.odb"
  }
}
```

返回格式：

```json
{"code": 200, "data": {}, "message": ""}
{"code": 400, "data": null, "message": "bad request"}
{"code": 500, "data": null, "message": "internal error"}
```

说明：
- 虽然它是兼容层接口，但当前包体已经和主接口的 `code/data/message` 风格对齐
- 它不走 `/api/odb/{odb_id}` 路由，而是通过 `args.filename` 解析目标模型

当前支持的 `name`：

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
- 这里的模型定位依赖 `args.filename`
- 它不是 `/api/odb/{odb_id}` 风格接口

---

## 16. L3BE 速记

L3BE 是 L3 Binary Envelope，自描述二进制格式。当前常见 dtype：

| code | dtype |
|---|---|
| `2` | `uint8` |
| `5` | `int32` |
| `9` | `float32` |
| `10` | `float64` |

### Python 解码示例

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
        name = name_b.rstrip(b"\\x00").decode("ascii")
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

常见 section 名回顾：

- 几何：`positions`、`indices`
- 边线：`edge_positions`
- 云图：`color_per_vertex`、`legend_range`
- 标量：`u_per_vertex`
- 变形：`positions`、`normals`
- 原始值：`node_labels`、`values`、`el_{etype}`、`v_{etype}`
- 剖切：`vertices`、`edge_verts`
- 节点表：`node_labels`、`values`
