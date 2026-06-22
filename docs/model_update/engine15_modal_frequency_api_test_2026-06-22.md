# engine15 频率修正接口联调记录

日期：2026-06-22

本次联调用真实 HTTP 接口完成，测试对象：

- FEM 模型：`C:\FEMtools\3.7.1\examples\updating\engine\sol103_final.bdf`
- 试验文件：`C:\FEMtools\3.7.1\examples\updating\engine\ema15.unv`
- 项目号：`15062215`
- 日志目录：`temp/engine15_api_run/`

## 调用链路

按下面顺序执行：

1. `POST /import/unv`
2. `POST /import/bdf`
3. `POST /solver/nastran/sol103/run_and_store_modal`
4. `POST /match/nodes`
5. `POST /match/dofs`
6. `POST /correlation/modal/compute`
7. `POST /optimization/response/modal_frequency/create_from_match`
8. `POST /solver/nastran/sol200/preview`
9. `POST /solver/nastran/sol200/run_and_store`
10. `POST /optimization/bayesian/sol200/modal_frequency/run`

已额外保存一个可重复执行的脚本：

- `tools/run_engine15_modal_update_api.py`

## 关键结果

- `SOL103` 模态求解与入库成功，导入了 `12` 阶 FEM 模态。
- 自动匹配后生成了 `12` 条频率响应目录。
- `all_elements_e` 预览成功，自动局部化为 `1548` 个单元级 `E` 参数。
- `SOL200` 频率灵敏度求解与入库成功。
- `SOL200 + Bayesian` 模态频率修正链路在修复后已跑通，`1` 次迭代成功完成。

本次 `Bayesian` 最终结果：

- 输出目录：`D:\WorkSpace\OtherProjects\VirtualReal702\model\15062215\cal\bayesian\sol200_modal_frequency`
- 更新后 BDF：`D:\WorkSpace\OtherProjects\VirtualReal702\model\15062215\cal\bayesian\sol200_modal_frequency\iteration_001\sol103_final_sol200.localized_source.bdf`
- 历史概览：`D:\WorkSpace\OtherProjects\VirtualReal702\model\15062215\cal\bayesian\sol200_modal_frequency\history\overview.html`

本次接口返回的两条被消费的频率响应：

- `FREQ_MODE_1`：初始 `334.2871 Hz`，更新后 `352.0492 Hz`，试验值 `462.26 Hz`
- `FREQ_MODE_2`：初始 `486.7077 Hz`，更新后 `530.8827 Hz`，试验值 `1598.9 Hz`

说明：

- 当前这组 `response frequency 1 2` 对应的第二条匹配很弱，`FE2 -> TEST11`，所以 1 次迭代虽然成功执行，但离试验值仍然较远。
- 这更像“链路验证成功”，不代表这组响应选择已经是最佳修正策略。

## 本次修复

为让这类纯 Nastran/BDF 的频率修正算例能跑通，我修了两个后端问题：

1. `services/model_update/importers/op2_service.py`
   - 当项目目录下没有 `manifest.db` 时，不再让参数映射直接失败。
   - 对 Nastran-only 场景回退为单实例 `BDF_MODEL` 映射。

2. `services/model_update/analysis/bayesian_service.py`
   - `SOL200` 模态频率 Bayesian 迭代保存历史时，不再强制要求 `manifest.db`。
   - 修复 `exit_diff_percent` 未传时 `exit_check` 为 `None` 触发的空指针异常。

## 建议

如果下一步目标是“让修正效果更明显”，建议优先做这两件事之一：

- 把响应从固定 `FREQ_MODE_1/FREQ_MODE_2` 换成匹配质量更高的模态对。
- 保持 `all_elements_e` 不变，但增加迭代次数，并对 `mac_threshold` / `max_freq_error_ratio` 做更严格筛选。
