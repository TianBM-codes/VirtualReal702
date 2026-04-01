# INP Parser 设计文档

> 目标：通用化 Abaqus INP 解析，支持多 Part / 多 Instance / Assembly、非线性材料、工况载荷等完整内容。
> 初稿时间：2026-03-28；v2 补充闭环策略：2026-03-28；v3 实现完成记录：2026-03-30
> 参与方：Claude（后端）、Gemini（前端）、Codex（review）

---

## 核心设计原则：Data Fidelity（数据保真）

> **INP Parser 的成功标志：其导出产物在 L1 HDF5 结构、标签语义及变换矩阵表示上，应与 `abaqus_dump.py` 从 ODB 抽取的产物无异。**

这条原则决定了框架的所有设计取舍：

- Parser 不做坐标变换，只存变换矩阵（与 ODB L1 一致）
- 保留原始节点/单元标签（Labels），不重新编号
- 输出格式以 L1 HDF5 schema 为唯一对齐目标
- 同一套 L2/L3 pipeline 应能无感知地消费 INP 或 ODB 的 L1 产物

---

## 一、现有代码能力盘点

| 功能 | 状态 | 备注 |
|---|---|---|
| 单 Part 节点/单元 | 基本可用 | generate 形式支持 |
| Nset / Elset | 基本可用 | generate 形式支持；无命名空间隔离 |
| Solid / Shell / Beam Section | 部分可用 | Beam 读取有问题（调用了不存在的 fem_db 方法） |
| Material（线性各向同性）| 部分可用 | 仅 Density / Elastic / Conductivity / Expansion / SpecificHeat |
| Amplitude 时程曲线 | 可用 | |
| Step + Cload | 部分可用 | 只处理了集中力；Boundary 是空存根 |
| Surface | 空存根 | ReadSurface 只跳过两行 |
| Assembly / Instance | 完全缺失 | 主循环无 `*assembly` 分支 |
| 多 Part 隔离 | 缺失 | 节点全塞一个全局列表，标签冲突风险 |

---

## 二、已知问题（代码层面）

1. **节点无 Part 隔离**：`self.nodes` / `self.global_node_hash` 是全局的，多 Part 时不同 Part 的节点标签可以重叠（Abaqus 允许），直接冲突
2. **Set 无命名空间**：`self.ele_sets` 是全局 dict，Part 级和 Assembly 级同名 set 会覆盖
3. **游标状态不一致**：各 `Read*` 函数推进 `iter_line` 的位置不统一，容易在扩展时引入漏行或重复读
4. **ReadBoundary / ReadSurface 是空存根**：只跳行，数据丢弃
5. **Elset 嵌套未支持**：Elset 内的成员可以是另一个 elset 的名字，当前直接 `int()` 会 ValueError（try/except 静默跳过）
6. **Set 合并语义**：Abaqus 允许同名 set 多次定义，取并集；当前对 Elset 会覆盖而非合并

---

## 三、未考虑到的部分（完整清单）

### 3.1 文件级

| 问题 | 说明 |
|---|---|
| `*Include, input=file.inp` | 递归文件包含，常见于大模型拆分；完全缺失 |
| 关键字大小写 | Abaqus 关键字不区分大小写，当前用 `.lower()` 基本处理了，但参数值（如 set 名）在某些地方未做统一处理 |
| 数据行续行 | 关键字行以逗号结尾时，下一行是同一条关键字的延续（参数过多时）；数据行同理（高阶单元节点数超出一行）。当前高阶单元的跨行读取是特殊处理的，但关键字行续行未处理 |
| 参数化输入 | `*Parameter` 关键字 + `<param>` 占位符，用于参数化建模；较少见但存在 |
| 文件编码 | 通常 ASCII，但模型名/注释可能含非 ASCII 字符 |

### 3.2 Assembly 与 Instance

这是扩展多模型支持的核心，完全缺失。

```
*Assembly, name=Assembly
  *Instance, name=PART-1-1, part=PART-1
    ** 可选：坐标偏移（平移向量，3个浮点数）
    ** 可选：旋转（参考点x,y,z + 轴向量x,y,z + 角度，共7个数）
  *End Instance

  *Nset, nset=BC-Nodes, instance=PART-1-1
    ...
  *Elset, elset=Load-Elems, instance=PART-2-1
    ...
  *Surface, name=Contact-Top, instance=PART-1-1
    ...
  *Tie, name=TIE-1
  *Coupling, ...
*End Assembly
```

**需要处理的细节：**
- Instance 平移：一行3个浮点数
- Instance 旋转：接在平移行之后，7个数（旋转中心3 + 轴方向3 + 角度1），角度单位是度
- Assembly 级别的 Nset/Elset 用 `instance=` 参数指定所属实例
- Set 成员可以用 `instance_name.set_name` 形式引用实例内的 set
- 同一 Part 可以实例化多次（PART-1-1, PART-1-2...），各自有独立变换

### 3.3 节点与单元

| 问题 | 说明 |
|---|---|
| `*Node, nset=xxx` | 节点定义时可以直接内联指定所属 nset |
| `*Node, system=C/S` | 圆柱/球坐标系下定义节点，需要坐标转换 |
| 高阶单元续行 | C3D20 有20个节点，必须跨多行；当前有特殊处理但逻辑不统一 |
| 节点标签不连续 | Abaqus 中节点/单元标签可以是任意非负整数，不一定从1开始也不一定连续 |

**单元类型覆盖缺口（可视化相关）：**

| 类别 | 类型示例 | 当前状态 |
|---|---|---|
| 实体 | C3D4/6/8/10/15/20/20R | 依赖 MeshElementFactory，不确定覆盖范围 |
| 壳 | S3/S4/S4R/S8R/S8R5 | 同上 |
| 平面 | CPS3/4/4R, CPE3/4/4R | 同上 |
| 轴对称 | CAX4/8R | 同上 |
| 梁 | B31/B32/PIPE31/PIPE32 | 需要截面方向向量才能可视化 |
| 桁架 | T3D2/T3D3 | 线单元 |
| 弹簧/阻尼 | SPRING1/2, DASHPOT | 零维/一维，可视化为点或线 |
| 质量 | MASS, ROTARYI | 零维 |
| 刚体 | R3D3/R3D4, RAX2 | 需标记为刚性 |
| 连接器 | CONN3D2 | 需要连接器类型属性 |
| 内聚力 | COH2D4/COH3D6/COH3D8 | 零厚度单元，可视化特殊 |
| 无限元 | CIN3D8/CINAX4 | 通常不需要可视化 |
| 用户单元 | UEL | 无法通用解析 |

### 3.4 Section（截面属性）

| 缺失类型 | 关键字 |
|---|---|
| 膜单元 | `*Membrane Section` |
| 通用梁截面 | `*Beam General Section` |
| 连接器截面 | `*Connector Section` |
| 内聚力截面 | `*Cohesive Section` |
| 衬垫截面 | `*Gasket Section` |
| 刚体 | `*Rigid Body`（不是 Section 但分配刚性） |
| 集中质量 | `*Mass` / `*Rotary Inertia` |
| 弹簧 | `*Spring` |
| 复合壳 | `*Shell Section, composite` — 多层铺层，每层有厚度+材料+角度 |

**Beam Section 特殊处理：**
需要截面形状（RECT/CIRCLE/PIPE/BOX/I/...）+ 尺寸参数 + 法线方向（`*Normal` 或 Section 数据行中的方向向量），用于可视化时渲染截面。

### 3.5 材料模型（重点：非线性）

#### 线性但当前缺失
```
*Elastic, type=ENGINEERING CONSTANTS   # 正交各向异性（9个参数）
*Elastic, type=ANISOTROPIC             # 完全各向异性（21个参数）
*Elastic, type=TRACTION                # 内聚力弹性
```

#### 非线性塑性
```
*Plastic                               # 等向硬化（von Mises）：(应力, 塑性应变) 对
*Plastic, hardening=KINEMATIC          # 随动硬化
*Plastic, hardening=COMBINED           # 组合硬化
*Cyclic Hardening                      # 循环硬化参数
*Rate Dependent                        # 应变率相关塑性
*Creep                                 # 蠕变（时间硬化 / 应变硬化）
*Viscous                               # 粘性
```

#### 损伤模型
```
*Damage Initiation, criterion=DUCTILE  # 韧性损伤起始
*Damage Initiation, criterion=SHEAR    # 剪切损伤
*Damage Initiation, criterion=FLD      # 成形极限图
*Damage Evolution                      # 损伤演化（能量/位移）
*Damage Stabilization
```

#### 超弹性（橡胶类）
```
*Hyperelastic, neo hooke               # Neo-Hookean
*Hyperelastic, mooney-rivlin           # Mooney-Rivlin
*Hyperelastic, ogden, n=3              # Ogden（n阶）
*Hyperelastic, yeoh
*Hyperelastic, test data input         # 从实验数据拟合
*Hyperfoam                             # 泡沫超弹
*Mullins Effect                        # Mullins 效应（填充橡胶）
```

#### 岩土 / 混凝土
```
*Drucker Prager                        # D-P 准则（土、岩石）
*Drucker Prager Hardening
*Cap Plasticity
*Mohr Coulomb Plasticity
*Concrete Damaged Plasticity           # CDP 模型（混凝土）
*Concrete Tension Stiffening
*Concrete Compression Hardening
*Brittle Cracking                      # 脆性开裂
```

#### 粘弹性
```
*Viscoelastic, time=PRONY              # Prony 级数
*Viscoelastic, frequency=TABULAR
```

#### 热相关
```
*Conductivity, type=ISO/ORTHO          # 各向异性导热
*Latent Heat                           # 潜热（相变）
*Plastic, dependencies=1               # 温度相关塑性（多行，每行对应一个温度）
*Elastic                               # 同样可以是温度相关的（多行）
```

#### 用户材料
```
*User Material, constants=N            # UMAT/VUMAT，N个常数，无法通用解析
```

**温度相关材料的通用模式：**
几乎所有材料参数都可以是温度的函数，数据格式是多行，最后一列是温度值。解析器必须识别并存储这种多行表格形式。

### 3.6 约束与相互作用

| 类型 | 关键字 | 说明 |
|---|---|---|
| 绑定约束 | `*Tie` | 面-面或点-面绑定 |
| 耦合约束 | `*Coupling` + `*Kinematic`/`*Distributing` | 参考点与面/集合的运动耦合 |
| 刚体 | `*Rigid Body` | 节点集合绑定到参考点做刚体运动 |
| 多点约束 | `*MPC` | BEAM/TIE/LINK/PIN 等类型 |
| 线性方程约束 | `*Equation` | 自由度的线性组合约束 |
| 接触对 | `*Contact Pair` | 主从面接触 |
| 通用接触 | `*Contact` + `*Contact Inclusions` | 全局接触定义（Abaqus/Explicit 常用）|
| 接触属性 | `*Surface Interaction` / `*Contact Property Assignment` | 摩擦、法向行为 |
| 预紧力 | `*Pre-tension Section` | 螺栓预紧 |

### 3.7 边界条件（当前是空存根）

`*Boundary` 格式复杂，有多种形式：

```
# 形式1：节点集 + DOF范围 + 值
*Boundary
Nset-1, 1, 3, 0.0    # DOF 1~3 = 0

# 形式2：对称/固定类型关键字
*Boundary
Nset-1, ENCASTRE     # 完全固定
Nset-1, XSYMM        # X对称面
Nset-1, PINNED       # 铰接

# 形式3：带时程
*Boundary, amplitude=AMP-1
Nset-1, 1, 1, 1.0

# 形式4：op=NEW（清除之前的边界条件）
*Boundary, op=NEW
```

固定类型关键字（ENCASTRE / PINNED / XSYMM / YSYMM / ZSYMM / XASYMM / YASYMM / ZASYMM）需要一个映射表展开到具体 DOF。

### 3.8 载荷（当前只有 Cload）

| 类型 | 关键字 | 说明 |
|---|---|---|
| 分布体力 | `*Dload, BX/BY/BZ` | 均匀体力 |
| 压力 | `*Dload, P` / `*Dsload` | 面压力，需要 Surface |
| 重力 | `*Gravity` | 重力加速度 |
| 离心力 | `*Dload, CENTRIF/ROTA` | 旋转惯性载荷 |
| 集中热流 | `*Cflux` | 热分析 |
| 分布热流 | `*Dflux` | 热分析 |
| 对流边界 | `*Film` | 热分析对流 |
| 辐射边界 | `*Radiate` | 热分析辐射 |
| 温度场 | `*Temperature` | 热-力耦合 |
| 初始条件 | `*Initial Conditions` | 初始应力/温度/速度等 |
| 预定义场 | `*Predefined Field` | 从结果文件读取场变量 |

### 3.9 分析步类型

| 步类型 | 关键字 | 说明 |
|---|---|---|
| 静力 | `*Static` | 线性/非线性静力 |
| Riks 弧长 | `*Static, riks` | 后屈曲分析 |
| 动力隐式 | `*Dynamic` | 时域动力响应 |
| 动力显式 | `*Dynamic, explicit` | 显式积分（ABAQUS/Explicit）|
| 粘弹性 | `*Visco` | |
| 特征频率 | `*Frequency` | 模态分析 |
| 屈曲 | `*Buckle` | 线性屈曲 |
| 稳态热 | `*Heat Transfer, steady state` | |
| 瞬态热 | `*Heat Transfer` | |
| 热-力耦合 | `*Coupled Temperature-displacement` | |
| 土力学 | `*Soils` / `*Geostatic` | |
| 模态叠加动力 | `*Modal Dynamic` | |
| 稳态动力 | `*Steady State Dynamics` | |

步参数（`nlgeom=YES`、增量步控制、收敛控制 `*Controls`）也需要解析。

### 3.10 Surface 定义（当前是空存根）

Surface 是许多载荷和约束的基础，必须正确解析。

```
# 基于单元集合的 Surface（最常见）
*Surface, type=ELEMENT, name=SURF-TOP
Elset-Top, S1        # 单元集 + 面标识符

# 基于节点的 Surface
*Surface, type=NODE, name=SURF-NODES
Nset-1,              # 节点集（权重可选）

# 面标识符含义（实体单元）
# S1=面1(1-2-3-4), S2=面2(5-6-7-8), S3=面3(1-2-6-5), ...
# 壳单元：SPOS=正面, SNEG=负面
# 梁单元：END1, END2
```

### 3.11 Orientation（材料方向）

```
*Orientation, name=ORI-1, system=RECTANGULAR
1.,0.,0., 0.,1.,0.    # 局部坐标轴定义
```

- 被 Section（壳、各向异性材料）引用
- 影响材料属性的方向性和应力输出分量的含义
- 对非线性材料分析是必须的

### 3.12 Transform

```
*Transform, nset=Nset-1, type=C
0.,0.,0., 0.,0.,1.    # 圆柱坐标变换
```

用于指定某节点集的自由度在局部坐标系下施加 BC 或输出结果，与 Instance 变换不同。

---

## 四、闭环策略补充（v2）

> 本章节根据 Codex 和 Gemini 的 review 补充，覆盖初稿中未闭环的 5 个关键环节。

### 4.0 完整数据流

```
INP 源文件（含 *Include）
    ↓ Lexer
List[KeywordBlock]（语法级，含 source location）
    ↓ Parser
InpModel（语法级，引用未展开）
    ↓ Resolver
ResolvedModel（语义级，引用已展开）
    ↓ Validator / Exporter
Canonical FEM Exchange Model
    ↓
HDF5 + manifest.db（与 ODB L1 产物格式一致）
```

`Canonical FEM Exchange Model` 是多格式支持的核心枢纽——INP、BDF、CDB 都输出到这一层，L2 ingest 以它为唯一输入。

---

### 4.1 作用域与命名规则（Scope & Name Resolution）

INP 中命名对象的作用域分为五级：

```
Scope = ROOT | PART(name) | ASSEMBLY | INSTANCE(name) | STEP(name) | MATERIAL(name)
QualifiedName = (scope, local_name)
```

**规则：**

| 情况 | 处理方式 |
|---|---|
| Part 内定义的 Nset/Elset/Surface | `(PART(name), local_name)` |
| Assembly 内定义的 Nset/Elset（无 `instance=`）| `(ASSEMBLY, local_name)` |
| Assembly 内定义的 Nset/Elset（有 `instance=X`）| `(INSTANCE(X), local_name)` |
| `PART-1-1.Set-Top` 显式引用 | 解析为 `(INSTANCE(PART-1-1), Set-Top)` |
| 同名 set 多次定义 | 取并集（union），不覆盖 |
| Part 与 Assembly 级别同名对象 | 按 scope 独立存储，不冲突 |

**Parser 阶段**：所有命名对象以 `QualifiedName` 为键存入，不做全局 dict。
**Resolver 阶段**：解析引用时按 scope 查找，找不到则生成 Diagnostic。

---

### 4.2 引用解析策略（Reference Resolution Pipeline）

引用分三类，分别在不同阶段处理：

**类型一：Name Reference（名字引用）**
- `set_name` / `material_name` / `instance_name` / `amplitude_name`
- Parser 阶段：只存字符串，不查找
- Resolver 阶段：按 QualifiedName 查找，生成实际对象指针

**类型二：Scoped Reference（带作用域引用）**
- `instance=PART-1-1`（Assembly Nset 中指定实例）
- `PART-1-1.Set-Top`（显式跨域引用）
- Parser 阶段：解析出 scope + local_name，存为 QualifiedName
- Resolver 阶段：查找对应 scope 下的对象

**类型三：Semantic Reference（语义展开引用）**
- `Surface` → `[(elem_label, face_id), ...]`（需查 Topology Hub）
- `Boundary` → `[(nset, dof_range, value, amplitude)]`（需展开 DOF 类型关键字）
- `Elset` 嵌套 → 递归展开为标签列表
- 只在 Resolver 阶段处理，依赖前两类引用已解析完毕

**顺序约束**：必须按 Name → Scoped → Semantic 顺序执行，不可并行。

---

### 4.3 Step 激活语义（Step Activation Semantics）

Step 不是关键字容器，而是一个**激活/覆盖语义容器**。解析器保存两层：

```python
@dataclass
class StepDeclaration:
    """Parser 阶段：原始声明，不做继承展开"""
    name: str
    step_type: StepType
    nlgeom: bool
    boundary_conditions: List[BCDeclaration]   # 含 op=NEW/MOD 标记
    loads: List[LoadDeclaration]

@dataclass
class StepResolvedState:
    """Resolver 阶段：经继承/覆盖规则展开后的生效状态"""
    name: str
    active_bcs: Dict[QualifiedName, BCValue]   # 已继承 + 已覆盖
    active_loads: List[ResolvedLoad]
```

**继承规则：**
- 默认：前一 Step 的 BC 继承到下一 Step（Abaqus 行为）
- `*Boundary, op=NEW`：清除该 Step 中所有之前的同类 BC，重新施加
- `*Boundary, op=MOD`（默认）：在已有 BC 上叠加/修改
- Amplitude 绑定：记录在 BCDeclaration 上，Resolver 阶段关联 Amplitude 对象

可视化和导出时直接使用 `StepResolvedState`，不需要重新计算继承逻辑。

---

### 4.4 拓扑知识库（Topology Hub）

Surface 解析（`S1`/`SPOS`/`END1` 对应哪些节点）不应散落在解析函数里，而应查一张独立的拓扑表：

```python
# 单元面定义：face_id → 该面的局部节点索引（0-based，按单元节点顺序）
ELEMENT_FACE_TABLE: Dict[str, Dict[str, List[int]]] = {
    "C3D8": {
        "S1": [0, 1, 2, 3],   # 底面
        "S2": [4, 5, 6, 7],   # 顶面
        "S3": [0, 1, 5, 4],
        "S4": [1, 2, 6, 5],
        "S5": [2, 3, 7, 6],
        "S6": [3, 0, 4, 7],
    },
    "S4": {
        "SPOS": [0, 1, 2, 3],  # 正面
        "SNEG": [0, 3, 2, 1],  # 负面（节点顺序翻转）
    },
    "B31": {
        "END1": [0],
        "END2": [1],
    },
    # ...（由同事补全完整表）
}
```

Resolver 在展开 Surface 时：
1. 遍历 Surface 引用的 Elset
2. 对每个单元查 `ELEMENT_FACE_TABLE[abaqus_type][face_id]`
3. 得到该面的节点标签列表
4. 输出 `[(elem_label, face_id, node_labels), ...]`

好处：加新单元类型只改这张表，不改解析逻辑。

---

### 4.5 Diagnostics 体系

通用解析器的另一半价值是**稳定地解释问题**，而不只是"支持什么"。

```python
@dataclass
class Diagnostic:
    severity: Literal["INFO", "WARNING", "ERROR"]
    code: str            # 见下方错误码表
    message: str
    file: str            # 来源文件（*Include 展开后仍保留原始文件名）
    line: int            # 来源行号
    include_stack: List[str]   # Include 调用链
    context: str         # 如 "Part:PART-1 / Element:1234"
```

**错误码：**

| 码 | 级别 | 说明 |
|---|---|---|
| `UNKNOWN_KEYWORD` | WARNING | 未知关键字，跳过 |
| `UNSUPPORTED_PARAM` | WARNING | 已知关键字但参数组合不支持 |
| `UNRESOLVED_SET` | ERROR | 引用的 Set 不存在 |
| `UNRESOLVED_MATERIAL` | ERROR | 引用的 Material 不存在 |
| `UNRESOLVED_INSTANCE` | ERROR | 引用的 Instance 不存在 |
| `UNRESOLVED_AMPLITUDE` | WARNING | 引用的 Amplitude 不存在 |
| `DUPLICATE_NAME` | WARNING | 同名对象多次定义（Set 取并集，其余记录） |
| `INCLUDE_CYCLE` | ERROR | `*Include` 形成循环引用 |
| `UNKNOWN_ELEMENT_TYPE` | WARNING | 无法映射到 factory_type，跳过该单元 |
| `INVALID_FACE_ID` | WARNING | Surface 中的面号对该单元类型无效 |
| `INVALID_DOF` | WARNING | Boundary 中的 DOF 超出范围 |
| `UMAT_SKIPPED` | INFO | 遇到 `*User Material`，跳过 |
| `MALFORMED_DATA` | ERROR | 数据行格式无法解析 |

**恢复策略：**
- `ERROR`：记录后跳过当前对象，继续解析（非破坏性）
- `WARNING`：记录，继续
- `INFO`：记录，继续
- 解析完成后，调用方可检查 `diagnostics` 列表决定是否使用结果

---

## 五、框架设计方案

### 数据模型层

```
InpModel
├── parts: Dict[str, Part]
│   └── Part
│       ├── nodes: Dict[int, Node]         # 标签→节点
│       ├── elements: Dict[int, Element]   # 标签→单元
│       ├── nsets: Dict[str, Nset]
│       ├── elsets: Dict[str, Elset]
│       ├── surfaces: Dict[str, Surface]
│       └── sections: List[Section]
│
├── assembly: Assembly
│   ├── instances: Dict[str, Instance]
│   │   └── Instance
│   │       ├── part_name: str
│   │       ├── translation: ndarray[3]    # 平移向量
│   │       └── rotation: Optional[Rotation]  # 旋转（中心+轴+角）
│   ├── nsets: Dict[str, AssemblyNset]    # 含 instance 信息
│   ├── elsets: Dict[str, AssemblyElset]
│   ├── surfaces: Dict[str, Surface]
│   └── constraints: List[Constraint]
│
├── materials: Dict[str, Material]
│   └── Material
│       ├── elastic: Optional[ElasticData]
│       ├── plastic: Optional[PlasticData]       # 多行表格
│       ├── hyperelastic: Optional[HyperelasticData]
│       ├── damage: Optional[DamageData]
│       └── ...（可扩展）
│
├── amplitudes: Dict[str, Amplitude]
├── orientations: Dict[str, Orientation]
│
└── steps: List[Step]
    └── Step
        ├── name: str
        ├── step_type: StepType (STATIC/DYNAMIC/FREQUENCY/...)
        ├── nlgeom: bool
        ├── boundary_conditions: List[BC]
        ├── loads: List[Load]
        └── output_requests: List[OutputRequest]
```

### 四层解析流水线

```
┌─────────────────────────────────────────┐
│  Layer 1: Lexer                         │
│  - *Include 递归展开（检测循环）        │
│  - 关键字续行合并                       │
│  - 切分关键字块（keyword+params+data）  │
│  - 保留 source location（文件+行号）    │
│  - 输出: List[KeywordBlock]             │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  Layer 2: Parser                        │
│  - 关键字注册表（按 context 分发）      │
│  - 维护解析上下文（Part/Assembly/Step） │
│  - 所有命名对象以 QualifiedName 为键    │
│  - 引用只存字符串，不立即查找           │
│  - 输出: InpModel（语法级，引用未展开） │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  Layer 3: Resolver                      │
│  - 按顺序：Name → Scoped → Semantic     │
│  - Set 嵌套递归展开                     │
│  - Instance → Part + 变换矩阵           │
│  - Surface → [(elem, face_id, nodes)]   │
│    （查 Topology Hub）                  │
│  - BC 继承/覆盖 → StepResolvedState     │
│  - 输出: ResolvedModel（语义级）        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  Layer 4: Validator / Exporter          │
│  - 生成 Diagnostics（含恢复策略）       │
│  - 输出 Canonical FEM Exchange Model   │
│  - 映射到 HDF5 + manifest.db           │
└─────────────────────────────────────────┘
```

### 关键字注册表设计

```python
# 支持按上下文注册不同处理器
registry = KeywordRegistry()
registry.register("NODE",    NodeHandler,    contexts=[CTX_PART, CTX_ROOT])
registry.register("ELEMENT", ElementHandler, contexts=[CTX_PART, CTX_ROOT])
registry.register("NSET",    NsetHandler,    contexts=[CTX_PART, CTX_ASSEMBLY, CTX_ROOT])
registry.register("SURFACE", SurfaceHandler, contexts=[CTX_PART, CTX_ASSEMBLY])
registry.register("PLASTIC", PlasticHandler, contexts=[CTX_MATERIAL])
# 未知关键字：记录警告 + 跳过，不崩溃
```

---

## 六、实现优先级

### 第一阶段：多 Part / Assembly 网格可视化 ✅ 已完成

- [x] Lexer：关键字块切分 + `*Include` 展开（`src/inp/lexer.py`）
- [x] Part 容器 + 节点/单元/nset/elset 隔离存储（`src/inp/model.py` + `src/inp/parser.py`）
- [x] Assembly + Instance（含平移旋转变换）（`src/inp/parser.py` + `src/inp/resolver.py`）
- [x] Assembly 级别的 Nset / Elset（含 `instance=` 参数）（`src/inp/parser.py`）
- [x] Set 嵌套展开 + 变换矩阵计算（Rodrigues）（`src/inp/resolver.py`）
- [x] 对接 L2/L3 pipeline（`src/inp/exporter.py` → L1 HDF5 + manifest.db）

### 第二阶段：工况与约束可视化

- [ ] Surface 解析（`type=ELEMENT`，展开到 face 列表）
- [ ] Boundary（完整 DOF + 类型关键字映射）
- [ ] Dload / Pressure（分布载荷）
- [ ] Tie / Coupling / Rigid Body 约束
- [ ] Orientation

### 第三阶段：非线性材料

- [x] Plastic（等向/随动/组合硬化）+ 温度相关表格（`src/inp/model.py`：`PlasticData`）
- [x] 损伤模型（Damage Initiation + Evolution）（`src/inp/model.py`：`DamageData`）
- [x] Hyperelastic（Neo-Hooke / Mooney-Rivlin / Ogden / test data）（`src/inp/model.py`：`HyperelasticData`）
- [x] Creep（`src/inp/model.py`：`CreepData`）
- [ ] Drucker-Prager / Concrete Damaged Plasticity
- [ ] Viscoelastic（Prony 级数）
- [x] User Material（记录常数后跳过，Diagnostic UMAT_SKIPPED）

### 第四阶段：完整 Step 解析

- [x] Step 类型枚举 + nlgeom 参数（`src/inp/model.py`：`StepDeclaration`）
- [x] Boundary（基本 DOF 格式）、Cload（集中力）、Dload（分布载荷）
- [ ] Boundary 类型关键字展开（ENCASTRE / PINNED / XSYMM 等）
- [ ] 热分析相关
- [ ] 初始条件

---

## 七、已确认决策

| # | 问题 | 决策 |
|---|---|---|
| 1 | 单元类型映射 | **重写**：在解析阶段直接将 Abaqus 单元类型映射到 `MeshElementFactory` 枚举值，不依赖现有代码的后续转换逻辑；同时在 Element 上保留原始 Abaqus 类型字符串（见下方补充说明） |
| 2 | 非线性材料解析目标 | **仅保存参数**：存储各属性的原始数值数据，不在解析阶段做行为建模或计算 |
| 3 | UMAT 支持 | **不支持**：专注 Abaqus 内置材料模型，遇到 `*User Material` 记录警告后跳过 |
| 4 | Instance 坐标变换 | **保留原始坐标 + 变换矩阵**：Resolver 阶段不修改节点坐标，存储平移向量和旋转矩阵，与 ODB L1 处理方式一致 |
| 5 | 温度相关材料参数 | **离散点列表**：按 `[(value, temperature), ...]` 存储，便于后续插值计算和可视化 |

### 补充说明：单元类型映射的原始信息保留

几何形状相同但积分方案不同的单元（如 `C3D8` 与 `C3D8R`、`S4` 与 `S4R`）映射到同一个 `MeshElementFactory` 枚举值，但**必须在 Element 对象上额外保留原始 Abaqus 类型字符串**。

原因：缩减积分（Reduced Integration）、非协调模式（Incompatible Modes，如 `C3D8I`）、杂交单元（Hybrid，如 `C3D8H`）等变体在几何可视化上无差异，但在后续分析判断（积分点数量、沙漏控制、不可压缩性处理等）上有本质区别。

建议的 Element 数据结构：

```python
@dataclass
class Element:
    label: int
    factory_type: ElementType      # 映射后的枚举，用于可视化
    abaqus_type: str               # 原始字符串，如 "C3D8R"，用于分析
    node_labels: List[int]
    # ...
```

映射表示例（完整表由同事维护）：

```python
ABAQUS_TO_FACTORY: Dict[str, ElementType] = {
    # 实体
    "C3D8":   ElementType.HEX8,
    "C3D8R":  ElementType.HEX8,   # 缩减积分，几何同 C3D8
    "C3D8I":  ElementType.HEX8,   # 非协调模式
    "C3D8H":  ElementType.HEX8,   # 杂交
    "C3D20":  ElementType.HEX20,
    "C3D20R": ElementType.HEX20,
    # 壳
    "S4":     ElementType.QUAD4,
    "S4R":    ElementType.QUAD4,
    "S3":     ElementType.TRI3,
    # ... 由同事补全
}
```

---

## 八、实现状态（v3，2026-03-30）

### 已实现文件

| 文件 | 层级 | 说明 |
|---|---|---|
| `src/inp/lexer.py` | Layer 1 | 关键字块切分，`*Include` 递归展开，循环检测，续行合并 |
| `src/inp/parser.py` | Layer 2 | 40+ 关键字处理，上下文栈（Part/Assembly/Material/Step），静默跳过无害关键字 |
| `src/inp/model.py` | 数据模型 | 完整 InpModel，含 Part/Assembly/Material/Step 所有数据类 |
| `src/inp/resolver.py` | Layer 3 | Set 嵌套展开（循环检测），Assembly 引用解析，Rodrigues 变换矩阵 |
| `src/inp/topology.py` | 拓扑知识库 | `get_face_nodes(factory_type, face_id)` —— Surface 展开用 |
| `src/inp/diagnostics.py` | 诊断 | 13 个错误码，非破坏性收集 |
| `src/inp/exporter.py` | Layer 4 | InpModel → L1 HDF5 + manifest.db，与 `abaqus_dump.py` 输出格式一致 |
| `src/inp/__init__.py` | 公共 API | `parse_inp(filepath) → InpModel` |

### 工具脚本

| 脚本 | 用途 |
|---|---|
| `tools/inp_tree.py` | 按 Abaqus CAE 模型树格式打印解析结果，快速核查 |
| `tools/inp_to_vtu.py` | 导出 VTU 格式，可用 ParaView 可视化 |
| `tools/inp_to_l1.py` | **主入口**：INP → L1 workspace，之后直接接 L2 `ingest.py` |

### 完整使用流程

```bash
# 1. INP → L1 HDF5（新路径，无需 Abaqus）
python tools/inp_to_l1.py model.inp /data/my_workspace

# 2. L2 预处理（与 ODB 路径完全相同，无需修改）
python src/l2/ingest.py --workspace /data/my_workspace

# 3. L3 服务（与 ODB 路径完全相同）
gunicorn src.l3.main:app -w 2 -k uvicorn.workers.UvicornWorker
```

### 关键实现细节

**face_node_conn 的语义**：存节点的 *行号*（row index，即在 `nodes/labels` 数组中的位置），而非节点标签本身。这与 L2 `ingest.py` 的期望完全一致。

**高阶单元降阶**：C3D10/C3D15/C3D20 等高阶单元，Exporter 只取前 N 个角节点（corner nodes）参与面定义，中间节点舍弃。这与 `abaqus_dump.py` 的 `FACE_DEFS` 行为一致。

**已验证**：`door.inp`（37558 节点，33986 单元）解析 0 错误，249 个无害关键字静默跳过。3 种材料（ELASTIC_E2800_RO1200、ELASTIC_E210E3_RO7890、ELASTIC_E76E3_RO2500）正确写入 L1 H5，L3 color-code 接口按材料上色验证通过。

### 尚未支持的场景

- 无 `*Assembly` 的单部件 INP（Exporter 当前要求 Assembly 存在）
- Boundary 类型关键字展开（ENCASTRE / PINNED / XSYMM 等）
- Drucker-Prager / CDP 混凝土 / Viscoelastic 材料模型
- 热分析步类型
