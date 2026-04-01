# L3 Module Boundary

## 目标

本文档定义 L3 服务内部三层模块边界：

- `router`
- `service`
- `repo`

目标是让 L3 的核心逻辑不绑死在具体 HTTP 框架上。

这样后面无论：

- 用 FastAPI
- 改 Flask
- 接已有 Flask 服务

都能复用同一套内部逻辑结构。

---

## 1. 总原则

一句话原则：

- `router` 负责接接口
- `service` 负责业务逻辑
- `repo` 负责拿数据

更准确一点：

- `router` 处理 HTTP 协议层
- `service` 处理业务语义层
- `repo` 处理存储访问层

---

## 2. Router 层

### 2.1 Router 负责什么

- 定义 URL 和 HTTP 方法
- 接收 query/path/body 参数
- 做基础参数校验
- 调用 service
- 把 service 结果转成 HTTP 响应
- 把异常转成统一错误响应

### 2.2 Router 允许做的校验

Router 只做“协议层”和“基础格式层”的校验。

例如：

- 必填参数是否存在
- 参数类型是否正确
- 枚举值是否在允许范围内
- body 结构是否符合 schema

### 2.3 Router 不应该做什么

- 不做 HDF5/SQLite 读取
- 不做业务规则判断
- 不做结果映射
- 不做云图/变形组装
- 不做大数组拼装
- 不直接写业务 SQL

### 2.4 Router 的典型样子

可以理解成：

```text
收请求 -> 基础校验 -> 调 service -> 返回响应
```

不应该变成：

```text
收请求 -> 打开文件 -> 查数据库 -> 拼业务结果 -> 返回
```

---

## 3. Service 层

### 3.1 Service 负责什么

Service 是 L3 的核心层。

它负责：

- 业务语义判断
- 调度 repo 获取数据
- 组合多个 repo 返回结果
- 做快速匹配和映射
- 生成前端可用的最终返回对象或二进制 payload

### 3.2 Service 应负责的判断

例如：

- ODB 是否存在
- ODB 是否 ready
- 当前 instance 是否可渲染
- `frame_idx` 是否越界
- 该 field 在当前 instance 上是否存在
- 当前模式是 smooth/flat/attribute 时该走哪条逻辑

### 3.3 Service 应负责的逻辑

例如：

- `render_face_idx -> source_elem_row`
- `source_elem_row -> elem_label`
- 结果场映射到壳子
- 组装 `RenderPayload`
- pick / hover / bbox 结果组装

### 3.4 Service 不应该做什么

- 不直接关心 HTTP request/response 细节
- 不自己拼 Flask/FastAPI 响应对象
- 不把数据库/HDF5 访问细节写死在逻辑里

### 3.5 Service 的典型样子

可以理解成：

```text
接业务参数 -> 判定业务语义 -> 调 repo -> 拼结果
```

---

## 4. Repo 层

### 4.1 Repo 是什么

Repo 是“数据访问层”。

它的职责很朴素：

**负责从存储里把数据拿出来。**

### 4.2 Repo 负责什么

对当前项目，repo 主要负责访问：

- `manifest.db`
- `registry.db`
- `l1/*.h5`
- `l2/*.h5`
- 文件系统路径

### 4.3 Repo 典型职责

例如：

- 根据 `odb_id` 找 workspace 路径
- 从 `manifest.db` 查 result block 路径
- 从 `geometry/<instance>.h5` 读取 labels/coords
- 从 `render/<instance>_render.h5` 读取 `source_elem_row`
- 从结果文件读取指定 frame 的数据切片

### 4.4 Repo 不应该做什么

- 不做业务规则决策
- 不决定当前应该返回 smooth 还是 flat
- 不做完整 payload 组装
- 不定义错误状态码
- 不做 HTTP 相关处理

### 4.5 Repo 的典型样子

可以理解成：

```text
给定定位条件 -> 读取原始数据 -> 返回基础结果
```

而不是：

```text
给定定位条件 -> 直接做完整业务判断 -> 返回最终接口响应
```

---

## 5. 一个典型例子：Pick 查询

### 5.1 Router 做什么

- 收到：
  - `odb_id`
  - `instance`
  - `render_face_idx`
  - 可选 `step/frame/field`
- 基础校验
- 调 `query_service.pick(...)`

### 5.2 Service 做什么

- 判断 ODB 是否 ready
- 判断 instance 是否存在
- 用 `render_face_idx` 做业务映射
- 如果需要结果值，再决定继续查结果文件
- 组装最终返回 JSON

### 5.3 Repo 做什么

- 读取 L2 的 `source_elem_row`
- 读取 L2 的 `source_node_rows`
- 读取 L1 的 `elem_label`
- 读取结果文件中该 frame 的值

---

## 6. 一个典型例子：Render State

### 6.1 Router 做什么

- 收到：
  - `instance`
  - `step`
  - `frame_idx`
  - `field`
  - `mode`
  - `set`
  - `deform_scale`
- 基础参数校验
- 调 `render_service.get_render_state(...)`

### 6.2 Service 做什么

- 判断当前请求组合是否合法
- 决定走 smooth / flat / attribute 哪条逻辑
- 调 repo 读取 geometry / result / set 数据
- 组装 `Binary Envelope`

### 6.3 Repo 做什么

- 读取 chunk geometry
- 读取 `source_elem_row/source_node_rows`
- 读取结果字段对应 frame 数据
- 读取 set 对应 labels 或 render rows

---

## 7. 推荐的代码依赖方向

依赖方向建议固定为：

```text
router -> service -> repo
```

不建议出现：

- `repo -> service`
- `service -> router`
- `router -> repo` 直接跨过 service

### 7.1 为什么不建议 router 直接调 repo

因为一旦这样做：

- 业务规则会散到每个接口里
- 后面很难换框架
- 很难统一错误语义

---

## 8. 错误处理边界

### 8.1 Router

负责：

- 把异常转成 HTTP 响应

### 8.2 Service

负责：

- 抛业务语义异常

例如：

- `NotReadyError`
- `NotFoundError`
- `UnsupportedOperationError`

### 8.3 Repo

负责：

- 抛存储访问异常

例如：

- 文件不存在
- dataset 不存在
- 读取失败

repo 不应该自己决定返回 404 还是 500，这应该交给更上层。

---

## 9. 参数校验边界

### 9.1 Router 做基础校验

例如：

- 类型
- 必填
- schema 结构
- 枚举合法性

### 9.2 Service 做业务校验

例如：

- `frame_idx` 对当前 step 是否有效
- `field` 是否在当前 instance 存在
- `chunk` 和 `partition` 的组合是否允许

---

## 10. 为什么这种拆法适合当前项目

因为你现在面临的现实就是：

- 框架未最终定型
- 可能有现成 Flask 代码
- 未来又想保持工程化和可维护性

如果现在把逻辑都揉进 router：

- 换框架会很痛
- 接别人代码会很乱
- 后面测试和维护都会变差

所以最稳的做法就是：

- 让 router 很薄
- 让 service 承担核心逻辑
- 让 repo 承担数据访问

---

## 11. 最终结论

L3 内部应严格遵守：

- `router`：接口层
- `service`：业务层
- `repo`：数据访问层

对当前项目最关键的一点是：

**不要让 L3 的核心逻辑绑定在 FastAPI 或 Flask 的路由函数里。**

这样后面你无论继续用 FastAPI，还是接入已有 Flask 服务，都有较大的回旋空间。

