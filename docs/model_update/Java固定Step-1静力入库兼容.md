# Java 固定 Step-1 的静力入库兼容

旧 Java 服务在 ODB 结果组 ready 后，调用 `/import/fem/static/from_project_result` 时固定传 `step: "Step-1"`。当实际 ODB 的分析步为 `step-1` 或自定义名称时，ODB 解析可以成功，但随后静力位移入库失败。

## Python 端处理规则

修改文件：`services/model_update/analysis/fem_result_service.py`。

1. 指定的 Step 精确存在时，按原名称处理。
2. 名称仅大小写不同时，采用结果组中唯一对应的实际名称。
3. 旧默认值 `Step-1` 不存在时，在指定结果组内查找 procedure 为 STATIC 且有 NODAL U 结果的分析步。仅有一个候选时自动使用，并记录请求名称与实际名称。
4. 多个候选时返回错误和 `candidate_static_steps`，调用方需明确选择，不自动猜最后一步。
5. 其他明确指定但不存在的 Step 仍报错。未指定 Step 的原有默认行为及 OP2 多子工况导入行为保持不变。

旧 Java 的 `instance` 字段仍按原接口行为忽略；正式实例筛选参数为 `instances`。未传 `instances` 时按所选分析步中实际存在的位移结果实例导入。

## 部署与重试

将上述 Python 文件同步到同版本服务并重启 Python 后端，不需要修改 Java 或前端。

已解析并 ready 的 ODB 不需要重新求解或重新导入。直接重新调用 `/import/fem/static/from_project_result`，可沿用原请求中的 `Step-1`；也可先调用 `/catalog/project_result/steps` 确认实际名称后显式指定。

若存在多个静力位移步，仍须在入库请求中指定实际名称。`frame` 省略时使用所选分析步的最后一帧，`overwrite: true` 会替换指定载荷工况的旧静力结果。

## 本地验证

`python -m pytest tests/test_inp_service_static_results.py tests/test_fem_router_async.py -q`

覆盖大小写、唯一候选、多候选拒绝、模态步排除、缺少节点位移、不跨结果组匹配、显式指定保留、默认导入、OP2 多子工况与异步接口。远程实际 ODB 需部署后重试确认。
