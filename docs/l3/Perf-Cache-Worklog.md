# frame-scalars / 变形接口性能优化工作日志与待办

> 更新时间：2026-09-02。本文档是跨对话的交接文档：性能优化主线四步全部完成
> （最后一步变形家族缓存已实现，待开发者实测），实现约定与遗留方向见文末。

## 背景

用户模型 700~800 万面片（单 instance 为主），S 场 `frame-scalars` 原耗时约 2 分钟。
逐步优化，**全部限定在 `src/l3` 内，前端零改动**。

## 已完成（四步）

### 第 1 步：ELEMENT_NODAL 条件平均向量化（已合 main，commit 0901fc6）

- `src/l3/services/result_service.py` 的 `_en_per_vertex_averaged`：原来两个逐三角形
  Python 循环 → 纯 numpy（按 etype 分组 + 稠密查表映射域 id；排序 + `reduceat`
  分组统计；flat 序散播保持旧实现的覆盖顺序）。
- 实测：用户模型 2min → **21.36s**；本机合成 2M 面片 50.5s → 5.7s。
- 有意的语义修正：越界分量产生的 NaN 不再参与平均统计（旧版把 NaN 塞进 Python
  列表后 `max()` 行为与遍历顺序相关，属未定义行为）。NaN 顶点照常渲灰。
- 回归测试：`tests/test_l3_en_averaging_vectorized.py`（与朴素参考实现逐位对拍）。

### 第 2 步：两级结果缓存（分支 `perf/frame-scalars-cache`，commit 15248e3 + eb3a2fc）

- `frame_scalars` 归一化前的 scalar_vertex、`compute_scalar_range` 的图例范围、
  `_compute_en_global_range` 的全模型范围，按
  **(结果文件 mtime_ns+size 签名, instance, 帧, 分量, 渲染参数)** 缓存。
- 内存层：进程内字节上限 LRU（`APP_SCALAR_CACHE_MB`，默认 512MB/worker，0=关）。
- 磁盘层：`<workspace>/l3_cache/`（`scalars/*.npz` 大数组、`ranges/*.json` 小结果），
  文件名 = key 的 sha1；临时文件 + `os.replace` 原子写；按 mtime LRU 淘汰
  （`APP_SCALAR_DISK_CACHE_MB`，默认 2048MB/工作区，0=关）。重启不丢、多 worker 共享。
  **这是"L3 只写 manifest.db"约定的唯一例外**（CLAUDE.md / 架构文档已改），随工作区删除。
- 失效：结果文件被重写（外部字段 / result_group 重新解析）→ 签名变 → 自动失效，
  无需手动清理。缓存数组 `setflags(write=False)` 共享只读；set 过滤 / override
  归一化不缓存，仍按请求执行。
- 缓存基础设施都在 `result_service.py` 顶部（`_vertex_cache_*` / `_range_cache_*` /
  `_vertex_disk_*` / `_range_disk_*` / `clear_result_caches`），**下一步直接复用**。

### 第 3 步：自动预热（同分支，commit eb3a2fc）

- `src/l3/services/warmup_service.py`：后台串行预算每个 result_group × step ×
  field × instance 的**第 0 帧默认视图**（模长分量）+ 归一化范围。幂等。
- 触发点：① `core/state.py` 的 render-ready 回调（`set_on_render_ready_loaded`，
  main.py lifespan 注册，覆盖启动预载与首次访问加载）；② L3 poll 发现常驻模型
  result_files 出现新 (group, step, field) 组合（覆盖 Windows 双进程：job_runner
  另一进程解析完新 result_group）；③ 手动 `POST /api/odb/{odb_id}/results/warmup`。
  `APP_WARMUP=0` 只关自动，接口仍可用。
- **已拍板：历史老模型不做启动全量回填**，需要时手动点名：
  `curl -X POST http://10.66.66.2:5000/api/odb/<模型id>/results/warmup`。
- 回归测试：`tests/test_l3_result_scalar_cache.py`、`tests/test_l3_disk_cache_warmup.py`。

### 第 4 步：变形/动画家族缓存（同分支，2026-09-02）

对 `src/l3` 全部接口的缓存评估结论：值得做的集中在变形家族，其余不动
（color-code 已有 legend_scan_cache；render-buffers/feature-edges 每模型只拉一次；
pick/node-time-value 读小切片；node-table/raw-values 低频；frame-colors 是 legacy）。
全部复用第 2 步的缓存设施，实现内容：

1. **`render/positions` / `render/indices` 常驻 ModelIndex**：
   `core/state.py` 新增 `render_positions` dict（float32 连续、只读，CoW 共享），
   `load_l2_render_data` 加载；`result_service.py` 新增 `_get_render_positions` /
   `_get_render_indices`（常驻优先，缺失回退读盘并回填——测试注入的 idx 也走通）。
   `_deform_surface_from_disp` 与 `_load_disp_vertex` 改为取内存。
   `api/routes/geometry.py` 的低频读盘处按拍板**未改**。

2. **deformed-positions 整包缓存**：`frame_deformed_with_aux` 按
   (U 文件签名, instance, 帧, scale) 缓存 (positions, normals, aux_sections)，
   内存字节 LRU + 磁盘 npz（aux 以 aux_names + aux_i 键存取，顺序保持）。
   `frame_deformed_positions`（deformed-normals 端点）改为委托它共享缓存。
   法线聚合 `np.add.at` → 按 (角, 分量) 的 `np.bincount`（9 次 bincount，
   结果与旧实现逐位一致，有对拍测试）。

3. **deform-suggest-scale 统计缓存**：`deform_scale_stats` 返回的小 dict 按
   (U 签名, step, 帧, result_group) 进 `_RANGE_CACHE` + 通用 JSON 磁盘层
   （`_json_disk_get/put`，`_range_disk_*` 重构为其薄封装）。

4. **顶点位移向量缓存**：`_cached_disp_vertex` 按 (U 签名, instance, 帧) 缓存
   [Nv,3]，vertex-displacements / modal-shape / modal-animation 三端点共用；
   modal-animation 的多帧 bytes 按拍板**不缓存**（sin 合成在缓存之上现算）。

5. **预热扩展**：`warm_odb_sync` 对 U 场顺带预算第 0 帧 suggest-scale 统计 +
   各 instance 位移向量（报告新增 `deform_warmed` 字段）；deformed-positions
   按 scale 进键、前端 scale 不可预知，按拍板**不预热**，留给首次请求填。

基础设施改动：内存 LRU 的字节统计泛化为 `_value_nbytes`（值可为数组或嵌套
tuple/list，兼容旧 scalar 条目）；新增通用 `_npz_disk_read/write`（与 scalar_vertex
共用 `scalars/` 目录和同一份容量淘汰）。缓存数组一律 `setflags(write=False)`。

- 回归测试：`tests/test_l3_deform_cache.py`（内存/磁盘两层命中、scale 进键、
  重写失效、modal 家族共享、stats 缓存、法线对拍、预热覆盖、0=关）。
- 本机 scratchpad 等价脚本已全部通过（含 frame-scalars 旧路径回归）。

### 测试状态

- 第 1 步已实测通过（2min→21.36s）。第 2、3、4 步**待开发者在分支上实测**：
  ① 冷启动首次显示时间；② 同帧二次请求时间；③ 重启服务后首次时间；
  ④ 变形动画循环第二圈的帧耗时、deform-suggest-scale 二次请求。
  通过后分支合 main。

## 实现约定（沿用）

- 磁盘键 = 缓存 key 元组的 `repr()` 取 sha1，key 必含结果文件 `_result_h5_sig()` 签名。
- 变形家族的签名应取 **U 结果文件**（`_manifest_result_h5_path(ws, step, "U", rg)`）。
- 本机（codex 服务器）不跑 pytest；用 scratchpad 等价脚本预验，pytest 文件沉淀给
  开发者本地跑。写完 commit + push 到分支。
- 接口只 GET/POST；改 `src/l3/api/routes/` 必须同步 `docs/l3/L3-API-Quick-Reference.md`
  （含"更新时间"行）。本待办预计不加新接口、不改参数（纯后端提速），若如此则
  Quick-Reference 只需在更新时间行提一句。
- 每步做完更新本文档的"已完成/待办"两节。

## 遗留的后续方向（未拍板，仅备忘）

- 冷算 21s 再往下压：热点是 6M 级 argsort/gather（收益递减）。
- 重计算移出事件循环（线程池），多 instance 请求可并行，避免一个大请求卡住全服务。
- 响应瘦身：gzip（`APP_ENABLE_GZIP` 默认关）或 uint16 量化 payload（需前端配合，暂缓）。
