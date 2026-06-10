# 基于 SOL200 的 MAC 灵敏度模型修正技术路线（路径二）

---

## 一、总体架构

整个修正框架由三个层次构成，Nastran 负责求解，Python 负责组装与优化：

```
┌─────────────────────────────────────────────────────────────────┐
│                     Python 主控程序                              │
│                                                                  │
│   ┌─────────────┐     ┌──────────────────────────────────────┐  │
│   │  优化算法    │ ◄── │       灵敏度组装层                    │  │
│   │  (SQP /     │     │  ① 读取 Φ_sim, f_sim (OP2)          │  │
│   │  L-BFGS-B)  │     │  ② 读取 ∂Φ/∂p, ∂f/∂p (PCH)         │  │
│   │             │     │  ③ 符号对齐                           │  │
│   │  ∇J → Δp   │     │  ④ 计算 MAC 及 ∂MAC/∂p（解析公式）   │  │
│   └──────┬──────┘     │  ⑤ 组装目标函数梯度 ∂J/∂p            │  │
│          │ 更新 BDF    └──────────────────────────────────────┘  │
└──────────┼──────────────────────────────────────────────────────┘
           ▼
  ┌─────────────────────────┐
  │      Nastran 求解层      │
  │                         │
  │  SOL103 → Φ_sim, f_sim  │  （验证迭代点）
  │  SOL200 → ∂Φ/∂p, ∂f/∂p │  （计算灵敏度）
  └─────────────────────────┘
```

**迭代终止条件（任一满足）：**
- $\|\Delta\mathbf{p}\|_\infty < \varepsilon_p$（参数变化量收敛）
- $|J^{(k+1)} - J^{(k)}| / J^{(k)} < \varepsilon_J$（目标函数相对变化收敛）
- 最小 MAC 对角值 $> 0.90$，且最大频率误差 $< 3\%$（工程验收准则）

---

## 二、数学推导

### 2.1 符号约定

| 符号 | 含义 |
|------|------|
| $N_p$ | 设计参数个数（如弹性模量、密度各区域数目之和） |
| $N_m$ | 参与修正的匹配阶数 |
| $N_s$ | 传感器自由度数 |
| $p_k$（$k=1,\ldots,N_p$） | 第 $k$ 个设计参数（当前迭代值） |
| $\boldsymbol{\phi}_{exp,i} \in \mathbb{R}^{N_s}$ | 第 $i$ 阶试验振型（传感器节点处分量，已归一化） |
| $\boldsymbol{\phi}_{sim,j} \in \mathbb{R}^{N_s}$ | 第 $j$ 阶仿真振型（对应传感器节点处分量，质量归一化后提取） |
| $f_{sim,j}$，$f_{exp,i}$ | 仿真/试验第 $j$/$i$ 阶固有频率 |
| $\lambda_j = (2\pi f_{sim,j})^2$ | 第 $j$ 阶特征值 |

振型匹配对 $\{(i,j)\}$ 在每次大迭代前由 MAC 矩阵确定，迭代中固定不变。

---

### 2.2 目标函数定义

$$\boxed{J(\mathbf{p}) = \alpha \sum_{n=1}^{N_m}(1 - \text{MAC}_{i_n j_n}) + \beta \sum_{n=1}^{N_m}\left(\frac{f_{sim,j_n} - f_{exp,i_n}}{f_{exp,i_n}}\right)^2}$$

其中：
- $(i_n, j_n)$ 为第 $n$ 个匹配对（试验阶次 $i_n$，仿真阶次 $j_n$）
- $\alpha, \beta$ 为权重系数，建议初始取 $\alpha = 1,\, \beta = 0.1$，根据残差量级调整
- 第一项驱动振型形状逼近，第二项驱动频率逼近

---

### 2.3 MAC 定义

$$\text{MAC}_{ij} = \frac{(\boldsymbol{\phi}_{exp,i}^T \boldsymbol{\phi}_{sim,j})^2}{(\boldsymbol{\phi}_{exp,i}^T \boldsymbol{\phi}_{exp,i})(\boldsymbol{\phi}_{sim,j}^T \boldsymbol{\phi}_{sim,j})}$$

定义三个中间量（便于推导）：

$$a_{ij} = \boldsymbol{\phi}_{exp,i}^T \boldsymbol{\phi}_{sim,j}, \quad b_i = \|\boldsymbol{\phi}_{exp,i}\|^2, \quad c_j = \|\boldsymbol{\phi}_{sim,j}\|^2$$

则 $\text{MAC}_{ij} = a_{ij}^2 / (b_i c_j)$。

> **符号对齐前提**：在每次大迭代开始时，对仿真振型执行符号校正，确保 $a_{ij} > 0$（匹配对的互相关为正）。校正方法：若 $\boldsymbol{\phi}_{exp,i}^T \boldsymbol{\phi}_{sim,j} < 0$，则令 $\boldsymbol{\phi}_{sim,j} \leftarrow -\boldsymbol{\phi}_{sim,j}$，同时将对应的灵敏度矩阵取反。

---

### 2.4 MAC 对设计参数的灵敏度（核心公式推导）

对 $\text{MAC}_{ij} = a_{ij}^2 / (b_i c_j)$ 求关于 $p_k$ 的偏导：

$$\frac{\partial \text{MAC}_{ij}}{\partial p_k} = \frac{2a_{ij}}{b_i c_j} \cdot \frac{\partial a_{ij}}{\partial p_k} - \frac{a_{ij}^2}{b_i c_j^2} \cdot \frac{\partial c_j}{\partial p_k}$$

由于 $b_i$ 与设计参数无关（试验振型固定），逐项展开：

$$\frac{\partial a_{ij}}{\partial p_k} = \boldsymbol{\phi}_{exp,i}^T \frac{\partial \boldsymbol{\phi}_{sim,j}}{\partial p_k}$$

$$\frac{\partial c_j}{\partial p_k} = 2\boldsymbol{\phi}_{sim,j}^T \frac{\partial \boldsymbol{\phi}_{sim,j}}{\partial p_k}$$

代入并提取公因子：

$$\frac{\partial \text{MAC}_{ij}}{\partial p_k} = \frac{2a_{ij}}{b_i c_j} \cdot \boldsymbol{\phi}_{exp,i}^T \frac{\partial \boldsymbol{\phi}_{sim,j}}{\partial p_k} - \frac{2a_{ij}^2}{b_i c_j^2} \cdot \boldsymbol{\phi}_{sim,j}^T \frac{\partial \boldsymbol{\phi}_{sim,j}}{\partial p_k}$$

$$\boxed{\frac{\partial \text{MAC}_{ij}}{\partial p_k} = \underbrace{\frac{2a_{ij}}{b_i c_j} \left[\boldsymbol{\phi}_{exp,i} - \frac{a_{ij}}{c_j}\boldsymbol{\phi}_{sim,j}\right]^T}_{\mathbf{g}_{ij}^T \,\in\, \mathbb{R}^{1 \times N_s}} \cdot \underbrace{\frac{\partial \boldsymbol{\phi}_{sim,j}}{\partial p_k}}_{\in\, \mathbb{R}^{N_s}}}$$

定义 **MAC 梯度向量**（仅依赖于当前振型，与 $p_k$ 无关）：

$$\mathbf{g}_{ij} = \frac{2a_{ij}}{b_i c_j} \left[\boldsymbol{\phi}_{exp,i} - \frac{a_{ij}}{c_j}\boldsymbol{\phi}_{sim,j}\right] \in \mathbb{R}^{N_s}$$

因此 MAC 灵敏度的计算归结为：
1. 用当前振型计算 $\mathbf{g}_{ij}$（$O(N_s)$ 运算）；
2. 将其与 SOL200 提供的特征向量灵敏度 $\partial \boldsymbol{\phi}_{sim,j}/\partial p_k$ 做点积。

---

### 2.5 特征值灵敏度（SOL200 原生提供）

对特征方程 $(\mathbf{K} - \lambda_j \mathbf{M})\boldsymbol{\phi}_j = \mathbf{0}$ 关于 $p_k$ 微分，左乘 $\boldsymbol{\phi}_j^T$，利用质量归一化条件 $\boldsymbol{\phi}_j^T \mathbf{M} \boldsymbol{\phi}_j = 1$，得：

$$\boxed{\frac{\partial \lambda_j}{\partial p_k} = \boldsymbol{\phi}_j^T \left(\frac{\partial \mathbf{K}}{\partial p_k} - \lambda_j \frac{\partial \mathbf{M}}{\partial p_k}\right) \boldsymbol{\phi}_j}$$

转换为频率灵敏度：
$$\frac{\partial f_{sim,j}}{\partial p_k} = \frac{1}{8\pi^2 f_{sim,j}} \cdot \frac{\partial \lambda_j}{\partial p_k}$$

**SOL200 通过 `DRESP1 FREQ` 直接输出此量**，无需手动计算。

---

### 2.6 特征向量灵敏度——Nelson 法（SOL200 内部实现原理）

> **注意**：此节说明 SOL200 的内部计算原理，用于理解和验证结果。用户无需自行实现，SOL200 通过 `DRESP1 DISP`（模态子工况）自动完成以下计算并输出结果。

对特征方程微分得到特征向量灵敏度满足的奇异线性方程组：

$$(\mathbf{K} - \lambda_j \mathbf{M}) \frac{\partial \boldsymbol{\phi}_j}{\partial p_k} = \underbrace{\frac{\partial \lambda_j}{\partial p_k} \mathbf{M} - \left(\frac{\partial \mathbf{K}}{\partial p_k} - \lambda_j \frac{\partial \mathbf{M}}{\partial p_k}\right)}_{\text{RHS}} \boldsymbol{\phi}_j$$

**系数矩阵奇异**（$\boldsymbol{\phi}_j$ 在其零空间中），Nelson 法通过如下步骤绕开奇异性：

**步骤 1**：确定主分量索引 $s = \arg\max_r |\phi_j^{(r)}|$

**步骤 2**：将上述方程组第 $s$ 行替换为 $v_s = 0$，构造**非奇异**修正方程：

$$\tilde{\mathbf{A}}_j \mathbf{v}_j^{(k)} = \tilde{\mathbf{d}}_j^{(k)}$$

其中 $\tilde{\mathbf{A}}_j$ 是将 $(\mathbf{K} - \lambda_j \mathbf{M})$ 的第 $s$ 行替换为 $\mathbf{e}_s^T$ 后的矩阵；$\tilde{\mathbf{d}}_j^{(k)}$ 是 RHS 向量将第 $s$ 分量设为零后的结果。

**步骤 3**：由质量归一化条件 $\boldsymbol{\phi}_j^T \mathbf{M} \boldsymbol{\phi}_j = 1$ 对 $p_k$ 微分，得定标系数：

$$\beta_j^{(k)} = -\boldsymbol{\phi}_j^T \mathbf{M} \mathbf{v}_j^{(k)} - \frac{1}{2}\boldsymbol{\phi}_j^T \frac{\partial \mathbf{M}}{\partial p_k} \boldsymbol{\phi}_j$$

**步骤 4**：完整特征向量灵敏度：

$$\boxed{\frac{\partial \boldsymbol{\phi}_j}{\partial p_k} = \mathbf{v}_j^{(k)} + \beta_j^{(k)} \boldsymbol{\phi}_j}$$

---

### 2.7 目标函数梯度汇总

$$\boxed{\frac{\partial J}{\partial p_k} = -\alpha \sum_{n=1}^{N_m} \mathbf{g}_{i_n j_n}^T \frac{\partial \boldsymbol{\phi}_{sim,j_n}}{\partial p_k} + 2\beta \sum_{n=1}^{N_m} \frac{f_{sim,j_n} - f_{exp,i_n}}{f_{exp,i_n}^2} \cdot \frac{\partial f_{sim,j_n}}{\partial p_k}}$$

其中 $\partial \boldsymbol{\phi}_{sim,j_n}/\partial p_k$ 和 $\partial f_{sim,j_n}/\partial p_k$ 均由 SOL200 提供，$\mathbf{g}_{i_n j_n}$ 由 Python 根据当前振型实时计算。

---

## 三、SOL200 BDF 配置

### 3.1 关键卡片一览

| 卡片 | 作用 |
|------|------|
| `DESVAR` | 定义设计变量（含初值、上下界） |
| `DVMREL1` | 将设计变量线性映射到材料属性（MAT1 字段） |
| `DRESP1 FREQ` | 定义固有频率响应（灵敏度 $\partial f/\partial p$） |
| `DRESP1 DISP` | 定义振型分量响应（灵敏度 $\partial \phi^{(r)}/\partial p$） |
| `DRESP1` (目标) | 用于 `DESOBJ`，SOL200 必须有目标函数（用频率残差代替即可） |
| `DOPTPRM DESMAX 0` | 仅执行初始点灵敏度计算，不做设计更新 |
| `DSAPRT` | 控制灵敏度矩阵输出到 `.pch` 文件 |
| `EIGRL` | 特征值求解参数（建议用 MASS 归一化） |

---

### 3.2 完整 BDF 示例

以下示例假设：
- 2 个设计变量：区域 1 的弹性模量 $E_1$、密度 $\rho_1$
- 关注前 3 阶模态
- 传感器节点编号：101, 102, 103, 104, 105（均为 Z 方向）

```nastran
$-----------------------------------------------------------------------
$  SOL200 Modal Sensitivity Extraction for MAC-Based Model Updating
$  用途：提取振型灵敏度和频率灵敏度，不在 Nastran 内做优化
$-----------------------------------------------------------------------
SOL 200
TIME 3600
CEND

TITLE    = MAC Model Updating - Sensitivity Extraction
SUBTITLE = Initial Design Point
ECHO     = NONE

$--- 目标函数（必须有，用第 1 阶频率充当 dummy 目标）---
DESOBJ(MIN) = 901

$--- 设计约束（本次不施加约束，仅提取灵敏度）---
$DESGLB = ...

$--- 灵敏度矩阵输出到 .pch 文件 ---
DSAPRT(END, FORMATTED, EXPORT)

$--- 优化参数：DESMAX=0 表示仅计算初始点灵敏度，不迭代 ---
$   （注：部分 Nastran 版本需 DESMAX=1 才触发灵敏度计算，可先试 0）

$--- 子工况：法模分析 ---
SUBCASE 10
  LABEL    = Normal Modes Analysis
  ANALYSIS = MODES
  METHOD   = 100
  SPC      = 1
  VECTOR(SORT1, REAL, PHASE) = ALL

BEGIN BULK

$=======================================================================
$  优化控制参数
$=======================================================================
DOPTPRM  DESMAX  0                                                      +
+        IPRINT  2     FSDMAX  1     P1      1     P2      15

$=======================================================================
$  设计变量定义
$  DESVAR, ID, LABEL,    XINIT,      XLB,     XUB,    DELXV
$=======================================================================
$  弹性模量 E1（初始值 70000 MPa，允许 ±30% 变化）
DESVAR,   1,  E1_ALU,   70000.0,   49000.0, 91000.0,  0.1
$  密度 RHO1（初始值 2700 kg/m³，允许 ±20% 变化）
DESVAR,   2,  RHO1_ALU, 2700.0,    2160.0,  3240.0,   0.05

$=======================================================================
$  设计变量与材料属性关联
$  DVMREL1, ID,  TYPE, MID, MPNAME, MPMIN,    MPMAX,  C0
$  +,        DVID1, COEF1, ...
$=======================================================================
$  设计变量 1（E1）→ MAT1 卡片 1 的 E 字段
$  材料属性 = C0 + COEF1 * DESVAR1，此处 C0=0, COEF1=1，即直接映射
DVMREL1,  10,  MAT1, 1,  E,     49000.0, 91000.0,  0.0                 +
+,         1,  1.0

$  设计变量 2（RHO1）→ MAT1 卡片 1 的 RHO 字段
DVMREL1,  11,  MAT1, 1,  RHO,   2160.0,  3240.0,   0.0                 +
+,         2,  1.0

$=======================================================================
$  响应定义 - 固有频率
$  DRESP1, ID, LABEL,   RTYPE, PTYPE, REGION, ATTA, ATTB, ATT1, ATT2
$  FREQ 响应：ATT1 为阶次编号
$=======================================================================
DRESP1,  1,  FREQ_M1,  FREQ,  ,  ,  ,  ,  1
DRESP1,  2,  FREQ_M2,  FREQ,  ,  ,  ,  ,  2
DRESP1,  3,  FREQ_M3,  FREQ,  ,  ,  ,  ,  3

$=======================================================================
$  响应定义 - 振型分量（传感器节点，Z 方向 = 分量 3）
$  DRESP1, ID, LABEL,   RTYPE, PTYPE, REGION, ATTA, ATTB, ATT1, ...
$  模态 DISP 响应：ATTA=方向分量(1-6), ATTB=阶次编号, ATT1+=节点 ID
$
$  注意：ATTB（阶次编号）对应 EIGRL 求解后的模态序号（按频率升序排列）
$=======================================================================
$  --- 第 1 阶振型，传感器节点 101~105，Z 方向 ---
DRESP1,  101, PH1_N101, DISP,  ,  10,  3,  1,  101
DRESP1,  102, PH1_N102, DISP,  ,  10,  3,  1,  102
DRESP1,  103, PH1_N103, DISP,  ,  10,  3,  1,  103
DRESP1,  104, PH1_N104, DISP,  ,  10,  3,  1,  104
DRESP1,  105, PH1_N105, DISP,  ,  10,  3,  1,  105

$  --- 第 2 阶振型 ---
DRESP1,  201, PH2_N101, DISP,  ,  10,  3,  2,  101
DRESP1,  202, PH2_N102, DISP,  ,  10,  3,  2,  102
DRESP1,  203, PH2_N103, DISP,  ,  10,  3,  2,  103
DRESP1,  204, PH2_N104, DISP,  ,  10,  3,  2,  104
DRESP1,  205, PH2_N105, DISP,  ,  10,  3,  2,  105

$  --- 第 3 阶振型 ---
DRESP1,  301, PH3_N101, DISP,  ,  10,  3,  3,  101
DRESP1,  302, PH3_N102, DISP,  ,  10,  3,  3,  102
DRESP1,  303, PH3_N103, DISP,  ,  10,  3,  3,  103
DRESP1,  304, PH3_N104, DISP,  ,  10,  3,  3,  104
DRESP1,  305, PH3_N105, DISP,  ,  10,  3,  3,  105

$  --- Dummy 目标函数响应（指向 FREQ_M1）---
DRESP1,  901, OBJ_DUMMY, FREQ, ,  ,  ,  ,  1

$=======================================================================
$  特征值求解参数
$  EIGRL, SID, V1, V2, ND, MSGLVL, MAXSET, SHFSCL, NORM
$  NORM=MASS：质量归一化（与振型灵敏度推导一致，必须使用此选项）
$=======================================================================
EIGRL,  100,  0.0,  2000.0,  10,  0,  ,  ,  MASS

$=======================================================================
$  结构模型（示意，替换为实际模型卡片）
$=======================================================================
$  边界条件
SPC1,   1,  123456,  1, 2, 3    $ 固定节点 1, 2, 3 的所有自由度

$  材料属性（MAT1 ID=1，对应 DVMREL1 中的 MID=1）
$  MAT1, MID,  E,       G,    NU,   RHO,    A,   TREF, GE
MAT1,  1,   70000.0,  ,  0.33,  2700.0

$  属性卡片（PSHELL/PSOLID 等，视模型类型而定）
$  PSHELL, 1, 1, 2.5, 1, , 1
$  ...

$  节点和单元卡片（省略）
$  GRID, 101, , x, y, z
$  ...

ENDDATA
```

---

### 3.3 关于 DESMAX 的设置策略

| `DESMAX` 值 | 行为 |
|-------------|------|
| `0` | 仅做初始分析与灵敏度计算，**不**更新设计变量（推荐用于灵敏度提取） |
| `1` | 做一次设计循环（计算灵敏度 + 一步近似优化）后停止 |
| `N` | 执行 N 次设计循环（完整 SOL200 优化流程，不推荐，由 Python 控制更灵活） |

---

### 3.4 灵敏度矩阵输出格式（.pch 文件）

`DSAPRT(END, FORMATTED, EXPORT)` 会将灵敏度矩阵追加到 `.pch` 文件，格式示例：

```
$SENSITIVITY MATRIX (dR/dp)
$
$RESPONSE   ID=101  LABEL=PH1_N101   RTYPE=DISP   VALUE=  3.2541E-03
$
 DESVAR         ID        LABEL            VALUE       SENSITIVITY
                 1       E1_ALU        7.0000E+04      -4.6512E-08
                 2      RHO1_ALU       2.7000E+03       2.1034E-07
$
$RESPONSE   ID=1    LABEL=FREQ_M1    RTYPE=FREQ   VALUE=  1.2345E+02
$
 DESVAR         ID        LABEL            VALUE       SENSITIVITY
                 1       E1_ALU        7.0000E+04       8.7231E-04
                 2      RHO1_ALU       2.7000E+03      -4.5621E-02
...
```

---

## 四、Python 实现

### 4.1 灵敏度矩阵读取与组装

```python
import numpy as np
import re
from pyNastran.op2.op2 import OP2

# ============================================================
# 模块 1：读取 SOL103/SOL200 的 OP2 文件，提取振型和频率
# ============================================================
def read_modes_from_op2(op2_path: str, sensor_nodes: list, dof: int = 3):
    """
    从 OP2 文件读取振型矩阵和频率向量。
    
    Parameters
    ----------
    op2_path    : OP2 文件路径
    sensor_nodes: 传感器节点 ID 列表
    dof         : 提取的自由度方向（1=TX, 2=TY, 3=TZ, 4=RX, 5=RY, 6=RZ）
    
    Returns
    -------
    phi_sim : shape (N_s, N_modes)，振型矩阵，列为各阶振型
    freqs   : shape (N_modes,)，固有频率向量（Hz）
    """
    op2 = OP2(debug=False)
    op2.read_op2(op2_path)
    
    # 提取法模结果（子工况 10）
    eigenvectors = op2.eigenvectors[10]  # subcase_id = 10
    eigenvalues  = op2.eigenvalues['LAMA']  # 或对应的 mode_table
    
    freqs = eigenvalues.freqs  # Hz
    n_modes = len(freqs)
    n_sensors = len(sensor_nodes)
    phi_sim = np.zeros((n_sensors, n_modes))
    
    dof_map = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}  # Nastran DOF → 索引
    dof_idx = dof_map[dof]
    
    for mode_idx in range(n_modes):
        for si, node_id in enumerate(sensor_nodes):
            # data shape: (n_modes, n_nodes, 6)
            phi_sim[si, mode_idx] = eigenvectors.data[mode_idx, 
                                        eigenvectors.node_gridtype[:, 0].tolist().index(node_id), 
                                        dof_idx]
    return phi_sim, freqs


# ============================================================
# 模块 2：解析 .pch 文件，提取灵敏度矩阵
# ============================================================
def parse_sensitivity_pch(pch_path: str, 
                           dresp_ids: list, 
                           n_desvar: int) -> dict:
    """
    解析 SOL200 punch 文件中的灵敏度数据。
    
    Returns
    -------
    sensitivity : dict，键为 DRESP1 的 ID，值为 shape (n_desvar,) 的灵敏度向量
    """
    sensitivity = {}
    current_resp_id = None
    
    with open(pch_path, 'r') as f:
        for line in f:
            # 匹配响应行
            m = re.match(r'\$RESPONSE\s+ID=(\d+)', line)
            if m:
                current_resp_id = int(m.group(1))
                if current_resp_id in dresp_ids:
                    sensitivity[current_resp_id] = np.zeros(n_desvar)
                continue
            
            # 匹配设计变量灵敏度行
            if current_resp_id in dresp_ids:
                m = re.match(r'\s+(\d+)\s+\S+\s+[\d.E+-]+\s+([\d.E+-]+)', line)
                if m:
                    desvar_id = int(m.group(1))
                    dsens     = float(m.group(2))
                    sensitivity[current_resp_id][desvar_id - 1] = dsens
    
    return sensitivity


# ============================================================
# 模块 3：将振型分量灵敏度汇总为振型向量灵敏度张量
# ============================================================
def build_mode_shape_sensitivity(sensitivity_dict: dict,
                                 dresp_ids_by_mode: list,
                                 n_sensors: int, 
                                 n_desvar: int) -> np.ndarray:
    """
    从分散的 DRESP1 灵敏度组装为振型灵敏度张量。
    
    Parameters
    ----------
    dresp_ids_by_mode : list of list，外层索引为阶次（0-based），
                        内层为该阶次对应的 DRESP1 ID（按传感器顺序排列）
    
    Returns
    -------
    dPhi_dp : shape (N_modes, N_sensors, N_desvar)
              dPhi_dp[j, r, k] = ∂φ_{sim,j}^{(r)} / ∂p_k
    """
    n_modes = len(dresp_ids_by_mode)
    dPhi_dp = np.zeros((n_modes, n_sensors, n_desvar))
    
    for j, resp_ids in enumerate(dresp_ids_by_mode):
        for r, resp_id in enumerate(resp_ids):
            if resp_id in sensitivity_dict:
                dPhi_dp[j, r, :] = sensitivity_dict[resp_id]
    
    return dPhi_dp


# ============================================================
# 模块 4：计算 MAC 矩阵
# ============================================================
def compute_mac_matrix(phi_exp: np.ndarray, phi_sim: np.ndarray) -> np.ndarray:
    """
    计算 MAC 矩阵。
    
    Parameters
    ----------
    phi_exp : shape (N_s, N_exp)
    phi_sim : shape (N_s, N_sim)
    
    Returns
    -------
    MAC : shape (N_exp, N_sim)
    """
    # 分子：(phi_exp.T @ phi_sim)^2，shape (N_exp, N_sim)
    numerator = (phi_exp.T @ phi_sim) ** 2
    
    # 分母：每阶范数的外积
    norm_exp = np.sum(phi_exp ** 2, axis=0)  # shape (N_exp,)
    norm_sim = np.sum(phi_sim ** 2, axis=0)  # shape (N_sim,)
    denominator = np.outer(norm_exp, norm_sim)  # shape (N_exp, N_sim)
    
    return numerator / (denominator + 1e-15)


# ============================================================
# 模块 5：符号对齐
# ============================================================
def align_sign(phi_exp: np.ndarray, 
               phi_sim: np.ndarray, 
               dPhi_dp: np.ndarray,
               pairs: list) -> tuple:
    """
    对匹配对的仿真振型执行符号对齐，同步调整灵敏度矩阵符号。
    
    Parameters
    ----------
    pairs     : list of (i, j)，匹配对（试验阶次 i，仿真阶次 j）
    phi_sim   : shape (N_s, N_sim)，in-place 修改
    dPhi_dp   : shape (N_modes, N_s, N_desvar)，in-place 修改
    
    Returns
    -------
    sign_flags : 各仿真阶次的符号（+1 或 -1）
    """
    phi_sim_aligned = phi_sim.copy()
    dPhi_dp_aligned = dPhi_dp.copy()
    sign_flags = np.ones(phi_sim.shape[1])
    
    for (i, j) in pairs:
        cross = phi_exp[:, i] @ phi_sim[:, j]
        if cross < 0:
            phi_sim_aligned[:, j] *= -1
            dPhi_dp_aligned[j, :, :] *= -1
            sign_flags[j] = -1
    
    return phi_sim_aligned, dPhi_dp_aligned, sign_flags


# ============================================================
# 模块 6：MAC 灵敏度计算（核心公式实现）
# ============================================================
def compute_mac_sensitivity(phi_exp: np.ndarray,
                             phi_sim: np.ndarray,
                             dPhi_dp: np.ndarray,
                             pairs: list) -> np.ndarray:
    """
    计算各匹配对 MAC 对设计参数的灵敏度。
    
    公式：∂MAC_ij/∂p_k = g_ij^T · (∂φ_sim_j/∂p_k)
    其中  g_ij = (2*a_ij)/(b_i*c_j) * (φ_exp_i - (a_ij/c_j)*φ_sim_j)
    
    Parameters
    ----------
    phi_exp  : shape (N_s, N_exp)
    phi_sim  : shape (N_s, N_sim)，已完成符号对齐
    dPhi_dp  : shape (N_sim, N_s, N_desvar)
    pairs    : list of (i, j)
    
    Returns
    -------
    dMAC_dp  : shape (N_pairs, N_desvar)
    """
    n_pairs  = len(pairs)
    n_desvar = dPhi_dp.shape[2]
    dMAC_dp  = np.zeros((n_pairs, n_desvar))
    
    for n, (i, j) in enumerate(pairs):
        psi_e = phi_exp[:, i]           # shape (N_s,)
        psi_s = phi_sim[:, j]           # shape (N_s,)
        
        a_ij = psi_e @ psi_s            # 标量
        b_i  = psi_e @ psi_e            # 标量（试验振型范数²）
        c_j  = psi_s @ psi_s            # 标量（仿真振型范数²）
        
        # MAC 梯度向量 g_ij，shape (N_s,)
        g_ij = (2.0 * a_ij / (b_i * c_j)) * (psi_e - (a_ij / c_j) * psi_s)
        
        # ∂MAC_ij/∂p_k = g_ij^T · (∂φ_j/∂p_k)，shape (N_desvar,)
        # dPhi_dp[j]: shape (N_s, N_desvar)
        dMAC_dp[n, :] = g_ij @ dPhi_dp[j]    # (N_s,) @ (N_s, N_desvar)
    
    return dMAC_dp


# ============================================================
# 模块 7：目标函数及其梯度
# ============================================================
def objective_and_gradient(phi_exp, phi_sim, freqs_exp, freqs_sim,
                            dPhi_dp, dfreq_dp, pairs,
                            alpha=1.0, beta=0.1):
    """
    计算目标函数值和梯度。
    
    Returns
    -------
    J     : float，目标函数值
    dJ_dp : shape (N_desvar,)，目标函数梯度
    """
    mac_matrix = compute_mac_matrix(phi_exp, phi_sim)
    dMAC_dp    = compute_mac_sensitivity(phi_exp, phi_sim, dPhi_dp, pairs)
    
    n_desvar = dPhi_dp.shape[2]
    J     = 0.0
    dJ_dp = np.zeros(n_desvar)
    
    for n, (i, j) in enumerate(pairs):
        mac_val  = mac_matrix[i, j]
        freq_err = (freqs_sim[j] - freqs_exp[i]) / freqs_exp[i]
        
        # 目标函数值
        J += alpha * (1.0 - mac_val) + beta * freq_err ** 2
        
        # 梯度：MAC 项
        dJ_dp -= alpha * dMAC_dp[n, :]
        
        # 梯度：频率项
        dJ_dp += 2.0 * beta * freq_err / freqs_exp[i] * dfreq_dp[j, :]
    
    return J, dJ_dp
```

---

### 4.2 优化主循环

```python
import subprocess
import shutil
from scipy.optimize import minimize

# ============================================================
# BDF 参数更新工具
# ============================================================
def update_bdf_parameters(bdf_template_path: str, 
                           bdf_output_path: str,
                           param_values: np.ndarray,
                           desvar_ids: list):
    """
    将新设计变量值写入 BDF 文件（替换 DESVAR 卡片初值）。
    """
    with open(bdf_template_path, 'r') as f:
        content = f.read()
    
    for k, (desvar_id, val) in enumerate(zip(desvar_ids, param_values)):
        # 通过正则表达式更新 DESVAR 初值（第 4 字段）
        pattern = rf'(DESVAR\s*,\s*{desvar_id}\s*,\s*\w+\s*,\s*)([\d.E+\-]+)'
        replacement = rf'\g<1>{val:.6E}'
        content = re.sub(pattern, replacement, content)
    
    with open(bdf_output_path, 'w') as f:
        f.write(content)


def run_nastran(bdf_path: str, nastran_exe: str = 'nastran'):
    """提交 Nastran 作业并等待完成。"""
    result = subprocess.run(
        [nastran_exe, bdf_path, 'scr=yes', 'delete=f06,log'],
        capture_output=True, text=True, timeout=3600
    )
    if result.returncode != 0:
        raise RuntimeError(f'Nastran failed:\n{result.stderr}')


# ============================================================
# 优化主函数
# ============================================================
def mac_model_updating(
    bdf_template:   str,
    phi_exp:        np.ndarray,   # shape (N_s, N_exp)
    freqs_exp:      np.ndarray,   # shape (N_exp,)
    sensor_nodes:   list,
    initial_params: np.ndarray,   # shape (N_desvar,)
    param_bounds:   list,         # list of (lb, ub) tuples
    desvar_ids:     list,
    dresp_ids_by_mode: list,      # DRESP1 IDs，按阶次 × 传感器排列
    freq_dresp_ids: list,         # 频率 DRESP1 IDs，按阶次排列
    dof:            int  = 3,
    alpha:          float = 1.0,
    beta:           float = 0.1,
    max_iter:       int  = 50,
    nastran_exe:    str  = 'nastran',
    workdir:        str  = './sol200_work'
):
    import os
    os.makedirs(workdir, exist_ok=True)
    
    params = initial_params.copy()
    n_desvar = len(params)
    iteration = [0]
    history = {'J': [], 'params': [], 'mac_diag': []}
    
    def nastran_solve_and_sensitivity(p):
        """
        给定参数 p，运行 SOL200，返回振型、频率及其灵敏度。
        """
        bdf_path = os.path.join(workdir, f'model_iter{iteration[0]:03d}.bdf')
        op2_path = bdf_path.replace('.bdf', '.op2')
        pch_path = bdf_path.replace('.bdf', '.pch')
        
        # 1. 更新 BDF 参数
        update_bdf_parameters(bdf_template, bdf_path, p, desvar_ids)
        
        # 2. 运行 SOL200（DESMAX=0 仅计算灵敏度）
        run_nastran(bdf_path, nastran_exe)
        
        # 3. 读取振型和频率
        phi_sim, freqs_sim = read_modes_from_op2(op2_path, sensor_nodes, dof)
        
        # 4. 解析灵敏度
        n_modes  = len(dresp_ids_by_mode)
        n_sensors = len(sensor_nodes)
        all_dresp_ids = [rid for mode_ids in dresp_ids_by_mode for rid in mode_ids]
        all_dresp_ids += freq_dresp_ids
        
        sens_dict = parse_sensitivity_pch(pch_path, all_dresp_ids, n_desvar)
        
        dPhi_dp   = build_mode_shape_sensitivity(sens_dict, dresp_ids_by_mode, 
                                                  n_sensors, n_desvar)
        dfreq_dp  = np.zeros((n_modes, n_desvar))
        for j, rid in enumerate(freq_dresp_ids):
            if rid in sens_dict:
                dfreq_dp[j, :] = sens_dict[rid]
        
        return phi_sim, freqs_sim, dPhi_dp, dfreq_dp
    
    def cost_function(p):
        nonlocal params
        params = p
        
        phi_sim, freqs_sim, dPhi_dp, dfreq_dp = nastran_solve_and_sensitivity(p)
        
        # 确定匹配对（MAC 最大值匹配）
        mac_raw = compute_mac_matrix(phi_exp, phi_sim)
        n_exp = phi_exp.shape[1]
        pairs = []
        for i in range(n_exp):
            j = np.argmax(mac_raw[i, :])
            pairs.append((i, j))
        
        # 符号对齐
        phi_sim_a, dPhi_dp_a, _ = align_sign(phi_exp, phi_sim, dPhi_dp, pairs)
        
        # 计算目标函数和梯度
        J, dJ = objective_and_gradient(phi_exp, phi_sim_a, freqs_exp, freqs_sim,
                                        dPhi_dp_a, dfreq_dp, pairs, alpha, beta)
        
        # 记录历史
        mac_now = compute_mac_matrix(phi_exp, phi_sim_a)
        mac_diag = [mac_now[i, j] for i, j in pairs]
        history['J'].append(J)
        history['params'].append(p.copy())
        history['mac_diag'].append(mac_diag)
        
        print(f"Iter {iteration[0]:3d} | J={J:.6f} | "
              f"MAC_min={min(mac_diag):.4f} | "
              f"Freq_err={np.max(np.abs(freqs_sim[[j for _,j in pairs]] - freqs_exp) / freqs_exp)*100:.2f}%")
        
        iteration[0] += 1
        return J, dJ
    
    # 使用 L-BFGS-B（支持梯度输入，效率高）
    result = minimize(
        cost_function,
        initial_params,
        method='L-BFGS-B',
        jac=True,          # cost_function 同时返回 J 和 ∂J/∂p
        bounds=param_bounds,
        options={'maxiter': max_iter, 'ftol': 1e-8, 'gtol': 1e-6, 'disp': True}
    )
    
    return result, history
```

---

## 五、灵敏度验证方法（必做步骤）

在进入完整优化循环前，**必须验证 MAC 灵敏度的正确性**，否则优化方向将完全错误。

### 5.1 有限差分对比

```python
def verify_mac_sensitivity(phi_exp, phi_sim, dPhi_dp, pairs, 
                            bdf_path, nastran_exe, sensor_nodes, dof,
                            param_idx=0, h=1e-4):
    """
    对第 param_idx 个设计变量，用有限差分验证 MAC 灵敏度。
    """
    # 解析灵敏度（已由 SOL200 提供）
    mac_matrix_0 = compute_mac_matrix(phi_exp, phi_sim)
    phi_sim_a, dPhi_dp_a, _ = align_sign(phi_exp, phi_sim.copy(), 
                                           dPhi_dp.copy(), pairs)
    dMAC_dp = compute_mac_sensitivity(phi_exp, phi_sim_a, dPhi_dp_a, pairs)
    
    # 有限差分：摄动第 param_idx 个设计变量
    # （需要重新运行 SOL103）
    # ... 运行 +h 和 -h 的两个 SOL103 ...
    # phi_sim_plus, freqs_plus  = run_sol103(params + h*e_k)
    # phi_sim_minus, freqs_minus = run_sol103(params - h*e_k)
    # mac_plus  = compute_mac_matrix(phi_exp, phi_sim_plus)
    # mac_minus = compute_mac_matrix(phi_exp, phi_sim_minus)
    # dMAC_dp_fd = (mac_plus - mac_minus) / (2*h)
    
    print("=== MAC 灵敏度验证（解析 vs. 有限差分）===")
    print(f"{'匹配对':>10} {'解析值':>15} {'有限差分':>15} {'相对误差':>12}")
    for n, (i, j) in enumerate(pairs):
        analytic = dMAC_dp[n, param_idx]
        # fd_val = dMAC_dp_fd[i, j]
        # err = abs(analytic - fd_val) / (abs(fd_val) + 1e-15)
        print(f"  ({i},{j})    {analytic:15.6E}")  # 加入 fd_val 和 err 后完善
```

### 5.2 验收标准

| 检查项 | 合格标准 |
|--------|----------|
| 解析 $\partial\text{MAC}/\partial p_k$ vs. 中心差分 | 相对误差 $< 1\%$ |
| 解析 $\partial f_j/\partial p_k$ vs. SOL200 输出 | 相对误差 $< 0.1\%$ |
| 目标函数梯度方向验证 | 沿负梯度方向小步长后 $J$ 下降 |

---

## 六、工程实践建议

### 6.1 DRESP1 数量的控制

传感器数 × 阶数 = DRESP1 数目。例如 30 个传感器 × 5 阶 = 150 个 DRESP1。Nastran SOL200 对大量 DRESP1 的处理效率仍然可接受，但建议：

- 只定义**实际参与修正的阶次**的振型分量响应
- 若传感器为多方向（X、Y、Z），则三个方向分别定义 DRESP1

### 6.2 模态追踪（避免阶次交叉）

在优化迭代中，参数变化可能导致仿真振型的频率顺序改变（阶次交叉）。处理方法：

```python
def track_modes_across_iterations(phi_exp, phi_sim_prev, phi_sim_curr):
    """
    用 MAC 矩阵在相邻迭代之间追踪模态对应关系，
    返回当前振型列重排后的索引。
    """
    n_modes = phi_sim_prev.shape[1]
    mac = compute_mac_matrix(phi_sim_prev, phi_sim_curr)
    reorder = np.argmax(mac, axis=1)  # prev_j → curr_j
    return reorder
```

### 6.3 质量归一化的统一处理

SOL200 输出的振型为**质量归一化振型**（$\boldsymbol{\phi}^T \mathbf{M} \boldsymbol{\phi} = \mathbf{I}$），而试验振型通常为**最大分量归一化**（$\max|\boldsymbol{\phi}| = 1$）。MAC 对此不敏感（分母中有范数），但为保证灵敏度公式的一致性，建议：

- 从 OP2 提取仿真振型后，立即对其做最大分量归一化（与试验一致）
- 同步对 $\partial\boldsymbol{\phi}/\partial p_k$ 做相同比例的缩放

### 6.4 参数归一化（提高优化收敛性）

将设计变量归一化到 $[0, 1]$ 区间，避免 $E$（量级 $10^4$ MPa）和 $\rho$（量级 $10^3$ kg/m³）数量级差异导致梯度方向偏斜：

```python
p_normalized = (p - lb) / (ub - lb)  # 优化器使用归一化变量
p_physical   = lb + p_normalized * (ub - lb)  # 传入 Nastran 的物理量
```

### 6.5 建议的参数初值策略

不要直接使用标称值作为初值，推荐：
1. 先做**频率匹配**（仅最小化频率残差，计算快），将参数调到频率基本对齐
2. 再以此为初值，引入 MAC 项做**全目标修正**

---

## 七、完整执行检查清单

```
□ BDF 模型检查
  □ EIGRL 使用 NORM=MASS（质量归一化）
  □ DESVAR 初值与当前 MAT1 卡片一致
  □ DVMREL1 的 MID 与对应 MAT1 的 ID 一致
  □ DRESP1 DISP 的 ATTB 字段为对应阶次编号（1-based）
  □ DRESP1 DISP 的 ATT1+ 为传感器节点 ID（与 Python 列表顺序一致）
  □ DOPTPRM DESMAX=0

□ 灵敏度验证
  □ 至少对 1-2 个设计变量做有限差分验证
  □ MAC 灵敏度相对误差 < 1%
  □ 频率灵敏度相对误差 < 0.1%

□ 优化前检查
  □ 初始 MAC 对角线均 > 0.5（匹配合理）
  □ 初始频率误差 < 20%（初值合理）
  □ 沿负梯度方向小步长验证目标函数下降

□ 优化过程监控
  □ 每次迭代输出 MAC 对角值和频率误差
  □ 发现阶次交叉时重新确认匹配对
  □ 参数变化超过 10% 时重新运行 SOL200 更新灵敏度（线性近似失效）
```
