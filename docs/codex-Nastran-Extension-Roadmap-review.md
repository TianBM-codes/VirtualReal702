# Codex 对 `Nastran-Extension-Roadmap.md` 的审阅意见

> 审阅时间：2026-05-09  
> 审阅对象：`docs/Nastran-Extension-Roadmap.md`

## 结论

这份路线图的大方向是对的：**尽量把 Nastran 适配收敛在 L1，少动 L2/L3**。  
但文档里有几处对“现有代码真实结构”的判断不够准确，按当前版本直接实施，容易在项目模式、result group 流程和 manifest schema 上踩坑。

下面只列需要优先修改的硬问题。

## 1. `BDF + OP2` 链路的工作量被低估了

### 文档中的表述

- `docs/Nastran-Extension-Roadmap.md` 第 1 节把目标写成：
  - `BDF + OP2 → L1（bdf_pack.py + op2_pack.py）→ L2（无改动）→ L3（无改动）`
- 第 8、9 节又把实现范围总结成“改 `job_runner.py`、让接口接受 `.bdf/.op2` 后缀即可”。

### 实际代码情况

- `projects` 模式当前只识别 `inp` 和 `odb` 两种 `source_type`：
  - [src/l3/api/routes/projects.py](/root/codexDir/702/repo/src/l3/api/routes/projects.py:138)
- `job_runner` 对 project 的主分支也只有：
  - `source_type == "odb"` → `_run_odb_project(...)`
  - 其他情况 → `_run_geom_project(...)`
  - 见 [src/job_runner.py](/root/codexDir/702/repo/src/job_runner.py:857)
- 现有 `result_group` 解析链路是专门为 **ODB 追加结果** 写的，里面直接调用 `abaqus_dump.py --mode extract` 和 `l1_pack.py --result-group`：
  - [src/job_runner.py](/root/codexDir/702/repo/src/job_runner.py:927)

### 为什么这会出问题

路线图现在给人的感觉是：“项目先吃 BDF，结果组再吃 OP2，基本沿用现有 project/result_group 机制即可。”  
但真实情况不是“换个后缀”这么简单，而是：

- `project` 主流程目前没有 `bdf` 这种几何源类型
- `result_group` 追加流程目前默认“源文件是 ODB”
- `OP2` 不是现有 `abaqus_dump extract` 这套机制能直接接住的输入

也就是说，**BDF project + OP2 result_group 这条链路本身就需要重新定义状态机和 runner 分支**，文档里应该明确写出来，不能只写“修改 `job_runner.py` 识别后缀”。

### 建议改法

把文档里的说法改成更准确的版本：

- `L2/L3 的查询和渲染核心目标是尽量不改`
- 但 `projects API`、`job_runner`、`result_group` 任务流转需要扩展
- 要单独定义：
  - BDF 作为几何 project 时的 `source_type`
  - OP2 作为结果组时的提交入口和调度分支
  - BDF 与 OP2 的绑定关系如何校验

## 2. `result_group_meta` 的字段说明写错了

### 文档中的表述

在 `5.1 BDF → 几何 HDF5` 中写了：

- `result_group_meta（source='nastran', source_file=bdf路径）`

见 [docs/Nastran-Extension-Roadmap.md](/root/codexDir/702/repo/docs/Nastran-Extension-Roadmap.md:181)

### 实际代码情况

`result_group_meta` 表结构里没有 `source` 这个字段，只有：

- `result_group`
- `display_name`
- `source_file`
- `consistency_check`
- `created_at`

见 [src/l1/manifest_schema.py](/root/codexDir/702/repo/src/l1/manifest_schema.py:96)

### 为什么这会出问题

如果 Claude 按文档直接写 SQL，把 `source='nastran'` 往 `result_group_meta` 里插，就会直接报错。  
这不是“措辞不严谨”，而是会误导实际实现。

### 建议改法

把这里改成两层含义分开写：

- `result_group_meta` 只记录 `display_name/source_file/consistency_check`
- 数据来源区分如果要保留，应写在 `result_files.source`

同时把第 7 节里“通过 `source` 字段区分来源”的描述，明确限定到 `result_files`，不要让读者误以为 `result_group_meta` 也有这个字段。

## 3. `MeshElementFactory.py` 的判断前后矛盾

### 文档中的表述

第 8 节说：

- `MeshElementFactory.py` 需要修改

见 [docs/Nastran-Extension-Roadmap.md](/root/codexDir/702/repo/docs/Nastran-Extension-Roadmap.md:354)

但第 11.1 节又说：

- `MeshElementFactory.py` 已有完整 NASTRAN 分支，**无需修改**

见 [docs/Nastran-Extension-Roadmap.md](/root/codexDir/702/repo/docs/Nastran-Extension-Roadmap.md:437)

### 实际代码情况

当前 `MeshElementFactory` 的 `NASTRAN` 分支只覆盖这些低阶类型：

- `CQUAD4`
- `CTRIA3`
- `CHEXA`
- `CPENTA`
- `CTETRA`
- `CBAR`
- `CBEAM`
- `CELAS`

见 [MeshElementFactory.py](/root/codexDir/702/repo/MeshElementFactory.py:169)

### 为什么这会出问题

第 11.2 节的映射表已经把这些高阶类型写进去了：

- `CQUAD8`
- `CTRIA6`
- `CHEXA20`
- `CPENTA15`
- `CTETRA10`

见 [docs/Nastran-Extension-Roadmap.md](/root/codexDir/702/repo/docs/Nastran-Extension-Roadmap.md:455)

这会让读者误以为“当前底层元素工厂已经天然兜得住这些高阶 Nastran 单元”。  
但从代码看，这件事现在并不成立。

### 建议改法

二选一，文档必须统一：

1. 如果一期只做低阶单元：
   - 明确写 `MeshElementFactory.py` 无需修改
   - 同时把高阶卡型从“一期支持列表”降成“后续扩展项”

2. 如果一期就要支持高阶单元：
   - 明确写 `MeshElementFactory.py` 需要补齐高阶 Nastran 识别
   - 并说明是给哪一层用，不要只给一个映射表

按当前仓库状态，我更建议先走第 1 条。

## 4. 验收标准里的接口名和现代码不一致

### 文档中的表述

第二阶段验收标准写的是：

- `L3 /result/scalar 端点返回正确的位移/应力数据`

见 [docs/Nastran-Extension-Roadmap.md](/root/codexDir/702/repo/docs/Nastran-Extension-Roadmap.md:400)

### 实际代码情况

当前 L3 实际暴露的是：

- `GET /api/odb/{odb_id}/results/frame-colors`
- `GET /api/odb/{odb_id}/results/frame-scalars`

见 [src/l3/api/routes/results.py](/root/codexDir/702/repo/src/l3/api/routes/results.py:32) 和 [src/l3/api/routes/results.py](/root/codexDir/702/repo/src/l3/api/routes/results.py:76)

### 为什么这会出问题

这会导致后面几件事一起跑偏：

- 联调同学找错接口
- 验收标准没法直接对应现实现
- 后续如果同步更新 `docs/l3/L3-API-Quick-Reference.md`，会出现两套名字

### 建议改法

把验收标准里的接口名改成当前真实接口名。  
如果你想表达的是“标量结果接口”这个概念，也建议写成：

- `frame-scalars` 用于标量场/位移幅值
- `frame-colors` 用于直接取颜色后的渲染数据

这样更贴近现代码。

## 5. 建议补一节：Nastran 在现有 project/result_group 模式下的真实接法

这是这份路线图现在最缺的一块。

建议单独补一节，至少把下面三个问题讲明白：

1. `POST /api/projects` 提交 BDF 时，`source_type` 是新增 `bdf`，还是继续抽象成更宽泛的 `geom`？
2. `POST /api/projects/{project_id}/results` 提交 OP2 时，是继续复用 `result_group`，还是新增 Nastran 专用入口？
3. `bdf_pack.py` 产出的几何与 `op2_pack.py` 产出的结果，靠什么做一致性校验？

不把这三个点写清楚，后面实现时会一直在“借现有 ODB 机制，还是专门开 Nastran 分支”之间摇摆。

## 建议修改优先级

建议 Claude 先改这几处，再继续细化实现细节：

1. 先修正第 1、7、8、9 节对 project/result_group 流程的描述
2. 再修正 `manifest.db` 相关字段说明，保证和真实 schema 一致
3. 再统一 `MeshElementFactory.py` 的支持范围表述
4. 最后把接口名、验收标准和工作量评估改成和现代码一致

## 一句话总结

这份路线图的问题不是“方向错了”，而是**把现有 ODB 项目流和结果组流复用得太乐观了**。  
只要先把“BDF project / OP2 result_group / manifest 真字段”这三件事写实，后面的设计就会稳很多。
