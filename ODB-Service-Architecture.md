# Abaqus ODB 后端查询与可视化服务
## 架构设计说明

**版本**：v5
**核心原则**：三层分离；L1 忠实转储 ODB 原始结构；查询效率优先；多 ODB Job 队列管理

---

## 目录

1. [架构概览](#1-架构概览)
   - [1.1 各层职责详解](#11-各层职责详解)
   - [1.2 三种渲染模式](#12-三种渲染模式前端-threejs)
   - [1.3 集合→渲染面片转换链](#13-集合渲染面片转换链)
   - [1.4 Step 程序类型与帧语义](#14-step-程序类型与帧语义)
   - [1.5 模态动画与变形显示](#15-模态动画与变形显示)
   - [1.6 区域轻量化](#16-区域轻量化region-lightweighting)
2. [Layer 1：ODB 忠实转储](#2-layer-1odb-忠实转储)
3. [Layer 2：预处理](#3-layer-2预处理)
4. [统一索引：manifest.db](#4-统一索引manifestdb)
5. [Layer 3：前端服务](#5-layer-3前端服务)
6. [实施流水线](#6-实施流水线)
7. [性能参考](#7-性能参考)
8. [多 ODB 作业管理](#8-多-odb-作业管理)
   - [8.1 目录结构](#81-目录结构)
   - [8.2 registry.db](#82-registrydb)
   - [8.3 作业状态机](#83-作业状态机)
   - [8.4 作业管理 API](#84-作业管理-api)
   - [8.5 L3 内存管理策略](#85-l3-内存管理策略)
9. [实现参考（开发者速查）](#9-实现参考开发者速查)
   - [9.1 ODB 数据模型](#91-odb-数据模型)
   - [9.2 Abaqus Python API](#92-abaqus-python-apiabaqus_dumppy-实现参考)
   - [9.3 单元类型参考表](#93-单元类型参考表)
   - [9.4 关键算法伪代码](#94-关键算法伪代码ingestpy)
   - [9.5 实现规范](#95-实现规范不能模糊的细节)
   - [9.6 job-runner 与 L3 通知机制](#96-job-runner-与-l3-通知机制)
   - [9.7 未解决 Blocker](#97-未解决-blocker开发前必跑-poc)

---

## 1. 架构概览

```
提交 .odb 文件  →  POST /api/jobs  →  registry.db（status: submitted）
                                            │
                         ┌──────────────────┘
                         ▼ 后台 worker
                   Layer 1：忠实转储（abaqus_dump.py，需 license）
                   │  status: l1_running → l1_done
                   │
                   ├── /data/<odb_id>/l1/assembly.h5
                   ├── /data/<odb_id>/l1/geometry/<instance>.h5
                   ├── /data/<odb_id>/l1/sets/sets.h5
                   ├── /data/<odb_id>/l1/results/<step>__<field>.h5
                   └── /data/<odb_id>/manifest.db（L1 表）
                   │
                   ▼ 后台 worker（接续）
                   Layer 2：预处理（ingest.py，纯 Python）
                   │  status: l2_running → ready
                   │
                   ├── /data/<odb_id>/l2/geometry/<instance>_surface.h5
                   └── /data/<odb_id>/l2/render/<instance>_render.h5
                   │
                   ▼ status=ready 时，L3 加载该 ODB 内存索引
                   Layer 3：前端服务（FastAPI + gunicorn）
                      GET /api/odb/{odb_id}/mesh/chunk
                      GET /api/odb/{odb_id}/result/field
                      ...（所有查询端点均带 odb_id 路由）
```

---

### 1.1 各层职责详解

#### Layer 1 — ODB 忠实转储（abaqus_dump.py）

**目标**：完整、无损地将 ODB 二进制格式转换为 HDF5/SQLite，保留 ODB 原始语义与层次结构，不做任何加工。

**运行时机**：一次性（需 Abaqus license，`abaqus python abaqus_dump.py`）

**输入**：`.odb` 文件

**输出**：`l1/` 目录下所有文件 + `manifest.db`（instances/steps/frames/result_files/result_blocks/sets 表）

**关键操作**：
- 遍历 `Assembly → Instances`，保留每个 Instance 的 `transform` 矩阵（不应用，局部坐标原样存储）
- 按 `elem_type` 分组存储单元连接（同类型 `n_ip`/`n_sp` 相同，构成规则数组；不同类型单独 dataset）
- 高阶单元取角节点写入 `geometry/<inst>.h5`，完整连接（含中间节点）写入 `geometry/<inst>_highorder.h5`
- 同 Step×Field 下相同 `(instance, position, elem_type)` 的 `bulkDataBlocks` 按帧合并 → `[num_frames, N, ...]` 多维 dataset（单帧切片 `data[frame_idx, :]` 和时程切片 `data[:, row]` 均一次 IO）
- 节点 label 升序排列存储，供 L3 `searchsorted` 快速映射

**明确不做**：计算全局坐标 / 三角化 / 表面提取 / 法线平滑 / 空间索引 / 渲染 Buffer

**标签作用域（重要）**：ODB 节点/单元 label 作用域为 **Instance 级别**，不同 Instance 可有相同 label（如两个 Instance 都有 `node_label=1`）。所有查询必须以 `(instance_name, label)` 联合定位，manifest.db 所有 label 相关表均包含 `instance_name` 字段。

---

#### Layer 2 — 预处理（ingest.py）

**目标**：基于 L1 数据生成前端渲染与交互所需的全部加工数据；不依赖 Abaqus license，可在普通服务器上运行。

**运行时机**：一次性批处理（L1 完成后执行，`python ingest.py`）

**输入**：`l1/assembly.h5`、`l1/geometry/*.h5`、`manifest.db`

**输出**：`l2/` 目录下所有文件 + 更新 `manifest.db`（l2_instances 表、result_files.val_min/val_max）

**关键操作（有序）**：
1. **全局坐标**：局部坐标 × Instance 变换矩阵 → `coords_global [N, 3] float32`
2. **高阶线性化**：高阶单元仅取角节点参与三角化；中间节点已在 `_highorder.h5` 保留备用
3. **三角化**：面定义 → 三角面片，建立三层映射（`tri → face → elem → node`）
4. **表面提取**：剔除相邻两单元共享的内部面，保留表面面片
5. **Feature Edges**：边界边（仅属于一个面）+ 折角边（相邻面法线夹角 ≥ 30°）+ 分区边界
6. **几何特征分区**：Instance 边界 + 法向特征角分区（用于 LOD / 区域轻量化）
7. **Triangle Soup**：表面面片展开为 `[Rf, 3, 3] float32`（无 indices），附全局唯一 `render_face_idx`
8. **平滑法向**：面积加权平均节点法向，展开对应每个 `render_face_idx`
9. **八叉树**：`max_depth=8`，叶节点阈值 `≤ 1000` 面片；用于 bbox 粗筛和区域轻量化

**明确不做**：读取 ODB / 修改 L1 文件 / 存储结果数据 / 在服务启动时运行

---

#### Layer 3 — 前端服务（FastAPI + gunicorn）

**目标**：按请求动态组合 L2 渲染几何和 L1 原始结果，通过 manifest.db 路由；不预合并，不存储冗余数据。

**运行时机**：持续运行（`gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker`）

**输入**：L1/L2 文件（只读）、manifest.db（读写：user_sets）

**输出**：大数组 `application/octet-stream` + 元数据 JSON

**启动流程**：
1. 加载 manifest.db，读取 instances/steps/frames/result_files
2. 从 `l1/geometry/*.h5` 加载节点 label 排序数组 → 构建 `label→row` 内存索引（numpy searchsorted，~80 MB/instance）
3. 从 `l2/geometry/*_surface.h5` 加载 `coords_global` → 构建 `cKDTree`（每 Instance）
4. 从 `l2/render/*_render.h5` 加载 `source_elem_row`、`source_node_rows` 映射到内存
5. 启动 4 workers，各自持有独立内存索引副本

**关键操作**：
- **结果路由**：manifest.db `result_blocks` 直接定位 HDF5 路径 + 帧切片，无文件扫描
- **稀疏结果**：有集合过滤时返回 `(indices, values)`，不传全局数组
- **bbox 查询**：八叉树粗筛候选面片 → cKDTree 精确判断 → 返回 elem_labels
- **pick 查询**：`render_face_idx → source_elem_row` → L1 element 信息
- **用户集合**：`user_sets + user_set_instances` 两表，按 instance 分片存储 BLOB（zlib 压缩 int32），支持跨 Instance 集合，避免 HDF5 多进程写冲突

**明确不做**：服务端三角化 / 几何计算 / 渲染 / 缓存 HDF5 结果文件

---

### 1.2 三种渲染模式（前端 Three.js）

所有渲染均基于 L2 Triangle Soup `[Rf, 3, 3] float32`，三种模式在法向和颜色处理上不同：

| 模式 | 结果来源 | 法向传输 | 颜色映射 |
|------|---------|---------|---------|
| **节点结果平滑**（Smooth） | L1 `NODAL data[frame, node_row, comp]` | L2 平滑法向，逐顶点 | 节点值插值 → colormap，Three.js 线性插值 |
| **单元结果均色**（Flat） | L1 `INTEGRATION_POINT` 或 `ELEMENT_NODAL` 均值 | 不传输，GPU `dFdx/dFdy` 实时计算 | 每面片单一颜色，无插值 |
| **属性着色**（Attribute） | 截面/材料/集合 → 离散颜色表 | 不传输，GPU `dFdx/dFdy` 实时计算 | 每面片按属性 ID 查颜色表，静态 |

**Focus 高亮模式**：选中集合的面片正常着色，其余面片 desaturate 或半透明；Three.js 中通过 `render_face_idx` 生成 mask buffer 实现，无需重发几何。

**API 约定**：`GET /api/odb/{odb_id}/mesh/chunk` 响应中始终包含平滑法向（Smooth 模式使用）；Flat 和 Attribute 模式前端忽略法向，由 GPU 计算 flat normal。

---

### 1.3 集合→渲染面片转换链

从集合名（ODB 原生或用户创建）到渲染面片索引的完整转换链：

```
集合名（set_name）
  ↓ manifest.db element_sets / node_sets → h5_path
  ↓ L1 sets.h5 → elem_labels / node_labels [int32]
  ↓ ModelIndex searchsorted → elem_rows [int32]
  ↓ L2 render.h5 source_elem_row → 过滤 → render_face_idx [int32]
  → 对应 chunk 行号 → 发送前端（或用于结果稀疏切片）
```

**结果稀疏切片时**：
- NODAL 结果：`render_face_idx → source_node_rows → node_rows` → L1 NODAL data 切片
- INTEGRATION_POINT：`render_face_idx → source_elem_row → elem_rows` → L1 IP data 切片

**用户集合（bbox 框选后创建）**：已预存 `render_rows BLOB`，直接跳过上述转换链，查询更快。

---

### 1.4 Step 程序类型与帧语义

| procedure | frame_value 含义 | 典型范围 | 前端时间轴标签 |
|-----------|-----------------|---------|--------------|
| `STATIC` | 分析时间（伪时间） | 0.0–1.0 | "Time" |
| `FREQUENCY` | 模态频率（Hz） | 取决于模型 | "Frequency (Hz)" |
| `DYNAMIC` | 物理时间（秒） | 0–T | "Time (s)" |
| `BUCKLE` | 屈曲载荷因子 | > 0 | "Load Factor" |

manifest.db `steps.procedure` 字段存储程序类型，前端根据此字段决定时间轴标签、刻度格式和动画播放逻辑。

---

### 1.5 模态动画与变形显示

**模态动画**（FREQUENCY step，`frame_idx` = 模态阶数，从 0 起）：

```
L1 results/<step>__U.h5  →  /NODAL/<instance>/data [num_modes, N, 3]  # U1, U2, U3

GET /api/odb/{odb_id}/result/displacement?instance=<name>&step=<freq_step>&frame_idx=<mode_idx>
  → octet-stream: [N × 3 float32]（该阶模态振型位移）

前端：
  pos_deformed[i] = pos_original[source_node_rows[i]] + scale_factor × displacement[source_node_rows[i]]
  scale_factor 由用户滑块调节（量级通常 1e3–1e6 倍放大）
  动画：scale_factor *= sin(2π × t / T)，前端每帧重算，不发起网络请求
```

**变形显示**（STATIC / DYNAMIC step）：同上，`scale_factor=1.0` 即真实变形，`scale_factor>1` 放大变形。

**数据依赖**：需要 L1 结果文件中包含位移场 `U`（3 分量），`frame_idx` 与 `frames` 表对齐。

---

### 1.6 区域轻量化（Region Lightweighting）

对大模型（> 5M 面片）的渐进加载策略，基于 L2 分区（`/partitions/`）：

**加载策略**：
- 初始只传 partition_meta（小数据）+ octree 根 bbox
- 前端按摄像机视锥 + 屏占比排序分区优先级
- 逐分区请求 `GET /api/odb/{odb_id}/mesh/chunk?instance=<name>&partition=<id>`
- 不在视锥内的分区不加载（或卸载 geometry buffer）

**相关字段**：
- `manifest.db l2_instances.partition_count`：分区总数
- `l2/geometry/<inst>_surface.h5 /partitions/partition_meta`：每分区 bbox + 面片数
- `l2/render/<inst>_render.h5 /render/tri_partition`（待补充）：每面片所属分区 ID

分区边界边（`edge_type=2`）在视图中显示为分区分隔线，可辅助用户理解模型结构。

---

## 2. Layer 1：ODB 忠实转储

### 2.1 文件结构

```
l1/
  assembly.h5
  geometry/
    <instance_name>.h5             # 每 Instance 一个文件，含角节点连接关系
    <instance_name>_highorder.h5   # 高阶单元完整连接关系（备用，不影响主路径）
  sets/
    sets.h5
  results/
    <step_name>__<field_name>.h5   # 每 Step×Field 一个文件
```

---

### 2.2 assembly.h5

```
/instances/
  <instance_name>/
    part_name        string
    transform        [4, 4]   float64   # 齐次变换矩阵（局部→全局）

/assembly_sets/
  # Assembly Set 按 Instance 分片存储（label 作用域为 Instance 级，不存在全局编号）
  <set_name>/
    <instance_name>/
      node_labels [K_i]  int32    # 该 Instance 下的 node_label（ODB 原始编号）
      elem_labels [L_i]  int32    # 该 Instance 下的 elem_label
```

---

### 2.3 geometry/\<instance\>.h5

每个 Instance 独立一个文件，按单元类型分组（同类型读取无需跳转）：

```
/nodes/
  labels    [N]       int32     # node_label，ODB 原始编号，升序
  coords    [N, 3]   float64   # 局部坐标（ODB 原生）

/elements/
  <elem_type>/                  # 按单元类型分组，如 S4R、C3D8R、S3
    labels   [Mk]              int32    # elem_label，升序
    # 角节点连接关系（用于 L2 处理，高阶单元只取角节点）
    conn     [Mk, n_corner]    int32    # node_label 索引（对应 /nodes/labels）
    # 面定义（S1/S2/S3... 每个面的节点与法线）
    face_elem_idx  [Mf]        int32    # 第几个单元（行号，对应 labels）
    face_seq       [Mf]        uint8    # 面编号（1=S1, 2=S2, ...）
    face_node_conn [Mf, max_fn] int32   # 面节点索引（-1=unused）
    face_normals   [Mf, 3]     float32  # 面外法线（预计算）

/sections/
  <section_name>/
    element_set   string
    material_name string
    type          string    # SHELL / SOLID / BEAM
    thickness     float64   # 壳截面厚度，无则 NaN

/materials/
  <material_name>/
    type    string
    # 弹性模量、泊松比等属性按需存储

/instance_sets/
  node_sets/<name>     [K]   int32
  element_sets/<name>  [L]   int32
```

---

### 2.4 geometry/\<instance\>\_highorder.h5

仅存储高阶单元的完整连接关系（含中间节点），供未来曲面渲染使用：

```
/elements/
  <elem_type>/           # 仅高阶类型，如 C3D20、S8R
    labels    [Mk]            int32
    conn_full [Mk, n_all]     int32    # 全部节点（角节点 + 中间节点）
    midnode_indices [n_mid]   uint8    # conn_full 中哪些列是中间节点
```

---

### 2.5 sets/sets.h5

```
/part_sets/
  <part_name>/
    node_sets/<name>     [K]  int32
    element_sets/<name>  [L]  int32

/assembly_sets/
  # 同 assembly.h5，按 Instance 分片（label 作用域为 Instance 级）
  <set_name>/
    <instance_name>/
      node_labels [K_i]  int32
      elem_labels [L_i]  int32
```

---

### 2.6 results/\<step\>\_\_\<field\>.h5

忠实保留 ODB bulkDataBlocks 层次，同类型 block 按帧合并（帧为第一维）：

```
/meta/
  step_name         string
  field_name        string
  field_description string
  components        [ncomp]  string   # 如 ['S11','S22','S33','S12','S13','S23']
  invariants        [ninv]   string   # 如 ['Mises','Max_Principal']

/frame_index/
  frame_values      [num_frames]  float64  # 时间 / 频率（Hz）
  descriptions      [num_frames]  string

# ── NODAL 位置结果 ──────────────────────────────────────────
/NODAL/
  <instance_name>/
    labels    [N]                    int32    # node_label，升序
    data      [num_frames, N, ncomp] float32  # 主分量
    invariants [num_frames, N, ninv] float32  # 不变量（如 Mises）

# ── INTEGRATION_POINT 位置结果 ──────────────────────────────
/INTEGRATION_POINT/
  <instance_name>/
    <elem_type>/               # 同类型单元 n_ip 相同，可构成规则数组
      labels    [M]                          int32
      data      [num_frames, M, n_ip, ncomp] float32
      ip_labels [n_ip]                       int32   # 积分点编号

    # 壳单元有截面点，额外维度
    <shell_elem_type>/         # 如 S4R
      labels    [Ms]                                   int32
      data      [num_frames, Ms, n_sp, n_ip, ncomp]   float32
      sp_labels [n_sp]                                 int32   # 截面点编号（BOT/MID/TOP）
      ip_labels [n_ip]                                 int32

# ── 其他位置（ELEMENT_NODAL、CENTROID 等，按需）──────────────
/ELEMENT_NODAL/
  <instance_name>/
    <elem_type>/
      labels    [M]                              int32
      data      [num_frames, M, n_enodes, ncomp] float32
```

---

### 2.6.1 数据形状示例与 bulkDataBlocks 映射

> 本节说明各 dataset 的维度从哪里来、Abaqus 原始数据怎么变成这个形状，及文件大小量级。

#### L369/L370：NODAL `data` / `invariants`

**典型场景**：位移场 U（3 分量），30 帧，Part-1-1 有 50,000 节点

```
labels    [50000]            int32  → node_label 升序排列
data      [30, 50000, 3]     float32  → U1, U2, U3
invariants 不存在（U 无不变量）

文件大小：30 × 50000 × 3 × 4 bytes = 18 MB
```

**应力 S（6 分量 + Mises/Max_Principal 2 个不变量），同规模**：

```
data       [30, 50000, 6]   float32  →  S11 S22 S33 S12 S13 S23   = 36 MB
invariants [30, 50000, 2]   float32  →  Mises, Max_Principal       = 12 MB
```

**bulkDataBlocks → dataset 的映射逻辑**：

```python
# Abaqus NODAL block.data 原始形状：(N, ncomp)
# 不同帧 stack → 最终 [num_frames, N, ncomp]

# abaqus_dump.py 写入策略（在内存中按帧积累，最后写 HDF5）：
nodal_frames = []   # 每帧 append 一个 [N, ncomp]
for frame in step.frames:
    for block in frame.fieldOutputs['S'].bulkDataBlocks:
        if str(block.position) == 'NODAL' and block.instance.name == inst_name:
            # block.data.shape = (N, ncomp)
            # 先按 block.nodeLabels 排序，保证行号对应 labels 升序
            sort_idx = np.argsort(block.nodeLabels)
            nodal_frames.append(block.data[sort_idx])   # [N, ncomp]

data_array = np.stack(nodal_frames, axis=0)   # [num_frames, N, ncomp]
# 分别处理 invariants：block.data 中不含不变量，需另取
# （NODAL invariants 通过 getSubset 或单独 fieldOutputs 获取，见 Blocker A）
```

---

#### L377：INTEGRATION_POINT 实体单元 `data`

**典型场景**：应力 S，C3D8R（8节点六面体，8个积分点），5,000 单元，30 帧，6 分量

```
labels    [5000]              int32   → elem_label 升序
ip_labels [8]                 int32   → [1, 2, 3, 4, 5, 6, 7, 8]
data      [30, 5000, 8, 6]   float32

文件大小：30 × 5000 × 8 × 6 × 4 bytes = 28.8 MB
```

**bulkDataBlocks 原始形状**（关键）：

```
Abaqus 原始：block.data.shape = (M × n_ip, ncomp)
             block.elementLabels.shape = (M × n_ip,)  ← 每 IP 重复一次 elem_label
             block.integrationPointLabels.shape = (M × n_ip,)

例（M=5000, n_ip=8, ncomp=6）：
  elementLabels:         [1, 1, 1, 1, 1, 1, 1, 1,   2, 2, 2, ...,  5000, 5000...]
  integrationPointLabels:[1, 2, 3, 4, 5, 6, 7, 8,   1, 2, 3, ...,  1, 2, ...]
  data:                  shape (40000, 6)
```

**reshape 逻辑**：

```python
# block.data.shape = (M * n_ip, ncomp)
# 先按 elem_label 排序，再按 ip_label 排序
labels_flat = np.array(block.elementLabels)
ip_flat     = np.array(block.integrationPointLabels)
data_flat   = np.array(block.data, dtype=np.float32)

# 获取有序的唯一 elem_labels 和 ip_labels
unique_elems = np.unique(labels_flat)          # [M]  升序
unique_ips   = np.unique(ip_flat)              # [n_ip]
M, n_ip = len(unique_elems), len(unique_ips)

# 建立二维索引映射
elem_map = {v: i for i, v in enumerate(unique_elems)}
ip_map   = {v: i for i, v in enumerate(unique_ips)}
data_3d  = np.zeros((M, n_ip, data_flat.shape[1]), dtype=np.float32)
for row, (el, ip) in enumerate(zip(labels_flat, ip_flat)):
    data_3d[elem_map[el], ip_map[ip], :] = data_flat[row]
# data_3d.shape = (M, n_ip, ncomp) → stack 帧后得 [num_frames, M, n_ip, ncomp]
```

---

#### L383：INTEGRATION_POINT 壳单元 `data`（含截面点）

**典型场景**：应力 S，S4R（4节点壳，4个积分点，3个截面点 BOT/MID/TOP），20,000 单元，30 帧，6 分量

```
labels    [20000]                   int32
sp_labels [3]                       int32   → [1, 2, 3]  (BOT=1, MID=2, TOP=3)
ip_labels [4]                       int32   → [1, 2, 3, 4]
data      [30, 20000, 3, 4, 6]      float32

文件大小：30 × 20000 × 3 × 4 × 6 × 4 bytes = 172.8 MB
         （实际较大；若仅存 MID 截面点则缩减为 57.6 MB）
```

**bulkDataBlocks 原始形状**（⚠️ 需 PoC 验证具体属性名）：

```
预期：block.data.shape = (M × n_sp × n_ip, ncomp)
      block.elementLabels.shape       = (M × n_sp × n_ip,)
      block.sectionPointNumber 或      ← 属性名待 PoC 确认
      block.integrationPointLabels.shape = (M × n_sp × n_ip,)

reshape 思路与实体单元类似，只是多一层 n_sp 维度：
  data_4d.shape = (M, n_sp, n_ip, ncomp)
  → stack 帧后 [num_frames, M, n_sp, n_ip, ncomp]
```

> **注**：若 Blocker A PoC 证实壳单元有 NODAL 外推，则 L383 的截面点维度不需要传给前端做渲染（仅保留原始数据供 L1 raw 查询）；前端渲染走 L369（NODAL 平滑法向）。

---

#### L392：ELEMENT_NODAL `data`

**典型场景**：应力 S（ELEMENT_NODAL），C3D8R，5,000 单元，每单元 8 节点，30 帧，6 分量

```
labels    [5000]                    int32
data      [30, 5000, 8, 6]         float32

文件大小：30 × 5000 × 8 × 6 × 4 bytes = 28.8 MB
```

**bulkDataBlocks 原始形状**：

```
block.data.shape = (M × n_enodes, ncomp)
block.elementLabels 重复每单元 n_enodes 次
reshape → (M, n_enodes, ncomp) → stack → [num_frames, M, n_enodes, ncomp]
```

**与 INTEGRATION_POINT 的区别**：ELEMENT_NODAL 是积分点值外推到该单元各节点的结果；每单元独立一份（相邻单元的公共节点有不同值）。用于 flat shading 时取 `.mean(axis=-2)` 得到单元平均值。

---

#### 跨 frame 写入策略与内存说明

```python
# 推荐写入策略：分帧写入（避免一次性在内存中持有所有帧）
with h5py.File(result_path, 'w') as f:
    # 先创建空 dataset（提前知道 num_frames）
    ds = f.create_dataset('NODAL/Part-1-1/data',
                          shape=(num_frames, N, ncomp), dtype='float32',
                          chunks=(1, min(N, 8192), ncomp), compression='lzf')
    # 逐帧填写
    for frame_idx, frame in enumerate(step.frames):
        data_this_frame = extract_nodal_frame(frame, inst_name, field_name)  # [N, ncomp]
        ds[frame_idx, :, :] = data_this_frame   # 一次 IO 写一帧

# 内存峰值：仅一帧数据，如 [50000, 6] float32 = 1.2 MB（可控）
```

**参见**：Section 9.2 有完整的 bulkDataBlocks 遍历骨架；Section 9.5 有 HDF5 chunk shape 策略；Section 5.2 有读取侧的维度切片代码（`frame_idx, :, sp_idx, ip_idx, comp_idx`）。

---

**两种高频查询的切片方式：**

```python
# 渲染：取某帧全量数据（一次 IO）
values = h5['NODAL/Part-1-1/data'][frame_idx, :, comp_idx]      # [N]

# 时程：取某节点所有帧（一次 IO）
values = h5['NODAL/Part-1-1/data'][:, node_row, comp_idx]       # [num_frames]
```

---

## 3. Layer 2：预处理

### 3.1 文件结构

```
l2/
  geometry/
    <instance_name>_surface.h5   # 表面几何、索引关系
  render/
    <instance_name>_render.h5    # 渲染 Buffer + 八叉树
```

L2 独立于 L1，不修改任何 L1 文件。
全局坐标（应用 Instance 变换矩阵后）在此阶段计算。

---

### 3.2 l2/geometry/\<instance\>\_surface.h5

```
/nodes/
  labels          [N]      int32
  coords_global   [N, 3]   float32   # 全局坐标（L1 局部坐标 × 变换矩阵）
  normals         [N, 3]   float32   # 节点平滑法向（面积加权平均）

/faces/
  # 三角化后的面片，保留与原始面、单元的完整映射
  tri_positions   [Tf, 3, 3]  float32  # Tf 三角面片，3顶点，xyz（全局坐标）
  tri_normals     [Tf, 3, 3]  float32  # 逐顶点平滑法向
  # 索引关系（三层映射）
  tri_face_idx    [Tf]        int32    # → L1 /elements/<type>/face_* 面定义表的共享行号
  tri_elem_row    [Tf]        int32    # → L1 /elements/<type>/labels 中的行号
  tri_elem_type   [Tf]        uint8    # 单元类型编码
  tri_node_rows   [Tf, 3]     int32    # → L1 /nodes/labels 中的行号（角节点）

/surface_faces/
  # 仅表面面片（剔除内部面后，用于渲染）
  face_rows       [Sf]        int32    # → /faces/ 中的行号（Sf ≤ Tf）
  is_boundary     [Sf]        bool     # 是否为模型边界面

/edges/
  # Feature Edges（原始单元边，剔除重复 + 按几何特征过滤）
  positions       [Ee, 2, 3]  float32
  elem_row        [Ee]        int32    # → L1 /elements/<type>/labels 行号
  edge_type       [Ee]        uint8    # 0=边界边, 1=折角边, 2=分区边界

/partitions/
  # 几何特征分区（Instance 边界 + 法向特征角）
  tri_partition   [Tf]        int32    # 每个三角面片所属分区 ID
  partition_meta  [Np]        # 分区元信息（bbox、面片数量）
```

---

### 3.3 l2/render/\<instance\>\_render.h5

```
/render/
  # Triangle Soup，全局坐标，chunk = 切片
  positions       [Rf, 3, 3]   float32   # 仅表面面片（= surface_faces 对应的三角化结果）
  normals         [Rf, 3, 3]   float32   # 平滑法向展开
  render_face_idx [Rf]         int32     # 全局唯一，用于点击拾取和结果对齐
  source_elem_row [Rf]         int32     # → L1 /elements/<type>/labels 行号
  source_node_rows [Rf, 3]     int32     # → L1 /nodes/labels 行号（角节点）
  source_elem_type [Rf]        uint8     # 单元类型编码

/octree/
  # 八叉树空间索引（用于 bbox 查询、LOD、区域轻量化）
  node_bbox       [Nnodes, 6]  float32   # 每个八叉树节点的 bbox [xmin,ymin,zmin,xmax,ymax,zmax]
  node_children   [Nnodes, 8]  int32     # 子节点索引（-1=叶节点）
  node_faces      [Nnodes]     int32     # 叶节点起始 face 偏移
  leaf_faces      [Rf]         int32     # 叶节点包含的 render_face_idx（排序后）
  leaf_offsets    [Nleaves+1]  int32     # CSR 偏移数组

/render_partition/
  # 每面片所属分区（用于区域轻量化分批加载）
  tri_partition   [Rf]         int32    # 分区 ID（与 surface.h5 /partitions/ 对齐）

/edges/
  # 渲染用边线（来自 l2/geometry/edges，全局坐标）
  positions       [Ee, 2, 3]   float32
  render_edge_idx [Ee]         int32
```

---

## 4. 统一索引：manifest.db

**一个 SQLite 文件，覆盖 L1 + L2，提供跨层路由。**

```sql
-- ── L1 文件索引 ────────────────────────────────────────────

CREATE TABLE instances (
  instance_name  TEXT PRIMARY KEY,
  part_name      TEXT,
  geom_path      TEXT,   -- l1/geometry/<name>.h5
  highorder_path TEXT,   -- l1/geometry/<name>_highorder.h5，NULL 若无高阶单元
  node_count     INTEGER,
  elem_count     INTEGER,
  bbox_min       TEXT,   -- JSON [x,y,z]，全局坐标，快速粗筛
  bbox_max       TEXT
);

CREATE TABLE element_type_dist (
  instance_name  TEXT,
  elem_type      TEXT,   -- 'S4R', 'C3D8R', 'C3D20', ...
  count          INTEGER,
  has_midnodes   INTEGER, -- 1=高阶单元
  n_corner_nodes INTEGER,
  n_faces        INTEGER,
  PRIMARY KEY (instance_name, elem_type)
);

CREATE TABLE steps (
  step_name   TEXT PRIMARY KEY,
  step_number INTEGER,
  procedure   TEXT,       -- 'STATIC', 'FREQUENCY', 'DYNAMIC'
  num_frames  INTEGER
);

CREATE TABLE frames (
  step_name    TEXT,
  frame_idx    INTEGER,   -- results.h5 第一维下标
  frame_value  FLOAT,     -- 时间 / 频率(Hz)
  description  TEXT,
  PRIMARY KEY (step_name, frame_idx)
);

-- 结果文件路由表（核心路由入口）
CREATE TABLE result_files (
  step_name    TEXT,
  field_name   TEXT,      -- 'S', 'U', 'PEEQ', 'RF' 等
  file_path    TEXT,      -- l1/results/<step>__<field>.h5
  components   TEXT,      -- JSON 数组: ['S11','S22','S33','S12','S13','S23']
  invariants   TEXT,      -- JSON 数组: ['Mises','Max_Principal']
  positions    TEXT,      -- JSON 数组: ['NODAL','INTEGRATION_POINT']
  has_section  INTEGER,   -- 1=壳截面点
  val_min      REAL,      -- 跨所有帧、所有 instance 的全局最小
  val_max      REAL,
  PRIMARY KEY (step_name, field_name)
);

-- 每个结果文件内的 DataBlock 目录
-- （按 instance × position × elem_type 分块，查结果块不需要扫描文件）
CREATE TABLE result_blocks (
  step_name     TEXT,
  field_name    TEXT,
  instance_name TEXT,
  position      TEXT,     -- 'NODAL', 'INTEGRATION_POINT', 'ELEMENT_NODAL'
  elem_type     TEXT,     -- NULL 表示 NODAL（不区分单元类型）
  h5_path       TEXT,     -- 文件内 Instance 组路径，如 '/NODAL/Part-1-1'（不含 /data）
                          -- 查询时拼接：f"{h5_path}/data" 或 f"{h5_path}/invariants"
  label_path    TEXT,     -- 对应 labels 数组路径，如 '/NODAL/Part-1-1/labels'
  n_entities    INTEGER,  -- N（节点数）或 M（单元数）
  n_ip          INTEGER,  -- 积分点数，NODAL 时为 NULL
  n_sp          INTEGER,  -- 截面点数，非壳单元时为 NULL
  PRIMARY KEY (step_name, field_name, instance_name, position, elem_type)
);

-- 集合目录
-- Assembly Set 和 Instance Set 统一按 instance 分行；
-- set_scope 区分来源（'assembly' | instance_name | 'part:<part_name>'）
CREATE TABLE node_sets (
  set_name      TEXT,
  set_scope     TEXT,      -- 'assembly' | instance_name | 'part:<part_name>'
  instance_name TEXT,      -- 数据实际所在 instance（assembly set 也拆分为多行）
  h5_path       TEXT,      -- sets.h5 内路径，指向该 instance 下的 node_labels 数组
  node_count    INTEGER,
  PRIMARY KEY (set_name, instance_name)
);

CREATE TABLE element_sets (
  set_name      TEXT,
  set_scope     TEXT,
  instance_name TEXT,
  h5_path       TEXT,
  element_count INTEGER,
  PRIMARY KEY (set_name, instance_name)
);

-- ── L2 文件索引 ────────────────────────────────────────────

CREATE TABLE l2_instances (
  instance_name  TEXT PRIMARY KEY,
  surface_path   TEXT,   -- l2/geometry/<name>_surface.h5
  render_path    TEXT,   -- l2/render/<name>_render.h5
  surface_face_count INTEGER,
  render_face_count  INTEGER,
  edge_count         INTEGER,
  partition_count    INTEGER
);

-- 用户集合（运行时创建，支持跨 Instance；主表 + 子表按 Instance 分片）
CREATE TABLE user_sets (
  us_id        INTEGER PRIMARY KEY,
  name         TEXT UNIQUE,
  created_at   TEXT,
  total_elem_count INTEGER   -- 所有 instance 汇总的单元数
);

-- 每个 instance 的行号数组独立存储，避免混合后无法区分归属
CREATE TABLE user_set_instances (
  us_id         INTEGER,
  instance_name TEXT,
  elem_count    INTEGER,
  -- zlib 压缩 numpy int32 数组，局部行号（仅在该 instance 内有效）
  elem_rows     BLOB,      -- → L1 /elements/<type>/labels 行号
  node_rows     BLOB,      -- → L1 /nodes/labels 行号（NODAL 结果切片用）
  render_rows   BLOB,      -- → L2 /render/render_face_idx 数组
  edge_rows     BLOB,      -- → L2 /edges/render_edge_idx 数组
  PRIMARY KEY (us_id, instance_name)
);

-- label→行号 快速映射（热路径，内存中维护；此表为持久化备份）
CREATE TABLE node_label_index (
  instance_name TEXT,
  node_label    INTEGER,
  h5_row        INTEGER,
  PRIMARY KEY (instance_name, node_label)
);
CREATE TABLE elem_label_index (
  instance_name TEXT,
  elem_label    INTEGER,
  elem_type     TEXT,
  h5_row        INTEGER,
  PRIMARY KEY (instance_name, elem_label)
);
```

---

## 5. Layer 3：前端服务

### 5.1 内存索引（服务启动时构建）

```python
class ModelIndex:
    # 能力标志（由 OdbRegistry 根据 odb_jobs.status 设置）
    is_render_ready: bool   # True = L2 已完成，渲染端点可用；False = 仅 L1 raw 可用

    # 每个 Instance 独立的 label→row 映射（numpy searchsorted）
    node_index:  dict[str, tuple[np.ndarray, np.ndarray]]  # inst → (labels, rows)
    elem_index:  dict[str, tuple[np.ndarray, np.ndarray]]  # inst → (labels, rows)

    # 全局坐标（L2 计算后，is_render_ready=True 时才有效）
    node_coords: dict[str, np.ndarray]   # inst → [N, 3] float32

    # 空间索引（每 Instance 一棵树，is_render_ready=True 时才有效）
    kdtrees:     dict[str, cKDTree]

    # 渲染映射（常驻内存，is_render_ready=True 时才有效）
    render_source: dict[str, np.ndarray]  # inst → source_elem_row [Rf]

    @classmethod
    def from_workspace(cls, ws, status: str) -> "ModelIndex":
        idx = cls()
        idx.load_l1_data(ws)           # 始终加载：label 映射
        if status == "ready":
            idx.load_l2_render_data(ws)  # 有条件加载：坐标、KDTree、render_source
            idx.is_render_ready = True
        else:
            idx.is_render_ready = False
        return idx
```

**API 能力门控**：所有渲染端点（`/mesh/chunk`、`/mesh/edges`、`/query/bbox` 等）须在处理前判断 `model_index.is_render_ready`；若为 `False` 返回 `HTTP 202 Accepted`（含 `Retry-After` 响应头），不返回 404 或 500。

### 5.2 结果查询路由逻辑

#### 路由完整流程（含 component / invariant 维度切片）

```python
def query_result(step, field, frame_idx, position, instance,
                 component=None, section_point=None, integration_point=None,
                 elem_type=None, elem_set=None):

    # ── Step 1: 从 manifest.db 定位 block ─────────────────────────
    # NODAL：elem_type=None；INTEGRATION_POINT 必须指定 elem_type
    block = db.query_result_block(
        step, field, instance, position, elem_type=elem_type
    )
    file_meta = db.query_result_file(step, field)  # 含 components/invariants 列表

    # ── Step 2: 确定 sub-dataset 路径与最后一维索引 ────────────────
    if component in json.loads(file_meta.invariants):
        sub_path = "invariants"
        comp_idx = json.loads(file_meta.invariants).index(component)
    else:
        sub_path = "data"
        comp_idx = json.loads(file_meta.components).index(component)

    dataset_path = f"{block.h5_path}/{sub_path}"
    # block.h5_path 示例: '/NODAL/Part-1-1'  → dataset: '/NODAL/Part-1-1/data'

    # ── Step 3: 维度切片（根据 position 和可选截面点/积分点）─────────
    with h5py.File(block.file_path, 'r') as f:
        labels = f[block.label_path][:]

        ds = f[dataset_path]
        if position == "NODAL":
            # [num_frames, N, ncomp]
            values = ds[frame_idx, :, comp_idx]                        # [N]

        elif position == "INTEGRATION_POINT" and block.n_sp:
            # 壳单元截面点: [num_frames, M, n_sp, n_ip, ncomp]
            sp_idx = section_point if section_point is not None else 0
            ip_idx = integration_point if integration_point is not None else 0
            values = ds[frame_idx, :, sp_idx, ip_idx, comp_idx]        # [M]

        elif position == "INTEGRATION_POINT":
            # 实体单元: [num_frames, M, n_ip, ncomp]
            ip_idx = integration_point if integration_point is not None else 0
            values = ds[frame_idx, :, ip_idx, comp_idx]                # [M]

        elif position == "ELEMENT_NODAL":
            # [num_frames, M, n_enodes, ncomp]；取所有节点均值供 flat shading
            values = ds[frame_idx, :, :, comp_idx].mean(axis=-1)       # [M]

    # ── Step 4: 集合过滤 → 稀疏返回 ───────────────────────────────
    if elem_set:
        rows = get_set_rows(elem_set, position, instance)  # node_rows 或 elem_rows
        return labels[rows], values[rows]                  # 稀疏子集
    return labels, values
```

**关键约定**：
- INTEGRATION_POINT 查询**必须携带 `elem_type`**；前端通过 `source_elem_type` 字段得知当前 Instance 有哪些 elem_type，分别请求后在 GPU 侧合并
- `result_files.components` / `result_files.invariants` 为 JSON 数组，是确定 `comp_idx` 的唯一来源
- `block.h5_path` 已是 dataset 父路径（如 `/NODAL/Part-1-1`），拼接 `/data` 或 `/invariants` 得到完整 dataset 路径（不可再追加 `/data`，否则路径错误）

**跨 Instance 用户集合的结果查询**：当 `elem_set` 为跨 Instance 的 `user_set` 时，需遍历 `user_set_instances` 中所有 instance 行，分别执行切片，再拼接返回：

```python
def query_user_set_result(us_id, step, field, frame_idx, position, component):
    rows = db.query("SELECT instance_name, elem_rows, node_rows FROM user_set_instances WHERE us_id=?", us_id)
    results = []
    for inst_row in rows:
        inst = inst_row.instance_name
        set_rows = decompress(inst_row.node_rows if position == "NODAL" else inst_row.elem_rows)
        labels, values = query_result(step, field, frame_idx, position, inst,
                                      component=component, elem_set=set_rows)
        results.append((inst, labels, values))
    return results  # 前端按 instance 分段消费
```

### 5.3 核心 API 端点

所有查询端点均带 `/api/odb/{odb_id}/` 前缀（见第 8 章）。
作业管理端点（`/api/jobs/...`）不带 odb_id 前缀。

```
# ── 模型信息 ──────────────────────────────────────────────
GET /api/odb/{odb_id}/model/info
→ JSON: { instances, steps, frames, field_outputs, sets }

GET /api/odb/{odb_id}/model/instance/{name}/info
→ JSON: { node_count, elem_count, elem_types, bbox, sections, materials }

# ── 几何加载 ──────────────────────────────────────────────
GET /api/odb/{odb_id}/mesh/chunk?instance=<name>&chunk=<n>&set=<set>
→ octet-stream: [positions, normals, source_elem_row, source_node_rows, render_face_idx]

GET /api/odb/{odb_id}/mesh/edges?instance=<name>&set=<set>
→ octet-stream: [positions, render_edge_idx]

GET /api/odb/{odb_id}/mesh/node-normals?instance=<name>&set=<set>&scale=0.01
→ octet-stream: [line segments]

# ── 结果查询 ──────────────────────────────────────────────
GET /api/odb/{odb_id}/result/field
Query: step= & frame_idx= & field= & component= & instance= & position=
      & elem_type=   # INTEGRATION_POINT 必填；NODAL 忽略
      & section_point= & integration_point= & set=

# 响应格式根据 position 不同：
#
# position=NODAL（节点连续）：
#   响应头: X-Position=NODAL, X-Count=<N>
#   Body: [values: N × 4 bytes float32]
#   有 set → 响应头: X-Sparse=1
#            Body: [rows: N' × 4 bytes int32] + [values: N' × 4 bytes float32]
#
# position=INTEGRATION_POINT（按 elem_type 分段，不可合并为单一数组）：
#   响应头: X-Position=INTEGRATION_POINT, X-Types-Count=<K>
#   Body（K 段，顺序由服务端确定）：
#     每段: [type_code: 1 byte uint8]  # 对应 source_elem_type 编码
#           [count:    4 bytes int32]
#           [values:   count × 4 bytes float32]
#   有 set → 稀疏段：额外 [rows: count' × 4 bytes int32]（每段独立）
#
# 前端消费：根据 render_buffer 的 source_elem_type + source_elem_row
# 定位到对应 type 段，再用 source_elem_row 取单元值 → 逐面片着色

GET /api/odb/{odb_id}/result/datablock
Query: step= & frame_idx= & field= & instance= & position= & elem_type=
→ 返回对应 DataBlock 的完整数组（贴近 ODB bulkDataBlocks 原始访问语义）

GET /api/odb/{odb_id}/history/node?instance=<name>&node_label=<l>&step=<s>&field=<f>&component=<c>
→ JSON: { frames: [{ frame_idx, frame_value, value }] }

GET /api/odb/{odb_id}/history/element?instance=<name>&elem_label=<l>&step=<s>&field=<f>
      &component=<c>&integration_point=<n>&section_point=<n>
→ JSON: { frames: [...] }

# ── 空间查询 ──────────────────────────────────────────────
GET /api/odb/{odb_id}/query/bbox
Query: xmin= & xmax= & ymin= & ymax= & zmin= & zmax=
      & instance= & mode=intersect|contained
→ JSON: { elem_labels, render_face_indices, edge_indices }

GET /api/odb/{odb_id}/query/probe
Query: x= & y= & z= & instance= & step= & frame_idx=
→ JSON: { nearest_node: { label, coords, fields }, nearest_element: { label, type, fields } }

GET /api/odb/{odb_id}/query/pick?render_face_idx=<n>&instance=<name>&step=<s>&frame_idx=<f>
→ JSON: { elem_label, elem_type, face_seq, node_labels, fields }

# ── 节点/单元直接查询（L1 原始数据）──────────────────────
GET /api/odb/{odb_id}/l1/node?instance=<name>&node_label=<l>
→ JSON: { label, coords_local, coords_global }

GET /api/odb/{odb_id}/l1/element?instance=<name>&elem_label=<l>
→ JSON: {
    label, elem_type,
    node_labels: [int],          # 角节点
    faces: [{ seq, node_labels, normal }],
    section, material
  }

# ── 集合管理 ──────────────────────────────────────────────
POST /api/odb/{odb_id}/sets/user
Body: { name, elem_labels: [{ instance, label }] }
→ JSON: { us_id, name, total_elem_count, instances: [{ instance, elem_count }] }
  # 按 instance 分片存入 user_set_instances；支持跨 Instance 集合

GET    /api/odb/{odb_id}/sets/user          → JSON: [{ us_id, name, total_elem_count }]
DELETE /api/odb/{odb_id}/sets/user/{name}   → JSON: { ok: true }  # 同时删除子表行

# ── 变形 / 模态动画 ───────────────────────────────────────
GET /api/odb/{odb_id}/result/displacement?instance=<name>&step=<s>&frame_idx=<f>
→ octet-stream: [N × 3 float32]  # U1, U2, U3，按 node 行号顺序
```

### 5.4 并发模型

```
geometry.h5 / results.h5 / l2 文件：只读
manifest.db / user_sets：SQLite WAL 模式，支持多进程并发写

部署：gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker
h5py IO：await run_in_threadpool(_read)  # 防事件循环阻塞
真正并发吞吐由进程数决定（非线程）
```

---

## 6. 实施流水线

```
── 服务启动 ─────────────────────────────────────────────────────
  · 加载 registry.db
  · 对所有 status IN ('ready', 'l1_done') 的 ODB 并行加载内存索引
  · 启动 FastAPI（gunicorn 4 workers，fork 前完成内存索引构建）
  注：job-runner 作为独立进程单独启动，不在此处启动（见第 8 章）

── 后台 worker（每个 ODB 串行执行 Step 1 → Step 2）────────────

Step 1：abaqus_dump.py（需 Abaqus license，workspace = /data/<odb_id>/）
  · registry.db status → l1_running
  · 遍历 Assembly → Instances
  · 写 l1/assembly.h5（变换矩阵）
  · 写 l1/geometry/<inst>.h5（节点、单元分类、面定义、面法线）
  · 写 l1/geometry/<inst>_highorder.h5（仅高阶单元存在时）
  · 写 l1/sets/sets.h5
  · 遍历 Steps → Frames → FieldOutputs → bulkDataBlocks
    · 同类 block 按帧合并 → 写 l1/results/<step>__<field>.h5
  · 写 manifest.db（instances/steps/frames/result_files/result_blocks/sets 表）
  · registry.db status → l1_done
  · 通知 L3 加载该 ODB 的 L1 索引（可响应 /api/odb/{odb_id}/l1/* 端点）

Step 2：ingest.py（纯 Python，不依赖 Abaqus license）
  · registry.db status → l2_running
  · 读 L1，计算全局坐标（局部坐标 × 变换矩阵）
  · 高阶单元线性化（取角节点，中间节点忽略）
  · 三角化（面定义 → 三角片，建立三层索引）
  · 表面提取（剔除内部面）
  · Feature Edges 提取（边界边 + 折角边 ≥30°）
  · 几何特征分区（Instance 边界 + 法向特征角）
  · 构建八叉树（max_depth=8，叶节点 ≤1000 面片）
  · 写 l2/geometry/<inst>_surface.h5
  · 写 l2/render/<inst>_render.h5
  · 更新 manifest.db（l2_instances 表）
  · 预计算每字段全局 min/max → 更新 result_files 表
  · registry.db status → ready
  · 通知 L3 补充加载该 ODB 的 L2 索引（全部 API 可用）
```

---

## 7. 性能参考

| 操作 | 实现路径 | 预期耗时 |
|------|---------|---------|
| 按节点号查坐标 | manifest → L1 geometry 文件 searchsorted | < 1ms |
| 按单元号查面/法线 | manifest → L1 geometry 文件 | < 1ms |
| 按单元集查结果块 | manifest result_blocks → L1 results 文件稀疏切片 | < 50ms |
| 渲染 chunk（200K 面片）| L2 render 文件切片 | < 200ms |
| 单帧全量结果 | L1 results 文件 frame 切片 | 0.5-1s |
| 节点时程（30帧）| L1 results 文件列切片（一次 IO）| < 10ms |
| 3D bbox 查询 | cKDTree + 八叉树粗筛 | < 50ms |
| 点击拾取 | render_face_idx → manifest | < 5ms |
| 服务启动 | 构建索引 + cKDTree | 30-60s/ODB × 活跃数 |
| 常驻内存（4 workers × N ODB）| 索引 + 坐标 + KDTree | ~2GB × N |

---

## 8. 多 ODB 作业管理

### 8.1 目录结构

每个 ODB 文件对应一个独立 workspace 目录，三层文件完全隔离：

```
/data/
  registry.db                      # 全局作业注册表（唯一）
  <odb_id>/                        # odb_id = 提交时生成的 UUID
    l1/
      assembly.h5
      geometry/<instance>.h5
      geometry/<instance>_highorder.h5
      sets/sets.h5
      results/<step>__<field>.h5
    l2/
      geometry/<instance>_surface.h5
      render/<instance>_render.h5
    manifest.db                    # 该 ODB 的专属索引
```

所有现有章节（L1 / L2 / L3 / manifest.db）描述的路径均为 `<odb_id>/` 下的相对路径。

---

### 8.2 registry.db

全局注册表，与所有 `<odb_id>/manifest.db` 独立存放：

```sql
CREATE TABLE odb_jobs (
  odb_id        TEXT PRIMARY KEY,  -- UUID，提交时生成
  display_name  TEXT,              -- 用户给定的可读名称
  odb_path      TEXT,              -- 原始 .odb 文件绝对路径
  workspace     TEXT,              -- /data/<odb_id>/
  status        TEXT,              -- 见状态机
  created_at    TEXT,
  l1_started_at TEXT,
  l1_done_at    TEXT,
  l2_started_at TEXT,
  l2_done_at    TEXT,              -- NULL 时仍可使用 L1 raw 查询（无渲染）
  error_msg     TEXT,
  odb_size_bytes INTEGER,
  node_count    INTEGER,           -- L1 完成后填入，供前端展示
  instance_count INTEGER
);
```

---

### 8.3 作业状态机

```
submitted
  │
  ▼ 后台 worker 取到作业
l1_running  ──(abaqus_dump.py 失败)──→  error
  │
  ▼ L1 完成，manifest.db 基础表写好
l1_done    ──可响应 L1 raw 查询，但无渲染──
  │
  ▼ 后台 worker 继续
l2_running  ──(ingest.py 失败)──→  l1_done（L2 失败不影响 L1 可用性）
  │
  ▼ L2 完成
ready      ──所有 API 全部可用──
```

**说明**：
- L1 和 L2 串行执行（同一 ODB 顺序排队）
- 多个 ODB 可并行：L1 受 Abaqus license 数量限制（通常 1 个并行）；L2 纯 Python 可多并行
- `l1_done` 状态下 `/api/odb/{odb_id}/l1/*` 端点可用，渲染相关端点返回 `202 Accepted`（含 `Retry-After` 响应头）

**Job-Runner 部署约定（关键）**：job-runner **必须作为独立进程部署**，与 Web 服务（gunicorn）完全分离。不得在 FastAPI `startup` 事件或 worker 进程内启动 job 线程——gunicorn 4 workers 会导致 4 个进程各自争抢同一 ODB 作业，造成重复处理和 SQLite 写冲突。

**作业抢占（原子操作）**：

```sql
-- job-runner 启动时，原子地领取一个 submitted 作业：
UPDATE odb_jobs
SET status = 'l1_running', l1_started_at = datetime('now')
WHERE odb_id = (
  SELECT odb_id FROM odb_jobs WHERE status = 'submitted'
  ORDER BY created_at LIMIT 1
);
-- 检查 changes() == 1 确认成功抢占，否则另一进程已领取
```

SQLite WAL 模式下，此 `UPDATE` 为原子操作，可安全用于多进程竞争。

---

### 8.4 作业管理 API

```
# ── 作业提交与查询 ───────────────────────────────────────
POST /api/jobs
Body: { odb_path: "/path/to/model.odb", display_name: "车身模型-v3" }
→ JSON: { odb_id, display_name, status: "submitted" }

GET  /api/jobs
→ JSON: [{ odb_id, display_name, status, created_at, node_count, instance_count }]

GET  /api/jobs/{odb_id}
→ JSON: { odb_id, display_name, status, l1_done_at, l2_done_at, error_msg, ... }

DELETE /api/jobs/{odb_id}
→ 删除 workspace 目录 + registry 记录 + 卸载内存索引
→ JSON: { ok: true }

POST /api/jobs/{odb_id}/retry
→ 从上次失败步骤重新触发（l1_running 或 l2_running）

# ── 数据查询（所有现有端点加 /odb/{odb_id} 前缀）──────
GET /api/odb/{odb_id}/model/info
GET /api/odb/{odb_id}/mesh/chunk?instance=<name>&chunk=<n>
GET /api/odb/{odb_id}/result/field?step=...
GET /api/odb/{odb_id}/query/pick?render_face_idx=<n>
# ...（其余端点结构不变，均加 /odb/{odb_id}/ 前缀）
```

---

### 8.5 L3 内存管理策略

**当前规模（2–3 个活跃 ODB）**：全量常驻，无需 LRU。

```python
class OdbRegistry:
    # odb_id → ModelIndex（含 label 映射、KDTree、render_source 等）
    loaded: dict[str, ModelIndex] = {}

    def load(self, odb_id: str):
        """L2 完成后或服务启动时调用，根据当前 status 有条件加载索引"""
        job = registry_db.get_job(odb_id)
        self.loaded[odb_id] = ModelIndex.from_workspace(job.workspace, job.status)

    def upgrade(self, odb_id: str):
        """L2 完成时调用：补充加载渲染数据，设 is_render_ready=True"""
        job = registry_db.get_job(odb_id)
        self.loaded[odb_id].load_l2_render_data(job.workspace)
        self.loaded[odb_id].is_render_ready = True

    def unload(self, odb_id: str):
        """DELETE /api/jobs/{odb_id} 时调用"""
        del self.loaded[odb_id]
```

**服务启动流程（多 ODB 版）**：
```
1. 加载 registry.db
2. 对所有 status IN ('ready', 'l1_done') 的 ODB 并行构建内存索引
   （ModelIndex.from_workspace 按 status 条件加载，不会因 L2 文件不存在而崩溃）
3. 启动 FastAPI（gunicorn 4 workers，fork 前完成所有内存索引构建，CoW 最大化共享）
4. Job-Runner 作为独立进程启动（与 gunicorn 完全分离）
```

**未来扩容（> 10 个 ODB）**：改为 LRU 策略，仅常驻最近访问的 N 个 ODB 的内存索引；其余 ODB 保留文件，查询时按需重建（代价：首次查询需 30–60s 重载）。

---

**内存估算（2–3 个活跃 ODB）**：

| ODB 规模 | 单 ODB 乐观估算 | 4 workers 悲观估算（CoW 退化后） | 建议物理机配置 |
|---------|--------------|-------------------------------|------------|
| ~1M 节点 | ~0.5 GB | ~2–3 GB | 8 GB |
| ~10M 节点 | ~2 GB | ~8–10 GB | 16 GB |
| 3 × 10M 节点 | ~6 GB | ~24–30 GB | 32 GB+ |

**乐观估算**：gunicorn fork，workers CoW 共享 ModelIndex，无私有化。
**悲观估算**：Python GC + dict 对象在大量请求后触发内存页私有化，每 worker 各自持有一份索引副本。
部署时建议以**悲观值**为基准预留物理内存。

---

## 9. 实现参考（开发者速查）

> 本章面向不熟悉有限元/Abaqus 的开发者，补充所有"不看这章就会卡死"的领域知识、API 用法和实现规范。

---

### 9.1 ODB 数据模型

ODB（Output Database）是 Abaqus 仿真软件的专有二进制结果文件。

**层次结构**：

```
ODB 文件
├── rootAssembly（装配体）
│   ├── instances                      零件实例列表
│   │   └── <instance_name>（如 "PART-1-1"）
│   │       ├── nodes                  节点：编号 + 局部坐标(x,y,z)
│   │       ├── elements               单元：编号 + 类型字符串 + 连接节点编号列表
│   │       ├── nodeSets               Instance 级节点集（有名字的节点子集）
│   │       └── elementSets            Instance 级单元集
│   ├── nodeSets                       Assembly 级节点集（跨 Instance）
│   └── elementSets                    Assembly 级单元集
└── steps                              分析步列表
    └── <step_name>（如 "Step-1"）
        ├── procedureType              分析类型字符串（见 9.2 节映射表）
        └── frames                     帧列表（按时间/频率排序）
            └── frame[i]
                ├── frameValue         时间(s) 或 频率(Hz)
                └── fieldOutputs       场输出，按字段名索引
                    └── <field>（如 'S' 应力，'U' 位移，'RF' 反力）
                        ├── componentLabels    分量名，如 ['S11','S22','S33','S12','S13','S23']
                        ├── validInvariants    不变量名，如 ['MISES','MAX_PRINCIPAL']
                        └── bulkDataBlocks     数据块列表（核心）
                            └── block
                                ├── position       'NODAL'/'INTEGRATION_POINT'/'ELEMENT_NODAL'
                                ├── instance       所属 Instance 对象
                                ├── elementType    单元类型字符串（NODAL 时为 None）
                                ├── nodeLabels     int32 [N]（NODAL 时）
                                ├── elementLabels  int32 [M]（非 NODAL 时）
                                └── data           float32 [N/M, ncomp]
```

**关键概念对照表**（给非仿真背景开发者）：

| ODB 概念 | 直觉类比 | 补充说明 |
|---------|---------|---------|
| Part | 零件模板 | 几何定义，不含位置 |
| Instance | 对象实例 | Part 放置到 Assembly 的一次，有局部→全局坐标变换 |
| Node（节点）| 顶点 | 有编号（label）和坐标；label 仅在 Instance 内唯一 |
| Element（单元）| 面片/体块 | 由若干节点围成；类型决定形状（壳/四面体/六面体）|
| Field Output | 结果数组 | 每字段一组值，S=应力，U=位移，RF=反力等 |
| Step | 分析阶段 | 一次加载过程，含多帧结果 |
| Frame | 时间帧 | 某时刻全量结果快照 |
| NODAL position | 节点上的值 | 位移 U 是典型 NODAL 结果 |
| INTEGRATION_POINT | 积分点上的值 | 应力 S 通常在积分点上，每单元有若干积分点 |
| ELEMENT_NODAL | 单元节点上的值 | 积分点值外推到该单元的各节点，每单元独立一份 |
| Section point | 壳截面点 | 壳单元在厚度方向分层（BOT底/MID中/TOP顶），每层各有积分点结果 |

---

### 9.2 Abaqus Python API（abaqus_dump.py 实现参考）

`abaqus_dump.py` 必须在 Abaqus 自带的受限 Python 环境中运行，不能用系统 Python：

```bash
abaqus python abaqus_dump.py --odb /path/to/model.odb --out /data/<odb_id>/
```

**核心遍历骨架**：

```python
import odbAccess
import numpy as np

odb = odbAccess.openOdb(path=odb_path, readOnly=True)
assembly = odb.rootAssembly

# ── 1. 遍历 Instance ─────────────────────────────────────────
for inst_name, instance in assembly.instances.items():

    # 节点（局部坐标）
    node_labels = np.array([n.label       for n in instance.nodes], dtype=np.int32)
    node_coords = np.array([n.coordinates for n in instance.nodes], dtype=np.float64)
    # node_coords 是 Instance 局部坐标，L2 乘变换矩阵得全局坐标

    # 单元（按类型分组）
    elem_by_type = {}
    for elem in instance.elements:
        t = elem.type                           # 字符串：'S4R', 'C3D8R', 'C3D20', ...
        conn = list(elem.connectivity)          # 节点 label 列表（Abaqus 原始顺序）
        elem_by_type.setdefault(t, {'labels': [], 'conn': []})
        elem_by_type[t]['labels'].append(elem.label)
        elem_by_type[t]['conn'].append(conn)

    # 变换矩阵（见 9.5 节详细说明）
    T = get_instance_transform(instance)        # [4, 4] float64

    # Instance 级集合
    for set_name, ns in instance.nodeSets.items():
        labels = np.array([n.label for n in ns.nodes], dtype=np.int32)
    for set_name, es in instance.elementSets.items():
        labels = np.array([e.label for e in es.elements], dtype=np.int32)

# ── 2. Assembly 级集合（必须按 Instance 分片）────────────────
for set_name, ns in assembly.nodeSets.items():
    by_inst = {}
    for node in ns.nodes:
        # node.instanceName：节点所属 Instance 名称
        by_inst.setdefault(node.instanceName, []).append(node.label)
    # 写入 /assembly_sets/<set_name>/<inst_name>/node_labels

# ── 3. 结果（bulkDataBlocks 按帧合并）────────────────────────
# 预先收集各 (step, field, inst, position, elem_type) 的所有帧 data，最后 stack
buffers = {}   # key=(step,field,inst,position,elem_type) → list of (frame_idx, labels, data)

for step_name, step in odb.steps.items():
    procedure = PROCEDURE_MAP.get(step.procedureType, step.procedureType)
    for frame_idx, frame in enumerate(step.frames):
        frame_value = frame.frameValue
        for field_name, field in frame.fieldOutputs.items():
            components = list(field.componentLabels)
            invariants  = [str(i) for i in field.validInvariants]
            for block in field.bulkDataBlocks:
                position  = str(block.position)   # 'NODAL', 'INTEGRATION_POINT', ...
                inst_name = block.instance.name if block.instance else '__assembly__'
                elem_type = getattr(block, 'elementType', None)  # 'S4R' 或 None
                labels    = (np.array(block.nodeLabels,    dtype=np.int32) if position == 'NODAL'
                             else np.array(block.elementLabels, dtype=np.int32))
                data      = np.array(block.data, dtype=np.float32)  # [N, ncomp]
                key = (step_name, field_name, inst_name, position, elem_type)
                buffers.setdefault(key, []).append((frame_idx, labels, data))

# 合并同 key 的多帧 → [num_frames, N, ncomp]，写入 results/<step>__<field>.h5

odb.close()
```

**`procedureType` 字符串映射**（Abaqus 原始值 → manifest.db `procedure` 字段）：

| Abaqus `step.procedureType` | manifest `procedure` |
|----------------------------|---------------------|
| `'STATIC_GENERAL'`, `'STATIC_RIKS'` | `'STATIC'` |
| `'FREQUENCY'` | `'FREQUENCY'` |
| `'DYNAMIC_IMPLICIT'`, `'DYNAMIC_EXPLICIT'`, `'DYNAMIC_TEMPDISPLACEMENT'` | `'DYNAMIC'` |
| `'BUCKLE'` | `'BUCKLE'` |
| 其他 | 原样保留字符串 |

**注意事项**：
- 同一帧的同一 `(inst, position, elem_type)` 可能出现多个 bulkDataBlock（边界情况）→ 合并时按 label 排序去重再 stack
- `block.instance` 有时为 `None`（装配体级结果），此时跳过或归入 `__assembly__`
- Abaqus 的 `validInvariants` 是枚举对象，用 `str()` 转字符串：`'MISES'`/`'MAX_PRINCIPAL'` 等

---

### 9.3 单元类型参考表

**常用单元类型**（几乎覆盖 95% 工程模型）：

| elem_type 字符串 | 描述 | 角节点数 | 总节点数 | 面数 | 是否高阶 | uint8 code |
|----------------|------|---------|---------|------|---------|-----------|
| `S3`, `S3R` | 3节点三角形壳 | 3 | 3 | 1 | ✗ | **0** |
| `S4`, `S4R`, `S4R5` | 4节点四边形壳 | 4 | 4 | 1 | ✗ | **1** |
| `S6` | 6节点三角形壳（高阶）| 3 | 6 | 1 | ✓ | **0** |
| `S8R`, `S8R5` | 8节点四边形壳（高阶）| 4 | 8 | 1 | ✓ | **1** |
| `C3D4`, `C3D4H` | 4节点四面体 | 4 | 4 | 4 | ✗ | **2** |
| `C3D6`, `C3D6H` | 6节点三棱柱（楔形）| 6 | 6 | 5 | ✗ | **3** |
| `C3D8`, `C3D8R`, `C3D8H` | 8节点六面体 | 8 | 8 | 6 | ✗ | **4** |
| `C3D10`, `C3D10M`, `C3D10H` | 10节点四面体（高阶）| 4 | 10 | 4 | ✓ | **5** |
| `C3D15`, `C3D15H` | 15节点三棱柱（高阶）| 6 | 15 | 5 | ✓ | **6** |
| `C3D20`, `C3D20R`, `C3D20H` | 20节点六面体（高阶）| 8 | 20 | 6 | ✓ | **7** |

**编码字典**（abaqus_dump.py 与 ingest.py 共用同一份）：

```python
ELEM_TYPE_CODE = {
    'S3': 0, 'S3R': 0, 'S6': 0,
    'S4': 1, 'S4R': 1, 'S4R5': 1, 'S8R': 1, 'S8R5': 1,
    'C3D4': 2, 'C3D4H': 2,
    'C3D6': 3, 'C3D6H': 3,
    'C3D8': 4, 'C3D8R': 4, 'C3D8H': 4, 'C3D8RH': 4,
    'C3D10': 5, 'C3D10M': 5, 'C3D10H': 5,
    'C3D15': 6, 'C3D15H': 6,
    'C3D20': 7, 'C3D20R': 7, 'C3D20H': 7, 'C3D20RH': 7,
}
ELEM_N_CORNER = {0: 3, 1: 4, 2: 4, 3: 6, 4: 8, 5: 4, 6: 6, 7: 8}  # code → 角节点数
HIGH_ORDER_CODES = {5, 6, 7}   # 需写 _highorder.h5 的 code 集合
```

**六面体 C3D8R 面定义**（Abaqus 标准，索引 0-based 对应 `connectivity` 顺序）：

```
节点编号（0-based，角节点）：
    7─────6
   /│    /│
  4─────5 │
  │ 3───│─2
  │/    │/
  0─────1

S1（底）：[0, 1, 2, 3]    S2（顶）：[4, 5, 6, 7]
S3（前）：[0, 1, 5, 4]    S4（右）：[1, 2, 6, 5]
S5（后）：[2, 3, 7, 6]    S6（左）：[3, 0, 4, 7]
法向：右手定则，从外向内看节点逆时针
```

**四面体 C3D4 面定义**：
```
S1：[0, 1, 2]    S2：[0, 3, 1]    S3：[1, 3, 2]    S4：[2, 3, 0]
```

**壳单元 S4R 面定义**：
```
仅 S1（单面），节点 [0, 1, 2, 3]（按 connectivity 顺序）
渲染时双面可见，法向取正面（前进方向右手定则）
```

**高阶单元处理规则**：L2 三角化时只取 `connectivity[:n_corner]`（角节点），面定义与对应低阶单元完全相同。

---

### 9.4 关键算法伪代码（ingest.py）

#### 9.4.1 三角化（多边形面 → 三角形）

```python
def triangulate_face(face_node_labels: list) -> list:
    """将 n 边形面片扇形分割为三角形，返回 [(n0, n1, n2), ...]"""
    n = len(face_node_labels)
    if n == 3:
        return [tuple(face_node_labels)]
    elif n == 4:
        # 四边形沿 0-2 对角线切割（两个三角形）
        return [(face_node_labels[0], face_node_labels[1], face_node_labels[2]),
                (face_node_labels[0], face_node_labels[2], face_node_labels[3])]
    else:
        # 通用扇形（以第 0 个节点为扇心）
        return [(face_node_labels[0], face_node_labels[i], face_node_labels[i+1])
                for i in range(1, n - 1)]
```

#### 9.4.2 表面提取（剔除内部共享面）

```python
from collections import defaultdict

def extract_surface(all_faces):
    """
    all_faces: [(elem_label, face_seq, [node_labels])]
               每条是一个单元的一个面，node_labels 是该面的节点 label 列表
    返回：表面面片列表（只属于一个单元的面）
    """
    face_owners = defaultdict(list)
    for elem_label, face_seq, node_labels in all_faces:
        key = frozenset(node_labels)    # 顺序无关（正反面视为同一面）
        face_owners[key].append((elem_label, face_seq))

    surface = []
    for key, owners in face_owners.items():
        if len(owners) == 1:            # 边界面：只有一个单元拥有
            surface.append(owners[0])
        # len == 2：两个单元共享 → 内部面，丢弃
    return surface
```

#### 9.4.3 Feature Edges（边界边 + 折角边）

```python
import numpy as np
from collections import defaultdict

def extract_feature_edges(surface_tris, node_coords_global, angle_deg=30.0):
    """
    surface_tris: [(node0_label, node1_label, node2_label), ...]  表面三角面片节点 label
    node_coords_global: dict {label: np.array([x,y,z])}
    返回：(boundary_edges, fold_edges)  各为 [(label_a, label_b)] 列表
    """
    # 计算每个三角面片的法向
    def tri_normal(a, b, c):
        v1 = node_coords_global[b] - node_coords_global[a]
        v2 = node_coords_global[c] - node_coords_global[a]
        n  = np.cross(v1, v2)
        norm = np.linalg.norm(n)
        return n / norm if norm > 1e-12 else n

    tri_normals = [tri_normal(*tri) for tri in surface_tris]

    # 收集每条边属于哪些三角面片
    edge_tris = defaultdict(list)  # frozenset({a,b}) → [tri_idx]
    for tri_idx, (a, b, c) in enumerate(surface_tris):
        for ea, eb in [(a, b), (b, c), (c, a)]:
            edge_tris[frozenset([ea, eb])].append(tri_idx)

    boundary_edges, fold_edges = [], []
    for edge, tris in edge_tris.items():
        if len(tris) == 1:
            boundary_edges.append(tuple(edge))
        elif len(tris) == 2:
            n1, n2 = tri_normals[tris[0]], tri_normals[tris[1]]
            cos_a  = np.clip(np.dot(n1, n2), -1.0, 1.0)
            if np.degrees(np.arccos(cos_a)) > angle_deg:
                fold_edges.append(tuple(edge))
    return boundary_edges, fold_edges
```

---

### 9.5 实现规范（不能模糊的细节）

#### render_face_idx 的作用域与分配规则

`render_face_idx` 是 **per-Instance 内的连续整数**，从 0 开始，最大值 = `Rf - 1`。
**不同 Instance 各自独立编号**，pick 查询同时传 `instance + render_face_idx` 定位唯一面片。

```python
# ingest.py 中分配方式：
render_face_idx = np.arange(len(surface_face_rows), dtype=np.int32)
# 写入 render.h5 /render/render_face_idx
```

#### Chunk 的定义与大小

Chunk 是 Triangle Soup 的连续切片，大小固定：

```python
CHUNK_SIZE = 200_000   # 每 chunk 200K 面片 ≈ 7.2 MB float32 positions

def chunk_count(render_face_count):
    import math
    return math.ceil(render_face_count / CHUNK_SIZE)

# API chunk=<n> 对应切片：[n * CHUNK_SIZE : (n+1) * CHUNK_SIZE]
# model/instance/info 返回的 chunk_count 字段由此计算
```

前端按 `chunk_count` 循环请求，最后一个 chunk 可能不足 `CHUNK_SIZE`。

#### Instance 变换矩阵的获取

```python
import numpy as np

def get_instance_transform(instance) -> np.ndarray:
    """返回 4×4 齐次变换矩阵（局部→全局）"""
    csys = getattr(instance, 'localCsys', None)
    if csys is None:
        return np.eye(4, dtype=np.float64)   # 未移动的 Instance

    # ⚠️ 下列属性名需在真实 ODB 上验证（Abaqus 版本差异）
    origin = np.array(csys.origin,  dtype=np.float64)   # 平移向量 [3]
    x_axis = np.array(csys.xAxis,   dtype=np.float64)   # 旋转矩阵第一列
    y_axis = np.array(csys.yAxis,   dtype=np.float64)
    z_axis = np.array(csys.zAxis,   dtype=np.float64)

    T = np.eye(4, dtype=np.float64)
    T[:3, 0] = x_axis
    T[:3, 1] = y_axis
    T[:3, 2] = z_axis
    T[:3, 3] = origin
    return T

# L2 应用变换（ingest.py）：
# coords_local: [N, 3] float64
# coords_global = (T[:3, :3] @ coords_local.T).T + T[:3, 3]
```

#### HDF5 写入规范

结果 dataset 支持两种高频访问模式，用折中 chunk shape 平衡两者：

```python
import h5py

# [num_frames, N, ncomp] dataset
with h5py.File(path, 'w') as f:
    f.create_dataset(
        'data',
        shape=(num_frames, N, ncomp),
        dtype='float32',
        # 折中 chunk：单帧读取快（dim0=1），时程读取可接受（dim1 小块）
        chunks=(1, min(N, 8192), ncomp),
        compression='lzf',   # 比 gzip 读取更快，压缩比略低
    )
```

几何 dataset（`positions`、`normals` 等）不需要压缩，顺序读取，chunk 与行数对齐：

```python
f.create_dataset('positions', data=arr, dtype='float32')  # 不压缩，mmap 直读
```

---

### 9.6 job-runner 与 L3 通知机制

**方案：L3 轮询 registry.db**（无 IPC，简单可靠）

```
进程 A：gunicorn（Web 服务）
进程 B：python job_runner.py（独立进程，supervisor/systemd 管理）

两者唯一共享点：registry.db（SQLite WAL 模式，安全多进程读写）
```

**L3 轮询线程**（在 gunicorn pre-fork 前启动，每个 worker 各有一个）：

```python
import threading, time

def poll_registry_loop(odb_registry, registry_db_path, interval=10):
    while True:
        with sqlite3.connect(registry_db_path) as conn:
            conn.row_factory = sqlite3.Row
            jobs = conn.execute(
                "SELECT odb_id, status FROM odb_jobs WHERE status IN ('ready','l1_done')"
            ).fetchall()
        for job in jobs:
            oid, status = job['odb_id'], job['status']
            if oid not in odb_registry.loaded:
                odb_registry.load(oid)                       # 新 ODB，全量加载
            elif status == 'ready' and not odb_registry.loaded[oid].is_render_ready:
                odb_registry.upgrade(oid)                    # L2 刚完成，补充加载
        time.sleep(interval)

# 在 FastAPI lifespan 中：
threading.Thread(target=poll_registry_loop, args=(...), daemon=True).start()
```

**job-runner 主循环**：

```python
# job_runner.py
import sqlite3, subprocess, time

def claim_job(conn):
    """原子抢占一个 submitted 作业，返回 odb_id 或 None"""
    cur = conn.execute("""
        UPDATE odb_jobs SET status='l1_running', l1_started_at=datetime('now')
        WHERE odb_id = (
          SELECT odb_id FROM odb_jobs WHERE status='submitted'
          ORDER BY created_at LIMIT 1
        )
    """)
    conn.commit()
    if cur.rowcount == 0:
        return None
    return conn.execute("SELECT odb_id FROM odb_jobs WHERE status='l1_running'").fetchone()[0]

while True:
    with sqlite3.connect(registry_db) as conn:
        odb_id = claim_job(conn)
    if odb_id:
        ws = f"/data/{odb_id}"
        # L1
        ret = subprocess.run(["abaqus", "python", "abaqus_dump.py",
                              "--odb", get_odb_path(odb_id), "--out", ws])
        status = 'l1_done' if ret.returncode == 0 else 'error'
        update_status(odb_id, status)
        # L2（L1 成功才继续）
        if status == 'l1_done':
            ret = subprocess.run(["python", "ingest.py", "--workspace", ws])
            update_status(odb_id, 'ready' if ret.returncode == 0 else 'l1_done')
    time.sleep(5)
```

---

### 9.7 未解决 Blocker（开发前必跑 PoC）

#### Blocker A：Shell 截面点 NODAL 外推（高优先级）

**背景**：壳单元应力 S 默认在 `INTEGRATION_POINT` 位置，有 BOT/MID/TOP 三个截面点。若能用 `getSubset(position=NODAL)` 外推到节点，则 Smooth 渲染模式完整可用。若不能，Smooth 模式降级。

**PoC 脚本**（需要含壳单元且有截面点应力输出的 ODB，约 5 分钟）：

```python
# abaqus python poc_shell_nodal.py
import odbAccess

odb  = odbAccess.openOdb('test_shell.odb', readOnly=True)
step = odb.steps.values()[-1]
frame = step.frames[-1]
field_S = frame.fieldOutputs['S']

print("原始 bulkDataBlocks position:")
for b in field_S.bulkDataBlocks:
    print(f"  {b.instance.name}  pos={b.position}  elem={b.elementType}"
          f"  data.shape={b.data.shape}")

# 尝试 getSubset → NODAL
inst = odb.rootAssembly.instances.values()[0]
try:
    s_nodal = field_S.getSubset(region=inst, position=odbAccess.NODAL)
    print("\ngetSubset NODAL 成功:")
    for b in s_nodal.bulkDataBlocks:
        print(f"  pos={b.position}  shape={b.data.shape}")
except Exception as e:
    print(f"\ngetSubset NODAL 失败: {e}")

odb.close()
```

**结果对策**：
- **成功** → L1 直接存储 NODAL 外推值，Smooth 渲染按节点插值着色
- **失败** → Smooth 模式降级：取 INTEGRATION_POINT 各截面点/积分点均值后插值（精度低于真正的节点外推，文档标注为"近似平滑"）

#### Blocker B：Instance 变换矩阵 API（中优先级）

在真实 ODB 上打印 `instance.localCsys` 的类型和属性，确认 9.5 节代码中 `origin`/`xAxis`/`yAxis`/`zAxis` 属性名在当前 Abaqus 版本是否正确。

```python
# abaqus python poc_transform.py
import odbAccess
odb = odbAccess.openOdb('test.odb', readOnly=True)
for name, inst in odb.rootAssembly.instances.items():
    csys = getattr(inst, 'localCsys', None)
    print(f"{name}: localCsys={csys}, type={type(csys)}")
    if csys:
        print("  attributes:", dir(csys))
odb.close()
```

预计 30 分钟内可验证。若属性名不同，更新 9.5 节代码即可，不影响整体架构。
