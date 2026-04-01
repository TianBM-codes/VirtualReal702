# INP Parser 设计补充 Review（面向闭环的通用策略）

> 目标：补充 `INP-Parser-Design.md` 中尚未完全闭环的“通用 INP 解析策略”部分。  
> 结论先行：现有设计文档已经是一个很好的工程起点，但距离“闭环的通用策略”还差 5 个关键环节：**标准输出、作用域规则、引用解析、Step 语义、诊断体系**。

---

## 一、总体评价

`INP-Parser-Design.md` 已经完成了两件重要工作：

1. 把现有 parser 的真实问题讲清楚了  
2. 把主要关键字族（Part / Assembly / Set / Surface / Step / Material / Load / BC）覆盖到了

因此，这份文档已经具备“重构/重写 parser 的设计基础”。

但如果目标提升为：

> **做一套真正可长期扩展、可接入现有三层架构的通用 INP 解析策略**

那么当前文档还没有完全闭环。

---

## 二、核心补充结论

### 1. 先定义“标准输出”，再谈解析覆盖

当前文档对“支持哪些关键字”写得很多，但对“最终输出成什么标准模型”写得不够硬。

通用解析器必须先回答：

- 最终内存模型长什么样
- 最终落盘 schema 长什么样
- 如何对齐现有 `L1/L2/L3` 架构

否则 parser 即使能读很多关键字，也可能无法稳定接到现有 pipeline。

**建议补充一个统一输出目标：**

```text
INP Source
  -> InpModel (语法级)
  -> ResolvedModel (语义级)
  -> Canonical FEM Exchange Model (标准交换模型)
  -> HDF5 + manifest.db
```

其中 `Canonical FEM Exchange Model` 才是“闭环”的关键。  
它应成为：

- INP 解析输出
- 后续 BDF / CDB 解析输出
- L2 ingest 的统一输入

换句话说，**不要让 INP parser 直接绑定 Abaqus 现有代码细节，而要绑定一个格式无关的中间标准模型。**

---

### 2. 必须把“作用域规则”提升为一级设计对象

INP 解析真正难的地方，不是 `*Node` / `*Element` 本身，而是**名字和引用的作用域**。

至少应明确以下规则：

- Part 级 `Nset/Elset/Surface`
- Assembly 级 `Nset/Elset/Surface`
- `instance=` 参数指向的上下文
- `instance_name.set_name` 的显式引用
- 同名 set 多次定义时的并集语义
- Part/Assembly 同名对象是否允许共存，如何解析优先级

建议在设计上显式引入：

```text
Scope = ROOT | PART(name) | INSTANCE(name) | ASSEMBLY | STEP(name) | MATERIAL(name)
QualifiedName = (scope, local_name)
```

所有命名对象都先保存为 `QualifiedName`，不要在 parser 阶段偷懒塞进全局 dict。

---

### 3. “引用解析”必须单独建模，不能只是 Resolver 里的杂项

INP 里的大量对象都不是当场可用的，而是要等后续统一解析：

- Elset 嵌套 Elset
- Surface 引用 Elset / Nset
- Section 引用 Material / Set
- Instance 引用 Part
- Step 里的 Load / BC 引用 Set / Amplitude

因此 Resolver 不是“收尾逻辑”，而是 parser 的半个主体。

建议把引用问题拆成三类：

1. **Name Reference**
   - `set_name`
   - `material_name`
   - `instance_name`

2. **Scoped Reference**
   - `instance=set`
   - `PART-1-1.Set-Top`

3. **Semantic Reference**
   - `Surface -> [(elem, face_id)]`
   - `Boundary -> [(target, dof_range, value, amplitude)]`

并明确：

- Parser 阶段只建“引用”
- Resolver 阶段才做“展开”
- Validation 阶段专门检查“引用是否可解”

---

### 4. Step 语义必须按“激活模型”设计，而不是按关键字清单设计

当前文档已经列了很多 Step / Load / Boundary 类型，但“闭环策略”还缺一个核心抽象：

> **Step 不是一个关键字容器，而是一个激活/覆盖语义容器。**

需要明确：

- Step 间边界条件是继承、累积还是覆盖
- `op=NEW` 的真正行为
- Load/BC 是否按 step 生效
- Amplitude 如何绑定到 Step 内对象
- 解析器是保留“原始声明”，还是生成“每一步的生效状态”

建议定义两层：

```text
StepDeclaration   # 输入文件里的原始声明
StepResolvedState # 经过继承/覆盖规则展开后的生效状态
```

这样后续无论做可视化、导出，还是转下游 schema，都不会反复重算 step 语义。

---

### 5. 诊断体系必须单独成层

当前文档主要在讲“支持什么”，但通用 parser 的另一半价值是：

> **当不支持、写错、引用缺失时，能不能稳定地解释问题。**

建议单独引入 `Diagnostics`：

```text
Diagnostic
- severity: INFO | WARNING | ERROR
- code: e.g. UNKNOWN_KEYWORD / UNRESOLVED_SET / DUPLICATE_NAME
- message
- source_location: file, line, include_stack
- context: part / assembly / step / material
```

至少要覆盖：

- 未知关键字
- 不支持的关键字参数组合
- 引用缺失
- Include 循环
- 同名冲突
- 非法面号 / 非法 DOF
- 不支持的单元类型

如果没有这一层，解析器在工程上就很难“通用”。

---

## 三、建议补充到原设计文档的策略段落

建议在 `INP-Parser-Design.md` 后续补一个“闭环策略”章节，至少包含以下 5 个子节：

1. **Canonical Output Model**
   - 统一标准模型
   - 与 HDF5 / manifest.db 的映射

2. **Scope & Name Resolution Rules**
   - Part / Assembly / Instance / Step / Material 的作用域规则

3. **Reference Resolution Pipeline**
   - 什么阶段保留引用
   - 什么阶段展开引用

4. **Step Activation Semantics**
   - 继承 / 覆盖 / `op=NEW` / amplitude 绑定

5. **Diagnostics & Recovery Policy**
   - 哪些错误中止
   - 哪些警告继续
   - 如何记录 source location

---

## 四、推荐的闭环版本解析流水线

建议把当前三层结构扩成四层：

```text
Layer 1: Lexer
- Include 展开
- 关键字块切分
- 保留 source location

Layer 2: Parser
- 只构建语法级对象
- 不急于做跨域引用展开

Layer 3: Resolver
- 作用域解析
- Set / Surface / Instance / Step 引用展开
- 构建语义级模型

Layer 4: Validator + Exporter
- 生成 Diagnostics
- 输出 Canonical FEM Exchange Model
- 再映射到 HDF5 + manifest.db
```

这里的关键变化是：

- `Resolver` 负责“让模型可用”
- `Validator/Exporter` 负责“让模型可落地”

这样整个策略才算闭环。

---

## 五、最终 Review 结论

### 已经做得好的

- 现状问题盘点充分
- 关键字覆盖面广
- 分阶段实施顺序合理
- `Lexer / Parser / Resolver` 方向正确

### 还需要补上的

- 标准输出模型
- 作用域规则
- 引用解析策略
- Step 激活语义
- Diagnostics 体系

### 最终判断

`INP-Parser-Design.md` 目前是：

> **“一份优秀的重构设计初稿”**

但还不是：

> **“一份真正闭环的通用 INP 解析策略文档”**

如果补齐本文列出的 5 个环节，它就能从“功能覆盖设计”提升为“通用解析框架设计”。
