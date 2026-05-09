# Nastran 接口与异步任务设计稿

## 1. 文档目的

本文档用于设计 Nastran Python 化之后，对外提供哪些接口，以及哪些接口应走异步任务模式。

这份文档重点回答：

1. Nastran 相关接口怎么分层
2. 哪些接口适合同步，哪些必须异步
3. 异步状态接口该返回什么
4. 如何和当前项目已有的后台任务风格保持一致

本文档是前三份设计稿的接口落地补充：

- [Nastran-Python-Workflow-Design.md](/D:/WorkSpace/OtherProjects/VirtualReal702/docs/model_update/Nastran-Python-Workflow-Design.md)
- [Nastran-SOL200-Field-Mapping-Design.md](/D:/WorkSpace/OtherProjects/VirtualReal702/docs/model_update/Nastran-SOL200-Field-Mapping-Design.md)
- [Nastran-OP2-Parsing-Design.md](/D:/WorkSpace/OtherProjects/VirtualReal702/docs/model_update/Nastran-OP2-Parsing-Design.md)

## 2. 为什么 Nastran 这条线要重点做异步

和普通的参数预览、配置生成不同，Nastran 流程有几个天然特点：

1. 求解时间可能很长
2. 结果文件可能很大
3. 求解期间会占用 CPU、磁盘、求解器 license
4. 同一条链路往往是“生成输入 -> 调用求解器 -> 解析 OP2 -> 可选写库”

这意味着：

- “只生成配置”这种很轻的能力可以同步
- “真正调用求解器”这种能力最好默认异步
- “解析大 OP2 并写库”在样本大时也可能需要异步

如果不做异步，用户一旦发起长时间 Nastran 任务，整个 Web 服务体验会非常差，甚至把其他请求也拖慢。

## 3. 当前项目已有的异步风格

当前项目已经有一套比较清楚的后台任务机制，核心在：

- [webapi/background_jobs.py](/D:/WorkSpace/OtherProjects/VirtualReal702/webapi/background_jobs.py)

它已经支持：

- `submitted / running / succeeded / failed / aborted`
- 持久化任务到 `background_tasks.db`
- `request / progress / result / error`
- 进程重启后的任务状态恢复

当前已经接上这套机制的代表接口有：

### 3.1 灵敏度任务

- [webapi/routers/sensitivity.py](/D:/WorkSpace/OtherProjects/VirtualReal702/webapi/routers/sensitivity.py)

已有风格：

- `POST /sensitivity/run_and_store`
- `POST /sensitivity/generate_run_and_store`
- `GET /sensitivity/tasks/{task_id}`

### 3.2 贝叶斯修正任务

- [webapi/routers/optimization.py](/D:/WorkSpace/OtherProjects/VirtualReal702/webapi/routers/optimization.py)

已有风格：

- `POST /optimization/bayesian/run`
- `GET /optimization/bayesian/tasks?task_id=...`
- 运行中会返回 `progress.current_iteration / total_iterations`

### 结论

Nastran 这条线不应该另起炉灶，而应该复用这套已有任务机制。

## 4. Nastran 接口分层建议

建议把 Nastran 对外接口拆成 4 类。

### 4.1 预览类接口

特点：

- 不调用求解器
- 不跑大计算
- 只做配置检查、文本生成、结果预览

这类接口适合同步。

### 4.2 输入生成类接口

特点：

- 生成 `SOL103` / `SOL200` BDF
- 主要是文件改写
- 一般几十毫秒到几秒

默认也适合同步，但要保留后续异步扩展空间。

### 4.3 求解执行类接口

特点：

- 真正调用 Nastran
- 时间长、资源重

这类接口建议默认支持异步，并鼓励异步使用。

### 4.4 结果导入类接口

特点：

- 读取 `OP2`
- 提取模态或灵敏度矩阵
- 可选写库

小文件可同步，大文件建议允许异步。

## 5. 第一阶段建议提供的接口

> 顺序修正说明  
> 第一阶段接口不要默认从 `SOL200` 开始，而应按下面顺序落地：
> 1. `SOL103` 求解接口
> 2. `OP2` 模态预览 / 导入接口
> 3. `SOL200` 预览 / 生成接口
> 4. `SOL200` 求解接口
> 5. `OP2` 灵敏度预览 / 导入接口

下面给的是“接口清单草案”，不是现在立刻全实现，但建议后面按这个体系演进。

## 5.1 BDF 基础与预览

### `POST /solver/nastran/bdf/preview`

作用：

- 检查 `BDF` 是否可读
- 返回基础摘要

建议输入：

- `input_bdf`
- `include_cards_summary`

建议输出：

- 节点数
- 单元数
- 材料数
- 属性数
- 支持的参数候选摘要
- warnings

### 为什么需要这个接口

因为很多 Nastran 项目真正出问题不是在求解，而是输入模型本身不满足后续 `SOL200` 映射条件。  
先有一个轻量预览接口，用户可以在跑大任务前先确认模型结构是否合理。

## 5.2 SOL200 生成

这一节在实现顺序上应排在 `SOL103` 求解与 `OP2` 模态解析之后。

### `POST /solver/nastran/sol200/preview`

作用：

- 不写正式文件
- 只返回 `SOL200` 控制段预览和参数/响应映射预览

建议输入：

- `input_bdf`
- `parameters`
- `responses`
- `settings`

建议输出：

- `control_lines_preview`
- `desvar_preview`
- `relation_preview`
- `response_preview`
- `warnings`

### `POST /solver/nastran/sol200/generate`

作用：

- 真正生成 `SOL200 BDF`

建议输入：

- `input_bdf`
- `output_bdf`
- `parameters`
- `responses`
- `settings`

建议输出：

- `output_bdf`
- `parameter_count`
- `response_count`
- `warnings`

### 同步还是异步

第一阶段建议同步。

原因：

- 这一步主要是文本改写
- 即使模型大，一般也远比求解快
- 同步更方便前端直接拿生成结果检查

## 5.3 Nastran 求解执行

> 顺序修正说明  
> 这一节里的通用 `run` 接口，第一阶段默认重点支持已有 `SOL103`。  
> `SOL200 run` 则应视为第二条主线的求解接口。

### `POST /solver/nastran/run`

作用：

- 直接运行已有 `BDF`

建议输入：

- `input_bdf`
- `output_dir`
- `job_name`
- `extra_args`
- `run_solver`
- `timeout_sec`
- `async_submit`

建议输出：

- 若同步：直接返回求解结果摘要
- 若异步：返回 `task_id`

### `POST /solver/nastran/sol200/run`

作用：

- 先生成 `SOL200`
- 再调用 Nastran

建议输入：

- `input_bdf`
- `output_bdf`
- `parameters`
- `responses`
- `settings`
- `output_dir`
- `job_name`
- `timeout_sec`
- `async_submit`

建议输出：

- 同步：返回 `generated_bdf + solver_result + artifacts`
- 异步：返回 `task_id`

### 同步还是异步

建议这两类接口都支持同步和异步，但：

- 前端默认走异步
- 文档里明确推荐异步

### 为什么不是“只保留异步”

因为在开发调试期，同步接口非常方便。

比如：

- 小模型验证
- 看命令拼接是否正确
- 看 `.f06` 错误信息

如果强制全异步，开发阶段排错会变慢很多。

## 5.4 OP2 模态解析

这一节在第一阶段应先于 `SOL200` 灵敏度接口落地，因为它直接服务于 FEM 模态与试验模态的比对。

### `POST /import/op2/modal/preview`

作用：

- 读取 `OP2`
- 返回模态摘要与节点预览

建议输入：

- `op2_path`
- `bdf_path` 可选
- `mode_numbers`
- `subcase_id` 可选

建议输出：

- 模态阶次列表
- 频率
- 每阶少量节点预览
- warnings

### `POST /import/op2/modal/store`

作用：

- 读取 `OP2`
- 整理为 `t_mt_py_fem_modal_result` 结构
- 写库

建议输入：

- `project_id`
- `op2_path`
- `bdf_path`
- `mode_numbers`
- `overwrite`
- `async_submit`

建议输出：

- `mode_count`
- `row_count`
- `rows_preview`

### 同步还是异步

建议：

- `preview` 同步
- `store` 同步优先，但允许异步

原因：

- 预览本来就是为了快速人工检查
- 真正导入大模型全部模态时，可能比较慢

## 5.5 OP2 灵敏度矩阵解析

这一节在第一阶段应排在 `OP2` 模态解析之后。

### `POST /import/op2/sensitivity/preview`

作用：

- 提取灵敏度矩阵原始预览
- 不直接写库

建议输入：

- `op2_path`
- `bdf_path` 可选
- `parameter_names` 可选
- `response_names` 可选

建议输出：

- `row_labels`
- `column_labels`
- `matrix_preview`
- `value_mode`
- `warnings`

### `POST /import/op2/sensitivity/store`

作用：

- 把已确认映射关系的灵敏度矩阵写入现有灵敏度表结构

建议输入：

- `project_id`
- `batch_no`
- `case_name`
- `op2_path`
- `bdf_path` 可选
- `parameter_names`
- `response_names`
- `mapping_mode`
- `async_submit`

建议输出：

- `analysis_run_id`
- `response_count`
- `parameter_count`
- `point_count`

### 同步还是异步

建议：

- `preview` 同步
- `store` 允许异步，并推荐异步

原因：

- 灵敏度矩阵预览通常是小范围检查
- 真正写库时会带矩阵展开、元数据构建和较多 DB 写入

## 6. 异步任务统一设计

## 6.1 任务类型命名建议

为了和现有 `sensitivity.*`、`optimization.*` 风格一致，建议使用点号命名：

- `solver.nastran.run`
- `solver.nastran.sol200.run`
- `import.op2.modal.store`
- `import.op2.sensitivity.store`

### 为什么这样命名

因为一眼就能看出：

- 这是哪一条业务线
- 是求解任务还是导入任务
- 对应哪个接口

后面查日志和状态也更清楚。

## 6.2 状态枚举

建议继续复用现有后台任务状态：

- `submitted`
- `running`
- `succeeded`
- `failed`
- `aborted`

不要为 Nastran 单独发明另一套状态词。

## 6.3 任务进度 `progress` 设计

Nastran 任务如果只返回一个 `running`，信息量太少。  
建议从第一阶段开始就带阶段性进度。

### 6.3.1 求解任务进度

建议格式：

```json
{
  "phase": "running_solver",
  "step": "solver",
  "job_name": "case1_sol200",
  "output_dir": "D:/temp/case1",
  "message": "Nastran solver is running"
}
```

更细一点可拆成：

- `preparing_input`
- `running_solver`
- `parsing_f06`
- `collecting_artifacts`
- `parsing_op2`
- `storing_results`

### 6.3.2 模态导入任务进度

建议格式：

```json
{
  "phase": "importing_modes",
  "subcase_id": 1,
  "current_mode": 3,
  "total_modes": 10
}
```

### 6.3.3 灵敏度导入任务进度

建议格式：

```json
{
  "phase": "storing_sensitivity",
  "response_count": 20,
  "parameter_count": 8,
  "point_count": 160
}
```

### 为什么进度要这么细

因为 Nastran 相关任务失败时，用户最想知道的不是“失败了”，而是：

- 卡在生成输入
- 卡在求解
- 卡在读 `OP2`
- 还是卡在写库

把阶段显式返回，排障体验会好很多。

## 7. 状态查询接口设计

建议 Nastran 相关后台任务统一走一条状态查询风格，不要一部分用路径参数，一部分用查询参数。

结合你现在项目的演进，我建议未来优先统一成**查询参数形式**：

### 建议接口

`GET /tasks/background?task_id=...`

或者在 Nastran 业务下先做：

`GET /solver/nastran/tasks?task_id=...`

### 为什么倾向查询参数

因为你最近已经把 Bayesian 那边改成了：

`GET /optimization/bayesian/tasks?task_id=...`

继续沿这条线统一，会比混着用更清楚。

### 第一阶段的保守方案

如果暂时不想做全局统一，也可以先为 Nastran 业务单独做：

- `GET /solver/nastran/tasks?task_id=...`
- `GET /import/op2/tasks?task_id=...`

但长期更建议收敛到一条公共任务查询接口。

## 8. 状态接口返回结构建议

建议继续复用当前 `background_jobs.py` 已有快照结构：

```json
{
  "task_id": "uuid",
  "task_type": "solver.nastran.sol200.run",
  "status": "running",
  "submitted_at": "...",
  "started_at": "...",
  "finished_at": null,
  "request": {},
  "progress": {},
  "result": null,
  "error": null
}
```

### 额外建议

对 Nastran 任务，`result` 里建议额外包含：

- `artifacts`
- `f06_path`
- `op2_path`
- `generated_bdf`
- `warnings`

对失败任务，`error.details` 里建议尽量包含：

- `job_name`
- `output_dir`
- `f06_path`
- `stdout_tail`
- `stderr_tail`

### 为什么

因为求解失败时，最有用的信息通常就在这些文件和尾部日志里。

## 9. 推荐的同步/异步边界

为了后续不反复纠结，建议第一阶段明确一条表。

| 接口类型 | 建议默认方式 | 说明 |
| --- | --- | --- |
| `BDF preview` | 同步 | 轻量检查 |
| `SOL200 preview` | 同步 | 轻量检查 |
| `SOL200 generate` | 同步 | 文件生成 |
| `Nastran run` | 异步推荐 | 真正求解 |
| `SOL200 run` | 异步推荐 | 真正求解 |
| `OP2 modal preview` | 同步 | 预览检查 |
| `OP2 modal store` | 同步/异步都可 | 取决于模型大小 |
| `OP2 sensitivity preview` | 同步 | 预览检查 |
| `OP2 sensitivity store` | 异步推荐 | 解析 + 写库 |

## 10. 接口请求模型建议

建议 Nastran 新接口统一包含这些通用字段：

- `output_dir`
- `job_name`
- `timeout_sec`
- `extra_args`
- `async_submit`

### 为什么

这样：

- 用户使用成本低
- 路由层参数风格统一
- `submit_background_task()` 的接线也统一

不然每个接口都长得不一样，前后端都会很累。

## 11. 错误设计建议

Nastran 相关接口的错误最好显式区分三层：

### 11.1 输入层错误

例子：

- `input_bdf` 不存在
- `op2_path` 不存在
- 参数映射不完整

### 11.2 求解层错误

例子：

- 求解器未找到
- `.f06` 中出现 `FATAL`
- 求解结束但未产出 `.op2`

### 11.3 解析层错误

例子：

- 未找到模态结果块
- 节点映射不完整
- 灵敏度标签无法解析

### 为什么要分层

因为用户处理方式不一样：

- 输入层错误一般改请求
- 求解层错误一般看模型/求解器
- 解析层错误一般看文件格式和解析器

## 12. 第一阶段建议不做的接口

为了避免铺太开，第一阶段建议先不做：

- Nastran 远程调度接口
- 任务取消接口
- 任务重试接口
- 批量任务编排接口

这些都不是现在的主矛盾。  
现在的主矛盾是先把“可用的 Nastran 单任务链路”走通。

## 13. 推荐的实施顺序

> 顺序修正说明  
> 本章如果只保留一条主线，应理解为：  
> `SOL103` 求解 -> `OP2` 模态预览/导入 -> `SOL103 -> SOL200` 预览/生成 -> `SOL200` 求解 -> `OP2` 灵敏度预览/导入

建议按下面顺序实现接口，而不是一口气全上。

### 第一步

- `POST /solver/nastran/sol200/preview`
- `POST /solver/nastran/sol200/generate`

先验证输入生成层。

### 第二步

- `POST /solver/nastran/run`
- `POST /solver/nastran/sol200/run`

先打通求解器调用层。

### 第三步

- `POST /import/op2/modal/preview`
- `POST /import/op2/modal/store`

先打通模态结果导入链。

### 第四步

- `POST /import/op2/sensitivity/preview`
- `POST /import/op2/sensitivity/store`

最后接灵敏度矩阵。

### 为什么这么排

因为这条顺序能把风险逐步隔离：

1. 先确认输入写对
2. 再确认求解能跑
3. 再确认模态能读
4. 最后再碰最复杂的灵敏度矩阵

## 14. 本文档的落地结论

如果把第四份稿子压缩成一句话，就是：

**Nastran 这条线的接口设计应复用当前项目已有的后台任务机制，预览类接口同步，真正求解和大结果写库接口默认异步，并且从第一阶段开始就返回结构化进度信息。**

这样做的好处是：

- 和当前 `sensitivity`、`bayesian` 体验一致
- 后面前端接入简单
- 任务失败时更容易定位卡在哪一层
- 不会再出现“一个求解任务把整个服务拖住”的问题
