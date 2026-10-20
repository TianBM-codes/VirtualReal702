# 解析、索引和首屏缓存的统一就绪判定

更新时间：2026-10-18

## 对外约定

新导入、追加结果组及 L2 重跑时，只有基础解析、高级索引、首屏缓存全部准备成功，才提交 `ready`。基础模型先完成不再提前发布。处理中保持原来的 `running`；L2 重跑保持 `l2_running`，旧版 ODB job 保持 `l2_running`。任何必需步骤失败，任务变为 `error`，具体原因可以从状态接口读取。

前端轮询 `GET /api/projects/{project_id}`：

- `data.geom_status=ready`：几何及当前已有结果的约定首屏缓存已经准备成功。
- `data.result_groups[].status=ready`：对应结果组已解析且首屏缓存准备成功。追加结果时仍须检查此字段，已有几何可能一直是 `ready`。
- 项目失败读取新增的 `data.error_message`；结果组失败读取既有的 `data.result_groups[].error_message`。
- `GET /api/projects`、`GET /api/projects/{project_id}/result-catalog` 也返回项目级 `error_message`。
- 查询成功仍返回 HTTP 200 / `code=200`，处理成功与否由状态字段决定。

项目失败示例（省略文件信息等既有字段）：

```json
{
  "code": 200,
  "data": {
    "project_id": "demo",
    "geom_status": "error",
    "error_message": "[首屏准备失败] 首屏缓存未完整保留，请检查磁盘空间、权限和 APP_SCALAR_DISK_CACHE_MB：...",
    "advanced_status": "ready",
    "result_groups": []
  },
  "message": ""
}
```

## 后端执行顺序

1. L1 写入原始几何和结果；L2 core 生成基础渲染数据。
2. L2 advanced 生成平均域、边线和空间索引，原子发布 H5，最后写 `advanced_status=ready`。高级阶段失败现在是任务失败。
3. ODB/RST 自带结果先登记为最终结果组名称。初始 `default_result` 在注册库保持 `running`，不能在缓存键确定前先标 `ready`。
4. runner 在最终状态写入函数中调用 `readiness_service.prepare_registered`。关闭注册库读事务后，加载本次工作区的局部 ModelIndex，检查每个实例的渲染和高级数据，并同步准备首屏缓存。
5. 成功后才提交注册库的 `ready`。初始 ODB/RST 的默认结果组和项目几何状态在同一事务中发布；不会顺便发布其他尚未解析的结果组。
6. 异常先持久化项目或结果组的 `error_message`，再向上抛出；外层 runner 保留具体异常原因。

覆盖独立 `src/job_runner.py` 和内嵌 `src/l3/infra/runner_thread.py`。L2 重跑和追加 ODB 引发的截面索引重建也需要经过屏障；索引重建失败不再视为非致命警告。L2 重跑成功会通知 Web 进程重新加载旧的驻留几何。

## 为什么不会互相等待

这一步是解析流水线的一部分，不走自动预热的 `_pending` 队列，也不检查“所有解析任务是否空闲”。局部索引直接读已经生成的 L2，不依赖项目先成为 `ready`，也不会触发 Web 全局注册表的加载回调。

与后台预热共用 `.warmup.lock`，保证同一工作区不重复预热。锁最多等待 300 秒，超时明确反馈错误；持锁计算不等待注册库的 `ready`。其他项目有排队任务不会阻止本项目完成首屏准备。

既有自动/手动预热仍然用于补算缓存，其“尽力而为、跳过无结果实例”的完成标记不作为本次屏障的成功依据。`APP_WARMUP=0` 只关闭自动后台预热，不能跳过导入完成前的必需准备。

## 首屏缓存的边界

- 模型几何：可用的颜色分类编码（不包括 elset）、单元网格边线。
- 每个结果组、每个 step 的最后一帧、每个字段：默认 `component_idx=None` 的标量和图例范围，以及旧 `frame-colors` 的默认 U1 分量中间结果。
- U 场：该帧的顶点位移和建议变形比例统计。
- 仅对 manifest 明确记录存在该字段结果块的实例计算；其他实例没有这个字段属于正常情况。声明有结果却读取失败、结果文件缺失、元数据缺失，都不能当作成功跳过。例外：结果块存在但值全为 NaN（例如实体实例上的面内/面外不变量 `*_MAX_INPLANE_PRINCIPAL`，L1 按 `_SHELL_ONLY_INVS` 写 NaN 置灰），读取层抛 `NotFoundError`，与 frame-scalars 接口返回空 payload 的语义一致，按“该实例无此量”跳过，不算失败；U 位移照常准备。
- 纯梁/质量点实例没有表面三角形，不需要表面云图或表面边线缓存；已有 U 数据的位移仍准备。

这不承诺所有帧、所有分量、所有用户选区或任意 scale/n_frames 的响应包均已缓存，也不代表浏览器已完成下载和绘制。默认标量沿用现有接口算法，不新增应力不变量的计算规则。

## 跨进程缓存与容量

独立 runner 的内存缓存不能被 Web 进程复用。因此严格准备必须验证磁盘缓存；仅内存里算过不能计为完成。`cache_store.require_persistent_cache` 使用线程上下文隔离严格模式，不改变普通接口的容错行为。严格模式忽略进程内命中，验证磁盘命中或写入，并在淘汰后检查本轮需要的文件仍全部存在。

`APP_SCALAR_CACHE_MB` 和 `APP_SCALAR_DISK_CACHE_MB` 必须均大于 0。关闭缓存、单项超过容量、总容量不足导致首屏条目被淘汰、写盘失败，都会反馈错误。可增大容量、腾出磁盘或修正权限后重试。

旧工作区不会在升级时统一重新解析，也不会仅因部署新版就批量修改原来的 `ready`；本约定在新导入、追加结果、L2 重跑时生效。旧格式没有 `advanced_status.json` 或独立高级索引时，需要通过 L2 重跑生成当前格式后才能经过严格屏障。

## 验证入口

针对性回归见 `tests/test_pipeline_readiness.py`，覆盖两种 runner 的提交顺序、失败持久化、查询接口、无数据实例、锁超时、写盘失败及容量淘汰。

```powershell
& 'E:/tools/anaconda3/python.exe' -X utf8 -m pytest tests/test_pipeline_readiness.py tests/test_l3_disk_cache_warmup.py tests/test_l3_deform_cache.py tests/test_l3_result_scalar_cache.py tests/test_l3_color_code_cache.py tests/test_l3_mesh_edges_cache.py tests/test_job_runner_op2_modal_import.py -q --basetemp=tmp/pytest-ready-本次唯一编号
```
