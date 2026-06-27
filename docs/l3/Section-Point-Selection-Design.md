# Section Points(截面点)选择 — 设计方案

> 状态:设计(未实现)
> 更新时间:2026-06-27
> 关联代码:`src/l3/services/result_service.py`、`src/l3/api/routes/results.py`、`src/l1/abaqus_dump.py`、`src/l1/l1_pack.py`
> 参考:`docs/Selecting section point data by category.md`(Abaqus CAE 原文档)

本文说明如何让壳/梁等单元在渲染时支持切换"截面点(section point)",对齐 Abaqus CAE 的 **Result → Section Points** 行为。当前实现永远只显示底面(Bottom),本方案给出从数据到接口的完整落地路径。

---

## 一、背景科普:section point 是什么

壳(shell)、梁(beam)这类单元在"厚度方向"上有内部结构。一块 1mm 厚的壳,弯曲时上表面受拉、下表面受压,中间为 0。Abaqus 不会只存一个值,而是沿厚度方向布若干个**积分点(integration point)/ 截面点(section point)**,每个点各存一份应力应变。

- 默认壳(Simpson 积分,N 个点):**编号最小的点 = 底面 = SNEG = Bottom**,**编号最大的点 = 顶面 = SPOS = Top**,中间编号 = 中面。
- 复合材料层合板:每一层(ply)算一个或多个 section point,各有名字。
- 实体单元(solid)没有厚度方向 section point 的概念,只有体内积分点。

Abaqus CAE 提供两种选法(见参考文档):

1. **Categories(按类别 + 位置)**:选 Bottom / Top(三维壳可同时上下两面),再在该类别可用截面点里挑一个具体点。Bottom/Top 默认对应 SNEG/SPOS。
2. **Envelope(包络 / 临界值)**:不挑固定点,而是在每个单元的所有截面点里取 **绝对值最大 / 最大 / 最小**,生成 envelope plot。

---

## 二、现状:我们现在只显示 Bottom

**好消息:L1 已经把所有截面点的数据都存下来了,无需重新提取 ODB。**

### 2.1 HDF5 数据布局

每个截面点是一个独立子组(`src/l1/l1_pack.py:742-755`):

```
/{position}/{instance}/{etype}/sp{n}/data        ← position = ELEMENT_NODAL 或 INTEGRATION_POINT
                              /sp{n}/labels
                              /sp{n}/sp_labels    ← 该块的 Abaqus 截面点编号
                              /sp{n}/ip_labels    ← INTEGRATION_POINT 才有
实体单元:  /{position}/{instance}/{etype}/data   ← 没有 sp 层,data 直接挂在 etype 组下
```

`sp{n}` 里的 `n` 就是 Abaqus 的 `sectionPoint.number`(`src/l1/abaqus_dump.py:550-551` 抓取)。`manifest.db` 的 `result_blocks` 表还存了 `n_sp`(该单元有几个截面点)。

> 边角布局:`l1_pack.py:791-807` 还存在一种 `[frames, N, n_sp, n_ip, ncomp]` 的"n_sp 合一维"老布局。实际数据现在走 `sp{n}` 子组布局,这条主要作防御性处理(见 4.2 的坑)。

### 2.2 问题点:4 处硬编码 `sp1`

`src/l3/services/result_service.py` 有 4 处硬编码只取 `sp1`(注释直说 "use sp1 only")。`sp1` 恰好是底面,所以现在永远显示 Bottom:

| 位置 | 函数 | 作用 |
|---|---|---|
| `result_service.py:114-118` | `_scalar_elem_pos_by_idx` | 按分量索引取面值(frame-scalars 主路径) |
| `result_service.py:317-321` | `_scalar_from_element_position` | 按分量名取面值(frame-colors 路径) |
| `result_service.py:1151-1155` | `_en_per_vertex_averaged` | ELEMENT_NODAL 75% 条件平均 |
| `result_service.py:1320-1323` | `_compute_en_global_range` | 计算图例 min/max |

> NODAL 位置是节点平均结果、没有 section point 层,因此本方案只涉及 **ELEMENT_NODAL** 与 **INTEGRATION_POINT**。`frame_colors` 会先尝试 NODAL —— 对有 NODAL 输出的壳,NODAL 没有逐截面点数据,section point 选择对它不生效(这与 Abaqus 用 ELEMENT_NODAL/IP 做截面点云图一致)。

---

## 三、目标

让前端/调用方可以请求指定截面点(或 Top/Bottom/Middle/包络),而非永远 Bottom。后端方案为主,前端只多传一个查询参数;不传则默认 Bottom,完全向后兼容。

---

## 四、落地方案

### 4.1 选择语义:一个参数搞定 5 种模式

新增参数 `section_point`(字符串),取值:

| 取值 | 含义 | 实现 |
|---|---|---|
| `bottom`(默认) | 底面 = SNEG | 取 `sp{n}` 中编号**最小**的 |
| `top` | 顶面 = SPOS | 取编号**最大**的 |
| `middle` | 中面 | 取编号**居中**的 |
| `sp:<num>` | 指定 Abaqus 截面点号 | 按号精确匹配子组 |
| `max` / `min` / `absmax` | 包络 | 读**全部** `sp{n}`,逐元素求 max / min / abs-max |

按"编号最小/最大/居中"推断 bottom/top/middle,对任意截面点编号方案都稳健(不依赖固定点数)。默认 `bottom` 保证行为与现状完全一致。

### 4.2 抽出公共 helper(关键,避免 4 处各写各的)

在 `result_service.py` 新增:

```python
def _select_sp_dataset(etype_grp, section_point="bottom"):
    """从 etype_grp 里按 section_point 规则选数据。
    - 实体单元(etype_grp 直接含 'data')→ 忽略 section_point,返回 (etype_grp['data'], None)。
    - 壳单元(含 sp{n} 子组):
        固定点(bottom/top/middle/sp:<n>)→ 返回 (单个 dataset, None)
        包络(max/min/absmax)         → 返回 ([dataset, ...], reduce_op)
    """
```

4 处全部改成调用它:
- **固定点模式**返回单个 dataset,各调用点改动极小(把原 `sp1` 选择换成 helper 返回值)。
- **包络模式**返回多个 dataset + reduce 操作,在各调用点读完帧、抽完分量后做一次跨 sp 归并(`np.maximum` / `np.minimum`,absmax 按 `|v|` 比较保留带符号原值)。

> **潜在坑(务必处理):** 现有代码用 `while frame_data.ndim > 2: frame_data.mean(axis=1)` 对中间维度求平均。若数据是 4.x 提到的 `[..., n_sp, n_ip, ncomp]` 老布局,这行会把多个截面点**平均掉**而非挑选。helper 要识别该布局并改成**按 n_sp 维索引**,而非平均。

### 4.3 接口透传(只加参数,不改前端既有行为)

在 `src/l3/api/routes/results.py` 给三个端点加可选查询参数,默认 `bottom`:

- `GET /results/frame-colors`
- `GET /results/frame-scalars`
- `GET /results/frame-scalar-range` —— **图例范围必须和着色用同一个 section point**,否则颜色和图例对不上。

```python
section_point: str = Query("bottom", description="bottom|top|middle|sp:<n>|max|min|absmax")
```

再透传进 `frame_colors` / `frame_scalars` / `compute_scalar_range` 的函数签名(同样默认 `bottom`)。

> **文档约定(CLAUDE.md):** 改完接口必须同步更新 `docs/l3/L3-API-Quick-Reference.md`,并更新其顶部"更新时间"行。

### 4.4 发现接口:让前端知道有哪些截面点可选

前端要弹出类似 CAE 的下拉框,得先知道某个 field/instance 有几个截面点。`manifest.db` 已有 `n_sp`,可在现有 `meta/overview` 或 fields 列表里给壳类 field 补字段:

```json
{
  "field": "S",
  "positions": ["ELEMENT_NODAL"],
  "section_points": { "count": 5, "numbers": [1, 2, 3, 4, 5] }
}
```

数据从 `result_blocks.n_sp` + `sp_labels` 聚合,无需扫文件。

### 4.5 可选增强:更友好的标签(需小改 L1)

现在 L1 只存截面点**编号**(`sp_obj.number`),没存 Abaqus 的描述串(SPOS/SNEG / ply 名 / 相对位置分数)。若想下拉框显示"Bottom (SNEG)""Ply-3 Top"这种人话:

1. 在 `src/l1/abaqus_dump.py` 的 `reshape_ip_block` / `_block_sp_num` 里顺手抓 `sp_obj.description`;
2. 在 `src/l1/l1_pack.py` 写进 `sp{n}` 子组的 attr;
3. L3 发现接口透出。

这是**纯增量**,不影响现有数据;老 workspace 没有 description 时回退到"Section Point N"。建议作第二阶段,第一阶段先用"编号 + bottom/top/middle 推断"即可跑通。

---

## 五、分阶段建议与工作量

| 阶段 | 内容 | 改动面 | 动前端/L1? |
|---|---|---|---|
| **P1(核心)** | helper + 4 处替换 + 3 个接口加参数 + 更新 API 文档 | 仅 `result_service.py` + `results.py` | 否(前端不传默认 bottom) |
| **P2(包络)** | `max/min/absmax` 跨 sp 归并 | helper + 各调用点 reduce | 否 |
| **P3(发现 + 标签)** | 发现接口 + L1 抓 description | manifest 聚合 +(可选)L1 | L1 小改、前端加下拉 |

P1 落地后"切换 Top / 指定截面点"即可用;P2 补 envelope;P3 让 UI 体验对齐 CAE。

---

## 六、关键不变量 / 验收要点

- **向后兼容**:不传 `section_point` 时,渲染结果与改动前逐像素一致(默认 = `sp1` = bottom)。
- **着色与图例一致**:frame-colors / frame-scalars / frame-scalar-range 三者必须用同一 `section_point`。
- **实体单元免疫**:solid(无 sp 层)忽略该参数,行为不变。
- **越界/缺失**:请求的 `sp:<n>` 不存在时回退到 bottom 或返回明确错误(实现时确定其一并在 API 文档注明)。
- **NODAL 不参与**:对走 NODAL 的壳,section point 选择不生效,符合 Abaqus 语义。
