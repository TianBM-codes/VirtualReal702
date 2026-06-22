# MAC Modal Sensitivity Standalone

## 目标

这个工具先解决两件最小但关键的事：

1. 生成 `SOL200` 灵敏度分析用的 `BDF`
2. 不依赖求解器，直接从外部矩阵计算 `MAC` 和 `dMAC/dp`

这样可以先把“公式对不对”和“BDF 卡片对不对”拆开验证。

脚本位置：

- [mac_modal_sensitivity_standalone.py](D:/WorkSpace/OtherProjects/VirtualReal702/tools/mac_modal_sensitivity_standalone.py)

## 子命令 1：生成 SOL200 BDF

命令：

```bash
python tools/mac_modal_sensitivity_standalone.py build-sol200-bdf --config build_sol200.json --output build_sol200_result.json
```

最小配置示例：

```json
{
  "input_bdf": "D:/demo/model.bdf",
  "output_bdf": "D:/demo/model_sol200.bdf",
  "parameter_preset": {
    "preset": "all_elements_e",
    "lower_scale": 0.01,
    "upper_scale": 1000000.0
  },
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "dynamic.norm": "MASS",
    "dynamic.vectors": 10,
    "result.target": "OP2"
  },
  "frequency_response_modes": [1, 2, 3],
  "modal_displacement_groups": [
    {
      "mode_numbers": [1, 2, 3],
      "node_ids": [101, 102, 103, 104, 105],
      "component": "U3",
      "name_template": "PHI_M{mode}_N{node}_{component}"
    }
  ]
}
```

说明：

- `frequency_response_modes` 会自动展开成 `DRESP1 FREQ`
- `modal_displacement_groups` 会自动展开成 `DRESP1 DISP`
- 如果你已经手工写好了响应，也可以直接传 `responses`
- 这个命令只生成 deck，不会自动跑求解器

## 子命令 2：直接计算 MAC 灵敏度

命令：

```bash
python tools/mac_modal_sensitivity_standalone.py compute-mac-sensitivity --config mac_matrix.json --output mac_result.json
```

最小配置示例：

```json
{
  "index_base": 1,
  "phi_exp": [
    [1.0, 0.1],
    [0.5, 0.3],
    [0.2, 0.8]
  ],
  "phi_sim": [
    [0.9, 0.2],
    [0.45, 0.35],
    [0.25, 0.75]
  ],
  "dphi_dp": [
    [
      [0.01, 0.02],
      [0.03, 0.01],
      [0.02, -0.01]
    ],
    [
      [0.00, 0.03],
      [0.01, 0.02],
      [0.02, 0.01]
    ]
  ],
  "pairs": [
    { "exp_mode": 1, "sim_mode": 1 },
    { "exp_mode": 2, "sim_mode": 2 }
  ],
  "parameter_names": ["E1", "E2"],
  "sensor_labels": ["N101_U3", "N102_U3", "N103_U3"]
}
```

## 输入矩阵的形状

`compute-mac-sensitivity` 的内部约定如下：

- `phi_exp`: `N_sensor x N_mode_exp`
- `phi_sim`: `N_sensor x N_mode_sim`
- `dphi_dp`: `N_mode_sim x N_sensor x N_param`

也就是：

- 先按仿真模态分层
- 每一层是一整个传感器振型向量
- 每个传感器点再对应一串参数灵敏度

## 文件输入

矩阵除了直接写在 JSON 里，也支持写成文件对象：

```json
{
  "phi_exp": { "path": "D:/demo/phi_exp.csv", "delimiter": "," },
  "phi_sim": { "path": "D:/demo/phi_sim.csv", "delimiter": "," },
  "dphi_dp": { "path": "D:/demo/dphi_dp.npy" },
  "pairs": [
    { "exp_mode": 1, "sim_mode": 1 }
  ]
}
```

支持格式：

- `phi_exp` / `phi_sim`: `csv` / `txt` / `json` / `npy` / `npz`
- `dphi_dp`: `json` / `npy` / `npz`

如果是 `npz`，需要额外给 `key`。

## 当前范围

这版工具刻意先不做频率灵敏度项，只做：

- `MAC`
- `sign alignment`
- `dMAC/dp`

也就是说，它现在是文档里“路径二”的前半段验证工具，用来先确认：

1. 振型顺序对不对
2. 符号对齐对不对
3. `dPhi/dp` 的组装对不对
4. `dMAC/dp` 的公式实现对不对

## 子命令 3：导出灵敏度 VTU

这个命令是给你现在这个场景准备的：

1. 先用 `build-sol200-bdf` 生成 `SOL200` 的 `BDF`
2. 手工调用 `Nastran` 求解
3. 拿求解生成的 `sens.csv` 和 `*.sol200.json`
4. 导出一个可以直接看云图的 `VTU`

命令：

```bash
python tools/mac_modal_sensitivity_standalone.py export-sensitivity-vtu --config export_vtu.json --output export_vtu_result.json
```

最小配置示例：

```json
{
  "input_bdf": "D:/demo/model.bdf",
  "matrix_path": "D:/demo/sens.csv",
  "metadata_json": "D:/demo/model_sol200.bdf.sol200.json",
  "output_vtu": "D:/demo/freq_mode_1_sensitivity.vtu",
  "response_name": "FREQ_MODE_1"
}
```

说明：

- `response_name` 表示你要看哪一个响应的灵敏度云图
- 频率响应可以写成 `FREQ_MODE_1`
- 模态位移响应可以写成类似 `PHI_M1_N101_U3`
- 这个 `VTU` 展示的是“某一个响应对各个设计变量的灵敏度”映射到单元上的结果
- 它适合拿来直观看 `SOL200` 原始灵敏度分布
- 它不是最终的 `dMAC/dp` 结果，因为 `dMAC/dp` 是后面把振型、`dPhi/dp`、`MAC` 公式一起算完之后得到的

当前映射规则：

- 如果参数本身就是单元参数，就直接映射到对应单元
- 如果参数是属性参数 `H`，就映射到该属性下的单元
- 如果参数是材料参数 `E` / `RHO`，就映射到使用该材料的单元

这样做的目的很简单：先把“求解器产出的原始灵敏度有没有问题”看清楚，再往后接 `MAC` 灵敏度，排查会轻松很多。

## 百分号口径说明

现在脚本里的 `MAC` 和 `dMAC/dp` 默认都按百分号口径输出，也就是：

- 原始 `MAC=0.975`
- 输出结果会写成 `97.5`

对应地：

- 原始 `dMAC/dp`
- 输出结果会自动乘以 `100`

这样做的原因不是公式变了，而是为了和 `FEMTools` 的显示口径对齐。脚本结果里同时保留：

- `mac_matrix` / `pair_results[].mac`：百分号口径
- `mac_matrix_raw` / `pair_results[].mac_raw`：`0~1` 原始口径
- `pair_results[].dmac_dp`：百分号口径
- `pair_results[].dmac_dp_raw`：原始口径

## 接口入口

除了命令行脚本，现在还可以直接走接口：

### `POST /optimization/modal_mac/sensitivity/compute`

这个接口的目标很直接：你把试验振型、仿真振型、振型灵敏度矩阵喂进去，它直接返回：

- `MAC`
- 符号对齐后的振型
- `dMAC/dp`

最小请求示例：

```json
{
  "phi_exp": [
    [1.0, 0.1],
    [0.5, 0.3],
    [0.2, 0.8]
  ],
  "phi_sim": [
    [0.9, 0.2],
    [0.45, 0.35],
    [0.25, 0.75]
  ],
  "dphi_dp": [
    [
      [0.01, 0.02],
      [0.03, 0.01],
      [0.02, -0.01]
    ],
    [
      [0.00, 0.03],
      [0.01, 0.02],
      [0.02, 0.01]
    ]
  ],
  "pairs": [
    { "exp_mode": 1, "sim_mode": 1 },
    { "exp_mode": 2, "sim_mode": 2 }
  ],
  "parameter_names": ["E1", "E2"],
  "sensor_labels": ["N101_U3", "N102_U3", "N103_U3"],
  "index_base": 1,
  "mac_scale": 100.0
}
```

如果矩阵太大，也可以沿用 standalone 的文件写法：

```json
{
  "phi_exp": { "path": "D:/demo/phi_exp.csv", "delimiter": "," },
  "phi_sim": { "path": "D:/demo/phi_sim.csv", "delimiter": "," },
  "dphi_dp": { "path": "D:/demo/dphi_dp.npy" },
  "pairs": [
    { "exp_mode": 1, "sim_mode": 1 }
  ],
  "mac_scale": 100.0
}
```
