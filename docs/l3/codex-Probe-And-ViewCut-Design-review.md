# Codex 技术审查意见

文档对象：[docs/l3/L3-Probe-And-ViewCut-Design.md](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md)

主参考文档：[ODB-Service-Architecture.md](/root/codexDir/odb-service-design/ODB-Service-Architecture.md)

审查范围：仅审查方案设计本身，不涉及代码修改。

## 主要问题

1. 高风险：Probe Phase 1 对“节点点击”能力的依赖和当前 `pick` 实现不一致，文档写成“无需后端改动”不成立。

参考位置：
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L239](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L239)
- [src/l3/api/routes/query.py#L10](/root/codexDir/odb-service-design/src/l3/api/routes/query.py#L10)
- [src/l3/services/query_service.py#L195](/root/codexDir/odb-service-design/src/l3/services/query_service.py#L195)
- [src/l3/services/query_service.py#L293](/root/codexDir/odb-service-design/src/l3/services/query_service.py#L293)
- [src/l3/services/query_service.py#L305](/root/codexDir/odb-service-design/src/l3/services/query_service.py#L305)

问题说明：
- 方案写的是前端点击直接复用现有 `/query/pick`，因此将 Probe Phase 1 定义为“无需后端改动”。
- 但当前接口虽然接收 `node_idx` 参数，`query_service.pick()` 里也算出了 `selected_node_row`，后续读取结果时却没有真正使用它。
- 当前节点模式读取结果时实际固定取 `face_node_rows[:1]`，也就是命中三角面片的第一个角点，不是用户屏幕空间里最近的那个节点。
- 这会导致 Node 模式表格里显示的数值和用户实际点击的节点不一致，属于功能语义错误，而不是显示小偏差。

审查建议：
- 文档应把“Node 模式 Phase 1 需要最小后端修正”写明。
- 如果坚持 Phase 1 完全不动后端，则应把一期降级为只支持 element probe，node probe 放到后续阶段。

2. 高风险：节点模式下 `S, Mises` 的语义未定义清楚，当前设计会返回一个看似确定、实际含糊的值。

参考位置：
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L103](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L103)
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L115](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L115)
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L193](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L193)

问题说明：
- 节点表格要求显示 `S, Mises`，来源允许是 `INTEGRATION_POINT` 或 `ELEMENT_NODAL`。
- 但节点本身可能附属于多个单元，不同单元上的应力本来就可能不同。
- 当前返回结构里同时带 `node_label` 和命中的 `elem_label`，文档却没有明确节点模式下的应力值到底定义为哪一种：
  - 命中单元上的值
  - 该节点所有附属单元上的平均值
  - 仅当存在真正的 `NODAL S` 时才显示
- 如果这里不先定语义，前后端和测试都会各自做默认假设，后续很容易出现“实现没错但结果解释不一致”的问题。

审查建议：
- 在设计里明确节点模式 `S, Mises` 的唯一语义。
- 建议把该值与 `elem_label` 的关系写死，否则用户会误以为这是节点的唯一真值。

3. 中高风险：View Cut Phase 2 的依赖评估偏乐观，“不需要修改 L1/L2，只需新增 L3 端点”这一表述不够成立。

参考位置：
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L312](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L312)
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L342](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L342)
- [ODB-Service-Architecture.md#L110](/root/codexDir/odb-service-design/ODB-Service-Architecture.md#L110)
- [ODB-Service-Architecture.md#L133](/root/codexDir/odb-service-design/ODB-Service-Architecture.md#L133)
- [ODB-Service-Architecture.md#L147](/root/codexDir/odb-service-design/ODB-Service-Architecture.md#L147)

问题说明：
- 方案假设 L3 可以直接遍历体单元并实时做平面求交。
- 但主架构里当前 L2/L3 的索引设计主要围绕“表面 Triangle Soup、表面八叉树、render_face 映射”展开，不是围绕体单元截面查询展开。
- 主架构还明确写了 L3 不做重几何计算，也不承担服务端三角化职责。
- 如果要在大模型上把截面切割做成可用功能，通常至少需要一种体单元级空间筛选或预计算索引；否则每次切割都全量扫描体单元，会直接突破当前 L3 的职责边界和性能设计。

审查建议：
- 将 View Cut Phase 2 标记为“可能触及架构边界的能力扩展”，而不是简单的 L3 端点补充。
- 设计里应显式评估是否需要新增 L2 预处理索引，或者新增专门的体单元空间结构。

4. 中风险：View Cut Phase 2 的性能预估和交互模型不够可信，容易把需求带到不可落地的方向。

参考位置：
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L337](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L337)

问题说明：
- 文档直接给出“0.5μs × 500万 = 约 2.5 秒”的估算，但没有说明这个估算建立在什么实现条件上。
- 没有区分 Python 层循环、numpy 向量化、HDF5 读取模式、不同单元类型分支、交点排序、截面三角化等成本。
- 更关键的是，文档没有定义前端交互触发模型：是滑块拖动实时触发、拖动结束触发、还是带 debounce 的异步请求。
- 如果用户拖动 slider 的每一步都触发秒级后端请求，这个功能在交互层面就不可用。

审查建议：
- 先在设计中明确触发时机、可接受延迟、是否取消旧请求、是否限制为“拖动结束再计算”。
- 在没有 POC 数据前，不建议把当前性能预估写成接近可交付承诺的表述。

## 开放问题

1. `face-label-map` 的全量传输成本需要补数量级估算。

参考位置：
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L88](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L88)

说明：
- 若按 `render_face_idx → [elem_label, node_labels[3]]` 全量下发，大模型下响应体积可能明显增大。
- 当前文档把它写成“推荐方案 A”，但没有给出体积、压缩率、加载时机、是否分 chunk 下发等依据。

建议：
- 增加一个简单容量估算，至少说明在 1M / 10M render faces 下的大致内存和网络量级。

2. `node_to_elements` 的旧数据兼容策略没有定义。

参考位置：
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L156](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L156)

说明：
- 该结构本身是合理的，但已有 workspace 不会自动拥有这个组。
- 需要明确 L3 在缺少该数据时如何表现：返回 `null`、隐藏列、提示需要重跑 L1，还是做慢速降级。

3. View Cut Phase 1 所需 bbox 数据来源需要更明确。

参考位置：
- [docs/l3/L3-Probe-And-ViewCut-Design.md#L291](/root/codexDir/odb-service-design/docs/l3/L3-Probe-And-ViewCut-Design.md#L291)

说明：
- 文档写“从 manifest.db 读取”滑块范围，但没有在这份方案里明确 bbox 对应哪个实例级字段或接口返回。
- 这会导致前端实现阶段去猜数据来源。

建议：
- 把 bbox 的权威来源写成明确字段或明确接口，而不是只写“从 manifest.db 读取”。

## 总结结论

这份方案的阶段化思路总体正确，特别是将 `Probe` 和 `View Cut` 都先拆出纯前端一期，方向合理。

但有两个关键前提目前写得过于乐观：
- `Node probe` 不是严格意义上的“零后端依赖”
- `View Cut Phase 2` 也不只是“加一个 L3 端点”

建议先修正文档中的这两个前提，再补齐以下设计空白：
- 节点模式 `S, Mises` 的唯一语义
- 截面切割的交互触发方式与延迟预算
- 大模型下 `face-label-map` 的容量评估
- 新增 L1 数据结构的兼容策略

在这些问题补齐之前，这份方案适合作为方向性设计，不适合作为直接进入开发排期的执行稿。
