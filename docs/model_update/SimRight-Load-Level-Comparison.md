# TXT 加载级试验导入与 SimRight 对比

四个近期接口的速查、调用顺序及公共响应约定，见 [近期试验与仿真接口汇总](近期试验与仿真接口汇总.md)。
当前 Web 路由捕获的错误统一经 `error_response` 返回 HTTP 500/业务码 500，
下文提到的 409、502 是原始状态，在 `error.details.original_status_code` 中保留；请求模型校验错误由 FastAPI 单独处理。

## 导入 TXT

`POST /import/test_load_level`

```json
{
  "project_id": 2026101601,
  "file_path": "https://files.example/test-data.txt",
  "ignore_last_reading": true
}
```

- `project_id` 由外部提供，不从文件推断，不创建新的有限元工程。
- `file_path` 支持 HTTP/HTTPS 下载链接和服务器本地路径。原文件归档到工程目录的 `test_data/load_level/<导入标识>/source.txt`。
- 复用 `tools/DumpTestDataToDB.py::parse_txt`，支持 GB18030、定宽和换页符。这里只调用解析函数，不启动绘图和交互。
- `ignore_last_reading` 默认 `true`：按本次试验格式排除每个测点最后一个读数。被排除的值仍保存在元数据，不参与表格或曲线。
- `axis_label` 可选，默认使用文件中第一行加载信息的原始名称。指定其他行可更换横轴，不固定为“级别”或“系数”。所有加载信息行始终完整保留。
- `unit` 可选；未指定时尝试读取表头的 `单位:`，不能识别则留空，不根据绘图默认标签推断。
- 先下载、完整解析和校验，成功后在一个事务内替换该工程全部试验读数及当前导入元数据；失败保留旧数据。重新导入会替换该工程之前的 CSV/演示读数，但不修改有限元节点匹配。

读数仍写入 `t_mt_py_test_load_level_data`。新增 `sample_index` 保存从零开始的原始记录序号，允许加载值重复、回零或下降。旧 CSV 记录的 `sample_index` 为空，继续按旧的加载值查询及去重。辅助生成列 `legacy_load_level` 用于兼容旧 CSV 唯一键，无需客户端填写。

元数据保存位置：

```text
t_mt_py_project_config.extra_json.test_load_level_import
```

包含 `axis_rows: [{label,value}]`（数量不固定）、测点名称与顺序、默认横轴、单位、试验起始日期时间、页数、每页记录数、各页原始表头和加载信息、末值排除规则及被排除值、源链接、本地文件位置、SHA256 和导入时间。`frequency=40` 只是旧脚本默认值，不作为实测采样频率入库。

各加载信息行必须长度一致，排除末值后的每个测点读数必须与其等长。不同页若重复了加载信息，内容必须一致；不会静默截断、补零或将不同加载过程拼接起来。

仓库外实际测试文件 `D:\WorkSpace\WebThreeJS\PyModel2JsonDataFolder\test_data\AT-2_lire.txt` 解析出 80 个测点、18 条有效记录、两行加载信息；原始第 19 个读数按上述规则排除。该路径也可直接传给 `file_path`。

## 单测点曲线

沿用 `POST /get/sensor_load_level_curve`：

```json
{"project_id": 2026101601, "sensor": "310004a"}
```

TXT 数据按 `sample_index` 排列，用导入元数据中选定的横轴生成曲线，保留重复横坐标及卸载顺序。
曲线仍是 `{label, xaxis, data:[[x,y],...]}`。旧 CSV 数据继续按数值加载级排序。

## SimRight 综合对比

`POST /get/simright_test_load_level_comparison`

请求使用 `Content-Type: application/json`。所有参数放在 JSON 请求体中。
已移除请求参数 `filename`、`step_mapping`；`sensors` 每项只允许 `name`。

### 请求参数与两种用途

| 参数 | 类型/默认值 | 说明 |
| --- | --- | --- |
| `project_id` | 正整数，必填 | 外部传入的项目 ID |
| `example_mode` | bool，默认 true | true 读取示例文件；false 查询数据库和数巧 |
| `sensors` | 对象列表，可省略或为 null | 如 `[{"name":"310004a"}]`；省略、null、空列表均只返回加载信息和 step 下拉列，不返回测点列或曲线 |
| `steps` | step ID/null 列表，可省略 | 每个位置对应一条试验加载记录；省略或 null 时全部未选择；显式列表长度必须等于有效记录数 |
| `simright_base_url` | URL，可省略 | 真实模式的数巧地址；省略时读取项目配置 `simright.base_url` |
| `profile` | markdown/excel，默认 markdown | 数巧客户端协议配置 |
| `axis_label` | 字符串，可省略 | 默认采用 TXT 导入时的横轴；可切换为其他加载信息行 |
| `shell_layer` | 字符串，默认 bottom | 数巧壳层 |
| `import_sha256` | 字符串，可省略 | 真实模式校验 TXT 是否已重新导入，防止记录错位 |

`steps` 替代原来的 `step_mapping`。例如 `[42, null, 91]` 表示第 1 条试验记录选 step 42，第 2 条未选择，第 3 条选 step 91。ID 取 `table` 中 `key=simright_step` 列的 `selectOptions[].value`，不是选项数组下标。JSON 中使用 `null`，对应 Python 的 `None`。

只提取用户选择的 step。真实模式对每个节点和变量取该 step 返回的最大 `stateId`；相同 step、节点和变量在单次请求内复用结果。未选择的位置仿真值为 null，试验读数保留。选择关系由每次请求传入，不在数据库中持久化。

### 示例模式：先取表头，再取指定测点曲线

当前默认示例模式，不访问 MySQL 或数巧。示例可在 [simright_load_level_comparison.json](../../tools/examples/simright_load_level_comparison.json) 中修改。
文件中的“上xx、下xx”属于动态加载信息，与测点 `310004a`、`310005a` 分开保存；示例有效记录数为 4。

首次请求：

```json
{"project_id": 2026101601}
```

返回完整成功信封示例（示例来源字段和 summary 等也保留）：

```json
{
  "ok": true,
  "code": 200,
  "message": "数巧加载级试验对比示例获取成功",
  "data": {
    "project_id": 2026101601,
    "is_example": true,
    "data_source": "example",
    "axis_label": "载荷",
    "unit": "με",
    "import_sha256": null,
    "record_indices": [0, 1, 2, 3],
    "table": [
      {"key": "axis:0", "label": "载荷", "value": [0, 1, 2, 3]},
      {"key": "axis:1", "label": "上xx", "value": [0, 10, 20, 30]},
      {"key": "axis:2", "label": "下xx", "value": [0, -8, -16, -24]},
      {
        "key": "simright_step", "label": "仿真step", "type": "select",
        "selectOptions": [
          {"label": "下拉1", "value": 0}, {"label": "下拉3", "value": 1},
          {"label": "下拉5", "value": 2}, {"label": "下拉7", "value": 3}
        ],
        "value": [null, null, null, null]
      }
    ],
    "curves": [],
    "resolved_states": [],
    "issues": [],
    "summary": {"sample_count": 4, "sensor_count": 0, "curve_count": 0}
  }
}
```

用户选择 step 后调用同一接口：

```json
{
  "project_id": 2026101601,
  "sensors": [{"name": "310004a"}, {"name": "310005a"}],
  "steps": [0, null, 2, 3]
}
```

此时 `data.table` 仍只包含动态加载信息列和 step 下拉列。测点的试验、仿真读数统一放在 `data.curves`，不再以 `test:*` 或 `simulation:*` 列重复返回。本次选择两个测点，因此返回四条曲线。下面展示其中一个测点对应的两条曲线：

```json
[
  {
    "label": "310004a-试验",
    "xaxis": ["0", "1", "2", "3"],
    "data": [["0", 0], ["1", 10], ["2", 20], ["3", 30]]
  },
  {
    "label": "310004a-仿真",
    "xaxis": ["0", "1", "2", "3"],
    "data": [["0", 0], ["1", null], ["2", 19.5], ["3", 29.5]]
  }
]
```

示例模式也会校验名称、steps 长度和 step ID，并根据用户选择提取示例值；不会随意给未知测点生成假曲线。示例仿真数组按 step 选项顺序提供数值，所有示例 step 必须有对应值。

### 真实模式与服务端项目配置

真实模式先通过 `/import/test_load_level` 导入 TXT，再调用本接口。每次真实请求都需要 `example_mode: false`。

```json
{
  "project_id": 2026101601,
  "example_mode": false,
  "sensors": [{"name": "310004a"}],
  "steps": [42, null, 91]
}
```

这里假设真实 TXT 有 3 条有效记录；实际长度应按首次响应的 `record_indices` 确定。首次只获取表头时省略 `sensors`、`steps` 即可。

模型标识不再由对比请求传入。使用现有 `POST /project/config` 配置，保存到 `t_mt_py_project_config.extra_json.simright`：

```json
{
  "project_id": 2026101601,
  "extra_json": {
    "simright": {
      "base_url": "http://simright-server:8080",
      "filename": "project/model.rdb",
      "instance_parts": {
        "PART-1-1": {"part_id": 5, "part_ori_id": 4}
      },
      "sensor_bindings": {
        "310004a": {
          "quantity": "scalar",
          "var_name": "E.EL.11",
          "simulation_scale": 1000000
        }
      }
    }
  }
}
```

配置示例需替换为实际地址、数巧模型标识、实例、零件及变量名。更新 `simright` 时应提交该块完整配置，项目配置合并规则不会递归合并其内部字段。

- 按 `project_id + name` 查询 `t_mt_py_fem_node_match`，使用 `fem_node_label`；实例通过 `instance_parts` 映射到数巧零件，不能直接把实例名当作 part ID。
- `sensor_bindings` 配置每个测点的结果变量、投影与单位换算；也允许在服务端明确配置 `node_id`、`part_id`、`part_ori_id`，适用于数巧节点编号与本地模型不同的情况。显式节点配置优先。
- `part_id` 是内部 ID，`part_ori_id` 是过滤用的 oriId；配置 oriId 时必须同时配置内部 ID。未配置零件时，仅在结果中的节点没有零件歧义时允许返回。
- 标量查询需要 `quantity=scalar` 和 `var_name`。张量查询配置 `quantity=stress/strain`、全局 `direction`；应变还需要 `shear_convention=tensor/engineering`。`component_vars` 可映射六分量，默认使用 `S.EL.*` / `E.EL.*`。
- `simulation_scale` 默认 1，不自动换算单位。试验与仿真单位必须一致，例如应变转微应变可以配置 1000000。
- 缺少模型配置、匹配或变量设置时明确报错；全部未选 step 时不查询节点结果或解析节点绑定。

### 响应与错误约定

曲线格式仍为 `{label, xaxis, data:[[x,y],...]}`，放在 `data.curves`。`table` 每个对象表示一列，所有 `value[i]` 对应同一条试验记录，加载信息列数量不固定。
`summary.sensor_count` 是本次返回的测点数；不传 sensors 时为 0。每个选定测点返回两条曲线，即使仿真值全部为 null。
未选择 step 不产生 issue；选择后单点提取失败会保留试验值、将仿真值设为 null，并在 `issues` 中记录测点、记录序号与原因。成功提取的零件和帧保存在 `resolved_states`。数巧工况请求整体失败时返回错误。

请求中的旧 `filename`、`step_mapping` 或 sensors 内的 `node_id` 等额外字段会触发请求模型校验错误（HTTP 422）。服务业务错误沿用本文开头说明的 HTTP 500/业务码 500 信封，原始状态保存在 `error.details.original_status_code`。真实数巧服务尚未联调。
