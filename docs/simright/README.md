# 数巧仿真数据调用、方向投影与测点匹配

本套程序面向数巧服务，依据项目中 `packages/apidoc-simright/DATA-API.md` 和 `packages/接口功能说明-仿真数据获取.xlsx` 整理。它与本项目已有的 `src/l3/services/simright_service.py` 兼容层独立，不修改现有服务路由。原始文档不随本次代码复制；生成的 `source-catalog.json` 保存来源 SHA-256、参数定义和 Excel 有效行。

## 交付文件

| 文件 | 用途 |
| --- | --- |
| `tools/simright/client.py` | HTTP 客户端、两种部署配置、任意 name 调用、转换任务调用 |
| `tools/simright/projection.py` | 应力张量和应变张量的指定方向投影 |
| `tools/simright/workflows.py` | 测点坐标匹配、匹配结果取值、六分量曲线获取与投影 |
| `tools/simright/__main__.py` | 命令行入口 |
| `simright.openapi.json` | Apifox 可导入的 OpenAPI 3.0.3 定义 |
| `simright.postman_collection.json` | Apifox 可导入的 Postman Collection 2.1，功能逐项独立展示 |
| `API-reference.md` | 各操作参数表、请求示例、响应结构索引、Excel 功能对应 |
| `examples/` | 可直接运行的离线投影、测点和请求配置示例 |

目前覆盖 DATA-API 文档中所有具名操作，并补充末节示例中的 `geometryParams`、创建 Session 及 Excel 的两个转换接口。客户端通过 `query(name, ...)` 调用全部分发操作；高频功能另外提供便捷方法。

## 安装与快速开始

在仓库根目录执行，运行环境为 Python 3.9+：

```powershell
python -m pip install -r tools/simright/requirements.txt
python -m tools.simright --help
python -m tools.simright project --input docs/simright/examples/stress-projection.json
python -m tools.simright project --input docs/simright/examples/strain-projection.json
```

上述两次投影不访问网络。应力示例输出 `normal_stress=80`；应变示例输出 `axial_strain=0.001`。

配置实际服务地址后再运行查询：

```powershell
$env:SIMRIGHT_BASE_URL = 'http://实际数巧服务:端口'
python -m tools.simright --profile markdown query loadcases --filename p/test/model.odb
python -m tools.simright --profile markdown query variables --filename p/test/model.odb --args docs/simright/examples/step-options.json
python -m tools.simright --profile excel query loadcases --filename 1-0
```

最后一条展示 Excel 部署的路径和文件参数转换；`loadcases` 出自 Markdown，能否在 Excel 部署使用，需要服务端支持。Excel 原有功能可直接调用 `XYCurveData1`、`nodeId` 等。

若服务有鉴权，可设置 `SIMRIGHT_AUTHORIZATION`（完整值，如 `Bearer ...`）或 `SIMRIGHT_COOKIE`；CLI 只把它们放入请求头，不写进交付文件。Python 也支持 `headers={...}` 和自定义 `requests.Session`。

```python
from tools.simright import SimRightClient

with SimRightClient('http://实际数巧服务:端口', profile='markdown') as api:
    steps = api.loadcases('p/test/model.odb')
    variables = api.variables('p/test/model.odb', loadcaseName='Step-1')
    curves = api.xy_curves('p/test/model.odb', [4747], 'S.EL.11',
                           loadcaseName='Step-1', xVarName='var_time', stateIds=[1, 2])
    # 其他操作统一入口，例如频率、单元信息、截图等：
    frequencies = api.query('freqValue', 'p/test/model.odb', method='order', range=[1, 3])
```

`query()` 检查 HTTP 状态、JSON 和业务码，返回原始 `data`，不统一解包成 `data.value`。原生 DATA-API 成功码为 **0**；本项目 L3 兼容层使用 **200**。若确实要访问本项目兼容层，显式配置 `success_codes=(200,)`，且仅调用该层已实现的操作；不能据此推断数巧原生接口返回 200。

## 两份来源的差异与待确认项

| 项目 | DATA-API.md | Excel / 处理方法 |
| --- | --- | --- |
| 数据接口路径 | 概述为 `/applications/3dlite/api/v1/query`，部分旧示例缺少 applications | Excel 为 `/applications/3dlite/api/v1/query/query`；分为 markdown/excel 两个 profile，不自动回退 |
| 模型参数 | `filename`，表示数巧已转换的 RDB 相对目录，可以保留 `.odb` 等名称 | Excel 为 `fileName`，示例是转换 uuid `1-0`；不把它当成客户端本地文件上传 |
| 文件转换 | 未定义转换接口 | Excel 给出 `/api/task/submit`、`/api/task/query`；未给 HTTP 方法、鉴权、响应和完成状态 |
| 中间主应变 | 变量由模型配置决定 | Excel 写 `E.EL.Midrincipal`，疑似笔误；生成物保留原样，使用前从 variables 确认，不静默改名 |
| 花片 a 通道 | 未给方向或安装定义 | Excel 将主应变与花片通道关联；主应变通常不等于固定安装方向的花片读数，应按下述张量公式投影 |
| 按颜色查 ID | 标题 `name=""`，示例为 `geometryParams` | 按示例收录，标记待服务端确认，不认定操作名已获验证 |
| modelDiff | 文字定义 data 为版本差异数组，返回示例却复制 assemble 对象 | 保留原始 data，不按错误示例强制反序列化 |
| Session | 表格写顶层 id/path，示例使用 scenes 数组；成功示例 code=1 | 按 scenes 示例提供调用，HTTP POST 为待确认约定，原样返回响应 |
| 数值样例 | 部分 JSON 缺逗号、带注释或拼写错误 | 请求示例修正 JSON 语法；不据此添加不存在的服务端路由 |

客户端允许单独指定 `query_path`、`filename_key`，CLI 对应 `--query-path`、`--filename-key`。两者按实际部署确认。转换任务方法可配置，未提供自动判定任务完成的轮询：

```python
with SimRightClient('http://实际数巧服务:端口', profile='excel') as api:
    submitted = api.submit_conversion('/home/file/a.odb', '1-0', method='POST')
    task = api.conversion_status('1-0', method='GET')
```

这里 POST 提交、GET 查询是调用约定，来源没有确认；应先与部署方核对。`modelPath` 是服务端可访问路径，不是自动上传。两次调用原样返回 JSON，不能把 HTTP 200 视为转换已经完成。

## Apifox 导入

在 Apifox 项目内选择“导入数据”，按需选择以下任一文件：

1. OpenAPI/Swagger → `docs/simright/simright.openapi.json`：保留真实 URL；共享入口通过 oneOf 和请求 examples 表达不同 name。
2. Postman → `docs/simright/simright.postman_collection.json`：建议用于逐项调试，原生接口、Excel 功能、六分量采集、Session 分组展示。

OpenAPI 不能在同一 path/method 下声明多条操作，所以没有伪造 `/query/nodeInfo` 等地址。Postman 中各请求仍发送到真实的共享入口。

导入后将 `baseUrl` 替换为实际地址，并填写 `filename`、`uuid`、`modelPath`、`loadcaseName` 和请求中的节点号。示例节点 1/2 和文件名均为占位值；变量名须先查 `variables`。删除模型、生成探针组和提交转换会改变服务状态，不应整组批量运行。

格式依据：[Apifox 导入 OpenAPI/Swagger](https://docs.apifox.com/doc-5174534)、[Apifox 支持的导入格式](https://apifox.com/help/api-manage/import-api/intro)。本次验证 JSON 与规范结构，未连接 Apifox 桌面执行实际导入。

## 指定方向的应力与应变

输入六分量：`11, 22, 33, 12, 13, 23`，按键取值，不依赖列表顺序。程序不根据三个主值重建张量，不将缺失分量自动补零。

令输入方向归一化为单位向量 n，对称应力张量为：

```text
σ = [[S11, S12, S13],
     [S12, S22, S23],
     [S13, S23, S33]]

t = σ n                             指定法向平面上的牵引力
σn = nᵀ σ n                         法向应力（保留正负号）
τ = t - σn n                        剪应力向量
|τ|                                 剪应力大小

σn = nx² S11 + ny² S22 + nz² S33
     + 2 nx ny S12 + 2 nx nz S13 + 2 ny nz S23
```

这里“方向应力”定义为法向应力，不等同于 `|σn|` 或 Von Mises。所有应力输出保持模型的应力单位。

指定方向的轴向应变为 `εn = nᵀ ε n`。若输入剪应变为工程剪应变 `γ12,γ13,γ23`，构造张量时需除以 2；应变计算要求显式指定 `shear_convention='engineering'` 或 `'tensor'`。Excel 没有定义 E.EL 的剪应变约定，不能仅凭变量名选择。

应变投影输出应变，不会自动变为应力。仅有应变而要计算应力时，还需要材料本构、弹性参数及温度/塑性等应变定义，本程序不额外假设这些条件。

```python
from tools.simright import project_stress, project_strain

stress = {'11': 100, '22': 40, '33': 10, '12': 10, '13': 0, '23': 0}
out = project_stress(stress, [1, 1, 0])
# normal_stress=80, traction≈[77.7817,35.3553,0], shear_stress=30

strain = {'11': 0.001, '22': 0.0002, '33': 0, '12': 0.0008, '13': 0, '23': 0}
out = project_strain(strain, [1, 1, 0], shear_convention='engineering')
# axial_strain=0.001；如需微应变，将结果乘 1e6
```

花片各通道应使用各自的安装方向，例如安装面上单位切向量 a、b 构成正交基，则角度 θ 的方向为 `n = cosθ a + sinθ b`。分别用 0°、45°、90° 的方向投影，得到三通道读数。方向应位于安装面内；本程序不自动从网格推断花片方向。

方向与张量必须处于相同坐标系。可令数巧统一返回全局分量，并提供全局方向；或者使用 `local_to_global=R`，其中 **R 的列是局部 x/y/z 轴在全局坐标系中的方向**，程序用 `n_local=Rᵀ n_global`。R 必须为右手正交旋转矩阵。壳单元局部坐标系可能逐单元变化，不能将同一个 R 用于所有单元。

### 获取六分量曲线并投影

```python
from tools.simright import SimRightClient, project_node_history

with SimRightClient('http://实际数巧服务:端口') as api:
    projected = project_node_history(
        api, 'p/test/model.odb', [4747], [1, 1, 0], kind='stress',
        loadcaseName='Step-1', stateIds=[1, 2], xVarName='var_time',
        coordSystemType=1, avgMethod='none', shellLayer='bottom')
```

默认应力变量为 `S.EL.11/22/33/12/13/23`；应变为 `E.EL.*`。这些是候选变量名，需以 `variables` 和模型配置为准，可用 `component_vars={'11':实际变量名,...}` 覆盖。应变另传 `kind='strain', shear_convention='tensor'` 或 `'engineering'`。

```powershell
python -m tools.simright --output projected.json project-history --input docs/simright/examples/history-projection.json
```

每次请求的工况、帧、坐标系、节点平均和壳层参数一致；程序按 `(partId,nodeId)` 和 x 轴对齐六分量。返回节点不全、重复曲线、时间轴长度/数值不一致、非有限值、重复或倒序 x 都会报错，不做截断或插值。重复时间帧应先按工况和 stateIds 消除歧义；复杂频响的实部/虚部未定义时不作实数张量计算。

`zero_components=['33','13','23']` 只适用于调用方明确确认的二维假设，例如平面应力；**平面应变一般不能把 S33 当成零**。原始三维计算若缺少分量会报错。返回数据包含原始分量、变量映射、假定零分量、请求选项和投影结果，便于核查。时间单位、应力单位、几何长度单位沿用模型，不自动换算。

## 如何获取节点与测点匹配结果

推荐流程：`测点坐标 → nodeId → 匹配距离检查 → nodeInfo / XYCurveData1 → 按测点编号关联`。

1. 测点需提供唯一 `sensorId` 和模型坐标系中的 `position`。先处理好测量坐标系到模型坐标系的刚体变换和长度单位。
2. 用 `nodeId` 的 `nodePosList` 查节点。原文返回包含 `nodeId/nodePos/stateId`，没有承诺批量顺序、距离阈值、零件唯一性或插值策略；程序每次只提交一个测点。返回多个候选时标记歧义。
3. 程序计算测点坐标与返回 `nodePos` 的欧氏距离。超过 `max_distance` 则标记未匹配，不继续取值。阈值单位必须与模型长度单位一致。
4. `nodeInfo` 按节点号读取指定帧的值。`XYCurveData1` 读取时程。`probeGroupByPos` 是创建可视化探针组，结果位于 `data.sprobes.probes` 和 `groups`，不是只读匹配的必要步骤。

```powershell
python -m tools.simright --output matches.json match --filename p/test/model.odb --sensors docs/simright/examples/sensors.json --max-distance 1.0 --options docs/simright/examples/match-options.json
python -m tools.simright --output parts.json query assemble --filename p/test/model.odb
python -m tools.simright --output sensor-values.json values --filename p/test/model.odb --matches matches.json --parts parts.json --var-name S.EL.11 --options docs/simright/examples/value-options.json
```

`--output` 等全局选项放在子命令之前。输出 `matches.json` 是数组，每条包含：

```json
{
  "sensorId": "P01",
  "position": [1, 2, 3],
  "nodeId": 178,
  "partId": null,
  "nodePos": [1.1, 2, 3],
  "distance": 0.1,
  "maxDistance": 1.0,
  "matched": true,
  "matchOptions": {"loadcaseName": "Step-1", "stateId": 1},
  "raw": {"nodeId": 178, "nodePos": [1.1, 2, 3], "stateId": 1}
}
```

找不到节点或超出阈值的条目也保留在数组中，包含 `matched=false` 和 `reason`；不丢弃未匹配测点。两个测点命中同一节点是允许的，保留各自的 sensorId。

Python 中读取匹配结果对应的数值：

```python
from tools.simright import SimRightClient, match_sensors, sensor_values

with SimRightClient('http://实际数巧服务:端口') as api:
    matches = match_sensors(api, 'p/test/model.odb', [
        {'sensorId': 'P01', 'position': [1, 2, 3]},
    ], max_distance=1.0, loadcaseName='Step-1', stateId=1)
    assembly = api.assemble('p/test/model.odb')
    part_ori_ids = {p['id']: p['oriId'] for p in assembly['parts']}
    results = sensor_values(api, 'p/test/model.odb', matches, 'S.EL.11',
                            part_ori_ids=part_ori_ids,
                            loadcaseName='Step-1', stateIds=[1, 2])
```

返回在原匹配条目上增加 `results`，保留各帧记录及 `values` 原数组，不对角点结果偷偷求平均。若要测点方向投影，先取匹配的 nodeId，再调用 `project_node_history()`；输出按 nodeId 关联回 sensorId。仅在节点号跨零件唯一时才能只用 nodeId；否则必须连同 partId 区分。

### 零件编号与匹配限制

- `assemble.parts[].id` 对应结果中的内部 `partId`；`assemble.parts[].oriId` 才用于 `entitiesIds`。程序不把两者混用。
- `nodeInfo` 文档支持 `entityType='Part', entitiesIds=[oriId]`。匹配返回 partId 时，`sensor_values()` 要求传入上面的映射；没有映射则报错。
- `nodeId` 示例可能不返回 partId。此时程序保留 null，再读取 nodeInfo。如果同一 nodeId 返回多个零件则报错；**即使只返回一条，也不能据此证明服务端已经排除了所有重号零件**。对重号模型，需由部署方提供可靠的零件限定匹配方式。
- `XYCurveData1` 没有文档化零件筛选参数。`sensor_history()` 遇到已知 partId 时默认报错；只有确认部署支持零件筛选扩展后，才可传 `allow_undocumented_part_scope=True` 和映射。不能依靠未确认的过滤参数解决重号问题。
- `nodePos` 与 `nodePosBase` 可能分别代表变形后/初始坐标。当前匹配距离使用 **nodeId 返回的 nodePos**。需确认指定工况、帧与测点坐标基准一致；程序不会自动假定 stateId=0 是初始帧。文档中帧号从 1 开始。
- 这是节点命中后的取值，不是单元内部空间插值。最近节点值与实际测点位置的插值值可能不同。

## 生成与验证

源文档保留在原 packages 路径时，可以重新生成接口文件：

```powershell
python -m pip install -r tools/simright/requirements-dev.txt
python -m tools.simright.generate_artifacts
python -m pytest tools/simright/tests -q
```

生成器支持 `--markdown`、`--excel`、`--output`。单独运行客户端只需 requests，不依赖 openpyxl、数巧桌面、Abaqus 或本项目数据库。

测试使用模拟 HTTP/业务响应，覆盖协议差异、失败响应、测点阈值、零件重号和映射、解析及六分量时间轴对齐，以及解析解和坐标旋转验证。未提供实际服务地址和鉴权，本次不声称已完成真实数巧服务联调，也未执行转换、删除或探针创建。
