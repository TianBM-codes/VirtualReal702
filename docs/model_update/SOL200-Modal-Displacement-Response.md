# SOL200 Modal Displacement Response

## 背景

SOL200 原来只支持模态频率响应，也就是 `DRESP1 ... FREQ`。这类响应只需要一个模态阶次，例如“第 1 阶频率”。

振型位移分量响应对应 Nastran 的 `DRESP1 ... DISP`。大白话说，它不是取“这一阶的频率是多少”，而是取“这一阶振型里，某个节点在某个方向上的位移分量是多少”。

因此一个 `DISP` 响应至少需要三个定位量：

| 字段 | 含义 | 示例 |
|---|---|---|
| `mode_number` | 第几阶模态 | `1` |
| `node_id` | 取哪个节点 | `3` |
| `component` | 取哪个方向分量 | `U3` |

## API 配置

`/optimization/sol200/response/create` 现在支持：

```json
{
  "project_id": 1,
  "response_name": "MODE1_NODE3_U3",
  "response_type": "DISP",
  "mode_number": 1,
  "node_id": 3,
  "component": "U3"
}
```

`response_type` 也兼容 `MODAL_DISPLACEMENT` 和 `NODAL_DISPLACEMENT`，内部都会转成 SOL200 使用的 `DISP`。

## 生成的 BDF

上面的配置会生成类似：

```text
DRESP1,2,MODE1_NODE3_U3,DISP,,,3,1,3
DCONSTR,1,2,-1.0E30,1.0E30
```

这里 `DISP,,,3,1,3` 后面的三个数字分别是：

| 位置 | 含义 |
|---|---|
| `3` | 位移分量，`U3` 会转成 Nastran 的数字分量 `3` |
| `1` | 第 1 阶模态 |
| `3` | 节点号 3 |

## 结果回读

格式化灵敏度 CSV 回读现在除了 `EIGN/FREQ` 频率响应，也会识别 `DISP` 响应块。优先按 `response_name` 匹配，所以建议响应名和生成 BDF 时保持一致。
