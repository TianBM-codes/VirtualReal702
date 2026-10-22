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

初次请求仅获取 TXT 动态表头、试验曲线和数巧 step 下拉选项：

```json
{
  "project_id": 2026101601,
  "simright_base_url": "http://simright-server:8080",
  "filename": "project/model.odb",
  "profile": "markdown"
}
```

`filename` 是数巧部署中的 RDB 相对路径或模型标识，不是本服务的 ODB 本地文件路径。
`profile` 支持 `markdown` 和 `excel`，分别使用现有客户端的两种接口地址及文件参数名。

前端选择加载记录对应的 step 后再次请求同一接口：

```json
{
  "project_id": 2026101601,
  "simright_base_url": "http://simright-server:8080",
  "filename": "project/model.odb",
  "sensors": [{
    "name": "310004a",
    "node_id": 123,
    "part_id": 5,
    "part_ori_id": 4,
    "quantity": "scalar",
    "var_name": "E.EL.11",
    "simulation_scale": 1000000
  }],
  "step_mapping": [
    {"sample_index": 0, "loadcase_id": 42},
    {"sample_index": 1, "loadcase_id": 91, "state_id": 2}
  ]
}
```

示例节点和 step ID 仅展示协议，必须使用实际模型中的 ID。

- 每条 `step_mapping` 对应一个试验**记录序号**，不使用加载值作为唯一标识。
- `loadcase_id` 取返回的 `selectOptions[].value`，不是工况数组索引。后端会将其转换为文档要求的 `loadcaseIndex`。
- 省略 `state_id` 时通过 `nodeInfo` 获取该变量全部帧，选择返回记录中最大的 `stateId`；指定时严格取该帧。响应 `resolved_states` 返回实际选择。
- `sensors` 是 TXT 名称与数巧节点的明确绑定，不能将 ODB 的 instance 名称直接当作数巧 part ID。`part_id` 是内部 ID，`part_ori_id` 是零件过滤需要的 oriId，二者不能互换。未提供 part ID 时，重复节点结果会报歧义。
- 标量变量名由数巧部署配置决定，`var_name` 不限制为示例值。
- 读数不自动换单位。`simulation_scale` 可显式转换，例如应变转微应变用 `1000000`。如不传则为 `1`，调用方需确保试验和仿真单位一致。
- `axis_label` 可在本次对比时切换为 TXT 的其他加载信息行。
- 选择关系通过请求传入，本接口不会持久化前端的 step 选择。重新导入 TXT 后应刷新表格再选择。
- 可将初次返回的 `import_sha256` 原样带回对比请求，文件已变化时返回原始状态为 409 的业务错误，防止旧的记录选择应用到新文件。

响应沿用现有成功/错误信封，以下为 `data` 的结构示意：

```json
{
  "project_id": 2026101601,
  "axis_label": "级别",
  "unit": "με",
  "import_sha256": "文件摘要",
  "record_indices": [0, 1, 2],
  "table": [
    {"key": "axis:0", "label": "级别", "value": [0, 1, 0]},
    {"key": "axis:1", "label": "系数", "value": [0, 0.5, 0]},
    {"key": "test:310004a", "label": "310004a", "value": [0, 10, 1]},
    {
      "key": "simright_step", "label": "仿真step", "type": "select",
      "selectOptions": [{"label": "Step-1", "value": 42}],
      "value": [null, 42, null]
    },
    {"key": "simulation:310004a", "label": "310004a-仿真", "value": [null, 9.8, null]}
  ],
  "curves": [
    {"label": "310004a-试验", "xaxis": ["0", "1", "0"], "data": [["0", 0], ["1", 10], ["0", 1]]},
    {"label": "310004a-仿真", "xaxis": ["0", "1", "0"], "data": [["0", null], ["1", 9.8], ["0", null]]}
  ],
  "resolved_states": [{"sensor": "310004a", "sample_index": 1, "loadcase_id": 42, "part_id": 5, "state_id": 7}],
  "issues": [],
  "summary": {"sample_count": 3, "sensor_count": 1, "curve_count": 2}
}
```

表头是所有 `axis_rows` 和所有 TXT 测点的动态结果，不固定为“上xx、下xx”。指定 `sensors` 时对比曲线仅包含选定测点，初次未绑定节点时返回全部试验曲线。
未选择 step 或提取失败的仿真点为 `null`，不会补零。错误位置和原因在 `issues`，试验结果仍保留。无法获取工况列表时返回原始状态为 502 的业务错误。

## 张量投影

节点绑定可设置 `quantity: "stress"` 或 `"strain"`，并提供全局 `direction`。
应变还必须指定 `shear_convention: "tensor"` 或 `"engineering"`。
默认变量为 `S.EL.11/22/33/12/13/23` 或 `E.EL.11/22/33/12/13/23`，实际部署命名不同可用 `component_vars` 显式映射六分量。

六次请求使用相同工况、帧、全局坐标系、壳层和平均选项，严格检查零件及帧身份相同后计算方向投影。
固定使用 `coordSystemId=global`、`coordSystemType=1`、`nodeAverage=1`、`avgMethod=simple`、`avgRegion=part`、`avgOrder=pre`、`useCornerData=0`；`shell_layer` 默认 `bottom`，可显式指定。
节点返回多个角点/层值时不自行取平均，记录错误等待确认服务端数值语义。数巧与 Abaqus 的平均规则是否完全一致仍需真实模型对照。

## 验证

```powershell
python -m pytest tests/test_simright_load_level_comparison.py tests/test_sensor_curve.py tools/simright/tests/test_simright.py -q
$env:RUN_LOAD_LEVEL_MYSQL_TEST='1'
python -m pytest tests/test_load_level_live.py -q
```

后者使用配置的 MySQL 与本机临时 HTTP 服务，验证真实下载、SQL 存储、元数据合并、重复加载值和数巧请求协议。测试读数在事务末回滚，数据库结构迁移保留。数巧 HTTP 响应在测试服务中模拟，不等同于真实数巧部署联调。
