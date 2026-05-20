# Field Component 存储约定 & USUM/MAGNITUDE 排查笔记

更新时间：2026-05-20

---

## 1. OP2 结果字段（Nastran）

### HDF5 列布局

`op2_pack.py` 写入的位移字段 HDF5（`SUBCASE_<n>__U.h5`）数据集路径：

```
/NODAL/<inst_name>/data   shape: [n_frames, N_bdf, 4]  dtype: float32
```

列顺序：

| index | 分量 | 说明 |
|-------|------|------|
| 0 | U1 | X 方向平动 |
| 1 | U2 | Y 方向平动 |
| 2 | U3 | Z 方向平动 |
| 3 | USUM | √(U1²+U2²+U3²)，pack 时预计算 |

`manifest.db` 的 `result_files.components` 字段同步写为 `["U1","U2","U3","USUM"]`。

### NaN 处理

OP2 节点数少于 BDF 节点数时，未覆盖节点填 NaN。USUM 对这些节点也是 NaN（用 `nansum` 计算后再把全 NaN 行还原为 NaN）。

---

## 2. ODB 结果字段（Abaqus）

### HDF5 列布局

`l1_pack.py` 写入的 U 字段 HDF5 数据集：

```
/NODAL/<inst_name>/data   shape: [n_frames, N_nodes, 3]  dtype: float32
```

**只有 3 列**（U1/U2/U3），MAGNITUDE **不写进** data 数组。

### 为什么 ODB 没有 MAGNITUDE 列

`abaqus_dump.py` 的 `_HIDDEN_INV_SUFFIXES`（第 191 行）把 MAGNITUDE 列为跳过项，注释原文：

> *MAGNITUDE is excluded — L3 computes it on-the-fly from components (identical result).*

**注意**：截至 2026-05-20，L3 实际上**没有**实现 on-the-fly 计算 MAGNITUDE 的逻辑，该注释描述的是设计意图，尚未落地。ODB 的前端下拉里目前不会出现 MAGNITUDE/USUM 选项。

其他 invariants（MISES、TRESCA 等）仅适用于应力/应变字段，且作为**独立字段**存入单独的 HDF5 文件（例如 `S_MISES__...h5`），不追加在 U 字段的列里。

---

## 3. L3 变形计算读取约定（result_service.py）

`frame_deformed_positions` 及相关函数读 U 字段时，**统一取前 3 列**：

```python
disp_node = ds[frame_idx, :, :3].astype(np.float32)  # UX/UY/UZ only
```

- 对 ODB（3列）：切片无影响，等价于读全部
- 对 OP2（4列）：排除第 4 列 USUM，避免与 positions(3列) shape 不匹配

共 3 处，分别在 `result_service.py`：
- `frame_deformed_positions`（第 1299 行附近）
- `suggest_deform_scale`（第 1390 行附近）
- 另一个变形辅助读取（第 1449 行附近）

---

## 4. 常见报错

### `ValueError: operands could not be broadcast together with shapes (N,3) (N,4)`

出现在 `deformed-positions` 接口，原因是读 U 字段时没有限制列数，OP2 数据 4 列直接和 3 列 positions 相加。
**修复**：在所有变形相关读取处加 `[:, :3]`。

---

## 5. 后续扩展提示

- 如果要给 ODB 也加 USUM/MAGNITUDE 支持，有两条路：
  1. 在 `l1_pack.py` 里写 data 时追加第 4 列（和 OP2 对齐）
  2. 在 `abaqus_dump.py` 里把 MAGNITUDE 从 `_HIDDEN_INV_SUFFIXES` 移除，走独立字段存储路径
- 扩展后需同步更新 `result_service.py` 变形相关读取（或确认 `[:3]` 切片已足够保护）
