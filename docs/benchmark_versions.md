# 版本性能自动对比

## 已解析模型的页面体验测试（2026-10-15）

已经解析好的项目直接复用工作区与注册库；不要为补测页面重复执行下方 `--mode auto`
导入流程。页面测试使用 `viewer/load-benchmark.html`，与正常页面共用 `ThreeViewport.vue`。
它只列出 `bench_` 开头且几何 ready 的测试项目，加载与变形使用 GET，拾取使用现有 POST 查询。
不会创建项目或提交解析任务。
测试按钮自动串联读取模型信息、特征边、完整表面、辅助几何；正常 Viewer 中对应的手动
操作间隔不计入等待。计时结束后刷新页面再开始下一次，避免上一轮画面影响观察。

启动专用 API 时，将 `APP_DATA_ROOT`、`APP_REGISTRY_DB_PATH` 指向现有测试数据，
设 `APP_EMBEDDED_RUNNER=0`、`APP_PRELOAD_PROJECTS=0`、`APP_WARMUP=0`，
确认注册库没有排队或运行中的任务。不要把业务注册库交给测试任务进程。
前端用 `npm run build` 生成生产页面，通过静态服务器打开 `/odb-viewer/load-benchmark.html`，
填写专用 API 地址。固定窗口、显卡、可见状态；记录重启 API 后第一次及刷新页面后的重复样本，
不要将开发服务器与生产包混作一组。

`outline_presented` 表示轮廓绘制已提交，GPU 完成后再经过一个浏览器帧；
`surface_presented` 对完整表面采用同一口径，是可自动测量的页面显示近似值，
不等于精确的显示器出光时刻。截图核对非空模型、三角面与边数。
`completed` 还等待辅助几何；两者可能不同。
“验证变形和恢复”会选择现有 ready 结果组、最后一帧，检查有限坐标、非零变形、轮廓同步及恢复；
没有合适结果的模型应标为不适用，不能拿空结果证明通过。

HTTP 压缩必须记入条件：原接口脚本用 `Accept-Encoding: identity` 测未压缩数据；
浏览器会请求 gzip，两种计时不能混用。本次增加 `APP_GZIP_COMPRESSLEVEL`（1～9，默认 1），
`APP_ENABLE_GZIP` 开关保持原配置。旧默认是中间件的 9。改变压缩档位后，要验证解压体哈希相同，
同时报告传输字节增加量。跨源资源计时未开放的首字节时间、传输字节记 `null`，不能把 0 当真实测量。

本次页面对比见 `docs/交付/ODB-Viewer页面体验测试-20261015.md`。

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
- `color_legend_<scheme>`：图例接口；`color_legend_entries_<scheme>`：当前 instance 的分类数量统计；`color_legend_entries_all_<scheme>`：整模型的分类统计。五种可用分类方案分别测量；解析 JSON 业务状态，避免把业务错误的 HTTP 200 当作成功。
- `region_mesh_edges_<scheme>`、`region_outline_<scheme>`：指定分类区域的内部单元边线、外轮廓。每种方案从实测的 `legend-entries` 中选面数最多的一个真实区域，使用内部 `legend_key`，并在报告、CSV 中记录区域标签。没有可见区域时明确跳过。
- `Improvement`：`(旧版耗时 - 新版耗时) / 旧版耗时`；正数表示新版更快，负数表示新版更慢。

可在 JSON 的 `benchmark` 中调整新增测量项：

```json
{
  "frame_colors_field": "U",
  "frame_colors_component": "U1",
  "frame_colors_modes": ["smooth", "flat"],
  "include_color_details": true,
  "color_code_schemes": ["etype", "material", "section_type", "section", "section_assignment"]
}
```

`frame_colors_modes=[]` 可跳过云图，`color_code_schemes=[]` 可跳过分类颜色。
`include_color_details=false` 可跳过图例、统计及区域边线/轮廓的扩展测量。
`include_op2=false` 时只测几何、单元边线和可用的分类颜色，不请求云图或模态结果。
`repeat_by_case` 仍可单独控制新增测量项的次数，如 `color_code_section=9`。
两版若可用方案不同，对应报告行会留空，不能据此计算性能提升。

云图和分类颜色缓存的是计算中间数据，接口没有 `X-Cache` 响应头，所以 CSV 中 `cache`
留空不代表没缓存；应结合重复请求的后端处理时间判断。只有一种材料、截面或区域的模型，
也不能代表复杂多区域模型的分类性能。本脚本测耗时，不验证两版结果数值或颜色是否一致。

扩展测量在主分类颜色请求之后执行，所以图例、统计和区域接口可能复用已经建立的分类缓存，
`first` 不代表它们的分类计算处于冷缓存。这样更接近前端已经显示分类颜色后打开图例或区域的操作。
区域接口除耗时外，还检查响应头 `X-Edge-Count`；CSV 新增 `edge_count`、`region`，报告新增
`Edge response checks`。两版边数或区域标签不同，不计算对应性能提升比例；空响应不能作为性能收益。
封闭区域的外轮廓为 0 可能合理，相同边数也不能替代逐坐标核对。

2026-10-13 扩展测试发现：`2a59fe06…` 基线的区域单元边线仍只读旧表面文件，因该版本 L2 已将
边数据拆到独立 `<instance>_edges.h5`，会返回空结果。新版已修复读取路径；涉及空结果的旧版耗时
保留用于诊断，不用于计算提升比例。区域外轮廓用渲染三角形计算，不受这个文件路径问题影响。

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

## 首次打开模型及 ODB 对比

`tools/benchmark_model_open.py` 补测模型信息、全部实例的特征边和前端实际使用的
`render-buffers-chunked`。原基准中的 `model_load` 只读取一个实例的非分块几何，且此前的
项目状态轮询和元信息发现已经把模型索引加载进内存，不能覆盖完整的首次打开开销。

复制 `tools/benchmark_model_open.json.example` 为本机 `.json`，填写两版代码位置、唯一的
非数字测试项目号和 ODB 路径，然后运行：

```powershell
python tools/benchmark_model_open.py --config tools/benchmark_model_open.local.json
```

工具先为每版复制独立的 ODB 副本并比对 SHA-256，再依次导入，各自保留独立的注册库
和数据目录。历史服务的版本转换流程会原地替换输入文件，因此服务只接收测试副本路径，
不接收用户原始文件。副本准备及校验耗时记录为 `odb_input_staging`，不计入导入就绪耗时；
需要的 Abaqus 版本转换仍属于正常导入流程。导入就绪后重启测试服务，以
`APP_PRELOAD_PROJECTS=0`、`APP_WARMUP=0` 启动，让首个模型信息请求承担模型索引的实际
加载开销。之后在同一服务中重复打开，区分首次请求和重复请求。该条件用于定位首次请求
瓶颈，不等于默认开启启动预加载和后台预热时的所有使用场景。

若复用第一轮 BDF/OP2 已解析的数据，去掉 `sources.odb_path`，在每版的 `env` 中指定该轮
`APP_DATA_ROOT`、`APP_REGISTRY_DB_PATH`，并使用原测试项目号。这样不会再次导入模型。
工具不会删除这些数据，后续可继续排查或复测。

结果为 `model_open_时间戳.json`，记录各请求耗时、响应字节数、面数/边数以及 SHA-256
校验值；每版保存一次，出错时也保存已有结果。相同版本的重复打开必须返回相同内容，
全部实例均未返回表面面片时直接报错，避免把空模型列为提速。跨版本校验值不同时需要
继续核查，不能只凭耗时认定优化正确。下载计时包含客户端流式校验，额外记录
`sha256_ms`，便于识别校验本身的开销。

`open_outline_http_total` 从元信息请求开始，止于全部特征边下载完成；
`open_surface_http_total` 再加上全部分块表面几何下载。请求串行执行，便于分离后端开销；
浏览器可能并发请求，因此这些总时间**不是浏览器实际显示时间**，也不包含 JavaScript
解析、显卡上传、绘制以及梁/点/耦合等辅助几何的加载。操作系统文件缓存不清空。

`include_endpoint_suite=true` 可在打开测试之后继续运行原接口基准。ODB 已有结果组，
此处 `include_op2=true` 仅表示原基准启用结果读取，不会追加导入 OP2。
`suite_instance=largest_surface` 选择表面面片最多的实例；`frame=last` 使用所选载荷步的
最后一帧，避开静力初始零帧。默认跳过非 FREQUENCY/BUCKLE 步的模态振型与谐波动画，
仍测变形与云图。也可通过 `exclude_cases` 显式排除不适用的主测试项；分类明细不受此项影响。
