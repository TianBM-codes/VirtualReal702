# SimRight DATA-API 覆盖分析

更新时间：2026-05-19

## 背景

`src/l3/api/routes/simright.py` 实现了一个 SimRight 3DLite DATA-API 兼容层，对外暴露单一入口：

```
POST /applications/3dlite/api/v1/query
Body: {"name": "<handler>", "args": {...}}
```

本文档梳理该兼容层目前覆盖的功能，以及与现有 L3 REST 接口之间的对应关系和空白。

---

## 已实现的 Handler 一览

| name | 功能 | 对应现有接口 |
|---|---|---|
| `loadcases` | 列出所有步（steps） | `GET /steps` |
| `variables` | 列出某步的结果字段和分量 | 字段元数据查询 |
| `assemble` | 装配树（instances、parts、sets） | `GET /meta/overview`（部分） |
| `extremeValue` | 某字段全局最大/最小值 | `GET /results/frame-scalar-range` |
| `nodeInfo` | 指定节点的坐标和结果值 | `GET /query/pick` |
| `elementInfo` | 指定单元的质心坐标和结果值 | 单元查询 |
| `XYCurveData1` | 节点时程曲线（X-Y 数据对） | 时程曲线 |
| `freqValue` | 模态频率列表 | 模态元数据 |
| `hitEntities` | 包围盒碰撞检测（查哪些 part 在框内） | `POST /query/bbox` |
| `measureValue` | 节点间距离 / 角度测量 | 测量工具 |
| `nodeId` | 坐标反查最近节点 | 坐标拾取 |
| `probeGroupByPos` | 按坐标批量创建探针组 | 探针 |
| `nearestFace` | 查最近表面三角面片 | `GET /query/nearest-face` |
| `deleteModelFile` | 从内存和注册表中卸载 ODB | `DELETE /api/jobs/{id}` |

所有 handler 均在 `src/l3/services/simright_service.py` 中实现。

---

## 未覆盖的接口（渲染类）

以下接口是 Three.js 前端实际渲染所必需的，SimRight DATA-API 在设计上不提供此类数据，兼容层中无对应 handler。

### 几何流

| 接口 | 作用 |
|---|---|
| `GET /geometry/{inst}/render-buffers` | Triangle Soup 顶点/法向量二进制流 |
| `GET /geometry/{inst}/render-buffers-chunked` | 分块版本（大模型） |
| `POST /geometry/{inst}/render-buffers-subset` | 子集渲染 buffer |
| `GET /geometry/{inst}/feature-edges` | 特征边（轮廓线） |
| `GET /geometry/{inst}/element-mesh-edges` | 单元网格线 |
| `GET /geometry/{inst}/lines` | 梁/杆单元线几何 |
| `GET /geometry/{inst}/points` | 质量点等点单元 |
| `GET /geometry/{inst}/couplings` | 耦合约束几何 |
| `GET /geometry/orientations` | 截面方向 |

### 结果渲染

| 接口 | 作用 |
|---|---|
| `GET /results/frame-colors` | 逐帧逐面颜色数组（Three.js 直接读） |
| `GET /results/frame-scalars` | 逐帧节点标量数组 |
| `GET /results/deformed-positions` | 变形后节点坐标（动画帧） |
| `GET /results/modal-shape` | 模态振型 |
| `GET /results/modal-animation` | 模态动画帧序列 |
| `GET /results/deform-suggest-scale` | 建议变形放大系数 |

### 色标 / 图例

| 接口 | 作用 |
|---|---|
| `GET /color-code/{inst}/legend` | 色标条数据 |
| `GET /color-code/{inst}/legend-entries` | 图例分段 |
| `GET /color-code/{inst}/display-names` | 显示名称映射 |
| `GET /color-code/{inst}/schemes` | 可用配色方案 |
| `GET /color-code/{inst}/region-mesh-edges` | 按颜色区域划分的网格线 |
| `GET /color-code/{inst}/region-outline` | 颜色区域轮廓 |

### 查询（REST 侧未迁移）

| 接口 | 作用 |
|---|---|
| `POST /query/ray-pick` | 鼠标射线拾取 |
| `POST /query/render-faces` | 批量查询渲染面 |
| `POST /query/surface-patch` | 连通面片查询 |
| `POST /query/node-displacements` | 批量节点位移 |

---

## 架构差异说明

SimRight DATA-API 的设计假设前端有自己的渲染引擎，API 只负责提供数值数据（"给我节点 X 在帧 3 的应力值"）。

本项目的设计是后端预计算渲染所需的所有 buffer（Triangle Soup、逐面颜色、特征边等）再推给 Three.js，Three.js 只负责绘制。

两套思路的分工边界不同，因此 SimRight 兼容层天然覆盖**查询/探针/元数据**类需求，但不覆盖**几何流和颜色流**类需求。

---

## 现有架构的合理分法

| 类型 | 推荐方式 | 原因 |
|---|---|---|
| 查询/探针/元数据 | SimRight dispatch（POST） | 接口扩展方便，参数灵活 |
| 几何 buffer / 颜色 / 特征边 | REST GET | 浏览器可缓存大二进制响应，切帧不重复传输 |
