# 接触结果报错：`could not convert string to float: '-3.402823466386e 38'`

状态：**待修复**（方案已确认，等待动手）
记录时间：2026-07-16

## 问题现象

解析完 ODB 后，前端调取**接触相关结果**（CPRESS / COPEN 这类）时后端 500：

```
src/l3/api/response.py, line 25, in nullable_float
    return float(s)
ValueError: could not convert string to float: '-3.402823466386e 38'
```

## 原因（两层）

### 直接原因：URL 里的 `+` 被解码成了空格

前端拿到 `global_min` 后是**裸拼字符串**进 URL 的（`viewer/src/components/ThreeViewport.vue:1206`）：

```js
const rangeParam = `&global_min=${globalMin}&global_max=${globalMax}`
```

没有做 `encodeURIComponent`。JS 把大数字序列化成科学计数法 `-3.402823466386e+38`，
里面带 `+` 号；而按 URL query string 编码规则，**`+` 表示空格**（`+` 想表示自身必须
写成 `%2B`）。后端（FastAPI/Starlette）解码时把 `e+38` 还原成 `e 38`，`float()` 就炸了。

### 根本原因：接触结果数据里混着 `-3.4e+38` 哨兵值

`-3.402823466386e+38` 正好是 **float32 的最小值（-FLT_MAX）**。Abaqus 输出接触类
结果时，对**没有发生接触的节点**用这个值当"此处无有效数据"的占位符，不是真实物理量。

链路：前端多 instance 显示 → 先调 `/results/field-range` 算全局 min/max →
后端 `compute_scalar_range`（`src/l3/services/result_service.py:588`）算范围时
没过滤哨兵值 → `global_min` 变成 -3.4e38 → 前端把它带回 `/results/frame-scalars`
→ 触发上面的 URL 编码问题。

**注意**：就算把 `float()` 报错修掉，图例范围也是错的——min 被撑到 -3.4e38，
所有真实数据被压成色条上一个颜色，云图没法看。所以两处都要修。

## 修改方案（按 CLAUDE.md"优先后端"约定，两处都在后端改）

### 改动 1 — 哨兵值过滤（治本）

`src/l3/services/result_service.py`：

- 新增 `_mask_sentinel()` 辅助函数：对刚从 HDF5 读出的帧数据，把绝对值 ≥ **1e30**
  的值置为 NaN（只对 float dtype 生效）。
- 在 **6 处**读数据的地方调用，都必须放在中间维 `mean()` **之前**——否则哨兵值
  会先被平均进真实值，之后滤不掉：
  1. `_scalar_nodal_by_idx`（约 line 76，`frame_data = ds[frame_idx]` 之后）
  2. `_scalar_elem_pos_by_idx`（约 line 184）
  3. `_scalar_from_nodal`（约 line 340）
  4. `_scalar_from_element_position`（约 line 387）
  5. `_en_per_vertex_averaged`（约 line 1227）
  6. `_compute_en_global_range`（约 line 1395）
- 下游已确认能安全接住 NaN，**不需要新增 NaN 处理逻辑**：
  - range 计算全部走 `isfinite` 过滤，全 NaN 返回 None（前端显示为无数据）；
  - 上色路径对 NaN 顶点本来就渲染灰色（"无数据→灰色"既有路径）。

### 改动 2 — URL 空格容错（兜底，不改前端）

`src/l3/api/response.py` 的 `nullable_float`：

- 解析前把字符串**内部**的空格还原成 `+`（一个数字参数里出现空格，只可能是
  前端没编码的 `+` 被 URL 解码规则换成的空格），例如 `s.replace(' ', '+')`。
- 加注释说明来龙去脉（`+`→空格 的 URL 解码规则、JS 科学计数法序列化）。
- 这样以后任何科学计数法数字从前端裸拼过来都不会再 500，前端不用动。

## 会不会影响真实结果显示？

**不会。** 阈值 1e30 和真实物理量差十几个数量级（应力最大也就 1e9~1e12 量级）；
真算出 1e30 说明计算发散，本身不是有效结果。位移/应力/模态等正常场不含这个量级
的值，过滤等于空操作。被过滤的接触节点显示灰色，与 Abaqus"未接触区域不参与云图"
的行为一致。

## 验证要点（改完后）

1. 接触结果（CPRESS 等）多 instance 显示：`/results/field-range` 返回的
   `global_min/global_max` 应为真实接触区数值，不再是 ±3.4e38。
2. `/results/frame-scalars` 带 `global_min=-3.402823466386e+38`（裸 `+` 未编码）
   请求不再 500。
3. 未接触节点渲染灰色；接触区云图颜色分布正常。
4. 位移/应力等常规场回归：图例范围与改前一致。
