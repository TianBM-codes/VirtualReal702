# DSA 灵敏度场合并方案

## 背景

Abaqus DSA（设计灵敏度分析）输出的字段以参数为单位分散存储：`d_U_T1`、`d_U_T2`……每个字段覆盖的是对应参数（Tn）控制的响应节点集上的灵敏度值，彼此独立，不构成全局单元视图。

本方案将这些离散场合并为以"响应节点 × 分量"为键的全局元素场，便于可视化和后续分析。

---

## 核心概念

### 现有字段结构

每个 `d_U_Tk` 字段存储的是：**对参数 Tk 的扰动，响应节点集上各节点位移的变化率**。

```
d_U_Tk（存在 workspace H5 中）:
    shape: [num_frames, num_response_nodes, 3]
    labels: [node_A_label, node_B_label, ...]  ← 响应节点 labels
    data[frame, i, :] = [dU1/dTk, dU2/dTk, dU3/dTk]  ← 节点 i 处三个分量
```

字段名中的 `k` 是参数序号，由 INP 中 `*DESIGN VARIABLES` 声明的顺序决定。

### 参数与单元集的关系

每个参数 Tk（如厚度参数）通过 `*SHELL SECTION` 绑定一个 elset，控制一批单元。各参数控制的单元集**互不重叠**。

```
T1 → ELSET_A → [101, 102, 103]
T2 → ELSET_B → [201, 202]
T3 → ELSET_C → [301]
```

---

## 合并目标

对每个 `(响应节点 label n, 分量 c)` 组合，构建一个**全局元素场**：

- 字段名：`d_{n}_{Uc}_T`（例如 `d_999_U1_T`）
- 覆盖范围：所有参数 T1…TN 控制单元的并集
- 每个单元的值：该单元所属参数 Tk 对 `(节点 n, 分量 c)` 的灵敏度标量

```
d_999_U1_T:
    元素 101, 102, 103  →  d_U_T1[node=999, U1]   （同一标量，来自 T1）
    元素 201, 202       →  d_U_T2[node=999, U1]   （来自 T2）
    元素 301            →  d_U_T3[node=999, U1]   （来自 T3）
    其余单元            →  0 或 NaN
```

产出场总数：`响应节点数 × 3`

---

## 完整处理链路

### 第一阶段：INP 导入 & 工作区初始化

```
INP 文件
├─ *PARAMETER + *DESIGN VARIABLES
│       → MySQL t_mt_py_fem_selected_parameter
│         (parameter_name, set_name, part_name, set_scope, ...)
│         参数序号 k 由 design_order 字段决定
│
├─ *ELSET + *SHELL SECTION
│       → workspace manifest.db element_sets 表
│         (set_name, instance_name, h5_path)
│       → workspace sets.h5
│         element_sets/{instance_name}/{set_name} → int32 labels 数组
│
└─ *DESIGN RESPONSE
        → 响应节点集（存于 INP，解析时可知响应节点 labels）
```

### 第二阶段：Abaqus 求解

Abaqus 跑 DSA，输出 ODB，包含字段 `d_U_T1` … `d_U_TN`。

### 第三阶段：ODB 解析 → H5 打包

- `abaqus_dump.py`（Abaqus Python 2.7）：从 ODB 提取各字段到 `.npy`
- `l1_pack.py`（Python 3）：`.npy` → H5，更新 `manifest.db result_blocks`

每个字段在 H5 中：`[num_frames, num_response_nodes, num_components]`

### 第四阶段：参数序号对齐

`_build_dsa_parameter_row_map`：从字段名 `d_U_Tk` 提取序号 `k`，映射到 `t_mt_py_fem_selected_parameter` 第 k 条记录，取得 `set_name + part_name`。

### 第五阶段：合并（新增）

```
输入：
  - workspace 路径（含 manifest.db、sets.h5、各 d_U_Tk H5）
  - project_id（查 MySQL 参数表用）
  - step, frame, field_prefix（定位字段）

流程：
  1. 发现所有 d_U_Tk 字段（field_prefix 过滤）
  2. 读取任一字段的响应节点 labels → [node_A, node_B, ...]
  3. 建立参数序号 k → (set_name, part_name/instance_name) 映射
  4. 通过 manifest.db instances + element_sets + sets.h5
     得到每个 Tk 的 element labels
  5. for 每个 (响应节点 n, 分量 c) in 节点×3:
       初始化 全局数组 大小 = 全部元素并集，默认值 NaN
       for k = 1..N:
           scalar = d_U_Tk[frame, node_idx(n), c]
           全局数组[Tk 的 element_labels] = scalar
       存储字段 d_{n_label}_{Uc}_T

输出：
  - 字段列表：[ {field_name, response_node_label, component, element_count}, ... ]
  - 字段数据写入目标（接口层决定写回 workspace 还是直接返回）
```

---

## 数据依赖

| 数据 | 来源 | 访问方式 |
|------|------|---------|
| 参数序号 → set_name, part_name | MySQL `t_mt_py_fem_selected_parameter` | 外部函数传入（service 层不直接查 MySQL） |
| set_name + instance → element labels | workspace `manifest.db` + `sets.h5` | `ManifestRepo.get_element_set_labels` |
| part_name → instance_name | workspace `manifest.db` `instances` 表 | `ManifestRepo` 或直接查 |
| d_U_Tk 灵敏度值 | workspace H5 result 文件 | `manifest.db result_blocks` 定位 → h5py 读 |
| 响应节点 labels | 同上 H5 labels 数组 | 与灵敏度值一起读出 |

---

## 约束

- **src 层不访问 MySQL**：参数行数据由调用方从 MySQL 查好后以 `List[dict]` 形式传入合并函数
- **不重叠假设**：各参数单元集无交集，合并时无需处理冲突
- **响应节点集一致**：所有 `d_U_Tk` 的响应节点 labels 相同（同一次分析的同一响应定义）

---

## 前端云图展示

合并后的字段通过 `ExternalResultWriter.write_element()` 写入 workspace，自动注册进 `manifest.db` 的 `result_files` / `result_blocks` 表。

### 当前调用（未合并）

```
GET /api/odb/1001/results/frame-scalars
    ?instance=PART-1-1
    &step=Step-1
    &frame=0
    &field=d_U_T2           ← 一个参数一个字段
    &component_idx=1         ← 在 [U1,U2,U3] 里选第2个（U2）
    &mode=flat
    &result_group=sensitivity_batch_1_1111_1777190240
```

数据形状：`d_U_T2` 是 NODAL 场，实体是**响应节点**，shape = `[frames, 响应节点数, 3]`。
渲染结果：只有响应节点处有颜色（几个点），不是全局单元云图。

### 合并后调用

```
GET /api/odb/1001/results/frame-scalars
    ?instance=PART-1-1
    &step=Step-1
    &frame=0
    &field=d_{node_label}_U2_T   ← 已按响应节点+分量拆分的字段
    &component_idx=0              ← 字段只有一个分量，固定为 0
    &mode=flat
    &result_group=merged_dsa
```

数据形状：ELEMENT_NODAL 场，实体是**设计单元**，shape = `[frames, 设计单元数, 1, 1]`。
渲染结果：所有设计单元都有颜色，形成全局参数敏感度云图。

### 对比

| | 当前（未合并） | 合并后 |
|--|------|--------|
| `field` | `d_U_T2`（按参数区分） | `d_{node_label}_U2_T`（按响应节点+分量区分） |
| `component_idx` | 1（在3分量里选） | 0（字段本身已是单分量） |
| `result_group` | 原始解析的 result_group | `merged_dsa` |
| 覆盖范围 | 仅响应节点（几个点） | 全部设计单元 |

### L3 接口改动

`frame-scalars` 接口**不需要任何改动**，完全兼容。

前端改动：
1. `field` 名从 `d_U_T2` 换成 `d_{node_label}_U2_T`（由合并接口返回的字段列表中读取）
2. `component_idx` 固定传 `0`
3. `result_group` 改为 `merged_dsa`

---

## 代码修改范围

### 新增文件：1 个

**`src/l3/services/dsa_merge_service.py`**（纯 workspace 操作，不访问 MySQL）

```python
def merge_dsa_fields(
    *,
    workspace: str,
    step: str,
    frame: int,
    field_prefix: str,           # 如 "d_U_"
    instances: List[str],
    parameter_rows: List[dict],  # 由调用方从 MySQL 查好后传入
    result_group: str,           # 写入 workspace 时使用的 result_group 名
) -> List[dict]:
    """
    返回写入的字段列表：
    [{ field_name, response_node_label, component, element_count }, ...]
    """
```

内部依赖（均已存在，不修改）：
- `ManifestRepo.get_element_set_labels()` — set_name+instance → labels
- `ManifestRepo.get_instances_by_part_name()` — PART scope 展开（需新增，见下）
- `ExternalResultWriter.write_element()` — 写入合并后的元素场

### 修改文件：4 个

**① `src/l3/infra/manifest_repo.py`** — 新增 1 个方法

```python
def get_instances_by_part_name(self, part_name: str) -> List[str]:
    # SELECT instance_name FROM instances WHERE part_name = ?
```

**② `services/model_update/analysis/sensitivity_service.py`** — 新增 1 个公开函数

```python
def merge_dsa_sensitivity_fields(
    *,
    project_id: int,
    workspace: str,
    step: str,
    frame: int,
    field_prefix: str,
    instances: List[str],
    result_group: str,
) -> List[dict]:
    # 1. 调用 _load_project_optimization_parameters(project_id) 查 MySQL
    # 2. 传给 src.l3.services.dsa_merge_service.merge_dsa_fields
    # 3. 返回结果
```

**③ `webapi/models.py`** — 新增 1 个请求模型

```python
class SensitivityMergeFieldsRequest(BaseModel):
    project_id: int
    workspace: str
    step: str
    frame: int = 0
    field_prefix: str
    instances: List[str] = Field(default_factory=list)
    result_group: str = "merged_dsa"
    async_submit: bool = False
```

**④ `webapi/routers/sensitivity.py`** — 新增 1 个路由

```python
@router.post("/sensitivity/merge_fields")
async def sensitivity_merge_fields(
    request: Request, body: SensitivityMergeFieldsRequest
):
```

### 不改动文件

| 文件 | 原因 |
|------|------|
| `src/l3/services/external_result_writer.py` | 直接复用 `write_element()` |
| `src/l3/api/routes/results.py` | 现有 `frame-colors` 接口无需改动 |
| `src/l3/services/result_service.py` | 自动识别 ExternalResultWriter 写入的字段 |

### 改动汇总

```
新增：
  src/l3/services/dsa_merge_service.py          ← 核心合并逻辑（纯 workspace）

修改（仅追加，不改现有函数）：
  src/l3/infra/manifest_repo.py                 ← +get_instances_by_part_name()
  services/model_update/analysis/
    sensitivity_service.py                       ← +merge_dsa_sensitivity_fields()
                                                 ← run_sensitivity_inp_and_store 加 merge_fields 开关
  webapi/models.py                               ← +SensitivityMergeFieldsRequest
                                                 ← SensitivityRunAndStoreRequest 加 merge_fields / merge_result_group
  webapi/routers/sensitivity.py                  ← +POST /sensitivity/merge_fields
                                                 ← _run_and_store_kwargs 透传新字段

触碰文件总计：5 个（4 个追加，1 个新建）
```

---

## 调用链

### 方式一：集成调用（推荐，一次完成）

```json
POST /sensitivity/run_and_store
{
  "project_id": 1001,
  "input_inp": "/data/job.inp",
  "output_dir": "/data/out",
  "step": "Step-1",
  "instances": ["PART-1-1"],
  "field_prefix": "d_U_T",
  "response_component": "U",
  "position": "NODAL",
  "merge_fields": true,
  "merge_result_group": "merged_dsa"
}
```

返回值中包含 `merge_result`：

```json
{
  "workspace": "/data/1001",
  "step": "Step-1",
  "field_prefix": "d_U_T",
  "merge_result": [
    { "field_name": "d_999_U1_T", "response_node_label": 999, "component": "U1", "element_count": 120 },
    { "field_name": "d_999_U2_T", "response_node_label": 999, "component": "U2", "element_count": 120 },
    { "field_name": "d_999_U3_T", "response_node_label": 999, "component": "U3", "element_count": 120 }
  ]
}
```

### 方式二：分步调用

```
POST /sensitivity/run_and_store   （merge_fields 默认 false，行为不变）
    → 拿到 workspace、step、field_prefix、project_result_parse.result_group

POST /sensitivity/merge_fields
{
  "project_id": 1001,
  "workspace": "/data/1001",
  "step": "Step-1",
  "frame": 0,
  "field_prefix": "d_U_T",
  "instances": ["PART-1-1"],
  "result_group": "merged_dsa",
  "source_result_group": "sensitivity_batch_1_1111_1777190240"
}
```

### 前端取色（两种方式结果相同）

```
GET /api/odb/1001/results/frame-scalars
    ?instance=PART-1-1
    &step=Step-1
    &frame=0
    &field=d_999_U1_T      ← 从 merge_result 列表里取
    &component_idx=0        ← 合并场固定单分量
    &mode=flat
    &result_group=merged_dsa
```
