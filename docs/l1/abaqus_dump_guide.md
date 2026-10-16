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

脚本退出码：`0` = 成功，`1` = 出错。Windows 的 Abaqus 启动器可能把脚本失败返回为 `0`，
因此 runner 还要求日志出现 `ODB_DUMP_COMPLETE` 完成标记；缺标记或出现失败/内存错误时，
不会启动后续打包。`ODB_VERSION_ERROR` 仍进入原有的升级、重试流程。

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

# 特殊单元（非曲面）——详见 docs/Special-Element-Handling.md
/couplings/
    positions  [Nseg*2, 3]  float32  # 分布式耦合(DCOUP3D)展开的 (ref,leaf) 线段对
                                     # 每 2 个点 = 一段，前端 LineSegments 直接渲染
/elements_special/<elem_type>/       # 变长/未识别特殊单元的原始连接（存档，L2/L3 暂不读）
    labels        [M]      int32     # 单元 label
    conn_flat     [sum]    int32     # 扁平节点 label（注意：是 label，非 row）
    conn_offsets  [M+1]    int32     # CSR 偏移，第 i 个单元 = conn_flat[off[i]:off[i+1]]
```

> **线/点单元**（梁/桁架/CONN3D2/SPRING2/MASS/ROTARYI 等）走普通
> `/elements/<elem_type>/`：线只写 `labels`+`conn`（无 face 数组），点写
> `[M,1]` 的 `conn`。L2 `collect_lines`/`collect_points` 据此渲染。

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

## 装配集合导出复用

2026年10月15日增加：装配数据和统一集合数据都需要同一批按实例拆分的节点集、单元集。
导出装配时已经通过 Abaqus 取得这些编号并写入 `.npy`；后续集合步骤直接复制本次写出的
文件，避免再次逐个读取 Abaqus 中的节点和单元对象。部件集合仍按原流程导出。

目录、数组内容、编号顺序、数据类型及后续 HDF5 格式不变。复用仅接受当前调用完成的文件
清单；缺文件、单独调用集合导出或装配单元集来自之前的运行时，回退原读取流程。
清单只在本次导出进程内传递，不写入最终 `dump_meta.json`，不会把其他旧集合带入新导出。

## 大 ODB 的低内存处理（2026年10月16日）

原流程在发现结果结构时就重排整块结果；正式导出时，又同时保存多个重排数组和完整合并数组。
模型很大或机器剩余内存很少时，会在创建索引等临时数组时失败。报错中的“申请 73.8 MiB
失败”只是最后一次分配，并非整个提取过程仅需这点内存。

现在的流程是：

1. 发现结构只读取编号、积分点号、分量数，不创建完整的结果张量。
2. 每帧使用标准 `.npy` 磁盘映射文件，按最多 65,536 条原始结果记录进行编号对齐、填充。
   单元节点的局部顺序跨分块保留；截面点分别保存，缺失单元继续按原规则补零。
   行号、积分点行号、局部节点号直接分配为 `uint32`，不是先生成 `int64` 再转换。
   唯一且连续的编号直接用差值定位；有缺号的编号采用分批二分查找，辅助数组最多覆盖
   8,192 条查询。正常单元顺序用连续段计算局部节点号，交错顺序直接计数，不创建排序索引。
3. 派生不变量也按同样大小分块计算。积分点仍使用 Abaqus 的标量结果；单元节点/节点仍先取
   张量分量再计算不变量，保留原有应变剪切换算、无效位置的 NaN 和截面点口径。
4. 一帧写完并关闭映射后才替换正式文件。写入失败时清理本次 `.partial.npy`，不把半帧发布
   成正式文件；这些临时文件不改变后续 HDF5 结构。
5. 打包阶段也以只读映射读取 `.npy`，按约 4 MiB 数据块写 HDF5、计算有限值范围，避免再次
   整帧复制和整帧筛选有限值。

磁盘映射表示数组的数据放在文件里，由系统按需读写页面，减少必须同时放在内存中的副本。
这会增加磁盘读写，不能承诺解析更快；工作区必须有足够空间保存中间文件。
Abaqus 自身取得一个原始数据块仍可能需要较多内存，因此不保证任意模型在仅余 4 GB 时都能完成。

`uint32` 优化只涉及结果提取代码显式创建的临时索引与局部节点计数，不改变文件里的编号和结果
类型，也不改变 NumPy 底层的寻址位宽。数组下标仍从 0 开始，有效性仍单独判断，避免把第一个
数据误当作无效值。可表示的插入位置和计数超过 4,294,967,295 时明确报错，不允许溢出回绕。
单份 65,536 条索引由 512 KiB 降到 256 KiB；这是索引数组的大小，不是整个解析进程的降幅。
稀疏编号的分批查找、交错节点顺序的计数可能增加计算时间，实际峰值和耗时需实机测量。

遇到内存错误时，runner 会把项目或结果组标记为失败，日志明确提示“ODB 提取内存不足”，
保留最初的异常，不再追加误导性的 `dump_meta.json not found` 打包错误。
部署时需一起更新 `abaqus_dump.py`、`l1_pack.py` 和 `job_runner.py`，并重启 Web 服务和 runner，
确保完成标记的发送方、接收方使用同一版协议。
结果组的一般提取错误也不再被误报成“ODB 与几何不匹配”。释放其他程序/已加载模型的内存，
检查 Windows 虚拟内存后，从项目或结果组入口重新提交解析；提取未完成时不能单独重跑打包。

回归测试代码：`tests/test_l1_odb_bounded_memory.py`。按仓库约定，本次只编写测试、进行静态检查，
不在 Codex 所在机器运行测试。开发者拉取后执行：

```bash
python -m pytest tests/test_l1_odb_bounded_memory.py -v
```

2026年10月16日按用户要求，已在本机 32 GB 环境实际跑完同一份 1440w ODB 的优化前后完整流程：
主要进程物理内存峰值 9.87 → 6.01 GiB（减少 39.1%），总耗时 45分19秒 → 56分50秒（增加 25.4%）。
全部 45 个 HDF5 文件、1,218 个数据集的路径、形状、类型和内容哈希一致。
这是整套低内存改动的对比，没有单独测量 uint32 的贡献，也没有证明仅余 4 GB 可用内存时能完成。
详见 [1440w ODB 内存优化实测](../交付/1440w-ODB内存优化实测-20261016.md)，含曲线、原始汇总和速度回退分析。
仍需在实际 16 GB 机器重试，记录运行前可用内存和解析进程峰值。
