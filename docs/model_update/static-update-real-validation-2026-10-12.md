# 静力模型修正真实迭代验证

2026-10-12，工程 `2026101201`，修正批次 `4`。本记录补全首次只验证灵敏度的测试。

## 输入与实际流程

- 原始模型：`D:/WorkSpace/OtherProjects/VirtualReal702/run/static.inp`。
- 原始 ODB：`run/static.odb` 的升级副本已以 `baseline_upgraded` 登记；修正过程重新求解，不复用原始 ODB 来冒充后续结果。
- WY4 坐标 `(0, -0.07635, 0)`，匹配到 `PART-1-1::4`；响应 UY/U2。
- 参数 `E@Set-149`，GLOBAL，初值 `210000000000`。
- 用户提供目标 `0.58`，按模型同一长度单位存入 MySQL `t_mt_py_test_static_result`，测点名为 WY4。
- 最大评估步数 4，停止阈值为相对目标误差 `0.1%`。

实际提交 `/optimization/bayesian/run` 使用普通原始 INP，不在请求中提供目标向量或参数向量。参数、响应和目标均由数据库读取。后端先生成包含 E 参数和 UY 响应的 DSA 输入，再调用 Abaqus 求解当前模型，读取位移和灵敏度，计算新 E，更新下一份 INP，然后重新求解。

成功任务：`1d674f7e-9135-41be-9168-7474c6631066`。完成 3 次真实求解/评估及 2 次参数更新，最后一步检查满足阈值，不再调整参数。

| 评估状态 | 当前 E | WY4 UY | 相对目标误差 | 本步归一化灵敏度 |
|---|---:|---:|---:|---:|
| 初始模型，iter0 | 210000000000 | 0.5259250998497009 | 9.32325865% | -1.01131694322713 |
| 第一次修正后，iter1 | 189059202044 | 0.5840977430343628 | 0.70650742% | -1.011034568199357 |
| 第二次修正后，iter2 | 190345897425 | 0.5801546573638916 | 0.02666506% | -1.0110530329654037 |

最终 E 相对初始值降低约 `9.35910%`，最终位移距离目标的绝对差为 `0.00015465736389164153`。最终保存参数与最后一次实际求解参数一致，不把尚未求解的更新参数当成验证成功。

## 每个状态都有可显示的结果

服务：`http://127.0.0.1:18070`，接口前缀 `/api/odb/2026101201`。

| 状态 | 位移/原始灵敏度结果组 | UY 灵敏度云图结果组 |
|---|---|---|
| iter0 | `sensitivity_batch_update_4_iter0` | `sensitivity_update_4_iter0_U_4_U2` |
| iter1 | `sensitivity_batch_update_4_iter1` | `sensitivity_update_4_iter1_U_4_U2` |
| iter2 | `sensitivity_batch_update_4_iter2` | `sensitivity_update_4_iter2_U_4_U2` |

三个灵敏度云图组均由 `/sensitivity/result_groups` 返回，其 `/sensitivity/fields?result_group=...` 均有字段 `E__FRAME_0000`，响应节点 4、分量 U2。
`/sensitivity/step_frame` 返回显示步 `Sensitivity` 和 frame 0。该 frame 0 是每个独立灵敏度云图组的显示帧，不是把修正的所有状态混成一个结果。

位移云图参数：

```text
result_group=sensitivity_batch_update_4_iter0
step=Step-1
field=U
component_idx=1
frame=1
instance=PART-1-1
```

查询第二、第三状态分别将组名改为 `...iter1`、`...iter2`。范围接口 `/results/frame-scalar-range` 使用 `instances=PART-1-1`；顶点接口 `/results/frame-scalars` 使用 `instance=PART-1-1`。

灵敏度云图使用各状态的合并组、`step=Sensitivity&field=E__FRAME_0000&frame=0`。
参数变化云图使用 `result_group=bayesian_batch_4&step=BayesianUpdate&field=PARAMETER_RELATIVE_DELTA_PERCENT&component_idx=0`，分别查询 frame 0/1/2。
参数变化云图描述的是每次评估后提出的更新值；位移/灵敏度云图描述的是本次实际求解的当前参数状态，二者的物理含义不同。

## 有效性判据与实际结果

本次不以 HTTP 200 作为通过条件。真实验证脚本直接读取 HDF5 数组，独立计算期望值，并逐项对比 HTTP 返回数据：

1. 几何非空，584 个顶点、292 个三角形，坐标有限，三角形索引不越界。
2. 三个状态各自的位移、灵敏度、参数变化云图均有 584 个有效顶点，无 NaN/Inf。合计验证 9 份标量云图。
3. 每步 UY 的标量范围与本步 HDF5 中可见节点位移的最小/最大值一致；不是复用旧结果。范围分别为 `[-0.0248846635, 0.5259250998]`、`[-0.0276548620, 0.5840977430]`、`[-0.0274670068, 0.5801546574]`。
4. 584 个位移标量逐顶点对齐比较，最大归一化数值误差小于 `8e-8`。
5. 灵敏度云图原始导数与节点 4 的 DSA U2 导数一致；数据库归一化矩阵与 `E/UY × 原始导数` 一致。单个 GLOBAL 参数导致此云图最小值等于最大值，属于正确的常数场。
6. 参数变化云图各帧与对应的 E 更新百分比一致，分别约为 `-9.97181%`、`-9.35910%`、`-9.35910%`；最后一次为停止检查，因此参数保持不变。
7. 三个状态均查询 `/results/deformed-positions`，比较每个顶点的变形后位置是否等于原位置加本步节点位移。形状、顶点数量和数值均正确。
8. 数据库响应历史三行与本步实际求解值一致；最终静力结果表中节点 4 的 U2 等于 `0.5801546573638916`，分析误差表也记录 WY4 的 `0.02666506%`。
9. 重启后端服务后，再执行上述逐步验证全部通过，证明结果可从工程文件和数据库恢复。

## 保存位置

工程根目录：`D:/WorkSpace/Temp/702Input/database/2026101201`。

- `cal/bayesian/inputs/iteration_0000` 至 `iteration_0003`：独立的当前/更新 INP 与参数 include。每轮有自己的目录，后续迭代不覆盖历史参数。
- `cal/bayesian/workspace_iter0` 至 `workspace_iter2`：各次真实求解的完整提取数据。
- `cal/bayesian/iteration_001` 至 `iteration_003`：每步摘要、参数、响应、目标、灵敏度矩阵和单元映射。
- `cal/bayesian/history/overview.html`：过程总览；同目录有 CSV、JSON 和图表。
- `cal/validation/update_4/iteration-api-validity.json`：每一步接口 URL、范围、有效顶点数和逐顶点比对误差。
- `cal/validation/update_4/*.l3be`：实际返回的各步云图和变形结构数据。
- `cal/validation/update_4/run-result.json`：成功任务及最终结果保存路径。
- MySQL 保存试验目标、响应跟踪、参数跟踪、迭代指标、参数变化、最终位移和每步灵敏度矩阵。

复查命令：

```powershell
python tools/verify_static_update_iterations.py --base-url http://127.0.0.1:18070 --project-id 2026101201 --workspace D:/WorkSpace/Temp/702Input/database/2026101201 --batch-no 4
```

## 本轮修复

- 静力试验表支持 WY4 等字符串测点名，保留原数字编号。
- 通过 `(instance, node label)` 匹配记录找到 WY4 实测值，避免拿裸节点号误当成测点名。
- 普通 INP 自动按数据库参数和响应生成 DSA 输入；真实求解模式不隐式复用工程旧结果作为初始求解。
- 保留用户参数名 `E@Set-149`，额外记住 Abaqus 参数标识 `E_SET_149`，写回和读取当前参数均使用正确标识。
- 每步输入和 include 分目录保存，保留参数历史。
- 每步 ODB、灵敏度矩阵和显示场均登记到工程中，后续查询能获得所有状态的结果。
- 参数变化云图写入工程目录，而不是未登记的临时求解 workspace。
- 最终误差计算支持从已入库的静力试验结果读取目标，不依赖本机没有的外部静力数据表。

新增回归测试覆盖测点映射的实例隔离和参数 include 的历史隔离；没有运行 pytest。实际验证使用真实 Abaqus、MySQL、HTTP 和 HDF5 数据。
