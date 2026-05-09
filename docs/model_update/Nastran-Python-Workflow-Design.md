# Nastran Python 工作流设计稿

## 1. 文档目的

本文档用于规划如何把 `femtools_bas/` 里的 Nastran 工作流逐步改写为 Python 程序，并接入当前项目已有的 FastAPI 服务。

目标不是做一个通用的 Nastran 平台，而是做一套面向“模型修正 / 灵敏度 / 模态比对”的业务工作流。

这份文档回答四类问题：

1. 如何调用 Nastran 计算 `BDF`
2. 如何根据普通 `BDF` 生成 `SOL200` 灵敏度分析 `BDF`
3. 如何用 `pyNastran` 解析 `BDF` 和 `OP2`
4. 如何从 `OP2` 中提取模态振型和灵敏度矩阵，并接入后续试验模态比对流程

## 2. 当前现状

仓库里已经有几块可复用基础：

- [BDFParserPyNastran.py](/D:/WorkSpace/OtherProjects/VirtualReal702/BDFParserPyNastran.py)：已能用 `pyNastran` 解析 `BDF`
- [Op2Reader.py](/D:/WorkSpace/OtherProjects/VirtualReal702/Op2Reader.py)：已证明可以读取 `OP2` 模态结果
- [services/model_update/importers/bdf_service.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/importers/bdf_service.py)：已能把 `BDF` 基础信息写库
- [services/model_update/solver_prep/nastran_sol103.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/solver_prep/nastran_sol103.py)：已能生成 `SOL103`
- [services/model_update/analysis/solver_service.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/analysis/solver_service.py)：已能调用本地 Nastran 跑 `SOL103`

所以这次不是从零开始，而是在现有雏形上补齐缺口。

## 3. 目标范围

> 顺序修正说明  
> 第一阶段的真实业务顺序应理解为：先 `SOL103 -> OP2 模态 -> 模态匹配 / DAC / DSF / 后续 MAC`，再 `SOL103 -> SOL200 -> OP2 灵敏度矩阵`。  
> 因此，`SOL200` 在这里是“第二条主线”，不是第一阶段入口。

第一阶段目标包括：

1. 调用 Nastran 计算 `BDF`
2. 根据 `BDF` 生成 `SOL200` 灵敏度求解 `BDF`
3. 使用 `pyNastran` 解析 `BDF`
4. 使用 `pyNastran` 解析 `OP2`
5. 从 `OP2` 中提取模态振型
6. 从 `OP2` 中提取灵敏度矩阵

这里第 6 点是本次补充的关键范围。之前如果只提“模态振型”，那还不够支撑后续模型修正。因为贝叶斯迭代、参数筛选、响应比对，最终都需要灵敏度矩阵。

## 4. 总体分层

建议把 Python 版 Nastran 能力分成 4 层。

### 4.1 输入生成层

职责：

- 读取原始 `BDF`
- 生成 `SOL103` 或 `SOL200` 版本的分析 `BDF`
- 不负责真正执行求解

建议位置：

- `services/model_update/solver_prep/nastran_sol103.py`
- 新增 `services/model_update/solver_prep/nastran_sol200.py`

### 4.2 求解调用层

职责：

- 拼接 Nastran 命令
- 建立工作目录
- 执行 Nastran
- 回收输出产物，如 `.f06/.op2/.xdb/.log`

建议位置：

- `services/model_update/analysis/solver_service.py`

### 4.3 结果解析层

职责：

- 解析 `BDF`
- 解析 `OP2`
- 提取模态频率、模态振型、灵敏度矩阵
- 整理成统一 Python 数据结构

建议位置：

- 保留 `services/model_update/importers/bdf_service.py`
- 新增 `services/model_update/importers/op2_service.py`

### 4.4 接口层

职责：

- 暴露 FastAPI 接口
- 控制请求/响应模型
- 未来接入异步任务状态

建议位置：

- `webapi/routers/solver.py`
- 后续如需结果导入，可新增 `webapi/routers/fem.py` 下的 Nastran 结果接口

## 5. 调用 Nastran 时不能只照搬最小命令

这一点要特别说明。

当前代码里 `run_nastran_sol103_job()` 走的是最简命令：

```text
nastran <bdf_name> [extra_args...]
```

但 `femtools_bas/nastran.bas` 实际做得更多。它不是“简单执行一个程序”，而是同时处理了这些事：

1. 根据 `nastran.ini` 读取执行程序路径和额外 flags
2. 区分本地运行和远程运行
3. 在不同分析类型下使用不同输出文件约定
4. 运行前清理旧文件
5. 运行后检查 `.f06`
6. 读取并导入 `.op2`、矩阵文件、灵敏度结果
7. 某些工况会额外插入 `PARAM POST`、`DSAPRT`、`EIGRL` 等卡片

所以 Python 版应采用“两步走”：

### 第一步：先保留简单本地模式

只支持：

- 本地执行
- 明确输入 `BDF`
- 明确返回 `.f06/.op2`

这样最容易先打通。

### 第二步：再逐步补 FEMTools 的行为

后续可补：

- 从配置文件加载 Nastran 路径和默认参数
- 统一输出文件命名
- 运行前清理残留锁文件/旧结果
- `.f06` 中 `FATAL` 错误扫描
- `SOL200` 的特殊输出文件支持

结论：**调用 Nastran 这一层必须参考 BAS 的处理逻辑，不能只看现在仓库里最小命令示例。**

## 6. SOL200 在业务里的定位

这里再补一条很关键的顺序约束：

- `SOL200` 不应该先于模态链路单独落地
- 它的前提是：已经有一份可运行的模态分析模型，通常就是 `SOL103`
- 它的用途是：在模态分析模型和用户选定参数已经明确之后，进一步求灵敏度

这里的 `SOL200` 不是为了做“完整优化”，而是为了做：

- 设计变量定义
- 设计响应定义
- 设计灵敏度计算
- 灵敏度矩阵提取

按照 `femtools_bas/nastran.bas` 和 `nastranopt.bas` 当前语义，第一阶段重点关注：

- 设计变量：`H / E / RHO`
- 响应：模态频率 `FREQ`
- 输出：灵敏度矩阵

后续再扩展到：

- `AX / IX / IY / IZ`
- 位移、应力、应变类响应

## 7. OP2 解析目标

Python 版 `OP2` 解析层第一阶段至少要产出两类数据。

### 7.1 模态结果

包括：

- 子工况 `subcase`
- 模态阶次 `mode_number`
- 频率 `frequency_hz`
- 特征值 `eigenvalue`
- 每个节点的 `TX/TY/TZ/RX/RY/RZ`

### 7.2 灵敏度矩阵

这里的“灵敏度矩阵”不是一个抽象概念，而是最终要整理成：

- 行：响应
- 列：参数
- 值：响应对参数的导数，或按业务定义归一化后的灵敏度

第一阶段设计上先不强行规定数据库表怎么落，只先规定解析层要能输出中间结果：

```python
{
  "parameters": ["T1", "E1", "RHO1"],
  "responses": ["MODE_1_FREQ", "MODE_2_FREQ"],
  "matrix": [
    [0.12, -0.03, 0.004],
    [0.08, -0.01, 0.002]
  ],
  "value_mode": "absolute_or_normalized",
  "source": {
    "op2_path": "...",
    "bdf_path": "...",
    "subcase": 1
  }
}
```

为什么先做中间结果而不是直接写库：

- 因为不同求解器输出格式可能不完全一致
- 因为后面可能还要和 Abaqus 的灵敏度矩阵格式统一
- 因为先有可预览中间结果，后续排错会容易很多

## 8. 模态结果如何和试验模态做自由度匹配

这是你补充的第二个关键问题。

答案是：**不是直接拿 Nastran 模态结果和试验模态“按节点编号对比”，而是先走你项目现有的节点匹配和 DOF 匹配链路。**

当前项目已有的思路在 [services/model_update/analysis/inp_service.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/analysis/inp_service.py) 中已经实现，核心分两步：

### 第一步：节点匹配

先确定：

- 试验测点 `test_node_id`
- 对应到哪个 FEM 节点 `fem_node_label`
- 属于哪个 `instance_name`

这一步解决的是“空间上谁对应谁”。

### 第二步：自由度匹配

再根据试验通道方向，得到：

- `test_dof`
- `fem_dof`
- 方向向量 `direction_x/y/z`

这一步解决的是“方向上怎么投影”。

### 在模态比对时怎么用

对于 FEM 模态振型，不能只取 `UX/UY/UZ` 某一列直接比，而是要：

1. 找到匹配 FEM 节点
2. 取该节点 FEM 模态向量 `[ux, uy, uz]`
3. 沿试验通道方向向量做投影
4. 得到一个和试验通道同语义的标量响应
5. 再与试验模态在该通道的值比较

当前代码中的 `DAC / DSF` 计算就是按这个逻辑做的。

所以对 Nastran 来说，不需要发明一套新的自由度匹配机制。更好的做法是：

- 先把 Nastran 模态结果导入成和 Abaqus FEM 模态结果同结构的数据
- 然后复用现有 `match_nodes -> match_dofs -> modal_correlation` 这条链

结论：**Nastran 模态结果的接入重点不是另做一套匹配算法，而是把解析结果整理成当前系统已经能消费的 FEM 模态结果结构。**

## 9. MAC 是否属于 BAS 内置响应

这是你补充的第四个问题。

先说结论：**从当前 `nastran.bas / nastranopt.bas` 可见逻辑看，MAC 不是 BAS 里直接交给 Nastran `SOL200` 去求的内置设计响应。**

目前 BAS 里清楚看到的是：

- 设计变量映射：`DESVAR`
- 属性/材料关联：`DVPREL1 / DVMREL1`
- 设计响应：`DRESP1`
- 当前示例里明确支持的响应是 `FREQ`

而 `MAC` 的本质不是一个传统有限元求解器内部标量响应，它更像一个**后处理相似度指标**。它依赖：

- 试验模态向量
- FEM 模态向量
- 两边已经完成空间与 DOF 对齐

所以它更适合放在**应用层后处理**，而不是直接指望 `SOL200 DRESP1` 里就有现成 `MAC`。

业务上建议这样理解：

- `FREQ`：适合放在求解器内，做 Nastran 自带响应
- `MAC`：适合在求解后，由 Python 服务读 `OP2` 和试验模态数据后自己算

这也更符合你当前系统已经在做的 `DAC / DSF` 风格。

## 10. 推荐接口草案

> 顺序修正说明  
> 接口落地时，建议先实现：
> 1. `POST /solver/nastran/run`，重点支持已有 `SOL103`  
> 2. `POST /import/op2/modal`，先提取 FEM 模态频率和振型  
> 3. 再实现 `POST /solver/nastran/sol200/generate` 与 `POST /solver/nastran/sol200/run`  
> 4. 最后实现 `POST /import/op2/sensitivity`

第一阶段建议的接口不需要很多，先够用就行。

### 10.1 运行已有 BDF

`POST /solver/nastran/run`

作用：

- 直接执行已有 `BDF`

### 10.2 生成 SOL200 BDF

`POST /solver/nastran/sol200/generate`

作用：

- 根据参数和响应定义生成 `SOL200` 版本 `BDF`
- 先只返回预览和输出文件路径

### 10.3 生成并运行 SOL200

`POST /solver/nastran/sol200/run`

作用：

- 先生成 `SOL200`
- 再调用 Nastran

### 10.4 解析 OP2 模态

`POST /import/op2/modal`

作用：

- 提取模态频率与模态振型

### 10.5 解析 OP2 灵敏度矩阵

`POST /import/op2/sensitivity`

作用：

- 提取设计响应对设计变量的灵敏度矩阵
- 第一阶段建议先支持 JSON 预览

## 11. 第一阶段实施边界

> 顺序修正说明  
> 第一阶段验收时，优先看这 5 件事是否成立：
> 1. 能运行已有 `SOL103`
> 2. 能从 `OP2` 读出模态频率与振型
> 3. 能把 FEM 模态结果接到现有模态相关性流程
> 4. 能把当前模态分析模型改写成 `SOL200`
> 5. 能从 `OP2` 读出灵敏度矩阵

第一阶段建议只做最小闭环：

1. 能本地调用 Nastran
2. 能生成 `SOL200`
3. 能从 `OP2` 读出模态振型
4. 能从 `OP2` 读出灵敏度矩阵
5. 能把 FEM 模态结果整理成现有模态相关性流程可消费的格式

先不做：

- 远程 Nastran
- 通用优化流程
- 一次性支持所有 `DRESP1`
- 一次性支持所有梁/壳/实体参数类型

## 12. 下一份文档的作用

本文档解决的是“这条链路怎么分层、范围是什么、关键取舍是什么”。

下一份文档 [Nastran-SOL200-Field-Mapping-Design.md](/D:/WorkSpace/OtherProjects/VirtualReal702/docs/model_update/Nastran-SOL200-Field-Mapping-Design.md) 会专门说明：

- `SOL200` 卡片怎么映射
- 每种设计变量从哪里找数据
- 每种响应怎么落到 `DRESP1`
- 灵敏度矩阵和模态结果的统一输出格式建议
