# ODB 后端服务设计方案

## 项目背景

Abaqus ODB 仿真结果文件的后端查询与可视化服务。
后端负责数据解析、存储、索引与计算，前端（Three.js）只负责渲染。

## 核心需求

- 解析 Abaqus `.odb` 文件（节点、单元、集合、截面、材料、结果）
- 支持 ~10M 节点规模、~30 帧结果
- 前端全部基于三角面片渲染（Triangle Soup）
- 渲染模式：节点平滑 / 单元均色 / 属性着色
- 用户集合：框选/点击创建，持久化，支持结果查询
- 点击面片返回单元号，3D 包围盒查询
- 边线（Feature Edges：边界边 + 折角边）、节点法向线
- 变形显示 + 模态动画（前端计算，无额外请求）
- 属性着色（材料/截面/集合可切换），focus 高亮模式

## 多 ODB 支持

每个 ODB 文件对应一个独立 workspace（`/data/<odb_id>/`），通过 job 队列异步处理：

```
POST /api/jobs  →  L1 转储（需 license）→  L2 预处理  →  ready
                   status 追踪：submitted → l1_running → l1_done → l2_running → ready
```

所有查询端点均带 `odb_id` 路由前缀：`/api/odb/{odb_id}/mesh/chunk`

当前目标规模：2–3 个活跃 ODB，全量常驻内存（~2GB × N）。

---

## 技术栈

- 语言：Python
- 存储：HDF5（h5py）+ SQLite（manifest.db）
- 空间索引：scipy.spatial.cKDTree + 八叉树（L2 预计算）
- label→idx：numpy searchsorted（非 Python dict，~80 MB/instance）
- 服务框架：FastAPI + gunicorn 多进程
- 前端渲染：Three.js

## 当前版本

| 文件 | 适用场景 |
|------|---------|
| [ODB-Service-Architecture.md](./ODB-Service-Architecture.md) | **主文档 v5，含完整设计（三层架构）** |

## 三层架构概要

```
ODB 文件
  │
  ▼ Layer 1：ODB 忠实转储（需 Abaqus license，一次性）
  │  保留 ODB 原始层次结构，不做任何加工
  │  l1/assembly.h5, geometry/<inst>.h5, sets/sets.h5, results/<step>__<field>.h5
  │
  ▼ Layer 2：预处理（纯 Python，一次性批处理）
  │  基于 L1 生成渲染与交互所需的加工数据，不修改 L1
  │  l2/geometry/<inst>_surface.h5, l2/render/<inst>_render.h5
  │
  ▼ Layer 3：前端服务（FastAPI + gunicorn，持续运行）
     按请求动态组合 L2（渲染几何）+ L1（原始结果）
     manifest.db 提供跨层路由
```

## 核心设计决策（v5）

1. **三层分离**：L1 忠实转储（需 license）→ L2 预处理（纯 Python）→ L3 API 服务（动态组合）
2. **Render Buffer = Triangle Soup**：`[Rf, 3, 3]` float32，全局存储，chunk = 切片，无 indices
3. **render_face_idx**：每 chunk 携带全局面片编号，set 过滤时仍可稳定对齐
4. **HDF5 结构**：`[num_frames, N, ...]` 多维 dataset，单帧和时程均高效（一次 IO）
5. **label→idx**：numpy 排序数组 + searchsorted，内存 ~80 MB/instance
6. **result/field 带 set**：稀疏返回（indices + values），不传全局数组
7. **user_sets**：`user_sets + user_set_instances` 两表，按 instance 分片存储 BLOB（zlib 压缩 int32），支持跨 Instance 集合，避免 HDF5 多进程写冲突
8. **manifest.db result_blocks 路由**：直接定位 HDF5 路径，无需扫描文件
9. **并发**：gunicorn 多进程 worker，run_in_threadpool 仅防事件循环阻塞
10. **法线**：只传平滑法向（NODAL 模式），flat shading 由 GPU dFdx/dFdy 实时计算
11. **API 格式**：大数组 `application/octet-stream`，元数据 JSON
12. **bbox 模式**：`intersect`（默认）| `contained`，符合工业软件框选直觉
13. **标签作用域**：label 为 Instance 级别，所有查询以 (instance_name, label) 联合定位
14. **高阶单元**：L2 线性化（取角节点），完整连接保留在 `_highorder.h5` 备用

## Blocker 验证项（开发前必须跑）

**Abaqus PoC**：`getSubset(position=NODAL)` 对壳单元截面点（BOT/MID/TOP）
的实际输出结构。验证脚本约 20 行，需要包含壳截面点输出的最小 ODB 文件。

- 通过 → L1 NODAL 路径可用，三种渲染模式均支持平滑着色
- 失败 → NODAL 无截面点，smooth 渲染降级为 flat shading fallback

## 历史版本存档

见 [temp/](./temp/) 目录：v1/v2/v3/v4 方案文档及全部 review 文件。
