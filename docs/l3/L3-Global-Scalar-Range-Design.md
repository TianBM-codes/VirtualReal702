# L3 全局 Scalar 归一化设计方案

## 背景

`frame-scalars` 接口当前对每个 instance 独立计算 min/max，然后将 scalar 归一化到 [0,1] 作为 uv.x 返回。当场景中同时显示多个 instance 时，各 instance 的颜色是独立归一化的，同一颜色在不同 instance 上代表不同的数值，云图无法跨 instance 对比。

**需求**：当前已加载（可见）的 instance 共用同一套 min/max 进行归一化，颜色具有统一物理含义。

---

## 现状分析

### 前端 `applyColors`（ThreeViewport.vue:961）

```
applyColors() {
  instNames = Object.keys(store.instanceMeshes)   // 所有已加载的 instance
  Promise.all(instNames.map(async instName => {
    1. 请求 frame-scalars（无 override，后端按 instance 自己归一化）
    2. 从响应 Header 读 X-Val-Min / X-Val-Max
    3. 用 tValues（已是归一化后 [0,1]）写入 uv.x
  }))
  emit('colors-loaded', { vMin: globalMin, vMax: globalMax })  // 仅供 legend 显示
}
```

**关键问题**：前端已经在归并所有 instance 的 min/max 用于图例（line 971–981），但 uv.x 写的是各自 instance 的归一化值，图例范围与颜色不匹配。

### 后端 `frame_scalars`（result_service.py:437）

- 接收单个 `instance`，计算该 instance 的 `global_range`（全模型节点范围，含内部单元）。
- 用该 range 归一化，返回 `u_per_vertex [0,1]` 和 `legend_range`。

---

## 方案 A（选定方案）：两阶段请求

### 设计原则

1. **"当前显示的 instance"** = `Object.keys(store.instanceMeshes)`，即用户通过 InstanceCard 选择并已加载几何的 instance 集合。
2. 第一阶段：前端用这个集合请求后端的新接口，拿到跨 instance 的全局 min/max。
3. 第二阶段：前端携带全局 min/max 请求每个 instance 的 `frame-scalars`，后端跳过自动计算，直接用传入的 range 归一化。

---

## 改动详情

### 1. 后端新增接口 `GET /results/frame-scalar-range`

**文件**：`src/l3/api/routes/results.py`

**作用**：接收一组 instance 名称，对每个 instance 复用现有的 range 计算逻辑（不做完整归一化），返回合并后的全局 [min, max]。

**请求参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `instances` | `str`（逗号分隔） | 需要参与计算的 instance 列表 |
| `step` | `str` | 步名 |
| `field` | `str` | 场名 |
| `frame` | `int` | 帧索引（默认 0） |
| `component_idx` | `int?` | 分量索引，不传表示 magnitude |
| `mode` | `str` | `smooth` \| `flat` |
| `result_group` | `str?` | 项目模式结果组 |
| `feature_angle` | `float?` | 默认 20.0 |
| `average_threshold` | `float` | 默认 0.75 |
| `use_geometry_split` | `bool` | 默认 true |

**响应**（JSON）：

```json
{
  "global_min": -123.45,
  "global_max":  678.90,
  "instance_ranges": {
    "PART-1-1": [-123.45, 200.0],
    "PART-2-1": [50.0, 678.90]
  }
}
```

`instance_ranges` 用于调试，可选保留。

**实现策略**：在 `result_service.py` 中提取一个新函数 `compute_scalar_range()`，与 `frame_scalars()` 共用解析和 range 计算逻辑，但不做最终归一化、不构建 `u_per_vertex`。`frame-scalar-range` 路由对每个 instance 调用该函数并合并结果。

---

### 2. 后端 `frame_scalars()` 新增 override 参数

**文件**：`src/l3/services/result_service.py`，函数 `frame_scalars()`

**新增参数**：

```python
def frame_scalars(
    ...,
    override_min: Optional[float] = None,
    override_max: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, str]:
```

**归一化逻辑修改**（当前第 676–684 行）：

```python
# 当前逻辑（per-instance）
if global_range is not None and np.isfinite(global_range[0]):
    val_min, val_max = float(global_range[0]), float(global_range[1])
else:
    ...

# 改后逻辑（优先使用外部传入的全局 range）
if override_min is not None and override_max is not None:
    val_min, val_max = float(override_min), float(override_max)
elif global_range is not None and np.isfinite(global_range[0]):
    val_min, val_max = float(global_range[0]), float(global_range[1])
else:
    ...
```

当 `override_min/max` 为 `None` 时，行为与当前完全一致（向后兼容）。

---

### 3. 后端路由 `get_frame_scalars()` 透传参数

**文件**：`src/l3/api/routes/results.py`

**新增 Query 参数**：

```python
@router.get("/results/frame-scalars")
async def get_frame_scalars(
    ...,
    global_min: Optional[float] = Query(None, description="Override normalization min"),
    global_max: Optional[float] = Query(None, description="Override normalization max"),
):
```

透传给 `frame_scalars()` 的 `override_min` / `override_max`。

**Header 变更**：当传入了 override 参数时，`X-Normalization-Scope` 从 `"instance"` 改为 `"global"`。

---

### 4. 前端 `applyColors` 改为两阶段

**文件**：`viewer/src/components/ThreeViewport.vue`，函数 `applyColors()`（line 962）

**新流程**：

```
阶段一：拿全局 range
  instNames = Object.keys(store.instanceMeshes)   // 当前已加载的 instance
  GET /results/frame-scalar-range?instances=A,B,C&step=...&field=...
  → { global_min, global_max }

阶段二：并行请求各 instance 的归一化 scalar（携带全局 range）
  Promise.all(instNames.map(instName =>
    GET /results/frame-scalars?instance=A&...&global_min=X&global_max=Y
  ))
  → 各 instance 的 u_per_vertex 已按全局 range 归一化
  → 写入 uv.x，图例使用 { global_min, global_max }
```

**原有 `globalMin/globalMax` 归并逻辑**（line 971–981）可删除，改为直接使用阶段一的结果。

---

### 5. 前端新增 API 方法

**文件**：`viewer/src/composables/useOdbApi.js`

新增函数 `fetchScalarRange(instances, step, frameIdx, field, ...)`，封装对 `frame-scalar-range` 的请求。

---

## 时序图

```
前端 applyColors()
  │
  ├─ [阶段一] GET /frame-scalar-range?instances=A,B&step=S1&field=U&frame=0
  │       后端：对每个 instance 算 range → 合并 → 返回 { global_min, global_max }
  │
  └─ [阶段二] Promise.all
        ├─ GET /frame-scalars?instance=A&...&global_min=-123&global_max=678
        │       后端：skip 自动 range，用 override 归一化 → 返回 u_per_vertex
        └─ GET /frame-scalars?instance=B&...&global_min=-123&global_max=678
                后端：同上
  
  前端：写 uv.x，emit('colors-loaded', { vMin: global_min, vMax: global_max })
```

---

## 边界情况

| 情况 | 处理 |
|---|---|
| 只加载了 1 个 instance | 阶段一只查 1 个 instance，结果等于 per-instance range，行为不变 |
| 某个 instance 某 field 无数据 | `frame-scalar-range` 对该 instance 返回 null range，合并时忽略；`frame-scalars` 该 instance 仍返回全 NaN，前端渲染灰色 |
| `global_min == global_max`（场均匀） | 归一化 span < 1e-12，后端统一输出 0.0，前端颜色固定在 colormap 中点，与当前 per-instance 行为一致 |
| override 范围外的值（本不该出现） | `np.clip(..., 0.0, 1.0)` 保证 uv.x 仍在 [0,1]，不会崩溃 |

---

## 改动文件汇总

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/l3/services/result_service.py` | 修改 + 新增 | `frame_scalars()` 加 `override_min/max` 参数；新增 `compute_scalar_range()` 函数 |
| `src/l3/api/routes/results.py` | 修改 + 新增 | `get_frame_scalars()` 加 2 个 Query 参数；新增 `get_frame_scalar_range()` 路由 |
| `viewer/src/composables/useOdbApi.js` | 新增 | 新增 `fetchScalarRange()` 方法 |
| `viewer/src/components/ThreeViewport.vue` | 修改 | `applyColors()` 改为两阶段，旧归并逻辑移除 |

预计改动量：后端约 80 行，前端约 30 行。
