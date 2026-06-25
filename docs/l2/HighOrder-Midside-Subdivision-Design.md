# 高阶单元中节点细分渲染 — 设计方案

**更新时间**：2026-06-25
**状态**：方案 A2 第一步已实现（C3D20/C3D10/C3D15 + 二次壳），需重跑 L2 验证；面心点(A1)未做
**适用主线**：ODB 可视化（L2 预处理 + L3 取值）

> **实现进展（2026-06-25）**
> A2 第一步已落地，覆盖 C3D20(R/H)、C3D10(M/H)、C3D15、二次壳(S6/S8R/STRI65)：
> - **L2（`src/l2/ingest.py`）**：新增 `FACE_DEFS_FULL` / `SHELL_FULL_FACE_BY_NFULL`
>   /`_subdivide_boundary_cols` / `build_render_faces`；`process_instance` 读
>   `<inst>_highorder.h5` 的 `conn_full`(标签→行)，把高阶表面面细分为含中节点的
>   小三角，`source_local_node_idx` 改用全连接列号。线性单元逐值不变。
> - **L3（`src/l3/services/result_service.py`）**：实现中发现 legend 覆盖函数
>   `_compute_en_global_range` 也只取角节点（`conn[:, :n_corner]`），会把 legend
>   封顶在角节点值上。已新增 `_load_full_conn_rows`，该函数改用全连接，使中节点
>   极值进入 legend。EN 逐顶点取值 `sc[er, li]` 因 `li` 现在是全连接列号、自动命中
>   中节点列，无需改动。
> - **不改前端、不改接口、不重跑 Abaqus**；但**需要重跑 L2**(`python src/l2/ingest.py
>   --workspace /data/<odb_id>/`)生成含中节点的渲染缓冲。
> - 测试：`tests/test_l2_highorder_subdivision.py`（细分纯逻辑）、
>   `tests/test_l3_highorder_legend_range.py`（legend 纳入中节点极值）。
> - 待办：面心合成点(A1)、三角数膨胀后的内存/octree 复核、真实数据重跑后与
>   Abaqus 云图/ legend 对照。

---

## 1. 背景与问题确认

云图上不变量/应力分量的峰值、谷值与 Abaqus 对不上（例如 S13 平均后峰值
本平台 `8.032e3` < Abaqus `8.324e3`，差约 3~4%）。经逐层对照导出脚本
（`tests/dump_ours_s.py` / `tests/dump_abq_s.py`）确认：

| 位置 | 本平台 S13 min/max | Abaqus S13 min/max | 结论 |
|---|---|---|---|
| INTEGRATION_POINT（积分点原值） | -11409 / 5462.3 | -11409 / 5462.3 | **完全一致** |
| ELEMENT_NODAL（外推到节点，未平均） | -21370 / 12349 | -21370 / 12349 | **完全一致** |

即：**L1 提取出的原始数据（含高阶单元中节点的外推值）100% 正确**，
极值 `-21370 / 12349` 正是来自高阶单元的**边中节点**。

差异出在"积分点 → 屏幕"的最后一步——**建面 + 取值只用了角节点，
把中节点整个跳过了**：

- **几何**（`src/l1/abaqus_dump.py:117` `FACE_DEFS`，注释 "corner only"）：
  C3D20 的一个面只用 4 个角节点拼成四边形 → L2（`src/l2/ingest.py:314`
  `triangulate`）再拆成 2 个三角片。边中节点从不进入几何顶点。
- **取值**（`src/l3/services/result_service.py:1183`）：每个三角顶点用
  `src_local_node_idx`（角节点在**角节点连接**里的局部序号 0..n_corner-1）
  去 EN 数据里取值，而中节点在 EN 数据里是第 8..19 列，**渲染从不读这些列**。

结果：峰值落在中节点上时，本平台既不在那里建几何、也不取那里的值，
于是峰值被角节点的较低值"拉平"，平均后整体偏低。**这不是 bug，是渲染
精度取舍；但要对齐 Abaqus，需要把中节点纳入渲染。**

**好消息**：中节点的值已经完整存在 L1 HDF5 的 ELEMENT_NODAL 数据里
（这也是 EN 对照能精确相等的原因）。本方案是**纯渲染层（L2/L3）修复，
不需要重跑 Abaqus、不需要重新做 L1 提取**。

---

## 2. 现有数据与缓冲格式（改动基线）

### L1（不改，仅引用）
- `geometry/<inst>.h5`：`elements/<etype>/conn` 为**角节点**连接
  `[N_elem, n_corner]`；`face_node_conn` 为**角节点**面连接。
- `geometry/<inst>_highorder.h5`：**全连接**（含中节点）`[N_elem, n_full]`。
- `abaqus_dump.py:MIDNODE_INDICES`：已按 etype 列出中节点在全连接里的列号
  （C3D20 → 8..19，C3D10 → 4..9，C3D15 → 6..14，二次壳 S6/S8R/STRI65 等）。
- `results/<step>__<field>.h5`：`/ELEMENT_NODAL/<inst>/<etype>/data`
  形状 `[num_frames, N_elem, n_full, ncomp]`——**第三维就是全连接节点顺序，
  中节点的外推值已经在里面**。

### L2（要改）当前产物
- `positions [Nv,3]`、`indices [Nt,3]`：渲染三角缓冲（角节点顶点）。
- `vtx_node_row [Nv]`：每个顶点对应的 FEM 节点行。
- `source_local_node_idx [Nt,3] int16`：每个三角角点在**角节点连接**里的局部序号。
- 连带：octree（按三角）、feature edges（按三角边）、picking（按三角/单元行）。

### L3（要改）当前取值
- `_en_per_vertex_averaged()`：`val = sc[er, li]`，`li = source_local_node_idx`
  仅 0..n_corner-1 → 只读角节点列。

---

## 3. 方案 A —— 全量细分（推荐，几何+取值都对齐）

核心：**对高阶单元的表面面，按"细分模板"拆成包含中节点的更多小三角片；
新增顶点直接复用已有的 FEM 中节点；取值改用全连接局部索引读 EN 数据。**

### 3.1 细分模板（按面的节点构成）

> 模板以"面在全连接里的节点"为输入。L2 需要能拿到**带中节点的面连接**，
> 即在 L1 `FACE_DEFS` 之外，再提供一份 `FACE_DEFS_FULL`（含边中节点列），
> 或在 L2 用 `MIDNODE_INDICES` + 边拓扑现算。建议在 **L1 侧新增
> `face_node_conn_full`**（含中节点的面连接），L2 直接读，避免重复推导。

**二次三角面（6 节点：角 C0 C1 C2 + 边中 M01 M12 M20）** —— C3D10 / C3D15
楔形三角面 / S6 / STRI65：
标准 4-子三角细分（每条边从中点断开）：
```
(C0, M01, M20)
(M01, C1, M12)
(M20, M12, C2)
(M01, M12, M20)   ← 中心小三角
```
4 个子三角，6 个顶点全部是真实 FEM 节点（含 3 个中节点）。

**二次四边面（8 节点 serendipity：角 C0..C3 + 边中 M0..M3，无面心节点）**
—— C3D20 / C3D15 楔形四边面 / S8R：
serendipity 四边面**没有面心节点**，要正确显示中节点二次插值，需要一个
面心采样点。两种做法：
- **A1（推荐，与 Abaqus 接近）**：合成一个面心点 Pc，其**坐标与标量值**
  都用 Q8 形函数从 8 个节点插值（ζ=η=0 处）。面细分为 8 个子三角（角-边中-面心
  扇形）：`(C0,M0,Pc),(M0,C1,Pc),(C1,M1,Pc),(M1,C2,Pc),...` 共 8 片。
  面心点是**渲染专用合成顶点**（不是 FEM 节点），其值在 L3 现算。
- **A2（更省事，略糙）**：不加面心，用 8 个边界节点做扇形三角化（6 片），
  中节点进了几何与取值，但面心区域是平面插值、非二次。视觉与 Abaqus 略有差。

**线性单元（C3D8/C3D4/C3D6 及一次壳）**：无中节点，**模板退化为现有行为**
（四边面 2 片、三角面 1 片），完全不变。

### 3.2 L2 改动点

1. **建面带中节点**：`collect_faces` 读取带中节点的面连接（`face_node_conn_full`），
   表面判定（count==1）逻辑不变——判定仍用**角节点排序键**去重，中节点不参与
   去重键（避免同一面因中节点顺序不同被判成两个面）。
2. **细分替换 `triangulate` / `build_indexed_geometry`**：按 3.1 模板生成
   `positions / indices / vtx_node_row`。中节点顶点的 `vtx_node_row` = 其 FEM
   节点行；合成面心点用特殊标记（如 `vtx_node_row = -1` 且另存 `vtx_synth_*`）。
3. **`source_local_node_idx` 升级为全连接局部索引**：改为在**全连接** `conn_full`
   里查局部序号（0..n_full-1），这样 L3 能定位到中节点对应的 EN 列。合成面心点
   单独标记（见 3.3）。
4. **连带缓冲**：
   - **三角数变化**：C3D20 表面三角约从 2/面 → 8/面（×4），整体三角数对二次网格
     会显著上升。需评估内存/octree 叶子阈值（`OCTREE_LEAF_THRESHOLD=1000`）。
   - **feature edges**：fold 角与边界判定改在细分后的几何上跑，或保持在"原始面
     边界"上跑后再映射——建议**仍按原始单元面边界算特征边**（中节点在同一面内部，
     不应产生新特征边），避免曲面被细分线"画花"。
   - **picking**：`tri_elem_row` 仍指向源单元行（细分不改单元归属），拾取不受影响。
5. **需重跑 L2**：`python src/l2/ingest.py --workspace /data/<odb_id>/`。

### 3.3 L3 改动点（`result_service.py`）

1. `_en_per_vertex_averaged()` 与 `_scalar_elem_pos_by_idx()` 取值：
   `li = source_local_node_idx`（现在是全连接局部序号）→ `val = sc[er, li]`
   直接命中中节点列。EN 数据第三维 `n_full` 与之对齐，无需改 EN 读取。
2. **合成面心点**（仅方案 A1）：面心顶点没有 EN 列，需在 L3 用 Q8 形函数从该面
   8 个节点的 EN 值现算 `val_center = Σ N_i(0,0)·val_i`。需要 L2 传出"面心点 →
   其 8 个源顶点"的映射（小数组）。若选 A2 则无此项。
3. **75% 平均逻辑不变**：仍按 domain 分块、阈值 `average_threshold` 做条件平均；
   只是参与平均的节点现在包含中节点，峰值不再被漏采。
4. `compute_scalar_range` / `frame_scalars` 出参格式不变，前端零改动。

### 3.4 验证方法
- 重跑 L2 后，用 `tests/dump_ours_s.py` 已经确认 EN 数据一致；新增一个
  "细分后表面顶点值"导出，断言其 min/max 覆盖到中节点极值（接近 -21370/12349
  的表面子集），并与 Abaqus 云图 legend 对照。
- 回归：线性单元网格（纯 C3D8）渲染结果应与改动前**逐顶点不变**。

---

## 4. 方案 B —— 仅修 legend/range（备选，不改几何）

不做细分。只在 L3 计算表面 min/max 与平均时，**把表面高阶单元的中节点 EN 值
也纳入统计**（用全连接局部索引把中节点列读进来参与 range 与该节点的平均）。

- **效果**：legend 数字能对上 Abaqus；但中节点的颜色**仍显示不出来**（几何上
  没有那个顶点），曲面仍是角节点直面片。视觉峰值位置不准。
- **成本**：仅 L3 改动，**不重跑 L2**，工作量小。
- **风险**：range 与可视像素不自洽（legend 标了某个极值，但画面上找不到那个颜色），
  对"所见即所得"的用户可能更困惑。

适合只在意数值标定、暂不在意视觉精度的过渡阶段。

---

## 5. 工作量与取舍

| | 方案 A（全量细分） | 方案 B（仅 legend） |
|---|---|---|
| 几何对齐 Abaqus | ✅ 曲面/峰值位置都对 | ❌ 仍是角节点直面片 |
| legend/range 对齐 | ✅ | ✅ |
| 改动范围 | L1 加 `face_node_conn_full` + L2 重写细分 + L3 取值 | 仅 L3 |
| 是否重跑 L2 | **需要** | 不需要 |
| 三角/内存增长 | 二次网格三角数约 ×3~4 | 无 |
| 连带（octree/特征边/拾取） | 需复核 | 无 |

**推荐**：目标是"和 Abaqus 对上"，应走**方案 A1**（带面心合成点，二次插值最接近
Abaqus）。可分两步落地：先做 A2（边界节点细分，6 片）拿到中节点的几何与取值，
验证 legend/峰值方向正确；再补面心点升级到 A1，把面心二次插值补齐。

---

## 5b. 不变量(Mises 等)单元节点值的口径修复（2026-06-25）

中节点细分上线后，应力**分量**(S11…S23)的云图能和 Abaqus 对上，但 **Mises 对不上**。
用 `tests/dump_abq_mises_compare.py` 把"单元节点 Mises"的两种口径并排导出，定位到根因：

| 口径 | 算法 | HEAD-1(C3D10M) min/max | 负值 |
|---|---|---|---|
| **A** 旧实现 | `getScalarField(MISES).getSubset(EN)`：先在积分点算 Mises 标量，再**外推标量** | -19021 / 64076 | 967 个 |
| **B** 正确 | 先把**张量**外推到节点(`getSubset(EN)`)，再算 Mises（= Abaqus `block.mises`） | 186.7 / 66983 | 0 |

Mises 是**非线性**不变量，`先算标量再外推` ≠ `先外推张量再算`。A 会过冲出**负 Mises**
（物理上不可能），且峰值偏低；Abaqus 云图用的是 B。验证中 `block.mises` 与"我们用
Voigt 公式从 EN 张量自算"逐值相等，说明 `_compute_invariants_numpy` 的公式与 Abaqus 一致。

**修复（`src/l1/abaqus_dump.py` `_extract_ip_invariants`）**：
- **EN / NODAL** 位置的不变量改为**从父张量的 EN/NODAL 子集**用 `_compute_invariants_numpy`
  现算（方法 B）；每帧只调一次 `field_out.getSubset(ELEMENT_NODAL/NODAL)`，再对各不变量
  套 numpy 公式。
- **IP** 位置不动，仍用 `getScalarField`（积分点无外推，本就正确）。
- 覆盖全部非线性不变量（Mises/Tresca/INV3/主应力/面内主应力）；PRESS 等线性量 A=B 不受影响。
- 需**重跑 L1**(`abaqus python src/l1/abaqus_dump.py ... --invariants full` + `l1_pack`)再重跑 L2。
- 对照脚本：`tests/dump_abq_mises_compare.py`（A vs block.mises vs numpy 三方对照）。

## 5c. 面内/面外主应力 + Abs 变体（含壳模型，2026-06-25）

含 shell/membrane 的模型，Abaqus 的应力/应变分量下拉除 Max/Mid/Min Principal 外，还提供
`Max. In-Plane Principal`、`Min. In-Plane Principal`、`Out-of-Plane Principal`、
`Max. Principal (Abs)`、`Max. In-Plane Principal (Abs)`；实体单元没有面内/面外之分，选这些
时 Abaqus 把实体显示为**灰色**。

用 `tests/dump_abq_inplane_probe.py` 在含壳模型(door.odb)上验证后确定的实现前提：

- **命名**：Abaqus `validInvariants` 实际吐 `MAX_INPLANE_PRINCIPAL`（无下划线），而旧映射表键写成
  `MAX_IN_PLANE_PRINCIPAL`（有下划线）→ `inv in INV_ATTR_MAP` 永远不匹配 → **面内不变量此前根本没生成**。
  已把 `_INV_CONSTANTS / INV_ATTR_MAP / _NUMPY_INV_NAME` 的键统一为 Abaqus 形式。
- **壳是 4 分量含 S33**（`['S11','S22','S33','S12']`）→ `_compute_invariants_numpy` 的
  `[11,22,33,12]` 索引假设成立，壳不用特判。
- **公式正确性**：积分点上 numpy 口径 vs `getScalarField` 逐点 diff ~1e-7，主应力/面内/面外全过。
- **`MAX_PRINCIPAL_ABS` 是 Abaqus 真常量**（`getScalarField` 认），但**不在 validInvariants 里**；
  且其值**恒 ≥ 0（无符号幅值 `max(|主应力|)`，不保留拉/压符号）**——已逐点验证（`abs(absmax)` diff ~0）。
- **`MAX_INPLANE_PRINCIPAL_ABS` 无 Abaqus 常量** → 三个位置都由 numpy 从张量算。

**实现（`src/l1/abaqus_dump.py`）**：
- `_compute_invariants_numpy` 加 `MAX_PRINCIPAL_ABS`（3D 主应力幅值）、`MAX_INPLANE_PRINCIPAL_ABS`
  （面内两主应力幅值），均**无符号**。
- `active_invs`：面内/面外由 validInvariants 驱动自动带；两个 Abs **手动补**
  （`MAX_PRINCIPAL_ABS` 任何张量场都加；`MAX_INPLANE_PRINCIPAL_ABS` 仅当该场含面内项=模型有壳）。
- **实体置灰**：`_SHELL_ONLY_INVS`(面内/面外/面内-abs) 的 EN/NODAL 写入按 `shell_keys` 门控，
  非壳块保持 NaN（→前端自动灰）。`shell_keys` 用 `getScalarField(MAX_INPLANE_PRINCIPAL)` 命中的
  `(instance,etype)` 判定，与 Abaqus 有效范围逐块一致。IP 位置：getScalarField 口径的面内不变量
  对实体本就无数据(自动 NaN)；`MAX_INPLANE_PRINCIPAL_ABS`(numpy-only) 的 IP 从 `tensor_ip` 算并同样门控。
- `Max/Mid/Min Principal` 与 `MAX_PRINCIPAL_ABS` **不置灰**（实体也有效）。
- 前端/L3 不改：合成字段(`S_MAX_INPLANE_PRINCIPAL`、`S_MAX_PRINCIPAL_ABS` 等)自动进字段目录，
  NaN→灰 已有。需重跑 L1(`--invariants full`)+ l1_pack。
- 验证脚本：`tests/dump_abq_inplane_probe.py`(Abaqus 端前提) + `tests/dump_ours_inplane_check.py`
  (我方 H5：逐块 finite/NaN，确认实体全 NaN、壳有值)。

## 6. 待确认问题

1. 目标模型主要高阶单元类型？（C3D20R / C3D10 / C3D15 / 二次壳）——决定优先实现
   哪几个细分模板。
2. 三角数 ×3~4 后，最大模型的渲染缓冲是否仍在前端可接受范围？（关系到要不要
   对细分做 LOD 或仅对表面可见面细分——本方案默认**只细分表面面**，已是最小集。）
3. 面心合成点（A1）是否一步到位，还是先 A2 过渡？
