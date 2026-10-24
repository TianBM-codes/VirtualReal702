# 试验测点与有限元节点曲线

## 有限元曲线

`POST /get/sensor_node_history`

```json
{
  "project_id": 2026101601,
  "sensors": [{"name": "DEMO_P01", "direction": [1, 0, 0]}],
  "fields": "U",
  "result_group": "default_result"
}
```

`fields` 是单个字符串，支持 `U/S/E/LE`，默认 `U`；数组或逗号分隔的多个变量会被拒绝。
测点名称来自已保存的匹配关系，方向使用有限元全局坐标，服务端单位化。
`result_group` 省略时优先使用 `default_result`，否则必须只有一个结果组。

响应沿用项目的成功/错误信封，`data` 的曲线结构与 `/sensitivity/stored/curve/parameter` 一致：

```json
{
  "project_id": 2026101601,
  "result_group": "default_result",
  "fields": "U",
  "actual_fields": ["U"],
  "curve_type": "sensor_node_history",
  "data": [{
    "label": "DEMO_P01|Step-15-30|U",
    "xaxis": ["0.0", "1.0"],
    "data": [["0.0", 0.0], ["1.0", 0.01]]
  }],
  "matches": [],
  "issues": [],
  "direction_coordinate_system": "global",
  "summary": {"sensor_count": 1, "curve_count": 1}
}
```

上例数值仅展示结构，不代表实际计算结果。

每个测点、每个 step、每个截面点分别返回一条投影曲线，所有帧均保留。
横坐标是 `frame_value` 转成的字符串；各 step 的局部时间轴分别呈现，不拼接。
模态等分析的 `frame_value` 不代表时间。壳截面点附在曲线 label 末尾。
`matches` 返回名称、测点坐标、方向及匹配节点信息。

位移为 `u·n`。张量通过 Abaqus `ELEMENT_NODAL` 外推，先坐标变换取方向的 11 分量，再按节点、截面点算术平均。
应变 `E` 在当前帧缺失时采用 `LE`，`actual_fields` 列出实际读取的字段。
缺失或投影失败的点返回 `null`，不会填零，字段提取错误保留在 `issues`。

需要已就绪的 ODB 结果组、可访问的原始 ODB 和 `APP_ABAQUS_CMD`。
ODB 只读打开，提取在独立 Abaqus Python 进程及服务器线程池执行，最长 3600 秒。

## 传感器曲线

`POST /get/sensor_load_level_curve`

```json
{"project_id": 2026101601, "sensor": "DEMO_P01", "work_condition_id": "condition-001", "measurement_id": "measurement-001"}
```

`sensor` 是传感器名称，查询限定工程，只读新表 `t_mt_py_test_load_level_data`。`work_condition_id`、`measurement_id` 为可选字符串，最长各 100 字符，按项目、测点、工况、测量精确查询；省略、null 或空字符串仅查询历史默认组。响应返回实际查询的两个标识。
每个传感器返回一条曲线，横坐标为加载级，纵坐标为实测值。旧 CSV 数据按加载值排序；
TXT 导入数据按原始记录序号排列，横轴来自 `extra_json.test_load_level_import.axis_rows`，保留重复值和卸载顺序。
指定工况或测量时，横轴来自 `extra_json.test_load_level_imports[work_condition_id][measurement_id].axis_rows`。
不要求先创建节点匹配关系，不进行方向投影、单位变换或数据插值。

响应曲线同样只含 `data/label/xaxis` 三个字段：

```json
{
  "project_id": 2026101601,
  "sensor": "DEMO_P01",
  "work_condition_id": "condition-001",
  "measurement_id": "measurement-001",
  "curve_type": "sensor",
  "data": [{
    "label": "DEMO_P01",
    "xaxis": ["0", "1"],
    "data": [["0", 0.1], ["1", 0.2]]
  }],
  "source": "t_mt_py_test_load_level_data",
  "unit": "mm",
  "summary": {"curve_count": 1, "point_count": 2}
}
```

工程 `2026101601` 的 `DEMO_P01/DEMO_P02/DEMO_P03` 已导入加载级 0、1、2 的演示读数，备注为 `example only`，并非实测数据。
其他没有读数的传感器查询返回 `data: []`。
缺失读数为 `null`，不补零、不构造模拟读数；接口只读数据库。

## 加载级试验数据表及导入脚本

表：`t_mt_py_test_load_level_data`，由 `db.py` 的初始化流程创建；导入脚本也会自动创建。

| 字段 | 含义 |
| --- | --- |
| id | 自增主键 |
| project_id | 工程 ID，BIGINT |
| sensor | 传感器名称 |
| load_level | 加载级，非负数，最多 8 位小数 |
| sample_index | TXT 原始记录序号，从 0 开始；旧 CSV 记录为空 |
| legacy_load_level | 兼容 CSV 唯一键的自动生成列，无需填写 |
| reading_value | 实测值，可为空 |
| unit | 读数单位，可为空 |
| remark | 备注，可为空 |
| created_at / updated_at | 创建及更新时间 |

CSV 记录的 `project_id + sensor + load_level` 唯一；TXT 记录的 `project_id + sensor + sample_index` 唯一。
同一传感器的曲线应使用相同单位，发现混合单位时接口报错。
加载级是试验数据自己的横坐标，与有限元的 step/frame 不自动对应。

CSV 使用 UTF-8 或 UTF-8 BOM，必需列 `sensor,load_level,value`，可选列 `unit,remark`。
空 value 存为 SQL NULL。示例：

```csv
sensor,load_level,value,unit,remark
DEMO_P01,0,0,mm,示例值
DEMO_P01,1,0.1,mm,示例值
DEMO_P01,2,0.2,mm,示例值
```

```powershell
python tools/import_test_load_levels.py --project-id 2026101601 --work-condition-id condition-001 --measurement-id measurement-001 --csv readings.csv
```

使用项目现有数据库配置。数据校验通过后整批提交；失败会回滚本批读数。
已存在同传感器、同加载级的数据默认报错，明确需要更新时增加 `--update-existing`。
只更新 CSV 中指定的加载级，不清空其他数据。

模板：`tools/examples/test_load_levels.csv`，其中数值仅用于演示；请替换为实际读数后导入。

TXT 下载导入、动态表格与数巧对比见 [SimRight 加载级对比接口](../model_update/SimRight-Load-Level-Comparison.md)。
TXT 导入支持同名的 `work_condition_id`、`measurement_id` 请求字段，仅原子替换该项目、工况、测量下的试验数据。不同组独立存储；同一组不要混入 CSV 和 TXT 行，混合格式会明确报错，避免记录错位。

## 验证

```text
python -m pytest tests/test_sensor_curve.py tests/test_sensor_node_history.py tests/test_odb_node_history_sensors.py -q
```

真实 Abaqus 集成测试（PowerShell）：

```powershell
$env:RUN_ABAQUS_HISTORY_TEST='1'
python -m pytest tests/test_sensor_node_history_abaqus.py -q
```

集成测试验证多个 step/frame、旋转局部张量坐标、斜向应变投影和 `E→LE` 回退。
