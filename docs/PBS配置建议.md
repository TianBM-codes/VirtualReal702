# PBS 配置建议

## 1. 这份文档解决什么问题

这份文档专门回答两个实际问题：

1. PBS 这条链路是否适合测试“两个 `inp`”和“两个 `bdf`”一起提交。
2. 除了数据库配置，还需要在 `service_config.json` 里放哪些字段，怎么分工更合适。

本文依据：

- `docs/PBS接口文档.xlsx`
- 当前实现 `services/model_update/analysis/pbs_service.py`
- 当前接口模型 `webapi/models.py`
- 当前 PBS 路由 `webapi/routers/solver.py`

---

## 2. 先说结论

### 2.1 关于“两个 inp / 两个 bdf”

PBS 平台本身是支持“一个主文件 + 若干 include 文件”的。

从 `docs/PBS接口文档.xlsx` 里的“获取应用模版”“提交任务参数”可以看到，Abaqus 和 Nastran 的提交参数里都存在：

- `PRIMARY_FILE`
- `INCLUDEFILES`

这说明 PBS 设计上并不是只能传一个文件，而是：

- `PRIMARY_FILE`：主控输入文件
- `INCLUDEFILES`：主文件依赖的附加文件

但是，**我们项目当前代码还没有把这条能力接起来**。现在的实现只有单个 `input_file`：

- `PBSSolverRunRequest` 只有 `input_file`
- `run_pbs_solver_job()` 只接收一个 `input_file`
- `PBSClient.run_job()` 只上传一个主文件
- `build_submit_payload()` 没有写入 `INCLUDEFILES`

所以当前现状不是“PBS 不支持”，而是“**我们现在的接口层还不支持一次请求上传两个文件**”。

### 2.2 关于配置分工

建议把配置分成两层：

1. `service_config.json` 放“平台级、环境级、基本不随项目变化”的配置。
2. 数据库放“项目级、任务级、会跟具体算例变化”的配置。

简单理解：

- `service_config.json` 解决“连到哪台 PBS、怎么登录、接口前缀是什么、默认应用参数是什么”
- 数据库解决“这个项目这次要用 Abaqus 还是 Nastran、核数多少、平台选哪个、是否切到 prod 环境”

这样做的好处是：

- 运维参数集中管理，不容易每个项目都抄一份
- 项目参数能按项目覆盖，不会影响别的项目
- 开发和排错时边界清楚，知道问题出在“平台连接”还是“项目提交参数”

---

## 3. 当前代码对多文件提交的真实支持情况

## 3.1 现在已经支持的部分

当前 PBS 流程是：

1. 登录 PBS
2. 展开远端 stage 目录
3. 创建远端任务目录
4. 上传一个主文件
5. 校验主文件存在
6. 组装提交参数
7. 提交任务
8. 轮询状态
9. 下载结果文件

这条链路对“单个 Abaqus 输入文件”或“单个 Nastran 输入文件”是成立的。

## 3.2 现在缺的部分

如果你要测试：

- 两个 `inp`
- 两个 `bdf`

本质上通常不是“两个同级主文件一起求解”，而是：

- 一个主文件
- 一个被主文件 `include` 进去的附加文件

当前代码缺两块：

1. 请求体里没有“附加文件列表”字段。
2. 提交 PBS 时没有把附加文件写进 `INCLUDEFILES`。

所以如果现在直接拿两个本地文件来测：

- 第一个文件会被上传
- 第二个文件不会被上传
- 如果主文件里引用了第二个文件，远端大概率会找不到

## 3.3 对 a 问题的建议结论

### Abaqus：两个 `inp`

建议支持，但语义应定义为：

- `input_file`：主 `inp`
- `include_files`：被 `*INCLUDE` 引用的附加 `inp`

不建议把“两份独立 `inp`”理解成“同时提交两个主任务文件”，因为 PBS 模版里的核心概念仍然是一个 `PRIMARY_FILE`。

### Nastran：两个 `bdf`

建议支持，但语义也应定义为：

- `input_file`：主 `bdf`
- `include_files`：被 `INCLUDE` 引用的附加 `bdf/dat/nas`

这个场景比 Abaqus 更常见，因为 Nastran 本来就经常把材料、属性、载荷、子工况拆成多个 deck。

### 现阶段是否能直接测试

如果不改代码，**不建议现在就用“上传两个文件”做 PBS 接口测试**。

原因很简单：

- 现在接口没有这个入参
- 服务层也不会上传第二个文件
- 提交载荷里也不会带 `INCLUDEFILES`

也就是说，现在测出来的失败，更像“接口没实现”，不是“PBS 平台不支持”。

---

## 4. 建议的配置分层

## 4.1 `service_config.json` 里放什么

建议放“平台公共配置”和“应用默认模板”。

### A. 平台连接信息

这些字段建议固定放在 `service_config.json`：

- `env`
- `environments.dev.base_url`
- `environments.dev.api_prefix`
- `environments.dev.service_prefix`
- `environments.dev.storage_prefix`
- `environments.dev.auth_path`
- `environments.dev.server_name`
- `environments.dev.stage_path_template`
- `environments.dev.username`
- `environments.dev.password`
- `environments.dev.verify_ssl`
- `environments.prod.*`
- `fallback_service_prefixes`

这些字段的共同特点是：

- 和 PBS 平台部署方式有关
- 通常一个环境里大家共用
- 不应该让业务项目各自乱填

### B. 应用默认模板

这些字段也建议放 `service_config.json`，作为默认值：

- `applications.Abaqus.application_id`
- `applications.Abaqus.application_name`
- `applications.Abaqus.version`
- `applications.Abaqus.cores`
- `applications.Abaqus.hosts`
- `applications.Abaqus.precision`
- `applications.Abaqus.platform`
- `applications.Abaqus.primary_file_exts`
- `applications.Abaqus.result_exts`
- `applications.Nastran.application_id`
- `applications.Nastran.application_name`
- `applications.Nastran.version`
- `applications.Nastran.cores`
- `applications.Nastran.memory`
- `applications.Nastran.platform`
- `applications.Nastran.primary_file_exts`
- `applications.Nastran.result_exts`

原因是：

- 这些字段本质上是在定义“平台上这个应用怎么提交”
- 它们更像“默认模板”，不是业务数据
- 新项目如果没有单独覆盖，也应该能直接跑

## 4.2 数据库里放什么

建议数据库只保存“项目对默认模板的覆盖项”。

以当前代码看，项目级 PBS 配置来自 `t_mt_work_condition_project.project_config` 里的 `pbs` 节点。建议项目里只放这些：

- `env`
- `ApplicationId`
- `ApplicationName`
- `VERSION`
- `CORES`
- `HOSTS`
- `MEMORY`
- `PRECISION`
- `PLATFORM`

如果后面支持 include，再加：

- `include_mode`
- `include_files`

建议意义如下：

- `env`：这个项目默认走 `dev` 还是 `prod`
- `VERSION`：这个项目要求的求解器版本
- `CORES/HOSTS/MEMORY`：这个项目的资源申请
- `PRECISION`：Abaqus 的求解精度/求解类型
- `PLATFORM`：这个项目固定跑哪个队列/平台

不建议把下面这些放数据库：

- `base_url`
- `username`
- `password`
- `auth_path`
- `service_prefix`
- `storage_prefix`
- `fallback_service_prefixes`

原因是这些属于“平台接入配置”，不是“项目业务配置”。放数据库后会带来几个问题：

- 同一套平台配置会被项目重复保存
- 改密码或改地址时要改很多地方
- 容易出现有的项目能连、有的项目不能连，但其实是同一平台

---

## 5. 推荐的 `service_config.json` 结构

建议在现有 `service_config.json` 基础上补一个 `PBS` 节点，结构类似下面这样：

```json
{
  "APP_DATA_ROOT": "D:\\WorkSpace\\OtherProjects\\VirtualReal702\\model\\",
  "APP_REGISTRY_DB_PATH": "D:\\WorkSpace\\OtherProjects\\VirtualReal702\\model\\registry.db",
  "APP_ENABLE_GZIP": 1,
  "APP_EMBEDDED_RUNNER": 0,
  "APP_ABAQUS_CMD": "C:\\Program Files\\SIMULIA\\Commands\\abaqus.bat",
  "APP_PYTHON3_CMD": "C:\\SoftWare\\Python\\python.exe",
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
    "fallback_service_prefixes": [
      "/api/Sservice6"
    ],
    "environments": {
      "dev": {
        "base_url": "https://10.24.129.168:4443",
        "api_prefix": "/pbsworks",
        "service_prefix": "/api/Service6",
        "storage_prefix": "/api/storage",
        "auth_path": "/api/ams/aaservice/authn/oauth2/token",
        "server_name": "hpccluster",
        "stage_path_template": "/ssddata/stage/$USER",
        "username": "你的 PBS 用户名",
        "password": "你的 PBS 密码",
        "verify_ssl": false
      },
      "prod": {
        "base_url": "https://prod-host:4443",
        "api_prefix": "/pbsworks",
        "service_prefix": "/api/Service6",
        "storage_prefix": "/api/storage",
        "auth_path": "/api/ams/aaservice/authn/oauth2/token",
        "server_name": "hpccluster",
        "stage_path_template": "/ssddata/stage/$USER",
        "username": "你的 PBS 用户名",
        "password": "你的 PBS 密码",
        "verify_ssl": true
      }
    },
    "applications": {
      "Abaqus": {
        "application_id": "Abaqus",
        "application_name": "Abaqus",
        "version": "2022",
        "cores": 48,
        "hosts": 1,
        "precision": "off",
        "platform": "MultiCore-48c-394G|Free:528|Total:672",
        "primary_file_exts": [".inp"],
        "result_exts": [".odb", ".dat", ".msg", ".sta", ".log", ".prt"]
      },
      "Nastran": {
        "application_id": "Nastran",
        "application_name": "Nastran",
        "version": "2019",
        "cores": 8,
        "memory": 2048,
        "platform": "MultiCore-48c-394G|Free:528|Total:672",
        "primary_file_exts": [".bdf", ".dat", ".nas"],
        "result_exts": [".f06", ".op2", ".pch", ".xdb", ".log", ".out"]
      }
    }
  }
}
```

---

## 6. 为什么这样分更合理

## 6.1 `service_config.json` 管“怎么连平台”

这部分一旦填好，所有项目都能共用。

比如：

- 登录地址
- 接口前缀
- 下载接口路径
- 是否校验证书
- 默认 stage 根目录

这些参数跟“你算车身模型还是支架模型”没关系，它只跟“你连的是哪套 PBS”有关。

## 6.2 数据库管“这个项目怎么跑”

数据库更适合保存：

- 这个项目默认跑 Abaqus 还是 Nastran
- 核数要 8 还是 48
- 内存要 2048 还是更大
- 走 `dev` 环境还是 `prod` 环境
- 平台选 `MultiCore` 还是 `Fat`

这样一个项目临时要降配、切环境、切平台时，只改项目配置就够了，不会把全局环境搞乱。

---

## 7. 针对“两个 inp / 两个 bdf”的接口建议

如果后续要支持多文件，建议不要新增“`input_file_2`、`input_file_3`”这种字段，因为很快就会失控。

更稳妥的接口设计是：

```json
{
  "project_id": 32,
  "env": "dev",
  "input_file": "D:/demo/main.inp",
  "include_files": [
    "D:/demo/material.inp"
  ],
  "job_name": "demo_job",
  "wait": true,
  "download_results": true,
  "submit_overrides": {}
}
```

或者 Nastran：

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

服务层建议做的事：

1. 先创建远端任务目录。
2. 上传主文件。
3. 再把 `include_files` 逐个上传到同一个远端目录。
4. 在提交参数里补上 `INCLUDEFILES`。
5. 返回时把 `remote_include_files` 一起带回来，方便排错。

这样做的好处是：

- 前端和接口都很直观
- 后面从 2 个文件扩展到 5 个文件也不用改协议
- 语义和 PBS 自己的 `PRIMARY_FILE + INCLUDEFILES` 一致

---

## 8. 建议的测试顺序

为了避免一上来把“平台问题”“接口问题”“求解器 include 语法问题”混在一起，建议按下面顺序测：

1. 先测单文件 `inp` 提交，确认 PBS 登录、建目录、上传、提交、下载都通。
2. 再测单文件 `bdf` 提交，确认 Nastran 模版参数正确。
3. 再做“一主一附”的 `inp + inp`。
4. 最后做“一主一附”的 `bdf + bdf`。

这样排查会很快，因为每一步只新增一个变量。

---

## 9. 推荐落地方案

### 第一阶段

先不改数据库结构，只做这两件事：

1. 在 `service_config.json` 增加完整 `PBS` 节点。
2. 继续让数据库只覆盖 `env / VERSION / CORES / HOSTS / MEMORY / PRECISION / PLATFORM`。

### 第二阶段

在 PBS 接口里补多文件能力：

1. `PBSSolverRunRequest` 增加 `include_files: List[str] = []`
2. `run_pbs_solver_job()` 增加 `include_files`
3. `PBSClient.run_job()` 上传 include 文件
4. `build_submit_payload()` 写入 `INCLUDEFILES`
5. 增加两组测试：
   - `main.inp + child.inp`
   - `main.bdf + child.bdf`

### 第三阶段

如果业务上确实存在“某些项目总是固定带某几个附加 deck”，再考虑把 `include_files` 的默认模板写入数据库。

不建议一开始就把它做成数据库永久字段，原因是：

- include 文件通常更像“本次提交的输入物料”
- 它跟文件路径强相关
- 很容易因为路径变了导致数据库里的记录失效

---

## 10. 最终回答

### 对问题 a 的回答

可以支持，但要分清“PBS 平台支持”和“当前项目代码支持”。

- PBS 平台：支持，通过 `PRIMARY_FILE + INCLUDEFILES`
- 当前项目代码：暂不支持一次请求上传两个文件

所以你现在这个需求是合理的，但需要补接口实现，不能直接拿现有接口当成已经支持。

### 对问题 b 的回答

建议这样配置：

- `service_config.json` 放 PBS 环境连接信息和应用默认模板
- 数据库放项目级覆盖参数

最重要的原则是：

- 平台地址、登录、前缀、证书、默认 stage 目录放全局
- 核数、内存、版本、平台、精度、环境选择放项目

这样后续维护成本最低，也最不容易把配置搞乱。
