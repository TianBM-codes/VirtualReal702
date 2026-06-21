# MODAL_MAC 接口联调示例

本文档补充当前已经落地的 `MODAL_MAC` 使用方式。这里说的 `MODAL_MAC`，指的是:

- 先选定一个试验模态和一个计算模态
- 后续模型修正只盯这一对
- MAC 计算使用“节点匹配后的全部三向位移分量”
- 对外统一按百分数使用，也就是乘以 `100`

## 当前实现说明

现在程序里已经接入了下面这条链路:

1. 导入试验 UNV
2. 导入 FEM BDF
3. 导入 FEM OP2 模态结果
4. 节点匹配
5. 自由度匹配
6. 计算模态相关性
7. 把选中的模态对写入正式响应目录，响应名形如 `MAC_MODE_FE1_TEST1`
8. 运行 `SOL200` 的专用 `MODAL_MAC` 灵敏度入口
9. 将得到的 `dMAC/dp` 入库
10. 调用 Bayesian 模型修正入口

说明:

- 正式响应目录里的 `MODAL_MAC` 目标值默认是 `100`
- `SOL200` 专用入口会先生成用于位移响应灵敏度提取的 deck
- 如果没有拿到可直接使用的 `SOL200` 位移灵敏度矩阵，当前实现会自动退回到有限差分 `SOL103` 兜底，保证整条接口能跑完

## 本次联调使用的数据

- `C:\FEMtools\3.7.1\examples\updating\car_floor\fem14_test_mode.unv`
- `C:\FEMtools\3.7.1\examples\updating\car_floor\fem14_out.bdf`
- `C:\FEMtools\3.7.1\examples\updating\car_floor\fem14_out.op2`
- `project_id = 25`
- 只选 `TEST1` 和 `FE1`

## 实际调用顺序

### 1. 导入试验模态

`POST /import/unv`

```json
{
  "file_path": "C:\\FEMtools\\3.7.1\\examples\\updating\\car_floor\\fem14_test_mode.unv",
  "project_id": 25,
  "file_id": 25,
  "clear_before_insert": true
}
```

### 2. 导入 BDF

`POST /import/bdf`

```json
{
  "file_path": "C:\\FEMtools\\3.7.1\\examples\\updating\\car_floor\\fem14_out.bdf",
  "project_id": 25,
  "clear_before_insert": true
}
```

### 3. 导入 OP2 第一阶模态

`POST /import/op2/modal/store`

```json
{
  "project_id": 25,
  "op2_path": "C:\\FEMtools\\3.7.1\\examples\\updating\\car_floor\\fem14_out.op2",
  "bdf_path": "C:\\FEMtools\\3.7.1\\examples\\updating\\car_floor\\fem14_out.bdf",
  "mode_numbers": [1],
  "overwrite": true
}
```

### 4. 节点匹配

`POST /match/nodes`

```json
{
  "project_id": 25,
  "overwrite": true
}
```

### 5. 自由度匹配

`POST /match/dofs`

```json
{
  "project_id": 25,
  "overwrite": true
}
```

### 6. 计算模态相关性

`POST /correlation/modal/compute`

```json
{
  "project_id": 25,
  "overwrite": true,
  "mac_threshold": 0
}
```

### 7. 将 FE1 / TEST1 写入正式响应目录

`POST /optimization/response/modal_match/select`

```json
{
  "project_id": 25,
  "overwrite": true,
  "response_types": ["MODAL_MAC"],
  "selected_pairs": [
    {
      "test_mode_no": 1,
      "fem_mode_no": 1
    }
  ]
}
```

### 8. 运行 MODAL_MAC 灵敏度入口

`POST /solver/nastran/sol200/modal_mac/run_and_store`

```json
{
  "project_id": 25,
  "batch_no": "903",
  "case_name": "fem14_modal_mac_first_mode",
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\car_floor\\fem14_out.bdf",
  "parameters": [
    {
      "name": "E_MAT1",
      "type": "E",
      "material_id": 1,
      "initial": 210000.0,
      "lower": 168000.0,
      "upper": 252000.0
    },
    {
      "name": "E_MAT2",
      "type": "E",
      "material_id": 2,
      "initial": 210000.0,
      "lower": 168000.0,
      "upper": 252000.0
    }
  ],
  "settings": {
    "sol200.deck_mode": "include",
    "sol200.sensitivity_csv": true,
    "result.target": "F06",
    "post": -1,
    "dynamic.vectors": 1,
    "dynamic.fmax": 200.0,
    "dynamic.norm": "MASS"
  },
  "run_solver": true,
  "timeout_sec": 600
}
```

### 9. 运行 Bayesian 模型修正

`POST /optimization/bayesian/modal_frequency/run`

说明: 路径名还是历史名字，但现在已经能消费 `MODAL_MAC` 正式响应和其灵敏度结果。

```json
{
  "project_id": 25,
  "batch_no": 902,
  "sensitivity_batch_no": 903,
  "input_bdf": "C:\\FEMtools\\3.7.1\\examples\\updating\\car_floor\\fem14_out.bdf",
  "save_results": true,
  "iterations": 1,
  "step_scale": 1.0,
  "damping": 1e-8
}
```

## 本次联调结论

这次已经确认:

- 接口链路能完整跑通
- 正式响应能按 `MAC_MODE_FE1_TEST1` 进入 Bayesian 响应集合
- `MODAL_MAC` 的当前值计算使用的是节点匹配后的三向位移分量
- `MAC` 对外按百分数使用
- 服务重启后新接口可直接调用

同时也要明确一个现象:

- 在 `fem14` 这个样例里，如果只修 `TEST1` 对 `FE1`，并且只选两个材料弹性模量参数，那么这一步算到的局部 `dMAC/dp` 目前是 `0`
- 所以 Bayesian 这次虽然成功执行，但不会推动参数变化

这说明“程序已经通了”，不代表“这个样例组合一定对 MAC 有足够敏感性”。如果后续要验证更新步是否明显非零，建议至少做下面之一:

- 增加可修正参数
- 换一组更敏感的模态对
- 一次引入多阶模态响应
