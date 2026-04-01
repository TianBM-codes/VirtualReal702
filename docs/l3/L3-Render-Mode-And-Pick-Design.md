# L3 渲染模式与点选设计

**版本**：v1
**状态**：待 Review
**涉及文件**：
- `src/l3/schemas/query.py`
- `src/l3/services/query_service.py`
- `src/l3/services/result_service.py`
- `src/l3/api/routes/results.py`
- `tools/viewer.html`

---

## 背景

L2 层将有限元网格三角化为 Triangle Soup 格式（`[Rf, 3, 3] float32`，完全展开，无共享顶点）。每个渲染三角片（render face）有唯一的 `render_face_idx`，通过以下两个映射关联回有限元数据：

- `source_elem_row[Rf]`：每个渲染面片 → 对应单元在 L1 HDF5 中的行号
- `source_node_rows[Rf, 3]`：每个渲染面片 → 3个节点在 L1 节点表中的行号

---

## 需求一：两种渲染模式

### 背景

有限元结果字段分两类：

| 类型 | 典型字段 | 存储位置 | 语义 |
|------|---------|---------|------|
| 节点型 | U（位移）、NT（温度） | NODAL | 每个节点一个值，单元内连续变化 |
| 单元型 | STH（截面厚度）、STATUS（单元状态） | ELEMENT_NODAL / INTEGRATION_POINT | 每个单元一个值，单元间可以突变 |

这两类字段在视觉上的需求完全不同：
- **节点型**需要颜色在单元内部平滑插值（Smooth 模式）
- **单元型**需要同一单元颜色完全一致，相邻单元可以完全不同（Flat 模式）

### 实现方案

在 `/api/odb/{odb_id}/results/frame-colors` 接口加入 `mode` 查询参数（`smooth` 或 `flat`，默认 `smooth`）。

#### Smooth 模式（NODAL 字段）

```
scalar_vertex[Rf*3] = scalar_node[source_node_rows.ravel()]
```

每个顶点取其对应节点的标量值，Three.js 在三角形内自动插值，颜色平滑过渡。

#### Flat 模式（NODAL 字段）

```
face_node_vals[Rf, 3] = scalar_node[source_node_rows]      # 每个面片的3个节点值
face_vals[Rf]         = face_node_vals.mean(axis=1)         # 每个面片取节点均值

# 按单元分组，求单元内所有面片的均值
unique_elems, inverse = np.unique(source_elem_row, return_inverse=True)
elem_mean[N_elems]    = groupby_mean(face_vals, inverse)

scalar_vertex[Rf*3]   = np.repeat(elem_mean[inverse], 3)
```

同一个单元的所有面片得到相同的颜色值，相邻单元颜色硬切。

#### ELEMENT_NODAL / INTEGRATION_POINT 字段

无论 mode 是什么，结果本身就是按单元存的，直接 `np.repeat(scalar_face, 3)` 即可。不受 mode 参数影响。

### 前端

Apply Colors 区域加 Render Mode 下拉框（Smooth / Flat），点击 Apply Colors 时将 `mode` 追加到请求 URL。

---

## 需求二：点选高亮整个单元

### 背景

用户点击屏幕上的某个三角片，Three.js 的 Raycaster 返回的是 `render_face_idx`（三角片编号）。但三角化只是为了显示，用户真正关心的是**有限元单元**。一个单元可能被分解成多个三角片（例如六面体 C3D8R 表面一个四边形面 → 2个三角片）。

因此高亮时应该高亮**该单元的所有三角片**，而不仅仅是被点击的那一个。

### 实现方案

#### 后端：`PickResponse` 新增字段

```python
class PickResponse(BaseModel):
    instance: str
    render_face_idx: int          # 被点击的三角片
    elem_label: int               # 单元标签（ODB 原始编号）
    node_labels: List[int]        # 单元角节点标签列表
    current_value: Optional[float] = None
    render_face_indices: List[int] = []   # 该单元所有三角片的 render_face_idx 列表
```

#### 后端：`query_service.pick()` 计算逻辑

`source_elem_row` 中的行号是**各 etype 组内独立编号的**（L2 ingest 直接 concatenate，没有全局偏移）。`S4R` 的 row=5 和 `C3D8R` 的 row=5 是完全不同的两个单元。因此必须同时匹配 etype：

```python
elem_row  = int(src_map[render_face_idx])
etype_key = etype_arr[render_face_idx]          # bytes, e.g. b"S4R\x00..."

elem_face_indices = np.where(
    (src_map == elem_row) & (etype_arr == etype_key)
)[0].tolist()
```

`src_map` 即 `render_source_elem_row[instance]`，形状 `[Rf]`。

#### 前端：高亮所有面片

```javascript
const facesToHighlight = d.render_face_indices.length > 0
  ? d.render_face_indices
  : [faceIdx];   // fallback

for (const fi of facesToHighlight) {
  for (let v = 0; v < 3; v++) {
    const base = (fi * 3 + v) * 3;
    colorAttr.array[base]   = 1.0;   // R (黄色)
    colorAttr.array[base+1] = 0.85;  // G
    colorAttr.array[base+2] = 0.1;   // B
  }
}
colorAttr.needsUpdate = true;
```

---

## 需求三：鼠标模式切换

### 背景

旋转模型时左键拖动，点选时左键点击，二者会互相干扰（旋转操作结束时也会触发 click 事件）。

### 实现方案

在前端维护一个 `pickMode` 布尔变量，通过 Navigate / Pick 切换按钮控制：

```javascript
function onCanvasClick(event) {
  if (!pickMode || !mesh) return;   // Navigate 模式下直接忽略点击
  // ... raycaster 逻辑
}
```

默认为 Navigate 模式，用户主动切换到 Pick 模式才能点选单元。

---

## 数据流总览

```
用户点击三角片
    ↓
Three.js Raycaster → render_face_idx
    ↓
GET /query/pick?render_face_idx=N
    ↓
query_service:
  elem_row  = src_map[N]
  etype_key = etype_arr[N]           ← 必须同时匹配 etype，elem_row 是 per-etype-local
  render_face_indices = np.where(
      (src_map == elem_row) & (etype_arr == etype_key)
  )[0]                               → [M 个属于该单元的面片]
  HDF5 → elem_label, node_labels
    ↓
PickResponse { elem_label, node_labels, render_face_indices: [M个] }
    ↓
前端高亮 M 个三角片（整个单元）
```

---

## 已确认问题及修复（Review 结果）

### 问题 1（已修复）：etype 跨组行号不唯一
`source_elem_row` 在 L2 ingest 时直接 `np.concatenate` 各 etype 组的 `face_elem_idx`，**没有全局偏移**（`src/l2/ingest.py:138`）。不同 etype 的单元行号从 0 开始独立计数，直接按 `elem_row` 匹配会误命中其他 etype 的单元。

**修复**：pick 时同时匹配 `etype_arr == etype_key`；flat 模式和 bbox 均改用 `(etype_idx, elem_row)` 复合 key。

### 问题 2（已修复）：Flat 模式跨 etype 串色
同上根因。`np.unique(src_elem_row)` 会把不同 etype 但 row 相同的单元分进同一组求均值，导致云图错误。

**修复**：改用 `composite = et_idx * max_er + src_elem_row` 作为 `np.unique` 的输入。

### 问题 3（已修复）：bbox elem_count 多计
同上根因，`np.unique(elem_rows)` 对多 etype 模型计数偏低（合并了不同 etype 的单元）。

**修复**：bbox 也改用复合 key 去重。

### 问题 4（已修复）：frame_idx 负数绕过校验
负数会被 NumPy/HDF5 当合法倒数索引，正数越界直接抛底层异常而非统一的 ValidationError。

**修复**：在 `frame_colors` 入口处提前校验 `frame_idx < 0`。

---

## 遗留问题

- **ELEMENT_NODAL 的 Flat vs Smooth 语义**：ELEMENT_NODAL 字段在 Smooth 模式下仍返回单元均值，因为该位置类型本身没有精确的节点级插值信息，此行为是合理的。
