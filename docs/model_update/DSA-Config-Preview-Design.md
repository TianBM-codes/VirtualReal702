# DSA Config Preview

## 目标

第一阶段只实现“数据库 -> DSA JSON 配置预览”。接口只读数据库，不调用 Abaqus，不生成最终 inp，也不改已有灵敏度求解、误差分析、VTU 导出链路。

接口：

```http
POST /sensitivity/dsa/config/preview
```

请求：

```json
{
  "project_id": 1001,
  "value_mode": "inherit"
}
```

`value_mode` 支持：

- `inherit`：`element_sets[]` 不写 `value`，后续生成器从原始 shell section 继承厚度。
- `explicit`：`element_sets[]` 写入 `value = current_value`，更稳，不依赖原始厚度继承。

## 参数生成

参数数据来自 `t_mt_py_fem_selected_parameter`，第一版只读取 `quantity_code = H`。

每条壳厚参数生成一个 `element_sets[]` 项：

```json
{
  "set_name": "DSA_T1",
  "parameter": "T1",
  "elements": [101, 102],
  "value": 2.5
}
```

规则：

- `parameter` 使用 `parameter_name`。
- `set_name` 自动生成，形如 `DSA_<parameter_name>`，并自动加后缀避免本次配置内重名。
- `elements` 优先读取 `extra_json.element_labels`。
- 如果没有 `extra_json.element_labels`，退化读取 `element_label`。
- 如果参数记录缺少展开后的单元号，会按同一 `set_name/set_type/set_scope/instance_name/part_name` 从 `t_mt_py_fem_quantity_set_capability.extra_json.element_labels` 兜底补齐，并返回告警。
- `explicit` 模式写入 `value`。
- `inherit` 模式不写 `value`。

## 响应生成

响应数据来自 `t_mt_py_fem_design_response_catalog`。

每条响应生成一个 `responses[]` 项：

```json
{
  "type": "node",
  "set": "RESP_NODES",
  "variables": ["U2"]
}
```

规则：

- `region_type = NODE` 映射为 `type = "node"`。
- `region_type = ELEMENT` 映射为 `type = "element"`。
- `set` 直接使用数据库中的 `set_name`。
- `variables` 使用 `variables_json` 解析后的非空列表。
- 第一版只引用已有 set，不为响应新建 node set 或 element set。
- 现有生成器会把响应写入最后一个分析步。

## 告警

预览接口不会因为普通数据缺失立刻中断，而是返回 `warnings` 供人工检查。当前包括：

- 参数没有 `element_labels` 且没有 `element_label`。
- 参数记录没有单元号，但已从 quantity/set capability 兜底补齐。
- `explicit` 模式下参数值为空。
- 响应变量为空。
- 响应 set 名为空。
- 响应区域类型不是 `NODE` 或 `ELEMENT`。
- 自动生成的 DSA 集合名重复并被改名。
- `inherit` 模式下提醒：如果参数覆盖多个原始 section 且厚度不一致，后续生成 inp 会报错。

## 边界

这个接口只是把数据库记录翻译成壳厚 DSA 生成器能理解的 JSON 草稿。它不负责验证原始 inp 中 set 是否真实存在，也不处理 instance/part 歧义；这些留到下一阶段生成 inp 或人工检查时处理。

## 生成 INP

确认预览合理后，可以调用：

```http
POST /sensitivity/dsa/inp/generate
```

请求：

```json
{
  "project_id": 1001,
  "input_inp": "C:/demo/model.inp",
  "output_dir": "C:/demo/sensitivity",
  "value_mode": "explicit"
}
```

可选字段：

- `output_inp`：生成后的主 inp 路径；默认是 `<原文件名>_dsa.inp`。
- `include_file`：生成的 include 文件名；默认是 `include.inp`，主 inp 里也会写 `*Include, input=include.inp`。
- `config_file`：落盘保存的 JSON 配置路径；默认是 `dsa_config.json`。

生成接口会复用预览接口的配置构造逻辑，并调用现有壳厚 DSA 生成器写出：

- 新主 inp：补 `*Include`，把最后一个分析步改为 DSA，并把响应写入最后一个分析步。
- `include.inp`：写入 `*PARAMETER`、`*DESIGN PARAMETER`、新 `*ELSET` 和新 `*SHELL SECTION`。
- `dsa_config.json`：保存本次生成使用的 JSON 配置，便于人工复查。

和预览不同，生成接口会拦截关键缺失数据。如果参数没有可用单元号、响应没有 set、响应变量为空，或者 explicit 模式下参数值为空，接口会返回校验错误，不会写 inp。
