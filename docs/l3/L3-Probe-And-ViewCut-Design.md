# Probe Values 与 View Cut 设计文档

**版本**：v3
**状态**：已实现
**最后更新**：2026-04-01

---

## 目录

1. [Probe Values](#1-probe-values)
   - [1.1 交互模式](#11-交互模式)
   - [1.2 悬浮提示（Hover Tooltip）](#12-悬浮提示hover-tooltip)
   - [1.3 节点模式浮窗表格](#13-节点模式浮窗表格)
   - [1.4 单元模式浮窗表格](#14-单元模式浮窗表格)
   - [1.5 后端数据来源分析](#15-后端数据来源分析)
   - [1.6 L1 改动：节点反查映射](#16-l1-改动节点反查映射)
   - [1.7 后端接口扩展](#17-后端接口扩展)
   - [1.8 实施计划](#18-实施计划)
2. [View Cut（截面切割）](#2-view-cut截面切割)
   - [2.1 功能描述](#21-功能描述)
   - [2.2 第一阶段：Three.js Clipping Plane](#22-第一阶段threejs-clipping-plane)
   - [2.3 第二阶段：后端截面网格生成](#23-第二阶段后端截面网格生成)
   - [2.4 实施计划](#24-实施计划)

---

## 1. Probe Values

### 1.1 交互模式

Abaqus 的 Probe Values 交互分两层：

- **悬停（Hover）**：鼠标在模型上移动时，光标旁出现小气泡显示当前最近的节点/单元 ID，同时临时高亮该节点或单元
- **点击（Click）**：将当前悬停的探针结果"钉"进下方浮窗表格，可累积多条记录

我们的实现策略：

```
mousemove → Three.js Raycaster（纯前端，0 后端调用）
              ├─ 高亮临时 render face
              └─ 显示 Hover Tooltip（节点/单元 ID）

click     → GET /query/pick（后端查完整数据）
              └─ 追加一行进浮窗表格（不覆盖已有行）
```

**为什么 hover 不调用后端**：鼠标移动频率可达 60fps，每次请求后端会造成严重延迟。Three.js Raycaster 是纯 CPU 计算，可在毫秒内返回 `render_face_idx`，足够用于高亮和 ID 显示。

**Pick 模式开关**：页面上有显式按钮切换 Node / Element 模式，决定 hover 和 click 的解释语义。默认为 Node 模式。

---

### 1.2 悬浮提示（Hover Tooltip）

**样式**：跟随鼠标的小气泡，显示在光标右上方，不遮挡主视图。

**内容**：
- Node 模式：`Node 1234`
- Element 模式：`Element 456 (C3D8R)`

**实现**：

```javascript
// mousemove handler（仅在 probe 模式激活时）
function onMouseMove(event) {
  if (!probeMode) return;
  const intersects = raycaster.intersectObject(mesh);
  if (intersects.length === 0) {
    tooltip.style.display = 'none';
    clearHoverHighlight();
    return;
  }

  const faceIdx = intersects[0].faceIndex;  // render_face_idx
  // 从前端缓存的 face-label-map 查 elem_label / node_labels
  // （一期若无缓存，hover 只高亮，不显示 ID；点击后再查后端）

  tooltip.textContent = pickMode === 'node'
    ? `Node ${nodeLabel}`
    : `Element ${elemLabel} (${elemType})`;
  tooltip.style.left = event.clientX + 12 + 'px';
  tooltip.style.top  = event.clientY - 20 + 'px';
  tooltip.style.display = 'block';
}
```

**关于 label 的来源**：`render_face_idx → elem_label / node_label` 这个映射当前只在后端。若要 hover 不调后端就能显示 label，有两个选项：

- **方案 A（推荐）**：前端在加载网格时，同时拉取一份紧凑的 `render_face_idx → [elem_label, node_labels[3]]` 映射缓存（新接口 `GET /geometry/{instance}/face-label-map`，返回压缩 int32 数组）
- **方案 B（简单）**：hover 只高亮不显示 ID，点击时再查后端拿 label

一期先用方案 B，方案 A 作为后续优化。

**方案 A 容量估算**（供后续决策参考）：

| 模型规模 | render faces (Rf) | 每 face 存储（elem_label 1×int32 + node_labels 3×int32） | 原始大小 | gzip 后约 |
|---|---|---|---|---|
| 小模型 | 100万 | 4×4 = 16 B | ~16 MB | ~6 MB |
| 大模型 | 1000万 | 4×4 = 16 B | ~160 MB | ~60 MB |

大模型下 60 MB 网络传输量较大，建议方案 A 实施时按实例分批懒加载，且仅在进入 Probe 模式时触发拉取。

---

### 1.3 节点模式浮窗表格

点击后追加一行，列定义：

| 列名 | 含义 | 数据来源 |
|---|---|---|
| Part Instance | 所属实例名 | pick 响应 `instance` |
| Node ID | ODB 节点号 | `odb.node_label` |
| Orig. Coords | 未变形坐标 `(X, Y, Z)` | L1 节点坐标表 |
| Def. Coords | 变形后坐标 = Orig + U × scale | 当前帧 U 字段 + Orig |
| Attached Elements | 该节点所属的单元 label 列表 | L1 节点→单元反查表（见 §1.6） |
| S, Mises | Von Mises 应力（当前帧） | 后端从 S 各分量计算 |

**Def. Coords 规则**：
- 固定使用当前激活帧的 U（位移）字段
- scale factor 由前端 UI 滑块控制，范围 0–100，默认 1.0；前端通过 `deform_scale` 参数传给 pick 接口
- 后端计算：`def_coords = orig_coords + U * deform_scale`
- 若当前帧无 U 字段，Def. Coords 列显示 `—`，scale 滑块置灰

**节点模式 S, Mises 语义（唯一定义）**：

> **定义**：节点模式的 S,Mises 值 = **命中单元**（即用户点击位置所在的那个单元）上的 Von Mises 应力，不是该节点所有附属单元的平均。
>
> **原因**：用户点击的是某个单元的表面，"命中单元"是用户行为的直接对象。附属单元列表是拓扑参考信息，不参与应力计算。这与 Abaqus 的 probe 行为一致。

- 从命中单元的 INTEGRATION_POINT 或 ELEMENT_NODAL 位置读取 S11/S22/S33/S12/S13/S23
- Von Mises 公式：`√(0.5 × ((S11-S22)² + (S22-S33)² + (S33-S11)² + 6×(S12² + S13² + S23²)))`
- 若命中单元有多个积分点，返回单元内所有积分点的均值
- 若当前帧无 S 字段，显示 `—`
- 响应里 `result.source_elem_label` 字段注明应力来源的单元号，避免歧义

> **待补充**：Abaqus 默认采用 nodal averaging（把包含该节点的所有相邻单元外推至节点处的应力求平均），结果在节点处更平滑。当前选择"命中单元"语义实现简单、语义清晰，后续若需要支持 averaging 模式，可作为独立选项追加，不影响现有逻辑。

---

### 1.4 单元模式浮窗表格

| 列名 | 含义 | 数据来源 |
|---|---|---|
| Part Instance | 所属实例名 | pick 响应 `instance` |
| Element ID | ODB 单元号 | `odb.elem_label` |
| Type | 单元类型（如 C3D8R） | L1 单元类型表 |
| Attached Nodes | 该单元所有角节点 label 列表 | `odb.elem_node_labels` |
| S, Mises | Von Mises 应力（当前帧） | 命中单元，计算方式同 §1.3 |

---

### 1.5 后端数据来源分析

| 数据 | 状态 | 说明 |
|---|---|---|
| `instance` | ✅ 已实现 | pick 响应已有 |
| `elem_label` | ✅ 已实现 | pick 响应已有 |
| `elem_node_labels` | ✅ 已实现 | pick 响应已有 |
| `elem_type` | ✅ 已实现 | pick 响应已有（`source_etype_str`） |
| `node_label`（正确节点） | ✅ 已实现 | 前端 click handler 计算最近角点索引，传 `node_idx` 给后端 |
| Orig. Coords | ✅ 已实现 | pick 响应扩展，从 L1 几何读 |
| Def. Coords | ✅ 已实现 | 读当前帧 U + 计算 |
| Attached Elements | ✅ 已实现 | L1 `node_to_elements` CSR 映射，旧数据返回 null |
| S, Mises | ✅ 已实现 | 读 S 各分量后端计算 Von Mises |

**关于 `node_label` 的实现**：

前端在 click handler 里，用 Three.js `intersects[0].point`（点击位置世界坐标）与命中三角面的 3 个顶点坐标比较距离，选出最近的角点索引（0/1/2），作为 `node_idx` 传给后端。后端无需任何改动。

---

### 1.6 L1 改动：节点反查映射

**问题**：L1 现在只存 `element → nodes`（单元包含哪些节点），没有 `node → elements`（节点属于哪些单元）的反查。

**决策**：在 L1 **写入阶段**（`l1_pack.py`）直接生成反查映射表，避免在 L3 实时构建。

**存储方案**：写入 `geometry/<inst>.h5` 的 `node_to_elements` 组：

```
geometry/<inst>.h5
  /nodes/labels        [N]       int32   节点标签（已有）
  /nodes/coords        [N, 3]    float32 节点坐标（已有）
  /connectivity/...              (已有)
  /node_to_elements/
    sorted_node_labels [N]       int32   排序后的节点标签（用于 searchsorted 查找）
    elem_label_data    [M_total] int32   所有节点附属单元 label 列表（flatten）
    offsets            [N+1]     int32   CSR 格式偏移，offsets[i]:offsets[i+1] 是第 i 个节点的单元列表
```

**CSR 格式示例**（Compressed Sparse Row）：

```python
# 查询节点 label=1234 的附属单元
i = np.searchsorted(sorted_node_labels, 1234)
elem_labels = elem_label_data[offsets[i]:offsets[i+1]]
```

**为什么不用字典**：百万节点模型下 Python dict 内存开销大，CSR numpy 数组更紧凑，且与 HDF5 直接对应。

**实施位置**：在 `l1_pack.py` 的打包阶段生成（不依赖 Abaqus Python 2.7），构建成本是 O(N_elem × avg_nodes_per_elem)，对 10M 节点模型约 1–2 秒。

**旧 workspace 兼容策略**：

已存在的 workspace（旧 L1 数据）不会自动拥有 `node_to_elements` 组。L3 在处理 `attached_elements` 请求时应先检测该组是否存在：
- 存在 → 正常返回
- 不存在 → `attached_elements` 字段返回 `null`，前端对应列显示 `—（需重跑 L1）`

不强制要求用户重跑 L1，只影响该列显示，不影响其他 pick 功能。

---

### 1.7 后端接口扩展

扩展现有 `GET /api/odb/{odb_id}/query/pick` 响应，新增字段：

**节点模式响应示例**：

```json
{
  "pick_mode": "node",
  "instance": "PART-1-1",
  "render_face_idx": 123,
  "render_face_indices": [123, 124],
  "odb": {
    "node_label": 1234,
    "elem_label": 456,
    "candidate_node_labels": [11, 12, 13],
    "orig_coords": [1.0, 2.0, 3.0],
    "def_coords": [1.002, 2.001, 3.005],
    "attached_elem_labels": [456, 457, 460]
  },
  "result": {
    "field": "S",
    "position": "INTEGRATION_POINT",
    "component": "Mises",
    "value_kind": "odb_raw",
    "raw_value": 123.4,
    "source_elem_label": 456
  }
}
```

> `result.source_elem_label`：注明 S,Mises 来自哪个单元（即命中单元），避免与附属单元列表混淆。

**单元模式响应示例**：

```json
{
  "pick_mode": "element",
  "instance": "PART-1-1",
  "render_face_idx": 123,
  "render_face_indices": [123, 124],
  "odb": {
    "elem_label": 456,
    "elem_type": "C3D8R",
    "elem_node_labels": [11, 12, 13, 14, 15, 16, 17, 18]
  },
  "result": {
    "field": "S",
    "component": "Mises",
    "value_kind": "odb_raw",
    "raw_value": 123.4,
    "source_elem_label": 456
  }
}
```

**请求参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `instance` | str | 实例名 |
| `render_face_idx` | int | 命中三角面索引 |
| `pick_mode` | str | `node` 或 `element` |
| `node_idx` | int (0/1/2) | 节点模式：命中三角面 3 个角点中最近的那个（前端计算后传入，后端已支持） |
| `step` | str | 当前步名 |
| `field` | str | 结果字段（如 `S`、`U`） |
| `frame_idx` | int | 帧索引 |
| `include_coords` | bool | 是否返回坐标（默认 false） |
| `deform_scale` | float | 变形缩放系数（默认 1.0，仅 `include_coords=true` 时生效） |

---

### 1.8 实施计划

**Phase 1（最小可用）** ✅ 已完成
- [x] 前端：Probe 模式开关（Node / Element 按钮）
- [x] 前端：Hover Tooltip（mousemove + raycaster + 气泡，一期只高亮不显示 ID）
- [x] 前端：点击调用 `/query/pick`，结果追加进浮窗表格（只显示当前已有字段）
- [x] 前端：click handler 计算命中三角面 3 顶点中距点击位置最近的角点索引，作为 `node_idx`（0/1/2）传给 `/query/pick`

**Phase 2（后端扩展 pick 响应）** ✅ 已完成
- [x] 后端：pick 响应增加 `orig_coords`（从 L1 几何读）
- [x] 后端：pick 响应增加 `def_coords`（当前帧 U × deform_scale + Orig）
- [x] 后端：pick 响应增加 S,Mises 计算（命中单元，积分点均值）
- [x] 前端：表格补全 Orig. Coords、Def. Coords、S,Mises 列
- [x] 前端：deform_scale 滑块 UI

**Phase 3（L1 反查映射 + 前端 label 缓存）** ✅ 已完成
- [x] 后端：`l1_pack.py` 生成 `node_to_elements` CSR 映射
- [x] 后端：pick 响应增加 `attached_elem_labels`（旧数据返回 null）
- [x] 前端：Attached Elems 列，null 时显示 `—（需重跑 L1）`
- [ ] 前端（可选，未实现）：拉取 face-label-map，hover 直接显示真实 label

---

## 2. View Cut（截面切割）

### 2.1 功能描述

用户通过 UI 控制一个切割平面，实时裁掉平面一侧的模型，查看截面。

**平面参数**：
- 切割轴：X / Y / Z（轴对齐，最常用）或自定义法向量
- 切割位置：沿轴方向的距离（通过滑块控制）
- 方向：保留正侧 / 负侧
- 激活 / 停用开关

---

### 2.2 第一阶段：Three.js Clipping Plane（空心切面）

**状态**：✅ 已完成

**原理**：Three.js `WebGLRenderer` 支持全局裁剪平面。设置后，GPU 在光栅化阶段自动丢弃平面一侧的所有片元，不需要修改几何数据。

```javascript
const clipPlane = new THREE.Plane(new THREE.Vector3(1, 0, 0), 0);  // X=0 平面，保留 X>0 侧

renderer.clippingPlanes = [clipPlane];
renderer.localClippingEnabled = true;

// 滑块控制（拖动时实时更新，纯 GPU 操作，无后端调用）
sliderX.addEventListener('input', e => {
  clipPlane.constant = -parseFloat(e.target.value);
});
```

**UI 控件**：
- Activate / Deactivate 开关（checkbox）
- 切割轴选择（X / Y / Z 单选）
- 位置滑块（范围由模型全局 bbox 决定）
- 翻转方向按钮（Flip Direction）
- 补面开关按钮（"补面" toggle，控制 Phase 2 截面填充网格的显示/隐藏）

**滑块范围的数据来源**：从已有的 `GET /api/odb/{odb_id}/meta/overview` 接口获取。该接口返回的 `data.instances` 数组中每个实例都带有 `bbox` 字段（来自 `manifest.db` 的 `instances` 表）。前端对所有实例的 bbox 取并集，得到全局模型范围，再按选中轴取 min/max 并留 10% 余量作为滑块范围。无需新增接口。

**局限性**：切面处模型是"开口"的，内部空心可见。Phase 1 接受此限制，Phase 2 用截面 Mesh 填补。

**优点**：
- 零后端调用，滑块拖动实时响应
- Feature Edges、Element Mesh Edges、截面 Mesh 等所有图层均自动跟随裁剪（Three.js 全局 clipping）

---

### 2.3 第二阶段：后端截面网格生成（网格填充）

**状态**：✅ 已完成

#### 性能 POC 结果

在实施前，使用真实 L1 HDF5 数据做了性能验证：

| 指标 | 实测值 |
|---|---|
| 模型规模 | 32,469 个体积单元（C3D4 + C3D6 + C3D8R） |
| 单元类型 | C3D4 / C3D6 / C3D8R（含 Shell S4R 已被过滤） |
| 截面计算耗时 | ~48 ms（含 HDF5 读取，Python NumPy 向量化） |
| 结论 | 实时可行，无需 L2 体单元索引（选择方案 A） |

**方案选择**：

| | 方案 A（最终选择）| 方案 B |
|---|---|---|
| 体单元空间索引 | L3 每次从 L1 全量扫描体单元 | L2 新增体单元 octree 预计算 |
| 响应时间 | ~48 ms（32K 单元实测）| 毫秒级粗筛，总体 <3 秒 |
| 数据改动 | 无，直接读 L1 HDF5 | L2 新增 `<inst>_volume.h5`，需重跑 L2 |
| 适用场景 | 当前规模完全可行 | 大模型（>100万单元）时考虑 |

> 方案 A 在 32K 单元模型上实测 48ms，满足实时响应（<200ms）要求。若未来遇到百万单元量级，再评估方案 B。

#### API 接口规格

**端点**：`GET /api/odb/{odb_id}/results/section-mesh`

**请求参数**：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `instance` | str | 必填 | 实例名 |
| `axis` | `X`\|`Y`\|`Z` | `Z` | 切割平面法线方向 |
| `position` | float | `0.0` | 切割平面沿轴偏移（世界坐标） |

**响应**：L3BE 二进制载荷（`application/octet-stream`），包含两个数组段：

| 段名 | 形状 | 类型 | 含义 |
|---|---|---|---|
| `vertices` | `[T×3, 3]` | float32 | 截面填充三角面（每三行 = 一个三角形） |
| `edge_verts` | `[E×2, 3]` | float32 | 截面多边形轮廓线段（每两行 = 一条线段） |

**响应 Headers**：

| Header | 含义 |
|---|---|
| `X-Payload-Type` | `section_mesh_v1` |
| `X-Layout-Version` | `1` |
| `X-Tri-Count` | 三角面数量（0 表示平面不与模型相交） |
| `X-Edge-Count` | 轮廓线段数量 |
| `X-Axis` | 切割轴（`X`/`Y`/`Z`） |
| `X-Position` | 切割位置 |

#### 算法说明

**体积单元类型支持**（按角节点数）：

| 单元类型 | 角节点数 | 棱边数 | 说明 |
|---|---|---|---|
| C3D4 | 4 | 6 | 四面体，直接使用 4 个角节点 |
| C3D6 | 6 | 9 | 楔形（三棱柱），直接使用 6 个角节点 |
| C3D8 / C3D8R | 8 | 12 | 六面体，直接使用 8 个角节点 |
| C3D10 | 10 | 6 | 高阶四面体，只取前 4 个角节点（Abaqus 排序） |
| C3D20 / C3D20R | 20 | 12 | 高阶六面体，只取前 8 个角节点 |

Shell / Beam 单元（etype 名称以 S、B、M、STRI、SAX、SC 开头）自动跳过，不参与截面计算。

**截面计算流程**（对每批同类单元向量化处理）：

```python
# 1. 计算所有节点到切割平面的有符号距离
dist = coords @ normal - position   # [N_nodes]

# 2. 筛选与平面相交的单元（d_min < 0 < d_max）
elem_dist = dist[conn]              # [N_elem, n_corner]
hit_idx = where((elem_dist.min(1) < 0) & (elem_dist.max(1) > 0))

# 3. 对每个命中单元，计算各棱边与平面的交点
for each edge (a, b):
    if sign(da) != sign(db):
        t = da / (da - db)          # 线性插值参数
        pt = va + t * (vb - va)     # 交点坐标

# 4. 将交点按极角排序（保证截面多边形为 CCW 凸序）
centroid = mean(pts)
angles = arctan2(delta @ v, delta @ u)  # 局部 2D 坐标
pts = pts[argsort(angles)]

# 5. 扇形三角化（fan triangulation）
for i in [1, n-2]:
    tri = [pts[0], pts[i], pts[i+1]]

# 6. 提取多边形轮廓边（用于显示截面网格线）
for i in [0, n-1]:
    edge = [pts[i], pts[(i+1) % n]]
```

#### 前端实现

**交互触发模型**：

- 滑块拖动时：只更新 Phase 1 的 clipping plane（GPU 实时，空心切面）
- 拖动结束（`mouseup` / `touchend`）时：触发后端截面计算请求
- 新请求到达时取消上一个未完成请求（`AbortController`）
- 后端返回后：更新截面 Mesh 和轮廓线，替换上一帧截面

**两个独立的 Three.js 对象**：

| 对象 | 材质 | 说明 |
|---|---|---|
| `sectionMesh` | `MeshBasicMaterial`，灰蓝色，双面，`polygonOffset` | 截面填充面 |
| `sectionEdges` | `LineBasicMaterial`，黑色 | 截面处单元轮廓线 |

**Z-fighting 处理**：截面 Mesh 与裁剪面恰好共面，若不处理会产生闪烁（Z-fighting）。通过 Three.js `polygonOffset` 解决：

```javascript
const fillMat = new THREE.MeshBasicMaterial({
    color: 0xc8d0dc,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -4,
});
```

**补面开关按钮**：UI 中 View Cut 面板提供"补面"按钮（toggle），点击后切换 `sectionMesh.visible`，可在有无填充面之间切换。轮廓线（`sectionEdges`）始终保持显示，方便对比。

**模型显示风格（Abaqus 风格）**：
- 基础模型：亮绿色（`rgb(56, 184, 56)` 近似）
- 单元网格线：黑色（`0x111111`）
- 截面填充面：灰蓝色（`0xc8d0dc`）
- 截面轮廓线：黑色（`0x111111`）

---

### 2.4 实施计划

**Phase 1（纯前端，空心切面，无后端依赖）** ✅ 已完成
- [x] View Cut UI 面板：Activate/Deactivate、轴选择、位置滑块、翻转按钮
- [x] Three.js `renderer.clippingPlanes` 接入
- [x] 从 `/api/odb/{odb_id}/meta/overview` 的 `data.instances[].bbox` 字段取并集，设置滑块范围
- [x] Feature Edges、Element Mesh Edges 自动跟随裁剪（全局 clipping 覆盖所有 Mesh）

**Phase 2（后端截面网格，已完成）** ✅ 已完成
- [x] POC：真实模型下验证 L3 全量扫描体单元的耗时（32K 单元 48ms，实时可行）
- [x] 确定不需要 L2 体单元预处理索引（选择方案 A）
- [x] 实现截面切割接口 `GET /results/section-mesh`（L3BE 二进制响应）
- [x] 实现体单元与平面求交算法（NumPy 向量化 + 逐单元 Python loop）
- [x] 前端接入截面 Mesh（独立图层 + `mouseup` 触发 + `AbortController`）
- [x] 截面处显示单元轮廓线（`LineSegments`）
- [x] Z-fighting 修复（`polygonOffset`）
- [x] 补面开关按钮（"补面" toggle）
- [x] CORS expose 新增 `X-Tri-Count`、`X-Edge-Count`、`X-Axis`、`X-Position`

**Phase 3（截面云图，待规划）**
- [ ] 截面 Triangle Soup 携带节点行号映射
- [ ] 从 L1 结果 HDF5 插值截面颜色
- [ ] 前端接入颜色 buffer

---

## 附：两个功能的依赖关系与优先级

```
Probe Phase 1 ─── 需最小后端修正（节点选择逻辑）✅
Probe Phase 2 ─── 需 L3 pick 扩展（coords + S,Mises）✅
Probe Phase 3 ─── 需 L1 node_to_elements 映射（l1_pack.py 改动）✅

View Cut Phase 1 ──────────────── 纯前端，无后端依赖 ✅
View Cut Phase 2 ─── 经 POC 验证，无需 L2 体单元索引，直接 L3 全量扫描 ✅
View Cut Phase 3 ─── 截面云图，待规划
```
