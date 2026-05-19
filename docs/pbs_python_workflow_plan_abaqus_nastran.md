# Altair PBS Python 调用方案（Abaqus / Nastran + 环境切换 + 前缀适配）

## 1. 目标

用 Python 串联 PBS 接口，实现：

```text
登录 → 解析远端用户目录 → 创建任务目录 → 上传输入文件 → 校验文件
→ 按应用组装提交参数 → 提交计算 → 轮询状态 → 查询结果 → 下载结果
```

要求：

- 同时支持 `Abaqus` 和 `Nastran`。
- 支持开发环境、工作环境切换。
- 支持接口前缀、服务前缀、远端路径前缀配置化。
- 用户名和密码为固定配置。
- 不使用“获取应用模板”接口。
- 接口之间有依赖关系，上一个接口返回值要作为后续接口参数。

---

## 2. 环境配置

建议用配置文件维护环境差异：

```yaml
env: dev

environments:
  dev:
    base_url: "https://dev-host:4443"
    api_prefix: "/pbsworks"
    service_prefix: "/api/Service6"
    storage_prefix: "/api/storage"
    auth_path: "/api/ams/aaservice/authn/oauth2/token"
    server_name: "hpccluster"
    stage_path_template: "/ssddata/stage/$USER"
    username: "<fixed_username>"
    password: "<fixed_password>"
    verify_ssl: false

  prod:
    base_url: "https://prod-host:4443"
    api_prefix: "/pbsworks"
    service_prefix: "/api/Service6"
    storage_prefix: "/api/storage"
    auth_path: "/api/ams/aaservice/authn/oauth2/token"
    server_name: "hpccluster"
    stage_path_template: "/ssddata/stage/$USER"
    username: "<fixed_username>"
    password: "<fixed_password>"
    verify_ssl: true

fallback_service_prefixes:
  - "/api/Sservice6"
```

### 前缀说明

| 配置项 | 说明 |
|---|---|
| `base_url` | 协议、主机、端口，例如 `https://host:4443` |
| `api_prefix` | 系统统一前缀，例如 `/pbsworks`；没有时配置为空字符串 |
| `service_prefix` | 文件、目录、提交任务接口前缀 |
| `storage_prefix` | 查询任务状态接口前缀 |
| `auth_path` | 登录接口路径 |
| `stage_path_template` | 远端工作目录模板，例如 `/ssddata/stage/$USER` |

完整 URL 统一拼接：

```text
full_url = base_url + api_prefix + path
```

例如：

```text
https://host:4443/pbsworks/api/Service6/pas/restservice/files/upload
```

---

## 3. 接口路径统一管理

不要在业务代码中散落接口地址，统一定义：

```python
API_PATHS = {
    "login": "{auth_path}",
    "expand_vars": "{service_prefix}/pas/restservice/files/expandvars",
    "create_dir": "{service_prefix}/pas/restservice/files/dir/create",
    "upload_file": "{service_prefix}/pas/restservice/files/upload",
    "file_exists": "{service_prefix}/pas/restservice/files/file/exists",
    "submit_job": "{service_prefix}/pas/restservice/jpbs",
    "job_status": "{storage_prefix}/jobs/{job_id}",
    "list_files": "{service_prefix}/pas/restservice/files/file/list",
    "download_file": "{service_prefix}/pas/restservice/files/download"
}
```

---

## 4. 文档笔误兼容

| 文档问题 | 处理方式 |
|---|---|
| `/ssdata` 与 `/ssddata` 混用 | 不写死路径，以 `expandvars(stage_path_template)` 返回值为准 |
| `/api/Sservice6/...` | 默认使用 `service_prefix`，404 时尝试 `fallback_service_prefixes` |
| `PEIMARY_FILE` | 按 `PRIMARY_FILE` 处理 |
| `downladCookie` | 优先按文档字段传，失败再尝试 `downloadCookie` |
| `success` 类型不统一 | 兼容 `true`、`"true"` 等格式 |
| 鉴权方式不统一 | 优先使用请求头 `access_token: <token>` |

---

## 5. 接口依赖关系

| 步骤 | 输出 | 后续用途 |
|---|---|---|
| 登录 | `access_token` | 所有接口鉴权 |
| 展开路径 | `stage_root` | 拼接任务目录 |
| 创建目录 | `remote_job_dir` | 上传、提交、查结果 |
| 上传文件 | `remote_primary_file` | 文件校验、提交任务 |
| 文件校验 | `fileExists=true` | 允许提交 |
| 提交任务 | `jobId` | 状态查询、下载结果 |
| 查询状态 | `jobState` | 判断是否下载 |
| 查询结果 | `result_files` | 下载文件 |

---

## 6. 应用配置

```python
APP_CONFIG = {
    "Abaqus": {
        "application_id": "Abaqus",
        "application_name": "Abaqus",
        "version": "2022",
        "cores": 48,
        "hosts": 1,
        "precision": "off",
        "platform": "<abaqus_platform>",
        "primary_file_exts": [".inp"],
        "result_exts": [".odb", ".dat", ".msg", ".sta", ".log", ".prt"]
    },
    "Nastran": {
        "application_id": "Nastran",
        "application_name": "Nastran",
        "version": "2019",
        "cores": 8,
        "memory": 2048,
        "platform": "<nastran_platform>",
        "primary_file_exts": [".bdf", ".dat", ".nas"],
        "result_exts": [".f06", ".op2", ".pch", ".xdb", ".log", ".out"]
    }
}
```

---

## 7. 核心流程

### 7.1 登录

```text
POST {base_url}{api_prefix}{auth_path}
```

请求：

```json
{
  "grant_type": "password",
  "username": "<fixed_username>",
  "password": "<fixed_password>"
}
```

返回 `access_token`，后续接口请求头带：

```text
access_token: <access_token>
```

### 7.2 展开远端用户目录

```text
POST {base_url}{api_prefix}{service_prefix}/pas/restservice/files/expandvars
```

请求：

```json
{
  "paths": ["<stage_path_template>"]
}
```

返回：

```text
stage_root
```

### 7.3 创建任务目录

```text
remote_job_dir = stage_root + "/" + application + "_" + job_name + "_" + timestamp
```

调用：

```text
POST {base_url}{api_prefix}{service_prefix}/pas/restservice/files/dir/create
```

请求：

```json
{
  "path": "<remote_job_dir>"
}
```

### 7.4 上传主文件

```text
POST {base_url}{api_prefix}{service_prefix}/pas/restservice/files/upload
```

表单参数：

```text
serversidefilepath = remote_job_dir
attfile = 本地主输入文件
```

上传后拼出：

```text
remote_primary_file = remote_job_dir + "/" + 本地文件名
```

### 7.5 校验主文件

```text
POST {base_url}{api_prefix}{service_prefix}/pas/restservice/files/file/exists
```

请求：

```json
{
  "paths": "<remote_primary_file>"
}
```

返回 `fileExists=true` 后才提交任务。

---

## 8. 提交任务

### 8.1 Abaqus

```text
POST {base_url}{api_prefix}{service_prefix}/pas/restservice/jpbs
?application_id=Abaqus&server_registered_name=<server_name>
```

```json
{
  "ApplicationId": "Abaqus",
  "ApplicationName": "Abaqus",
  "CORES": 48,
  "HOSTS": 1,
  "JOB_NAME": "<job_name>",
  "PLATFORM": "<abaqus_platform>",
  "PRECISION": "off",
  "PRIMARY_FILE": [
    {
      "value": "<remote_primary_file>"
    }
  ],
  "SUBMISSION_DIRECTORY": {
    "value": "<remote_job_dir>"
  },
  "VERSION": "2022",
  "myapp": "OtherApps"
}
```

### 8.2 Nastran

```text
POST {base_url}{api_prefix}{service_prefix}/pas/restservice/jpbs
?application_id=Nastran&server_registered_name=<server_name>
```

```json
{
  "ApplicationId": "Nastran",
  "ApplicationName": "Nastran",
  "CORES": 8,
  "JOB_NAME": "<job_name>",
  "MEMORY": 2048,
  "PLATFORM": "<nastran_platform>",
  "PRIMARY_FILE": [
    {
      "value": "<remote_primary_file>"
    }
  ],
  "SUBMISSION_DIRECTORY": {
    "value": "<remote_job_dir>"
  },
  "VERSION": "2019",
  "myapp": "OtherApps"
}
```

提交成功返回：

```text
jobId
```

---

## 9. 状态查询与结果下载

### 9.1 查询状态

```text
GET {base_url}{api_prefix}{storage_prefix}/jobs/{jobId}?serverName=<server_name>
```

| 状态 | 处理 |
|---|---|
| `Q` | 排队，继续等待 |
| `R` | 运行中，继续等待 |
| `C` | 完成，查询并下载结果 |
| `F` | 失败，停止并提示 |
| 其他 | 短暂等待后仍异常则提示人工检查 |

### 9.2 查询结果文件

```text
POST {base_url}{api_prefix}{service_prefix}/pas/restservice/files/file/list
```

请求：

```json
{
  "includeHidden": false,
  "path": "<remote_job_dir>"
}
```

### 9.3 下载结果文件

```text
GET {base_url}{api_prefix}{service_prefix}/pas/restservice/files/download
```

参数：

```text
serversidefilepath = 结果文件远端路径
jobid = jobId
downladCookie = downloadCookie_<timestamp>
dodelete = false
```

按应用筛选结果：

```text
Abaqus:  .odb, .dat, .msg, .sta, .log, .prt
Nastran: .f06, .op2, .pch, .xdb, .log, .out
```

---

## 10. Python 类设计

第一版建议一个 `PBSClient`：

```text
PBSClient
  load_env_config(env)
  build_url(path_key, **kwargs)
  request()
  login()
  expand_stage_root()
  create_remote_dir()
  upload_file()
  file_exists()
  build_submit_payload()
  submit_job()
  get_job_status()
  wait_until_done()
  list_result_files()
  download_file()
  run_job()
```

关键职责：

- `load_env_config(env)`：选择开发环境或工作环境。
- `build_url()`：拼接 `base_url + api_prefix + path`。
- `request()`：统一注入 `access_token`、处理 timeout、SSL、错误。
- `build_submit_payload()`：根据 `application` 组装 Abaqus 或 Nastran 参数。
- `run_job()`：封装完整流程。

---

## 11. 伪代码

```python
env = "dev"  # dev 或 prod
config = load_config(env)
client = PBSClient(config)

application = "Abaqus"  # 或 "Nastran"
job_name = "test_job"
local_primary_file = "./input.inp"

client.login()

stage_root = client.expand_stage_root(
    config.stage_path_template
)

remote_job_dir = client.make_remote_job_dir(
    stage_root=stage_root,
    application=application,
    job_name=job_name
)

client.create_remote_dir(remote_job_dir)

client.upload_file(
    local_path=local_primary_file,
    remote_dir=remote_job_dir
)

remote_primary_file = (
    remote_job_dir + "/" + basename(local_primary_file)
)

if not client.file_exists(remote_primary_file):
    raise RuntimeError("primary file upload failed")

payload = client.build_submit_payload(
    application=application,
    job_name=job_name,
    remote_primary_file=remote_primary_file,
    remote_job_dir=remote_job_dir
)

job_id = client.submit_job(
    application=application,
    payload=payload
)

job = client.wait_until_done(job_id)

if job.state != "C":
    raise RuntimeError(f"job not completed: {job.state}")

result_files = client.list_result_files(remote_job_dir)
target_exts = APP_CONFIG[application]["result_exts"]

for file in result_files:
    if file.suffix.lower() in target_exts:
        client.download_file(
            remote_file=file.abs_path,
            local_dir="./results",
            job_id=job_id
        )
```

---

## 12. 第一版范围

第一版支持：

```text
开发环境 / 工作环境切换
接口前缀配置
远端路径前缀配置
Abaqus 单主文件
Nastran 单主文件
固定账号密码
固定 server_name
固定版本、核心数、平台参数
完成后按应用下载常见结果文件
```

暂不处理：

```text
多 include 文件
动态平台选择
失败日志自动解析
断点续传
任务恢复
```
