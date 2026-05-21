# Color-Code Legend 完整性设计

更新时间：2026-05-21

## 背景

L3 的 color-code 接口（`/color-code/{instance}/legend`、`/color-code/{instance}/legend-entries`）
负责给前端提供图例列表和每个渲染顶点的颜色。

最初实现中，图例候选值完全来源于 **渲染面**（L2 surface extraction 输出的元素）：
扫描每个渲染面对应的元素属性（`material_name`、`section_type`、etype），
取 unique 值作为图例项。

这造成一个系统性遗漏：**内部元素（非表面元素）的属性值不会出现在图例里**。

## 问题示例

`fem15.bdf` 模型有 4 种材料（ID 1、2、3、4）。  
其中材料 2 只被 1 个 PSHELL 壳单元（elem label=1666，pid=22）引用，
该壳单元夹在实体网格内部，无任何暴露的外表面。  
L2 表面提取不包含这个单元，因此图例里只出现 1、3、4，材料 2 缺失。

同样的问题影响三个 scheme：

| Scheme | 问题 | 典型场景 |
|---|---|---|
| `material` | 内部元素的材料不出现 | Embedded shell（内嵌壳）、仅在内部的实体分区 |
| `section_type` | 内部元素的截面类型不出现 | 同上 |
| `etype` | 内部元素的单元类型不出现 | MASS/SPRING/梁单元全部在内部时 |

不受影响的 scheme：

| Scheme | 原因 |
|---|---|
| `section` | Averaging domain 本身是表面概念（由 L2 表面划分产生），逻辑正确 |
| `elset` | 集合**名字**从 manifest.db 读取，已完整；无法高亮内部元素是另一个问题 |

## 修复方案

### 核心思路

把"图例候选值的来源"和"渲染着色的范围"分开：

- **渲染着色**：仍然只针对表面渲染面，逻辑不变
- **图例候选值**：先取表面值（保持原顺序和调色板分配），再扫描 L1 H5 所有元素组补全缺失值

### 实现

`color_service.py` 新增两个辅助函数：

```python
_all_unique_vals_from_l1(idx, instance, attr_name)
    # 读 L1 geometry H5 所有 elements/* 组的 attr_name 数据集
    # 返回去重后的非空值列表（保持首次出现顺序）

_all_etypes_from_l1(idx, instance)
    # 读 L1 geometry H5 的 elements/ 所有子组名
    # 返回排序后的 etype 列表
```

`_compute_labels_and_legend` 和 `get_legend_entries` 中，对 `etype`、`material`、
`section_type` 三个 scheme，在 surface unique_vals 之后追加 L1 中存在但不在表面的值。

内部元素在图例中的 `face_count` 为 **0**，前端可据此显示"该分组无可见面"的提示。

### 颜色一致性

表面已出现的值优先分配调色板（顺序不变），内部补全的值追加在末尾分配后续颜色。
`get_legend` 和 `get_legend_entries` 使用同一套逻辑，颜色完全一致。

## 数据流

```
bdf_pack.py (L1)
  └─ elements/{etype}/material_name  [所有元素，含内部]
  └─ elements/{etype}/section_type   [同上]

ingest.py (L2)
  └─ render/FEM15_render.h5          [仅表面元素]

color_service.get_legend()
  ├─ _labels_from_elem_attr()        ← 只扫渲染面（着色用）
  └─ _all_unique_vals_from_l1()      ← 扫全量 L1（图例补全用）
```

## 相关 Bug 修复（同次提交）

### PSHELL material_name 为空（bdf_pack.py）

pyNastran 中 `PSHELL` 属性的材料引用字段名为 `mid1`，不是 `mid`。
`PSOLID` 用 `mid`。之前代码统一用 `getattr(prop, 'mid', None)`，
对 PSHELL 返回 `None`，写入 H5 的 `material_name` 为空字符串，
导致所有壳单元显示为 `(none)`。

修复：PSHELL / PSHEAR 改为优先取 `mid1`，fallback 到 `mid`：

```python
mid_raw = getattr(prop, 'mid1', None) or getattr(prop, 'mid', None)
```

影响范围：`bdf_pack.py` 里 `sid_to_mat` 计算循环和 sections H5 attrs 写入两处。

## 选中内部元素的问题

内部元素（face_count=0）在 3D 视图里无法通过点击选中。
已有的两种机制可以覆盖这个场景，但尚未接入：

- **剖切平面**（已实现）：切开模型可暴露内部元素表面
- **按属性选中**（已实现）：通过材料/属性 ID 在后端查元素列表，不依赖 3D 拾取

后续再处理，当前版本不做。
