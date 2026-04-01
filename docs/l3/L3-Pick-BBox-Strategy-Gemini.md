# L3 核心架构落地与空间查询 (拾取/框选) 优先实施策略
**Target Audience:** Claude, Codex, Gemini
**Context:** Abaqus ODB 仿真文件的高性能后端服务 (Layer 3 - FastAPI)

## 一、 当前 L3 架构共识回顾
基于现有的 `L3-FastAPI-Scaffold-Design.md` 和 `L3-Module-Boundary.md`，L3 层的开发必须严格遵循以下基建原则：
1. **多进程与 CoW (写时复制)**：使用 `Gunicorn + Uvicorn Worker`。由于 Python 中密集型计算和 HDF5 读取会阻塞事件循环，不能单纯依赖 `async`。服务启动时（Pre-fork 阶段），将只读的字典、空间索引树（ModelIndex）全量加载到内存，依靠 Linux 的 CoW 机制在多 Worker 间共享，极大降低内存占用。
2. **严格的三层模块边界**：
   * **Router (协议层)**：仅负责 FastAPI 路由、HTTP 参数校验、定义响应 Schema。
   * **Service (业务层)**：核心层。处理 HDF5 索引映射、空间查询、组装 Render Buffer、业务规则判断。
   * **Repo (存储层)**：仅负责与 HDF5 (`h5py`) 和 SQLite (`manifest.db`) 的底层 IO交互。
3. **拥抱二进制流**：渲染用的大型 Buffer 必须以 `application/octet-stream` 返回，避免 JSON 序列化的巨大开销。

## 二、 核心战略：空间查询 (Pick & BBox) 优先级提升
根据最新需求，**单点拾取 (Pick)** 和 **3D框选 (BBox)** 被提升为最高优先级。
前端（Three.js）将放弃在浏览器端进行海量面片的 Raycaster 计算或复杂的空间拓扑存储，彻底转为“瘦客户端”。**一切空间与拓扑的映射计算，由 L3 后端承担。**

### 1. 单点拾取 (Pick / Hover) 实现路径
**目标**：前端传入鼠标点击处的全局唯一面片 ID，后端返回该面片对应的单元、节点、物理场结果。
* **输入 (Router)**：`GET /api/odb/{odb_id}/query/pick?instance={inst}&render_face_idx={idx}&step={s}&frame={f}&field={fld}`
* **逻辑 (Service & Repo)**：
  1. **O(1) 内存映射**：通过常驻内存的 `render_source` 数组，直接用 `render_face_idx` 映射出该面片在 L1 原始数据中的 `source_elem_row`。
  2. **提取拓扑**：拿着 `source_elem_row`，去 L1 Geometry HDF5 中读取对应的 `elem_label`（真实单元编号）和 `conn`（节点连接关系）。
  3. **提取结果**：拿着 `source_elem_row` 和 `frame` 参数，去 L1 Results HDF5 中切片读取当前的应力/应变值。
* **输出**：组装为 JSON 返回（因为单个 Pick 数据量极小）。
* **性能预期**：< 5ms，无复杂空间计算，纯数组索引查找。

### 2. 3D 框选 (BBox) 实现路径
**目标**：前端传入一个 3D Bounding Box (Min/Max)，后端计算出被框中的所有单元，并生成一个“用户集合 (User Set)”，供后续局部高亮或查询使用。
* **输入 (Router)**：`POST /api/odb/{odb_id}/query/bbox` (包含 `instance`, `bbox_min[3]`, `bbox_max[3]`, `mode="intersect|contained"`)
* **逻辑 (Service & Repo)**：
  1. **粗筛 (Octree)**：Service 调用 Repo 读取 L2 HDF5 中的八叉树 (Octree) 节点信息。利用八叉树的节点 BBox 与前端传来的 BBox 做 AABB 相交测试，快速剔除大量不相关的面片块。
  2. **精筛 (NumPy 坐标过滤)**：针对粗筛留下的候选面片，Service 从 **L2 (`l2/geometry/<inst>_surface.h5`)** 加载 **全局坐标 (`coords_global`)**。因为这是表面节点的全局坐标 (即 Triangle Soup 里三角形顶点的坐标 `[R, 3, 3]`)，利用 NumPy 向量化操作时需注意边界情况：
     *   **contained 模式**：三角形的**所有三个顶点**都在 bbox 内 -> 面片被选中。
     *   **intersect 模式**：三角形**任意一个顶点**在 bbox 内 -> 面片被选中。
     *   *(实现提醒：在编写 `query_service.py` 时，务必注意 `[R, 3, 3]` 的 axis 聚合方向。需先沿 `axis=-1` 判断单个顶点是否在 BBox 范围内，然后再沿 `axis=1` 根据模式做 `all()` 或 `any()` 聚合。)*
  3. **生成集合 (User Set)**：被选中的面片对应到具体的 `render_face_idx`。Service 必须将 **`render_rows`**（供后续渲染快速跳过转换链）和对应的 **`elem_rows`**（供工程语义查询）都提取出来。随后调用 Repo，在 `manifest.db` 的 `user_sets` 和 `user_set_instances` 表中，写入新的临时集合记录（保存为 Zlib 压缩后的 int32 BLOB）。
* **输出**：返回生成的 `set_name` (或 `set_id`) 以及选中的 `elem_count`。后续前端拿着这个 `set_name` 去请求 `/mesh/chunk` 或 `/render/state`，后端即可实现局部刷新或高亮。
* **性能预期**：< 50ms。

## 三、 第一阶段 L3 代码骨架搭建计划
为了让后续团队能高效并行开发，第一步必须建立好脚手架。请遵循以下目录结构：

```text
src/l3/
├── main.py                     # FastAPI Entrypoint, Gunicorn hooks (Lifespan 中初始化全局状态)
├── core/
│   ├── config.py               # Pydantic BaseSettings 
│   ├── state.py                # 【关键】存放 ModelIndex、Registry 等全局单例 (Pre-fork 阶段加载)
│   ├── errors.py               # 定义 NotReadyError, NotFoundError 等
│   └── exception_handlers.py   # FastAPI 异常拦截，统一返回 {ok: false, error: ...}
├── api/
│   ├── router.py               # 主路由注册
│   └── routes/
│       ├── health.py
│       ├── meta.py
│       └── query.py            # Pick 和 BBox 接口写在这里 (占位或实现)
├── schemas/
│   └── query.py                # Pick/BBox 的 Request/Response Pydantic Models
├── services/
│   └── query_service.py        # Pick 和 BBox 的核心计算逻辑，依赖注入的 ModelIndex
└── infra/
    ├── manifest_repo.py        # SQLite (manifest.db) 读写封装 (必须配置 WAL 模式)
    └── hdf5_repo.py            # L1/L2 HDF5 切片读取封装
```

## 四、 致 Claude & Codex 的下一步开发建议 (Next Steps)
1. **搭建基础脚手架**：请先生成 `src/l3/main.py`、`core/config.py` 和 `core/errors.py`，建立起能跑起来的 FastAPI 服务和统一的 JSON 错误响应格式。
2. **实现 ModelIndex 类与全局状态**：在 `core/state.py` 中编写内存缓存结构，演示如何从 L2 HDF5 中加载全局坐标，以及加载 `render_face_idx -> source_elem_row` 数组。注意其作为全局单例的生命周期。
3. **实现 Query 链路**：在 `api/routes/query.py` 中定义 Pick 和 BBox 的路由，并在 `services/query_service.py` 中利用全局的 `ModelIndex` 实现映射和过滤逻辑。

> **⚠️ 注意与避坑指南**：
> 1. **SQLite 并发写**：BBox 生成用户集合时会触发多个 Gunicorn Worker 并发写入单文件 `manifest.db`。`infra/manifest_repo.py` 初始化连接时必须显式开启 `PRAGMA journal_mode=WAL;` 并设置合理的 `timeout`（如 5.0 秒），利用底层 C 库的重试机制防锁。
> 2. **HDF5 数据源**：构建空间索引和坐标过滤时，认准 L2 的 `coords_global`。L1 存的仅仅是未经过矩阵变换的局部坐标！
