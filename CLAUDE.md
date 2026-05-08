# CLAUDE.md

**用中文回复用户。**

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 协作约定

**用户背景**：用户不熟悉有限元、HDF5、FastAPI、Three.js 等技术栈。在回答问题、解释设计决策、说明代码改动时，需要：
- 用大白话解释"为什么这么做"，不只说"怎么做"
- 新概念第一次出现时主动解释
- 实现完成后说明整个流程是什么，前后端各做什么
- 遇到技术取舍时说清楚利弊，而不是只给结论
- 科普性内容写进 `FEM-Viewer-Primer.md`，流程/设计写进对应 `docs/` 文档
- **每次修改 `src/l3/api/routes/` 下的接口（新增、删除、改参数/响应）后，必须同步更新 `docs/l3/L3-API-Quick-Reference.md`**，包括更新文件顶部的"更新时间"行
- **接口只使用 GET 和 POST**，不使用 PUT / PATCH / DELETE

## Project Overview

本项目是一个 FEM（有限元）后处理与模型修正平台，包含两条并行主线，共用同一个 FastAPI 进程（`app.py`）：

**主线 1 — ODB 可视化服务**：读取 Abaqus ODB 仿真文件，通过三层流水线（L1 提取 → L2 预处理 → L3 服务）将大规模 FEM 数据（最多 ~10M 节点、~30 帧）推送给 Three.js 前端做 3D 云图渲染。

**主线 2 — 模型修正服务**：基于试验数据（UNV 模态、BDF 静力）与仿真结果的对比，计算灵敏度矩阵，通过贝叶斯优化迭代修正 FEM 模型参数（材料属性、壳厚等），结果写回 MySQL。

**ODB 可视化实现状态**：L1 已完成；L2 基本完成（表面提取 + Triangle Soup + Feature Edges + Octree，缺 Partitioning）；L3 核心骨架已完成（health/pick/bbox/frame-colors 等端点），L3 尚未加载 L2 的 octree/feature-edge 数据。

**当前主要入口（前端实际走的链路）**：`POST /api/projects`（2.3 Projects 分支）。前端通过 project 模式提交 ODB/INP，所有查询也带 `project_id`。`POST /api/jobs`（2.2 Legacy ODB Jobs）是旧接口，仅保留兼容性，新功能不在此分支迭代。进度日志接口 `GET /api/jobs/{odb_id}/logs` 目前仅覆盖 legacy jobs，project 分支进度暂未接入。

**主设计文档**：`ODB-Service-Architecture.md`（v5，项目内最高权威）。所有 ODB 可视化的实现细节、HDF5 schema、算法伪代码、manifest.db schema 均以该文档为准。遇到歧义时以该文档为准，不以代码为准。

## Git 工作流

本项目托管在私有 Gitea 实例：`ssh://git@62.234.179.217:43128/RealVirtual/--702.git`。

通过 Git+SSH 与开发者本地机器同步。**每次修改完必须提交并推送**，开发者通过 `git pull` 取最新代码。

```bash
git add <files>
git commit -m "..."
git push
```

除非明确说"只提交不推"，否则每次 commit 后必须 push。

## 测试约定

**不要在本机（codex 服务器）上运行测试。** 测试由开发者在本地机器上执行，通过 `git pull` 取代码后运行：

```bash
python3 -m pytest tests/ -v
```

`tests/` 下包含两条主线的测试（ODB 可视化 + 模型修正），conftest.py 提供公用 fixture。写完测试代码后直接 commit + push，等开发者反馈结果。

## Running the Code

There is no build system. All execution is manual.

**正常启动（Windows，双进程）：**
```bat
start.bat          # 同时打开两个 cmd 窗口，分别运行下面两条命令
```
等价于：
```bash
python app.py              # 进程 1：Web 服务，默认端口 5000
python src/job_runner.py   # 进程 2：L1/L2 流水线 runner（可嵌入 app.py，见下）
```
`app.py` 是真正入口：它以 L3 的 FastAPI app 为基础，再挂载模型修正的所有路由。

**配置**：服务从 `service_config.json`（项目根目录）读取，也可以用环境变量覆盖。关键字段：`DB_HOST/PORT/USER/PASSWORD/DATABASE`（MySQL），`APP_PORT`（默认 5000），`APP_EMBEDDED_RUNNER`（1=job_runner 作为 app.py 的后台线程自动启动，0=需要手动启另一个进程，Windows 默认 0）。

**L1 Extraction (requires Abaqus license):**
```bash
# Phase 1: Run under Abaqus Python 2.7 — dumps ODB to temporary .npy files
abaqus python src/l1/abaqus_dump.py --odb /path/to/model.odb --out /data/<odb_id>/

# Phase 2: Run under Python 3 — packs .npy → HDF5 + manifest.db
python src/l1/l1_pack.py --workspace /data/<odb_id>/
```

**L2（已实现）：**
```bash
python src/l2/ingest.py --workspace /data/<odb_id>/
```

**前端（Three.js，开发模式）：**
```bash
cd viewer && npm install && npm run dev
```

## Three-Layer Architecture

### L1 — Faithful ODB Dump (`src/l1/abaqus_dump.py` + `src/l1/l1_pack.py`)
Two-phase extraction: `abaqus_dump.py` runs under Abaqus Python 2.7 to extract data to `.npy` files; `l1_pack.py` runs under standard Python 3 to pack them into HDF5 + SQLite. Data is stored as-is — no processing, no coordinate transforms applied. Output structure:
```
/data/<odb_id>/
  l1/
    assembly.h5                      (instances, transforms, assembly sets)
    geometry/<inst>.h5               (nodes, elements, corner connectivity)
    geometry/<inst>_highorder.h5     (full high-order connectivity)
    sets/sets.h5                     (node/element set memberships)
    results/<step>__<field>.h5       (NODAL, INTEGRATION_POINT, ELEMENT_NODAL)
  manifest.db                        (routing & metadata)
```

### L2 — Preprocessing (`src/l2/ingest.py`, not yet implemented)
One-time batch job (pure Python 3) that converts L1 raw data into rendering-ready buffers. Key operations: apply instance transforms, linearize high-order elements (use corner nodes only), triangulate and de-duplicate faces to extract surface geometry, compute feature edges (boundary + fold ≥30°), build octree spatial index (`max_depth=8`, leaf threshold ≤1000 triangles).

### L3 — Frontend Service (`src/l3/`, not yet fully implemented)
Continuous FastAPI + Gunicorn multi-worker service. Reads L1 HDF5 for results and L2 HDF5 for geometry; only writes to `manifest.db` (user sets). See `docs/l3/` for full API contract and scaffold design.

## Key Data Conventions

**Label scoping:** Node/element labels are only unique within an Instance. Always use `(instance_name, label)` pairs, never bare label numbers.

**Triangle Soup geometry:** Render buffers are `[Rf, 3, 3] float32` — fully exploded, no index buffer. Each face carries its `render_face_idx` (globally stable per instance).

**Label→index mapping:** Uses sorted numpy arrays + `numpy.searchsorted` (not dicts). Binary search over sorted label arrays, ~80 MB/instance in memory.

**Multi-frame HDF5 layout:** Results stored as `[num_frames, N, ...]` — enables single-IO access for both frame slices and node time-series.

**High-order elements:** Main geometry arrays use corner nodes only. Full connectivity (including mid-nodes) is in `_highorder.h5`.

**L1 is immutable after creation. L2 files are read-only by L3. L3 only writes `user_sets` table in `manifest.db`.**

## manifest.db Schema

Central routing registry — no filesystem scanning at runtime.

| Table | Key columns |
|---|---|
| `instances` | `instance_name, part_name, geom_path, bbox` |
| `element_type_dist` | `instance_name, elem_type, count, n_corner_nodes` |
| `steps` | `step_name, procedure (STATIC\|FREQUENCY\|DYNAMIC\|BUCKLE), num_frames` |
| `frames` | `step_name, frame_idx, frame_value, description` |
| `result_files` | `step_name, field_name, file_path, components, positions` |
| `result_blocks` | `step_name, field_name, instance_name, position, elem_type, h5_path` |
| `node_sets / element_sets` | `set_name, instance_name, h5_path` |
| `user_sets` | `set_name, set_scope, user_set_instances` (compressed BLOB) |

Job lifecycle tracked in manifest: `submitted → l1_running → l1_done → l2_running → ready`

## Key Design Decisions

- **No Abaqus at runtime:** L1 extraction requires Abaqus Python 2.7; L2/L3 use standard Python 3 only.
- **Concurrent safety:** HDF5/SQLite are single-writer. Job runner (L1/L2) is a separate process from the L3 web service; multiple L3 Gunicorn workers can read in parallel (HDF5 is multi-reader safe). User sets stored as compressed BLOBs in SQLite to avoid HDF5 multi-process write conflicts.
- **Memory model:** L3 loads 2–3 active ODB workspaces (~2 GB each); Gunicorn workers share static numpy indices via copy-on-write fork.
- **No result caching in L3:** Reads directly from L1 HDF5 each request.

## Model Update Service（模型修正主线）

模型修正流程：导入试验数据 → 与仿真节点匹配 → 计算灵敏度矩阵 → 贝叶斯迭代优化参数 → 将修正后结果写回 L3 外部字段接口。

### 目录结构

```
services/model_update/
  analysis/
    inp_service.py            — INP目录提取、测点匹配、DOF匹配、响应目录构建、DAC/DSF计算
    sensitivity_service.py    — 灵敏度计算编排（DSA工作流、结果存储）
    bayesian_service.py       — 贝叶斯模型修正迭代主逻辑
    solver_service.py         — 求解器调度（Abaqus/Nastran）
    inp_tree_service.py       — INP 文件树状结构解析
    model_update_meta_service.py  — 项目元数据管理
    project_source_service.py / project_status_service.py
  importers/
    bdf_service.py            — Nastran BDF 文件导入
    unv_service.py            — UNV 试验模态数据导入
  solver_prep/
    abaqus_sensitivity.py     — 生成 Abaqus DSA inp 文件
    abaqus_adjoint.py         — 生成 Abaqus adjoint inp 文件
    nastran_sol103.py         — 转换 Nastran SOL103 输入文件

webapi/                       — 模型修正 REST API（挂载到 app.py 的 FastAPI app）
  routes.py                   — 汇总所有子路由
  models.py                   — Pydantic 请求/响应模型（所有接口入参定义都在这里）
  routers/
    fem.py          — /import/bdf, /import/inp/catalog, /catalog/inp, /tools/inp/tree
    test_data.py    — /import/unv, /get/sensor_position, /get/deform_sensor_position
    matching.py     — /pair/node_point, /get/pair_node_point_result, /transform/*
    sensitivity.py  — /sensitivity/* （计算、导出、workspace 管理）
    optimization.py — /optimization/parameter/create, /optimization/bayesian/run, /add/response
    solver.py       — Abaqus/Nastran 求解器直接触发接口
    system.py       — 健康检查等
```

### 主要接口分组（app.py 中直接定义的）

```
/match/dofs                   — DOF 匹配
/catalog/response/build       — 构建响应目录
/import/fem/modal             — 导入 FE 模态结果
/import/fem/static            — 导入 FE 静力结果
/correlation/modal/compute    — 计算模态相关性（MAC）
/correlation/static/compute   — 计算静力相关性（DAC/DSF）
```

### MySQL 数据库（`db.py`）

连接配置来自 `config.py`（读 `service_config.json` 或环境变量）。`db.py` 中定义所有建表 DDL 和公共查询函数，`ensure_tables_exist()` 在服务启动时调用。主要表：

| 表名前缀 | 存储内容 |
|---|---|
| `t_mt_py_test_*` | 试验测点坐标、试验模态频率/振型、试验静力结果 |
| `t_mt_py_fem_*` | FE 模态/静力导入结果、节点匹配、DAC/DSF、参数变化、迭代追踪 |
| `t_mt_measuring_point_info` | 测点基础信息（坐标、传感器类型） |
| `t_mt_py_optimization_*` | 优化参数定义、响应定义 |
| `t_mt_py_sensitivity_*` | 灵敏度矩阵存储 |

## INP 解析器（`src/inp/`）

Abaqus INP 文件的纯 Python 解析器，被模型修正服务广泛使用：

```
src/inp/
  lexer.py          — 词法分析（处理 include、注释、续行）
  parser.py         — 语法分析，生成 AST
  model.py          — 数据模型（节点、单元、Section、Set、Step 等）
  resolver.py       — 处理 *INCLUDE 文件展开、路径解析
  topology.py       — 从解析结果提取拓扑关系（单元-节点连接）
  parameter_mapping.py — 提取设计参数、响应定义（用于灵敏度/优化）
  summary.py / diagnostics.py / exporter.py — 辅助工具
  abaqus_sensitivity_tool.py — 生成 Abaqus 灵敏度分析的 INP 模板
```

## 独立模态服务（`src/modal_service/`）

独立的 FastAPI 服务，端口 8001，提供模态振型 JSON 数据和对应的静态前端页面（`tools/modal_viewer.html`）。与主 `app.py` **相互独立**，单独启动：

```bash
uvicorn src.modal_service.main:app --reload --port 8001
```

## 根目录工具文件

| 文件 | 作用 |
|---|---|
| `MeshElementFactory.py` | 单元类型工厂，按 Abaqus 单元名称返回角节点数、面连接等信息 |
| `FemToolsUNVParser.py` | UNV 格式解析器（FEMTools 试验数据） |
| `BDFParserPyNastran.py` | pyNastran 的 BDF 读取封装 |
| `CustomException.py` | 项目级自定义异常 |
| `FemNode.py` | 有限元节点数据类 |
| `utils/VirtualRealUtils.py` | 通用工具函数 |

## Known Blockers

- **Blocker A (Shell Sections):** PoC needed for `getSubset(position=NODAL)` on shell mid-surface/bottom/top points in Abaqus Python API.
- **Blocker B (Transform Attributes):** Verify `localCsys.origin/.xAxis/.yAxis/.zAxis` attribute names in the target Abaqus version.

## Documentation Map

| File | Purpose |
|---|---|
| `ODB-Service-Architecture.md` | Comprehensive v5 design doc — authoritative reference for all layers |
| `FEM-Viewer-Primer.md` | FEM concepts, ODB structure, Three.js rendering relationship |
| `docs/l3/L3-API-Quick-Reference.md` | **当前权威接口文档**（面向前端/调用方，与代码同步） |
| `docs/l3/L3-API-Contract.md` | 旧版接口设计文档（格式与实现已有出入，仅供参考） |
| `docs/l3/Binary-Payload-Spec.md` | Binary envelope protocol for large array responses |
| `docs/l3/L3-Render-Assembly-Design.md` | How L3 assembles render buffers from L1/L2 data |
| `docs/l3/L3-Module-Boundary.md` | Router/Service/Repository responsibility separation |
| `docs/l1/abaqus_dump_guide.md` | Practical guide for running L1 extraction |
| `temp/` | Historical design iterations (v1–v4) and review notes; not authoritative |
| `docs/model_update/模型修正接口清单.md` | 模型修正全部接口清单（可能部分过时，以代码为准） |
| `docs/model_update/DSA-Config-Preview-Design.md` | 灵敏度 DSA 配置预览设计 |
| `docs/model_update/DSA-Merge-Field-Design.md` | 灵敏度字段合并设计 |
| `全部测试流程.md` | 开发迭代记录（需求 checklist），**不是权威文档，以代码为准** |
