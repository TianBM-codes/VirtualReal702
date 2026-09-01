# frame-scalars / 变形接口性能优化工作日志与待办

> 更新时间：2026-09-01。本文档是跨对话的交接文档：记录性能优化主线已完成的三步、
> 已拍板待实施的下一步（变形家族缓存），以及实现约定。新对话从"待办"一节接续。

## 背景

用户模型 700~800 万面片（单 instance 为主），S 场 `frame-scalars` 原耗时约 2 分钟。
逐步优化，**全部限定在 `src/l3` 内，前端零改动**。

## 已完成（三步）

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

### 测试状态

- 第 1 步已实测通过（2min→21.36s）。第 2、3 步**待开发者在分支上实测**：
  ① 冷启动首次显示时间；② 同帧二次请求时间；③ 重启服务后首次时间。
  通过后分支合 main。

## 已拍板待办：变形/动画家族缓存（下一个对话从这里开始）

对 `src/l3` 全部接口的缓存评估结论：值得做的集中在变形家族，其余不动
（color-code 已有 legend_scan_cache；render-buffers/feature-edges 每模型只拉一次；
pick/node-time-value 读小切片；node-table/raw-values 低频；frame-colors 是 legacy）。

**继续在 `perf/frame-scalars-cache` 分支上做**，复用第 2 步的缓存设施：

1. **`render/positions` 常驻 ModelIndex（补漏，最优先）**
   `deformed-positions` / `modal-shape` / `modal-animation` 每次请求都从
   `l2/render/<inst>_render.h5` 全量读 `render/positions`（[Nv,3]，大模型几十上百 MB）
   —— 见 `result_service.py` 的 `_deform_surface_from_disp`（≈2049 行）与
   `_load_disp_vertex`（≈2345 行）。而 `render/indices` 早已常驻
   （`core/state.py` `load_l2_render_data` ≈114 行）。把 positions 同样加载进
   ModelIndex（新 dict 字段，CoW 共享），两处读盘改为取内存。
   注意：`api/routes/geometry.py` 里多处也读 positions（render-buffers 等），那些是
   每模型一次的低频接口，**不改**，避免扩散。

2. **deformed-positions 每帧结果缓存**
   按 (U 结果文件签名, instance, frame, scale) 缓存最终 (positions, normals)，
   进内存字节 LRU + 磁盘层（同 scalar_vertex 模式）。动画循环第二圈起全命中。
   法线的 `np.add.at`（`_compute_vertex_normals` ≈2017 行）在 8M 三角形上是秒级
   慢操作，可顺手换 `np.bincount` 按分量聚合（向量化优化，非缓存）。
   aux 几何（line/point/coupling sections）也在同一响应里，一起进缓存值。

3. **deform-suggest-scale 结果缓存（收益/成本比最高）**
   `deform_scale_stats`（≈2273 行）为算一个标量把该帧所有 instance 的整块 U 读盘。
   按 (U 签名, step, frame, result_group) 缓存返回的小 dict，进 `_RANGE_CACHE`
   同款小结果缓存 + JSON 磁盘层。

4. **顺带：vertex-displacements / modal-shape 的 [Nv,3] 位移向量缓存**
   键 (U 签名, instance, frame)。modal-animation 的整包多帧 bytes **不缓存**
   （n_frames×Nv×3×4 可达几百 MB），缓存位移向量后 sin 合成本来就快。

5. **预热范围顺带扩一项**：`warmup_service` 把第 0 帧的 deformed-positions
   （scale 用 `suggest_deform_scale` 的建议值？——注意 scale 进缓存键，前端实际
   传什么 scale 要先确认，不确定就只预热 ③ 的 stats 和 ④ 的位移向量，
   ② 的按 scale 键缓存留给首次请求填）。

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
