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
    "lower_scale": 0.8,
    "upper_scale": 1.2
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
