# 静力 E 参数真实链路验证（2026-10-12）

## 已验证范围

工程 `2026101201`，服务 `http://127.0.0.1:18070`。
输入为 `run/static.inp`，原始结果为 `run/static.odb`。
工程实际目录：`D:/WorkSpace/Temp/702Input/database/2026101201`。

真实链路：通过工程创建接口导入 INP → 解析集合和材料并入 MySQL → 将用户提供的 WY4 坐标写入测点表 → 节点匹配接口 → 参数创建接口 → 响应创建接口 → 从数据库生成 DSA 输入 → Abaqus 2026 求解 → 结果登记和 HDF5 提取 → 灵敏度矩阵写入 MySQL → 合并云图场 → HTTP 查询五个显示接口。

WY4 坐标 `(0, -0.07635, 0)` 匹配到 `PART-1-1` 节点 `4`，节点坐标 `(0, -0.07625000178813934, 0)`，距离 `9.999821186065794e-05`。响应 UY 对应 Abaqus 的 U2。
参数为 `E@Set-149`，GLOBAL，初始值 `210000000000`。仅为测试设置上下限 `105000000000` / `315000000000`，scatter `0.1`，使用范围 UPDATE / SENSITIVITY。

成功灵敏度任务 `f9172a7a-c525-470d-a1d0-80ef3d1d7b2f`，批次 `3`，分析记录 `60`。
从数据库读回矩阵列名为 `E@Set-149`，行名为 `PART-1-1|U|U2|NODAL|4`，归一化灵敏度 `-1.011316943227`。
模型 UY 为 `0.5259250998497009`（INP 的长度单位；不能在未核实单位体系前认定为米）。

## 五个接口的实际结果

统一前缀 `/api/odb/2026101201`。

| 路径 | 查询参数 | 结果 |
|---|---|---|
| `/sensitivity/result_groups` | 无 | 200，包含 `sensitivity_3_U_4_U2` |
| `/sensitivity/fields` | `result_group=sensitivity_3_U_4_U2` | 200，`E__FRAME_0000`，节点 4 / U2 |
| `/sensitivity/step_frame` | 无 | 200，`Sensitivity` / frame 0 |
| `/results/frame-scalar-range` | `instances=PART-1-1&step=Sensitivity&field=E__FRAME_0000&frame=0&result_group=sensitivity_3_U_4_U2` | 200，min=max=`-2.5327474492686797e-12` |
| `/results/frame-scalars` | `instance=PART-1-1&step=Sensitivity&field=E__FRAME_0000&frame=0&result_group=sensitivity_3_U_4_U2` | 200，解压后 2544 字节，584 个有效顶点，legend 与范围接口一致 |

这里只有一个 GLOBAL 参数，146 个壳单元采用同一响应导数，因此 min=max 是合理结果，归一化后的顶点值均为 0。云图保存的是原始导数，数据库矩阵保存的是归一化灵敏度，两者数值不同。
前端依次读取结果组、字段和显示步，再读取范围和顶点数值进行着色；后端负责求解、数据存储和数值读取。

证据保存在工程 `cal/validation/`：

- `api-evidence.json`：五个接口的 URL、状态码、结果和二进制解析统计。
- `frame-scalars.l3be`：实际响应二进制数据。
- `database-and-run-evidence.json`：数据库查询接口结果、节点匹配、成功求解任务记录。
- `cal/sensitivity/`：生成的 INP、参数/集合 include、DSA 配置、ODB 和求解过程文件。

复查命令：

```powershell
python tools/verify_static_sensitivity_api.py --base-url http://127.0.0.1:18070 --project-id 2026101201 --result-group sensitivity_3_U_4_U2 --instance PART-1-1 --output-dir D:/WorkSpace/Temp/702Input/database/2026101201/cal/validation
```

## 发现并修复的问题

1. 旧节点配对表把测点限定为 INT，WY4 匹配失败。改为 VARCHAR(100)，并迁移已有列，原数字编号保留为字符串。
2. 本地结果登记和 PBS 路径未传新增原始源文件字段。补齐新建和重新提交两条路径。
3. DSA 生成器把选中的 E 偷换为同集合壳厚 T。保留 E，并为选中壳单元生成独立材料副本，仅把副本中的 E 参数化，厚度和原材料保持原值。
4. 矩阵发现字段时混入历史批次。指定结果组时过滤其它结果组。
5. 字段列表把 frame 序号当成响应节点号。对 `4:U2` 描述解析真实节点号和分量。

新增 E 路径目前沿用壳集合拆分逻辑，仅支持主 INP 中定义的常数各向同性材料；更复杂材料明确报错。没有验证实体/梁 E 参数生成。
新增回归测试 `tests/test_static_e_dsa_generation.py`，本次没有运行 pytest，验证采用实际 HTTP、MySQL、Abaqus 和文件数据。

## 原始 ODB 的版本问题

原始 `static.odb` 来自旧版 Abaqus，当前 Abaqus 2026 无法直接打开。
在工程下运行 upgrade，生成 `static_upgraded.odb`，以 `baseline_upgraded` 结果组成功导入。原始文件未修改。
首次失败的 `baseline` 和厚度测试批次 `1` 保留供排查；请使用批次 `3` 的 E 结果，不要把批次 `1` 当成 E 灵敏度。

## 尚未完成：真实修正迭代

后续用户已提供目标 `0.58`，真实修正迭代及逐步有效性验证现已完成。以下段落是首次验证时的历史状态；最新结论见 [完整修正验证记录](static-update-real-validation-2026-10-12.md)。

数据库没有 WY4 的实测位移，用户尚未提供目标值及单位，因此未执行 Bayesian 修正迭代，也未伪造迭代历史。
当前结论仅证明指定 E 参数的灵敏度求解、入库和五个显示接口可运行，不能宣称完整静力模型修正已通过。
后续需将真实目标位移保存到工程试验数据表，确认单位一致，再让修正接口从数据库读取目标并执行迭代、保存历史和最终模型。
