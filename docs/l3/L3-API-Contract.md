# L3 API Contract

## 目标

本文档定义 Layer 3 第一版对外接口契约。

重点是先明确：

- 有哪些接口
- 每个接口接收什么参数
- 每个接口返回什么结构
- 哪些接口返回 JSON
- 哪些接口返回二进制流
- 未就绪、找不到、参数错误时怎么返回

本文档只定义协议，不定义具体实现。

---

## 1. 总体约定

### 1.1 路由前缀

建议统一前缀：

```text
/api
```

其中 ODB 相关接口统一挂在：

```text
/api/odb/{odb_id}/...
```

### 1.2 响应分类

L3 响应分两类：

- JSON：元信息、查询结果、错误信息
- Binary：几何、结果 buffer、变形 buffer

### 1.3 JSON 成功格式

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 1.4 JSON 错误格式

```json
{
  "ok": false,
  "error": {
    "code": "NOT_READY",
    "message": "ODB render data is not ready",
    "details": {}
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 1.5 二进制响应头

二进制流统一使用：

- `Content-Type: application/octet-stream`

建议统一附带这些响应头：

- `X-Request-Id`
- `X-ODB-Id`
- `X-Instance-Name`
- `X-Payload-Type`
- `X-Array-Dtype`
- `X-Array-Shape`
- `X-Layout-Version`

其中：

- `X-Payload-Type` 用于标识当前 buffer 的语义
- `X-Array-Shape` 使用逗号分隔，例如 `1024,3,3`

### 1.6 通用状态码

- `200 OK`
- `202 Accepted`
- `400 Bad Request`
- `401 Unauthorized`
- `403 Forbidden`
- `404 Not Found`
- `409 Conflict`
- `500 Internal Server Error`

### 1.7 未就绪约定

当 ODB 已存在但 L2 或 L3 运行时索引未 ready 时：

- 返回 `202 Accepted`
- `error.code = NOT_READY`
- 可附带 `Retry-After`

### 1.8 认证约定

当前阶段先预留 token 认证，不绑定具体 Java 认证模式。

建议默认支持：

- `Authorization: Bearer <token>`

第一版认证模式建议做成可配置：

- `disabled`
- `static_token`
- `bearer_passthrough`

说明：

- `disabled`
  不校验认证，适合本地调试

- `static_token`
  L3 自己校验固定 token，适合联调前期或内网环境

- `bearer_passthrough`
  先只要求请求带 Bearer token，具体 token 含义由上游 Java 系统控制

这意味着：

- 现在先把接口和中间件口子预留出来
- 等 Java 认证模式明确后再补真实联调逻辑

### 1.9 未认证错误格式

状态码：

- `401 Unauthorized`

示例：

```json
{
  "ok": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Missing or invalid authorization token",
    "details": {}
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 2. 健康检查接口

### 2.1 `GET /health/live`

用途：

- 判断服务进程是否存活

响应：

```json
{
  "ok": true,
  "data": {
    "status": "live"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 2.2 `GET /health/ready`

用途：

- 判断服务是否可接请求

响应：

```json
{
  "ok": true,
  "data": {
    "status": "ready"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 3. Jobs 接口

### 3.1 `GET /api/jobs/{odb_id}`

用途：

- 查询某个 ODB 的处理状态

返回字段建议：

- `odb_id`
- `status`
- `is_l1_ready`
- `is_l2_ready`
- `is_render_ready`
- `message`

示例响应：

```json
{
  "ok": true,
  "data": {
    "odb_id": "demo_001",
    "status": "ready",
    "is_l1_ready": true,
    "is_l2_ready": true,
    "is_render_ready": true,
    "message": "ready"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 4. Meta 接口

### 4.1 `GET /api/odb/{odb_id}/meta/overview`

用途：

- 返回前端初始化需要的总览信息

返回字段建议：

- `instances`
- `steps`
- `fields`
- `default_step`
- `default_frame_idx`
- `default_field`

### 4.2 `GET /api/odb/{odb_id}/meta/instances`

用途：

- 返回实例列表及基础信息

每个实例建议包含：

- `instance_name`
- `part_name`
- `node_count`
- `elem_count`
- `bbox_min`
- `bbox_max`
- `is_render_ready`

### 4.3 `GET /api/odb/{odb_id}/meta/steps`

用途：

- 返回 step 和 frame 结构

每个 step 建议包含：

- `step_name`
- `procedure`
- `num_frames`
- `frames`

每个 frame 建议包含：

- `frame_idx`
- `frame_value`
- `description`

### 4.4 `GET /api/odb/{odb_id}/meta/fields`

用途：

- 返回可用结果场

每项建议包含：

- `field_name`
- `components`
- `invariants`
- `positions`
- `has_section`

### 4.5 `GET /api/odb/{odb_id}/meta/sets`

用途：

- 返回可用集合列表

每项建议包含：

- `set_name`
- `set_scope`
- `instance_name`
- `set_type`

---

## 5. Mesh 接口

### 5.1 `GET /api/odb/{odb_id}/mesh/chunk`

用途：

- 返回某个 instance 的基础显示壳子 chunk

查询参数建议：

- `instance` 必填
- `chunk` 选填
- `partition` 选填
- `set` 选填

约束建议：

- `chunk` 和 `partition` 至少给一个
- `chunk` 与 `partition` 不同时使用

成功响应：

- Binary

`X-Payload-Type` 建议为：

```text
mesh_chunk_v1
```

二进制 payload 语义建议包含：

- `positions [R, 3, 3] float32`
- `normals [R, 3, 3] float32`
- `render_face_idx [R] int32`
- `source_elem_row [R] int32`
- 可选 `source_elem_type [R] string/code`
- 可选 `source_node_rows [R, 3] int32`

### 5.2 `GET /api/odb/{odb_id}/mesh/edges`

用途：

- 返回 feature edges

查询参数建议：

- `instance` 必填
- `partition` 选填

返回：

- Binary 或 JSON metadata + Binary

---

## 6. Render 接口

### 6.1 `GET /api/odb/{odb_id}/render/state`

用途：

- 按 step/frame/field/component 组装最终渲染状态

查询参数建议：

- `instance` 必填
- `chunk` 选填
- `partition` 选填
- `step` 必填
- `frame_idx` 必填
- `field` 必填
- `component` 选填
- `mode` 必填
- `set` 选填
- `focus` 选填
- `deform_field` 选填
- `deform_scale` 选填，默认 `0.0`

其中 `mode` 建议允许：

- `smooth`
- `flat`
- `attribute`

成功响应：

- Binary

`X-Payload-Type` 建议为：

```text
render_state_v1
```

payload 可以包含以下几类字段中的若干个：

- `render_face_idx [R] int32`
- `scalar_per_vertex [R, 3] float32`
- `scalar_per_face [R] float32`
- `color_per_vertex [R, 3, 4] uint8`
- `color_per_face [R, 4] uint8`
- `positions_deformed [R, 3, 3] float32`
- `focus_mask [R] uint8`
- `value_min float32`
- `value_max float32`

说明：

- 第一版不要求同时返回所有字段
- 可按模式返回不同最小必要集

### 6.2 `GET /api/odb/{odb_id}/render/displacement`

用途：

- 单独返回位移场驱动的变形坐标或位移值

查询参数建议：

- `instance` 必填
- `step` 必填
- `frame_idx` 必填
- `scale` 选填
- `chunk` 或 `partition` 选填

返回建议：

- 若面向直接显示：返回 `positions_deformed`
- 若面向进一步计算：返回 `displacement_per_node`

第一版建议偏向前者，减少前端计算负担。

### 6.3 `GET /api/odb/{odb_id}/render/legend`

用途：

- 返回当前结果场对应的图例信息

返回字段建议：

- `field`
- `component`
- `value_min`
- `value_max`
- `ticks`
- `palette`

---

## 7. Query 接口

### 7.1 `GET /api/odb/{odb_id}/query/pick`

用途：

- 根据 `render_face_idx` 回查工程语义（单元号、节点号）和当前结果值

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instance` | string | 是 | 实例名 |
| `render_face_idx` | int | 是 | 三角面片索引（Triangle Soup 行号） |
| `pick_mode` | "element"\|"node" | 否 | 默认 `element` |
| `node_idx` | 0\|1\|2 | 否 | node 模式下：哪个顶点（前端屏幕空间最近点） |
| `step` | string | 否 | 步骤名，结合 field/frame 查询结果值 |
| `frame_idx` | int | 否 | 帧索引 |
| `field` | string | 否 | 字段名，如 `U`、`S` |
| `component` | string | 否 | 分量名，如 `U1`、`USUM` |
| `component_idx` | int | 否 | 显式列索引，优先于 component 名（用于 S11→0 等非位移场） |

成功响应（直接 JSON，无 `ok`/`data` 包装）：

```json
{
  "pick_mode": "element",
  "instance": "PART-1-1",
  "render_face_idx": 1234,
  "render_face_indices": [1234, 1235, 1236],
  "odb": {
    "elem_label": 5678,
    "elem_node_labels": [101, 102, 103, 104, 105, 106, 107, 108],
    "node_label": null,
    "candidate_node_labels": null
  },
  "result": {
    "field": "U",
    "position": "NODAL",
    "component": "USUM",
    "value_kind": "odb_raw",
    "raw_values": [0.12, 0.13, 0.11],
    "display_value": 0.12
  }
}
```

node 模式时 `odb` 中 `node_label` 有值，`elem_node_labels` 为空；
`render_face_indices` 为该单元所有三角面片索引，用于前端绘制高亮线框。

### 7.2 `GET /api/odb/{odb_id}/query/hover`

用途：

- 返回 hover tooltip 所需最小信息（暂未实现）

查询参数建议：

- `instance` 必填
- `render_face_idx` 必填
- `step` 选填
- `frame_idx` 选填
- `field` 选填

返回字段建议：

- `instance`
- `elem_label`
- `current_value`
- `unit`

### 7.3 `POST /api/odb/{odb_id}/query/bbox`

用途：

- 根据世界坐标 AABB 返回命中单元集合（服务端空间查询，带 Octree 加速）
- **注意**：前端框选建议使用 `query/render-faces`（屏幕空间，更准确），bbox 接口保留用于服务端 set 持久化场景

请求体：

```json
{
  "instance": "PART-1-1",
  "bbox_min": [0.0, 0.0, 0.0],
  "bbox_max": [1.0, 1.0, 1.0],
  "mode": "intersect",
  "set_name": "MySet"
}
```

`mode`：`intersect`（有顶点在盒内即命中）或 `contained`（全部顶点在盒内）。
`set_name` 可选，填写则将结果持久化到 `manifest.db` 的 `user_sets` 表。

成功响应：

```json
{
  "set_name": "MySet",
  "elem_count": 42,
  "render_face_count": 156,
  "elem_labels": [1, 3, 7, 12]
}
```

`elem_labels` 仅在 `elem_count ≤ 2000` 时返回，否则为 `null`。

### 7.4 `POST /api/odb/{odb_id}/query/render-faces` *(已实现)*

用途：

- 前端框选的核心接口。接收前端屏幕空间检测得到的 render face 索引列表，
  按模式展开为完整单元（不出现"半个单元被选中"）或唯一节点，返回标签和几何信息。

请求体：

```json
{
  "instance": "PART-1-1",
  "render_face_indices": [0, 1, 5, 23, 100],
  "mode": "element"
}
```

`mode`：`element` 或 `node`。

**element 模式响应**：

```json
{
  "mode": "element",
  "elem_count": 3,
  "elem_labels": [1, 5, 12],
  "elem_face_indices": [0, 1, 2, 5, 6, 23, 24, 100, 101],
  "node_count": 0,
  "node_labels": null,
  "node_positions": null
}
```

- `elem_face_indices`：所选单元的**所有**三角面片索引（展开后），用于前端绘制完整线框
- `elem_labels`：仅在 `elem_count ≤ 2000` 时填充

**node 模式响应**：

```json
{
  "mode": "node",
  "elem_count": 0,
  "elem_labels": null,
  "elem_face_indices": null,
  "node_count": 27,
  "node_labels": [101, 102, 103],
  "node_positions": [[1.0, 0.0, 0.5], [1.2, 0.1, 0.6]]
}
```

- `node_positions`：唯一节点的三维坐标，仅在 `node_count ≤ 5000` 时填充，用于前端绘制点云
- `node_labels`：仅在 `node_count ≤ 2000` 时填充

**与 `/query/bbox` 的区别**：

| | `/query/bbox` | `/query/render-faces` |
|---|---|---|
| 空间检测 | 服务端 AABB + Octree | 前端屏幕空间投影 |
| 精度 | 世界坐标盒，透视相机下不准确 | 像素级准确，穿透深度 |
| 适用场景 | 服务端 set 持久化 | 前端框选高亮 |

### 7.5 `POST /api/odb/{odb_id}/query/set/resolve`

用途：

- 将集合名解析成 render rows / elem rows（暂未实现）

请求体建议：

```json
{
  "instance": "Part-1-1",
  "set_name": "SET_A"
}
```

响应字段建议：

- `instance`
- `set_name`
- `elem_count`
- `render_face_count`

---

## 8. 二进制 payload 设计建议

### 8.1 第一版原则

第一版不要求引入复杂自定义二进制协议。

建议原则：

- 一次请求只返回一种主 payload
- shape 和 dtype 放响应头
- 布局版本放 `X-Layout-Version`

### 8.2 可选方案

有两种可行方式：

#### 方案 A：单数组 payload

适合：

- 只返回一类数组

例如：

- 只返回 `positions_deformed`
- 只返回 `scalar_per_face`

优点：

- 最简单

缺点：

- 组合结果时不灵活

#### 方案 B：多段拼接 payload

适合：

- 一次返回多块数组

例如：

- positions
- normals
- render_face_idx

这时需要额外有一个 metadata 描述各段 offset。

第一版建议：

- `mesh/chunk` 可采用“多段拼接 + metadata header”
- `render/state` 可先按模式拆成更小 payload

### 8.3 推荐使用统一 envelope

为了让前端更稳定地解包，建议二进制响应统一采用：

- 一个固定长度 header
- 后面跟多个连续数组段

可称为：

```text
Binary Envelope v1
```

### 8.4 Binary Envelope v1 结构

建议布局：

```text
[Fixed Header][Section Table][Raw Buffer Sections...]
```

#### Fixed Header

建议字段：

- `magic`：4 bytes，例如 `L3BF`
- `version`：uint16
- `section_count`：uint16
- `reserved`：若干保留字节

#### Section Table

每个 section 记录一段数组的元信息：

- `name`
- `dtype`
- `ndim`
- `shape`
- `offset`
- `nbytes`

这样前端拿到 buffer 后，可以按 section name 解出：

- `positions`
- `normals`
- `render_face_idx`
- `scalar_per_face`
- `positions_deformed`

### 8.5 为什么推荐 envelope

因为仅靠响应头描述多段数组会越来越乱。

envelope 的好处是：

- 一个响应可以带多个数组
- 前端解包逻辑统一
- 后续增加 section 不容易把协议弄碎
- 版本升级更容易

---

## 9. `mesh/chunk` payload 细化

### 9.1 `mesh/chunk` 的目标

它返回的是：

- 基础显示壳子
- 不带当前 frame 的结果态

所以它应该尽量稳定、可缓存。

### 9.2 `mesh/chunk` 推荐 section

建议 section 名如下：

- `positions`
- `normals`
- `render_face_idx`
- `source_elem_row`
- `source_node_rows`
- 可选 `source_elem_type`

### 9.3 `mesh/chunk` 推荐 dtype 和形状

- `positions`
  - dtype: `float32`
  - shape: `[R, 3, 3]`

- `normals`
  - dtype: `float32`
  - shape: `[R, 3, 3]`

- `render_face_idx`
  - dtype: `int32`
  - shape: `[R]`

- `source_elem_row`
  - dtype: `int32`
  - shape: `[R]`

- `source_node_rows`
  - dtype: `int32`
  - shape: `[R, 3]`

- `source_elem_type`
  - 建议第一版可选
  - 若支持混合单元类型，推荐补上
  - dtype 可用 `int16` 或 `int32 code`
  - shape: `[R]`

### 9.4 `mesh/chunk` 缓存建议

由于基础壳子相对稳定，建议：

- 前端长缓存
- 参数不变时尽量不重复拉

对同一：

- `odb_id`
- `instance`
- `chunk` 或 `partition`

返回内容应尽量稳定。

---

## 10. `render/state` payload 细化

### 10.1 `render/state` 的目标

它返回的是：

- 当前 step/frame/field 下的渲染态

与 `mesh/chunk` 不同，它更容易随交互变化。

### 10.2 第一版建议支持三类返回

#### 类型 A：平滑云图

建议 section：

- `render_face_idx`
- `scalar_per_vertex`
- 可选 `positions_deformed`
- 可选 `focus_mask`
- 可选 `legend_range`

推荐形状：

- `scalar_per_vertex`
  - dtype: `float32`
  - shape: `[R, 3]`

- `positions_deformed`
  - dtype: `float32`
  - shape: `[R, 3, 3]`

- `focus_mask`
  - dtype: `uint8`
  - shape: `[R]`

#### 类型 B：单元均色

建议 section：

- `render_face_idx`
- `scalar_per_face`
- 可选 `positions_deformed`
- 可选 `focus_mask`
- 可选 `legend_range`

推荐形状：

- `scalar_per_face`
  - dtype: `float32`
  - shape: `[R]`

#### 类型 C：属性着色

建议 section：

- `render_face_idx`
- `attribute_id_per_face`
- 可选 `positions_deformed`
- 可选 `focus_mask`

推荐形状：

- `attribute_id_per_face`
  - dtype: `int32`
  - shape: `[R]`

### 10.3 关于颜色和标量的取舍

第一版建议优先返回：

- `scalar`

而不是直接返回：

- `color`

原因：

- 传输更省
- 前端可以本地切 colormap
- Java/前端联调时更透明

只有在前端性能明显不够时，再考虑后端直接返回 `color_per_face` 或 `color_per_vertex`。

### 10.4 `legend_range` 建议

建议作为一个小 section 或 JSON metadata 附带：

- `value_min`
- `value_max`

第一版若放在二进制 envelope 中，可定义为：

- dtype: `float32`
- shape: `[2]`

---

## 11. 配置和部署约定补充

### 11.1 配置来源

建议接口服务遵循以下配置来源优先级：

1. 环境变量
2. 外部 JSON 配置文件
3. 代码默认值

### 11.2 推荐环境变量

建议至少支持：

- `APP_ENV`
- `LOG_LEVEL`
- `DATA_ROOT`
- `APP_CONFIG_PATH`
- `AUTH_MODE`
- `AUTH_STATIC_TOKEN`
- `DEFAULT_CHUNK_SIZE`
- `GUNICORN_WORKERS`

### 11.3 推荐 JSON 配置文件

可选文件路径：

- `/app/config/appsettings.json`

示例结构：

```json
{
  "app": {
    "name": "odb-l3-service",
    "env": "prod"
  },
  "logging": {
    "level": "INFO"
  },
  "storage": {
    "data_root": "/data"
  },
  "api": {
    "default_chunk_size": 50000
  },
  "security": {
    "auth_mode": "bearer_passthrough"
  },
  "runtime": {
    "gunicorn_workers": 4
  }
}
```

### 11.4 为什么不是只用 JSON

因为 Docker 和运维体系里：

- 环境变量最直接
- 覆盖最方便

所以 JSON 适合作为默认配置载体，但线上最终以环境变量覆盖更稳。

---

## 12. 状态错误示例

---

## 9. 状态错误示例

### 9.1 ODB 不存在

状态码：

- `404`

错误体：

```json
{
  "ok": false,
  "error": {
    "code": "ODB_NOT_FOUND",
    "message": "ODB not found",
    "details": {
      "odb_id": "demo_001"
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 9.2 Render 未就绪

状态码：

- `202`

错误体：

```json
{
  "ok": false,
  "error": {
    "code": "NOT_READY",
    "message": "Render data is not ready",
    "details": {
      "odb_id": "demo_001",
      "status": "l2_running"
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 9.3 参数错误

状态码：

- `400`

典型情况：

- `chunk` 和 `partition` 同时给
- `mode=flat` 但没给合法 field
- `frame_idx` 越界

---

## 13. 第一阶段最值得先冻结的接口

虽然接口很多，但第一阶段建议优先冻结这些：

- `GET /health/live`
- `GET /health/ready`
- `GET /api/jobs/{odb_id}`
- `GET /api/odb/{odb_id}/meta/overview`
- `GET /api/odb/{odb_id}/mesh/chunk`
- `GET /api/odb/{odb_id}/render/state`
- `GET /api/odb/{odb_id}/query/pick`

原因：

- 这几个已经覆盖前端初始化、基本显示、结果切换、点击查询的主链路

---

## 14. 结果云图接口：frame-colors + pick 配合

### 14.1 设计思路

前端显示云图需要两种数据：

- **颜色**：每个顶点显示什么颜色（大量数据，后端算好发给前端）
- **数值**：用户点击某个面时显示精确数值（单次查询，走 pick 接口）

这两个接口职责不重叠，配合使用。

---

### 14.2 frame-colors 接口

```
GET /api/odb/{odb_id}/results/frame-colors
  ?instance=PART-1-1
  &step=Step-1
  &frame=0
  &field=U
  &component=USUM          # 可选 U1 / U2 / U3 / USUM
```

**后端处理流程**：

```
1. 读 L1 HDF5：/NODAL/<instance>/data[frame_idx]
   → [N, 3] float32，每节点 [Ux, Uy, Uz]

2. 按 component 计算标量：
   USUM = norm(axis=1)     → [N]
   U1   = data[:, 0]       → [N]

3. 用 source_node_rows [Rf, 3] 展开到 Triangle Soup 顶点：
   scalar_vertex = scalar_node[source_node_rows.ravel()]  → [Rf*3]

4. 归一化：
   normalized = (v - v_min) / (v_max - v_min)            → [Rf*3] float

5. 查 jet colormap LUT → [Rf*3, 4] uint8 RGBA

6. 打包 L3BE binary 返回
```

**响应**：`application/octet-stream`，L3BE 格式，包含两个 section：

| section | dtype | shape | 含义 |
|---|---|---|---|
| `color_per_vertex` | uint8 | [Rf*3, 4] | RGBA 颜色，对齐 Triangle Soup 顶点 |
| `legend_range` | float32 | [2] | [val_min, val_max]，用于显示图例 |

响应 Header 额外带：
- `X-Val-Min` / `X-Val-Max`：图例范围
- `X-Component`：当前分量名
- `X-Frame`：帧号

**前端使用**：
```javascript
// 解包 L3BE → 拿到 color_per_vertex
geometry.setAttribute('color', new THREE.BufferAttribute(colors, 4, true));
material.vertexColors = true;
// 图例用 X-Val-Min / X-Val-Max 显示
```

---

### 14.3 pick 接口（单面精确查询）

```
GET /api/odb/{odb_id}/query/pick
  ?instance=PART-1-1
  &render_face_idx=1234
  &step=Step-1
  &field=U
  &frame_idx=0
```

返回 JSON：
```json
{
  "ok": true,
  "data": {
    "instance": "PART-1-1",
    "render_face_idx": 1234,
    "elem_label": 5678,
    "node_labels": [101, 102, 103],
    "current_value": 0.1523
  }
}
```

用户点击某个面时调这个接口，显示在信息面板里。精确 float 值，不走颜色路径。

---

### 14.4 两个接口的分工

```
加载一帧云图：
  GET frame-colors → 后端算好颜色 → 前端直接渲染，零计算

用户点击某个面：
  GET pick → 精确 float 值 → 显示在 UI 面板
```

---

## 15. 属性上色接口：color-code

### 15.1 设计思路

Abaqus CAE 里有 "Color Code" 功能——按材料、截面类型、单元类型、或 Elset 成员身份给模型染色，直观区分不同区域。本接口在后端完成颜色计算，返回 per-vertex RGB 数组直接写入 Three.js 的 `colorAttribute`，前端零计算。

---

### 15.2 查询可用方案

```
GET /api/odb/{odb_id}/color-code/{instance}/schemes
```

**响应 JSON**：

```json
{
  "schemes": ["etype", "material", "section_type", "elset"],
  "elsets": ["Set-1", "Set-2", ...]
}
```

- `etype`：始终可用（L2 render H5 里有 `source_etype_str`）
- `material` / `section_type`：仅当 L1 H5 里有对应属性时出现（INP 导出路径会写入；ODB 路径视 Abaqus 版本而定）
- `elset`：当 `l1/sets/sets.h5` 里有 element sets 时出现

---

### 15.3 获取颜色数据

```
GET /api/odb/{odb_id}/color-code/{instance}
  ?scheme=etype|material|section_type|elset
  [&set_names=Set-1,Set-2,...]   # scheme=elset 时必填，逗号分隔
```

**响应**：

- Body：L3BE 二进制，包含 `color_per_vertex [Rf*3, 3] float32`（与 Triangle Soup 顶点一一对应）
- Header `X-Face-Count`：渲染面数 Rf
- Header `X-Color-Legend`：JSON 数组 `[{id, name, r, g, b}, ...]`

**上色规则**：

| scheme | 分组依据 | 数据来源 |
|---|---|---|
| `etype` | 单元类型字符串（C3D8R / S4R / …） | L2 `render/source_etype_str` |
| `material` | 材料名（ELASTIC_STEEL / …） | L1 `elements/{etype}/material_name` |
| `section_type` | 截面类型（SOLID / SHELL / …） | L1 `elements/{etype}/section_type` |
| `elset` | 所属 element set（优先级 = set_names 顺序） | L1 `sets/sets.h5` |

- 调色板：16 色定性色板，循环使用
- `(none)` / 不在 elset 内的单元：统一灰色 `(0.35, 0.35, 0.35)`
- elset 多选时各 set 独立分配颜色，优先级按 `set_names` 顺序（靠前的覆盖靠后的）

---

### 15.4 前端交互流程

```
加载 geometry 完成后：
  GET /schemes → 填充 Scheme 下拉 + Elset 复选框列表

用户选择 scheme 后点 Apply：
  GET /color-code?scheme=... → L3BE body + X-Color-Legend header
  解析 L3BE → float32 数组 → geometry.setAttribute('color', ...)
  解析 X-Color-Legend → 渲染色块图例

点 Clear：
  重置为默认蓝灰色 (0.25, 0.35, 0.55)，清除图例
```

---

### 15.5 实现文件

| 文件 | 职责 |
|---|---|
| `src/l3/api/routes/color_code.py` | 路由：两个 GET 端点，解析参数，返回 L3BE + 响应头 |
| `src/l3/services/color_service.py` | 服务：按 scheme 读 H5，组装颜色数组和图例 |
| `tools/viewer.html` | 前端：Color Code 卡片，Scheme 下拉，Elset 复选框（含全选/全不选），图例渲染 |

---

## 16. 最终结论

L3 第一版 API 契约应先稳定这几件事：

- 路由命名和分组
- JSON 成功/错误格式
- 二进制响应头约定
- token 认证预留
- 可用于多数组返回的 binary envelope
- `202 NOT_READY` 语义
- `mesh/chunk`、`render/state`、`query/pick` 三条核心链路

在 L1/L2 完全落地之前，先把这些契约钉住，后续服务开发和前端对接都会更顺。
