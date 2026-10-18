# L3 预计算缓存方案（导入即算好、接口直接走缓存）

> 更新时间：2026-10-18。新导入与重跑新增必需的同步首屏准备屏障，成功后才对外 ready；高级索引和缓存失败返回 error。见 [统一就绪判定](Ready-Barrier-Design.md)。原有自动/手动后台预热仍用于补算，其让路和跳过失败的规则不适用于必需准备。
> 历史更新时间：2026-10-09。状态：第 1～7 步已全部实现并推送，待开发者本地跑测试 + 实测。
> 前置背景：`docs/l3/Perf-Cache-Worklog.md`（frame-scalars 四步优化）、
> `docs/近期效率优化与算例总览.md`（开发者 9c2afe2 引入的另一套整包缓存）。

## 1. 目标

模型导入后（不涉及模型修正时），显示用的接口数据不会再变化。因此：

1. **导入即预计算**：模型 / 结果组解析完成后，后台把"打开模型时一定会调到"的接口数据算好，
   存到项目目录下；接口调用时直接读缓存。
2. **存放位置**：项目工作区 `<workspace>/l3_cache/`。**删除项目时整个工作区被删，缓存一并清理**
   （`projects.py` 的 `delete_project` 已经是 `safe_rmtree(workspace)`，无需改动）。
3. **帧选择**：预计算**每个 step 的最后一帧**（多 step 时每个 step 各算最后一帧）。
   静力 step 的第 0 帧通常是加载前的初始状态（全零），没有预计算价值。
4. **前端零改动**：所有接口的 URL、参数、响应格式保持不变。前端侧缓存本期不考虑
   （新前端在内网，无法同步其调用清单）。

## 2. 现状问题（合并 perf 分支后）

合并后同时存在两套缓存，互不知道对方：

| 来源 | 内容 | 位置 | 容量上限 / 淘汰 | 开关 |
|---|---|---|---|---|
| perf 分支（我们） | 云图 scalar_vertex、图例范围、变形整包、顶点位移、suggest-scale | 内存 LRU + `l3_cache/scalars/`、`ranges/` | 有（2GB，按 mtime 淘汰） | `APP_SCALAR_CACHE_MB` |
| 9c2afe2（开发者） | 原始节点位移 | `l3_cache/raw_displacements/` | **无** | 跟随上面开关 |
| 9c2afe2 | 变形法向量 | `l3_cache/deformed_normals/` | **无** | **无** |
| 9c2afe2 | deformed-positions 整个响应包 | `l3_cache/deformed_payloads/` | **无** | **无** |
| 9c2afe2 | modal-shape / modal-animation 整个响应包 | `l3_cache/modal_payloads/` | **无** | **无** |

问题：

- 同一份数据在磁盘上存 2～4 份（例：deformed-positions 一次请求写出响应包 + 法向量 + 原始位移 + 我们的整包）。
- 开发者的目录没有容量上限和淘汰，每换一个 scale / 动画帧数就多一个几百 MB 文件，只增不减。
- 外层整包命中直接返回，内层缓存形同虚设；关掉开关也关不掉整包缓存。
- `_load_render_positions_indices` / `_RENDER_ARRAY_CACHE`（`result_service.py`）无任何调用方，是死代码。
- 预热只算第 0 帧。
- "按属性 / section 分类着色"（`color-code`）完全没有缓存，且 `scheme=section` 有逐面片 Python 循环
  （`color_service._labels_from_section_id`），几百万面片时很慢。
- 缓存文件名是 key 的 sha1，看不出属于哪个结果组，无法按结果组清理。

## 3. 设计

### 3.1 接口分类

**A 类：只与模型几何 / 属性有关 → 导入后预计算**

| 接口 | 缓存内容 | 说明 |
|---|---|---|
| `GET color-code/{instance}`（etype / material / section_type / section / section_assignment） | 每个面片的分类编号 + 分类值列表 | 见 3.3，用户改色/改名即时生效 |
| `GET color-code/.../legend`、`legend-entries`、`schemes` | 复用同一份分类结果 | |
| `GET geometry/{instance}/element-mesh-edges` | 去重后的边端点 | 现状每次对几百万条边 `np.unique` |
| `render-buffers`、`feature-edges`、`lines`、`points`、`couplings`、`orientations`、`node-labels` | **不缓存** | 直接读 L2 预处理文件，已经很快（200 万节点约 0.3s），再存一份只是重复占盘 |

**B 类：与结果有关 → 预计算每个 step 的最后一帧**

| 接口 | 预计算内容 |
|---|---|
| `frame-scalars` + `frame-scalar-range` | 每个 result_group × step（最后一帧）× field × instance 的默认分量（模长 / Mises） |
| `frame-colors`（旧接口） | 同上的默认分量。新前端用哪个接口未知，两个都覆盖 |
| `deform-suggest-scale` | 每个 step 最后一帧的统计 |
| `vertex-displacements` / `modal-shape` | 每个 step 最后一帧的顶点位移 |
| `deformed-positions` | scale 由用户决定，无法预计算；依赖的顶点位移已预计算，首次请求后缓存整包 |

**C 类：参数组合无穷 → 不预计算，必要时首次请求后缓存**

`section-mesh`（任意剖切位置）、`query/*`（点选）、`node-table` / `raw-values`、`modal-animation`
（scale、帧数由用户定，首次请求后缓存）、`user-field*`（用户自定义场）、`color-code?scheme=elset`
（集合组合无穷）。

### 3.2 统一缓存存储层

新增 `src/l3/services/cache_store.py`，所有缓存（我们的 + 开发者的）都走它：

```
<workspace>/l3_cache/
  model/                 ← A 类：只依赖模型几何，结果组变化不影响
    arrays/<sha1>.npz
    json/<sha1>.json
  groups/<result_group>/ ← B/C 类：按结果组分目录，便于整组清理
    arrays/<sha1>.npz
    json/<sha1>.json
    payloads/<sha1>.bin  ← 最终 L3BE 响应包（deformed-positions / modal-animation）
```

- **统一容量上限**：整个 `l3_cache/` 合计不超过 `APP_SCALAR_DISK_CACHE_MB`，**默认改为 102400（100GB）**，
  超出按最近使用时间（mtime，命中时 touch）删最旧的文件。上限最终由领导定，改配置即可。
- **统一开关**：`APP_SCALAR_CACHE_MB=0` 关闭全部结果缓存（内存 + 磁盘 + 整包）；
  `APP_SCALAR_DISK_CACHE_MB=0` 只关磁盘层。内存层默认 512MB/worker 不变。
- 写入一律"临时文件 + `os.replace`"原子写；读到损坏文件直接删除当未命中。
- key 仍包含源文件的 `(mtime_ns, size)` 签名：结果文件被重写后自动失效。
- 无 result_group 的老模型（legacy ODB jobs）用 `groups/_default/`。
- 缓存最终响应包的接口（`deformed-positions`、`modal-animation`）在响应头返回 `X-Cache: hit|miss`；
  `modal-shape` 改为直接由顶点位移缓存打包，不再单独存响应包，也不再带 `X-Cache`。
- L2 重建基础几何（`ingest.py --phase core` 或 `all`）开始时整体删除 `l3_cache/`；`advanced` 阶段保留基础几何就绪后产生的缓存。所有依赖渲染几何的 key 同时带 render.h5 签名。
- 单元边线缓存优先读取 `<instance>_edges.h5`，兼容旧工作区中嵌入 `<instance>_surface.h5` 的边数据。缓存 key 同时包含 surface 文件签名、边数据来源与 edges 文件签名；高级文件首次出现或重新生成时自动失效。高级文件尚未生成时不缓存“缺失”结果。

**去冗余后的分层**（变形 / 模态家族）：

| 数据 | 保留 | 去掉 |
|---|---|---|
| 顶点位移 [Nv,3] | 内存 + `arrays/`（所有变形/模态接口的公共底座） | 开发者的 `raw_displacements/`（节点级原始位移，与顶点位移重复） |
| deformed-positions | 整个响应包 `payloads/`（命中省掉计算 + 打包） | 我们的 (positions, normals, aux) 整包磁盘层、开发者的 `deformed_normals/` |
| modal-shape | 直接由顶点位移缓存打包（打包单个数组只需几十毫秒） | 开发者的 modal-shape 响应包 |
| modal-animation | 整个响应包 `payloads/` | — |
| render positions | `ModelIndex` 常驻（已有） | 死代码 `_load_render_positions_indices` / `_RENDER_ARRAY_CACHE` |

旧目录（`scalars/`、`ranges/`、`raw_displacements/`、`deformed_normals/`、`deformed_payloads/`、
`modal_payloads/`）：新代码不再读写，服务启动加载模型时顺手删除（一次性迁移，数据可重算）。

### 3.3 color-code 分类缓存

用户可以在图例里改颜色、改名字（`legend_overrides` / `display_names` 表），改完要立即生效，
所以**不缓存最终颜色**，只缓存与模型绑定、计算量大的部分：

- 缓存：每个面片的分类编号 `face_codes [Rf] int32` + 分类值列表 `unique_vals`（按 instance × scheme）。
  key 带 L1 几何文件 / L2 render 文件签名。
- 每次请求：读 overrides → 组装 legend → `pal_arr[face_codes][vtx_tri_idx]`，纯查表，几十毫秒。
- 同时把 `_labels_from_section_id` 的逐面片 Python 循环改为 numpy 批量查表，保证缓存未命中时也不慢。
- `scheme=elset` 不预计算（集合组合无穷），仍按请求计算。

### 3.4 预计算（扩展现有 warmup_service）

- 触发时机不变：模型加载完成 / poll 发现新 result_group / 手动 `POST /results/warmup`。
- 帧：从第 0 帧改为每个 step 的**最后一帧**（`num_frames - 1`，从 manifest `frames`/`steps` 表取，
  按 result_group 区分）。
- 内容：A 类（color-code 各 scheme、element-mesh-edges）+ B 类（见 3.1）。
- 仍是单线程串行的后台 daemon 线程，幂等（已缓存的直接命中跳过），失败只记日志。
- `APP_WARMUP=0` 关闭自动预计算，手动接口仍可用。
- **调度（2026-10-09 第二轮补充）**：
  - 导入即预计算：poll 记录每个项目的状态签名（几何状态 + 各结果组状态），服务运行期间签名变化且
    项目整体就绪（几何 ready、无排队/运行中的结果组）→ 记入待办，解析空闲时主动加载并预热，
    即使没人打开。首轮 poll 只记基线：服务启动前就有的老项目不主动加载（避免一启动全部进内存）。
  - 解析让路：有任何解析任务（项目几何 / 结果组 / 旧版 ODB job，以及几何 ready 之后后台继续跑的
    L2 高级索引阶段——`l2/advanced_status.json` 为 pending/running，超过 12 小时未更新视为残留）
    排队或运行时，自动触发的预热一律推迟；预热进行中每个组合开始前也检查一次，遇到解析开始即中止，之后补做（已算部分在缓存里）。
    原因：结果组解析时每写完一个字段 result_files 就多一行，旧逻辑下 poll 每 10 秒都会重新触发一轮
    预热，和解析抢 CPU/磁盘。手动 `POST /results/warmup` 不受限制。
  - 跨进程去重：`l3_cache/.warmup.lock`（Linux flock / Windows msvcrt，进程退出自动释放）保证同一
    工作区同时只有一个进程在预热；完成后写 `l3_cache/.warmup_done`（result_files 全部条目 + 结果文件
    (mtime,size) 的摘要），其他进程处理待办时看到签名一致就不再为预热去加载模型。两个控制文件不参与
    容量淘汰；L2 重跑清空 l3_cache 时一并删除。
  - L2 分阶段（开发者 2a59fe0）后，单元平均云图与按截面/区域着色依赖高级阶段的 averaging_data：
    frame-scalars / scalar-range 的 key 已带 `advanced_version`（开发者加），color-code 分类编码的
    key 同样加入该版本，高级索引加载前算出的结果不会在加载后继续命中。
  - 代价：新导入的模型会被加载进内存，受 `APP_MAX_LOADED_PROJECTS` LRU 上限约束，连续导入多个
    会挤出最久没用的模型（磁盘缓存不受影响）。

### 3.5 清理

- 删除项目：整个工作区删除，缓存随之清理（已满足）。
- 结果组重新解析：清掉 `groups/<result_group>/` 整个目录（旧签名的条目反正已失效，立即回收磁盘）。
  注：当前代码中没有"单独删除结果组"的接口（`registry_repo.delete_result_group` 无调用方），
  如以后新增该接口，同样调用清理函数。
- 平时：统一容量上限 + LRU 淘汰兜底。

## 4. 实施步骤（每步单独提交 + 推送，等开发者实测）

1. **统一缓存存储层**：新增 `cache_store.py`；现有 scalars / ranges / 变形家族 / 开发者四个目录全部迁入；
   去冗余（3.2 表）；删死代码；容量默认 100GB；统一开关与 `X-Cache`；旧目录清理。
2. **预计算改为每个 step 最后一帧**。
3. **color-code 分类缓存 + section 循环向量化 + 加入预计算**。
4. **element-mesh-edges 缓存 + 加入预计算**。
5. **frame-colors 接入结果缓存 + 加入预计算**。
6. **结果组重新解析时清理该组缓存目录**。
7. 文档同步：`L3-API-Quick-Reference.md`（更新时间行 + 缓存说明，接口本身不变）、`CLAUDE.md` 缓存段落、
   `Perf-Cache-Worklog.md`。

测试：按项目约定本机不跑 pytest，每步附带 `tests/` 用例由开发者本地执行；
可经 Windows 测试隧道 curl 验证 `X-Cache` 命中情况。

## 5. 进度

- [x] 第 1 步 统一缓存存储层（`services/cache_store.py`；测试 `test_l3_deform_cache.py`、`test_l3_disk_cache_warmup.py` 已同步）
- [x] 第 2 步 最后一帧预计算（`warmup_service._last_frame`：frames 表最大 frame_idx → steps.num_frames-1 → 0）
- [x] 第 3 步 color-code 分类缓存（`color_service._face_codes`；全部 scheme 逐面循环改 numpy，与旧实现逐项对拍一致；测试 `test_l3_color_code_cache.py`）
- [x] 第 4 步 element-mesh-edges 缓存（`services/mesh_edge_service.py`；响应头加 `X-Cache`；去重改一维 int64 键；测试 `test_l3_mesh_edges_cache.py`）
- [x] 第 5 步 frame-colors 缓存（set 过滤与着色前的 soup 顶点标量进统一缓存；预热用路由默认分量 U1）
- [x] 第 6 步 结果组重新解析清理（`job_runner` / `runner_thread` 的 `_cleanup_result_group` 在解析前删 `groups/<result_group>/`）
- [x] 补充 预热调度：导入即预计算 + 解析让路 + 跨进程文件锁（`warmup_service`、`main._poll_registry_once`、
  `registry_repo.has_active_tasks`；测试 `test_l3_warmup_scheduling.py`）
- [x] 第 7 步 文档同步（Quick-Reference 更新时间行与 warmup / element-mesh-edges / deformed-positions / modal-animation 段落、CLAUDE.md、Perf-Cache-Worklog、近期效率优化总览）

## 6. 验证情况（2026-10-09，本机等价脚本，非 pytest）

- 缓存 / 预热 / 变形 / color-code / 单元边线相关测试 + 其他调用到 result_service、color_service、
  results / geometry 路由的 L3 测试：77/79 通过。
- 未通过的 2 个在改动前的代码（e564bbe）上同样失败，与本次改动无关，待开发者本地确认：
  `test_l3_raw_results_route::test_raw_values_route_returns_json_by_default`、
  `test_l3_result_service::test_frame_scalars_maps_sparse_nodal_labels_back_to_geometry_rows`。
- color-code 新旧实现对拍：多材料 / 多截面 / 两种单元类型 / elset 跨 instance 的合成模型上，
  标签、颜色数组、图例、legend-entries（含 face/elem 计数、默认标题）、region mask 共 118 项逐项一致。
- 性能（合成 400 万面片）：etype 分类约 1s，section 分类约 3.8s（旧实现为逐面 Python 字典查找），
  首次算完后走缓存，导入后由预计算完成。

### 待开发者实测

1. `python3 -m pytest tests/ -v`（重点：`test_l3_disk_cache_warmup`、`test_l3_deform_cache`、
   `test_l3_color_code_cache`、`test_l3_mesh_edges_cache`、`test_color_*`）。
2. 用大模型导入后看日志 `Warmup[...]: N color-code, N mesh-edges, N combo(s) warmed ...`，
   确认预计算完成时间与 `l3_cache/` 占用。
3. 预热完成后首次打开云图 / 分类着色 / 单元边线是否秒开；`X-Cache` 响应头是否为 `hit`。
