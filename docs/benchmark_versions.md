# 版本性能自动对比

`tools/benchmark_versions.py` 可以在不操作前端的情况下，依次测试旧版和新版。
自动模式会完成以下流程：

1. 为旧提交创建独立 Git worktree，不切换当前工作区。
2. 先启动旧版 API，等初始化完成后再启动任务进程，避免任务进程抢先查询尚未创建的项目表。
   删除同名 benchmark 项目，提交 BDF；几何一就绪就提交 OP2，
   与前端的真实操作一致。
3. 等待解析全部完成（包括新版在后台继续跑的 L2 高级索引），记录导入各阶段耗时，再测量
   模型和结果接口的首次及重复请求耗时。
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

## 测试条件

- **项目 ID 不能是纯数字**：自动模式启动的服务只隔离了数据目录和注册库，MySQL 仍然是
  `service_config.json` 里配置的那一个。服务删除项目时会按数字项目号清理 MySQL 里的模型修正
  数据，BDF 导入也会按数字项目号先清空再重写 FEM 表，所以脚本会直接拒绝纯数字的
  `project_id`。示例里的 `bench_50w_old` 不是数字，服务会跳过 MySQL 这一步；这也意味着
  测到的导入时间不包含正式使用时的"BDF 导入 model_update"。
- **默认关闭缓存预热**：新版的服务会在结果就绪后自动在后台预热缓存（提前算好每个 step
  最后一帧的云图和位移）。预热和测量同时进行时，会和接口请求抢资源，还可能让首次请求直接
  用上预热好的缓存，结果随机、不可复现。因此脚本启动服务时默认设置 `APP_WARMUP=0`；旧版
  没有预热功能，会忽略这个变量。报告的输入表会列出每个版本的 `APP_WARMUP`。如果想测"开着
  预热"的效果，在该版本的 `env` 里写 `"APP_WARMUP": "1"`，并在解读首次请求时说明哪些是缓存
  命中。

## 输出指标

- `bdf_import_total`：从提交 BDF 到基础几何 ready（前端已经能看到模型）的耗时。新版两阶段 L2 中，它只包含 BDF 解析和 `l2_core`；旧版包含完整 L2。
- `bdf_full_ready_total`：从提交 BDF 到 L2 高级索引（邻接、边线、Octree）也完成的耗时。旧版不分阶段，等于 `bdf_import_total`。比较新旧版几何处理的总耗时看这一行。
- `op2_queue_wait`：从提交 OP2 到任务进程开始处理它的排队时间。任务进程一次只做一件事，新版在几何 ready 后还要继续跑高级索引，OP2 会排在它后面。这段是等待，不是 OP2 解析变慢，所以报告不计算它的提升百分比。
- `op2_parse`：从任务进程开始处理 OP2 到结果组 ready 的耗时，反映 OP2 解析本身。
- `op2_import_total`：从提交 OP2 到结果组 ready 的总耗时，等于排队加解析，也就是用户提交 OP2 后实际等待的时间。
- `end_to_end_total`：从提交 BDF 到 OP2 结果组 ready 的端到端总耗时。
- 以上导入耗时都靠轮询状态得到，精度约等于 `poll_seconds`（默认 1 秒）。如果 OP2 在两次轮询之间就处理完，无法拆分排队和解析，报告里只有 `op2_import_total`。
- `log_l1_bdf`、`log_l2_core`、`log_l2_advanced`、`log_l2_ingest`、`log_rg_op2`：由任务日志估算的各处理阶段耗时。`l2_ingest` 用于旧版兼容，日志时间戳精度为一秒。
- `metadata`、`model_load`、`modal_shape`、`deformed_positions`、`modal_animation`：前端实际使用的读取接口耗时。
- `element_mesh_edges`：显示全部单元边线的接口耗时；新版缓存完整响应包，命中时 CSV 中的 `cache` 为 `hit`。
- `frame_colors_smooth`、`frame_colors_flat`：云图的平滑和分片两条路径，默认测 `field=U`、`component=U1`、第 0 帧。分别记录首次和重复请求，比较云图中间数据缓存的收益。
- `color_code_etype`、`color_code_material`、`color_code_section_type`、`color_code_section`、`color_code_section_assignment`：按单元类型、材料、截面类型、平均域、截面指派上色。脚本先读取可用方案，只测模型支持的方案；缺失的方案会打印跳过原因并写入报告的 `Coloring coverage`。`elset` 需要指定单元集，不在这轮自动分类测试中。
- `Improvement`：`(旧版耗时 - 新版耗时) / 旧版耗时`；正数表示新版更快，负数表示新版更慢。

可在 JSON 的 `benchmark` 中调整新增测量项：

```json
{
  "frame_colors_field": "U",
  "frame_colors_component": "U1",
  "frame_colors_modes": ["smooth", "flat"],
  "color_code_schemes": ["etype", "material", "section_type", "section", "section_assignment"]
}
```

`frame_colors_modes=[]` 可跳过云图，`color_code_schemes=[]` 可跳过分类颜色。
`include_op2=false` 时只测几何、单元边线和可用的分类颜色，不请求云图或模态结果。
`repeat_by_case` 仍可单独控制新增测量项的次数，如 `color_code_section=9`。
两版若可用方案不同，对应报告行会留空，不能据此计算性能提升。

云图和分类颜色缓存的是计算中间数据，接口没有 `X-Cache` 响应头，所以 CSV 中 `cache`
留空不代表没缓存；应结合重复请求的后端处理时间判断。只有一种材料、截面或区域的模型，
也不能代表复杂多区域模型的分类性能。本脚本测耗时，不验证两版结果数值或颜色是否一致。

报告把接口耗时分成两张表：

- **HTTP 总时间**：包含后端处理、回环网络传输和客户端下载完整响应体，使用每个版本独立的持久连接；适合衡量前端实际等待时间。
- **Backend processing time**：读取响应头 `X-Elapsed-Time-Ms`，只反映服务端处理；对于只有几百字节的 `metadata`，这个指标比 HTTP 总时间稳定。

自动模式每次都会重新启动被测服务，因此接口的 `first` 是脚本在该版本本轮测量阶段观察到的首次请求；`repeat median` 是除首次请求外的中位数，更接近日常重复操作的响应速度。示例配置通过 `repeat_by_case.metadata=21` 单独增加小响应采样次数，不会让几十 MiB 的模型接口也重复 21 次。低于 10 ms 的 HTTP 数值很容易受 Windows 线程调度影响，不应根据单次请求计算出的百分比判断代码回归。

`first` 只有一次采样，结论要结合 `repeat median` 看。另外，带统一结果缓存的新版里，
`modal_shape`、`deformed_positions`、`modal_animation` 共用同一帧的顶点位移；脚本按这个
顺序测，所以后两项的 `first` 可能已经用上了 `modal_shape` 算好的位移，不是完全冷启动。
更早的基线可能已有独立的位移或响应包缓存，具体要看所选提交，不能把“旧版”一概视为无缓存。
新增云图在原有变形/动画测量之后执行；平滑和分片云图使用不同缓存键，分类颜色逐 scheme 测量。

如果专门比较本次统一缓存改造，可将旧版设为 `2a59fe06b4314e3259649261ab8adec7bd6be273`。
它已有早期内存/磁盘缓存，但尚未合并统一缓存改造；并且已经支持快速 BDF 解析和两阶段 L2，
此时应给两版相同的 `APP_FAST_BDF_MIN_BYTES` 设置。示例中的 `9139bdd…` 则是更早的基线，
用于比较更广范围的解析和预处理优化。

两版顺序执行可以避免同时争抢 CPU、内存和磁盘，但后运行的新版仍可能受益于 Windows 的
文件缓存。日常判断可直接使用本报告；如果结果要用于正式对外结论，建议重启电脑后再跑一轮，
或交换新旧顺序复测，确认导入提升不是缓存造成的。
