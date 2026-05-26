# PBS 配置建议

本文档基于以下内容整理：

- `docs/PBS接口文档.xlsx`
- 当前代码实现 `services/model_update/analysis/pbs_service.py`
- 当前请求模型 `webapi/models.py`
- 当前路由 `webapi/routers/solver.py`

目标是给出一份可直接落地的 PBS 配置和接口说明，覆盖：

- 完整配置文件示例
- 工程 `project_config.pbs` 示例
- 各接口如何调用
- 返回参数说明
- 成功/失败返回示例
- 影响的数据库
- 当前实现边界

## 1. 总体原则

当前 PBS 能力按下面规则设计和实现：

1. 调用 PBS 接口前，先检查工程 `project_config.pbs`。
2. 如果未配置，直接报错：`请先配置高性能集群计算配置`。
3. `service_config.json` 只负责 PBS 平台连接信息和接口路径。
4. `service_config.json` 中所有 PBS 接口路径都要直接完整列出，不再拆成 `api_prefix / service_prefix / storage_prefix / auth_path` 再拼装。
5. Abaqus / Nastran 的应用平台信息都从工程 `project_config.pbs` 读取。
6. PBS 的 Nastran 配置中不要 `MEMORY` 这一项。

## 2. 配置职责划分

### 2.1 `service_config.json` 放什么

这里只放平台级、环境级、所有项目共用的配置：

- `PBS.env`
- `PBS.api_paths`
- `PBS.environments.<env>.base_url`
- `PBS.environments.<env>.server_name`
- `PBS.environments.<env>.stage_path_template`
- `PBS.environments.<env>.username`
- `PBS.environments.<env>.password`
- `PBS.environments.<env>.verify_ssl`

这里不再放：

- `applications.Abaqus.*`
- `applications.Nastran.*`
- `PBS_APPLICATIONS`
- `api_prefix`
- `service_prefix`
- `storage_prefix`
- `auth_path`
- `fallback_service_prefixes`

### 2.2 `project_config.pbs` 放什么

这里只放工程自己的 PBS 提交参数，也就是应用平台配置：

- `env`
- `ApplicationId`
- `ApplicationName`
- `VERSION`
- `PLATFORM`
- `CORES`
- `HOSTS`
- `PRECISION`

如果是 Nastran：

- 仍然使用 `ApplicationId`、`ApplicationName`、`VERSION`、`PLATFORM`、`CORES`
- 不要配置 `MEMORY`

## 3. 完整 `service_config.json` 示例

下面给一份“完整版示例”。其中非 PBS 的系统字段按当前项目常见结构保留，PBS 部分按本文档推荐写法组织。

```json
{
  "APP_DATA_ROOT": "D:\\WorkSpace\\OtherProjects\\VirtualReal702\\model\\",
  "APP_REGISTRY_DB_PATH": "D:\\WorkSpace\\OtherProjects\\VirtualReal702\\model\\registry.db",
  "APP_ENABLE_GZIP": 1,
  "APP_EMBEDDED_RUNNER": 0,
  "APP_ABAQUS_CMD": "C:\\Program Files\\SIMULIA\\Commands\\abaqus.bat",
  "APP_PYTHON3_CMD": "C:\\Python311\\python.exe",
  "APP_BAYESIAN_OUTPUT_DIR": "D:\\WorkSpace\\Temp\\workspace",
  "APP_INVARIANTS": "full",
  "DB_HOST": "127.0.0.1",
  "DB_PORT": 3306,
  "DB_USER": "root",
  "DB_PASSWORD": "******",
  "DB_DATABASE": "db_simu_real_test",
  "DB_CHARSET": "utf8mb4",
  "NASTRAN": "C:\\MSC.Software\\MSC_Nastran\\20180\\bin\\nastran.exe",
  "PBS": {
    "env": "dev",
    "api_paths": {
      "login": "/api/ams/aaservice/authn/oauth2/token",
      "job_query": "/api/storage/jobs/query",
      "dynamic_app_def": "/api/Service6/pas/restservice/applications/dynamicappdef",
      "expand_vars": "/api/Service6/pas/restservice/files/expandvars",
      "create_dir": "/api/Service6/pas/restservice/files/dir/create",
      "upload_file": "/api/Service6/pas/restservice/files/upload",
      "file_exists": "/api/Sservice6/pas/restservice/files/file/exists",
      "submit_job": "/api/Service6/pas/restservice/jobs",
      "job_status": "/api/storage/jobs/{job_id}",
      "list_files": "/api/Sservice6/pas/restservice/files/file/list",
      "download_file": "/api/Service6/pas/restservice/files/download"
    },
    "environments": {
      "dev": {
        "base_url": "https://10.24.129.168:4443/pbsworks",
        "server_name": "hpccluster",
        "stage_path_template": "/ssddata/stage/$USER",
        "username": "pbs_dev_user",
        "password": "pbs_dev_password",
        "verify_ssl": false
      },
      "prod": {
        "base_url": "https://prod-host:4443/pbsworks",
        "server_name": "hpccluster",
        "stage_path_template": "/ssddata/stage/$USER",
        "username": "pbs_prod_user",
        "password": "pbs_prod_password",
        "verify_ssl": true
      }
    }
  }
}
```

说明：

- `base_url` 直接写到 `/pbsworks` 这一层，代码不会再额外补前缀。
- `api_paths` 建议全部放在 `PBS.api_paths` 下统一管理；如果某个环境有差异，再在 `PBS.environments.<env>.api_paths` 里局部覆盖。
- `job_query`、`dynamic_app_def` 目前主流程没有直接调用，但建议仍然完整列出来，便于后续调试和能力扩展。

## 4. 工程 `project_config.pbs` 示例

### 4.1 Abaqus 工程配置示例

```json
{
  "pbs": {
    "env": "dev",
    "ApplicationId": "Abaqus",
    "ApplicationName": "Abaqus",
    "VERSION": "2022",
    "PLATFORM": "MultiCore-48c-394G|Free:528|Total:672",
    "CORES": 48,
    "HOSTS": 1,
    "PRECISION": "off",
    "primary_file_exts": [".inp"],
    "result_exts": [".odb", ".dat", ".msg", ".sta", ".log", ".prt"]
  }
}
```

### 4.2 Nastran 工程配置示例

```json
{
  "pbs": {
    "env": "dev",
    "ApplicationId": "Nastran",
    "ApplicationName": "Nastran",
    "VERSION": "2019",
    "PLATFORM": "MultiCore-48c-394G|Free:528|Total:672",
    "CORES": 8,
    "primary_file_exts": [".bdf", ".dat", ".nas"],
    "result_exts": [".f06", ".op2", ".pch", ".xdb", ".log", ".out"]
  }
}
```

说明：

- `primary_file_exts` 和 `result_exts` 不传时，代码会使用内置默认值。
- `ApplicationId`、`ApplicationName`、`VERSION`、`PLATFORM` 缺任何一项都会报“PBS 应用配置不完整”。
- Nastran 不要配置 `MEMORY`，代码默认也不会在 PBS 提交载荷中生成 `MEMORY`。

## 5. 接口总览

当前 PBS 相关接口共 3 个：

- `POST /solver/pbs/abaqus/run`
- `POST /solver/pbs/nastran/run`
- `POST /solver/pbs/job/status`

### 5.1 统一成功返回包

```json
{
  "ok": true,
  "code": 200,
  "message": "success message",
  "data": {}
}
```

### 5.2 统一失败返回包

```json
{
  "ok": false,
  "code": 500,
  "message": "error message",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "error message",
    "details": {}
  }
}
```

说明：

- 当前模型修正接口统一对外只暴露 `200` 和 `500` 两种业务码。
- 如果底层原始状态码不是 `500`，会放到 `error.details.original_status_code` 中。

## 6. `POST /solver/pbs/abaqus/run`

### 6.1 接口含义

把本地 `.inp` 文件提交到 PBS 上运行 Abaqus，可选等待任务结束并下载结果文件。

### 6.2 影响数据库

- 读取：`t_mt_work_condition_project.project_config`
- 写入：无

说明：

- 当前 PBS 接口只负责任务调度和结果下载，不直接写业务库。
- 读取数据库的目的仅仅是获取工程 `project_config.pbs`。

### 6.3 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | `int` | 是 | 项目 ID；用于读取工程 `project_config.pbs` |
| `env` | `string` | 否 | PBS 环境名；不传则优先取工程 `project_config.pbs.env`，再取 `service_config.json` 的 `PBS.env` |
| `input_file` | `string` | 是 | 本地 Abaqus 主输入文件路径，当前要求为 `.inp` |
| `job_name` | `string` | 否 | PBS 作业名；不传则使用输入文件名 |
| `output_dir` | `string` | 否 | 本地结果下载目录；不传则使用输入文件所在目录 |
| `wait` | `bool` | 否 | 是否等待远程任务完成，默认 `true` |
| `download_results` | `bool` | 否 | 是否下载结果文件，默认 `true` |
| `poll_interval_sec` | `float` | 否 | 轮询间隔秒数，默认 `10.0` |
| `wait_timeout_sec` | `int` | 否 | 等待任务完成超时秒数，默认 `3600` |
| `timeout_sec` | `int` | 否 | 单次 HTTP 请求超时秒数，默认 `60` |
| `submit_overrides` | `object` | 否 | 对 PBS 提交载荷的覆盖项，如 `CORES`、`HOSTS`、`PRECISION` |
| `async_submit` | `bool` | 否 | 是否后台提交，默认 `false` |

### 6.4 同步调用示例

```json
{
  "project_id": 1001,
  "env": "dev",
  "input_file": "D:/demo/abaqus/main.inp",
  "job_name": "abaqus_case_01",
  "output_dir": "D:/demo/abaqus/output",
  "wait": true,
  "download_results": true,
  "poll_interval_sec": 10.0,
  "wait_timeout_sec": 3600,
  "timeout_sec": 60,
  "submit_overrides": {
    "CORES": 32,
    "HOSTS": 1,
    "PRECISION": "off"
  },
  "async_submit": false
}
```

### 6.5 同步成功返回示例

```json
{
  "ok": true,
  "code": 200,
  "message": "PBS Abaqus 求解成功",
  "data": {
    "env": "dev",
    "application": "Abaqus",
    "job_name": "abaqus_case_01",
    "job_id": "123456",
    "remote_job_dir": "/ssddata/stage/user/Abaqus_abaqus_case_01_20260521_102030",
    "remote_primary_file": "/ssddata/stage/user/Abaqus_abaqus_case_01_20260521_102030/main.inp",
    "stage_root": "/ssddata/stage/user",
    "submit_payload": {
      "ApplicationId": "Abaqus",
      "ApplicationName": "Abaqus",
      "JOB_NAME": "abaqus_case_01",
      "PLATFORM": "MultiCore-48c-394G|Free:528|Total:672",
      "PRIMARY_FILE": [
        {
          "value": "/ssddata/stage/user/Abaqus_abaqus_case_01_20260521_102030/main.inp"
        }
      ],
      "SUBMISSION_DIRECTORY": {
        "value": "/ssddata/stage/user/Abaqus_abaqus_case_01_20260521_102030"
      },
      "VERSION": "2022",
      "myapp": "OtherApps",
      "CORES": 32,
      "HOSTS": 1,
      "PRECISION": "off"
    },
    "status": "submitted",
    "job_status": {
      "jobState": "C"
    },
    "resolved_job_state": "C",
    "result_files": [],
    "result_file_paths": [
      "/ssddata/stage/user/Abaqus_abaqus_case_01_20260521_102030/main.odb"
    ],
    "downloaded_files": [
      "D:/demo/abaqus/output/main.odb"
    ],
    "input_file": "D:\\demo\\abaqus\\main.inp",
    "output_dir": "D:\\demo\\abaqus\\output",
    "project_id": 1001,
    "workflow": "pbs_abaqus_run"
  }
}
```

### 6.6 异步提交示例

```json
{
  "project_id": 1001,
  "input_file": "D:/demo/abaqus/main.inp",
  "async_submit": true
}
```

### 6.7 异步提交返回示例

```json
{
  "ok": true,
  "code": 200,
  "message": "PBS Abaqus 任务已提交",
  "data": {
    "task_id": "2d7d30b4-0d7d-4c34-b6b7-3cbce0f1e111",
    "task_type": "solver.pbs.abaqus.run",
    "status": "submitted",
    "submitted_at": "2026-05-21T10:20:30Z",
    "started_at": null,
    "finished_at": null,
    "request": {
      "project_id": 1001,
      "input_file": "D:/demo/abaqus/main.inp",
      "async_submit": true
    },
    "progress": null,
    "result": null,
    "error": null
  }
}
```

### 6.8 主要返回字段说明

| 字段 | 说明 |
| --- | --- |
| `job_id` | PBS 任务 ID |
| `remote_job_dir` | PBS 远程任务目录 |
| `remote_primary_file` | 上传后的远程主输入文件 |
| `stage_root` | 远程 stage 根目录 |
| `submit_payload` | 实际提交给 PBS 的请求载荷 |
| `job_status` | PBS 返回的原始任务状态对象 |
| `resolved_job_state` | 代码提取后的任务状态，常见值 `Q/R/C/F` |
| `result_files` | PBS 文件列表接口返回的原始文件对象列表 |
| `result_file_paths` | 过滤后的结果文件路径列表 |
| `downloaded_files` | 本地下载完成的结果文件路径列表 |
| `workflow` | 当前工作流标识，Abaqus 为 `pbs_abaqus_run` |

## 7. `POST /solver/pbs/nastran/run`

### 7.1 接口含义

把本地 `.bdf/.dat/.nas` 文件提交到 PBS 上运行 Nastran，可选等待任务结束并下载结果文件。

### 7.2 影响数据库

- 读取：`t_mt_work_condition_project.project_config`
- 写入：无

### 7.3 请求字段

请求字段与 `POST /solver/pbs/abaqus/run` 基本一致，差异点如下：

- `input_file` 当前要求为 `.bdf`、`.dat`、`.nas`
- `submit_overrides` 建议主要用于覆盖 `CORES`
- Nastran 不再配置也不再提交 `MEMORY`

### 7.4 调用示例

```json
{
  "project_id": 1002,
  "env": "dev",
  "input_file": "D:/demo/nastran/main.bdf",
  "job_name": "nastran_case_01",
  "output_dir": "D:/demo/nastran/output",
  "wait": true,
  "download_results": true,
  "submit_overrides": {
    "CORES": 16
  },
  "async_submit": false
}
```

### 7.5 成功返回示例

```json
{
  "ok": true,
  "code": 200,
  "message": "PBS Nastran 求解成功",
  "data": {
    "env": "dev",
    "application": "Nastran",
    "job_name": "nastran_case_01",
    "job_id": "654321",
    "remote_job_dir": "/ssddata/stage/user/Nastran_nastran_case_01_20260521_102030",
    "remote_primary_file": "/ssddata/stage/user/Nastran_nastran_case_01_20260521_102030/main.bdf",
    "stage_root": "/ssddata/stage/user",
    "submit_payload": {
      "ApplicationId": "Nastran",
      "ApplicationName": "Nastran",
      "JOB_NAME": "nastran_case_01",
      "PLATFORM": "MultiCore-48c-394G|Free:528|Total:672",
      "PRIMARY_FILE": [
        {
          "value": "/ssddata/stage/user/Nastran_nastran_case_01_20260521_102030/main.bdf"
        }
      ],
      "SUBMISSION_DIRECTORY": {
        "value": "/ssddata/stage/user/Nastran_nastran_case_01_20260521_102030"
      },
      "VERSION": "2019",
      "myapp": "OtherApps",
      "CORES": 16
    },
    "status": "submitted",
    "job_status": {
      "jobState": "C"
    },
    "resolved_job_state": "C",
    "result_files": [],
    "result_file_paths": [
      "/ssddata/stage/user/Nastran_nastran_case_01_20260521_102030/main.op2",
      "/ssddata/stage/user/Nastran_nastran_case_01_20260521_102030/main.f06"
    ],
    "downloaded_files": [
      "D:/demo/nastran/output/main.op2",
      "D:/demo/nastran/output/main.f06"
    ],
    "input_file": "D:\\demo\\nastran\\main.bdf",
    "output_dir": "D:\\demo\\nastran\\output",
    "project_id": 1002,
    "workflow": "pbs_nastran_run"
  }
}
```

### 7.6 注意事项

- 如果 `project_config.pbs` 中缺少 `ApplicationId`、`ApplicationName`、`VERSION`、`PLATFORM` 中任一项，会直接失败。
- 如果输入文件后缀不在 `primary_file_exts` 中，会报扩展名不支持。
- 当前实现按 `result_exts` 过滤下载结果，因此工程里如需下载更多扩展名，要同步调整 `result_exts`。

## 8. `POST /solver/pbs/job/status`

### 8.1 接口含义

查询一个 PBS 任务的当前状态。

### 8.2 影响数据库

- 读取：`t_mt_work_condition_project.project_config`
- 写入：无

### 8.3 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | `int` | 是 | 项目 ID；用于读取工程 `project_config.pbs` 并先校验 PBS 配置 |
| `env` | `string` | 否 | PBS 环境名 |
| `job_id` | `string` | 是 | PBS 任务 ID |
| `timeout_sec` | `int` | 否 | 单次状态查询请求超时秒数，默认 `60` |

### 8.4 调用示例

```json
{
  "project_id": 1002,
  "env": "dev",
  "job_id": "654321",
  "timeout_sec": 60
}
```

### 8.5 成功返回示例

```json
{
  "ok": true,
  "code": 200,
  "message": "PBS 任务状态获取成功",
  "data": {
    "env": "dev",
    "project_id": 1002,
    "job_id": "654321",
    "job_status": {
      "jobState": "R"
    },
    "resolved_job_state": "R"
  }
}
```

### 8.6 返回字段说明

| 字段 | 说明 |
| --- | --- |
| `job_status` | PBS 返回的原始任务状态对象 |
| `resolved_job_state` | 解析后的任务状态 |

常见 `resolved_job_state`：

- `Q`：排队中
- `R`：运行中
- `C`：完成
- `F`：失败

## 9. 常见失败场景

### 9.1 未配置工程 PBS

```json
{
  "ok": false,
  "code": 500,
  "message": "请先配置高性能集群计算配置",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请先配置高性能集群计算配置",
    "details": {
      "project_id": 1002,
      "field": "project_config.pbs"
    }
  }
}
```

### 9.2 工程 PBS 应用信息不完整

```json
{
  "ok": false,
  "code": 500,
  "message": "PBS 应用配置不完整，请先在工程 project_config.pbs 中配置",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "PBS 应用配置不完整，请先在工程 project_config.pbs 中配置",
    "details": {
      "application": "Nastran",
      "env": "dev",
      "missing_fields": [
        "platform",
        "version"
      ],
      "field": "project_config.pbs"
    }
  }
}
```

### 9.3 输入文件不存在

```json
{
  "ok": false,
  "code": 500,
  "message": "未找到 PBS 本地输入文件",
  "data": null,
  "error": {
    "code": "NOT_FOUND",
    "message": "未找到 PBS 本地输入文件",
    "details": {
      "input_file": "D:\\demo\\not_exists.bdf",
      "original_status_code": 404
    }
  }
}
```

### 9.4 输入文件后缀不支持

```json
{
  "ok": false,
  "code": 500,
  "message": "pbs primary file extension is not supported for application",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "pbs primary file extension is not supported for application",
    "details": {
      "application": "Abaqus",
      "input_file": "D:\\demo\\main.bdf",
      "input_ext": ".bdf",
      "allowed_exts": [
        ".inp"
      ]
    }
  }
}
```

## 10. 当前实现边界

### 10.1 已支持

- 单主文件 PBS 提交
- Abaqus `.inp`
- Nastran `.bdf/.dat/.nas`
- 等待任务完成
- 根据 `result_exts` 下载结果文件
- 异步后台提交

### 10.2 暂未支持

- 一次请求上传多个输入文件
- `include_files` 自动上传
- 在提交载荷中写入 `INCLUDEFILES`
- 自动分析失败日志
- 任务恢复 / 断点续传

## 11. 关于多文件提交的后续建议

PBS 平台本身支持：

- `PRIMARY_FILE`
- `INCLUDEFILES`

所以后续可以扩展成下面这种接口形式：

```json
{
  "project_id": 32,
  "env": "dev",
  "input_file": "D:/demo/main.bdf",
  "include_files": [
    "D:/demo/property.bdf"
  ],
  "job_name": "demo_job",
  "wait": true,
  "download_results": true,
  "submit_overrides": {}
}
```

建议实现顺序：

1. `PBSSolverRunRequest` 增加 `include_files`
2. `run_pbs_solver_job()` 增加 `include_files`
3. `PBSClient.run_job()` 上传附加文件
4. `build_submit_payload()` 写入 `INCLUDEFILES`
5. 返回值补充 `remote_include_files`

## 12. 代码对应关系

关键实现位置如下：

- 配置读取与校验：`services/model_update/analysis/pbs_service.py`
- 请求模型：`webapi/models.py`
- 接口路由：`webapi/routers/solver.py`
- 统一响应包：`webapi/common.py`

如果后续文档和实现不一致，以这几个文件中的代码行为为准。
