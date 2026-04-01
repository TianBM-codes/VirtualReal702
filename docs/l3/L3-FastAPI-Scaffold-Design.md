# L3 FastAPI 服务骨架设计

## 目标

本文档只讨论 Layer 3 服务框架本身，不涉及 L1/L2 具体实现代码。

目标是先把下面这些基础问题定清楚：

- 项目目录怎么拆
- 配置怎么管理
- 日志怎么打
- 统一错误处理怎么做
- JSON 和二进制响应怎么约定
- 并发模型怎么选，避免接口“卡在那里”

这份文档面向当前阶段：

- L1/L2 还在验证实现
- L3 可以先搭服务框架和接口契约
- 先不写业务实现，只先定服务骨架

---

## 1. 设计原则

L3 的第一版框架应遵循下面几个原则：

### 1.1 先搭骨架，不先写业务细节

先把这些搭好：

- 路由层
- 服务层
- 配置层
- 日志层
- 错误处理层
- 响应协议层

不要一开始就把查询逻辑直接写进路由函数里。

### 1.2 让未来的 L1/L2 接入点清晰

虽然现在还没有完整的 L1/L2 可用产物，但 L3 框架需要明确：

- 将来从哪里拿 `manifest.db`
- 将来从哪里拿 `l1/` 和 `l2/` 文件
- 哪些服务模块依赖哪些文件和索引

### 1.3 不照搬 Node.js 的 async 心智

Python + FastAPI 的并发模型和 Node.js 不一样。

不能简单理解成：

- `async def` = 不会阻塞

实际上：

- 如果内部做了 CPU 重任务或阻塞 IO，接口照样会卡

所以必须在设计上明确：

- 什么可以直接 `async`
- 什么需要线程池
- 什么应该由多进程 worker 来承担

---

## 2. 推荐目录结构

建议 L3 服务目录采用如下结构：

```text
app/
├── main.py
├── api/
│   ├── routes/
│   │   ├── health.py
│   │   ├── jobs.py
│   │   ├── meta.py
│   │   ├── mesh.py
│   │   ├── render.py
│   │   └── query.py
│   └── router.py
├── core/
│   ├── config.py
│   ├── logging.py
│   ├── errors.py
│   ├── exception_handlers.py
│   └── response.py
├── schemas/
│   ├── common.py
│   ├── jobs.py
│   ├── meta.py
│   ├── render.py
│   └── query.py
├── services/
│   ├── registry_service.py
│   ├── model_index_service.py
│   ├── mesh_service.py
│   ├── render_service.py
│   └── query_service.py
├── infra/
│   ├── manifest_repo.py
│   ├── hdf5_repo.py
│   └── binary_stream.py
└── tests/
    ├── test_health.py
    ├── test_errors.py
    └── test_response_contract.py
```

### 2.1 分层职责

- `api/`
  对外 HTTP 接口

- `core/`
  配置、日志、异常、统一响应等基础设施

- `schemas/`
  Pydantic 请求和响应模型

- `services/`
  业务组织层，负责调用 `infra` 和拼装结果

- `infra/`
  与 HDF5、SQLite、文件系统交互

---

## 3. 推荐模块职责

### 3.1 `main.py`

职责：

- 创建 FastAPI app
- 注册路由
- 注册中间件
- 注册异常处理器
- 注册启动和关闭生命周期钩子

### 3.2 `core/config.py`

职责：

- 读取环境变量
- 维护统一配置对象

建议配置项：

- `APP_NAME`
- `APP_ENV`
- `LOG_LEVEL`
- `DATA_ROOT`
- `REGISTRY_DB_PATH`
- `DEFAULT_CHUNK_SIZE`
- `GUNICORN_WORKERS`
- `ENABLE_DEBUG_ROUTES`

### 3.2.1 配置放在哪里更合适

对当前项目，建议采用：

- **环境变量为主**
- **JSON 配置文件为辅**

不建议把配置只写死在代码里。

推荐方式：

1. 基础默认值写在代码配置模型里
2. 部署环境通过环境变量覆盖
3. 可选加载一个外部 JSON 配置文件

这样做的原因：

- Docker 部署对环境变量最友好
- 运维改配置不需要改代码
- 本地开发也可以用 JSON 文件集中管理

### 3.2.2 推荐配置来源优先级

建议优先级：

1. 环境变量
2. 外部 JSON 配置文件
3. 代码默认值

也就是说：

- 线上 Docker 环境主要靠环境变量
- 本地或测试环境可以挂一个 JSON 文件

### 3.2.3 推荐配置文件位置

可选约定：

- `/app/config/appsettings.json`
- 或由环境变量指定：
  - `APP_CONFIG_PATH=/app/config/appsettings.json`

JSON 配置文件适合放这些内容：

- 默认日志级别
- 默认 chunk 大小
- 默认 worker 数
- 路径前缀
- 功能开关

不建议把真正敏感信息硬写在 JSON 里。

### 3.2.4 Docker 部署建议

推荐容器内约定：

- 应用代码目录：`/app`
- 配置目录：`/app/config`
- 数据挂载目录：`/data`

例如：

- `DATA_ROOT=/data`
- `APP_CONFIG_PATH=/app/config/appsettings.json`

这样后面运维只需要：

- 挂载 `/data`
- 注入环境变量
- 可选挂载一份 JSON 配置

就能比较平稳地部署。

### 3.2.5 配置项分组建议

建议把配置按下面几类组织：

- `app`
- `logging`
- `storage`
- `api`
- `security`
- `runtime`

例如：

- `app.name`
- `logging.level`
- `storage.data_root`
- `api.default_chunk_size`
- `security.auth_mode`
- `runtime.gunicorn_workers`

### 3.3 `core/logging.py`

职责：

- 统一初始化日志
- 提供结构化字段
- 规范日志格式

### 3.4 `core/errors.py`

职责：

- 定义领域异常

建议异常类型：

- `AppError`
- `ValidationError`
- `NotFoundError`
- `NotReadyError`
- `ConflictError`
- `StorageError`
- `UnsupportedOperationError`

### 3.5 `core/exception_handlers.py`

职责：

- 把 Python 异常统一转换成 HTTP 响应

### 3.6 `core/response.py`

职责：

- 统一 JSON 成功响应
- 统一 JSON 错误响应
- 封装二进制流响应辅助函数

### 3.7 `core/security.py`

职责：

- 认证方式选择
- token 提取
- 占位认证校验

当前阶段不要求和 Java 后台完成真实联调，但应预留接口。

建议支持的模式：

- `disabled`
- `static_token`
- `bearer_passthrough`

说明：

- `disabled`
  本地开发或纯内网调试

- `static_token`
  通过配置文件或环境变量配置固定 token

- `bearer_passthrough`
  预留给未来 Java 侧透传 `Authorization: Bearer ...`

这样后面即使 Java 认证模式没完全确定，L3 也不用重改整体框架。

---

## 4. 路由层建议

L3 第一版只需要把路由分组和协议定下来，不需要立刻实现所有逻辑。

### 4.1 `health`

用途：

- 服务存活检查
- 配置和依赖可读性检查

建议接口：

- `GET /health/live`
- `GET /health/ready`

### 4.2 `jobs`

用途：

- 查询 ODB 作业状态

建议接口：

- `GET /api/jobs/{odb_id}`

### 4.3 `meta`

用途：

- 读 steps、frames、instances、fields、sets 等元信息

### 4.4 `mesh`

用途：

- 读基础显示壳子
- 按 chunk 或 partition 返回 geometry buffer

### 4.5 `render`

用途：

- 按 step/frame/field/component 组装云图或变形结果

### 4.6 `query`

用途：

- pick
- hover
- bbox
- set 解析

---

## 5. 统一响应约定

L3 有两类核心响应：

- JSON 元数据响应
- 二进制流响应

### 5.1 JSON 成功响应

建议统一格式：

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "request_id": "..."
  }
}
```

### 5.2 JSON 错误响应

建议统一格式：

```json
{
  "ok": false,
  "error": {
    "code": "NOT_READY",
    "message": "ODB render data is not ready",
    "details": {}
  },
  "meta": {
    "request_id": "..."
  }
}
```

### 5.3 二进制流响应

二进制响应建议：

- `Content-Type: application/octet-stream`
- 通过响应头带必要元数据
- 或配套一个 JSON metadata 接口

建议带的头：

- `X-Request-Id`
- `X-ODB-Id`
- `X-Instance-Name`
- `X-Payload-Type`
- `X-Array-Dtype`
- `X-Array-Shape`

认证通过后，响应里不需要回传 token，但日志里应记录：

- 是否启用认证
- 当前认证模式
- 请求是否通过认证

不要把 token 明文打进日志。

这样前端在消费 buffer 时更容易解包。

---

## 6. 统一错误处理设计

### 6.1 为什么要统一

L3 将来会碰到很多“不是代码崩了，而是业务状态不满足”的情况，比如：

- ODB 不存在
- L1 已完成但 L2 未完成
- ODB 已注册但 render index 未加载
- 查询参数合法，但当前 field 不存在

这些都不应该表现成随便抛一个 500。

### 6.2 建议错误分类

- `400 Bad Request`
  参数错、组合非法

- `404 Not Found`
  `odb_id`、`instance`、`set`、`field` 不存在

- `409 Conflict`
  状态冲突，比如 ODB 正在处理中，当前请求不允许执行

- `202 Accepted`
  已接收但尚未 ready，适合 `is_render_ready=False`

- `500 Internal Server Error`
  真正的内部错误

### 6.3 对应到当前项目

特别建议保留一个明确的“未就绪”错误路径：

- `code = NOT_READY`
- HTTP 状态可以是 `202`
- 带 `Retry-After`

这和主架构文档里关于 `is_render_ready` 的思路一致。

---

## 7. 日志设计

### 7.1 日志目标

L3 日志不是为了“多打点”，而是为了后面定位：

- 哪个 `odb_id` 有问题
- 哪个接口慢
- 哪个查询组合最常见
- 哪种异常最容易发生

### 7.2 建议日志字段

每条关键日志尽量带上：

- `request_id`
- `path`
- `method`
- `status_code`
- `latency_ms`
- `odb_id`
- `instance`
- `step`
- `frame_idx`
- `field`

不是每条都必须全有，但接口日志要尽量带上下文。

### 7.3 日志分层

建议至少分三类：

- 请求访问日志
- 业务关键路径日志
- 异常日志

### 7.4 不建议一开始做的事

- 过早接复杂日志平台
- 过早追求链路追踪全家桶

第一版先把本地结构化日志打干净更重要。

---

## 8. 并发模型：怎么避免接口卡住

这是当前最重要的基础设计点之一。

### 8.1 先说结论

对于这个项目，推荐的并发思路是：

- **HTTP 层：FastAPI + Uvicorn worker**
- **进程层：Gunicorn 多 worker**
- **阻塞文件读取：必要时线程池包装**
- **重 CPU 任务：不放在请求路径里做**

也就是说：

- 不靠单进程 `async` 硬扛全部请求
- 主要依赖 **多进程 worker** 承担并发

### 8.2 为什么不能简单类比 Node.js async

Node.js 里很多人会形成一种直觉：

- 只要是 async，接口就不会阻塞

在 Python/FastAPI 里，这个理解不成立。

如果你的 `async def` 里面做了这些事：

- 阻塞式 HDF5 读取
- 大量 NumPy 运算
- SQLite 阻塞访问

那事件循环照样会被占住。

### 8.3 L3 适合什么并发方式

L3 的典型请求路径其实是：

- 查 SQLite
- 读 HDF5
- 做一些 NumPy 切片和拼装
- 返回二进制流

这种场景更适合：

- 多进程 worker 做隔离和并发
- 局部阻塞 IO 用线程池包一下，避免卡事件循环

### 8.4 推荐部署模型

推荐：

- `gunicorn`
- `uvicorn.workers.UvicornWorker`
- 多个 worker 进程

因为你们主文档里本来也已经是这个方向。

### 8.5 线程和进程各自的作用

#### 多进程

适合：

- 提升并发承载
- 隔离请求
- 利用多核
- 避免单个 worker 被拖死后拖垮全局

#### 线程池

适合：

- 包装短时阻塞 IO
- 避免把阻塞读文件放在主事件循环里

#### 不建议在线请求里做的事

- 大规模几何重计算
- 长时间全模型扫描
- 重型批量预处理

这些应该留给 L2 或异步 job。

### 8.6 对当前项目的直接建议

L3 第一版遵守这条规则：

- 请求路径里只做“快速查找、切片、映射、组装”
- 不做重预处理

这样接口才不会“卡在那里”。

---

## 9. 服务层设计建议

即使现在先不实现，也应该先把服务边界定清楚。

### 9.1 `registry_service`

职责：

- 查询 `odb_id` 状态
- 判断 L1/L2 是否 ready

### 9.2 `model_index_service`

职责：

- 加载和缓存每个 ODB 的运行时索引
- 提供 `is_render_ready`
- 提供 label 到 row 的快速查找

### 9.3 `mesh_service`

职责：

- 返回 L2 基础壳子 geometry
- 支持 chunk / partition

### 9.4 `render_service`

职责：

- 按 frame/field/component 组装渲染数据
- 负责 scalar、color、deformed positions 等

### 9.5 `query_service`

职责：

- pick
- hover
- bbox
- set 到 render 的转换

---

## 10. 第一阶段应该先实现什么

虽然现在先不开发，但后续开发顺序建议如下：

### 第一批

- `main.py`
- 配置
- 日志
- 异常处理
- 统一响应
- 认证占位
- `/health/live`
- `/health/ready`

### 第二批

- `jobs` 和 `meta` 路由骨架
- `registry_service` 骨架
- `model_index_service` 骨架

### 第三批

- `mesh`、`render`、`query` 的协议层
- 用 mock 数据跑通响应结构

这样做的好处是：

- 不依赖 L1/L2 完成度
- 可以先稳定框架
- 可以先把前后端协议谈清楚

---

## 11. 当前阶段不建议做什么

### 11.1 不建议先堆空路由

只有 URL，没有响应契约和错误协议，价值不高。

### 11.2 不建议先写一堆业务逻辑

L1/L2 还没完全落地，这时业务细节太早写，返工概率高。

### 11.3 不建议把所有东西都写成 async

这会制造“好像不会阻塞”的错觉。

应先根据请求路径里的真实操作决定：

- 普通函数
- `async def`
- 线程池包装
- 单独 job

---

## 12. 最终结论

L3 现在最合理的起手方式不是直接开发业务，而是先定好：

- FastAPI 服务目录结构
- 配置体系
- 结构化日志
- 统一错误处理
- 可切换的认证占位
- JSON / 二进制响应契约
- 多进程为主、线程池为辅的并发模型

对当前项目来说，最重要的并发认知是：

**不要把 FastAPI 的 `async` 当成 Node.js 式“天然不阻塞”；L3 应以多进程 worker 承载并发，请求路径只做轻量查询和组装。**
