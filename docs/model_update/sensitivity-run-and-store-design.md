# sensitivity run_and_store 接口逻辑速查

> 文件对应源码：`services/model_update/analysis/sensitivity_service.py`
> 路由入口：`webapi/routers/sensitivity.py` → `POST /sensitivity/run_and_store`

---

## 一、三个公开入口函数

| 函数 | 路由 | 说明 |
|---|---|---|
| `run_sensitivity_inp_and_store` | `POST /sensitivity/run_and_store` | 主入口：跑 Abaqus、解析结果、入库、（可选）合并场 |
| `generate_sensitivity_inp_and_store` | `POST /sensitivity/generate_run_and_store` | 先从 DB 生成 INP，再调用 `run_sensitivity_inp_and_store` |
| `store_dsa_sensitivity_results` | `POST /sensitivity/calculate_and_store` | 另一条路：支持传已有 ODB/workspace，直接解析入库（无 merge） |

`generate_sensitivity_inp_and_store` 透传所有参数给 `run_sensitivity_inp_and_store`，只多了 INP 生成一步，行为完全一致。

---

## 二、`run_sensitivity_inp_and_store` 完整流程

```
输入 INP + output_dir
        │
        ▼
 ① run_abaqus_job()          ← 跑 Abaqus 求解，得到 ODB
        │
        ├─ parse_via_project_results=True (默认)
        │         │
        │         ▼
        │  ② _submit_project_result_group_and_wait()
        │     • 调 L3 API 把 ODB 注册为 project result_group
        │     • 轮询直到 status="ready"
        │     • 返回 workspace = settings.data_root/<project_id>
        │              source_result_group = "sensitivity_batch_<batch>_<job>_<ts>"
        │
        └─ parse_via_project_results=False
                  │
                  ▼
           ③ 在本地 build_workspace_from_odb()
              workspace = output_dir/<job_name>_workspace   ← 本地临时目录
              source_result_group = None
        │
        ▼
 ④ _load_dsa_normalized_sensitivity_matrix()
     • 从 workspace 读取 d_U_T1…TN 字段
     • 计算归一化灵敏度矩阵
     • 返回 matrix_payload，含 "workspace" 键
        │
        ▼
 ⑤ _finalize_sensitivity_store_result()
     • 把矩阵写进 MySQL（project 灵敏度表）
     • 组装 result dict，其中：
         result["workspace"] = matrix_payload["workspace"]
         result["step"]      = matrix_payload["step"]
        │
        ▼
 ⑥ merge_fields=True ?
     ├─ parse_via_project_results=True
     │     ▼
     │  merge_dsa_sensitivity_fields(
     │      workspace = result["workspace"],         ← L3 项目 workspace
     │      source_result_group = <project RG>,      ← 步骤②里的 RG 名
     │      result_group = resolved_merge_result_group
     │  )
     │  写出字段 d_<node>_<comp>_T 到项目 workspace
     │
     └─ parse_via_project_results=False
           当前行为：merge 写入本地 solver workspace（路径不对！）
           原因：result["workspace"] = output_dir/<job>_workspace
           ⚠ 计划修复：False 时跳过 merge，让调用方另行触发
        │
        ▼
 返回 result dict
```

---

## 三、关键参数语义

### workspace 路径决策

| 场景 | resolved_workspace |
|---|---|
| `parse_via_project_results=True` | `settings.data_root/<project_id>`（L3 项目目录） |
| `parse_via_project_results=False` | `output_dir/<job_name>_workspace`（本地临时目录） |

`settings.data_root` 来自 `src/l3/core/config.py`，优先级：环境变量 `APP_DATA_ROOT` > `service_config.json` > 默认 `<repo_root>/model`。

### workspace_frame（帧号重映射）

| 场景 | workspace 内帧索引 |
|---|---|
| `parse_via_project_results=True` | 固定 `0`（L3 只提取了请求帧，存为 frame_idx=0） |
| `parse_via_project_results=False` | 用户传入的 `frame` 原值 |

实现：`_resolved_workspace_frame()` at line ~1278。

### result_group 命名规则

| 名称 | 格式 | 用途 |
|---|---|---|
| `source_result_group` | `sensitivity_batch_<batch>_<job>_<ts>` | L3 存原始 d_U_T* 字段的 RG |
| `resolved_merge_result_group` | `sensitivity_<batch>_<response_token>` | merge 后写出的 RG，前端下拉用这个 |

`resolved_merge_result_group` 由 `_auto_merge_result_group(batch_no, field_prefix)` 生成，或由请求体 `merge_result_group` 覆盖。

`_auto_merge_result_group` 实现（line ~1230）：
```python
response_field = _field_prefix_response_token(field_prefix) or "sensitivity"
# field_prefix="d_U_T" → response_field="U"
raw = f"sensitivity_{batch_no}_{response_field}"   # e.g. "sensitivity_1_U"
return _normalize_result_group_name(raw)
```

### cleanup_process_files

`True`（默认）时，Abaqus 生成的过程文件（`.msg`, `.dat`, `.sta` 等）在解析完后删除。**不删 workspace 和 ODB。**

---

## 四、merge_dsa_sensitivity_fields 做了什么

入口：`sensitivity_service.py` line ~4217，调用 `dsa_merge_service.merge_dsa_fields()`。

```
从 MySQL 加载 project 优化参数（set_name, element_label 等）
        │
        ▼
从 workspace manifest.db 找所有 d_U_T* 字段（按 source_result_group 过滤）
        │
        ▼
读每个字段每个 instance 的 labels + data（H5 文件）
        │
        ▼
按 (响应节点 label, 分量) 生成合并场名：d_<node>_<comp>_T
        │
        ▼
写回 workspace（ExternalResultWriter），result_group = resolved_merge_result_group
```

合并后的字段格式示例：`d_4_U1_T`（节点 4 在 U1 方向的灵敏度场）。

---

## 五、已知问题

### merge 写入路径错误（parse_via_project_results=False）

- **现象**：merge 结果写进 `output_dir/<job>_workspace`，L3 无法从项目 workspace 提供该场。
- **根因**：`merge_dsa_fields` 用同一个 `workspace` 既读源字段又写结果；`False` 时该 workspace 是本地 solver 目录，不是 L3 项目目录（`settings.data_root/<project_id>`）。
- **临时方案（方案 B）**：`parse_via_project_results=False` 时跳过 merge，响应体返回 `merge_skipped_reason`，调用方需在 ODB 导入 L3 后再手动调 `POST /sensitivity/merge_fields`。
- **彻底修复（方案 A）**：给 `merge_dsa_fields` 加 `source_workspace` 参数，读写分离。

---

## 六、`store_dsa_sensitivity_results` 的区别

- 接受已有 `workspace` 或 `odb_path`（不一定重新跑 Abaqus）。
- `odb_path` 存在时，在 `build_dsa_normalized_sensitivity_matrix` 内部自动建 workspace（路径：`workspace_root/<odb_stem>_workspace`，`workspace_root` 对应 `output_dir`）。
- **没有** `merge_fields` 参数，不触发合并。需要合并另调 `POST /sensitivity/merge_fields`。

---

## 七、`POST /sensitivity/merge_fields` 独立端点

调用 `merge_dsa_sensitivity_fields(project_id, workspace, step, frame, field_prefix, ...)`。

**前提**：`workspace` 里必须已有目标 d_U_T* 字段（source_result_group 对应的数据已入库）。

典型用法（配合 `parse_via_project_results=False` 的 run_and_store）：
1. `run_and_store` 跑完，ODB 在 output_dir。
2. 手动调 `workspace/build`（或 L3 project result_group 接口）把 ODB 导入项目 workspace。
3. 调 `merge_fields`，传项目 workspace 路径和正确的 `source_result_group`。

---

## 八、instance_name=NULL 导致 merge_fields crash 的根因分析

### Bug 现象

`merge_fields` 最后一步报错：
```
FileNotFoundError: Geometry HDF5 not found: \\l1\\geometry\\.h5
```
路径中实例名缺失（`.h5` 前面为空）。

### 数据链路（PART-1 / PART-1-1 标准 INP）

| 层级 | instance_name | part_name |
|------|--------------|-----------|
| INP parser：PART scope elset | None（设计如此） | "PART-1" |
| `t_mt_py_fem_quantity_set_capability` | NULL | "PART-1" |
| `t_mt_py_fem_selected_parameter`（LOCAL 模式）| NULL | "PART-1" |
| manifest.db `instances` | "PART-1-1" | "PART-1" |
| manifest.db `element_sets` | "PART-1-1" | — |

**PART scope elset 的 `instance_name=NULL` 是正常的**：INP 的 `*Elset` 定义在 `*Part` 块里，天生不绑定实例，只有 `part_name`。实例名需要通过 `part_name` 反查 manifest 得到。

### 根因

`dsa_merge_service._resolve_element_labels` 的 `element_label` 分支：

```python
# 旧代码
instance_name = param_row.get("instance_name") or ""   # NULL → ""
result[instance_name] = [int(element_label)]            # result[""] = [...]
```

空字符串 key 最终传到 `write_element(instance="")` → 路径 `\\l1\\geometry\\.h5` → crash。

### 修复（提交 `fc8ae7d`）

`instance_name` 为空时，用 `part_name` 查 manifest：

```python
if instance_name:
    result[instance_name] = [int(element_label)]
    return result
# instance_name 为空：用 part_name 展开实例
if part_name:
    for inst in repo.get_instances_by_part_name(part_name):
        result[str(inst)] = [int(element_label)]
else:
    for inst_row in repo.list_instances():
        result[str(inst_row["instance_name"])] = [int(element_label)]
return result
```

### 为什么解析侧不需要改

- `src/inp/parser.py`：正确解析 `part=PART-1` → `Instance.part_name="PART-1"` ✅
- `src/inp/exporter.py`：写 manifest `instances` 表时 `part_name="PART-1"` ✅
- `src/l1/abaqus_dump.py`：读 ODB `instance.partName` → `"PART-1"` ✅
- `get_instances_by_part_name("PART-1")` → `["PART-1-1"]` ✅

整条解析链路正确，唯一 bug 在 `_resolve_element_labels` 未利用已有的 `part_name`。
