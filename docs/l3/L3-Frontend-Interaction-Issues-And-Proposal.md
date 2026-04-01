# L3 前端交互问题与方案建议

> **状态：已实现（2026-03-29）**
> 本文描述的三个问题均已修复：
> - 框选 AABB 问题 → 改为前端屏幕空间检测 + 新接口 `POST /query/render-faces`
> - 网格线重复 → `element-mesh-edges` 后端去重
> - pick 高亮语义 → 单元模式显示线框，节点模式显示球体
>
> 本文保留作为历史设计记录，当前实现以代码和 `L3-API-Contract.md` 为准。

---

## 目标

本文整理当前前端交互相关的 3 个问题：

1. 框选会选出过大区域
2. 当前显示的是三角面片线，不是单元网格线
3. pick 返回的节点号 / 单元号 / 结果值语义需要回到 ODB 语义

本文只给出**原因分析**和**建议方案**，不涉及代码修改。

---

## 1. 框选会选出很大一片

### 当前原因

当前 viewer 的框选流程是：

1. 前端拖出屏幕矩形
2. 把屏幕矩形四角反投影成近远平面上的 8 个世界点
3. 对这 8 个点取世界坐标 AABB
4. 后端按该 AABB 做 `intersect/contained` 判断

这个做法的问题是：

- 用户画的是**屏幕空间矩形**
- 当前实现实际使用的是**世界空间 AABB**
- 在透视相机下，屏幕矩形对应的是一个视锥体，不是一个轴对齐包围盒
- 视锥体被扩大成世界 AABB 后，通常会显著放大选择体积，导致多选

### 建议方案：分两步演进

**第一步（已完成）：接入 octree 粗筛，提升世界坐标 bbox 性能**

L2 octree 已在 `src/l2/ingest.py` 实现（`build_octree()`，`max_depth=8`，`leaf ≤ 1000` 面片，flat array 写入 HDF5）。L3 现已在 `state.py` 的 `ModelIndex` 中加载 octree，并在 `query_service.py` 的 `bbox` 查询中先走 octree AABB 粗筛，再对候选面片做精筛。

当前状态：

- 数据生产：L2 已完成
- 数据加载：L3 已完成
- 查询接入：L3 已完成
- 当前框选语义：仍是世界坐标 AABB

第一步的收益是：在不改变现有 bbox 接口语义的前提下，显著减少精筛面片数量，提升大模型下的查询性能。

**第二步（后续）：升级为屏幕空间框选**

职责划分：

- 前端：负责交互、显示和高亮
- L3：负责候选筛选与命中计算

流程：

1. 前端发送：`screen_rect`、`viewport`、相机参数（position/target/up/fov/near/far）、`mode=intersect|contained`
2. L3 用 octree 对相机视锥体做粗筛
3. L3 将候选三角面顶点投影回屏幕空间
4. 在屏幕空间判断三角形与矩形是否相交

判定：
- `intersect`：三角形与矩形有交集即命中
- `contained`：三角形 3 个顶点都在矩形内才命中

这样结果会和用户视觉直觉一致，同时计算仍然放在后端。第二步建立在当前 octree 粗筛已经接入的基础上继续演进。

---

## 2. 当前显示的是三角线，不是单元边界线

### 当前原因

现在 viewer 使用的是 `THREE.WireframeGeometry(mesh.geometry)`。

但当前 `mesh.geometry` 是 Triangle Soup：

- 一个四边形面会被拆成两个三角形
- 一个六面体表面单元会被拆成多个三角面

因此 `WireframeGeometry` 画出的必然是三角剖分边，包括人工生成的对角线，不是有限元单元真实边界。

---

### 两种边线的区别

在讨论修复方案前，需要先区分两种性质不同的边线：

**`feature_edges`（特征边 / 外轮廓增强线）**

- L2 已在 `src/l2/ingest.py` 实现，写入 `geometry/<inst>_surface.h5` 的 `feature_edges/` 组
- 定义：边界边（只属于一个表面三角形）+ 折角边（两个三角形夹角 ≥ 30°）
- 用途：突出形体轮廓、识别折角结构，增强模型可读性
- **不是**完整单元网格：共面的单元间分界线会被过滤掉（夹角 < 30°）

**`element_mesh_edges`（完整单元网格边）**

- L2 现已规划通过 `compute_all_surface_edges` 实现（需要重跑 L2 ingest 才会产出新数据）
- 定义：所有表面单元的每条边，去掉内部共享边后的完整单元边界集合
- 用途：严格显示每一个单元的轮廓，包括共面单元之间的分界线
- 关键规则：
  - 不做角度过滤，共面单元分界边必须保留
  - 只排除同一原始表面面在三角化时产生的内部对角线
  - 对 quad 场景，可通过共享边两侧三角形的 `(surf_tri_er, surf_tri_fs)` 完全相同来识别并排除内部对角线
- 成本：数据量比 `feature_edges` 大，且旧的 `_surface.h5` 不会自动拥有该 group

---

### 建议方案：先用 feature_edges 作为实验性预览

L2 的 feature_edges 数据已经就绪，L3 现已新增实验性端点下发该 buffer，可作为**形体增强预览线**快速验证效果：

### 新需求：模型需要支持两种边线显示方式

前端新需求可整理为两个显示模式：

1. **整体模型的外轮廓线框 / 形体增强线**
   - 对应 `feature_edges`
   - 目标是增强轮廓与折角可读性
   - 允许省略共面单元之间的内部边

2. **所有单元的边界线**
   - 对应 `element_mesh_edges`
   - 目标是显示完整有限元网格
   - 必须保留共面单元之间的分界线

因此，这两个显示模式不能共用同一套 edge 数据，也不应在命名上混淆。

当前建议的接口语义是：

- `/geometry/{instance}/feature-edges`
  - 对应整体模型外轮廓线框 / 形体增强线
- `/geometry/{instance}/element-mesh-edges`
  - 对应所有单元的真实边界线

两个端点的响应格式可以保持一致，但语义不能互换。

输出格式：
- `edge_positions [E*2, 3] float32`（每条边两个端点坐标，直接可用于 `LineSegments`）

前端渲染方式：
- 使用单独的 `LineSegments`，与三角面 mesh 分层显示
- 提供显示开关

**需要明确的口径**：
- `feature_edges` 对应“整体模型外轮廓线框 / 形体增强线”
- `element_mesh_edges` 对应“所有单元的边界线”
- 如果最终目标是严格显示每一个单元边界（包括共面单元分界线），`feature_edges` 一定不够
- `element_mesh_edges` 需要通过新的 L2 edge 数据生成；旧数据不会自动补齐
- 使用 `element_mesh_edges` 前，需要确认对应实例已经重新跑过 L2 ingest

---

## 3. pick 的节点号 / 单元号 / 值要回到 ODB 语义

### 3.1 当前情况

### 单元号

当前 `elem_label` 已经是从 L1 HDF5 中读取的 ODB 原始单元号，不是 render row。

### 节点号

当前 `node_labels` 也是从 L1 连接关系映射回去的 ODB 原始节点号。

但问题在于：

- 当前返回的是**整个单元的角节点**
- 不是“当前点击面对应的节点”
- 也不是“当前点击到的那个节点”

### 结果值

当前 `current_value` 语义不够严格：

- 只走 `NODAL` 路径
- 取的是点击面第一个顶点节点的值
- 前端现在也没有把 `step/field/frame/component` 完整传给 `/query/pick`

因此现在的 pick 值不能视为完整的 ODB pick 结果。

---

## 3.2 建议的 pick 模式拆分

用户希望“都要，按钮区分”，建议拆成两种模式：

- `pick_mode = element`
- `pick_mode = node`

前端通过按钮切换，后端按模式返回不同主语义。

---

## 3.3 PickResponse 结构建议

建议保留两层结构：

- `odb`：原始拓扑/编号语义
- `result`：结果语义

### Element Pick

用途：

- 高亮整个单元
- 返回该单元的 ODB 语义信息

建议结构：

```json
{
  "pick_mode": "element",
  "instance": "PART-1-1",
  "render_face_idx": 123,
  "render_face_indices": [123, 124],
  "odb": {
    "elem_label": 456,
    "elem_node_labels": [11, 12, 13, 14],
    "face_node_labels": [11, 12, 13]
  },
  "result": {
    "field": "S",
    "position": "INTEGRATION_POINT",
    "component": "S11",
    "raw_values": [...],
    "display_value": 12.3,
    "value_kind": "odb_raw"
  }
}
```

> **[Claude 注]** `face_node_labels` 的数据链路当前尚不完整。要返回"当前点击三角面对应的原始面节点"，需要：
> `render_face_idx` → 该三角面所属的原始面 → 原始面的 corner node labels。
> 但目前 L2 render buffer 只存了 `source_elem_row`（单元行号）和 `source_etype_str`（单元类型），
> 没有存"三角面 → 原始面"的反查映射，`hdf5_repo` 也没有对应查询。
> 因此这个字段在后端实现前是虚的，需要先确认 L2 是否补存该映射，再决定是否列入协议。

### Node Pick

用途：

- 选中一个节点
- 返回 ODB 节点号以及该节点结果

建议结构：

```json
{
  "pick_mode": "node",
  "instance": "PART-1-1",
  "odb": {
    "node_label": 1234,
    "elem_label": 456,
    "candidate_node_labels": [11, 12, 13]
  },
  "result": {
    "field": "U",
    "position": "NODAL",
    "component": "U1",
    "raw_value": 0.0023,
    "value_kind": "odb_raw"
  }
}
```

---

## 3.4 结果值语义建议

建议严格区分：

- `odb_raw`：ODB 原始值
- `interpolated`：后续算法得到的插值值

### NODAL

- `element pick`：返回当前点击面 3 个节点的 ODB 原始值
- `node pick`：返回被选中节点的 ODB 原始值
- 若后续需要“点击点插值值”，应单独新增 `interpolated_value`

### ELEMENT_NODAL / INTEGRATION_POINT

先按当前需求返回：

- ODB 原始值
- 例如单元积分点结果、单元节点结果

后续如需接入额外插值算法，应新增独立字段，不要覆盖 `raw_value`
语义。

---

## 3.5 接口参数建议

建议 `/query/pick` 与 `/results/frame-colors` 参数统一：

- `step`
- `field`
- `frame_idx`
- `component`
- `pick_mode`

避免一处用 `frame`，另一处用 `frame_idx`，造成前后端和文档不一致。

> **[Claude 注]** 统一后应使用 `frame_idx`（整数下标），而非裸 `frame`。
> `frame` 容易与 frame value（帧对应的时间值或频率值，是浮点数）混淆；
> `frame_idx` 明确表示"第几帧"的下标语义，与后端 HDF5 切片 `ds[frame_idx]` 直接对应。

---

## 推荐实施顺序

1. **octree 接入 → bbox 性能提升（已完成）**：L3 已在 `ModelIndex` 加载 octree，`bbox` 查询已先走 octree 粗筛。当前框选语义仍保持世界坐标 AABB。
2. **feature_edges 外轮廓模式（已完成后端端点）**：L3 已新增端点下发 feature_edges buffer；下一步是前端接入 `LineSegments`，作为“整体模型外轮廓线框 / 形体增强线”显示模式。
3. **pick 协议升级**：定义 `pick_mode` 与新的 `PickResponse`（`odb + result` 两层结构），前端接入按钮切换。
4. **bbox 升级为屏幕空间框选**：在现有 octree 粗筛基础上，将 bbox 从世界 AABB 升级为屏幕矩形投影方案。
5. **element_mesh_edges（第二种边线模式）**：若前端需要“所有单元的边界线”，则在 L2 补 `compute_all_surface_edges`，L3 新增对应端点。

---

## 总结

- 框选问题：根因是把屏幕矩形近似成了世界 AABB；当前已接入 octree 粗筛提升性能，后续再升级为屏幕空间筛选
- 边线问题：根因是对 Triangle Soup 做 wireframe；现已明确拆成两种模式：`feature_edges` 用于整体模型外轮廓线框 / 形体增强线，`element_mesh_edges` 用于所有单元边界线，二者不能混用
- pick 问题：根因是返回语义过粗、结果值语义不完整；建议拆成 `element/node` 两种模式，返回结构整理为 `odb + result` 两层
