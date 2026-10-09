# L3 预计算缓存方案（导入即算好、接口直接走缓存）

> 更新时间：2026-10-09。状态：方案已确认，按第 4 节步骤逐步实施，每步完成后更新第 5 节进度。
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
- 统一在响应头返回 `X-Cache: hit|miss`。

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

- [ ] 第 1 步 统一缓存存储层
- [ ] 第 2 步 最后一帧预计算
- [ ] 第 3 步 color-code 分类缓存
- [ ] 第 4 步 element-mesh-edges 缓存
- [ ] 第 5 步 frame-colors 缓存
- [ ] 第 6 步 结果组重新解析清理
- [ ] 第 7 步 文档同步
