# L3 Pick 协议升级草案

> **状态：已实现（2026-03-29）**
> 本草案描述的 pick 协议升级已落地：
> - `pick_mode`（element / node）区分已实现
> - `odb` 字段（elem_label / node_label / elem_node_labels）已实现
> - `result` 字段（field / position / raw_value / display_value）已实现
> - `render_face_indices` 返回单元所有面片索引已实现
>
> 当前实现以代码和 `L3-API-Contract.md` 第 7.1 节为准。

---

## 目标

当前 `/query/pick` 的返回语义过粗：

- 只区分“点到了某个 render face”
- `node_labels` 返回的是整个单元角节点
- `current_value` 只近似代表某个节点值

本草案的目标是将 pick 协议升级为**明确的 ODB 语义协议**，支持两种模式：

- `pick_mode = element`
- `pick_mode = node`

并统一采用：

- `odb`：拓扑/编号语义
- `result`：结果语义

---

## 请求参数

建议 `GET /api/odb/{odb_id}/query/pick` 统一使用：

- `instance`
- `render_face_idx`
- `pick_mode`
- `step`
- `field`
- `frame_idx`
- `component`

说明：

- `render_face_idx` 仍是前端点击后的统一入口
- `pick_mode` 决定点击结果优先解释为单元还是节点
- `frame_idx` 与结果接口保持一致，避免 `frame` / `frame_idx` 混用

---

## 顶层响应结构

```json
{
  "pick_mode": "element",
  "instance": "PART-1-1",
  "render_face_idx": 123,
  "render_face_indices": [123, 124],
  "odb": {},
  "result": {}
}
```

字段约定：

- `render_face_indices`
  - `element` 模式：返回整个单元对应的所有三角片
  - `node` 模式：一期可选，若保留则表示该节点所属点击单元的三角片集合

---

## Element Pick

### `odb`

一期建议稳定支持：

- `elem_label`
- `elem_node_labels`

延期字段：

- `face_node_labels`

说明：

`face_node_labels` 需要补齐 `render_face_idx -> 原始面` 的反查链路。当前 L2 仅稳定提供：

- `source_elem_row`
- `source_node_rows`
- `source_etype_str`

因此一期不建议把 `face_node_labels` 作为硬承诺字段。

### `result`

建议统一字段：

- `field`
- `position`
- `component`
- `value_kind`
- `raw_value`
- `raw_values`
- `display_value`

语义规则：

- `NODAL`
  - 返回点击面相关节点的 ODB 原始值，放 `raw_values`
- `ELEMENT_NODAL`
  - 返回该单元的 ODB 原始值，按数据形态放 `raw_value` 或 `raw_values`
- `INTEGRATION_POINT`
  - 返回该单元积分点原始值，放 `raw_values`
- `display_value`
  - 仅在前端需要单值摘要显示时返回
  - 不替代 `raw_value/raw_values`

一期 `value_kind` 固定为：

- `odb_raw`

后续如接入插值算法，再增加：

- `interpolated`

---

## Node Pick

### `odb`

一期建议支持：

- `node_label`
- `elem_label`
- `candidate_node_labels`

说明：

- `node_label`：最终选中的 ODB 节点号
- `candidate_node_labels`：点击三角面 3 个候选节点，便于调试和解释

### `result`

建议结构：

- `field`
- `position`
- `component`
- `value_kind`
- `raw_value`

语义规则：

- `NODAL`
  - 返回该节点的 ODB 原始值
- `ELEMENT_NODAL / INTEGRATION_POINT`
  - 一期不建议默认支持
  - 若未来支持，应明确这是“所属单元的关联结果”，不是节点原生结果

---

## 一期实现边界

建议一期只承诺以下能力：

1. `element pick` 必须支持
2. `node pick` 先只支持 `NODAL`
3. `face_node_labels` 不纳入一期硬承诺
4. `result.value_kind` 一期只返回 `odb_raw`
5. 插值值先不进入一期协议

---

## 一期最小响应示例

### Element Pick

```json
{
  "pick_mode": "element",
  "instance": "PART-1-1",
  "render_face_idx": 123,
  "render_face_indices": [123, 124],
  "odb": {
    "elem_label": 456,
    "elem_node_labels": [11, 12, 13, 14]
  },
  "result": {
    "field": "S",
    "position": "INTEGRATION_POINT",
    "component": "S11",
    "value_kind": "odb_raw",
    "raw_values": [1.2, 1.3, 1.1, 1.4]
  }
}
```

### Node Pick

```json
{
  "pick_mode": "node",
  "instance": "PART-1-1",
  "render_face_idx": 123,
  "odb": {
    "node_label": 12,
    "elem_label": 456,
    "candidate_node_labels": [11, 12, 13]
  },
  "result": {
    "field": "U",
    "position": "NODAL",
    "component": "U1",
    "value_kind": "odb_raw",
    "raw_value": 0.0023
  }
}
```

---

## 推荐实施顺序

1. 先确认协议字段与一期边界
2. 再更新 `schemas/query.py` 与 `/query/pick` 参数
3. 先实现 `element pick`
4. 再实现 `node pick (NODAL only)`
5. 最后再讨论插值值和 `face_node_labels`
