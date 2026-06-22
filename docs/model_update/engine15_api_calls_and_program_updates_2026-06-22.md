# engine15 接口调用与主程序改动建议

日期：2026-06-22

## 1. 哪些值得更新到主程序

这次工作里，建议区分为两类：

### 1.1 建议直接保留到主程序的改动

这几项属于通用修复，不是只针对 `engine15`：

1. `services/model_update/importers/op2_service.py`
   - 当项目目录下没有 `manifest.db` 时，参数映射不再直接失败。
   - 对纯 Nastran/BDF 场景回退为单实例 `BDF_MODEL`。
   - 这条修复建议保留。

2. `services/model_update/analysis/bayesian_service.py`
   - `SOL200` 模态频率 Bayesian 迭代保存历史时，不再强依赖 `manifest.db`。
   - 这条修复建议保留。

3. `services/model_update/analysis/bayesian_service.py`
   - 修复 `exit_diff_percent` 未传时 `exit_check is None` 导致的空指针。
   - 这条修复建议保留。

4. `tests/test_op2_sensitivity_store_cloud.py`
   - 为上面的 Nastran-only 工作区兜底补了测试。
   - 这条测试建议保留。

### 1.2 建议写入主程序默认行为的内容

结合这次和 FEMTools 的对比结果，下面这项建议直接进入主程序默认值：

1. `all_elements_e` / `all_used_material_e_rho` 的参数默认上下界
   - 默认改为宽松边界：
   - `lower_scale = 0.01`
   - `upper_scale = 1.0e6`
   - 这和本次 `engine15` 调试结论一致，也更接近 FEMTools 中“下界可接近 0、上界基本不限制”的使用方式。
   - 调用方仍然可以显式传 `lower_scale/upper_scale` 覆盖默认值。

2. 只保留 `FE1 -> TEST1`
   - 这是本次调试策略，不是通用默认逻辑。

3. `iterations=8`
   - 这也是本次对标 FEMTools 的试验设置，不宜直接写成全局默认。

### 1.3 建议后续再做的主程序能力

如果要让前后端长期更好用，建议后面加一个明确能力：

1. 给 `all_elements_e` 增加“边界策略”选项
   - 例如：
   - `default_relaxed`: `0.01x ~ 1.0e6x`
   - `legacy_conservative`: `0.8x ~ 1.2x`
   - `custom`: 调用方显式传 `lower_scale/upper_scale`

2. 给 `SOL200 Bayesian` 增加“单模态调试模式”说明
   - 便于像这次一样快速只盯住 `FE1 -> TEST1`

## 2. 本次实际调用的接口

本次联调项目：

- `project_id = 15062215`
- 试验文件：`C:\FEMtools\3.7.1\examples\updating\engine\ema15.unv`
- FEM 文件：`C:\FEMtools\3.7.1\examples\updating\engine\sol103_final.bdf`

## 3. 基础导入与匹配链路

### 3.1 导入试验 UNV

接口：

- `POST /import/unv`

请求体：

```json
{
  "file_path": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\ema15.unv",
  "project_id": 15062215,
  "file_id": 15062215,
  "clear_before_insert": true
}
```

### 3.2 导入 BDF

接口：

- `POST /import/bdf`

请求体：

```json
{
  "file_path": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "project_id": 15062215,
  "clear_before_insert": true
}
```

### 3.3 运行 SOL103 并导入 12 阶模态

接口：

- `POST /solver/nastran/sol103/run_and_store_modal`

请求体：

```json
{
  "project_id": 15062215,
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "settings": {
    "dynamic.vectors": 12,
    "dynamic.fmin": 100.0,
    "result.target": "OP2",
    "post": -1
  },
  "timeout_sec": 1200,
  "overwrite": true,
  "mode_numbers": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
}
```

### 3.4 节点匹配

接口：

- `POST /match/nodes`

请求体：

```json
{
  "project_id": 15062215,
  "overwrite": true
}
```

### 3.5 自由度匹配

接口：

- `POST /match/dofs`

请求体：

```json
{
  "project_id": 15062215,
  "overwrite": true
}
```

### 3.6 计算模态相关性

接口：

- `POST /correlation/modal/compute`

请求体：

```json
{
  "project_id": 15062215,
  "overwrite": true,
  "mac_threshold": 0
}
```

### 3.7 从模态匹配结果生成频率响应目录

接口：

- `POST /optimization/response/modal_frequency/create_from_match`

请求体：

```json
{
  "project_id": 15062215,
  "overwrite": true,
  "mac_threshold": 0,
  "max_freq_error_ratio": 1.0,
  "matching_method": "greedy",
  "scatter": 0.05
}
```

## 4. 双响应版本：FREQ_MODE_1 + FREQ_MODE_2

### 4.1 运行 SOL200 频率灵敏度

接口：

- `POST /solver/nastran/sol200/run_and_store`

请求体：

```json
{
  "project_id": 15062215,
  "batch_no": "15062215",
  "case_name": "engine15_all_elements_e_freq12",
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "parameter_preset": {
    "preset": "all_elements_e",
    "lower_scale": 0.01,
    "upper_scale": 1000000.0
  },
  "responses": [
    {
      "name": "FREQ_MODE_1",
      "type": "FREQ",
      "mode_number": 1
    },
    {
      "name": "FREQ_MODE_2",
      "type": "FREQ",
      "mode_number": 2
    }
  ],
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "result.target": "F06",
    "post": -1,
    "dynamic.vectors": 12,
    "dynamic.fmin": 100.0,
    "dynamic.norm": "MASS"
  },
  "run_solver": true,
  "timeout_sec": 1800,
  "write_cloud_result": false
}
```

### 4.2 运行 SOL200 Bayesian 8 步

接口：

- `POST /optimization/bayesian/sol200/modal_frequency/run`

请求体：

```json
{
  "project_id": 15062215,
  "batch_no": 15062218,
  "sensitivity_batch_no": 15062215,
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "save_results": true,
  "iterations": 8,
  "step_scale": 1.0,
  "damping": 1e-08,
  "mac_threshold": 0,
  "max_freq_error_ratio": 1.0,
  "matching_method": "greedy",
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "result.target": "F06",
    "post": -1,
    "dynamic.vectors": 12,
    "dynamic.fmin": 100.0,
    "dynamic.norm": "MASS"
  },
  "timeout_sec": 1800,
  "write_cloud_result": false
}
```

## 5. 单响应版本：只保留 FREQ_MODE_1

### 5.1 运行 SOL200 频率灵敏度

接口：

- `POST /solver/nastran/sol200/run_and_store`

请求体：

```json
{
  "project_id": 15062215,
  "batch_no": "15062221",
  "case_name": "engine15_all_elements_e_freq1_only",
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "parameter_preset": {
    "preset": "all_elements_e",
    "lower_scale": 0.01,
    "upper_scale": 1000000.0
  },
  "responses": [
    {
      "name": "FREQ_MODE_1",
      "type": "FREQ",
      "mode_number": 1
    }
  ],
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "result.target": "F06",
    "post": -1,
    "dynamic.vectors": 12,
    "dynamic.fmin": 100.0,
    "dynamic.norm": "MASS"
  },
  "run_solver": true,
  "timeout_sec": 1800,
  "write_cloud_result": false
}
```

### 5.2 运行 SOL200 Bayesian 8 步

接口：

- `POST /optimization/bayesian/sol200/modal_frequency/run`

请求体：

```json
{
  "project_id": 15062215,
  "batch_no": 15062222,
  "sensitivity_batch_no": 15062221,
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "save_results": true,
  "iterations": 8,
  "step_scale": 1.0,
  "damping": 1e-08,
  "mac_threshold": 0,
  "max_freq_error_ratio": 1.0,
  "matching_method": "greedy",
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "result.target": "F06",
    "post": -1,
    "dynamic.vectors": 12,
    "dynamic.fmin": 100.0,
    "dynamic.norm": "MASS"
  },
  "timeout_sec": 1800,
  "write_cloud_result": false
}
```

## 6. 单响应宽边界版本：FREQ_MODE_1，边界接近 FEMTools

说明：

- 这个版本是为了对标 FEMTools
- 下界放到初值的 `1%`
- 上界放到极大值，等效近似“不限制上限”

### 6.1 运行 SOL200 频率灵敏度

接口：

- `POST /solver/nastran/sol200/run_and_store`

请求体：

```json
{
  "project_id": 15062215,
  "batch_no": "15062231",
  "case_name": "engine15_all_elements_e_freq1_relaxed_bounds",
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "parameter_preset": {
    "preset": "all_elements_e",
    "lower_scale": 0.01,
    "upper_scale": 1000000.0
  },
  "responses": [
    {
      "name": "FREQ_MODE_1",
      "type": "FREQ",
      "mode_number": 1
    }
  ],
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "result.target": "F06",
    "post": -1,
    "dynamic.vectors": 12,
    "dynamic.fmin": 100.0,
    "dynamic.norm": "MASS"
  },
  "run_solver": true,
  "timeout_sec": 1800,
  "write_cloud_result": false
}
```

### 6.2 运行 SOL200 Bayesian 8 步

接口：

- `POST /optimization/bayesian/sol200/modal_frequency/run`

请求体：

```json
{
  "project_id": 15062215,
  "batch_no": 15062232,
  "sensitivity_batch_no": 15062231,
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\engine\\sol103_final.bdf",
  "save_results": true,
  "iterations": 8,
  "step_scale": 1.0,
  "damping": 1e-08,
  "mac_threshold": 0,
  "max_freq_error_ratio": 1.0,
  "matching_method": "greedy",
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "result.target": "F06",
    "post": -1,
    "dynamic.vectors": 12,
    "dynamic.fmin": 100.0,
    "dynamic.norm": "MASS"
  },
  "timeout_sec": 1800,
  "write_cloud_result": false
}
```

## 7. 本次最值得保留的结论

1. 主程序里值得保留的是“纯 Nastran/BDF 项目不再强依赖 manifest.db”这组修复。
2. 宽边界不是通用默认值，但很值得做成可选模式。
3. 对 `engine15` 第一阶来说，边界放宽后效果明显改善，说明之前“变化慢”的首要原因确实是参数边界太紧。
