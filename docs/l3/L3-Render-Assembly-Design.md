# L3 渲染数据组装与交互速查设计

## 目标

本文档单独描述 Layer 3 在“大模型前端轻量显示”模式下的职责。

这里的 L3 不只是查询 API 壳子，而是：

- 基于 L1 原始语义数据和 L2 渲染桥接数据
- 快速响应点击、hover、框选、结果切换等高频交互
- 按请求组装前端可直接消费的渲染二进制流

本文重点回答两个问题：

1. 现有 L1/L2 设计能否支撑这种 L3？
2. 前端的“显示壳子模型文件”应由谁生成？

结论先行：

- **可以支撑。** 现有 v5 文档里的 L1/L2 数据已经具备主要条件。
- **显示壳子应由后端生成。** 更准确地说，应由 **L2 预生成基础壳子几何**，L3 再按查询条件组装为最终下发给前端的渲染 buffer。

---

## 1. 设计边界

本文讨论的不是“服务端远程出图”，而是：

- 后端负责重计算和语义映射
- 前端保留 Three.js，仅负责显示和交互采集

也就是说，前端不再从原始仿真结果自行推导最终渲染状态，而是直接消费 L3 下发的渲染 buffer。

### 1.1 前端负责什么

- Three.js 场景、相机、轨道控制
- 二进制流转 `BufferGeometry`
- 材质切换、局部 UI 状态
- 点击、hover、框选等交互事件上传

### 1.2 L3 负责什么

- 结果值到渲染面片/顶点的快速映射
- 变形后坐标组装
- 集合过滤与 focus mask 组装
- 点击命中到工程语义的快速翻译
- bbox / set / field / frame 维度上的快速查询

---

## 2. L1/L2 是否足够支撑 L3

### 2.1 已经具备的关键条件

根据主架构文档，当前设计已经有以下关键基础：

- L2 `Triangle Soup`：`[Rf, 3, 3] float32`
- L2 平滑法向：与 `render_face_idx` 对齐
- L2 `render_face_idx`：Instance 内稳定编号
- L2 `source_elem_row [Rf]`
- L2 `source_node_rows [Rf, 3]`
- L2 八叉树 / 分区信息
- L1 节点/单元 label 升序数组
- L1 结果数据按 `[frame, entity, ...]` 规则数组存储
- L3 启动时已有 `searchsorted` 类标签索引设计

这些条件意味着，L3 理论上已经能完成：

- `render_face_idx -> elem_row -> elem_label`
- `render_face_idx -> node_rows -> node_label`
- `render_face_idx -> 当前帧结果值`
- `set_name -> render_face_idx 子集`
- `bbox -> render_face_idx / elem_label`

所以从数据结构上说，**L1/L2 足以支撑一个“渲染数据组装 + 交互速查”的 L3。**

### 2.2 当前最关键的能力缺口

现有设计还差的不是底层文件，而是 L3 的职责定义和若干内存索引的正式化。

需要明确固化的内容主要有：

- 每个 Instance 的 `elem_label -> elem_row` 内存索引
- `render_face_idx -> render_row` 是否天然等价的约束
- `source_elem_row` 在混合单元类型下的解释边界
- `source_node_rows` 对应的是角节点还是渲染顶点节点
- L3 输出 buffer 的统一二进制协议

也就是说，**缺的是“L3 运行时组织方式”，不是“L1/L2 存不出来”。**

---

## 3. 显示壳子模型应由谁生成

结论：

- **基础显示壳子由 L2 生成**
- **面向具体视图和结果状态的最终显示 buffer 由 L3 生成**

### 3.1 为什么不应由前端生成壳子

如果前端自己从原始连接关系生成显示壳子，会遇到这些问题：

- 表面提取成本高
- 三角化规则复杂，前后端难保持一致
- `render_face_idx` 无法稳定对齐
- 结果映射链会分散到浏览器里，内存和 CPU 压力大

这和当前项目“大模型、前端轻量”的方向相反。

### 3.2 正确分层

建议这样理解“壳子模型”：

- **L2 壳子**：稳定的基础渲染几何
  - surface triangles
  - smooth normals
  - render-face 到 source-row 的映射

- **L3 壳子**：带状态的下发包
  - 某个 set 过滤后的面片子集
  - 某个 frame 的颜色/标量
  - 某个变形倍率下的 positions
  - 某个 focus 状态下的 mask

因此，理论上前端拿到的“显示壳子”也应该是后端生产的，只是其中：

- 几何基础壳子主要来自 L2
- 查询态、渲染态组装来自 L3

---

## 4. L3 的核心定位

建议将 L3 明确为：

**面向前端交互的语义速查与渲染数据组装层**

它不做 ODB 忠实转储，不做一次性几何预处理，但要做高频、低延迟的运行时拼装。

---

## 5. L3 需要持有的内存索引

### 5.1 渲染命中索引

每个 Instance：

- `render_face_idx -> source_elem_row`
- `render_face_idx -> source_node_rows[3]`
- `render_face_idx -> partition_id`
- `render_face_idx -> chunk_id`

用途：

- 点击拾取
- hover tooltip
- 渲染子集过滤
- 分区 / chunk 请求

### 5.2 标签定位索引

每个 Instance：

- `node_labels_sorted`
- `elem_labels_sorted`
- `searchsorted(node_label) -> node_row`
- `searchsorted(elem_label) -> elem_row`

用途：

- set label 转 row
- 用户输入 label 直接定位
- 结果和几何的语义回查

### 5.3 集合速查索引

- `set_name -> elem_rows`
- `set_name -> node_rows`
- `set_name -> render_rows`
- `user_set_id -> render_rows`

用途：

- 集合过滤
- focus 模式
- 基于集合的统计与导出

### 5.4 空间索引

- octree：面片粗筛
- cKDTree：节点或空间精筛

用途：

- bbox 查询
- 框选
- 邻域查找

---

## 6. L3 的主要映射链

### 6.1 点击拾取

前端传：

- `odb_id`
- `instance`
- `render_face_idx`
- 可选：`step / frame / field / component`

L3 链路：

`render_face_idx`
-> `source_elem_row`
-> `elem_label`
-> L1 几何 / 截面 / 材料 / 集合信息
-> 若请求结果值，再到对应结果 dataset 切片

返回：

- `elem_label`
- `instance`
- `material`
- `section`
- `set_names`
- `current_value`
- 可选 `centroid / bbox / node_labels`

### 6.2 NODAL 平滑着色

L3 链路：

`render_face_idx`
-> `source_node_rows[3]`
-> L1 `/NODAL/<inst>/data[frame_idx, node_rows, comp]`
-> 组装逐顶点标量或颜色

可选两种下发策略：

- 直接下发 `scalar_per_vertex`
- 直接下发 `color_per_vertex`

建议优先下发 `scalar + range`，前端 colormap 可本地切换，传输更省。

### 6.3 单元均色

L3 链路：

`render_face_idx`
-> `source_elem_row`
-> L1 `INTEGRATION_POINT` 或 `ELEMENT_NODAL`
-> L3 完成均值 / 截面点选择 / 分量选择
-> 生成 per-face 标量或颜色

### 6.4 变形显示

L3 链路：

`source_node_rows`
-> 位移场 `U`
-> `coords_global + scale * U`
-> 生成变形后 `positions`

说明：

- 若模型很大，变形坐标建议由 L3 直接算好再下发
- 前端不再自己把位移加回坐标

### 6.5 集合过滤 / focus

L3 链路：

`set_name`
-> `elem_rows / node_rows / render_rows`
-> 生成过滤后的面片子集或 mask

返回可选：

- 过滤后的独立 chunk
- 固定 geometry + `visibility mask`
- 固定 geometry + `focus mask`

对于频繁切换 focus，优先返回 mask；对于强裁剪，优先返回子集 geometry。

---

## 7. 建议的 L3 下发包

建议把 L3 的主要输出抽象成统一的 `RenderPayload`。

### 7.1 基础几何包

用于首次加载或切换 partition/chunk：

- `positions [R, 3, 3] float32`
- `normals [R, 3, 3] float32`
- `render_face_idx [R] int32`
- `source_elem_row [R] int32`
- 可选 `source_node_rows [R, 3] int32`

### 7.2 结果包

用于切换帧、字段、分量、渲染模式：

- `render_face_idx [R] int32`
- `scalar` 或 `color`
- 可选 `value_min`
- 可选 `value_max`
- 可选 `mask`

### 7.3 变形包

用于切换位移场和倍率：

- `render_face_idx [R] int32`
- `positions_deformed [R, 3, 3] float32`

### 7.4 拾取结果包

- `instance`
- `render_face_idx`
- `elem_label`
- `node_labels`
- `field_value`
- `section`
- `material`
- `sets`

---

## 8. 前端应传回什么

前端不需要传回大结果数组，主要传回“交互定位信息”。

### 8.1 点击 / hover

优先传：

- `instance`
- `render_face_idx`

次选传：

- ray origin / direction
- 或屏幕坐标 + 相机矩阵

但若前端本地已持有 mesh，优先直接传 `render_face_idx`，这样最简单，服务端无须重复射线求交。

### 8.2 框选

- 屏幕矩形
- 相机矩阵
- 视口大小

或直接传世界坐标 bbox。

### 8.3 视图状态

- `step`
- `frame`
- `field`
- `component`
- `deform_scale`
- `set / focus`

---

## 9. 对现有 API 的最小改造建议

### 9.1 保留现有几何接口，但语义升级

`GET /api/odb/{odb_id}/mesh/chunk`

从“只返回基础几何”升级为“返回可直接渲染的基础几何包”。

### 9.2 新增结果组装接口

`GET /api/odb/{odb_id}/render/state`

输入：

- `instance`
- `chunk` 或 `partition`
- `step`
- `frame_idx`
- `field`
- `component`
- `mode=smooth|flat|attribute`
- `set`
- `focus`
- `deform_field`
- `deform_scale`

输出：

- 对应 `RenderPayload`

### 9.3 保留并强化 pick 接口

`GET /api/odb/{odb_id}/query/pick`

输入仍优先使用：

- `instance`
- `render_face_idx`

### 9.4 新增 hover 轻量接口

`GET /api/odb/{odb_id}/query/hover`

只返回 tooltip 所需最小信息，避免每次 hover 拉完整 element 详情。

---

## 10. 哪些计算应放在后端

建议明确放在后端：

- 结果值到渲染面片/顶点的映射
- 单元结果均值、截面点选择、分量裁剪
- 变形后坐标计算
- set 到 render_rows 的转换
- bbox / 框选命中的工程语义映射
- 大模型分区和可见性相关筛选

建议保留在前端：

- 相机控制
- 最终 draw call
- colormap UI 切换
- 轻量 tooltip 展示
- 非语义性的 hover 高亮

说明：

- 若 colormap 频繁切换，可考虑后端只传 `scalar`，前端本地做颜色映射
- 若前端性能依然不足，再降级为后端直接传 `color`

---

## 11. 风险与约束

### 11.1 带宽压力

若每次状态切换都回传整块 `positions + normals + colors`，网络压力会很大。

建议分层缓存：

- 基础 geometry 长缓存
- 结果 buffer 单独切换
- deform buffer 单独切换
- mask 单独切换

### 11.2 source_elem_row 的类型边界

主文档中 `source_elem_row` 注释为“指向 L1 某 element 行号”，但混合单元类型时必须额外明确：

- 它是否是“某 elem_type 分组内的局部 row”
- 若是，L3 还必须同时持有 `source_elem_type`

否则 L3 在 IP 查询时无法稳定路由到正确 dataset。

这是当前最需要补清楚的一个实现细节。

### 11.3 source_node_rows 仅覆盖角节点

当前设计适用于线性化后的渲染壳子。

这意味着：

- 当前 L3 的变形和节点着色面向角节点展开面片
- 若未来做高阶曲面显示，需要新的渲染映射层

---

## 12. 最终结论

对于“大模型、前端性能敏感”的场景，推荐采用：

**L2 预生成基础显示壳子，L3 基于 L1+L2 组装最终渲染数据，前端只保留 Three.js 显示壳子和交互采集。**

现有 L1/L2 设计已经基本满足该模式，L3 需要补的是：

- 正式化的内存速查索引
- 稳定的二进制下发协议
- 混合单元类型下的映射约束说明

因此，若后续要扩展文档，建议将主架构文档中的 Layer 3 从“前端服务”进一步改写为：

**交互查询与渲染数据组装服务**

