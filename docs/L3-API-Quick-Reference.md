# ODB 服务 L3 接口速查手册

> 面向调用方，只需关注"发什么请求、收到什么数据"。  
> 服务地址：`http://<host>:18765`（端口按实际部署）

---

## 启动服务（部署方负责）

```bash
# 开发（默认使用项目根目录的 model/ 目录，无需设置环境变量）
uvicorn src.l3.main:app --host 0.0.0.0 --port 18765

# 生产（多 worker）
gunicorn src.l3.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

如需自定义路径，在项目根目录创建 `service_config.json`（不创建也可运行）：
```json
{
  "APP_DATA_ROOT": "model/",
  "APP_REGISTRY_DB_PATH": "model/registry.db",
  "APP_ABAQUS_CMD": "abaqus"
}
```

---

## 一、作业管理

> 把一个 ODB 文件提交给服务处理，等处理完再读数据。

### 提交新作业

```
POST /api/jobs
Content-Type: application/json

{ "odb_path": "/data/raw/car_body.odb", "display_name": "车身模型-v3" }
```

**成功响应 201：**
```json
{
  "odb_id": "a3f7c2d1-1876-4b2a-823c-abad46f8c353",
  "display_name": "车身模型-v3",
  "status": "submitted"
}
```

把 `odb_id` 保存好，后续所有接口都用它。

---

### 查询处理状态

```
GET /api/jobs/{odb_id}
```

**响应：**
```json
{
  "odb_id": "a3f7c2d1-...",
  "display_name": "车身模型-v3",
  "status": "ready",
  "is_render_ready": true,
  "node_count": 284000,
  "instance_count": 3,
  "created_at": "2026-04-08T10:00:00Z",
  "l1_done_at": "2026-04-08T10:05:00Z",
  "l2_done_at": "2026-04-08T10:08:00Z",
  "error_msg": null
}
```

**`status` 流转：**

```
submitted → l1_running → l1_done → l2_running → ready
                                              （或 error）
```

**`is_render_ready = true` 才能读后续数据。**

---

### 列出所有作业

```
GET /api/jobs
```

返回所有 job 的数组，字段同上。

---

### 删除作业

```
DELETE /api/jobs/{odb_id}              # 软删除（保留磁盘文件）
DELETE /api/jobs/{odb_id}?hard=true   # 硬删除（同时删磁盘）
```

运行中（`l1_running` / `l2_running`）时拒绝删除，返回 409。

---

### 重试失败作业

```
POST /api/jobs/{odb_id}/retry
```

仅当 `status == "error"` 时可用。

---

## 二、模型结构信息

> 处理完成后，先查总览，了解模型有哪些实例、步骤、结果场。

### 总览

```
GET /api/odb/{odb_id}/meta/overview
```

**响应：**
```json
{
  "ok": true,
  "data": {
    "instances": ["PART-1-1", "PART-2-1"],
    "steps": ["Step-1", "Step-2"],
    "fields": ["U", "S", "RF"],
    "default_step": "Step-1",
    "default_frame_idx": 0,
    "default_field": "U"
  }
}
```

- `instances`：模型里的部件实例名，后续接口都要带这个
- `steps`：分析步骤（对应一段加载历程）
- `fields`：可读的结果场，`U`=位移，`S`=应力，`RF`=反力等

---

## 三、几何数据

> 返回**二进制**数据（L3BE 格式），需要解码后才能用。  
> 解码方式见文末"二进制格式说明"。

### 3.1 获取表面三角网格

```
GET /api/odb/{odb_id}/geometry/{instance}/render-buffers
```

**示例：**
```bash
curl -o part1_render.bin \
  "http://localhost:18765/api/odb/a3f7c2d1-.../geometry/PART-1-1/render-buffers"
```

**响应头：**
```
X-Face-Count: 48320     ← 三角面片数量 Rf
```

**响应体（L3BE 二进制，解码后得到）：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `positions` | `[Rf×3, 3]` | float32 | 三角面顶点坐标 XYZ |
| `normals` | `[Rf×3, 3]` | float32 | 法向量（可能不存在） |

**含义说明：**  
每个三角面有 3 个顶点，总共 `Rf×3` 行，每行是一个 `[x, y, z]`。
数据是"展开的"（Triangle Soup），没有索引缓冲区，可以直接用于渲染或计算面积/体积。

---

### 3.2 获取特征边线（外轮廓 + 折痕）

```
GET /api/odb/{odb_id}/geometry/{instance}/feature-edges
```

**响应头：**
```
X-Edge-Count: 3200     ← 边的数量 E
```

**解码后：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `edge_positions` | `[E×2, 3]` | float32 | 每两行是一条边的起点和终点 |

---

### 3.3 获取单元网格边线（所有单元边界）

```
GET /api/odb/{odb_id}/geometry/{instance}/element-mesh-edges
```

格式与 3.2 相同，区别：包含所有单元边界线（密度更高），3.2 只包含外形轮廓。

---

## 四、结果数据

### 4.1 获取某帧的结果云图颜色

```
GET /api/odb/{odb_id}/results/frame-colors
  ?instance=PART-1-1
  &step=Step-1
  &field=U
  &frame=5
  &component=USUM
  &mode=smooth
```

**参数说明：**

| 参数 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `instance` | 是 | 实例名 | — |
| `step` | 是 | 步骤名 | — |
| `field` | 是 | `U`/`S`/… | 结果场名 |
| `frame` | 否 | 整数，默认 0 | 帧索引 |
| `component` | 否 | `U1`/`U2`/`U3`/`USUM` | 分量，默认 `USUM` |
| `mode` | 否 | `smooth`/`flat` | 插值方式，默认 `smooth` |

**响应头：**
```
X-Val-Min: -0.0023
X-Val-Max:  0.0157
X-Component: USUM
X-Frame: 5
```

**解码后：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `color_per_vertex` | `[Rf×3, 4]` | uint8 | 每顶点 RGBA 颜色（0~255） |
| `legend_range` | `[2]` | float32 | [最小值, 最大值] |

---

### 4.2 剖面网格

```
GET /api/odb/{odb_id}/results/section-mesh
  ?instance=PART-1-1
  &axis=X
  &position=50.0
```

**参数说明：**

| 参数 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `instance` | 是 | — | — |
| `axis` | 否 | `X`/`Y`/`Z` | 切割平面法向，默认 `Z` |
| `position` | 否 | 浮点数，默认 0.0 | 切割位置（世界坐标） |

**响应头：**
```
X-Tri-Count: 1024   ← 截面三角形数量
X-Edge-Count: 320   ← 截面轮廓线段数量
X-Axis: X
X-Position: 50.0
```

**解码后：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `vertices` | `[T×3, 3]` | float32 | 截面三角形顶点（填充用） |
| `edge_verts` | `[E×2, 3]` | float32 | 截面多边形轮廓线段 |

---

### 4.3 原始结果数值（raw-values）

返回指定帧的**原始数值**（不做上色），支持三种结果位置类型。面向计算工作流（疲劳分析、二次后处理等）。

```
GET /api/odb/{odb_id}/results/raw-values
  ?instance=PART-1-1
  &step=Step-1
  &field=S
  &frame=5
  &position=INTEGRATION_POINT
```

**参数说明：**

| 参数 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `instance` | 是 | — | 实例名 |
| `step` | 是 | — | 步骤名 |
| `field` | 是 | `U`/`S`/`LE`/… | 结果场名 |
| `frame` | 否 | 整数，默认 0 | 帧索引（0-based） |
| `position` | 是 | `NODAL` / `ELEMENT_NODAL` / `INTEGRATION_POINT` | 结果位置类型 |

**三种 position 的区别：**

| position | 含义 | 适用场景 |
|---|---|---|
| `NODAL` | 每个节点一个值（如位移 U） | 位移、反力 |
| `ELEMENT_NODAL` | 每个单元的角点各一个值 | 应力外推到节点后的平均值 |
| `INTEGRATION_POINT` | 每个单元的积分点各一个值 | 应力/应变积分点原始值，精度最高 |

**响应头：**
```
X-Payload-Type:  raw_values_v1
X-Position:      INTEGRATION_POINT
X-Frame:         5
X-Components:    ["S11","S22","S33","S12","S13","S23"]
X-Etype-Groups:  ["C3D8R","C3D4"]
```

**解码后（NODAL）：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `node_labels` | `[N]` | int32 | ODB 节点号 |
| `values` | `[N, ncomp]` | float32 | 每节点结果值 |

**解码后（ELEMENT_NODAL / INTEGRATION_POINT）：**

每种单元类型对应一组 section，名称中的 `{etype}` 是单元类型字符串（特殊字符替换为 `_`）：

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `el_{etype}` | `[M]` | int32 | ODB 单元号 |
| `v_{etype}` | `[M, n_ip, ncomp]` | float32 | 每积分点/角点结果值 |

> `n_ip` = 积分点数（如 C3D8R 为 1，C3D20R 为 8）；ELEMENT_NODAL 时为角点数。

**Python 解码示例：**

```python
import requests

# 读取应力积分点数据
resp = requests.get(
    "http://localhost:18765/api/odb/a3f7c2d1-.../results/raw-values",
    params={
        "instance": "PART-1-1",
        "step": "Step-1",
        "field": "S",
        "frame": 5,
        "position": "INTEGRATION_POINT",
    }
)
import json
components  = json.loads(resp.headers["X-Components"])   # ["S11","S22","S33","S12","S13","S23"]
etype_groups = json.loads(resp.headers["X-Etype-Groups"]) # ["C3D8R"]

sections = decode_l3be(resp.content)

elem_labels = sections["el_C3D8R"]   # [M] int32 — ODB 单元号
values      = sections["v_C3D8R"]    # [M, n_ip, 6] float32

s11 = values[:, :, 0]   # 所有单元所有积分点的 S11
print(f"S11 最大值: {s11.max():.4f}")

# 读取节点位移
resp2 = requests.get(
    "http://localhost:18765/api/odb/a3f7c2d1-.../results/raw-values",
    params={"instance": "PART-1-1", "step": "Step-1",
            "field": "U", "frame": 5, "position": "NODAL"}
)
sections2   = decode_l3be(resp2.content)
node_labels = sections2["node_labels"]  # [N] int32
displ       = sections2["values"]       # [N, 3] float32 — U1, U2, U3
```

---

## 五、查询接口

### 5.1 点选单元/节点（pick）

根据三角面片索引，查出对应的 ODB 单元号、节点号以及当前结果值。

```
GET /api/odb/{odb_id}/query/pick
  ?instance=PART-1-1
  &render_face_idx=1234
  &pick_mode=element
  &step=Step-1
  &field=U
  &frame_idx=5
  &component=USUM
```

**完整参数：**

| 参数 | 必填 | 说明 |
|---|---|---|
| `instance` | 是 | 实例名 |
| `render_face_idx` | 是 | 三角面片全局索引（从几何接口对应） |
| `pick_mode` | 否 | `element`（默认）/ `node` |
| `node_idx` | 否 | node 模式：最近顶点 0/1/2 |
| `step` | 否 | 不传则不查结果值 |
| `field` | 否 | 结果场名 |
| `frame_idx` | 否 | 帧索引 |
| `component` | 否 | 分量名，如 `U1`、`USUM`、`S11` |
| `component_idx` | 否 | 显式列索引（S11→0, S22→1, S33→2；优先于 component 名） |
| `include_coords` | 否 | node 模式：是否返回节点坐标，默认 false |
| `deform_scale` | 否 | 变形放大系数，默认 1.0 |

**element 模式响应：**
```json
{
  "pick_mode": "element",
  "instance": "PART-1-1",
  "render_face_idx": 1234,
  "render_face_indices": [1234, 1235, 1236, 1237],
  "odb": {
    "elem_label": 5678,
    "elem_type": "C3D8R",
    "elem_node_labels": [101, 102, 103, 104, 105, 106, 107, 108]
  },
  "result": {
    "field": "U",
    "position": "NODAL",
    "component": "USUM",
    "value_kind": "odb_raw",
    "display_value": 0.0124
  },
  "mises": 125.4
}
```

**node 模式响应（含坐标）：**
```json
{
  "pick_mode": "node",
  "instance": "PART-1-1",
  "render_face_idx": 1234,
  "render_face_indices": [1234, 1235],
  "odb": {
    "node_label": 102,
    "candidate_node_labels": [101, 102, 103],
    "orig_coords": [10.5, 20.3, 0.0],
    "def_coords": [10.512, 20.298, 0.003],
    "attached_elem_labels": [5678, 5679]
  },
  "result": {
    "field": "U",
    "component": "U1",
    "display_value": 0.012
  },
  "mises": null
}
```

---

### 5.2 空间包围盒查询（bbox）

给一个 3D 矩形区域，返回区域内的单元集合。

```
POST /api/odb/{odb_id}/query/bbox
Content-Type: application/json

{
  "instance": "PART-1-1",
  "bbox_min": [0.0, 0.0, 0.0],
  "bbox_max": [100.0, 50.0, 30.0],
  "mode": "intersect",
  "set_name": "MyRegion"
}
```

**字段说明：**

| 字段 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `instance` | 是 | — | — |
| `bbox_min` | 是 | [x, y, z] | 包围盒最小角（世界坐标） |
| `bbox_max` | 是 | [x, y, z] | 包围盒最大角 |
| `mode` | 否 | `intersect`（默认）/ `contained` | intersect：有顶点在盒内即命中；contained：全部顶点在盒内 |
| `set_name` | 否 | 字符串 | 传了则持久化为命名集合 |

**响应：**
```json
{
  "set_name": "MyRegion",
  "elem_count": 142,
  "render_face_count": 388,
  "elem_labels": [5001, 5002, 5003]
}
```

`elem_labels` 仅在 `elem_count ≤ 2000` 时返回完整列表。

---

### 5.4 最近面查询（nearest-face）

给定空间中任意一点，找到模型表面最近的三角面及其法线。

```
GET /api/odb/{odb_id}/query/nearest-face?instance=PART-1-1&x=0.0&y=0.0&z=0.15
```

**响应：**
```json
{
  "instance": "PART-1-1",
  "render_face_idx": 142,
  "elem_label": 1023,
  "elem_type": "C3D8R",
  "normal": [0.0, 0.0, 1.0],
  "closest_point": [0.0, 0.0, 0.2],
  "distance": 0.05
}
```

| 字段 | 说明 |
|------|------|
| `normal` | 该面的单位法向量 |
| `closest_point` | 面上离查询点最近的位置 |
| `distance` | 查询点到最近点的距离（模型单位） |

> 点不需要在网格上，可以是模型外部的任意空间点。

---

### 5.5 法向矩形选取（surface-patch）

以某点为中心，在其法线平面上铺一个矩形，选取所有被覆盖的面/单元/节点。

**典型用法：**
1. 先用 `5.4 nearest-face` 获得中心点和法线
2. 再调用本接口，传入宽高完成区域选取

```
POST /api/odb/{odb_id}/query/surface-patch
Content-Type: application/json

{
  "instance": "PART-1-1",
  "center": [0.0, 0.0, 0.2],
  "normal": [0.0, 0.0, 1.0],
  "width": 0.05,
  "height": 0.03
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `center` | 是 | 矩形中心点（世界坐标） |
| `normal` | 是 | 矩形平面法向，内部自动归一化 |
| `width` | 是 | 矩形宽度（模型单位） |
| `height` | 是 | 矩形高度（模型单位） |
| `up_hint` | 否 | 定义"高度"方向，默认全局 Y `[0,1,0]` |

**矩形方向说明：**
- 宽度方向 `u = normalize(cross(up_hint, normal))`
- 高度方向 `v = normalize(cross(normal, u))`
- 默认 `up_hint=[0,1,0]`，即矩形高度尽量朝 Y 轴

**响应：**
```json
{
  "face_count": 45,
  "elem_count": 23,
  "node_count": 67,
  "render_face_indices": [100, 101, 142],
  "elem_labels": [1001, 1002, 1023],
  "node_labels": [201, 202, 203],
  "node_positions": [[0.01, 0.0, 0.2]]
}
```

| 字段 | 说明 |
|------|------|
| `render_face_indices` | 传给前端直接高亮 |
| `elem_labels` | 单元标签，`> 2000` 时为 null |
| `node_labels` | 节点标签，`> 2000` 时为 null |
| `node_positions` | 节点坐标，`> 5000` 时为 null |

---

### 5.3 批量面片解析（render-faces）

已知一批三角面片索引，查出它们属于哪些单元/节点。

```
POST /api/odb/{odb_id}/query/render-faces
Content-Type: application/json

{
  "instance": "PART-1-1",
  "render_face_indices": [100, 101, 102, 500, 501],
  "mode": "element"
}
```

**element 模式响应：**
```json
{
  "mode": "element",
  "elem_count": 3,
  "elem_labels": [5001, 5002, 5010],
  "elem_face_indices": [100, 101, 102, 500, 501, 502],
  "elem_ids_per_face": [0, 0, 1, 1, 2, 2]
}
```

> `elem_face_indices` 会自动扩展到命中单元的**全部**三角面，不只是传入的面。  
> `elem_ids_per_face` 标注每个面属于第几个单元（0-based）。

**node 模式响应：**
```json
{
  "mode": "node",
  "node_count": 12,
  "node_labels": [101, 102, 103, 201, 202],
  "node_positions": [[10.5, 20.3, 0.0], [11.0, 20.1, 0.0]]
}
```

> `elem_labels`/`node_labels` 超过 2000 时截断；`node_positions` 超过 5000 时截断。

---

## 六、属性上色

### 查询可用方案

```
GET /api/odb/{odb_id}/color-code/{instance}/schemes
```

```json
{
  "schemes": ["etype", "material", "section_type", "elset"],
  "elsets": ["Set-1", "Set-2", "Nozzle"]
}
```

### 获取颜色数据

```
GET /api/odb/{odb_id}/color-code/{instance}
  ?scheme=etype

GET /api/odb/{odb_id}/color-code/{instance}
  ?scheme=elset&set_names=Set-1,Set-2
```

**参数：**

| 参数 | 必填 | 取值 |
|---|---|---|
| `scheme` | 是 | `etype` / `material` / `section_type` / `elset` |
| `set_names` | 否 | 逗号分隔集合名，仅 `scheme=elset` 时有效 |

**响应头：**
```
X-Face-Count: 48320
X-Color-Legend: [{"id":0,"name":"C3D8R","r":0.8,"g":0.2,"b":0.1}, ...]
```

**解码后：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `color_per_vertex` | `[Rf×3, 3]` | float32 | 每顶点 RGB（0.0~1.0） |

---

## 七、节点字段表

> 批量查询指定节点在多个结果场/分量下的数值，常用于探针面板、疲劳后处理等。

### 7.1 查询可用字段

```
GET /api/odb/{odb_id}/fields?instance=PART-1-1&step=Step-1
```

**响应：**
```json
{
  "instance": "PART-1-1",
  "step": "Step-1",
  "fields": [
    {
      "field": "U",
      "positions": ["NODAL"],
      "components": ["U1", "U2", "U3", "USUM"]
    },
    {
      "field": "S",
      "positions": ["INTEGRATION_POINT", "ELEMENT_NODAL"],
      "components": ["S11", "S22", "S33", "S12", "S13", "S23", "MISES", "PRESS", "TRESC", "INV3"]
    }
  ]
}
```

---

### 7.2 批量节点结果表（node-table）

给一批节点号 + 多列 `(field, component)` 的组合，返回 `[N × M]` 浮点矩阵。

```
POST /api/odb/{odb_id}/results/node-table
Content-Type: application/json

{
  "instance": "PART-1-1",
  "step": "Step-1",
  "frame_idx": 5,
  "node_labels": [101, 102, 103, 200],
  "items": [
    {"field": "U",  "component": "USUM"},
    {"field": "U",  "component": "U1"},
    {"field": "S",  "component": "MISES"}
  ]
}
```

**响应头：**
```
X-Payload-Type:   node_table_v1
X-Layout-Version: 1
X-Node-Count:     4
X-Col-Count:      3
X-Columns:        [{"field":"U","component":"USUM"}, ...]
X-Field-Coverage: step
```

**解码后（L3BE）：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `node_labels` | `[N]` | int32 | ODB 节点号（与请求顺序一致） |
| `values` | `[N, M]` | float32 | 结果矩阵；节点不在实例中时为 NaN |

---

## 八、自定义结果场（user-field）

> 把外部计算结果（如疲劳损伤、安全系数）按单元号写入服务，再用云图渲染。

### 8.1 上传自定义场

```
POST /api/odb/{odb_id}/results/user-field
Content-Type: application/json

{
  "name": "fatigue_damage",
  "instance": "PART-1-1",
  "value": 0.85,
  "element_labels": [1001, 1002, 1003, 5678]
}
```

同一 `(name, instance)` 重复调用会覆盖之前的值（upsert）。

**响应 201：**
```json
{"name": "fatigue_damage", "instance": "PART-1-1", "elem_count": 4}
```

---

### 8.2 列出已有字段

```
GET /api/odb/{odb_id}/results/user-fields
GET /api/odb/{odb_id}/results/user-fields?instance=PART-1-1
```

**响应：**
```json
{
  "odb_id": "a3f7c2d1-...",
  "fields": [
    {"name": "fatigue_damage", "instance": "PART-1-1", "elem_count": 4, "value": 0.85}
  ]
}
```

---

### 8.3 获取自定义场云图颜色

```
GET /api/odb/{odb_id}/results/user-field-colors
  ?name=fatigue_damage
  &instance=PART-1-1
  &val_min=0.0
  &val_max=1.0
```

`val_min` / `val_max` 可省略，省略时自动以实际值范围归一。

**响应头：**
```
X-Payload-Type:   user_field_colors_v1
X-Val-Min: 0.0
X-Val-Max: 1.0
X-Field-Name: fatigue_damage
```

**解码后（L3BE）：**

| Section 名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `color_per_vertex` | `[Rf×3, 4]` | uint8 | 每顶点 RGBA；集合内按 jet 上色，集合外为灰色 |
| `legend_range` | `[2]` | float32 | [最小值, 最大值] |

---

### 8.4 删除自定义场

```
DELETE /api/odb/{odb_id}/results/user-field?name=fatigue_damage&instance=PART-1-1
```

成功返回 `{"deleted": true, "name": "fatigue_damage", "instance": "PART-1-1"}`；不存在返回 404。

---

## 九、模态分析（modal）

> 独立于 ODB 工作流。读取预先生成的模态 JSON 文件，用于振型可视化和动画。

### 9.1 注册 JSON 文件

```
POST /api/modal/load
Content-Type: application/json

{"path": "/data/modal/bridge_modal.json"}
```

**响应：**
```json
{"ok": true, "model_id": "bridge_modal"}
```

`model_id` 由文件名（不含扩展名）派生，后续接口均以此为索引。重复注册同一文件不报错。

---

### 9.2 几何数据

```
GET /api/modal/{model_id}/geometry
```

**响应（JSON）：**
```json
{
  "ok": true,
  "data": {
    "originPos": [x0, y0, z0, x1, y1, z1, ...],
    "index":     [0, 1, 2, ...]
  }
}
```

`originPos` 是 flat 节点坐标数组 `[N×3]`，`index` 是三角化索引，可直接传入 Three.js `BufferGeometry`。

---

### 9.3 阶次列表

```
GET /api/modal/{model_id}/modes
```

**响应：**
```json
{
  "ok": true,
  "data": [
    {"label": "EMA 1 - 12.3 Hz", "value": 1},
    {"label": "EMA 2 - 34.7 Hz", "value": 2}
  ]
}
```

---

### 9.4 分量列表

```
GET /api/modal/{model_id}/components
```

**响应：**
```json
{
  "ok": true,
  "data": ["U-Modulus:usum", "DOF UX", "DOF UY", "DOF UZ"]
}
```

---

### 9.5 变形振型数据

```
GET /api/modal/{model_id}/deformed?order=1&max_scalar_size=0.1&coefficient=1.0
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `order` | 是 | 模态阶次（从 9.3 取值） |
| `max_scalar_size` | 否 | 最大位移放大后的比例（相对模型包围盒），默认 0.1 |
| `coefficient` | 否 | 额外放大系数，默认 1.0 |

**响应：**
```json
{
  "ok": true,
  "data": {
    "componentData": [0.0012, 0.0034, ...],
    "maxValue": 0.0045,
    "minValue": 0.0,
    "scaleFactor": 50.0,
    "newPos": [x0_def, y0_def, z0_def, ...]
  }
}
```

`newPos` 是变形后节点坐标 flat 数组，直接覆盖 Three.js geometry 的 `position` 属性即可。

---

### 9.6 动画数据

```
GET /api/modal/{model_id}/animation?order=1
```

**响应：**
```json
{
  "ok": true,
  "data": {
    "real": [dx0, dy0, dz0, ...],
    "imag": [dx0, dy0, dz0, ...]
  }
}
```

前端按 `pos(t) = originPos + real×cos(ωt) + imag×sin(ωt)` 做动画。

---

### 9.7 指定分量云图

```
GET /api/modal/{model_id}/colormap?order=1&component=usum&max_scalar_size=0.1
```

`component` 取值：`usum` | `ux` | `uy` | `uz`（也接受 9.4 返回的完整标签字符串）。

响应结构与 9.5 `/deformed` 相同。

---

## 十、Simright 兼容层

> 兼容 simright 3DLite DATA-API 格式，面向已对接 simright 的前端。

```
POST /applications/3dlite/api/v1/query
Content-Type: application/json

{"name": "<handler>", "args": {...}}
```

**注意：此接口没有 `/api/odb/{odb_id}` 前缀，`odb_id` 通过 `args` 传入。**

**响应格式：**
```json
{"code": 0,    "data": <结果>,  "message": "success"}
{"code": 1,    "data": null,    "message": "<错误描述>"}
```

支持的 `name` 值：

| name | 说明 |
|---|---|
| `loadcases` | 列出分析步骤（load cases） |
| `variables` | 列出某步骤的可用结果变量 |
| `assemble` | 部件树（instances + sets） |
| `extremeValue` | 指定场的全帧最大/最小值 |
| `nodeInfo` | 指定节点的坐标和结果值 |
| `elementInfo` | 指定单元的坐标和结果值 |
| `XYCurveData1` | 指定节点的时间序列 XY 数据 |
| `freqValue` | 模态频率列表 |

---

## 十一、服务健康检查

```
GET /api/health/live     ← 进程存活（始终返回 200）
GET /api/health/ready    ← 服务就绪（数据库可用时返回 200）
```

**响应：**
```json
{"status": "ok"}
```

Load balancer / k8s liveness probe 用 `/live`，readiness probe 用 `/ready`。

---

## 十二、错误格式

所有 JSON 错误统一格式：

```json
{
  "detail": "ODB 'xxx' not found"
}
```

常见 HTTP 状态码：

| 状态码 | 含义 |
|---|---|
| 400 | 参数错误（文件不存在、路径非法等） |
| 404 | odb_id / instance 不存在 |
| 409 | 操作冲突（如删除正在运行的 job） |
| 500 | 服务内部错误 |

---

## 附：二进制格式（L3BE）解码说明

所有几何/结果接口返回的是 **L3 Binary Envelope v1**，是一个自描述的二进制文件，包含若干命名 numpy 数组。

### 文件结构

```
[固定头 40 字节]
[Section 描述表，每项 80 字节，共 N 项]
[数据区（8字节对齐）]
```

### 固定头（40 字节，小端序）

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---|---|---|---|---|
| 0 | 4 | bytes | magic | `"L3BE"` |
| 4 | 2 | uint16 | version | = 1 |
| 6 | 2 | uint16 | flags | 保留，= 0 |
| 8 | 4 | uint32 | header_size | = 40 |
| 12 | 4 | uint32 | section_count | Section 数量 N |
| 16 | 4 | uint32 | table_offset | = 40 |
| 20 | 4 | uint32 | payload_offset | = 40 + N×80（对齐后） |
| 24 | 16 | — | reserved | — |

### 每个 Section 描述项（80 字节，小端序）

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---|---|---|---|---|
| 0 | 32 | bytes | name | ASCII 名称，右填 `\x00` |
| 32 | 2 | uint16 | dtype_code | 见下表 |
| 34 | 2 | uint16 | ndim | 维度数 |
| 36 | 16 | uint32×4 | shape | 各维大小，不足补 0 |
| 52 | 8 | uint64 | offset | 数据在整个 payload 中的字节偏移 |
| 60 | 8 | uint64 | nbytes | 数据字节数 |
| 68 | 4 | uint32 | flags | 保留 |
| 72 | 8 | — | reserved | — |

**dtype_code 对照：**

| code | 类型 | numpy dtype |
|---|---|---|
| 2 | uint8 | `np.uint8` |
| 5 | int32 | `np.int32` |
| 9 | float32 | `np.float32` |
| 10 | float64 | `np.float64` |

### Python 解码示例

```python
import struct
import numpy as np

DTYPE_MAP = {1: np.int8, 2: np.uint8, 3: np.int16, 4: np.uint16,
             5: np.int32, 6: np.uint32, 7: np.int64, 8: np.uint64,
             9: np.float32, 10: np.float64}

def decode_l3be(data: bytes) -> dict:
    """解码 L3BE 二进制，返回 {section_name: np.ndarray}"""
    magic, version, flags, hdr_size, n_sections, tbl_off, pay_off = \
        struct.unpack_from("<4sHHIIII", data, 0)
    assert magic == b"L3BE", "不是 L3BE 格式"

    result = {}
    for i in range(n_sections):
        entry_off = tbl_off + i * 80
        name_b, dtype_code, ndim, s0, s1, s2, s3, offset, nbytes, _ = \
            struct.unpack_from("<32sHH4IQQI", data, entry_off)
        name = name_b.rstrip(b"\x00").decode("ascii")
        shape = [s0, s1, s2, s3][:ndim]
        dtype = DTYPE_MAP[dtype_code]
        arr = np.frombuffer(data, dtype=dtype, count=nbytes // np.dtype(dtype).itemsize,
                            offset=offset).reshape(shape).copy()
        result[name] = arr
    return result


# 使用示例
import requests

resp = requests.get("http://localhost:18765/api/odb/a3f7c2d1-.../geometry/PART-1-1/render-buffers")
sections = decode_l3be(resp.content)

positions = sections["positions"]   # shape: [Rf*3, 3], dtype: float32
normals   = sections.get("normals") # shape: [Rf*3, 3], dtype: float32（可能没有）

print(f"三角面片数: {positions.shape[0] // 3}")
print(f"顶点坐标范围: x={positions[:,0].min():.3f}~{positions[:,0].max():.3f}")
```

### 读取结果颜色示例

```python
resp = requests.get(
    "http://localhost:18765/api/odb/a3f7c2d1-.../results/frame-colors",
    params={"instance": "PART-1-1", "step": "Step-1", "field": "U",
            "frame": 5, "component": "USUM"}
)
sections = decode_l3be(resp.content)

colors       = sections["color_per_vertex"]  # [Rf*3, 4] uint8, RGBA
legend_range = sections["legend_range"]       # [2] float32, [min, max]

val_min = legend_range[0]
val_max = legend_range[1]
print(f"结果值范围: {val_min:.6f} ~ {val_max:.6f}")
```

### 读取 pick 查询示例

```python
resp = requests.get(
    "http://localhost:18765/api/odb/a3f7c2d1-.../query/pick",
    params={
        "instance": "PART-1-1",
        "render_face_idx": 1234,
        "pick_mode": "element",
        "step": "Step-1",
        "field": "S",
        "frame_idx": 5,
        "component_idx": 0,   # S11
    }
)
data = resp.json()
print(f"单元号: {data['odb']['elem_label']}")
print(f"单元类型: {data['odb']['elem_type']}")
print(f"S11 值: {data['result']['display_value']}")
print(f"Von Mises: {data['mises']}")
```
