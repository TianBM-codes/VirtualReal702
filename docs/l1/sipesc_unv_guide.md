# SIPESC UNV 导入

本项目中的 `sipesc_unv` 是 SIPESC 后处理文件，和试验数据导入使用的 UNV 不是同一格式。

- `POST /api/projects`：`.unv` 自动识别为 `sipesc_unv`，导入一个 instance 的节点和单元。
- SIPESC UNV 未提供材料或截面属性，因此模型导入时所有支持的单元进入 `SIPESC_DEFAULT` 默认属性组。
- `POST /api/projects/{project_id}/results`：上传 `.unv` 时只导入 `Static Displacement` 的 `U1/U2/U3`，以及 `Static Stress` 六分量计算出的节点标量 `S.Mises`。
- 结果只能追加到单 instance 项目。对于 CDB/BDF 项目，原有材料/截面分组保持不变；UNV 只按节点编号写结果。
- 节点标签不完全一致时会输出已匹配、模型缺失、结果多余的计数，但仍导入可匹配节点。

结果固定写入 `SIPESC-Static` 这一个 step。Mises 是唯一保存的应力值，不保存六个原始应力分量。
