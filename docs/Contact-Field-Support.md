# 接触输出场（CPRESS / CSHEAR / COPEN / CSLIP）支持方案

> 更新时间：2026-07-17
> 关联需求：接触分析类 ODB 的云图显示（"只有一面节点有数据"的场）

## 1. 背景：这类场是什么

接触分析（一个零件压在/滑过另一个零件上）会产出一组**接触面专属的输出变量**：

| 字段 | 含义 | 类型 |
|---|---|---|
| CPRESS | 接触压力（法向压强） | 标量 |
| CSHEAR1 / CSHEAR2 | 摩擦剪应力的两个切向分量 | 各自独立标量 |
| COPEN | 接触开度（两面间的缝隙） | 标量 |
| CSLIP1 / CSLIP2 | 接触面相对滑移量 | 各自独立标量 |

它们的两个特殊之处：

1. **只在接触面那一圈节点上有值**。模型其余节点不是"值为 0"，而是**根本没有数据**。
   Abaqus CAE 把无数据区域画成灰白色，只有接触面显示云图。
2. **ODB 里的字段名自带接触对后缀**。fieldOutputs 的 key 不是 `CPRESS`，而是类似
   `CPRESS   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1`（从面/主面名拼在字段名后面，
   用空格分隔）。CAE 下拉里那个干净的 `CPRESS` 是 CAE 自己合成的聚合项；
   如果模型有多个接触对，ODB 里会有多个带不同后缀的同名场。

展示约定（已与需求方确认）：CSHEAR1、CSHEAR2 等**保持独立字段**，下拉里各占一项
（类似 S_MISES 这类标量场的展示方式），不合并成"一个场多个分量"。

## 2. 现状盘点：哪些已经支持、哪些是缺口

底子比想象的好——L1 的落盘格式和 L3 的部分链路本来就是稀疏友好的：

**已经支持（不用改）：**

- **L1 稀疏落盘**：L1 按 block 存 `labels + data`（`abaqus_dump.py` 的 NODAL 块存
  `block.nodeLabels` 的并集），不假设"所有节点都有值"。
- **L3 云图（frame-scalars）**：`result_service._expand_sparse_nodal_to_geometry_rows`
  会把带 labels 的稀疏 NODAL 数据映射回完整几何节点行，缺数据的节点填 NaN，
  前端把 NaN 顶点画灰 —— 正好是 CAE 的显示效果。
- **无数据的 instance**：前端把查询广播给所有 instance；没有该场数据的 instance，
  frame-scalars 路由已把 NotFound 转成"0 顶点空 payload"，前端对该 instance 不上色。
- **frame-scalar-range**：跳过无数据 instance，range 只按有数据的节点算（finite 值）。
- **节点表格（node_table_service）/ 按时间查值（node_time_value_service）**：
  都已按 `/NODAL/<inst>/labels` 做 label→行号对齐，缺节点返回 NaN/无数据。

**缺口（本次修改）：**

| # | 缺口 | 层 | 后果 |
|---|---|---|---|
| 1 | 字段名不归一化：`CPRESS ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1` 原样进 manifest 和前端下拉；多接触对时出现 N 个 CPRESS 变体 | L1 | 下拉又长又丑、同名量不聚合 |
| 2 | NODAL 块的 elem_type 兜底命名 `_auto_<N>x<C>` 会混进 HDF5 路径（`/NODAL/<inst>/_auto_...`），而 L3 只读 `/NODAL/<inst>/data` | L1 | 一旦触发该分支，整个场找不到数据 |
| 3 | pick（点击查值）的 NODAL 分支按**几何节点行号**直接索引结果数组 | L3 | 稀疏场行号错位 → 取到错误的值（张冠李戴），而不是报"无数据" |

## 3. 方案

### 3.1 L1：字段名归一化 + 同名接触对合并（`abaqus_dump.py`）

**识别规则**：字段名按第一个空白符拆成两段，若第二段**含 `/` 或以 `ASSEMBLY` 开头**，
则视为"带 region 后缀的字段"，基名 = 第一段，region = 第二段。
普通字段（U、S、RF……）名字里没有空格，不受影响，行为与现在完全一致。

**合并规则**：同一 step 内，把**基名相同**的所有 ODB 字段归为一个"逻辑字段"：

- 落盘目录 / manifest / HDF5 的 `field_name` 用基名（如 `CPRESS`）；
- 各成员（各接触对）的 bulkDataBlocks 全部并入同一套 block 结构 ——
  同 instance 的 NODAL 块 labels 取**并集**（现有的多 chunk 合并逻辑天然支持）；
- `meta.json` 里新增 `source_fields`：记录原始 ODB 字段名 + region，便于追溯；
- 只有一个接触对时也做重命名（`CPRESS ASSY.../...` → `CPRESS`）。

**为什么在 L1 做而不是 L3 做**：字段名要进 manifest.db、HDF5 meta、前端下拉、
所有查询参数。L1 归一化一次，下游全部拿到干净名字；若在 L3 做映射，
每个查询入口都要带一层"别名解析"，且多接触对的数据合并在 L3 做代价更高。

### 3.2 L1：NODAL 块不再使用 `_auto` 兜底命名

发现/写帧两处循环里，`position == 'NODAL'` 时 elem_type 一律置 `''`（NODAL 数据
本来就不分单元类型），保证 HDF5 路径恒为 `/NODAL/<inst>/…`，与 L3 读取路径一致。
ELEMENT_NODAL / WHOLE_ELEMENT 等位置的 `_auto` 兜底保持不变。

### 3.3 L1：并行 launcher 按逻辑字段分组（`abaqus_dump_parallel.py`）

同基名的字段必须分到**同一个 worker**（否则两个 worker 写同一个字段目录会互相覆盖）。
chunk 划分从"按字段"改为"按逻辑字段组"。

### 3.4 L3：pick 的 NODAL 分支做 label 对齐（`query_service._read_pick_result`）

若结果 HDF5 里存在 `/NODAL/<inst>/labels`，先把请求的几何节点行号换成节点 label，
再 searchsorted 到结果 labels 找数据行；找不到的节点视为"无数据"：

- node pick：该节点无数据 → NODAL 分支返回 None（走后续 fallback，最终 pick 只回几何信息）；
- element pick：三个角节点里有数据的参与平均，全都没有 → 同上。

对稠密场（labels == 几何全节点）这个对齐是恒等映射，行为不变。

### 3.5 前端：不改

- 云图：复用"NaN 顶点 → 灰色"与"空 payload → 不上色"的既有行为；
- 下拉：拿到的就是干净的 `CPRESS` / `CSHEAR1` / …，无分量标量场的展示方式已有先例。

## 4. 数据流小结（大白话）

1. 开发者本地重新解析接触 ODB → L1 把 `CPRESS ASSY…` 改名成 `CPRESS` 落盘，
   数据只有接触面那一圈节点（labels + data）。
2. L3 云图请求：把稀疏数据摊回全部节点，缺的填 NaN → 前端画灰；
   没有数据的另一个 instance 拿到空 payload → 整块保持底色。
3. 图例范围只按有数据的节点算，和 CAE 一致（0 → 最大压力）。
4. 点击接触面节点 → label 对齐后取到正确的值；点击非接触区节点 → 显示无数据。

## 5. 兼容性

- 旧 workspace（已解析的非接触 ODB）不受影响：普通字段名无空格不触发改名；
  pick 的 label 对齐对稠密场是恒等映射。
- 本次不改任何接口的 URL / 参数 / 响应结构；只有 pick 对"无数据节点"的语义
  从"错值"变成"无值"。

## 6. 开发者本地验证步骤

1. `git pull` 后重新提交接触 ODB（图例：`NTS_SLID1784272014.746.odb`）走 project 解析。
2. L1 日志应出现 `Field 'CPRESS' (merged from 1 region-qualified field(s))` 之类的行。
3. `GET /api/odb/{id}/meta/overview`：fields 里应看到干净的
   `CPRESS / CSHEAR1 / CSHEAR2 / COPEN / CSLIP1 / CSLIP2`（components 为空的标量场）。
4. 前端选 CPRESS：接触面有云图，同 instance 其余部分灰色；另一个 instance 整体灰色、
   控制台无报错。图例 min/max 与 CAE 对照（图例值 0 ~ 4.161e8 量级）。
5. pick 接触面上的节点：值与 CAE probe 一致；pick 非接触区节点：不显示结果值。
6. 节点表格 / node-time-value 选 CPRESS：接触面节点有值，其余 NaN/无数据。
