# L3 Docs

这个目录集中放 Layer 3 相关文档。

目前包含：

- `L3-Render-Assembly-Design.md`
  说明 L3 作为“渲染数据组装 + 交互速查层”时，L1/L2 如何支撑它

- `L3-FastAPI-Scaffold-Design.md`
  说明 L3 服务骨架、配置、日志、错误处理、并发模型

- `L3-API-Contract.md`
  说明 L3 对外 API 契约、状态码和主要接口输入输出

- `Binary-Payload-Spec.md`
  说明 L3 二进制 envelope、section table 和前端解包规则

- `L3-Module-Boundary.md`
  说明 `router / service / repo` 的职责边界

- `L3-API-Contract.md` §14
  frame-colors 接口 + pick 接口配合，云图颜色生成完整流程

建议阅读顺序：

1. `L3-Render-Assembly-Design.md`
2. `L3-FastAPI-Scaffold-Design.md`
3. `L3-API-Contract.md`
4. `Binary-Payload-Spec.md`
5. `L3-Module-Boundary.md`

