# 版本性能自动对比

`tools/benchmark_versions.py` 可以在不操作前端的情况下，依次测试旧版和新版。
自动模式会完成以下流程：

1. 为旧提交创建独立 Git worktree，不切换当前工作区。
2. 启动旧版 API 和任务进程，删除同名 benchmark 项目，提交 BDF，再提交 OP2。
3. 等待解析完成，记录导入阶段耗时，并测量模型和结果接口的首次及重复请求耗时。
4. 停止旧版进程，再用同一端口启动当前工作区的新版，重复相同测试。
5. 输出原始 CSV、Markdown 汇总和两套服务日志。

## 50 万节点模型

仓库内的 50 万节点配置已经写好源文件路径，可以直接运行：

```powershell
python tools\benchmark_versions.py --config tools\benchmark_versions.50w.json.example --mode auto
```

需要调整路径或参数时，可以先复制为 `.json` 再修改；普通 `.json` 配置已被
`.gitignore` 忽略，不会误提交本机路径。

报告写入 `benchmark_results/`。测试专用项目 ID 是 `bench_50w_old` 和
`bench_50w_new`；脚本只会删除配置中明确写出的项目，不会清理其他项目。

该 BDF 大约 55 MiB，小于新版快速解析默认的 100 MiB 自动启用门槛，所以示例仅对
新版设置 `APP_FAST_BDF_MIN_BYTES=1`。这样新版一定会尝试本次新增的快速解析；如果模型含有
快速路径不支持的卡片，仍会自动回退到原解析器，不会为了跑性能测试牺牲可用性。旧版不认识
这个环境变量，会继续走原来的解析流程。

旧版 worktree 默认创建在当前仓库的同级目录 `VirtualReal702-benchmark-old`。脚本会复用
已有且提交一致、没有本地修改的 worktree，但不会自动删除它。解析产生的临时数据放在
`D:/WorkSpace/Temp/VirtualReal702-benchmark/runs/<运行编号>`。每次运行使用独立注册库，避免
上一次意外中断留下的任务干扰本轮；每轮结束会通过项目 API 删除本轮的大体积模型数据，
只留下很小的注册库和日志用于排查。

## 输出指标

- `bdf_import_total`：从提交 BDF 到几何项目 ready 的总耗时，包含 BDF 解析和 L2 预处理。
- `op2_import_total`：从提交 OP2 到结果组 ready 的总耗时。
- `log_l1_bdf`、`log_l2_ingest`、`log_rg_op2`：由任务日志估算的各处理阶段耗时，日志时间戳精度为一秒。
- `metadata`、`model_load`、`modal_shape`、`deformed_positions`、`modal_animation`：前端实际使用的读取接口耗时。
- `Improvement`：`(旧版耗时 - 新版耗时) / 旧版耗时`；正数表示新版更快，负数表示新版更慢。

报告把接口耗时分成两张表：

- **HTTP 总时间**：包含后端处理、回环网络传输和客户端下载完整响应体，使用每个版本独立的持久连接；适合衡量前端实际等待时间。
- **Backend processing time**：读取响应头 `X-Elapsed-Time-Ms`，只反映服务端处理；对于只有几百字节的 `metadata`，这个指标比 HTTP 总时间稳定。

自动模式每次都会重新启动被测服务，因此接口的 `first` 是脚本在该版本本轮测量阶段观察到的首次请求；`repeat median` 是除首次请求外的中位数，更接近日常重复操作的响应速度。示例配置通过 `repeat_by_case.metadata=21` 单独增加小响应采样次数，不会让几十 MiB 的模型接口也重复 21 次。低于 10 ms 的 HTTP 数值很容易受 Windows 线程调度影响，不应根据单次请求计算出的百分比判断代码回归。

两版顺序执行可以避免同时争抢 CPU、内存和磁盘，但后运行的新版仍可能受益于 Windows 的
文件缓存。日常判断可直接使用本报告；如果结果要用于正式对外结论，建议重启电脑后再跑一轮，
或交换新旧顺序复测，确认导入提升不是缓存造成的。
