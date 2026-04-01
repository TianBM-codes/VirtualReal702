# Layer 1 使用说明：abaqus_dump.py + l1_pack.py

Layer 1 分两个阶段运行，原因是 **Abaqus Python 环境没有 h5py**：

```
阶段 1: abaqus_dump.py   (abaqus python，有 odbAccess)  → l1_raw/（npy + JSON）
阶段 2: l1_pack.py       (普通 Python 3，有 h5py)       → l1/ HDF5 + manifest.db
```

---

## 运行方式

```bash
# 阶段 1（需 Abaqus license）
abaqus python abaqus_dump.py --odb /path/to/model.odb --out /data/<odb_id>

# 阶段 2（普通 Python，需 h5py）
python l1_pack.py --workspace /data/<odb_id>

# 调试时保留中间文件
python l1_pack.py --workspace /data/<odb_id> --keep-raw
```

退出码：`0` = 成功，`1` = 出错（错误打印到 stdout）。

---

## 依赖

| 脚本 | 运行环境 | 依赖 |
|------|---------|------|
| `abaqus_dump.py` | `abaqus python` | `numpy`（内置）、`odbAccess`（内置）、`os/json`（标准库） |
| `l1_pack.py` | Python 3.8+ | `h5py`、`numpy` |

安装 l1_pack.py 依赖：
```bash
pip install h5py numpy
```

---

## 输出结构

```
/data/<odb_id>/
│
├── l1_raw/                          ← 阶段 1 输出，阶段 2 完成后自动删除
│   ├── dump_meta.json               # 全部元数据（步骤、帧、实例、集合信息）
│   ├── assembly/
│   │   ├── instances/<inst>/transform.npy, part_name.txt
│   │   └── asmsets/<set>/<inst>/node_labels.npy, elem_labels.npy
│   ├── geom/<inst>/
│   │   ├── node_labels.npy, node_coords.npy
│   │   ├── elems/<etype>/labels.npy, conn.npy, face_*.npy
│   │   ├── highorder/<etype>/conn_full.npy, midnode_indices.npy
│   │   ├── isets/node_sets/<name>.npy, elem_sets/<name>.npy
│   │   ├── sections.json, materials.json
│   ├── sets/asmsets/, sets/partsets/
│   └── results/<step>__<field>/
│       ├── meta.json
│       └── <inst>/<position>/[<etype>/]
│           ├── labels.npy, ip_labels.npy, sp_labels.npy
│           └── f0000.npy, f0001.npy, ...   ← 每帧一个文件
│
├── manifest.db                      ← 阶段 2 输出（SQLite）
└── l1/
    ├── assembly.h5
    ├── geometry/<inst>.h5
    ├── geometry/<inst>_highorder.h5  ← 仅高阶单元
    ├── sets/sets.h5
    └── results/<step>__<field>.h5
```

---

## HDF5 文件结构

### assembly.h5

```
/instances/<inst_name>/
    part_name    string
    transform    [4, 4]  float64    # 局部→全局齐次变换矩阵

/assembly_sets/<set_name>/<inst_name>/
    node_labels  [K]  int32         # ODB 原始 label，升序
    elem_labels  [L]  int32
```

### geometry/\<inst\>.h5

```
/nodes/
    labels  [N]     int32          # 升序（供 searchsorted）
    coords  [N, 3]  float64        # Instance 局部坐标

/elements/<elem_type>/
    labels         [M]            int32
    conn           [M, n_corner]  int32   # row index（不是 label）
    face_elem_idx  [Mf]           int32   # 面对应 element row
    face_seq       [Mf]           uint8   # 面编号 S1=1, S2=2, ...
    face_node_conn [Mf, max_fn]   int32   # 面节点 row（-1=未使用）
    face_normals   [Mf, 3]        float32 # 外法向（局部坐标）

/sections/<name>/ attrs: element_set, material_name, type, thickness
/materials/<name>/ attrs: type; dataset: elastic_table
/instance_sets/node_sets/<name>   [K] int32
/instance_sets/element_sets/<name>[L] int32
```

> **`conn` 存 row index**：`conn[i, j]` 是第 i 个单元第 j 个角节点在
> `/nodes/labels` 数组中的下标（0-based），不是 label 本身。
> L3 取坐标：`node_coords[conn[i]]`，无需再次查找。

### geometry/\<inst\>\_highorder.h5

```
/elements/<elem_type>/
    labels          [M]        int32
    conn_full       [M, n_all] int32   # 全部节点的 node_label（含中间节点）
    midnode_indices [n_mid]    uint8   # conn_full 中哪些列是中间节点
```

### sets/sets.h5

```
/part_sets/<part>/node_sets/<name>    [K] int32
/part_sets/<part>/element_sets/<name> [L] int32
/assembly_sets/<set>/<inst>/node_labels  [K] int32
/assembly_sets/<set>/<inst>/elem_labels  [L] int32
```

### results/\<step\>\_\_\<field\>.h5

```
/meta/ step_name, field_name, field_description, components[ncomp], invariants[ninv]
/frame_index/ frame_values[F] float64, descriptions[F] string

/NODAL/<inst>/
    labels      [N]            int32
    data        [F, N, ncomp]  float32
    invariants  [F, N, ninv]   float32  # 当前填 NaN，L3 按需重算

/INTEGRATION_POINT/<inst>/<elem_type>/
    labels    [M]                       int32
    ip_labels [n_ip]                    int32
    data      [F, M, n_ip, ncomp]       float32  # 实体单元
    # 壳单元（有截面点）额外：
    sp_labels [n_sp]                    int32
    data      [F, M, n_sp, n_ip, ncomp] float32

/ELEMENT_NODAL/<inst>/<elem_type>/
    labels  [M]                    int32
    data    [F, M, n_enodes, ncomp] float32
```

> **`h5_path` 不含 `/data`**：`result_blocks.h5_path` 存 Instance 组路径
> （如 `/NODAL/Part-1-1`）。L3 查询时拼接：`h5_path + '/data'`
> 或 `h5_path + '/invariants'`。

---

## manifest.db 表速查

| 表 | 主键 | 关键字段 |
|----|------|---------|
| `instances` | instance_name | geom_path, node_count, bbox_min/max |
| `element_type_dist` | (instance_name, elem_type) | count, n_corner_nodes, n_faces |
| `steps` | step_name | procedure, num_frames |
| `frames` | (step_name, frame_idx) | frame_value, description |
| `result_files` | (step_name, field_name) | file_path, components, invariants, val_min/max |
| `result_blocks` | (step_name, field_name, instance_name, position, elem_type) | h5_path, n_entities, n_ip, n_sp |
| `node_sets` | (set_name, instance_name) | set_scope, h5_path, node_count |
| `element_sets` | (set_name, instance_name) | set_scope, h5_path, elem_count |

---

## 已知限制与 Blockers

### Blocker A：壳截面点属性名（高优先级）

`abaqus_dump.py` 自动尝试三个可能属性名读取截面点标签：
`sectionPointNumber` / `sectionPoint` / `sectionPointNumbers`。

- **成功**：IP 数据存为 `[F, M, n_sp, n_ip, ncomp]`，`sp_labels` 存储 BOT/MID/TOP
- **失败**：退化为 `[F, M, n_ip, ncomp]`，Smooth 渲染降级

跑 PoC 脚本确认（见架构文档 Section 9.7）。

### Blocker B：Instance 变换矩阵属性名（中优先级）

`get_instance_transform()` 依次尝试 `localCsys.origin/xAxis/yAxis/zAxis`
和 `localCsys.translation/rotation`，均失败则打印 WARNING 并用单位矩阵。

跑 PoC 脚本确认属性名，失败时修改函数第一个 `try` 块中的属性名。

### 不变量存 NaN

ODB `bulkDataBlocks.data` 不含 Mises 等不变量，需单独调用 `getSubset`。
当前版本 `invariants` dataset 全填 NaN，L3 可按公式重算常用不变量：

```python
# Mises（von Mises 等效应力，6 分量 S11 S22 S33 S12 S13 S23）
s11, s22, s33 = data[..., 0], data[..., 1], data[..., 2]
s12, s13, s23 = data[..., 3], data[..., 4], data[..., 5]
mises = np.sqrt(0.5 * ((s11-s22)**2 + (s22-s33)**2 + (s33-s11)**2
                       + 6*(s12**2 + s13**2 + s23**2)))
```

---

## 单元类型支持

| code | 字符串 | 角节点 | 面数 | 高阶 |
|------|--------|--------|------|------|
| 0 | S3, S3R, S6 | 3 | 1 | S6 ✓ |
| 1 | S4, S4R, S4R5, S8R, S8R5 | 4 | 1 | S8R ✓ |
| 2 | C3D4, C3D4H | 4 | 4 | — |
| 3 | C3D6, C3D6H | 6 | 5 | — |
| 4 | C3D8, C3D8R, C3D8H, C3D8RH | 8 | 6 | — |
| 5 | C3D10, C3D10M, C3D10H | 4 | 4 | ✓ |
| 6 | C3D15, C3D15H | 6 | 5 | ✓ |
| 7 | C3D20, C3D20R, C3D20H, C3D20RH | 8 | 6 | ✓ |

未知类型打印 WARNING 后跳过，不影响其他类型处理。

---

## Abaqus 版本兼容性说明

代码在 **Abaqus 2024** 上验证，对旧版 API 做了以下兼容处理：

| 属性 / 行为 | 旧版（≤2022） | 新版（2023+） | 处理方式 |
|------------|-------------|-------------|---------|
| `OdbInstance.partName` | 有 | 无 | 依次尝试 `partName` → `instance.part.name` → 用 instance 名代替 |
| `OdbStep.procedureType` | 符号常量，如 `'STATIC_GENERAL'` | 无，改为 `step.procedure` 返回关键字字符串如 `'*STATIC'` | 两者都尝试，再做前缀匹配 |
| `FieldBulkData.integrationPointLabels` | 有 | 无 | 依次尝试 `integrationPointLabels` → `ipLabels` → 从数据形状推断 |
| `FieldBulkData.elementType` | 返回类型字符串 | 返回 `None` | 若为 `None`，用 `(单元数 × 分量数)` 自动生成 key，保证不同形状 block 分开存储 |
| Assembly set `ns.nodes` | 扁平序列，每个节点有 `instanceName` | 按 instance 分组的嵌套序列 | 检查 `ns.instances` 长度是否与 `ns.nodes` 一致，按对应关系拆分 |

如果遇到新的 `AttributeError`，通常是这类改名问题，照上表的处理思路加 fallback 即可。

---

## 常见问题

**Q: 阶段 2 中途失败，l1_raw/ 还在怎么办？**
A: 直接重新运行 `l1_pack.py` 即可（会覆盖已有 HDF5 和 manifest.db）。
阶段 1 不需要重跑。

**Q: 结果文件某些帧数据全为 0？**
A: 该帧 `f<idx>.npy` 文件不存在（该帧无此字段）。l1_pack.py 跳过写入，
dataset 默认值为 float32 的 0。查 `frames` 表确认 frame_value 是否合理。

**Q: `safe()` 函数把 `/` 替换成 `__`，HDF5 里集合名字跟原始 ODB 不一样？**
A: 是的。manifest.db 的 `h5_path` 字段存的就是 safe 后的路径，L3 通过
manifest.db 定位，不直接用原始名字拼路径，不影响查询。
