# 复用已有 ODB 解析静力灵敏度

## 大小写修复

INP/响应目录可能使用 `zd-1`，ODB 使用 `ZD-1`。解析节点响应时，现将仅有大小写差异的实例作用域对齐到 ODB 名称，再匹配节点。不同实例的相同节点号仍保持隔离。

将以下两个文件同步到计算机上的同版本项目，并重启后端：

- `services/model_update/analysis/sensitivity_service.py`
- `services/model_update/analysis/bayesian_service.py`

## 读取已有 ODB

`POST /sensitivity/abaqus/calculate_and_store`

```json
{
  "project_id": 2026101201,
  "batch_no": "parse_test_1",
  "input_inp": "D:/your-job/model_dsa.inp",
  "odb_path": "D:/your-job/model_dsa.odb",
  "step": "Step-1",
  "instances": ["ZD-1"],
  "field_prefix": "d_U_",
  "response_component": "U2",
  "position": "NODAL",
  "frame": 1,
  "run_solver": false,
  "write_cloud_result": false,
  "async_submit": true
}
```

替换工程号、路径、分析步、响应方向和实际结果帧号。`input_inp` 必须是生成该 ODB 时实际提交的 DSA INP，并保留其引用文件。实例名使用 ODB 中的名称。这里的 `frame: 1` 只是示例：选用实际包含位移与灵敏度的结果帧，不能默认视为最后一帧。该接口帧号默认为 0，省略并不会自动选最后一帧。

明确传 `run_solver: false`，该字段默认是 true。提供 `odb_path` 后，程序提取已有结果、计算归一化矩阵并入库，不重新求解，不重新生成 DSA INP；提取阶段仍可能启动 Abaqus Python 读取 ODB。

收到任务 ID 后调用 `GET /sensitivity/tasks/{task_id}`。成功后调用 `POST /sensitivity/stored/matrix`：

```json
{
  "project_id": 2026101201,
  "batch_no": "parse_test_1"
}
```

同批次已有的数据库矩阵会被替换，建议解析测试使用单独批次。

## 复用已提取的工作区

若已有独立灵敏度 workspace，可将 `odb_path` 替换为 `workspace` 路径，避免再次提取。若工作区数据属于带名称的 result group，当前此接口不接收 result_group，应优先从 ODB 重新提取到独立工作区。工作区帧号以其 manifest 为准，可能与原 ODB 不同。

## 验证范围

回归测试覆盖大小写不同的响应节点匹配、不同实例节点隔离，以及 run_solver=false 时现有 ODB 路径跳过求解和输入生成。远程真实 ODB 尚需使用以上请求验证。
