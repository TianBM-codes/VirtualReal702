# Nastran SOL200 字段映射设计稿

## 1. 文档目的

> 顺序修正说明  
> 本文档描述的是第一阶段中的“第二条主线”。  
> 第一条主线应先完成：`SOL103` 求解、`OP2` 模态解析、模态匹配与相关性。  
> 然后才进入本文档讨论的 `SOL103 -> SOL200` 改写与灵敏度配置。

本文档专门回答一个更具体的问题：

**怎样把 Python 侧的“参数 / 响应 / 模态 / 灵敏度”业务对象，映射成 `SOL200` 需要的 Nastran 卡片。**

这里重点参考：

- [femtools_bas/nastran.bas](/D:/WorkSpace/OtherProjects/VirtualReal702/femtools_bas/nastran.bas)
- [femtools_bas/nastranopt.bas](/D:/WorkSpace/OtherProjects/VirtualReal702/femtools_bas/nastranopt.bas)

目标不是逐字符复制 BAS，而是保留它的业务语义。

## 2. BAS 里已经明确的 SOL200 语义

从 BAS 可读出的主流程是：

1. 把分析类型改成 `SOL 200`
2. 写 `METHOD / DESSUB / DSAPRT`
3. 写模态分析子工况 `SUBCASE 1 / ANALYSIS=MODES`
4. 写 `EIGRL`
5. 写设计变量 `DESVAR`
6. 写设计变量到属性/材料的关联 `DVPREL1 / DVMREL1`
7. 写设计响应 `DRESP1`
8. 写设计约束 `DCONSTR`

其中：

- `DESVAR` 决定“改谁”
- `DVPREL1 / DVMREL1` 决定“怎么挂到模型上”
- `DRESP1` 决定“看哪个响应”
- `DSAPRT` 决定“跑完灵敏度就导出并结束”

## 3. 第一阶段支持范围

这里的输入 `BDF`，建议统一理解成：

- 一份已经验证可用于模态分析的输入文件
- 最常见来源就是当前正在使用的 `SOL103` 模型
- `SOL200` 生成器是在这份模型基础上做“灵敏度分析改写”，不是第一步就独立生成一份全新分析模型

建议第一阶段只支持下面这些设计变量类型：

- `H`
- `E`
- `RHO`

原因：

- 它们已经在 BAS 里有清晰映射
- 最符合当前模型修正需求
- 对 `pyNastran` 来说比较稳

后续第二阶段再支持：

- `AX`
- `IX`
- `IY`
- `IZ`

## 4. SOL200 控制段建议

建议第一阶段生成器固定写出下列控制骨架：

```text
SOL 200
CEND
METHOD = 1
DISPLACEMENT = ALL
DESSUB = 1
DSAPRT(NOPRINT,EXPORT,END=SENS)

SUBCASE 1
  ANALYSIS = MODES

BEGIN BULK
PARAM   POST     -5
PARAM   GRDPNT    0
PARAM   K6ROT   10.0
PARAM   COUPMASS -1
EIGRL   ...
```

### 为什么沿用这套骨架

因为 BAS 已经验证过这套写法适合“模态灵敏度 + 输出”场景。  
第一阶段不应该自己发明新的控制组合，否则很容易出现“卡片语法没错，但求解器输出不是我们想要的结果”的问题。

## 5. 参数中间模型

为了避免生成器直接依赖数据库细节，建议先在 Python 内部统一成中间结构。

建议结构：

```python
{
  "parameters": [
    {
      "name": "T1",
      "type": "H",
      "target_type": "property",
      "property_type": "PSHELL",
      "property_id": 101,
      "material_id": None,
      "initial": 2.0,
      "lower": 1.5,
      "upper": 2.5
    },
    {
      "name": "E1",
      "type": "E",
      "target_type": "material",
      "property_type": None,
      "property_id": None,
      "material_type": "MAT1",
      "material_id": 201,
      "initial": 210000.0,
      "lower": 180000.0,
      "upper": 230000.0
    }
  ]
}
```

生成器只依赖这个结构，不直接关心这些值最初是从数据库、接口还是脚本来的。

## 6. 设计变量映射规则

### 6.1 厚度 `H`

#### BAS 语义

- `DESVAR`
- `DVPREL1`
- `PTYPE = PSHELL`
- `FID = 4`

#### Python 映射

当参数类型是 `H` 时：

- 生成一条 `DESVAR`
- 生成一条 `DVPREL1`
- 目标属性类型必须是 `PSHELL`

示意：

```text
DESVAR   1   T1   2.0   1.5   2.5
DVPREL1  1   PSHELL   101   4
         1   1.0
```

#### 业务解释

这表示：  
“把设计变量 `T1` 挂到 `PSHELL 101` 的厚度字段上。”

### 6.2 弹性模量 `E`

#### BAS 语义

- `DESVAR`
- `DVMREL1`
- `PTYPE = MAT1`
- `MPNAME = E`

#### Python 映射

当参数类型是 `E` 时：

- 生成一条 `DESVAR`
- 生成一条 `DVMREL1`
- 目标材料类型必须是 `MAT1`

示意：

```text
DESVAR   2   E1   2.10E+05   1.80E+05   2.30E+05
DVMREL1  2   MAT1   201   E
         2   1.0
```

#### 业务解释

这表示：  
“把设计变量 `E1` 挂到 `MAT1 201` 的杨氏模量上。”

### 6.3 密度 `RHO`

#### BAS 语义

- `DESVAR`
- `DVMREL1`
- `PTYPE = MAT1`
- `MPNAME = RHO`

#### Python 映射

```text
DESVAR   3   RHO1   7.85E-09   ...
DVMREL1  3   MAT1   201   RHO
         3   1.0
```

#### 业务解释

这表示：  
“把设计变量 `RHO1` 挂到 `MAT1 201` 的密度上。”

## 7. 参数解析与追溯规则

这是生成器最容易出错的地方。

### 7.1 对 `H`

要能回答：

- 这个参数对应哪个 `PSHELL`

常见来源有两种：

1. 参数已经直接指定 `property_id`
2. 参数先指向单元/单元集合，再通过单元追溯到 `PID`

第一阶段建议优先支持第一种，原因是最稳。

### 7.2 对 `E / RHO`

要能回答：

- 这个参数对应哪个 `MAT1`

来源同样有两种：

1. 参数直接指定 `material_id`
2. 参数先指向属性/单元，再追溯到 `MID`

第一阶段也建议优先支持直接给 `material_id`。

### 为什么第一阶段偏向“直接目标”

因为 `BDF` 里真实模型经常存在：

- 同一组单元挂多个属性
- 同一属性引用多个材料
- 不同单元类型混用

如果第一版就允许“自动追溯并自动分裂参数”，行为会很难解释。  
先要求上游把目标说清楚，更稳。

## 8. 响应映射规则

### 8.1 第一阶段响应：模态频率 `FREQ`

#### BAS 语义

`DRESP1`

- `RTYPE = FREQ`
- `ATTA = mode_number`

示意：

```text
DRESP1   1   FREQ_MODE_1   FREQ           1
```

#### Python 中间结构建议

```python
{
  "responses": [
    {
      "name": "FREQ_MODE_1",
      "type": "FREQ",
      "mode_number": 1
    }
  ]
}
```

### 8.2 为什么第一阶段不把 MAC 写进 DRESP1

因为 `MAC` 不是普通的求解器内建物理响应，它依赖：

- 试验模态
- FEM 模态
- 空间和自由度匹配

所以它更适合作为 Python 后处理指标，而不是 `SOL200` 内的 `DRESP1`。

结论：

- `FREQ` 放在 Nastran 内
- `MAC` 放在 Python 结果后处理层

## 9. DCONSTR 映射规则

第一阶段可直接沿用 BAS 的简单策略：

- 所有 `DRESP1` 都挂到 `DESSUB = 1`
- 每个响应生成一条 `DCONSTR`

示意：

```text
DCONSTR  1   1   1E30   1E30
DCONSTR  1   2   1E30   1E30
```

### 为什么可以先这么做

因为当前目标不是正式优化求最优解，而是为了输出灵敏度矩阵。  
先统一挂在一个设计子工况上，足够支撑第一阶段。

## 10. EIGRL 配置建议

因为第一阶段主打模态灵敏度，`EIGRL` 是必须的。

建议保留当前 `SOL103` 生成器已有参数风格：

- `dynamic.fmin`
- `dynamic.fmax`
- `dynamic.vectors`
- `dynamic.norm`
- `dynamic.size`
- `compute.lumped`
- `fem.k6rot`

这组参数已经在 [services/model_update/solver_prep/nastran_sol103.py](/D:/WorkSpace/OtherProjects/VirtualReal702/services/model_update/solver_prep/nastran_sol103.py) 中有雏形，可以复用。

## 11. OP2 模态结果输出结构建议

为了后面能直接接到现有模态比对链路，建议解析层输出统一结构：

```python
{
  "modes": [
    {
      "subcase_id": 1,
      "mode_number": 1,
      "frequency_hz": 12.34,
      "eigenvalue": 6012.0,
      "vectors": [
        {
          "node_id": 1001,
          "tx": 0.01,
          "ty": 0.02,
          "tz": -0.03,
          "rx": 0.0,
          "ry": 0.0,
          "rz": 0.0
        }
      ]
    }
  ]
}
```

关键点：

- `node_id` 必须保留
- 不能假设 OP2 里的结果顺序和 BDF 节点顺序一致
- 必须用节点 ID 做映射

## 12. OP2 灵敏度矩阵输出结构建议

第一阶段建议先输出统一 JSON 预览结构：

```python
{
  "parameters": [
    {"id": 1, "name": "T1", "type": "H"},
    {"id": 2, "name": "E1", "type": "E"}
  ],
  "responses": [
    {"id": 1, "name": "FREQ_MODE_1", "type": "FREQ", "mode_number": 1},
    {"id": 2, "name": "FREQ_MODE_2", "type": "FREQ", "mode_number": 2}
  ],
  "matrix": [
    [0.12, -0.04],
    [0.08, -0.03]
  ],
  "extra": {
    "subcase_id": 1,
    "value_mode": "raw"
  }
}
```

### 为什么先做 JSON 预览

因为 `OP2` 灵敏度块解析最容易出“数值读到了，但语义列对不上”的问题。  
先有预览，就能先人工确认：

- 行顺序对不对
- 列顺序对不对
- 模态序号对不对
- 矩阵值量级对不对

## 13. 与现有试验模态比对链路的衔接

后续接入时，不建议单独为 Nastran 写一套模态比对算法。

更稳的策略是：

1. 把 `OP2` 模态结果整理成当前 `t_mt_py_fem_modal_result` 能接受的结构
2. 复用现有：
   - 节点匹配
   - 自由度匹配
   - `DAC / DSF`
   - 后续 `MAC`

### 后续如果要加 MAC

建议放在模态后处理层，和 `DAC / DSF` 同一层。

推荐思路：

- 先完成 `test_mode_vector` 与 `fem_mode_vector` 的同测点、同方向对齐
- 再计算：

```text
MAC = |phi_test^H * phi_fem|^2 / ((phi_test^H * phi_test) * (phi_fem^H * phi_fem))
```

这样 MAC 才有业务意义。

## 14. 第一阶段接口需要的输入

如果要做 `POST /solver/nastran/sol200/generate`，建议最小输入结构如下：

```json
{
  "input_bdf": "D:/demo/model.bdf",
  "output_bdf": "D:/demo/model_sol200.bdf",
  "parameters": [
    {
      "name": "T1",
      "type": "H",
      "property_id": 101,
      "initial": 2.0,
      "lower": 1.5,
      "upper": 2.5
    },
    {
      "name": "E1",
      "type": "E",
      "material_id": 201,
      "initial": 210000.0,
      "lower": 180000.0,
      "upper": 230000.0
    }
  ],
  "responses": [
    {
      "name": "FREQ_MODE_1",
      "type": "FREQ",
      "mode_number": 1
    }
  ],
  "settings": {
    "dynamic.vectors": 10,
    "dynamic.fmin": 0.0,
    "dynamic.fmax": 1000.0
  }
}
```

## 15. 第一阶段边界和取舍

> 顺序修正说明  
> 第一阶段在 `SOL200` 侧的目标，不是抢在模态链路之前落地；而是在模态链路已经跑通之后，补上参数映射、响应映射和灵敏度求解能力。

第一阶段建议做这些取舍。

### 做

- `H / E / RHO`
- `FREQ`
- 模态振型解析
- 灵敏度矩阵预览

### 不做

- 所有 `DRESP1` 类型一次性支持
- 所有梁属性参数一次性支持
- 把 `MAC` 硬塞进 `SOL200`
- 一上来就做数据库全链路自动入库

### 为什么这样取舍

因为这一阶段最重要的是：

**先验证 Python 版 `SOL200 -> Nastran -> OP2 -> 模态/灵敏度` 这条主链路成立。**

链路一旦通了，后续扩展参数类型和响应类型就会轻松很多。
