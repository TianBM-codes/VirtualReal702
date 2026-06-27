# 混合 solid + shell 模型陷阱与排查

> 一句话：壳和实体的「分量集」「不变量集」天生不一样，凡是要**合并**这两套元数据
> 的地方，都要想清楚「多出来 / 少掉的那部分，缺数据时怎么显示」，否则就会出
> 「有些模型行、有些不行」的怪现象。本文沉淀已知的两个坑及排查思路。

## 0. 背景：壳和实体差在哪

| | 应力/应变分量 | 主不变量 |
|---|---|---|
| **实体（C3D*）** | 6 个：S11,S22,S33,**S12,S13,S23** | Mises、Tresca、Press、INV3、Max/Mid/Min Principal、Max Principal(Abs) |
| **壳/膜（S4R 等）** | 4 个：S11,S22,S33,S12（**无 S13/S23**） | 上面这些 **＋** 面内/面外：Max/Min InPlane Principal、OutOfPlane Principal、面内 Abs |

两套集合既不互相包含、也不相等。一旦同一个模型里两者并存，任何「把它们拼到一个
下拉 / 一个字段」的动作，都要在「并集 vs 交集」之间做选择，而这个选择不对称就会出 bug。

## 1. 已知坑一：混合模型缺面内/面外不变量（L1）

- **现象**：实体+壳混合模型，应力/应变只出 Max/Min/Mid Principal 和 Abs，**缺 in-plane / out-of-plane**。纯壳模型正常。
- **根因**：Abaqus `first_field.validInvariants` 对混合场返回各单元类型的**交集**。
  实体没有面内/面外主应力，交集 = 实体 ∩ 壳，面内三项被实体拖没 → 进不了
  `active_invs` → 不生成。
- **修复**：`src/l1/abaqus_dump.py` 取到 `invariants` 后，用
  `getScalarField(MAX_INPLANE_PRINCIPAL)` 探测有没有壳/膜块，有就把
  `MAX_INPLANE_PRINCIPAL / MIN_INPLANE_PRINCIPAL / OUTOFPLANE_PRINCIPAL` 补回。
  下游对实体块按 `_SHELL_ONLY_INVS` 置 NaN（前端置灰），与纯壳模型一致。
- **性质**：**静默** bug——不报错，只是下拉少几个选项，不点面内分量发现不了。

## 2. 已知坑二：选 S13/S23、E13/E23 时云图报错（L3）

- **现象**：混合模型选 `S23`/`E23` 等分量，整张云图挂掉，报
  `no result data (NODAL/ELEMENT_NODAL...) for instance 'PART-1-1' in field 'S'`。
- **根因**：分量下拉是所有 instance 分量的**并集**（含实体才有的 S13/S23）。
  当某个 instance 是**纯壳/膜**（只有 4 分量）时，选 S23（下标 5）对它全部单元越界
  → 全 NaN。`_scalar_elem_pos_by_idx` 遇全 NaN 返回 `None`，应力场又通常没有 NODAL，
  于是 NODAL→EN→IP 一路 fall through → 抛硬错误。
- **修复**：`src/l3/services/result_service.py` 的 `_scalar_elem_pos_by_idx` 区分两种全 NaN：
  - **分量对该实例所有 etype 越界**（壳选 S13/S23/E13/E23）→ 合法的「该实例无此分量」，
    返回全 NaN 让前端**置灰**，不再 fall through 报错；
  - **块存在但 L1 未填值**（不变量场 EN 块只在 IP 出值，`component_idx=None`）→ 仍返回
    `None`，继续回退到 INTEGRATION_POINT。
  - 回归测试：`tests/test_l3_oob_component_grey.py`。
- **性质**：**显性** bug——会弹错、渲染挂掉。

## 3. 为什么「有些混合模型行、有些不行」

**核心：这两个 bug 是按「场 field / 实例 instance」粒度触发的，不是按「整个模型」。**
一个模型整体混合，但具体某个 field / 某个 instance 可能是「纯的」：

- 坑一**按 field 触发**：只有同一个 field 同时有壳块和实体块才取交集丢面内。
  若某 field 只在实体上输出（壳没请求该场 / 壳是刚体），它就退化成纯实体场，本就无面内项。
- 坑二**按 instance 触发**：只有点到「缺该分量的纯壳 instance」才崩。
  点实体 instance、或壳实体在同一 instance 混着（实体块有 S23）→ 不全 NaN → 不报错。

「某个混合模型没报错」常见原因（坑二）：

1. 只看了 Mises / 主应力 / 主应变——壳实体都有，永不越界。
2. 点的 instance 不是纯壳。
3. 那个 field 带了 NODAL 数据（按并集存含 NaN）→ 读到就置灰，不报错。
4. 壳在该 field 上没输出，下拉退化，选不到「壳上不存在」的分量。

而坑一（缺面内项）是静默的，「没问题」往往只是**没人去点面内分量**，其实也中了。

## 4. 排查决策树（再遇到类似怪现象先问这几条）

```
现象：某分量/某不变量在「部分模型」上异常
  │
  ├─ 这模型是 solid + shell 混合的吗？
  │     否 → 多半不是这一类坑，另查
  │     是 ↓
  │
  ├─ 是「报错/渲染挂」还是「静默少了选项/显示灰」？
  │     报错  → 坑二：分量越界。看是不是点到纯壳 instance 选了 13/23 分量
  │     静默  → 坑一：面内/面外不变量被交集丢掉
  │
  └─ 缩小粒度：不是看整模型，看具体
        · 哪个 field（壳实体是否都在这个 field 出数据）
        · 哪个 instance（纯壳？纯实体？还是同一 instance 混合）
        · 哪个 position（有没有 NODAL 兜底）
```

## 5. 通用原则（避免再挖同类坑）

只要代码里出现「合并壳和实体的元数据」（分量列、不变量列、单元类型表……）：

1. 明确这次合并用**并集**还是**交集**，并记下「为什么」。
2. 并集会引入「对某些块不存在」的项 → 必须保证读数据时**缺数据渲染成灰（NaN）**，
   而不是报错或 fall through 到死路。
3. 交集会**丢掉只有一方有**的项 → 必须显式把缺的补回，并对另一方置灰。
4. 测试至少覆盖：纯实体、纯壳、混合（且包含纯壳 instance 与混合 instance）三类。

---

相关代码：`src/l1/abaqus_dump.py`（validInvariants 探测补回、`_SHELL_ONLY_INVS`、
`_NUMPY_ONLY_INVS`）、`src/l3/services/result_service.py`（`_scalar_elem_pos_by_idx`、
`_extract_component`）。相关文档：`docs/l1/不变量计算公式表.md`、
`docs/Special-Element-Handling.md`。
