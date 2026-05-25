# 从 `4d8ddfa6b4be4982708c42dd9a686ed11119edca` 到当前 `HEAD(879e91d)` 的 PBS 手工更新说明

## 1. 提交范围总览

起点提交：

- `4d8ddfa6b4be4982708c42dd9a686ed11119edca`

当前提交：

- `879e91d` (`2026-05-25`, `transform matrix4 fem and test`)

区间内全部提交：

```text
879e91d 2026-05-25 transform matrix4 fem and test
40b15be 2026-05-25 Update 代办事项.md
d956431 2026-05-25 Support project-based sensitivity generation defaults
ce111d8 2026-05-25 fix: unblock solver run-and-parse workflow
a63dcc8 2026-05-25 Merge branch 'main' of https://github.com/TianBM-codes/VirtualReal702
3c9464f 2026-05-23 matrix4 fem and test
3ba3d6d 2026-05-23 Update system.py
6493a0f 2026-05-23 Merge branch 'main' of https://github.com/TianBM-codes/VirtualReal702
0b358e0 2026-05-22 tools: 新增 diag_section_ids.py 诊断 section_id 覆盖情况
dc90733 2026-05-22 fix(section_id): 正确处理 isInternal=True 的 element set
ae5ff4e 2026-05-22 update 代办
7033b37 2026-05-22 Merge branch 'main' of http://62.234.179.217:43127/RealVirtual/--702
6e76758 2026-05-22 fix jobrunner add op2 to database
40fc789 2026-05-21 Merge fix/block-merge into main
d41e59a 2026-05-21 fix(projects): GET /{id} 项目不存在时返回空结构而非404
aa85726 2026-05-21 fix(l3): 无模型时各端点返回空正常结构而非404
c5e6869 2026-05-21 fix(abaqus_dump): 合并同key的多个bulkDataBlock，修复并行分块导致的shape不一致
a9080e8 2026-05-21 tools: 新增 diag_dump_shapes.py 诊断 abaqus_dump npy shape 一致性
a492da5 2026-05-21 Merge branch 'main' of http://62.234.179.217:43127/RealVirtual/--702
51b5c7d 2026-05-21 UNV导入支持下载功能
dcdd3a3 2026-05-21 fix(abaqus_dump): canonical对齐补等值校验，防止searchsorted错误映射
11f40d8 2026-05-21 fix(abaqus_dump): STATUS等标量场的1D数据崩溃 + 元素删除后逐帧shape不一致
8111183 2026-05-21 Merge branch 'main' of http://62.234.179.217:43127/RealVirtual/--702
c077c63 2026-05-21 频率匹配
237f1f2 2026-05-21 add log
a5b35fa 2026-05-21 fix bug
da5a34d 2026-05-21 fix(color_service): legend-entries 同步补全内部元素；补文档
58c77b7 2026-05-21 fix(color_service): legend 补全内部元素的 etype/material/section_type
8ddedcd 2026-05-21 fix(bdf_pack): PSHELL material_name 读取 mid1 而非 mid
5af2663 2026-05-21 Create PBS接口文档.xlsx
f373030 2026-05-21 docs: add unified solver-parse interface design
fc08160 2026-05-21 docs: add PBS configuration recommendations
b8d0d8b 2026-05-21 Add modal flip flag and bind SOL103 imports to generated BDF
a86b4bf 2026-05-21 Merge branch 'main' of https://github.com/TianBM-codes/VirtualReal702
ee4359b 2026-05-20 temp
37496ee 2026-05-20 docs: 重写第 13 节 testMesh 接口文档，补充 flip 参数
1ed2721 2026-05-20 feat(modal_service): 支持 flip 参数翻转振型符号
cefa0b0 2026-05-20 fix(viewer): 补上 vtxIdxs 解构，region highlight 变形同步实际生效
1e7cb48 2026-05-20 fix(viewer): region highlight 在变形后加载时正确跟随变形
5ac29e4 2026-05-20 feat(viewer): region highlight 随变形/动画同步位置
```

## 2. 和 PBS 直接相关的提交

实际影响 PBS 调试/调用的主要是下面 4 个提交：

1. `a5b35fa` `2026-05-21` `fix bug`
2. `237f1f2` `2026-05-21` `add log`
3. `c077c63` `2026-05-21` `频率匹配`
4. `ce111d8` `2026-05-25` `fix: unblock solver run-and-parse workflow`

另外 2 个提交只补文档，不影响后端逻辑：

1. `fc08160` `docs: add PBS configuration recommendations`
2. `5af2663` `Create PBS接口文档.xlsx`

## 3. 离线机器如果只为了调试 PBS，必须同步的文件

优先同步这 5 个文件：

1. `services/model_update/analysis/pbs_service.py`
2. `webapi/routers/solver.py`
3. `webapi/models.py`
4. `tests/test_pbs_service.py`
5. `tests/test_solver_pbs_router.py`

文档可选同步：

1. `docs/PBS配置建议.md`
2. `docs/PBS接口文档.xlsx`
3. `docs/model_update/模型修正接口清单.md`
4. `docs/model_update/文件计算并解析统一接口设计.md`

## 4. 必须手工修改的内容

### 4.1 `services/model_update/analysis/pbs_service.py`

当前关键位置：

- `DEFAULT_PBS_API_PATHS` 在 `34` 行附近
- `_ensure_project_pbs_settings()` 在 `127` 行附近
- `load_pbs_environment_config()` 在 `179` 行附近
- `PBSClient` 在 `257` 行附近
- `run_pbs_solver_job()` 在 `707` 行附近
- `get_pbs_job_status()` 在 `837` 行附近

需要同步的核心逻辑：

1. **PBS 环境配置结构改了**
   - 旧版是 `api_prefix/service_prefix/storage_prefix/auth_path/fallback_service_prefixes` 这套拼接方式。
   - 新版改成 `api_paths` 显式配置每个接口路径，不再动态拼路径。
   - 新增常量 `DEFAULT_PBS_API_PATHS`，要求至少包含：
     - `login`
     - `expand_vars`
     - `create_dir`
     - `upload_file`
     - `file_exists`
     - `submit_job`
     - `job_status`
     - `list_files`
     - `download_file`

2. **工程级 PBS 配置变成必填**
   - 新增 `_ensure_project_pbs_settings(project_id, pbs_settings)`。
   - 当 `project_config.pbs` 缺失时，直接报错：
     - `"请先配置高性能集群计算配置"`
   - 错误详情里会带：
     - `"field": "project_config.pbs"`

3. **Abaqus/Nastran 的应用参数必须从 `project_config.pbs` 补齐**
   - `get_application_config()` 不再只检查 `platform`。
   - 现在会同时校验这 4 个字段：
     - `application_id`
     - `application_name`
     - `version`
     - `platform`
   - 缺任意一个都会报错：
     - `"PBS 应用配置不完整，请先在工程 project_config.pbs 中配置"`

4. **支持从 `project_config.pbs.applications` 读取嵌套应用配置**
   - `_normalize_project_application_settings()` 增加了对
     - `pbs.Abaqus`
     - `pbs.abaqus`
     - `pbs.applications.Abaqus`
     - `pbs.applications.abaqus`
     的兼容读取。

5. **Nastran 提交载荷不再带 `MEMORY`**
   - 旧版 `build_submit_payload()` 会给 Nastran 带 `MEMORY`。
   - 新版已删掉该字段。
   - 所以如果你另一台机器的 `PBS` 平台不接受 `MEMORY`，这个改动是必须打上的。

6. **请求 URL 组装方式改了**
   - 旧版 `PBSClient` 会尝试多个 `service_prefix` 候选路径。
   - 新版只有 `_build_path()`，直接从 `config.api_paths[path_key]` 取模板。
   - `_build_url()` 也从：
     - `base_url + api_prefix + path`
     变成：
     - `base_url + path`

7. **PBS 运行流程加了项目日志**
   - 文件顶部新增：
     - `from .project_log_service import log_project_error, log_project_info, log_project_step`
   - `run_pbs_solver_job()` 整体包了一层 `try/except`，并增加这些日志阶段：
     - `pbs_run_started`
     - `path_resolved`
     - `pbs_config_ready`
     - `pbs_submit`
     - `pbs_wait`
     - `pbs_finished`
     - `pbs_submitted`
     - `failed`
   - 如果你另一台机器只想“能跑 PBS”而不关心项目进度日志，也建议同步，因为这部分和新的异常处理绑在一起了。

8. **PBS 本地输入文件不存在时，报错文案变了**
   - 旧版是：
     - `未找到 PBS 本地输入文件`
   - 现在运行入口里变成：
     - `PBS local input file not found`
   - 这个不是功能必需，但如果你对比日志时发现错误文本不同，这是正常的。

9. **`get_pbs_job_status()` 现在必须传 `project_id`**
   - 新签名：
     - `get_pbs_job_status(*, project_id: int, job_id: str, env: Optional[str] = None, timeout_sec: int = 60)`
   - 查询状态前也会先校验项目里的 `project_config.pbs`。
   - 返回值里新增：
     - `project_id`

### 4.2 `webapi/models.py`

当前关键位置：

- `SolverRunAndParseRequest` 在 `576` 行附近
- `PBSSolverRunRequest` 在 `615` 行附近
- `PBSJobStatusRequest` 在 `630` 行附近

需要同步的改动：

1. `PBSSolverRunRequest.project_id`
   - 从 `Optional[int] = None`
   - 改为 `project_id: int`

2. `PBSJobStatusRequest`
   - 新增必填字段：
     - `project_id: int`

3. 新增 `SolverRunAndParseRequest`
   - 这是统一“计算并解析”接口用的请求模型。
   - 虽然不是 PBS 本身，但后面 `solver.py` 会用到。

4. 新增 `ModalMatchScatterRequest`
   - 这是 `c077c63` 带来的频率匹配模型。
   - 和 PBS 无直接关系。
   - 如果你只补 PBS，可暂时不打；但如果你想让离线机器尽量接近当前版本，建议一起补。

### 4.3 `webapi/routers/solver.py`

当前关键位置：

- `run_in_threadpool` 导入在 `10` 行附近
- `_solver_run_and_parse_kwargs()` 在 `196` 行附近
- `get_pbs_job_status_api()` 在 `332` 行附近
- `run_solver_and_parse_api()` 在 `499` 行附近

需要同步的改动：

1. **PBS 状态查询接口必须把 `project_id` 传下去**
   - `get_pbs_job_status_api()` 调用 `get_pbs_job_status()` 时新增：
     - `project_id=body.project_id`

2. **新增统一计算并解析接口**
   - 新增请求参数整理函数：
     - `_solver_run_and_parse_kwargs()`
   - 新增路由：
     - `POST /solver/run_and_parse`
   - 同步调用：
     - `run_solver_and_parse_project_result`

3. **`ce111d8` 修复了阻塞问题**
   - `run_solver_and_parse_api()` 同步执行时，从：
     - `run_solver_and_parse_project_result(**kwargs)`
   - 改为：
     - `await run_in_threadpool(run_solver_and_parse_project_result, **kwargs)`
   - 这不是 PBS 本体，但如果你另一台机器也要调这个统一接口，这个修复必须同步。

### 4.4 `tests/test_pbs_service.py`

建议一起同步，便于离线自测。新增/变化重点：

1. `PBSEnvironmentConfig` 的测试构造参数改为 `api_paths`
2. 验证项目级 PBS 配置缺失时报错
3. 验证应用配置缺字段时报错
4. 验证 Nastran 提交载荷不再包含 `MEMORY`

### 4.5 `tests/test_solver_pbs_router.py`

建议一起同步，重点是：

1. PBS 状态查询接口请求体里新增 `project_id`
2. 返回值断言里也新增 `project_id`

## 5. 推荐你在另一台机器上的手工补丁顺序

1. 先替换 `services/model_update/analysis/pbs_service.py`
2. 再替换 `webapi/models.py`
3. 再替换 `webapi/routers/solver.py`
4. 最后补测试文件
5. 如果配置文件还是旧结构，再按 `docs/PBS配置建议.md` 调整 `service_config.json` 和项目 `project_config.pbs`

## 6. 配置层面的关键变化

如果另一台机器代码补好了但还是跑不通，最可能是配置格式没跟上。这里是新版 PBS 配置的关键要求：

1. `service_config.json`
   - `PBS.environments.<env>.base_url` 直接写到 `/pbsworks` 层
   - 接口路径放在 `PBS.api_paths` 或 `PBS.environments.<env>.api_paths`
   - 不再依赖 `api_prefix/service_prefix/storage_prefix/auth_path`

2. `project_config.pbs`
   - 必须存在
   - 至少要有：
     - `ApplicationId`
     - `ApplicationName`
     - `VERSION`
     - `PLATFORM`
   - 可选：
     - `CORES`
     - `HOSTS`
     - `PRECISION`
   - Nastran 不要再配 `MEMORY`

## 7. 如果你只想做“最小 PBS 可用补丁”

最少同步下面三处就能覆盖主要 PBS 逻辑变更：

1. `services/model_update/analysis/pbs_service.py`
2. `webapi/models.py`
3. `webapi/routers/solver.py`

其中最关键的是 `pbs_service.py`，因为 PBS 的配置读取、参数校验、提交流程、状态查询入口要求，都在这里变了。
