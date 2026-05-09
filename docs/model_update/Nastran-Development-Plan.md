# Nastran 开发计划（第一阶段）

## 1. 目标

第一阶段先不追求一次性把 Nastran 全部能力做完，而是优先打通两条最小主线：

1. 模态主线  
   `BDF -> SOL103 生成 -> Nastran 求解 -> OP2 模态解析 -> 整理成当前系统可消费的 FEM 模态结果`
2. 灵敏度主线  
   `BDF -> SOL200 生成 -> Nastran 求解 -> OP2 灵敏度矩阵解析 -> 整理成当前系统可消费的灵敏度结果结构`

其中，模态主线优先级最高，原因是：

- 现有代码基础更多
- 更容易验证结果是否正确
- 可以尽快接到现有模态匹配、`DAC / DSF` 流程

## 2. 第一阶段范围

### 2.1 先做

- `SOL103` 预览、生成、运行接口
- `SOL103` 运行异步任务提交与状态查询
- `OP2` 模态预览与导入
- `SOL200` 预览、生成、运行
- `OP2` 灵敏度矩阵预览与导入

### 2.2 暂不做

- Nastran 远程调度
- 一次性支持所有 `SOL200` 参数类型
- 一次性支持所有 `DRESP1`
- 自动归一化全部灵敏度矩阵变体
- 面向前端的一次性全流程编排接口

## 3. BAS 到 Python 的迁移原则

迁移重点不是逐行翻译 `femtools_bas`，而是保留其中已经被业务验证过的语义：

- `SOL103 / SOL200` 控制段写法
- 参数与响应到 Nastran 卡片的映射逻辑
- 求解前后清理与结果文件约定
- 结果读取后的桥接方向

不直接迁移的部分主要是 FEMtools 运行时专属 API，例如：

- `Ft_Command`
- `Ft_Import`
- `Ft_GetShape`
- `Ft_Run`

这些逻辑在 Python 版中要改造成当前项目自己的 service 和 router 结构。

## 4. 第一阶段接口顺序

建议按下面顺序落地接口：

1. `POST /solver/nastran/sol103/preview`
2. `POST /solver/nastran/sol103/generate`
3. `POST /solver/nastran/sol103/run`
4. `GET /solver/nastran/tasks?task_id=...`
5. `POST /import/op2/modal/preview`
6. `POST /import/op2/modal/store`
7. `POST /solver/nastran/sol200/preview`
8. `POST /solver/nastran/sol200/generate`
9. `POST /solver/nastran/sol200/run`
10. `POST /import/op2/sensitivity/preview`
11. `POST /import/op2/sensitivity/store`

这样安排的好处是：

- 每一步都能单独用 APIfox 验证
- 先打通稳的模态主线
- 把最依赖真实样本的灵敏度矩阵部分放后面

## 5. 第一阶段验收

### 5.1 模态主线验收

- 能从已有 `BDF` 生成 `SOL103`
- 能调用本地 Nastran 并得到 `.op2`
- 能从 `OP2` 中读出模态阶次、频率、节点位移
- 能整理成当前系统已有 FEM 模态结果结构

### 5.2 灵敏度主线验收

- 能从已有 `BDF` 生成 `SOL200`
- 能调用本地 Nastran 并得到带灵敏度结果的 `.op2`
- 能输出原始灵敏度矩阵预览
- 能明确行、列、数值和 `value_mode`
- 能整理成当前系统现有灵敏度表结构兼容 payload

## 6. 当前执行策略

当前开发从 `SOL103` 相关能力开始，优先补齐：

- `preview`
- `generate`
- `run`
- `tasks`

等 `SOL103` 这一条最小主线具备稳定接口后，再继续进入 `OP2` 模态解析。
