# 特殊单元（非曲面）的解析与显示

> 更新时间：2026-06-24
>
> 记录耦合（coupling）、连接器（connector）、集中质量（mass）、弹簧（spring）等
> **没有曲面几何**的单元在两条数据路径（ODB / INP）里的解析与显示约定。

## 背景：为什么这些单元曾经丢失

L1/L2 的几何流水线本质是把单元三角化成可渲染的**表面**。最初只认壳、实体、
梁/桁架这些"有形状"的单元，凡是不在白名单里的类型一律 `skip`：

- `src/l1/abaqus_dump.py`（ODB 路径）：`_resolve_elem_code` 查不到 → 打印
  `WARNING: unknown type X, skipped` 后丢弃。
- `src/inp/exporter.py`（INP 路径）：`_ELEM_TYPE_CODE.get(...) < 0` → `continue`，
  静默丢弃。

结果：DCOUP3D 耦合、CONN3D2 连接器、SPRING、（部分）MASS 在前端**完全不可见**。

## 三类处理方式

按单元形态分三类，全部复用前端**既有**的渲染组件，前端零改动：

| 形态 | 典型单元 | 类型码 | 渲染 | 前端组件 |
|---|---|---|---|---|
| **线**（2 节点） | T3D2/B31、CONN3D2、SPRING2、SPRINGA | 9 / 10 | 线段 | `lineMeshes`（LineSegments） |
| **点**（1 节点） | MASS、ROTARYI、SPRING1 | 11 | 点 | `pointMeshes`（Points） |
| **星形**（1 ref + N，变长） | DCOUP3D / DCOUP2D（分布式耦合） | — | spider | `couplingMeshes`（LineSegments） |

线/点单元走普通 `/elements/<type>/` 路径（带 label、可拾取）；
星形耦合因连接数可变、塞不进固定 `[M,k]` 矩阵，单独展开成线段。

## 类型码表三处必须同步

同一份 `ELEM_TYPE_CODE` 在三个文件各有一份拷贝，**改一处必须改三处**（各自源码注释也写了 "keep in sync"）：

| 文件 | 运行环境 | 作用 |
|---|---|---|
| `src/l1/abaqus_dump.py` | Abaqus Python 2.7 | ODB → npy，决定单元怎么分流 |
| `src/inp/exporter.py` `_ELEM_TYPE_CODE` | Python 3 | INP → 几何 h5 |
| `src/l2/ingest.py` `ELEM_TYPE_CODE` | Python 3 | 读几何 h5，`collect_lines`/`collect_points` 据码分类 |

线 = code 9/10，点 = code 11（`LINE_ELEM_CODES`/`POINT_ELEM_CODES`）。

## spider：两条路径，殊途同归到 `couplings/positions`

"一个点连多条线"的 spider 显示链路前端早已具备，关键是**数据格式契约**：

```
couplings/positions = [Nseg*2, 3] float32
  (参考点, 从节点) 成对交错；一个 spider(1 ref + K leaf) = K 段 = [ref,s1, ref,s2, ...]
  前端 LineSegments 每 2 个顶点画一段。
```

两条路径用**完全不同的起点**算出同一份数据：

- **INP 路径**：耦合不是单元，而是 `*Coupling` + `*Kinematic`/`*Distributing`
  **约束关键字**。`parser._handle_coupling` 解析 ref node + surface →
  `exporter._append_coupling_lines` 展开成线段。**KINEMATIC 和 DISTRIBUTING 都画。**
- **ODB 路径**：求解器把同一个耦合**内部实体化成 DCOUP3D 单元**（INP 里没有
  DCOUP3D 这个词）。`abaqus_dump` 对未识别的多节点单元按 `conn[0]=ref,
  conn[1:]=leaf` 展开成线段。

下游统一：L2 `collect_couplings` 透传 → L3 `GET /geometry/{inst}/couplings`
（L3BE 二进制）→ 前端 `loadCouplingLines`。

## 数据落点

ODB 路径（`abaqus_dump` → `l1_pack`）在 `geometry/<inst>.h5` 写：

```
/couplings/positions              [Nseg*2, 3] float32   # spider 线段
/elements_special/<type>/         # 变长/未识别单元的原始连接（存档，暂不被 L2/L3 读）
    labels       [M]   int32
    conn_flat    [sum] int32       # 节点 label（非 row）
    conn_offsets [M+1] int32       # CSR 偏移
```

INP 路径在同名 `couplings/positions` 数据集写（`exporter._append_coupling_lines`），
线/点单元写进 `/elements/<type>/`。

## 已知限制

1. **耦合参考点必须属于本 instance** 才画得出 spider。若 ref 是装配级节点
   （不在本 instance 节点表），整个 spider 被跳过——与 INP 导出器行为一致。
2. **`*MPC` / `*Tie` / `*Rigid Body`** 这类约束在 ODB 里**不是 element**，挂在
   `rootAssembly.constraints` 上，不进 `instance.elements` 循环，本次未覆盖。
   （`*Coupling`→DCOUP3D 会变成 element，所以能解析。）
3. **真正未识别的单元类型**（不在三处码表、又不是星形）仅归档进
   `elements_special/`，不渲染。运行日志里表现为
   `special type X (N elems): parsed (non-surface)`。

## 相关代码

| 关注点 | 位置 |
|---|---|
| ODB 单元分流 / spider 展开 / special 归档 | `src/l1/abaqus_dump.py` `dump_geometry` |
| 几何 h5 打包（couplings/elements_special） | `src/l1/l1_pack.py` `pack_geometry` |
| INP 线/点/耦合导出 | `src/inp/exporter.py` `_write_geometry_h5` / `_append_coupling_lines` |
| INP 耦合关键字解析 | `src/inp/parser.py` `_handle_coupling` |
| L2 收集 | `src/l2/ingest.py` `collect_lines` / `collect_points` / `collect_couplings` |
| L3 端点 | `src/l3/api/routes/geometry.py` `GET /geometry/{inst}/couplings` |
| 前端渲染 | `viewer/src/components/ThreeViewport.vue` `loadCouplingLines` |
