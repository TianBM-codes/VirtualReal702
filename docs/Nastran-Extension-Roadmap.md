# Nastran 格式扩展技术路线

> 版本：v3  
> 创建时间：2026-05-09  
> 更新时间：2026-05-09（v3：根据 Codex 审阅意见修正 §1/§5/§7/§8/§9/§11，新增 §12）  
> 作者：技术对话整理

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [Nastran 文件格式说明](#2-nastran-文件格式说明)
3. [概念对照：Nastran vs Abaqus ODB](#3-概念对照nastran-vs-abaqus-odb)
4. [架构适配策略](#4-架构适配策略)
5. [L1 层详细设计](#5-l1-层详细设计)
   - [5.1 BDF → 几何 HDF5（bdf_pack.py）](#51-bdf--几何-hdf5bdf_packpy)
   - [5.2 OP2 → 结果 HDF5（op2_pack.py）](#52-op2--结果-hdf5op2_packpy)
   - [5.3 F06 → manifest 补充（f06_pack.py）](#53-f06--manifest-补充f06_packpy)
6. [HDF5 Schema 对齐细节](#6-hdf5-schema-对齐细节)
7. [manifest.db 变更](#7-manifestdb-变更)
8. [各层改动范围汇总](#8-各层改动范围汇总)
9. [实施分阶段计划](#9-实施分阶段计划)
10. [主要技术风险](#10-主要技术风险)

---

## 1. 背景与目标

当前平台已支持 Abaqus 格式的完整可视化链路：

```
ODB 文件 → L1（abaqus_dump.py）→ L2（ingest.py）→ L3（FastAPI）→ Three.js 前端
```

目标是在 **L2 渲染核心（ingest.py）和 L3 查询/渲染接口不改动**的前提下，将链路扩展到 Nastran 格式：

```
BDF + OP2 → L1（bdf_pack.py + op2_pack.py）→ L2（无改动）→ L3（无改动）→ Three.js 前端
```

> **注意**：L2/L3 渲染核心不改，但以下部分需要扩展：  
> - `src/l3/api/routes/projects.py`：新增 `source_type="bdf"` 识别  
> - `src/job_runner.py`：新增 BDF 项目流程分支、OP2 结果组分支  
> 详见 §12 和 §11.9–11.10。

扩展范围：
- **BDF**（模型输入文件）：节点、单元、材料、截面、集合 → 几何可视化
- **OP2**（二进制结果文件）：位移、应力、模态振型等 → 结果云图
- **F06**（文本结果摘要）：模态频率列表等 → 辅助元数据（可选）

---

## 2. Nastran 文件格式说明

### BDF（Bulk Data File）

Nastran 的模型定义文件，类似 Abaqus 的 INP 文件，包含：
- `GRID`：节点坐标（可引用局部坐标系 CORD1R/CORD2R）
- `CQUAD4`、`CTRIA3`、`CHEXA`、`CPENTA`、`CTETRA` 等：单元连接
- `PSHELL`、`PSOLID`、`PBEAM`：属性卡（截面定义，引用材料）
- `MAT1`、`MAT8`：各向同性/正交各向异性材料
- `SET1`、`SET3`：集合定义

### OP2（Output 2 Binary）

Nastran 的二进制结果文件，包含：
- `DISPLACEMENT`：节点位移（6 个自由度：UX/UY/UZ/RX/RY/RZ）
- `OES`（Element Stress）：单元应力
- `OEF`（Element Force）：单元内力
- `EIGENVECTORS`：模态振型位移
- `EIGENVALUES`：特征值（模态分析时包含频率）
- 每类结果按 Subcase 组织（对应 Abaqus 的 Step）

### F06（Formatted Output 6）

人类可读的文本报告，包含：
- 模态频率列表、质量参与系数
- 静力分析收敛信息
- 载荷汇总等

---

## 3. 概念对照：Nastran vs Abaqus ODB

| 概念维度 | Abaqus ODB | Nastran | 处理方式 |
|---------|-----------|---------|---------|
| **模型层次** | Assembly → Instance → Part | 无层次，节点/单元全局编号 | 将整个 BDF 视为"一个 Instance" |
| **节点编号作用域** | Instance 级（不同 Instance 可重复） | 全局唯一 | 直接使用，无需 Instance 前缀 |
| **单元类型命名** | S4R、C3D8R、S3... | CQUAD4、CHEXA、CTRIA3... | 建立名称映射表 |
| **分析步** | Step（STATIC/FREQUENCY/DYNAMIC/BUCKLE） | Subcase（ID 为整数） | Subcase ID → step_name，类型通过 SOL 号推断 |
| **结果位置** | NODAL / INTEGRATION_POINT / ELEMENT_NODAL | Grid Point（节点）/ Element（单元中心或角点） | DISPLACEMENT → NODAL；OES → INTEGRATION_POINT 或 ELEMENT_NODAL |
| **坐标系** | 局部坐标 + 装配变换矩阵 | 直接全局坐标（或 CORD 局部系） | L1 阶段展平为全局坐标，assembly.h5 写单位矩阵 |
| **截面/属性** | \*SHELL SECTION + material_name | PSHELL(pid) → MAT1(mid) | pid → Section，mid → Material |
| **集合** | Nset / Elset（Part 级 + Assembly 级） | SET1（节点集）/ SET3（单元集） | 统一写入 instance_sets |
| **模态帧语义** | frame_value = 频率（Hz）| 特征值 → 转换为 Hz | 同 ODB，procedure='FREQUENCY' |

---

## 4. 架构适配策略

### 核心原则

L1 层新增两条转储路径，L2/L3 完全不动：

```
┌─────────────────────────────────────────────┐
│  L1 转储层（当前已有 + 新增）                  │
│                                             │
│  abaqus_dump.py + l1_pack.py  ← ODB 路径    │
│  bdf_pack.py                  ← BDF 路径（新）│
│  op2_pack.py                  ← OP2 路径（新）│
│  f06_pack.py                  ← F06 路径（新，可选）│
│                                             │
│  输出：相同的 HDF5 schema + manifest.db       │
└─────────────────────────┬───────────────────┘
                          │ 相同 schema，L2/L3 无感知
                          ▼
┌──────────────────────────────────────────────┐
│  L2 预处理（ingest.py）— 无改动               │
│  L3 服务（FastAPI）— 无改动                   │
│  前端（Three.js）— 无改动（可选小改：显示来源标签）│
└──────────────────────────────────────────────┘
```

### Instance 虚拟化策略

Nastran 没有 Assembly/Instance 概念，但 L1 HDF5 schema 要求每个几何文件对应一个 Instance。处理方式：

- **默认策略**：将整个模型视为单一 Instance，名称取文件名（如 `model.bdf` → Instance 名 `MODEL`）
- **按属性分组策略（可选）**：按 PID（Property ID）将单元分成若干虚拟 Instance，便于按截面类型分色。此策略实现复杂，一期不做。

---

## 5. L1 层详细设计

### 5.1 BDF → 几何 HDF5（bdf_pack.py）

**文件位置**：`src/l1/bdf_pack.py`

**依赖**：pyNastran（已在项目依赖中）

**入口**：
```bash
python src/l1/bdf_pack.py --bdf /path/to/model.bdf --workspace /data/<job_id>/
```

**处理流程**：

```
读取 BDF
  ↓ pyNastran BDF()
  ↓
1. 解析坐标系卡（CORD1R/CORD2R）
   → 建立局部坐标系 → 全局坐标字典

2. 读取节点（GRID）
   → 所有节点转换为全局坐标
   → 升序排列 → node_labels [N] + node_coords [N,3]

3. 读取单元（按类型分组）
   CQUAD4 → S4R 等价，4 角节点
   CTRIA3 → S3，3 角节点
   CHEXA  → C3D8R，8 角节点
   CPENTA → C3D6，6 角节点
   CTETRA → C3D4，4 角节点
   CBAR/CBEAM → 梁单元（暂跳过或标记）
   → 每类型写入 /elements/<mapped_etype>/

4. 读取属性 + 材料
   PSHELL(pid) → Section(type=SHELL, thickness=t, elset=P<pid>_ELEMS)
   PSOLID(pid) → Section(type=SOLID, elset=P<pid>_ELEMS)
   MAT1(mid)   → Material(type=ISOTROPIC, E=e, nu=nu)
   MAT8(mid)   → Material(type=ORTHOTROPIC, ...)

5. 读取集合
   SET1(sid) → node_set（按 ID 命名 SET1_<sid>）
   SET3(sid) → element_set

6. 构建 assembly.h5
   → 单个 Instance，transform = 单位矩阵（4×4）

7. 写 manifest.db
   → instances 表（instance_name, part_name, geom_path, ...）
   → element_type_dist 表
   → meta 表（node_count, instance_count 汇总）
   注：result_group_meta 不在此步写入，由 op2_pack.py 在写结果后写入
```

**输出文件**：
```
l1/
  assembly.h5
  geometry/
    <MODEL_NAME>.h5       ← 节点 + 单元 + 截面 + 材料 + 集合
  sets/sets.h5            ← SET1/SET3 → node_sets/element_sets
manifest.db               ← instances + element_type_dist + result_group_meta
```

---

### 5.2 OP2 → 结果 HDF5（op2_pack.py）

**文件位置**：`src/l1/op2_pack.py`

**依赖**：pyNastran OP2 读取器

**入口**：
```bash
python src/l1/op2_pack.py --op2 /path/to/result.op2 --workspace /data/<job_id>/
```

**前置条件**：bdf_pack.py 已运行完成（manifest.db 中已有 instances 表）

**Subcase → Step 类型推断**：

| SOL 号 | Nastran 分析类型 | manifest.db procedure |
|--------|---------------|----------------------|
| SOL 101 | 线性静力 | STATIC |
| SOL 103 | 实特征值（模态） | FREQUENCY |
| SOL 111 | 频率响应 | DYNAMIC |
| SOL 112 | 瞬态响应 | DYNAMIC |

**处理流程**：

```
读取 OP2
  ↓ pyNastran OP2(debug=False); op2.read_op2(file_path)
  ↓
1. 读取特征值表（SOL 103）
   op2.eigenvalues → 频率列表
   → 写入 frames 表（frame_value = 频率 Hz）

2. 读取位移结果（DISPLACEMENT）
   op2.displacements[subcase_id].data → [num_modes/frames, N, 6]
   节点 ID 列表 → op2.displacements[subcase_id].node_gridtype[:, 0]
   → 取前 3 列（UX/UY/UZ）对齐 ODB U 场格式
   → 写入 results/<step>__U.h5
     /NODAL/<instance>/labels [N] int32
     /NODAL/<instance>/data [num_frames, N, 3] float32

3. 读取单元应力（OES）
   op2.cquad4_stress[subcase_id] / op2.chexa_stress[subcase_id] 等
   → 按单元类型分组
   → 转换为 [num_frames, M, n_ip, ncomp] 格式
   → 写入 results/<step>__S.h5
     /INTEGRATION_POINT/<instance>/<mapped_etype>/

4. 读取 SPC 力（OQMG，可选）
   → 写入 results/<step>__RF.h5（支撑反力）

5. 更新 manifest.db
   → steps 表（step_name='SUBCASE_<id>', procedure=推断值）
   → frames 表（每个 subcase 的帧列表）
   → result_files 表（step_name, field_name, file_path, positions）
   → result_blocks 表（精确路由到 HDF5 内部路径）
   → 状态更新：ready
```

**输出文件**：
```
l1/results/
  SUBCASE_1__U.h5     ← 位移场
  SUBCASE_1__S.h5     ← 应力场（如有）
manifest.db           ← steps + frames + result_files + result_blocks
```

---

### 5.3 F06 → manifest 补充（f06_pack.py）

**文件位置**：`src/l1/f06_pack.py`

**优先级**：低（OP2 已包含主要结果，F06 提供补充信息）

**用途**：
- 补充质量参与系数（modal participation factor）到 frames 表
- 补充模态频率精度（F06 的频率精度高于 OP2 读取值）
- 解析静力收敛状态（非收敛分析的警告标记）

**实现方式**：正则表达式解析文本，无需外部库。

---

## 6. HDF5 Schema 对齐细节

以下逐一说明 Nastran 数据如何对齐到现有 L1 HDF5 schema，确保 L2/L3 无需修改。

### geometry/<instance>.h5

| 字段 | ODB 来源 | Nastran 来源 | 差异处理 |
|------|---------|-------------|---------|
| `/nodes/labels` | ODB node_label（Instance 级） | GRID ID（全局唯一） | 无差异，直接存 |
| `/nodes/coords` | 局部坐标（未应用 transform） | CORD 局部系转换后的全局坐标 | **Nastran 在 L1 就展平为全局坐标**，assembly.h5 写单位矩阵，L2 应用 transform 后结果相同 |
| `/elements/<etype>/labels` | Abaqus elem_label | Nastran EID | 无差异 |
| `/elements/<etype>/conn` | node_label 索引（行号） | GRID ID 索引（行号） | 无差异，同样存行号 |
| `/sections/<name>/` | \*SHELL SECTION 等 | PSHELL + MAT 引用 | 字段语义相同，PID 作为 section name |
| `/materials/<name>/` | \*MATERIAL | MAT1/MAT8 | 字段对应：E→E，NU→nu |
| `/instance_sets/node_sets/` | Nset | SET1 | 无差异 |
| `/instance_sets/element_sets/` | Elset | SET3 或按 PID 构建 | 无差异 |

### assembly.h5

| 字段 | ODB 来源 | Nastran 处理 |
|------|---------|------------|
| `/instances/<name>/transform` | 实例变换矩阵 | 写 4×4 单位矩阵（坐标已在 L1 展平） |
| `/instances/<name>/part_name` | Part 名 | 取 BDF 文件名（无 Part 概念） |

### results/<step>__<field>.h5

| 字段 | ODB 来源 | Nastran 来源 | 差异处理 |
|------|---------|-------------|---------|
| `/meta/step_name` | ODB Step 名 | `SUBCASE_<id>` | 格式统一 |
| `/meta/field_name` | U、S、E 等 | DISPLACEMENT→U，OES→S | 映射到 ODB 惯用名 |
| `/meta/components` | ['U1','U2','U3'] | ['UX','UY','UZ'] | **需统一**：Nastran 分量名映射到 ODB 分量名 |
| `/NODAL/<inst>/data` | [frames, N, ncomp] | [frames, N, 3]（取 UX/UY/UZ） | 形状一致，旋转自由度 RX/RY/RZ 写入单独字段或忽略 |
| `/INTEGRATION_POINT/<inst>/<etype>/data` | [frames, M, n_ip, ncomp] | [frames, M, n_ip, ncomp] | Nastran 单元应力插值点数与单元类型相关，需按类型处理 |

**分量名统一策略**（写入 `/meta/components`）：

| Nastran 分量 | 写入值（对齐 ODB） |
|------------|----------------|
| UX, UY, UZ | U1, U2, U3 |
| RX, RY, RZ | UR1, UR2, UR3（额外字段，ODB 无此概念但 schema 兼容） |
| OXX, OYY, OZZ, TXY, TXZ, TYZ（应力） | S11, S22, S33, S12, S13, S23 |

---

## 7. manifest.db 变更

### 无需修改的表

`instances`、`element_type_dist`、`steps`、`frames`、`result_blocks`、`node_sets`、`element_sets`、`user_sets` ——这些表的 schema 与数据来源无关，Nastran 数据可直接填入。

### 轻微扩展（已有字段，语义复用）

| 表 | 字段 | ODB 值 | Nastran 值 |
|---|------|--------|-----------|
| `result_files` | `source` | `'odb'` | `'nastran'` |
| `result_group_meta` | `source_file` | ODB 文件路径 | OP2 文件路径（几何 BDF 不写此表） |
| `steps` | `step_name` | ODB Step 名 | `'SUBCASE_<id>'` |
| `steps` | `procedure` | STATIC/FREQUENCY/... | 同，通过 SOL 号推断 |

> **重要**：`result_group_meta` 表无 `source` 字段（见 `src/l1/manifest_schema.py`）。  
> 来源区分仅靠 `result_files.source`（值为 `'odb'` 或 `'nastran'`）。  
> `result_group_meta` 只记录 `result_group / display_name / source_file / consistency_check / created_at`。

### 无需新增字段

现有 schema 已能区分来源（`result_files.source`），无需加新列。

---

## 8. 各层改动范围汇总

### 需要新增或修改的文件

| 文件 | 操作 | 工作量 | 说明 |
|------|------|--------|------|
| `src/l1/bdf_pack.py` | **新增** | 大 | BDF → geometry HDF5 + manifest.db |
| `src/l1/op2_pack.py` | **新增** | 大 | OP2 → results HDF5 + manifest.db |
| `src/l1/f06_pack.py` | **新增** | 小 | F06 → manifest 补充元数据（可延后）|
| `MeshElementFactory.py` | **无需修改**（一期） | — | NASTRAN 低阶分支已覆盖 CQUAD4/CTRIA3/CHEXA/CPENTA/CTETRA；高阶单元留二期 |
| `src/job_runner.py` | **修改** | 大 | 新增 `_run_l1_bdf()`、`_run_bdf_project()`、`_run_op2_result_group()` 三条分支 |
| `src/l3/api/routes/projects.py` | **修改** | 小 | `_detect_source_type()` 加 `bdf` 识别；`jobs.py` 无需改动 |
| `docs/l3/L3-API-Quick-Reference.md` | **修改** | 极小 | 更新接口说明，注明支持 Nastran 格式 |

### 完全不需要改动的文件

| 文件/层 | 原因 |
|--------|------|
| `src/l2/ingest.py` | 只读 L1 HDF5，格式无关 |
| `src/l3/` 所有 service/repo 文件 | 读 L2 HDF5 + manifest.db，格式无关 |
| `viewer/`（Three.js 前端） | 数据格式相同，无需改动；可选：添加"Nastran"来源标签显示 |
| `src/l1/manifest_schema.py` | Schema 无变化 |
| `src/l1/odb_model.py` | ODB 专用，不涉及 |
| `src/l1/l1_pack.py` / `abaqus_dump.py` | ODB 专用，不涉及 |

---

## 9. 实施分阶段计划

### 第一阶段：BDF 几何可视化（约 1–2 周）

目标：提交 BDF 文件 → 看到 3D 网格，支持按集合高亮，不含结果云图。

步骤：
1. 实现 `bdf_pack.py`（节点、单元、截面、材料、集合，仅低阶单元）
2. 修改 `projects.py`：`_detect_source_type()` 加 `bdf` 识别
3. 修改 `job_runner.py`：加 `_run_l1_bdf()` + `_run_bdf_project()` 分支
4. 手动测试：提交一个简单 BDF，验证 L2 → L3 → 前端链路

验收标准：
- `manifest.db` instances 表有记录
- Three.js 前端能渲染出 BDF 网格
- 属性着色模式（按截面/材料着色）正常

### 第二阶段：OP2 结果云图（约 2–3 周）

目标：提交 OP2 作为结果组 → 支持位移云图、应力云图、模态动画。

步骤：
1. 实现 `op2_pack.py`（位移 DISPLACEMENT + 应力 OES）
2. 修改 `job_runner.py`：`_run_result_group()` 加 `.op2` 分支，新增 `_run_op2_result_group()`
3. 验证 manifest.db steps/frames/result_blocks 正确填写
4. 手动测试：静力分析位移云图、模态动画

验收标准：
- L3 `GET /api/odb/{project_id}/results/frame-scalars` 返回正确的位移/应力数据
- L3 `GET /api/odb/{project_id}/results/frame-colors` 返回正确的颜色映射数据
- Three.js 前端云图颜色映射正常
- SOL 103 模态动画可正常播放

### 第三阶段：F06 补充 + 工程化（约 1 周）

目标：完善元数据，提升稳定性。

步骤：
1. 实现 `f06_pack.py`，补充质量参与系数到 frames 表
2. 坐标系边界测试（含 CORD 局部坐标系的 BDF）
3. 大模型性能测试（> 1M 节点的 BDF）
4. 完善错误处理：不支持的 SOL 类型、缺少 OP2 时的降级显示

---

## 10. 主要技术风险

| 风险 | 影响 | 缓解方案 |
|------|------|---------|
| **CORD 坐标系展平不完整** | 节点坐标错误，网格变形 | 使用 pyNastran 内置的 `get_xyz_in_coord()` 方法，它已处理坐标系递归解析 |
| **OP2 应力结果的 n_ip 不固定** | 无法构成规则 `[M, n_ip, ncomp]` 数组 | 按单元类型分别处理（CQUAD4 固定 4 个 IP；CHEXA 固定 8 个 IP） |
| **旋转自由度（RX/RY/RZ）处理** | L3 位移接口只期望 3 分量 | 将平动自由度（UX/UY/UZ）作为 U 场写入；旋转自由度可写入单独字段 `UR`，暂不在前端显示 |
| **Nastran 无 Instance 层次导致集合命名冲突** | 多 BDF 模型共用 manifest 时 SET1 名称冲突 | 每个 job 独立 workspace，manifest.db 不跨 job 共享，无冲突 |
| **pyNastran 版本兼容性** | 不同版本 API 差异 | 锁定版本，在 requirements.txt 中固定 pyNastran >= 4.0 |
| **超大 BDF 内存占用** | 节点 > 5M 时 pyNastran 读取可能 OOM | 使用 pyNastran 的 `read_bdf(skip_cards=[...])` 减少非必要字段加载 |

---

## 11. 代码审阅后的具体实现规范

> 本节是对现有代码进行审阅后，补充的具体实现细节，供编码参考。  
> 审阅文件：`src/l1/l1_pack.py`、`src/job_runner.py`、`src/l3/api/routes/projects.py`、  
> `MeshElementFactory.py`、`src/l1/manifest_schema.py`。

---

### 11.1 MeshElementFactory.py — 一期无需修改

`MeshElementFactory.py` 已有 `fem_software="NASTRAN"` 分支，覆盖以下**低阶**类型：  
`CQUAD4 / CTRIA3 / CHEXA / CPENTA / CTETRA / CBAR / CBEAM / CELAS`  
均已映射到对应的 `MeshElement` 子类，无需修改。

**高阶单元（CQUAD8 / CTRIA6 / CHEXA-20 / CPENTA-15 / CTETRA-10）**：  
当前 NASTRAN 分支**未覆盖**这些类型。一期 `bdf_pack.py` 只处理低阶单元，遇到高阶卡型记录警告后跳过。  
二期若需支持高阶单元，需同时扩展 `MeshElementFactory.py` 的 NASTRAN 分支。

---

### 11.2 HDF5 元素组名必须使用 Abaqus 等价名

L2 `ingest.py` 读取 `elements/{etype}/conn` 时内部调用  
`MeshElementFactory.CreateElement(etype, fem_software="ABAQUS")`，  
因此 `bdf_pack.py` 写入 HDF5 的 group 名必须使用 Abaqus 等价名，**不能保留 Nastran 原始名**。

映射表（`bdf_pack.py` 内部常量，**一期只处理低阶单元**）：

```python
# Nastran 卡名 → (Abaqus 等价 etype, 角节点数, n_faces)
# 一期支持范围：低阶单元。CQUAD8/CTRIA6 等高阶壳遇到时跳过并打印警告。
NASTRAN_TO_ABAQUS = {
    'CQUAD4':  ('S4R',  4, 1),   # 壳：1 面（元素本身即面）
    'CTRIA3':  ('S3',   3, 1),   # 壳：1 面
    'CHEXA':   None,             # 按节点数分派（见下）
    'CPENTA':  None,             # 按节点数分派
    'CTETRA':  None,             # 按节点数分派
    'CBAR':    ('B31',  2, 0),   # 梁/杆，无面，L2 会跳过
    'CBEAM':   ('B31',  2, 0),
}

# CHEXA / CPENTA / CTETRA 按节点数再分派
# 一期只支持低阶（8/6/4 节点）；20/15/10 节点版本遇到跳过
SOLID_BY_NNODE = {
    ('CHEXA',  8): ('C3D8R', 8, 6),
    ('CPENTA', 6): ('C3D6',  6, 5),
    ('CTETRA', 4): ('C3D4',  4, 4),
}
```

---

### 11.3 `conn` 数组格式：行号索引，非节点 GID

`l1_pack.py` 的 `pack_geometry()` 读取 `conn.npy`，里面存的是节点在  
`node_labels`（已升序排列）数组里的**0-based 行号**，不是节点原始 ID。

`bdf_pack.py` 必须做转换：

```python
node_labels = np.array(sorted(bdf.nodes.keys()), dtype=np.int32)
# 对每个单元类型组：
conn_gids = np.array([[n for n in elem.nodes[:n_corner]] for elem in elems])
conn_rows = np.searchsorted(node_labels, conn_gids).astype(np.int32)
```

---

### 11.4 `face_*` 数组：L2 表面提取的输入

L2 通过 `face_elem_idx / face_seq / face_node_conn` 枚举所有面，去重后得到表面。  
`bdf_pack.py` 必须按单元类型计算并写入这三个数组。

各类型的面定义（角节点 conn 内的局部索引）：

```python
FACE_DEFS = {
    'S4R':   [[0, 1, 2, 3]],
    'S3':    [[0, 1, 2]],
    'S8R':   [[0, 1, 2, 3]],          # 高阶壳，同 S4R 面定义（用角节点）
    'S6':    [[0, 1, 2]],
    'C3D8R': [                         # 6 个四边形面
        [0,1,2,3], [4,5,6,7],
        [0,1,5,4], [1,2,6,5],
        [2,3,7,6], [3,0,4,7],
    ],
    'C3D20R': [                        # 同 C3D8R，角节点面定义
        [0,1,2,3], [4,5,6,7],
        [0,1,5,4], [1,2,6,5],
        [2,3,7,6], [3,0,4,7],
    ],
    'C3D4':  [                         # 4 个三角形面
        [0,1,2], [0,1,3],
        [0,2,3], [1,2,3],
    ],
    'C3D10': [                         # 同 C3D4
        [0,1,2], [0,1,3],
        [0,2,3], [1,2,3],
    ],
    'C3D6':  [                         # 2 三角形 + 3 四边形
        [0,1,2], [3,4,5],
        [0,1,4,3], [1,2,5,4], [2,0,3,5],
    ],
    'C3D15': [                         # 同 C3D6
        [0,1,2], [3,4,5],
        [0,1,4,3], [1,2,5,4], [2,0,3,5],
    ],
    'B31':   [],                       # 梁/杆单元：无面，跳过
}
```

计算逻辑（对每个 etype 组）：

```python
face_elem_idx_list, face_seq_list, face_node_conn_list = [], [], []
for ei, row in enumerate(conn_rows):          # conn_rows: [M, n_corner]
    for fi, face_local in enumerate(FACE_DEFS[etype]):
        face_elem_idx_list.append(ei)
        face_seq_list.append(fi)
        face_node_conn_list.append([row[k] for k in face_local])

grp.create_dataset('face_elem_idx',  data=np.array(face_elem_idx_list,  dtype=np.int32))
grp.create_dataset('face_seq',       data=np.array(face_seq_list,       dtype=np.int32))
grp.create_dataset('face_node_conn', data=np.array(face_node_conn_list, dtype=np.int32))
```

---

### 11.5 `section_id` 用 PID 构建

Nastran 每个单元有 Property ID (PID)。处理方式：

```python
# 收集该 etype 组中出现的所有 PID，排序构建 section_names
pids_in_group = sorted(set(elem.pid for elem in elems))
pid_to_sid = {pid: i for i, pid in enumerate(pids_in_group)}

section_id = np.array([pid_to_sid[elem.pid] for elem in elems], dtype=np.int32)
section_names = [str(pid) for pid in pids_in_group]   # 写入 /section_names
```

跨 etype 全局 section_names（`_refine_shell_sections` 需要全局一致的 section_id）：

```python
# 建议：先收集全模型所有出现的 PID，统一编号，所有 etype 共用同一个 pid_to_sid
all_pids = sorted(set(elem.pid for elem in all_elements))
pid_to_sid = {pid: i for i, pid in enumerate(all_pids)}
section_names = [str(p) for p in all_pids]
```

Sections 组（对应每个 PID）：

```python
for pid in all_pids:
    prop = bdf.properties[pid]
    sg = f.require_group(f'sections/{pid}')
    if prop.type == 'PSHELL':
        sg.attrs['type']          = 'SHELL'
        sg.attrs['thickness']     = float(prop.t or float('nan'))
        sg.attrs['material_name'] = str(prop.mid)
        sg.attrs['element_set']   = f'P{pid}_ELEMS'
    elif prop.type == 'PSOLID':
        sg.attrs['type']          = 'SOLID'
        sg.attrs['thickness']     = float('nan')
        sg.attrs['material_name'] = str(prop.mid)
        sg.attrs['element_set']   = f'P{pid}_ELEMS'
```

Materials 组（MAT1 / MAT8）：

```python
for mid, mat in bdf.materials.items():
    mg = f.require_group(f'materials/{mid}')
    if mat.type == 'MAT1':
        mg.attrs['type'] = 'ISOTROPIC'
        mg.create_dataset('elastic_table',
                          data=np.array([[mat.e, mat.nu]], dtype=np.float64))
    elif mat.type == 'MAT8':
        mg.attrs['type'] = 'ORTHOTROPIC'
```

---

### 11.6 坐标展平

使用 pyNastran 内置方法，自动处理 CORD1R/CORD2R 递归：

```python
from pyNastran.bdf.bdf import BDF
bdf = BDF(debug=False)
bdf.read_bdf(bdf_path)

# get_xyz_in_coord(0) 将所有节点坐标转换到基本坐标系（global）
# 返回 dict: {nid: [x,y,z]}
xyz_dict = bdf.get_xyz_in_coord(cid=0)
node_labels = np.array(sorted(xyz_dict.keys()), dtype=np.int32)
node_coords = np.array([xyz_dict[n] for n in node_labels], dtype=np.float64)
```

`assembly.h5` 中该 Instance 的 transform 写 4×4 单位矩阵（坐标已展平，无需装配变换）。

---

### 11.7 集合写入

```
SET1(sid) → instance_sets/node_sets/SET1_{sid}    (存节点 GID 数组)
SET3(sid) → instance_sets/element_sets/SET3_{sid} (存单元 GID 数组)
```

注意：`instance_sets/node_sets/{name}` 里存的是**节点原始 GID**（非行号），  
与 `l1_pack.py` 的 `pack_geometry()` 一致（L3 查询时会用 searchsorted 转换）。

manifest.db 写法（对齐 `pack_geometry()` 里的格式）：
```python
db_conn.execute(
    "INSERT OR REPLACE INTO node_sets VALUES (?,?,?,?,?)",
    (set_name, inst_name, inst_name,
     h5_rel + ':instance_sets/node_sets/' + set_name_safe, node_count)
)
```

---

### 11.8 op2_pack.py — 直接写 HDF5，无中间 npy

ODB 链路因 Abaqus Python 2.7 限制必须先 dump npy 再 pack HDF5，  
OP2 在 Python 3 下直接读写，一步到位。

```
op2_pack.py 流程：
  1. 读取 manifest.db → 取 inst_name（bdf_pack 已写入）
  2. op2.read_op2(op2_path)
  3. 对每个 subcase：
     a. 推断 procedure（SOL 号 → STATIC/FREQUENCY/DYNAMIC）
     b. 写 steps / frames 到 manifest.db
     c. 读 op2.displacements[sc].data → [n_frames, N, 6]，取前 3 列（UX/UY/UZ）
     d. 对齐节点顺序（op2 node id → searchsorted → 行号 → 写到 H5 正确行）
     e. 写 results/SUBCASE_{sc}__U.h5（NODAL 位移）
     f. 写 result_files / result_blocks 到 manifest.db
  4. 写 result_group_meta（result_group='default_result'）
  5. 更新 manifest.db 里 result_group=NULL 的行（同 _adopt_odb_result_group）
```

结果 HDF5 内部结构（对齐 `pack_results()` 的输出）：

```
SUBCASE_1__U.h5
  meta/
    step_name      = 'SUBCASE_1'
    field_name     = 'U'
    components     = ['U1', 'U2', 'U3']     # 统一用 Abaqus 分量名
    invariants     = []
  frame_index/
    frame_values   [n_frames] float64
    descriptions   [n_frames] str
  NODAL/
    {inst_name}/
      labels       [N] int32   — 节点 GID（排序后）
      data         [n_frames, N, 3] float32
```

---

### 11.9 job_runner.py 需要修改的位置

**（A）新增脚本路径常量**（第 40–43 行附近）：

```python
BDF_PACK_SCRIPT = REPO_ROOT / "src" / "l1" / "bdf_pack.py"
OP2_PACK_SCRIPT = REPO_ROOT / "src" / "l1" / "op2_pack.py"
```

**（B）新增 `_run_l1_bdf()` 函数**（放在 `_run_l1_inp()` 之后）：

```python
def _run_l1_bdf(odb_id: str, bdf_path: str, workspace: str) -> bool:
    logger.info("[%s] L1 (BDF): %s", odb_id, bdf_path)
    _log_job(odb_id, "step", f"L1（BDF）：解析 {os.path.basename(bdf_path)}", stage="l1_bdf")
    rc, tail = _run_streaming(
        [sys.executable, str(BDF_PACK_SCRIPT), "--bdf", bdf_path, "--workspace", workspace],
        odb_id, "l1_bdf",
    )
    if rc != 0:
        _update_status(odb_id, "error", error_msg=f"bdf_pack failed: {tail}")
        _log_job(odb_id, "error", f"BDF 解析失败（rc={rc}）：{tail[-500:]}", stage="l1_bdf")
        return False
    _log_job(odb_id, "step", "L1（BDF）解析完成", stage="l1_bdf")
    return True
```

**（C）修改 `_run_l1()` dispatch**（第 470–480 行附近）：

```python
def _run_l1(odb_id, source_path, workspace):
    if source_path.lower().endswith(".inp"):
        ok = _run_l1_inp(odb_id, source_path, workspace)
    elif source_path.lower().endswith(".bdf"):          # ← 新增
        ok = _run_l1_bdf(odb_id, source_path, workspace)
    else:
        ok = _run_l1_odb(odb_id, source_path, workspace)
    ...
```

**（D）新增 `_run_bdf_project()` 函数**（参照 `_run_geom_project()` 结构）：

```python
def _run_bdf_project(project_id: str, bdf_path: str, workspace: str) -> bool:
    # 1. 检查文件存在
    # 2. 调用 bdf_pack.py（subprocess）
    # 3. 调用 ingest.py（L2）
    # 4. 更新 geom_status → ready
```

**（E）修改 `_run_project()` dispatch**：

```python
def _run_project(project_id, source_path, source_type, workspace):
    if source_type == "odb":
        return _run_odb_project(...)
    if source_type == "bdf":                            # ← 新增
        return _run_bdf_project(...)
    return _run_geom_project(...)   # INP
```

**（F）修改 `_run_result_group()` 支持 OP2**（第 930 行附近）：

```python
def _run_result_group(project_id, result_group, source_path, parse_options_json, workspace):
    ...
    if source_path.lower().endswith(".op2"):            # ← 新增分支
        return _run_op2_result_group(
            project_id, result_group, source_path, workspace
        )
    # 原有 ODB 逻辑不变
    ...
```

新增 `_run_op2_result_group()`：

```python
def _run_op2_result_group(project_id, result_group, op2_path, workspace):
    _log_job(project_id, "step", f"[{result_group}] OP2 结果打包启动", stage="rg_op2")
    rc, tail = _run_streaming(
        [sys.executable, str(OP2_PACK_SCRIPT),
         "--op2", op2_path,
         "--workspace", workspace,
         "--result-group", result_group],
        project_id, "rg_op2",
    )
    if rc != 0:
        _update_result_group_status(project_id, result_group, "error", tail)
        return False
    _update_result_group_status(project_id, result_group, "ready")
    _log_job(project_id, "step", f"[{result_group}] OP2 结果打包完成", stage="rg_op2")
    return True
```

---

### 11.10 projects.py 需要修改的位置

**`_detect_source_type()` 函数**（第 138–171 行）：

```python
def _detect_source_type(source_path, explicit=None):
    allowed = {"inp", "odb", "bdf"}          # ← 加 "bdf"
    ...
    if suffix == ".inp":
        inferred = "inp"
    elif suffix == ".odb":
        inferred = "odb"
    elif suffix == ".bdf":                   # ← 加此分支
        inferred = "bdf"
    ...
```

**`POST /api/projects/{id}/results` 端点**：该端点目前只接受 ODB 文件作为结果组来源，  
OP2 结果组通过相同端点提交（source_path 指向 .op2 文件），job_runner 按后缀路由，  
**API 层不需要改动**，后缀检测在 runner 端做。

---

### 11.11 bdf_pack.py 入口与文件结构

```
src/l1/bdf_pack.py

用法：
  python src/l1/bdf_pack.py --bdf /path/to/model.bdf --workspace /data/<job_id>/

输出：
  l1/
    assembly.h5                   — 单 instance，4x4 单位矩阵 transform
    geometry/<INST_NAME>.h5       — 节点 + 单元 + 截面 + 材料 + 集合 + CSR
    sets/sets.h5                  — SET1/SET3 集合（也可为空文件）
  manifest.db                     — instances + element_type_dist + node/element_sets + meta

Instance 命名规则：
  取 BDF 文件名（不含扩展名）转大写，例如 model.bdf → "MODEL"
```

```
src/l1/op2_pack.py

用法：
  python src/l1/op2_pack.py --op2 /path/to/result.op2 \
    --workspace /data/<job_id>/ \
    [--result-group default_result]

前置条件：bdf_pack.py 已运行（manifest.db 中 instances 表有记录）

输出：
  l1/results/
    SUBCASE_1__U.h5              — 位移场（NODAL）
    SUBCASE_1__S.h5              — 应力场（INTEGRATION_POINT，如有）
  manifest.db                    — steps + frames + result_files + result_blocks
                                   + result_group_meta
```

---

## 12. Nastran 在 project/result_group 模式下的接法

> 本节回答三个关键问题，防止实现时在"复用 ODB 机制 vs 新开 Nastran 分支"之间摇摆。

### 12.1 BDF 作为几何 project：source_type 用 `"bdf"`

**决策**：新增 `source_type="bdf"`，不复用 `"inp"` 或 `"odb"`，也不抽象为 `"geom"`。

**理由**：
- `"inp"` 走 `src/inp/` 解析器（INP 专用），BDF 不适用
- `"odb"` 走 `abaqus_dump.py`（需要 Abaqus license），BDF 不需要
- 独立 `"bdf"` 类型，runner 端按类型走 `_run_bdf_project()`，逻辑清晰无耦合

**调用方式**（不变）：
```http
POST /api/projects
{
  "project_id": "<uuid>",
  "source_path": "/path/to/model.bdf",
  "source_type": "bdf"           ← 显式传，或由后缀自动推断
}
```

**改动范围**：
- `projects.py`：`_detect_source_type()` 加 `"bdf"` 到 `allowed` 集合 + `.bdf` 后缀推断
- `job_runner.py`：`_run_project()` 加 `source_type == "bdf"` 分支

### 12.2 OP2 作为结果组：复用 `POST /api/projects/{id}/results`，runner 端按后缀路由

**决策**：API 层不新增端点，沿用现有 `AddResultGroupRequest`；runner 端按 `.op2` 后缀路由到 `_run_op2_result_group()`。

**理由**：
- `result_group` 机制与文件格式无关，核心语义（"给已有几何追加一批结果"）完全适用于 OP2
- API 层只负责"接收 source_path + result_group 名"，格式解析是 runner 的责任
- 避免引入 Nastran 专用端点，保持接口数量稳定

**调用方式**（不变）：
```http
POST /api/projects/{project_id}/results
{
  "source_path": "/path/to/result.op2",
  "result_group": "load_case_1",
  "display_name": "静力工况 1"
}
```

**一致性校验策略**：  
ODB result_group 流程中有"几何节点数一致性校验"（`abaqus_dump --check-mode`）。  
OP2 分支**暂不做自动一致性校验**，原因：
- OP2 的节点集合可以是 BDF 全集的子集（只输出部分节点的结果）
- 校验时只需确认 OP2 节点 ID 都在 BDF `node_labels` 里即可（用 `np.isin`）
- `op2_pack.py` 做软性校验：节点 ID 不在 BDF 中的按 NaN 填入，打印警告，不中断流程

### 12.3 BDF 几何与 OP2 结果的绑定校验

**无强制绑定，仅 workspace 级别隐式关联**：

| 问题 | 处理方式 |
|------|---------|
| OP2 提交时 BDF 已改变 | 不检测；OP2 按当前 `manifest.db` 里的 node_labels 对齐，无法对齐的节点跳过 |
| 多个 OP2 结果绑到同一 BDF | 完全允许，每个 OP2 对应一个 result_group |
| BDF 未解析就提交 OP2 | `op2_pack.py` 启动时检查 `manifest.db` 里 instances 表是否有记录，没有则报错退出 |
| OP2 节点数多于 BDF | 打印警告，多余节点忽略；不中断 |
| OP2 节点数少于 BDF | 正常，对应节点的 data 填 NaN |
