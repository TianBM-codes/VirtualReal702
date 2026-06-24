# FRF接口补充说明

本文档补充说明频响曲线 FRF 相关接口，格式参照《模型修正接口清单》中的接口说明小节。

### `POST /import/unv`

接口含义：导入试验 UNV 文件。

功能说明：兼容 UNV 文件中 `dataset 55` 和 `dataset 58` 的解析。  
当存在 `dataset 55` 时，解析试验节点、单元、模态或静力结果，并写入试验相关表；同时会把测点信息写入 `t_mt_measuring_point_info`。  
当存在 `dataset 58` 时，解析 FRF 频响曲线，并写入 `t_mt_py_test_frf_curve` 和 `t_mt_py_test_frf_point`。  
当同一文件同时包含 `dataset 55` 和 `dataset 58` 时，会按语义段落分别执行两个分支。  
对于 FRF 数据，不区分 `file_id`，以 `curve_name` 作为业务唯一键，同名曲线覆盖，不同名曲线增量保存。

影响数据库：

- `t_mt_py_test_node`、`t_mt_py_test_element`：写，保存试验节点和单元。
- `t_mt_py_test_modal_frequency`、`t_mt_py_test_modal_shape`、`t_mt_py_test_modal_shape_real`、`t_mt_py_test_modal_shape_imag`：写，保存模态频率与振型。
- `t_mt_py_test_static_result`：写，保存静力结果。
- `t_mt_py_test_frf_curve`、`t_mt_py_test_frf_point`：写，保存频响曲线及频点数据。
- `t_mt_measuring_point_info`：写，保存或刷新测点信息。
- `t_mt_work_condition_project`：写，更新项目试验数据状态。

请求参数：

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file_path` | `string` | 是 | UNV 文件路径或 URL |
| `project_id` | `int` | 否 | 项目 ID，代码层面允许为空，但业务上建议必传 |
| `file_id` | `int` | 否 | 文件 ID；FRF 分支不按该字段区分覆盖范围 |
| `clear_before_insert` | `bool` | 否 | 是否先清空该项目已有试验导入数据；仅对 `dataset 55` 分支生效，默认 `true` |

请求示例：

```json
{
  "file_path": "D:/demo/test.unv",
  "project_id": 101,
  "clear_before_insert": true
}
```

返回 `data` 示例：

```json
{
  "file_path": "D:/demo/test.unv",
  "result_kind": "dynamic",
  "imported_sections": ["dataset55", "dataset58"],
  "result_kinds": ["modal_or_static", "frf"],
  "cleared_before_insert": true,
  "test_node_count": 120,
  "measuring_point_count": 120,
  "test_element_count": 80,
  "test_mode_count": 6,
  "test_static_result_count": 0,
  "frf_curve_count": 48,
  "frf_point_count": 9600,
  "frf_curve_names": [
    "TEST FRF 1 (+3UZ : +29UZ)",
    "TEST FRF 2 (+5UX : +29UZ)"
  ]
}
```

### `POST /frf/names`

接口含义：获取当前项目所有已解析的 FRF 曲线名称。

功能说明：从 `t_mt_py_test_frf_curve` 中查询当前 `project_id` 下的所有曲线名称，用于前端初始化曲线选择框。

影响数据库：

- `t_mt_py_test_frf_curve`：读，返回所有 FRF 曲线名称。

请求参数：

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | `int` | 是 | 项目 ID |

请求示例：

```json
{
  "project_id": 101
}
```

返回 `data` 示例：

```json
{
  "project_id": 101,
  "names": [
    "TEST FRF 1 (+3UZ : +29UZ)",
    "TEST FRF 2 (+5UX : +29UZ)"
  ]
}
```

### `POST /frf/curve`

接口含义：获取指定 FRF 曲线的实部、虚部、幅值或相位序列。

功能说明：按曲线名称定位 `t_mt_py_test_frf_curve` 中的曲线，再从 `t_mt_py_test_frf_point` 读取频点数据，返回用于绘图的频率与数值序列。  
`index` 取值含义如下：

- `1`：实部 `Real`
- `2`：虚部 `Imaginary`
- `3`：幅值 `Magnitude`
- `4`：相位 `Phase`，计算方式为 `angle(real + 1j*imag, deg=True)`，不做 `unwrap`

影响数据库：

- `t_mt_py_test_frf_curve`：读，按名称定位曲线。
- `t_mt_py_test_frf_point`：读，返回曲线的频点序列。

请求参数：

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | `int` | 是 | 项目 ID |
| `name` | `string` | 否 | 单条 FRF 曲线名称 |
| `names` | `string[]` | 否 | 多条 FRF 曲线名称列表；与 `name` 二选一，或同时传入 |
| `index` | `int` | 是 | 数据类型，`1=实部`，`2=虚部`，`3=幅值`，`4=相位` |

请求示例：

```json
{
  "project_id": 101,
  "name": "TEST FRF 1 (+3UZ : +29UZ)",
  "index": 4
}
```

返回 `data` 示例：

```json
{
  "project_id": 101,
  "names": [
    "TEST FRF 1 (+3UZ : +29UZ)",
    "TEST FRF 2 (+5UX : +29UZ)"
  ],
  "line_name": "TEST FRF 1 (+3UZ : +29UZ)",
  "x_name": "Frequency[Hz]",
  "y_name": "Phase",
  "x": [1.0, 2.0],
  "series": [
    [1.0, 12.5],
    [2.0, 15.2]
  ]
}
```

多曲线请求示例：

```json
{
  "project_id": 101,
  "names": [
    "TEST FRF 1 (+3UZ : +29UZ)",
    "TEST FRF 2 (+5UX : +29UZ)"
  ],
  "index": 3
}
```

多曲线返回 `data` 示例：

```json
{
  "project_id": 101,
  "names": [
    "TEST FRF 1 (+3UZ : +29UZ)",
    "TEST FRF 2 (+5UX : +29UZ)"
  ],
  "x_name": "Frequency[Hz]",
  "y_name": "Magnitude",
  "line_name": "TEST FRF 1 (+3UZ : +29UZ)",
  "line_names": [
    "TEST FRF 1 (+3UZ : +29UZ)",
    "TEST FRF 2 (+5UX : +29UZ)"
  ],
  "x": [1.0, 2.0],
  "series": [
    [1.0, 12.5],
    [2.0, 15.2]
  ],
  "lines": [
    {
      "line_name": "TEST FRF 1 (+3UZ : +29UZ)",
      "x": [1.0, 2.0],
      "series": [
        [1.0, 12.5],
        [2.0, 15.2]
      ]
    },
    {
      "line_name": "TEST FRF 2 (+5UX : +29UZ)",
      "x": [1.0, 2.0],
      "series": [
        [1.0, 10.2],
        [2.0, 11.6]
      ]
    }
  ]
}
```
