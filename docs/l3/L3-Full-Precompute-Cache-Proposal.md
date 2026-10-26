# L3 全量预计算与缓存覆盖方案（待审阅）

更新时间：2026-10-21

代码基线：`188d459`（新增 frame-scalars 缓存来源日志）

状态：Claude 已审阅（2026-10-21，见 §0）；ready 语义已拍板；**第一阶段已实施**（§0.6）。

## 0. 审阅结论与已拍板决策（2026-10-21）

### 0.1 ready 语义：已拍板为“全量算完才 ready”

领导要求：模型显示类接口能缓存的都缓存，**必须等全量计划的必需项全部计算、落盘并核验通过后才 ready**。因此：

- §1 第 4 点、§7 中“待确认的新语义”改为**已确定**；不采用“先 ready、后台补算”方案。
- 导入到 ready 的时间会明显增加，实施第 1 步必须用真实项目（至少 `2022610152141` 的 200 万节点模型）
  估算全量条目数、磁盘体积和预计算耗时，提前同步给领导和使用方。
- 旧项目仍按 §9 处理：不批量改动历史 ready，补算完成前在内部账本标记为“历史 ready，当前计划未完成”。

### 0.2 默认分量 √2 合量 bug：已在实际数据上确认并修复

§4.3 担心的“默认分量与 USUM 是否等价”经核对是**确定的数值错误**，不只是缓存键问题：

- `src/l1/op2_pack.py` 为 OP2 位移写入 `[U1, U2, U3, USUM]` 4 列；
- `_extract_component(component_idx=None)` 原先对全部列求模长，得到 √(U1²+U2²+U3²+USUM²) = √2·|U|。
- 在开发者机器上实测（项目 `2022610152141`、结果组 A、`200W-NODES-CYLINDER`、`SUBCASE_1`、第 9 帧、U 场）
  `frame-scalar-range`：省略分量 max = 1.7262e-4，`component_idx=3`（USUM）max = 1.2206e-4，比值 1.4142。
- ODB 的 U 只有 3 列，不受影响。

已修复（在全量预计算之前单独提交，避免把错值批量写入缓存）：

- `result_service.stored_magnitude_col` 读取 `meta/components`，求模长前去掉自带的 USUM 列；
  覆盖 frame-scalars / frame-scalar-range（NODAL）、`query/pick` 的 NODAL 取值、Simright 的 NODAL 合量查询。
- 带 USUM 列的文件在默认分量下缓存键改为 `"mag-excl-USUM"`，修复前的错误缓存不再命中；其他文件的缓存键不变。
- 位移向量相关接口（vertex-displacements、deformed-*、modal-*）本来只取前 3 列，不受影响。

修复后，带 USUM 列文件的默认分量与 `component_idx=3` 数值等价，但缓存键仍然分开；全量计划可以把二者视为同一组合，只存一份。

### 0.3 其他审阅意见（实施时落实）

1. **规模先实测再定计划**：条目数 = 实例 × 字段 × 步 × 帧 × 分量 × smooth/flat，再乘集合等维度；模态项目帧数多、应力分量多，
   很可能是上万项、远超 100 GB。§11 第 1 步要输出实测总量。
2. **按“实例 × 字段”整块读取**：现有 `prepare_workspace` 逐帧调用，每次都重新打开 HDF5 只读一帧；
   全量计算应一次读出所有帧，在内存中切分后逐项写盘，否则 I/O 会成倍放大。
3. **容量策略**：`cache_store` 的 100 GB 按 mtime 淘汰，以及严格准备时“单项超过容量直接报错”（`cache_store._fits`），
   在不限容量模式下都需要调整；`APP_SCALAR_DISK_CACHE_MB=0` 的含义（关闭磁盘层）保持不变。
4. `frame-colors` 路由的 `component` 只接受 U1/U2/U3（`Component` Literal），USUM 实际传不进来；
   覆盖矩阵按这 3 个分量枚举即可。


### 0.4 相邻分析步交接帧：实测记录（不纳入本次优化）

疑问：“每个 step 的最后一帧与下一个 step 的第 0 帧是否相同？”结论：**只对部分物理量成立，不能作为通用规则**。

实测（2026-10-21，开发者机器项目 `202610091649`，5 个连续 DYNAMIC 步、各 11 帧，实例 DAO-1 + PART-1-1；
对比各实例 `frame-scalar-range` 的最小/最大值，Step-1→2、2→3、4→5 三对）：

| 字段 | 结果 |
|---|---|
| U 位移、S 应力 | 3 对均相同 |
| V 速度、A 加速度、RF 支反力 | 3 对均不同 |
| CPRESS 接触压力 | 2 对不同、1 对相同 |

解释：第 0 帧是新步开始时的状态。位移和应力这类状态量在步间连续；新步开始时 Abaqus 换上本步的载荷、边界条件和接触设置并重新求平衡，
速度、加速度、反力、接触力随之改变。Nastran SUBCASE 是相互独立的工况，FREQUENCY 步的帧是模态阶次，都不适用该规律。
注意：最值相同不代表逐节点相同；最值不同则可以确定存在差异。

本次全量预计算的处理：**第 0 帧照常单独计算，不能用上一步最后一帧代替**。

后续可选优化（暂不做）：预计算时比对原始数据内容（如对 L1 帧数组求哈希），只有逐字节相同的帧才共用缓存条目，从而省下 U/S 等字段的重复计算和存储；
不依赖“交接帧必然相同”的假设。

### 0.5 全量规模实测估算（2026-10-21，项目 `202610211132`）

模型：Job-7.odb，实例 PART-1-1（684,204 节点 / 644,006 单元）+ DAO-1（10,944 节点 / 53,094 单元）；
5 个 DYNAMIC 步 × 11 帧 = 55 帧；每步 37 个字段（NODAL 类 15 个，如 U/V/A/RF/CPRESS；ELEMENT_NODAL 类 22 个，如 S/LE/PE/S_MISES）。
L1 导出单步约 37 分钟（日志 11:33 开始），按 5 步推算整个导入约 3～4 小时（未取到准确的 ready 时刻）。

**只算 frame-scalars 一个接口**的组合数：每个字段的选项 = 各分量 + 默认（合量/标量本身），每步 81 个选项；
× 55 帧 × smooth/flat × 2 实例 = **17,820 项**（其中 PART-1-1 8,910 项）。

单项服务端耗时（取响应头 `x-elapsed-time-ms`，不含网络传输；首次 = miss，再次 = 命中）：

| 类型 | 首次计算 | 命中 |
|---|---|---|
| NODAL（U/V/A/CPRESS 等），smooth 或 flat | 0.2～0.3 s | 20～30 ms |
| ELEMENT_NODAL smooth（S/LE/PEEQ/HFL/TEMP/S_MISES） | 1.5～2.3 s | 25～35 ms |
| ELEMENT_NODAL flat | 0.2～0.7 s | 25～60 ms |
| DAO-1（小实例） | 0.03～0.07 s | 约 20 ms |

估算（单线程、逐项调用现有函数，未做整块读取优化）：

- **耗时**：PART-1-1 约 2.0 小时，DAO-1 约 0.1 小时，合计 **约 2 小时**，相当于在 3～4 小时的导入之后再加 50%～65%。
  其中 ELEMENT_NODAL smooth 约占 80%（条件平均计算），NODAL 部分大多是读盘时间，按 §0.3 第 2 点整块读取后还能缩短。
- **磁盘**：PART-1-1 单项约 1.27 MB（float32 每顶点一个值），DAO-1 约 0.1 MB；合计 **约 12 GB**，在现有 100 GB 上限之内。

未计入（量级更小或另需测量）：frame-scalar-range（ELEMENT_NODAL smooth 的范围要单独计算，可能再加数十分钟）、
55 帧 × 2 实例的顶点位移与 deform-suggest-scale、几何类（color-code、单元边线）、集合维度、frame-colors（U1/U2/U3）。
STATUS（WHOLE_ELEMENT）再次请求仍需约 190 ms：已查明是 L3 不认 WHOLE_ELEMENT 位置、每次都返回空 payload，空结果不进缓存；2026-10-22 已支持（见 §0.8）。

结论：这一规模下“全量算完才 ready”可行，代价主要是导入时间增加约一半，磁盘不是瓶颈。
模态项目（帧数 = 模态阶数）和更大网格的项目需要按同一方法另测。

### 0.6 第一阶段实施记录（2026-10-21）

用户补充：`viewer/` 只是 Demo 前端，不能据此判断真实前端用哪些接口，所以 frame-colors 也全量覆盖。
导入用时补充：项目 `202610211132` 从 11:44 到 15:21 完成（3 小时 37 分），其中缓存准备是旧的首屏范围。

已实现：

- `src/l3/services/precompute_plan.py`：从 manifest 枚举计划（result_files 的 components、result_blocks 的实例、frames 表的真实帧编号），
  按 (目标, 实例, 帧) 顺序执行：range（默认 + 各分量）、frame-scalars（默认 + 各分量 × smooth/flat）、frame-colors（U1/U2/U3 × smooth/flat）、
  U 场每帧顶点位移与 deform-suggest-scale。无表面实例只算位移；NotFoundError 记为跳过；strict 模式下其他异常直接失败。
  `PLAN_VERSION` 写进后台预热完成标记，覆盖范围升级后旧项目会重新补算。
- `readiness_service.prepare_workspace`（新导入/追加结果/L2 重跑的 ready 屏障）改为严格执行全量计划，进度约每 30 秒写一条
  `cache_prepare` 项目日志（完成数/总数/预计剩余时间）。
- `warmup_service`（旧项目补算/手动预热）改为执行同一计划，每项计算前检查是否给解析让路。
- `cache_store`：`APP_SCALAR_DISK_CACHE_MB=-1` 表示不限容量；严格准备期间不做逐次写入后的目录扫描（结束时统一淘汰并核验），
  普通写入后的扫描改为每工作区最多 30 秒一次，避免上万条目时的平方级开销。

本项目按 §0.5 方法加上 frame-colors（单项 0.2～0.75 s，12,210 项）重新估算：全量准备约 **4 小时**，磁盘约 **25～30 GB**。

未实施（后续阶段）：整块读取所有帧以减少 I/O（§0.3 第 2 点）、任务账本、已有集合与分类区域、几何类最终响应、raw-values/元数据等其他接口族、
按内容去重（§0.4）、多进程并行。

### 0.7 耗时剖析与优化计划（2026-10-21，`tools/profile_precompute.py`）

开发者机器（8 物理核 / 16 逻辑核，32 GB），项目 `202610211132`，实例 PART-1-1（158,804 个表面三角形），S 场第 5 帧，缓存全部关闭：

- 单元量 smooth 每次约 2.25 s，其中约 1.75 s 是 `_compute_en_global_range`（全模型平均后的图例范围），表面平均本身约 0.47 s。
- 这个范围被 frame-scalars smooth、flat 和 frame-scalar-range 三处调用，结果相同。平时有内存缓存只算一次；但严格准备
  （`require_persistent_cache`）期间 `small_get` 总是不命中，**同一帧同一分量算 3 遍**。S 一帧约 43 s，其中约 36 s 是重复的范围计算。
- 范围函数每次重读网格连接与区域编号、重新做两次 stable argsort，另有对约 470 万个节点逐个 `dict.get` 的 Python 循环（1.4 s / 7 次）。
- 读取：一帧 S 118 MB，读 1 次 0.24 s，按 7 个分量各读 1 次 1.47 s；范围函数和平均函数还各读一次。全部 11 帧 1.3 GB 一次读完 2.48 s。
- 多进程（每进程约 0.8 GB）：2/4/8 进程分别加速 1.7/2.8/4.0 倍（测试时机器已有 26% 占用）。

优化顺序（每步都不改算法，只改计算方式）：

1. 同一帧同一分量的全模型范围只算一次，三处共用（严格模式下中间结果允许进程内复用，最终条目仍须落盘核验）；`dict.get` 循环改为数组运算。
2. 每个实例预建只依赖网格的排序/分组索引（范围与表面平均共用），每帧只读一次、各分量共用。
3. 计划按“字段 × step”分给多个进程并行，进程数按 CPU 核数与可用内存确定。

**第 1 步已实施**（2026-10-21）：`cache_store.small_get(..., intermediate=True)` 让严格准备期间全模型平均范围在进程内复用
（缓存关闭时仍每次现算）；区域范围查表改为 `searchsorted`，单元测试 `tests/test_en_range_optimization.py` 对照旧实现逐位一致。

**第 2 步已实施**（2026-10-21，范围计算部分）：
- `_compute_en_global_range_uncached` 的网格部分（区域编号、含中节点的连接）按 (几何文件签名, 高阶文件签名, domain_version) 缓存；
  两次稳定排序的下标按“参与的单元类型及 N/n_shared”布局缓存，每次只按本帧 NaN 掩码过滤。稳定排序保证“先过滤再排序”与
  “先排序再过滤”序列相同，结果逐位一致。各缓存 2 项 LRU（大模型约数百 MB/进程）。
- `_frame_slice`：原始结果帧按 (文件, 签名, dataset, 帧) 进程内复用（上限 256 MB，只读数组），frame-scalars、范围、frame-colors 共用。
- 表面平均 `_en_per_vertex_averaged` 用的是不稳定排序，预排序会改变组内求和顺序、可能动到 float32 最后一位，**未改动**。
- 假设：网格连接里的节点行号非负（L1 由 searchsorted 生成）。新实现按 (区域, 节点) 成对比较分组，旧实现按复合键分组，两者在此假设下等价。

**第 3 步已实施**（2026-10-21）：严格准备按目标（结果组 × step × 字段）分给多个 worker 进程（`precompute_plan.run_plan_parallel`，
worker 为 `python -m src.l3.services.precompute_worker`，不用 multiprocessing，避免 Windows spawn 在子进程里重跑 app.py）。
先起 1 个 worker，完成第一个目标后按其内存峰值与当前可用内存的 70% 决定总数（`APP_PRECOMPUTE_WORKERS`：0 = 自动，最多 8 且不超过物理核数；
1 = 本进程串行；没有 psutil 时自动模式最多 4 个）。worker 只收集落盘条目，父进程合并后统一淘汰与核验；任一目标失败或 worker 退出即整体失败。
后台预热（旧项目补算）仍在 Web 进程内单线程执行。`tools/snapshot_results.py --use-disk-cache` 可直接核对导入时写入的缓存内容。

**防止改动计算结果**：优化前的代码打 tag `precompute-opt-baseline`。用 `tools/snapshot_results.py` 先在真实模型上
生成基准快照（抽样：首末 step × 第 0/中间/最后帧 × 全部字段、分量、模式、实例），每完成一步优化就用同一批组合比对，
要求逐字节一致（或 NaN 位置一致且误差为 0），并在单元测试里对比新旧实现。

### 0.8 当前进度与接续（2026-10-21 收尾）

已完成并推送：USUM √2 修复（`e8bfdb9`，已在开发者机器验证）→ 全量计划（`41e32e6`）→ 基准 tag `precompute-opt-baseline`（`fccb74d`）
→ 优化第 1 步（`0cc0083`）、第 2 步（`e669422`）、第 3 步（`7e9dc81`）。**优化后的代码尚未在开发者机器上运行过。**

开发者正在生成基准快照 `baseline.npz`（项目改为 `202610211530`，1440w 自由度、241 万节点、1 step × 2 帧、38 个字段）。
快照工具已改为：基准代码放在 `git worktree add ..\702-baseline precompute-opt-baseline`，用 `--code-root ..\702-baseline`
导入，主目录不切版本（避免正在运行的服务/解析读到混合版本）；默认 `--cache memory`（只关磁盘层，范围不重复算、不写工作区）；
`--procs N` 多进程。接续时按顺序：

1. 确认 `baseline.npz` 已生成（必须用 tag 版本生成）。
2. `git checkout main && git pull`，跑 `tests/test_en_range_optimization.py`、`test_precompute_parallel.py`、`test_precompute_plan.py`、
   `test_pipeline_readiness.py`、`test_l3_disk_cache_warmup.py`、`test_l3_warmup_scheduling.py`、`test_l3_deform_cache.py`、
   `test_stored_magnitude_col.py`，再跑全量 `tests/`（conftest 新增了 autouse 清理）。
3. `python tools/snapshot_results.py --workspace <202610211132 工作区> --compare baseline.npz`：“不一致”必须为 0。
4. 重跑 `tools/profile_precompute.py` 对比 §0.7 的耗时（注意它关闭缓存，看不到第 1 步效果）。
5. 重启服务后导入一个小模型：看 `cache_prepare` 进度日志与进程数、ready 后切帧/分量耗时；对与基准相同的项目用
   `snapshot_results.py --use-disk-cache --compare baseline.npz` 核对多进程写入的缓存内容。
6. 用实测更新 §0.5/§0.7 的耗时估算；之后估算 1440 万自由度（约 3 GB）模型的规模。

**验证记录（2026-10-21）**：项目 `202610211530`（1440w 自由度，1 step × 2 帧，38 个字段），832 项抽样
（frame-scalars / range / frame-colors / 位移 / 变形统计）。基准 `fccb74d` 与新代码 `ce3b37a`（含优化第 1～3 步）分别生成快照，
`--diff` 结果 **完全一致 832，数值等价 0，不一致 0**。快照走的是进程内计算路径（`--cache memory`），覆盖第 1、2 步；
第 3 步多进程严格准备的落盘内容尚待重启服务导入后用 `--cache disk` 核对。

**多进程实测（2026-10-22，项目 `test1` = door.odb）**：壳模型，PART-1-1 37,828 节点 / 33,986 单元（65,481 个表面三角形），
2 个 step（eigenfrequency、sag），每步约 24 个字段。计划 7533 项（range 1155、scalars 2310、colors 4014、位移 27、变形统计 27），
**全部完成、0 跳过、0 失败，8 个 worker，127.7 s**。日志：前约 50 s 只有 1 个进程（先跑完第一个目标测内存峰值），
之后扩到 8 个，剩余约 7000 项用了约 80 s。ready 后首次请求（内存冷、磁盘命中）frame-scalars 约 40～80 ms。
观察：小模型上"先单进程测内存"占了总时长约 40%，可考虑按模型规模预估首批进程数；导入日志另有
`catalog 导入失败（非致命）：invalid literal for int() with base 10: 'test1'`（项目 ID 非数字时模型修正 catalog 跳过），与缓存无关。
多进程写入内容与单进程是否逐字节一致，尚未用 `snapshot_results.py --cache disk --compare` 在 1440w 项目上核对。

**实机验证（2026-10-22，`201b7ce` 之后）**：
- STATUS（`202610211132` PART-1-1）：返回 `WHOLE_ELEMENT_FLAT`、范围 0～1，首次 482 ms、再次 38 ms（修复前每次约 180 ms 且为空 payload）。
- 手动预热让路：对 `202610211132` 手动 `POST /results/warmup` 返回 started → 导入 door.odb（项目 `2026102201`）期间再调返回 `deferred`
  → 控制台 `Warmup[202610211132]: parse task started, yielding (39.2s)` → 导入的严格准备直接拿到锁，7533 项 / 8 进程 / 约 2.2 分钟完成、ready，
  无等锁超时 → ready 后再调返回 `already_running`，预热已由 poll 自动接着补算。

**接口覆盖探测工具（2026-10-22）**：`tools/probe_endpoints.py --project <id> [--full]`，从服务 `/openapi.json` 读全部路由、
通过服务接口发现参数取值，对只读 GET 各请求两遍，按服务端耗时分 ERROR / NOT_CACHED / NOT_PRECOMPUTED / OK，输出 .md 汇总与 .csv 明细。
远程取样（door，`2026102201`，每接口 1 组）初步结果：`color-code/{instance}/schemes` 每次约 150 ms（未缓存）；
`deformed-positions`（默认 scale）首次约 370 ms、`modal-animation`（31 MB）首次约 340～500 ms（最终响应包未预算）；
`raw-values` 带 result_group 一律 404（已修，见接口文档 2026-10-22 第三轮）。完整结果待在开发者本机跑 `--full`。

**探测结果处理（2026-10-22，door `--full` 9405 个请求）**：`query/pick` 壳面内主值类字段 500（EN 块 NaN 写进 JSON）已修；
`schemes` 每次约 110 ms → 按文件签名缓存；`legend-entries`（section）约 300 ms → 材料名按单元类型整列读取；
`raw-values` 404 已修（服务未重启所以探测时仍报错）；`projects/{id}/summary` 404 是 ODB 项目没有 `model_summary.json`，非缓存问题。
`modal-animation` 首次 340～500 ms：领导决定也预算存盘 → 计划版本 2，FREQUENCY 步每阶 × 有表面实例预算
scale=建议值、n_frames=20 的响应包（`precompute_plan._modal_animation`）。磁盘约 n_frames × 顶点数 × 12 字节/阶，
241 万节点模型每阶约数百 MB，阶数多时需关注 `APP_SCALAR_DISK_CACHE_MB`（默认 100 GB）：严格准备时单项超过上限会直接报错，全部条目总量超过上限会在核验时报“全量缓存未完整保留”，导入失败；可设 -1 不限容量。

遗留问题：
- 导入在缓存准备阶段中断（重启/崩溃）会从 L1 整条重跑、L2 清空缓存；准备出错没有单独重试入口。断点续做方案见 `Pipeline-Resume-Design.md`，2026-10-22 决定暂不实施。
- `202610211132` 的变形动画显示“炸开”，开发者说之后再查，与缓存无关、尚未排查。
- ~~STATUS（WHOLE_ELEMENT）字段再次请求仍约 190 ms~~：原因是 L3 的云图/范围/着色只认 NODAL、ELEMENT_NODAL、INTEGRATION_POINT，STATUS 只有 WHOLE_ELEMENT 块，每次都判为无数据、返回空 payload（灰色），空结果不缓存。2026-10-22 起按单元着色（`WHOLE_ELEMENT_FLAT`，与积分点 flat 同一读取函数），结果进缓存、全量计划自动覆盖；含 WHOLE_ELEMENT 块的结果文件缓存键加 `("we", …)`，旧的“无范围”缓存不再命中，其他文件键不变。测试 `tests/test_l3_whole_element_field.py`。
- 仍存在：任何字段在某实例上真的没有数据时（如 CPRESS 在 DAO-1），每次请求仍会重新判定一遍（DAO-1 约 30 ms），未做“无数据”缓存。
- ~~手动 `POST /results/warmup` 不给解析让路，期间追加结果可能等缓存锁超时（300 s）~~：2026-10-22 已修。原因有两层：手动预热带 `force`、跑起来不检查解析；而 `APP_WARMUP=0` 时解析繁忙检测根本没注册。现在繁忙检测始终注册，手动预热开始前繁忙则返回 `deferred`，进行中遇到解析则在当前组合后中止、释放锁，记入待办，由 poll 的 `run_pending`（不再受 `APP_WARMUP` 控制）在解析结束后接着补算。单个组合最长约数十秒，远小于 300 秒。
- ~~重启后旧项目被打开会在 Web 进程内单线程补算全量计划~~：已按开发者要求把 `APP_WARMUP` 默认改为 0（2026-10-21），旧项目需手动 `POST /results/warmup` 或设 `APP_WARMUP=1` 才补算。
- 后续可选：按内容去重相邻 step 交接帧（§0.4）、整块读取所有帧、任务账本、集合/区域/几何类最终响应等其他接口族。

### 0.9 接续清单（2026-10-22 收尾）

2026-10-22 已完成并推送（详见 §0.8 各条记录）：STATUS/WHOLE_ELEMENT 支持（`4ac300b`）、手动预热给解析让路（`201b7ce`，实机验证通过）、
测试修复若干、接口探测工具 `tools/probe_endpoints.py`、raw-values 路径修复（`8cdd3f4`）、pick NaN 500 / schemes 缓存 /
legend-entries 提速（`8703566`）、FREQUENCY 步 modal-animation 预算（`74c02ae`，计划版本 2）、断点续做设计备忘（未实施）。

**待开发者在本机验证（新对话先问结果）**：
1.–3. 已完成（2026-10-22）：单测通过；door 重新导入 ready 正常；`--full` 探测 ERROR 只剩 summary（见下方复测记录）。
4. ~~向前端同事确认第三方前端调 `modal-animation` 的实际 `scale` / `n_frames`~~ 已确认（2026-10-22）：前端传
   `deform-suggest-scale` 返回的建议值 + `n_frames=20`，与预算一致。实测项目 2026102201 eigenfrequency 第 4 阶带
   `scale=0.5442756586073418&n_frames=20` 首次请求 49 ms（命中）。注意前端须原样回传建议值，四舍五入后缓存 key 对不上。
   （此前探测工具不带 scale，测的是默认 1.0 的包，不能说明预算命中。）
5. **进行中（2026-10-22 晚，开发者在跑）**：1440w（`E:\code\odb-service-design\model_v2\1440w-dof_grav_v3.odb`，
   旧项目 `202610211530`）用 `402cb14` 之后的代码（计划已去掉 frame-colors）重新导入，然后
   `python tools/snapshot_results.py --workspace <新工作区> --cache disk --compare baseline.npz --procs 2`。
   预期“不一致 = 0”（STATUS 等整单元字段不一致属预期：基准代码不支持）；快照工具仍比对 frame-colors，disk 模式下现算。
   新对话先要：比对输出全文 + 导入日志最后一条 `cache_prepare`（总项数/跳过/失败/进程数/耗时，总项数比旧版少属预期）。

**2026-10-22 下午探测结果（`probe_2026102201_20261022_153257.md`，阈值 10 ms）**：
- 已验证：pick 不再 500；`schemes` 6 ms；eigenfrequency 步 `modal-animation` 命中（实测约 20 ms，包 31.8 MB）。
- 阈值 10 ms 过严：缓存命中的大响应（frame-scalars、modal-animation 等）服务端耗时本身就有 20–60 ms，被误判为 NOT_CACHED；
  判断是否进缓存建议仍用 100 ms，或看同一请求两次耗时是否接近。
- `raw-values` 432 个 500：8 个面内/面外主值字段（`*_INPLANE_*`、`*_OUTOFPLANE_*`）在 `format=json` 下含 NaN，JSONResponse 拒绝序列化；
  已改为 NaN→null（`format=l3be` 本来就正常）。raw-values 每次读 L1，不在缓存计划内（约 10–550 ms，随数据量）。
- `region-mesh-edges`（etype 方案大区域约 110 ms）改为整实例边表去重后进内存缓存；`legend-entries`（section 约 100 ms）
  表面单元计数与默认标题按几何记忆化。两者都是每进程首次计算一次，未纳入磁盘预算。

**2026-10-22 傍晚复测（`probe_2026102201_20261022_164353.md`，阈值 100 ms，`c9e32ca` 后）**：单测通过；ERROR 只剩 summary（预期）；
region-mesh-edges、legend-entries、modal-animation、pick 全部 OK。剩余：
- 4 个接口各有 1 个 NOT_PRECOMPUTED（color-code、deformed-normals、frame-colors、全实例 legend-entries），是每个进程首个请求的冷启动（加载索引/记忆化），其余同类请求都命中。
- `raw-values` 432 个 NOT_CACHED：大字段（如 STH ELEMENT_NODAL，JSON 2.5 MB）`format=json` 约 325 ms，其中主要是 JSON 序列化，
  `format=l3be` 同一请求 40 ms。raw-values 是计算类接口（viewer 不调用），按全量预算需复制全部 L1 数据（JSON 还会膨胀约 4 倍），暂未纳入，待定。

**决定（2026-10-22）：`raw-values` 不缓存、不纳入全量预算。** 理由：计算类接口（viewer 不调用），不属于“模型显示类接口”；
全量预算等于把 L1 原始结果整份复制（JSON 再膨胀约 4 倍）；慢主要在 JSON 序列化（STH ELEMENT_NODAL json 约 325 ms，
`format=l3be` 40 ms），调用方要快可用 l3be。探测报告里 raw-values 的 NOT_CACHED 属预期。

**决定（2026-10-22）：全量缓存只对 viewer 实际调用的接口负责。** 清单以 `viewer/src` 为准，写在
`tools/probe_endpoints.py` 的 `VIEWER_ROUTES`（探测工具默认只测这些，`--all-routes` 测全部）。据此：
- `frame-colors` 移出全量计划：viewer 只定义了 `fetchFieldColors`、从未调用（云图走 frame-scalars + 色带贴图），
  它却占计划条目一半以上（test1：4014 / 7533，每字段每帧 U1-3 × smooth/flat 6 项）。接口保留，按需现算并缓存。
  范围缩小，`PLAN_VERSION` 不变（旧项目完成标记仍有效）。
- 探测工具请求 `modal-animation` 时与前端一样带 `deform-suggest-scale` 建议值 + `n_frames=20`（此前不带 scale，测的是默认 1.0 的包）。
- viewer 调用、但计划里只做中间层的：`deformed-positions`（scale 任意，缓存位移向量）、`modal-shape`、`pick`、
  `section-mesh`（截面位置任意）、区域边线/轮廓，现有速度在阈值内，不再加预算。

**后续可做**：
- 小模型导入时前约 50 s 单进程测内存（test1 占总时长约 40%），可按模型规模预估首批 worker 数。
- `202610211132` 变形动画“炸开”：该模型有 STATUS（单元删除），疑为被删单元节点位移无意义所致，Abaqus 会隐藏 STATUS=0 单元；未查。
- 断点续做：`Pipeline-Resume-Design.md`，待定。
- 全量测试另有约 68 个与缓存无关的失败（模型修正/灵敏度/求解器，多为 inp_service 拆分后测试未跟上），建议单独清理。
- 探测工具只测 GET；POST 纯查询（render-faces、surface-patch、node-table 等）未覆盖。

### 0.10 1440w 实测与几何类显示缓存（2026-10-23）

**1440w 重新导入（项目 `202610221440`，`9f870ce`）**：`cache_prepare` 376/376、0 跳过、0 失败，4 进程，507 s
（range 124、scalars 248、位移 2、变形统计 2；静力步无 modal-animation）。
`snapshot_results.py --workspace <完整路径> --cache disk --compare baseline.npz --procs 2`：**完全一致 832、不一致 0**（13 分钟），
多进程严格准备落盘的内容与基准代码逐项相同（§0.8 遗留项完成）。注意 `--workspace` 必须给完整数据路径，只写项目号会按代码目录
相对路径解析，模型加载失败、832 项全部“不一致”（第一次比对就是这样）。

**`--full` 探测（`probe_202610221440_20261023_000518`，阈值 100 ms）**：表面约 322 万三角面、967 万渲染顶点，逐顶点响应很大
（frame-scalars 39 MB；color-code、element-mesh-edges、modal-shape 各约 116 MB；deformed-positions 232 MB）。
- frame-scalars 两次 143～330 ms：命中缓存，慢在 39 MB 的归一化与打包，属下限，不是漏缓存。
- modal-animation 测在静力步（每阶 20 帧包 **2.3 GB**，首次 13～15 s、命中 2 s）：viewer 只在 FREQUENCY 步调用，属探测工具误测，
  已改为只测 FREQUENCY 步。但说明大模型模态动画预算包远大于 §0.8 的“每阶数百 MB”估计（约 n_frames × 渲染顶点数 × 12 字节），
  **是否继续预算 modal-animation 待领导确认**。
- deformed-positions 首次 9.2 s / 2.7 s、命中 107 ms（scale 任意，只预算了位移底座）：**是否预算建议 scale 的整包待领导确认**。
- pick 每次 200～380 ms（现查，不属缓存），以后单独看。
- 下面四项 viewer 调用且大模型明显慢，2026-10-23 用户拍板先做：

| 接口 | 实测（修改前） | 处理 |
|---|---|---|
| `color-code/{instance}/region-mesh-edges` | 整模型大小区域每次 9～10 s（116 MB），中等区域 1～3 s | 整包存盘；区域节点标记去掉 `np.unique`（结果相同） |
| `color-code/{instance}/region-outline` | 大区域每次约 2.6 s（0.3 MB） | 整包存盘 |
| `color-code/{instance}` | 每次约 0.6 s，单元集合首次约 2 s（116 MB） | 整包按“面分类 key + 当前改色/改名”存盘 |
| `color-code/{instance}/legend-entries` | 首次 2.7～8.4 s，之后约 40 ms（仅进程内记忆化） | 只依赖几何的部分存盘；单元集合单元数改用编码 bincount |

实现（`services/region_edge_service.py`、`color_service.color_code_payload` / `_legend_entries_base`）：
- 缓存 key 都以面分类 key（`color_service._codes_key`：render.h5 / L1 几何 / sets.h5 签名 + 高级索引版本）为身份；区域边线另加
  边文件与 surface.h5 签名。改色/改名进 color-code 包的 key（改后换新包，立即生效）；legend-entries 改色/改名每次现查叠加。
- `warm_color_views`：导入严格准备（`readiness_service`，在单元边线之后）与旧项目手动预热（`warmup_service`）都会执行。范围 =
  每个实例 × 每个非 elset 方案 + 每个单元集合单选（前端把同一份 set_names 广播给所有实例，所以每个实例都算每个集合）的着色包与
  图例条目，以及每个方案在表面出现的每个区域、每个集合（只在所属实例）的单元边线与外轮廓。多选集合任意组合不穷举（首次请求后缓存）。
  请求本身就会报参数错误的组合（ValidationError / NotFoundError）记为跳过。已在磁盘的大条目只检查存在（`cache_store.payload_exists`），
  追加结果重跑准备时不重读几 GB 响应包。结果写进导入日志一条“几何显示缓存完成：{…}”。
- 响应头新增 `X-Cache: hit|miss`（三个二进制接口），探测工具可直接看是否命中。
- 磁盘估算（1440w）：着色包（5 个方案 + 15 个集合）× 116 MB ≈ 2.3 GB；区域边线每个方案合计约一份整模型边线（≤ 116 MB）× 5 + 集合若干百 MB；
  外轮廓与图例条目可忽略。**单元集合很多的模型，着色包 = 集合数 × 整模型顶点数 × 12 字节**，需要留意磁盘上限。
- 测试 `tests/test_l3_color_views_cache.py`。

**待开发者验证**：跑 `test_l3_color_views_cache.py`、`test_l3_region_edges_route.py`、`test_l3_color_code_cache.py`、
`test_color_elset_prefix.py`、`test_color_legend_save_route.py`、`test_pipeline_readiness.py`、`test_l3_warmup_scheduling.py`、
`test_l3_disk_cache_warmup.py`；重启服务后对 `202610221440` 手动 `POST /results/warmup` 补算（或重新导入，看“几何显示缓存完成”日志的
条目数与秒数），再跑 `--full` 探测：上述四个接口第二次应全部 `X-Cache: hit` 且远低于原耗时（已验证，见下方复测）。

**复测（2026-10-23 凌晨，`dde76cd`，`probe_202610221440_20261023_011643`）**：服务重启后手动 warmup，日志
`color views {'color_code': 20, 'legend_entries': 20, 'region_mesh_edges': 97, 'region_outline': 97, 'skipped': 0, 'seconds': 86.0}`
（82 个方案区域 + 15 个集合区域；结果计划 376 项已在盘上，整个 warmup 128 s）。探测（再次请求中位数 / 最大值，ms）：
region-mesh-edges 140/10116 → 9/65、region-outline 49/2606 → 8/16、color-code 576/674 → 55/87、legend-entries 首次 2692/8441 → 10/26，
四项全部 `X-Cache: hit`。
- 新暴露：`color-code/{instance}/legend`（viewer 调用）单元集合首次约 1.4～1.6 s。原因是单元集合分类只进内存，以前着色包现算时
  顺带算好了分类，图例跟着命中；着色包改为直接命中后分类没人算。已改为图例与着色包同 key 存盘（`key + ("legend",)`），
  `warm_color_views` 增加 `legend` 一类。
- 探测工具仍在静力步请求 modal-animation：`meta/overview` 不带 `result_group` 时不列 step，工具拿不到 procedure，已改为按结果组取。
- 其余与上次一致：pick 约 0.2～0.36 s/次、render-buffers(-chunked) 0.25～1 s、frame-scalars 命中 124～234 ms（39 MB 下限）。

**第二次复测（`5ebe167`，`probe_202610221440_20261023_012948`，821 个请求）**：warmup 日志
`color views {'color_code': 20, 'legend': 20, 'legend_entries': 20, 'region_mesh_edges': 97, 'region_outline': 97, 'skipped': 0, 'seconds': 34.9}`。
legend 首次最大 1607 → 33 ms；modal-animation 不再出现在静力步探测里；region-outline / legend-entries 全部 OK，region-mesh-edges 81 OK + 1 个首次 101 ms。
color-code 20 个都是 `hit/hit`，首次 100～137 ms、再次约 50 ms：命中后从磁盘读 116 MB 的耗时（第二次走系统文件缓存），属 I/O 下限，
被 100 ms 阈值判为 NOT_PRECOMPUTED 不代表没预算。至此 viewer 调用的接口里，仍超过阈值的只剩与缓存无关或响应体积决定的：
pick / nearest-face（现查）、render-buffers(-chunked)（174～213 MB）、frame-scalars（39 MB）、modal-shape（116 MB）；
deformed-positions 首次约 0.2 s（任意 scale，只预算位移底座）与 modal-animation 预算策略待领导确认。

### 0.11 接续清单（2026-10-23 收尾，最后提交见 git log）

**已完成并验证**：1440w 多进程落盘快照比对 832/832 一致；color-code / legend / legend-entries / region-mesh-edges / region-outline
存盘 + 导入预算（`cfc6126`、`5ebe167`），测试全绿，`--full` 探测全部命中（§0.10）；探测工具只在 FREQUENCY 步测 modal-animation。

**待领导确认（新对话先问结论）**：
1. `deformed-positions` 是否预算“建议 scale”整包：现首次约 0.2～9 s（冷启动差异大）、命中约 100 ms；每帧约 232 MB（1440w），
   帧数多的模型线性增长。viewer 调用时 scale 由用户调整，且**不带 `result_group`**（`ThreeViewport.vue:1835`，多结果组项目可能取默认组，需单独确认）。
2. `modal-animation` 预算策略：现 FREQUENCY 步每阶预算 scale=建议值、n_frames=20 的整包，大小约 20 × 渲染顶点数 × 12 字节
   （1440w 每阶 2.3 GB，20 阶约 46 GB，逼近默认 100 GB 磁盘上限）；viewer 默认 shader 模式只用 modal-shape（116 MB），
   预计算模式需用户手动切换。可选：保留 / 按规模（顶点数或总大小）决定 / 不再预算。

**后续可做（未排期）**：pick 每次 0.2～0.36 s 提速；多选单元集合组合首次约 2 s（单集合分类可存盘后组合）；
单元集合很多的模型着色包磁盘 = 集合数 × 顶点数 × 12 字节需留意；§0.9 原有的“后续可做”各项仍有效。

### 0.12 领导结论落地：deformed-positions 预算建议 scale（2026-10-26）

领导结论（2026-10-26）：① `deformed-positions` 按建议 scale 缓存；② `modal-animation` 前端用的是一次性下载，也缓存。

- ②：计划里本来就有（FREQUENCY 步每阶 × 有表面的实例，scale = 建议值、n_frames = 20，§0.11），**保持不变**。
- ①：U 场每个实例 × 全部帧各预算一包，scale = 该帧 `deform-suggest-scale`（`precompute_plan._deformed_positions`）。
  接口与计划共用 `result_service.cached_deformed_positions`，key 不变（`deformed_positions_v1`），所以导入前请求过的包照样命中。
  纯梁/质量点实例也算（梁线、点、耦合线随变形一起返回）。`PLAN_VERSION` 升为 3，旧项目手动 warmup 时补算。
- §0.11 里说 viewer “不带 `result_group`” 不成立：`store.getApiUrl()`（`viewer/src/store/viewer.js`）会给每个请求拼上
  当前 `result_group`；变形面板 `autoFillScale` 把建议值原样填进 scale（不取整），与预算的 key 一致。
- 覆盖不到的情况：非 FREQUENCY 步点“播放”时，**所有帧都用当前所选帧的建议 scale**（`startDeformAnim`），
  而预算的是每帧各自的建议 scale，因此除所选帧外第一圈都是现算（之后命中）。要覆盖需要 帧数² 包，暂不做。
- 探测：`tools/probe_endpoints.py` 的 deformed-positions 改为带建议 scale（原用默认 scale=1，测不到预算的包）。
- 磁盘：1440w 每帧约 232 MB × 帧数；100 GB 默认上限内，帧数多的大模型要留意（与 modal-animation 叠加）。

## 1. 需求与结论

用户当前要求：**能缓存的都缓存，需要遍历接口可能性进行缓存；暂不以磁盘存储量作为缩减覆盖范围的理由。**

现有方案按“首屏优先”设计，只准备每个分析步最后一帧的默认视图，不能满足上述要求。建议改为：

1. 数据本身可以列全的组合，全部提前计算并持久化，包括所有有效帧、分量、实例和已有区域。
2. 用户可以任意输入的组合，提前准备完整的数据底座和索引；预计算默认及明确列入计划的组合，其余合法请求首次计算后持久化。
3. 用统一任务清单定义覆盖范围，同步准备、后台补算、手动预热和验收使用同一份清单。
4. 新导入及追加结果的完成条件，从“约定首屏准备完成”扩展为“本次全量计划的必需项均已验证落盘”。**已拍板采用（2026-10-21，见 §0.1）**，实施前仍不是现有行为。
5. 保持现有接口 URL、方法、参数、响应体和响应头不变。进度和缓存覆盖情况先写日志及工作区内部清单。

这里的“全部”指全部有效、可枚举的数据组合，以及明确约定的参数配置。不能承诺提前生成任意浮点参数、任意节点列表和任意空间位置的所有响应。

## 2. 当前实现与实际缺口

### 2.1 当前两条准备路径

| 路径 | 代码位置 | 当前范围 |
|---|---|---|
| 导入、追加结果、L2 重跑后的同步准备 | `src/l3/services/readiness_service.py::prepare_workspace` | 每步最后一帧、默认标量及范围、旧 frame-colors 默认 U1、U 位移及比例统计、几何分类和单元边线 |
| 加载模型、发现新增结果、手动触发后的后台预热 | `src/l3/services/warmup_service.py::_warm_targets` | 基本相同；部分失败只记录或跳过，不能作为严格完成依据 |

相关现有约定见 [统一就绪判定](Ready-Barrier-Design.md)、[现有预计算缓存方案](L3-Precompute-Cache-Design.md)。现状由当前代码核对；这些文档中的历史说明不能代替代码核对。

### 2.2 本次前端操作暴露的问题

在项目 `2022610152141`、结果组 `A`、实例 `200W-NODES-CYLINDER`、步 `SUBCASE_1` 上：

- 元数据包含 10 帧，字段 U 的分量为 U1、U2、U3、USUM。
- 第 9 帧、`component_idx=2` 和 `3` 的首次请求出现 `miss`；之后均已生成对应磁盘缓存。
- 只覆盖四个显式分量和 smooth/flat 两种模式，就有 `10 × 4 × 2 = 80` 个标量组合。
- 继续保留省略分量参数的默认请求语义，则共有 `10 × 5 × 2 = 100` 个组合；此数字尚未计入集合和自定义渲染参数。
- 现有 frame-scalars 主预热只覆盖“最后一帧、默认分量、smooth”的一个组合。

这是覆盖范围不足的证据，不是缓存整体失效的证据。上述记录也不是全量预热性能测试，不能据此推算所有模型的完成时间。

### 2.3 不只是多加几层循环

- 部分有限组合尚无持久缓存，例如区域网格边线和区域轮廓。
- frame-scalars 当前缓存归一化前的顶点数值，命中后仍需集合处理、归一化、响应打包和传输。
- 后台预热的完成标记主要基于结果文件签名，未表达新方案的覆盖清单和策略版本。
- 当前每工作区磁盘上限为 100 GB，超过后淘汰旧条目；直接全量遍历可能边生成边淘汰。
- 旧项目已有 ready，不会仅因代码升级就自动完成新的全量计划。

## 3. 审计范围与接口覆盖矩阵

主要范围为当前 L3 可视化读取链路，包括 Project 模式、仍在使用的 legacy 读取接口、兼容查询和旧模态读取接口。模型修正的读取接口需单列审计，不能未经盘点承诺全部纳入。

应按“有没有副作用”分类，不能按 GET/POST 分类。POST 的节点表、框选等也可能是纯查询；导入、写字段、修改图例、删除、启动求解等操作不得被预热自动重放。

| 接口族 | 可枚举维度与建议预计算内容 | 任意参数或动态部分 | 当前缺口/实施要求 |
|---|---|---|---|
| `results/frame-scalars` | 全结果组 × 步 × 有结果块的实例 × 全有效帧 × 全实际分量/已有派生字段 × smooth/flat；覆盖默认请求参数 | set、归一化上下限、feature_angle、average_threshold、use_geometry_split | 扩展当前最后一帧默认分量循环；标准请求同时考虑最终响应缓存 |
| `results/frame-scalar-range` | 与标量相同的有效组合，各实例范围及已有集合范围 | 任意实例组合、动态集合、任意平均参数 | 各实例范围提前算，多实例请求复用后合并，避免穷举实例子集 |
| `results/frame-colors` | 按该接口实际支持的 component 和 mode 枚举全部有效帧 | 集合、动态颜色配置等 | 不能直接照搬 component_idx 列表；以旧接口支持的语义为准 |
| `vertex-displacements`、`modal-shape`、`deform-suggest-scale` | 所有可用 U 帧/模态的顶点位移、建议比例和标准响应 | 可变实例组合等 | 目前主要准备最后一帧的公共底座 |
| `deformed-positions`、`deformed-normals` | 所有有效帧的基础位移；scale=1、前端自动建议比例以及计划中的其他比例对应响应 | 任意 scale | 当前 positions 有按 scale 的整包缓存；normals 等路径需要核对是否真正复用，不能只靠底层位移命中 |
| `modal-animation` | 所有适用模态的位移底座；计划内 scale 和 n_frames 的最终动画包 | 任意 scale；n_frames 当前合法整数 4～120 | 固定 scale 后帧数是有限维度，可全部列入计划；前端默认值 20 必须覆盖，其他配置的必需范围需审阅确定 |
| `geometry/*` | 全实例 render-buffers、标准分块、element-mesh-edges、feature-edges、lines、points、couplings、orientations 等 | 任意 chunk_size、集合子集、任意面列表 | 已有 L2 文件不等于已有最终响应缓存；可预生成标准响应，任意切块复用基础数组 |
| `color-code/*` | 已有 scheme、已有 region、已有单集合；分类编码、区域边线、区域轮廓 | 任意集合组合、用户改色和改名 | 目前分类预热排除 elset；应覆盖已有集合，动态显示信息必须有版本或即时组合 |
| `results/raw-values` | **不缓存**（2026-10-22 拍板，见 §0.9） | — | 计算类接口，viewer 不调用；每次直接读 L1，需要快用 `format=l3be` |
| `results/node-table`、`results/node-time-value`、`query/node-displacements` | 全实际帧的节点结果底座、标签映射、时间轴和必要索引 | 任意节点/列组合、请求顺序、任意时间插值 | 不穷举所有列表；复用完整数据，提取/插值后可缓存具体查询结果，保持顺序和错误语义 |
| pick、ray-pick、bbox、nearest-face、surface-patch、render-faces、section-mesh | 几何、结果映射、空间/拓扑索引，固定预设剖切可额外生成 | 射线、坐标、包围盒、剖切位置和任意选区 | 准备公共底座；合法具体查询首次计算后可缓存，不宣称所有任意空间输入已预算 |
| meta、steps、frames、fields、sensitivity/model-update 字段目录 | 每个数据版本的元数据快照 | 新结果、字段、集合、图例或模型修正状态变化 | 小响应也纳入清单；实时进度和任务状态不能长期返回旧快照 |
| `results/user-fields`、`user-field-colors` | 已存在用户场及其默认显示数据 | 新建/覆盖用户场、任意显示范围 | 按用户场版本增量生成，写入操作只触发失效与补算 |
| Simright query、`/api/modal/*` 等兼容/旧入口 | 对当前注册模型逐项审计可复用的几何、字段、频率、模态和时序结果 | 任意节点集合、系数及第三方参数 | 必须落到具体 handler 的依赖和参数，不能只登记一个路由就宣布覆盖完成 |

实施前应从实际注册路由和调用服务生成完整清单，给每个读取接口标注：全量预计算、复用底座、首次计算后缓存、实时读取及其原因。上表为接口族审计起点，不冒充逐个接口均已完成实现评估。

## 4. 参数覆盖规则

### 4.1 必须穷举的维度

- 结果组、分析步、有效实例、真实帧编号；不得只用 num_frames 构造连续编号而忽略实际 frames 元数据。
- 实际存储的分量、已存在的不变量/派生字段、接口的默认分量请求。
- smooth/flat；已有节点集、单元集、用户集合及分类区域。
- 接口明确支持的有限输出格式和其他有限枚举。
- 第 0 帧也覆盖；不能继续以“可能是全零”作为跳过理由。

有效组合来自 manifest 的 result_files、result_blocks、frames、组件信息，并核对真实数据。不同实例/单元类型的分量与帧覆盖可能不同，不能机械做所有字段和实例的笛卡尔积，也不能只按字段全局分量数判断合法性。

### 4.2 任意参数的处理

- feature_angle、average_threshold、scale、归一化范围、空间位置等不能靠存储不限解决穷举问题。先覆盖现有默认值和明确列入计划的配置，其他值按需缓存。
- use_geometry_split 是布尔量，可枚举；但与任意角度、阈值组合后仍需参数配置边界。
- 已有集合可遍历；所有任意集合组合不作为全量完成条件。新建集合后触发该集合的补算。
- 任意节点列表、分页、排序、切块应复用公共数组和索引，不能为了命中响应缓存预生成所有排列组合。
- n_frames 是 4～120 的有限整数。不能把它本身称为“无法枚举”；困难来自与任意 scale 的组合。具体必需配置必须写进计划，不能藏在代码默认值里。

### 4.3 参数归一化不能改变数值语义

应统一默认值、大小写、实例别名、结果组解析等缓存键表示；只有确认数值、数组顺序、元数据及响应行为等价的请求才能共享缓存。

特别审查 `result_service.py::_extract_component`：当前 `component_idx=None` 对多列数据取范数，显式索引则读取单列。对于已有 USUM 列的 U 数据，两条路径不能仅因名称都像“合量”就直接合并。（已确认原实现会得到 √2 倍错值，并已修复，见 §0.2。）本文不修改合量、应力不变量、平均、符号、NaN、集合筛选或归一化算法；若发现算法问题，单独提交设计和验证依据。

## 5. 缓存分层与请求流程

建议保留三种用途，避免“缓存命中”含义混乱：

1. **基础数据与索引**：几何、节点/单元映射、原始结果块、空间索引、时序轴，供多个接口共享。
2. **计算结果**：顶点标量、范围、位移、分类编码、区域边线等，覆盖全部有限数据组合。
3. **最终响应**：对计划内确定参数提前生成请求实际返回的二进制/JSON 内容，并保存恢复响应所需的元信息；其余合法参数首次请求后保存。

请求先定位当前数据版本和参数，再按对应层次读缓存；缺失时使用同一计算函数补算并原子写入。具体接口应明确读到的是基础数据、计算结果还是完整响应，不能用一次底层命中证明整个请求不再计算。

frame-scalars 中自定义 global_min/global_max、集合模式等会改变最终结果；预生成标准响应不能改变这些入参的优先级。颜色和显示名称修改后也必须即时体现。

完整缓存命中仍有磁盘读取、HTTP 压缩、网络传输、前端数组处理和 GPU 绘制成本，不承诺页面瞬间显示，也不将磁盘命中等同内存命中。

## 6. 统一计划、断点续算与完整性

建议增加工作区内部预计算计划/任务账本，具体采用 SQLite 还是其他格式由实现审阅确定。每项至少包含：

- plan_version、cache_schema_version、项目/结果组、接口族、规范化参数、数据版本和任务唯一键。
- 必需标记、依赖项、任务状态、落盘条目、输出形状/类型/元信息及耗时。
- 状态建议为 pending/running/succeeded/not_applicable/failed；not_applicable 必须有明确依据。
- 未执行任务和失败任务都不能计为完成；中途退出后 running 项可恢复并重新核验。

计划不等于把所有结果一次放进内存。生成器按依赖顺序分批计算，输出立即落盘，再释放当前批次的临时数据。

完成判定必须逐项验证可读性与必要元数据，不能只看文件存在或循环结束。损坏文件重新生成；写盘失败、声明有结果却读取失败、必要元数据缺失均记录 failed。

纯梁/质量点等无表面实例、实际无结果块的实例、合法全 NaN 结果等需沿用现有接口语义，明确记录原因。合法空响应可预生成；不能将所有 NotFoundError 都当作可跳过。

最终完成标记要依赖计划版本、覆盖参数、源文件版本、渲染几何、高级索引以及相关集合/用户场版本；不能仅记录 result_files 的摘要。发布前重新确认依赖版本未变化，避免计算旧版本期间发生更新却发布新版本的完成标记。

## 7. 执行顺序与 ready 语义（建议变更）

建议新流程为：

```text
L1 基础数据 → L2 core → L2 advanced
    → 固定本次数据版本并生成全量计划
    → 分批计算、逐项落盘及核验
    → 确认必需项完整且依赖版本仍一致
    → 发布计划完成标记与现有 ready 状态
```

- 独立 runner 和内嵌 runner 使用同一执行器，关闭长时间数据库读事务后再计算。
- 严格准备不依赖项目先 ready，也不等待“所有解析任务空闲”；延续现有防循环等待约束。
- 后台预热、手动预热用于检查和补齐同一份计划，不能用另一套更小范围的完成标记替代。
- 新导入的必需项失败，沿用项目/结果组现有 error 与 error_message 反馈；接口结构不变，但 ready 的承诺范围变更必须同步文档。
- 追加结果只准备新结果组及确实受到影响的共享数据。已有项目几何可继续 ready，追加组在完成前保持 running；不得误发布其他未完成组。
- 必须通过工作区/版本粒度协调严格准备、后台补算和前端按需填充。正常请求的同键并发需避免重复重算，锁等待不能形成依赖环。
- 多项目补算采用受控调度，不为每个项目无限启动计算线程；计算批次间允许后台任务让路。严格导入阶段不能被自身“解析繁忙”判定永久挡住。

代价是导入到 ready 的时间增加。当前没有全量方案实测，不能给出未经测量的倍率或时长。已拍板 ready 必须覆盖全量计划（§0.1），不采用“先 ready 后补算”。

## 8. 存储、内存与失效

### 8.1 磁盘暂不限制覆盖范围

全量持久缓存不应因现有容量上限自动淘汰本计划的必需条目。建议为磁盘缓存增加含义清楚的“不限容量”策略；具体配置形式待审阅。

不得把现有 `APP_SCALAR_DISK_CACHE_MB=0` 改解释为不限容量，因为它当前表示关闭磁盘缓存。内存上限与磁盘策略应独立，磁盘可持久保存全部结果，内存仍按预算淘汰。

“不在意存储”不代表忽略实际磁盘写入失败：无空间、无权限、文件损坏时必须明确失败，不得发布虚假完成状态。现有每次大条目写入后的目录容量扫描，在不限容量模式应避免成为全量任务的额外重复开销。

### 8.2 版本与失效

| 变化 | 建议处理 |
|---|---|
| 新增结果组 | 增量生成该组计划；共享数据按真实依赖判断是否补算 |
| 重写结果文件、外部字段或用户场 | 对应结果版本失效，重新计算受影响任务 |
| L2 core 或 advanced 更新 | 更新几何/高级索引版本，受影响项失效；禁止命中旧几何对应响应 |
| 新建、修改、删除集合 | 对应集合筛选、区域结果和最终响应失效；不能只依赖结果 H5 签名 |
| 图例颜色、名称修改 | 带该显示信息的结果失效或即时组合，基础分类编码可继续复用 |
| 预热策略、缓存格式升级 | 新版本计划补齐新增项，数值和布局兼容的旧条目经核验后复用 |
| 删除项目 | 沿用工作区删除流程清理相关缓存与计划 |

优先继续使用现有源文件签名和原子写机制，同时明确所有动态依赖。旧结果在更新时如何继续服务、何时切换新版本，需在实现前给出一致性方案；不能任由旧任务覆盖新版本条目。

## 9. 旧项目补算与部署

- 不自动重跑所有原始 ODB，也不因升级批量改变旧项目的 ready；对可兼容的已有 L1/L2 生成新计划并补算。
- 旧项目在全量补算完成前，内部标记为“历史 ready，当前计划未完成”，不得冒充满足新保证。现有接口不新增字段时，先在日志/内部账本明确区分。
- 若业务要求历史 ready 也统一承诺全量完成，应先完成选定项目补算再切换承诺，或单独批准状态迁移策略。
- 缺少当前格式高级索引的工作区需单独处理，不能吞掉缺失错误后标记完成。
- 先选小项目验证数值和完整性，再覆盖当前 200 万节点项目和大模型；不一次性同时加载所有模型。

## 10. 日志与验收

保持 frame-scalars 当前 `memory_hit/disk_hit/miss` 日志，不新增接口参数或响应头。计划日志建议另含 plan_id、版本、接口族、执行来源（导入准备/后台补算/前端请求）、总项数、完成数、失败数、无适用数据项数、当前参数和耗时。

验收分成三层：

1. **数值与协议不变**：同一数据版本下有缓存/无缓存的数组、顺序、NaN、图例、响应体和既有响应头一致；覆盖 NODAL、ELEMENT_NODAL、INTEGRATION_POINT、多实例和已有集合。
2. **覆盖与故障正确**：计划内所有必需项可读；进程重启后仍可磁盘命中；中断能续算；损坏/失败不算完成；源数据、集合和计划版本变化后正确补算；独立/内嵌 runner 均不提前 ready。
3. **真实页面与代价可测**：页面切遍全部帧、分量和模式；覆盖范围内首次访问应命中相应缓存层。分别记录导入解析时间、预计算时间、重启首次访问、重复访问、传输及浏览器处理时间，不能只看总耗时判定命中。

200 万节点样例中至少核对 80 个显式标量组合，加上 20 个默认分量组合；这只是该接口在默认渲染配置下的子集，不是整个计划总数。

遵守仓库测试约定：本次不运行测试；未来实现后的自动测试由开发者本地执行。方案中的验证清单是待执行要求，不是已完成记录。

## 11. 建议实施顺序

1. 完成逐接口清单，确认参数语义、默认配置、必需范围和 ready 承诺。
2. 建立统一计划、账本、依赖版本、持久化校验和受控执行器；先接入当前已有缓存。
3. 优先补齐所有帧/分量/smooth-flat 的标量、范围、颜色及全部 U 位移底座，解决当前明确缺口。
4. 补齐几何最终响应、已有集合和分类区域、区域边线/轮廓、raw-values、元数据与兼容读取链路。
5. 补齐标准变形/动画响应及任意查询的公共底座、按需结果缓存；逐项确认覆盖矩阵没有遗漏。
6. 接入两种 runner 的严格完成判定，处理旧项目补算和版本切换。
7. 更新主设计、缓存设计、Ready-Barrier-Design、前端权威接口说明及必要科普，进行开发者本地验证和真实页面验收。

分批实现不等于降低最终覆盖目标。未完成全部必需阶段前，应明确记录仍是部分覆盖，不能对外宣布全量方案已完成。

若实现涉及 `src/l3/api/routes/` 的接口修改，必须同步 `L3-API-Quick-Reference.md` 和顶部更新时间；即使结构不变，ready 承诺和缓存边界的变更也应同步使用说明。主设计 [ODB-Service-Architecture.md](../../ODB-Service-Architecture.md) 需在方案批准实施时更新，本文草案暂不覆盖现行权威约定。

## 12. 请 Claude 重点审查

1. 覆盖矩阵是否漏掉实际注册的读取接口或兼容 handler？哪些路径虽然调用了同一服务，却仍重新读盘或重复计算？
2. 全帧、全分量及派生字段的枚举，是否符合各实例、位置和单元类型的实际数据覆盖？如何处理缺帧、空结果和纯梁/点实例？
3. 默认分量与 USUM 等别名是否真的等价？缓存键归一化会不会意外改变现有数值语义？
4. 对 scale、n_frames、平均参数、集合及分块，哪些配置应纳入必需预计算计划？哪些只保证公共底座及按需缓存？需要把具体列表写清楚。
5. 计划任务粒度、账本格式和完成核验，能否在大量条目下保持可恢复且不把所有输出驻留内存？
6. 两种 runner、Web 按需填充、后台预热之间的锁和状态依赖是否存在死锁、重复计算、提前 ready 或版本竞争？
7. 写盘、容量策略、单项大小检查和旧完成标记是否会造成“刚生成就淘汰”或“漏算却完成”？
8. 全量准备失败、追加组失败、旧项目补算失败时，现有状态及 error_message 能否清楚表达，且不误伤仍可用的数据？
9. 哪些响应需要版本化图例/集合/用户场依赖，才能保证修改后立即生效？
10. 请先反馈设计缺口、风险及建议修改，不直接把草案视为已批准的实现指令；尤其核对全量 ready 语义和任意参数的覆盖边界。
