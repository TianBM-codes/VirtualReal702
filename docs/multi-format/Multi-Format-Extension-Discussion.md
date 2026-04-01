# Multi-Format Extension Discussion

> 本文档记录扩展支持 BDF / INP / CDB 格式的讨论过程，供后续决策参考。
> 开始时间：2026-03-28

---

## 背景

当前服务仅支持 Abaqus ODB 格式（结果文件，包含网格 + 计算结果）。
同事提出扩展支持以下格式：

| 格式 | 求解器 | 类型 | 配套结果文件 |
|---|---|---|---|
| BDF | MSC/NX Nastran | 输入文件（前处理） | .OP2 |
| INP | Abaqus | 输入文件（前处理） | .ODB |
| CDB | ANSYS MAPDL | 数据库文件 | .RST |

**关键区别**：ODB 是后处理文件，自带结果；BDF/INP/CDB 是前处理文件，本身只有网格，结果在单独文件里。

---

## 需求确认（2026-03-28）

### BDF
- 已用 pyNastran 完成解析
- 需求：展示有限元网格、支持不同 set 集合的单元显示、点击选择创建 set 等基本操作
- **当前阶段：纯几何，不涉及结果**

### INP
- 现有解析程序功能有限：仅支持单个 instance、单个 part，能解析节点、单元、材料、工况、属性、约束
- 目标：
  - 支持多 part、多 instance
  - 支持 assembly
  - 支持 surface 工况解析
  - 支持时间历程载荷（Amplitude）解析
- **第一步：设计标准化的 INP 解析框架**

### CDB
- 暂无具体讨论，待后续

---

## 架构影响分析

### 改动量总结

| 层 | 改动量 | 说明 |
|---|---|---|
| **L1** | 大 | 现有 L1 强依赖 Abaqus Python API；BDF/INP/CDB 需要各自独立的解析器，但输出 HDF5 schema 需保持一致 |
| **L2** | 小 | 只要 L1 输出 schema 不变，L2 几乎不用动；需要一个单元类型名称跨求解器的映射层（如 CQUAD4 → S4R） |
| **L3** | 小 | 读 L2 HDF5，格式无关；需增加"无结果模式"分支（geometry-only） |
| **manifest.db** | 极小 | 加一列 `source_format` |

**核心结论**：架构天然支持扩展，代价集中在 L1 解析器。L2/L3 对格式无感知，只要 L1 输出 schema 对齐即可复用。

### L1 输出与 INP 概念的映射

| INP 概念 | L1 HDF5 对应 |
|---|---|
| Part 节点/单元 | `geometry/<inst>.h5` |
| Instance + 坐标变换 | `assembly.h5` transforms |
| Nset / Elset | `sets/sets.h5` |
| Step / 载荷 / Amplitude | 无直接对应（前处理，无结果） |

---

## INP 解析框架设计

### INP 文件结构概述

```
*Part, name=PART-1
  *Node / *Element / *Nset / *Elset / *Surface / *Section
*End Part

*Assembly, name=Assembly
  *Instance, name=PART-1-1, part=PART-1
    （可选：局部节点覆盖 + 坐标变换）
  *End Instance
  *Nset / *Elset / *Surface    ← assembly 级别集合
  *Tie / *Coupling / ...       ← 约束
*End Assembly

*Material, name=STEEL
*Amplitude, name=AMP-1        ← 时间历程曲线

*Step, name=Step-1
  *Static / *Dynamic / *Frequency
  *Cload / *Dload / *Pressure / *Boundary
  *Output
*End Step
```

### 主要复杂点

1. **Set 嵌套**：`*Elset` 内可引用其他 elset 名称，需递归展开
2. **两级 Set 域**：Part 级别和 Assembly 级别同名但不同域，需区分
3. **Include 文件**：`*Include, input=other.inp` 需递归展开处理
4. **Instance 变换**：节点坐标在组装时才做平移 + 旋转，Part 内坐标是局部坐标
5. **Surface 展开**：最终需解析到 `(elem_label, face_id)` 列表

### 推荐框架：三层设计

**第一层：Lexer（关键字块切分）**
- 按 `*Keyword` 切块，每块包含：关键字名、参数字典、数据行列表
- 处理 `**` 注释、续行符、`*Include` 递归展开
- 输出：扁平的 `[KeywordBlock]` 列表

**第二层：Parser（关键字分发）**
- 关键字注册表：`{"NODE": NodeHandler, "ELEMENT": ElementHandler, ...}`
- 遍历块列表，维护当前上下文（当前处于哪个 Part / Assembly / Step）
- 输出：原始文档模型（引用尚未解析，set 内容可能仍是名称字符串）

**第三层：Resolver（引用解析 + 组装）**
- Instance → 查找对应 Part，应用坐标变换
- Set 嵌套展开
- Surface → 展开为 `(elem_label, face_id)` 列表
- Amplitude → 插值曲线对象，供 Step 载荷引用

### 建议实现顺序

**第一阶段（对齐 L2 所需最小集）**
- Lexer + Include 展开
- Node、Element、Nset、Elset
- Part、Assembly、Instance（含坐标变换）

完成后即可接 L2，实现多 part 多 instance 网格可视化。

**第二阶段**
- Surface 解析
- Material、Section
- Constraint（Tie、Coupling）

**第三阶段**
- Step 解析
- Amplitude（时间历程）
- Cload、Dload、Pressure、Boundary

---

## 待确认

- [ ] 同事现有 INP 解析程序的结构（一遍扫描 or 已有关键字分发雏形）→ 决定是扩展还是重写
- [ ] CDB 格式的具体需求（是否也只需要几何，还是需要结果）
- [ ] BDF 的 set 操作是否需要回写（创建的 set 要不要保存回文件）
