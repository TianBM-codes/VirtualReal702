# Nastran OP2 解析设计稿

## 1. 文档目的

本文档专门回答这几个实现问题：

1. Python 侧如何读取 Nastran `OP2`
2. 如何从 `OP2` 中提取模态频率和模态振型
3. 如何从 `OP2` 中提取灵敏度矩阵
4. 解析结果如何和当前项目已有的数据库表、自由度匹配、模态相关性流程衔接

这份文档比前两份更“贴实现”，目标是后面写代码时可以直接照着拆模块，而不是临时边读 `OP2` 边猜。

## 2. 为什么单独做这份文档

`OP2` 是 Nastran 的结果文件，不是输入文件。

和 `BDF` 相比，`OP2` 的难点不在“能不能打开”，而在：

- 同一个文件里可能有几何，也可能没有几何
- 结果块很多，名字和组织方式不总是直观
- 节点顺序、结果顺序、模型节点顺序不一定一致
- 灵敏度矩阵就算读到了数值，也未必天然知道“哪一行是哪一个响应、哪一列是哪一个参数”

所以 `OP2` 解析必须单独设计，不适合只靠一个脚本边试边改。

## 3. 当前仓库里已经有的基础

### 3.1 参考脚本

文件：

- [Op2Reader.py](/D:/WorkSpace/OtherProjects/VirtualReal702/Op2Reader.py)

它已经证明了三件关键事：

1. 可以用 `pyNastran.op2.op2_geom.read_op2_geom()` 打开 `OP2`
2. 可以从 `op2.eigenvectors` 中读出模态振型
3. 不能假设结果顺序和几何节点顺序一致，必须按节点 ID 做映射

### 3.2 当前业务表结构

当前项目里已经有两类和本次设计直接相关的表：

1. `t_mt_py_fem_modal_result`
2. `t_mt_py_fem_sensitivity_matrix_result`

定义位置：

- [db.py](/D:/WorkSpace/OtherProjects/VirtualReal702/db.py)

当前已有的对应服务包括：

- [services/model_update/analysis/inp_service.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/analysis/inp_service.py) 中的 `import_fe_modal_results()`
- [services/model_update/analysis/sensitivity_service.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/analysis/sensitivity_service.py) 中的灵敏度矩阵存储逻辑

这很重要，因为说明我们不是要新发明一套数据库结构，而是优先考虑怎么复用现有结构。

## 4. 设计目标

> 顺序修正说明  
> `OP2` 解析虽然同时覆盖“模态”和“灵敏度”，但实现顺序必须明确：  
> 先做 `SOL103 OP2 -> 模态频率/振型`，先让 `DAC / DSF / 后续 MAC` 有可靠 FEM 输入；  
> 再做 `SOL200 OP2 -> 灵敏度矩阵`。

第一阶段 `OP2` 解析层建议支持两条主输出线：

### 4.1 模态输出线

输出内容：

- 模态阶次
- 模态频率
- 每个节点的模态位移分量
- 可选的旋转分量

### 4.2 灵敏度输出线

输出内容：

- 参数列表
- 响应列表
- 灵敏度矩阵数值
- 必要的元信息，如子工况、单位模式、归一化状态、来源文件

## 5. 模块职责划分

建议新增一个独立服务模块：

- `services/model_update/importers/op2_service.py`

它只负责三件事：

1. 打开并读取 `OP2`
2. 提取并整理结果
3. 输出统一 Python 结构

它先不负责：

- 直接写数据库
- 直接做模态相关性计算
- 直接触发求解

### 为什么要这样切

因为 `OP2` 解析本身就已经足够复杂。  
如果一开始就把“解析 + 写库 + 比对 + 接口”全缠在一起，后面一旦有一种 `OP2` 样本读不通，排错会非常费劲。

建议层次是：

- `op2_service.py`：只负责“读”
- 上层 import/service：负责“写”
- 上层业务流程：负责“比”

## 6. `OP2` 读取入口设计

第一阶段建议提供两种读取模式。

### 6.1 `op2_only`

输入：

- `op2_path`

用途：

- 当 `OP2` 本身带有几何时，直接读几何和结果

优点：

- 调用简单

风险：

- 不是所有 `OP2` 都带完整几何

### 6.2 `bdf_plus_op2`

输入：

- `bdf_path`
- `op2_path`

用途：

- 当 `OP2` 缺几何，或几何不完整时，用 `BDF` 提供节点和单元基准

优点：

- 更稳
- 更适合后面做节点对齐、模态入库、响应映射

第一阶段建议默认优先支持这种模式。

## 7. 对 `pyNastran` 的使用建议

第一阶段建议沿用当前脚本思路：

```python
from pyNastran.op2.op2_geom import read_op2_geom
op2 = read_op2_geom(op2_path, debug=False)
```

### 为什么先沿用这个入口

因为当前仓库里的 [Op2Reader.py](/D:/WorkSpace/OtherProjects/VirtualReal702/Op2Reader.py) 已经基于这个入口验证过模态读取是可行的。

第一阶段应优先复用已验证路线，而不是上来改成另一套读取接口。

## 8. 模态结果解析设计

这里对应的是第一阶段的第一条结果链路，也是 `OP2` 解析层优先级最高的部分。

## 8.1 目标结果结构

建议内部统一输出成下面这种结构：

```python
{
  "source": {
    "op2_path": "D:/demo/result.op2",
    "bdf_path": "D:/demo/model.bdf",
    "mode": "bdf_plus_op2"
  },
  "subcases": [
    {
      "subcase_id": 1,
      "modes": [
        {
          "mode_no": 1,
          "frequency": 12.34,
          "eigenvalue": 6012.0,
          "node_count": 12345,
          "nodes": [
            {
              "node_id": 1001,
              "u1": 0.001,
              "u2": -0.002,
              "u3": 0.003,
              "ur1": 0.0,
              "ur2": 0.0,
              "ur3": 0.0
            }
          ],
          "extra_json": {}
        }
      ]
    }
  ]
}
```

这个结构有两个特点：

1. 足够完整，便于后面转入数据库
2. 不强依赖具体前端显示格式

## 8.2 结果来源字段

从 [Op2Reader.py](/D:/WorkSpace/OtherProjects/VirtualReal702/Op2Reader.py) 可见，当前模态结果主要来自：

- `op2.eigenvectors`
- `eigen_data.data`
- `eigen_data.modes`
- `eigen_data.node_gridtype`
- 可选 `eigen_data.eigns`

建议第一阶段按这个假设实现。

## 8.3 频率读取规则

建议优先顺序：

1. 如果结果对象直接给频率字段，优先直接取
2. 否则尝试从 `eigns` 推导：

```text
frequency = sqrt(eigenvalue) / (2 * pi)
```

3. 如果上述都拿不到，允许频率为空，但要记 warning

### 为什么允许为空

因为有些结果文件里频率元信息不一定完整。  
模态振型本身可能已经能读出来，如果这时直接整个失败，会让流程太脆。

## 8.4 节点对齐规则

这是实现里的关键点。

不能假设：

- `BDF` 节点顺序
- `OP2` 几何节点顺序
- `eigenvectors` 结果节点顺序

三者天然一致。

必须按节点 ID 做映射。

建议固定流程：

1. 取得基准节点序列
   - 优先来自 `BDF`
   - 若无 `BDF`，则来自 `OP2` 几何
2. 从 `eigen_data.node_gridtype[:, 0]` 取结果节点 ID
3. 建 `node_id -> result_index`
4. 遍历基准节点序列，把结果映射回统一顺序

这一步是后续和试验模态做 `DOF` 匹配的前提。

## 8.5 几何缺失处理

并不是所有 `OP2` 都带完整几何。

所以解析器需要明确处理三种情况：

### 情况 A：`OP2` 自带完整几何

直接用 `OP2` 几何。

### 情况 B：`OP2` 没有完整几何，但提供了结果节点号

用 `BDF` 补几何，用 `OP2` 补结果。

### 情况 C：`OP2` 连结果节点对应关系都不完整

直接报错，并返回结构化错误信息。

建议错误信息至少包含：

- `op2_path`
- 当前 subcase
- 当前 mode
- 缺失节点数量

## 9. 模态结果与现有数据库的衔接

当前项目已有 `t_mt_py_fem_modal_result`，字段核心是：

- `mode_no`
- `frequency`
- `instance_name`
- `part_name`
- `fem_node_label`
- `u1/u2/u3`
- `extra_json`

对应导入函数是 [inp_service.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/analysis/inp_service.py) 里的 `import_fe_modal_results()`。

### 因此建议

第一阶段不要新建 Nastran 专用模态表，而是把 `OP2` 解析结果整理成这个函数已经能吃的 payload 结构。

建议中间落地结构直接兼容：

```python
[
  {
    "mode_no": 1,
    "frequency": 12.34,
    "nodes": [
      {
        "instance_name": "PART-1-1",
        "part_name": "PART-1",
        "fem_node_label": 1001,
        "u1": 0.001,
        "u2": 0.002,
        "u3": -0.001,
        "extra_json": {
          "node_id": 1001,
          "ur1": 0.0,
          "ur2": 0.0,
          "ur3": 0.0
        }
      }
    ]
  }
]
```

### 为什么这样做

因为后续：

- `match_dofs`
- `build_fe_response_catalog`
- `DAC / DSF`
- 未来 `MAC`

都已经是围绕 `t_mt_py_fem_modal_result` 在组织的。  
复用它，比另建一套 Nastran 模态通道更稳。

## 10. 灵敏度矩阵解析设计

这里对应的是第一阶段的第二条结果链路。  
它依赖前面的模态分析模型和参数选择已经明确，因此不建议在模态解析之前抢先实现。

这是这份文档里最需要提前说清楚的部分。

## 10.1 第一阶段的现实判断

和模态振型不一样，灵敏度矩阵的最大难点不是“数值在哪”，而是：

- 它在 `OP2` 中到底以哪种结果块形式出现
- 行和列分别对应哪些响应、哪些参数
- 是否是原始导数
- 是否已经归一化

也就是说，第一阶段设计上必须允许“先拿到矩阵和元信息，再做业务映射确认”。

## 10.2 第一阶段目标

第一阶段建议把灵敏度解析目标拆成两层。

### 层 1：原始提取层

负责：

- 识别 `OP2` 中可用的灵敏度结果块
- 把原始矩阵读成二维数组
- 把原始行列标签尽可能读出来

### 层 2：业务映射层

负责：

- 把原始行映射为响应
- 把原始列映射为参数
- 整理成当前系统的 `response_def / parameter_def / sensitivity_result` 结构

### 为什么必须分两层

因为很多时候 `OP2` 能把矩阵数值给你，但不一定直接给出“这是 MODE_3_FREQ 对 E1 的导数”这种业务语义。  
如果不分层，代码会很容易把“原始格式解析”和“业务意义判断”搅在一起。

## 10.3 灵敏度矩阵中间结构建议

建议统一成：

```python
{
  "source": {
    "op2_path": "D:/demo/result.op2",
    "subcase_id": 1
  },
  "matrix_kind": "raw_sensitivity",
  "value_mode": "raw",
  "row_labels": ["RESP_1", "RESP_2"],
  "column_labels": ["PARAM_1", "PARAM_2", "PARAM_3"],
  "matrix": [
    [0.12, -0.04, 0.003],
    [0.08, -0.03, 0.001]
  ],
  "warnings": []
}
```

后续业务映射完成后，再转成：

```python
{
  "response_names": ["MODE_1_FREQ", "MODE_2_FREQ"],
  "parameter_names": ["T1", "E1", "RHO1"],
  "matrix": [
    [0.12, -0.04, 0.003],
    [0.08, -0.03, 0.001]
  ],
  "value_mode": "raw_or_normalized"
}
```

## 10.4 与现有灵敏度数据库结构的衔接

当前项目已有的灵敏度存储结构是：

- `t_mt_py_fem_analysis_run`
- `t_mt_py_fem_sensitivity_matrix_response`
- `t_mt_py_fem_sensitivity_matrix_parameter`
- `t_mt_py_fem_sensitivity_matrix_result`

这套结构非常适合继续复用，因为它本来就是“矩阵分表存储”的模式：

- `response_def` 存行定义
- `parameter_def` 存列定义
- `sensitivity_result` 存矩阵点值

### 因此建议

Nastran 灵敏度矩阵一旦成功映射成：

- `response_names`
- `parameter_names`
- `matrix`

就直接复用当前 Abaqus 那套写库方式，而不是另建 Nastran 专用灵敏度表。

### 为什么复用是好事

因为后面的：

- 贝叶斯迭代
- 灵敏度矩阵查询
- 点表展示
- 参数曲线
- 响应曲线

本来都是围绕这套表做的。  
只要 Nastran 数据能落成同结构，后面很多能力就能白拿。

## 11. 归一化问题

这是灵敏度矩阵设计里必须明确的一点。

当前系统里对 Abaqus 灵敏度矩阵大量使用的是“归一化灵敏度矩阵”。

所以对 Nastran `OP2` 来说，第一阶段要明确区分两种值：

1. 原始灵敏度值
2. 归一化灵敏度值

建议解析输出里必须带：

- `value_mode = raw | normalized | unknown`

### 为什么要显式标识

因为如果后续把“原始值”误当“归一化值”去喂给贝叶斯更新，数值量级会完全失真。

第一阶段如果无法百分百确认，就宁可标成 `unknown`，也不要假装知道。

## 12. 模态结果和试验模态比对的接线方式

这里再把链路说得更具体一点。

### 12.1 Nastran 侧负责什么

Nastran `OP2` 解析层负责产出：

- 每阶模态频率
- 每个 FEM 节点的模态位移向量

### 12.2 当前系统负责什么

当前系统已有能力负责：

- 试验测点和 FEM 节点的空间匹配
- 试验通道方向和 FEM 位移方向的 `DOF` 匹配
- 将 FEM 模态向量沿试验方向投影
- 计算 `DAC / DSF`

### 12.3 未来 MAC 放在哪里

建议未来 `MAC` 也放在这条后处理链里，而不是放在 `OP2` 解析层。

原因很简单：

`OP2` 解析层只知道 FEM 结果；  
`MAC` 需要 FEM 模态和试验模态两边都准备好之后才有意义。

## 13. 建议提供的服务函数

为了后面实现更清楚，建议 `op2_service.py` 第一阶段就拆成这几个函数。

### 13.1 基础读取

- `load_op2(op2_path)`
- `load_bdf_and_op2(bdf_path, op2_path)`

### 13.2 模态提取

- `extract_modal_subcases(...)`
- `extract_mode_shape(...)`
- `build_modal_payload(...)`

### 13.3 灵敏度提取

- `extract_raw_sensitivity_blocks(...)`
- `build_sensitivity_preview(...)`
- `map_sensitivity_to_business_names(...)`

### 13.4 结果桥接

- `build_modal_import_payload(...)`
- `build_sensitivity_store_payload(...)`

### 为什么拆成这些函数

这样每一步都能单独调试：

- 是文件没读开
- 是模态没找到
- 是节点对不齐
- 还是灵敏度块解析不了

后面一眼就能定位。

## 14. 建议提供的接口草案

第一阶段先不一定马上全实现，但建议接口先按这个方向设计。

### 14.1 模态预览

`POST /import/op2/modal/preview`

作用：

- 读取 `OP2`
- 返回模态摘要和少量节点预览

### 14.2 模态导入

`POST /import/op2/modal/store`

作用：

- 读取 `OP2`
- 整理成 `t_mt_py_fem_modal_result` 可接受结构
- 写入数据库

### 14.3 灵敏度矩阵预览

`POST /import/op2/sensitivity/preview`

作用：

- 读取 `OP2`
- 返回原始矩阵预览、标签预览、warning

### 14.4 灵敏度矩阵入库

`POST /import/op2/sensitivity/store`

作用：

- 把已确认映射关系的灵敏度矩阵写入现有灵敏度表结构

## 15. 第一阶段 warning 设计

由于 `OP2` 结果样本可能差异很大，建议预览接口从第一天开始就支持 `warnings`。

建议 warning 场景包括：

- `OP2_GEOMETRY_MISSING`
- `MODE_FREQUENCY_MISSING`
- `MODE_NODE_MAPPING_INCOMPLETE`
- `SENSITIVITY_BLOCK_NOT_FOUND`
- `SENSITIVITY_LABELS_INCOMPLETE`
- `SENSITIVITY_VALUE_MODE_UNKNOWN`

### 为什么 warning 很重要

因为这类问题很多时候不是“完全失败”，而是“能读，但有不确定性”。  
如果没有 warning，后面很容易把半正确数据当全正确数据用下去。

## 16. 第一阶段实现边界

> 顺序修正说明  
> 这一章里的实现边界，建议按如下顺序理解：
> 1. 先稳定读取 `SOL103` 模态结果
> 2. 先输出模态 JSON 预览
> 3. 先整理成当前 FEM 模态入库结构
> 4. 再读取 `SOL200` 灵敏度矩阵原始预览
> 5. 最后桥接到现有灵敏度表结构

第一阶段建议做到：

1. 能稳定读取模态结果
2. 能输出可供人工检查的模态 JSON 预览
3. 能整理成当前 FEM 模态入库结构
4. 能提取灵敏度矩阵原始预览
5. 能把灵敏度矩阵桥接到现有灵敏度表结构

先不要求：

- 一次性支持所有 `OP2` 变体
- 一次性支持所有灵敏度块类型
- 一次性完成自动归一化
- 一次性完成 MAC 计算

## 17. 与前三份设计稿的关系

这份文档是前三份设计中的“结果读取层”落地说明：

- [Nastran-Python-Workflow-Design.md](/D:/WorkSpace/OtherProjects/VirtualReal702/docs/model_update/Nastran-Python-Workflow-Design.md) 讲整体链路
- [Nastran-SOL200-Field-Mapping-Design.md](/D:/WorkSpace/OtherProjects/VirtualReal702/docs/model_update/Nastran-SOL200-Field-Mapping-Design.md) 讲 `SOL200` 卡片生成
- 本文档讲 `OP2` 解析和结果桥接

三份合起来，基本就把后续 Nastran Python 化的主骨架搭清楚了。
