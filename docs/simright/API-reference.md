# 数巧接口参数索引

由 `python -m tools.simright.generate_artifacts` 生成。来源摘要见 source-catalog.json。

共同入口：`POST /applications/3dlite/api/v1/query`。原生成功码为 `0`。

请求体为 `{"name": 操作名, "args": 参数对象}`。下表保留来源说明；例子已修正原文 JSON 语法。

视图公共参数和 resultOptions 参数直接放在 args 中，与其他参数平级。

响应统一外层 `code/data/message`，data 的对象/数组结构随操作变化，不能统一取 data.value。

| 操作 | data 主要结构（依据原文） |
| --- | --- |
| nodeId | `[{nodeId,nodePos,stateId,...}]`；示例不含 partId |
| nodeInfo / elementInfo | `[{nodeId或elemId,partId,stateId,values,...}]`；values 可为数组 |
| XYCurveData1 | `[{nodeId,data:[[x,y],...],xVarName,yVarName}]` |
| probeGroupByPos | `{sprobes:{probes:[...],groups:[...]}}` |
| loadcases / freqValue | `{value:[...]}` |
| variables | `{variables:[...]}` |
| assemble | `{root,assms,parts:[{id,oriId,name,...}],sets}` |
| extremeValue | `{nodeId,nodePos,partId,oriId,stateId,value}` |
| contourImage / modelImage / animationGif / freqGif | `{value:Base64字符串}` |
| curveInfo / curveData | 树形数组 / 含 values 的数组 |
| hitEntities | `[{partids:[...],oriIds:[...]}]` |
| measureValue | 数值或数组，原文示例为数组 |
| deleteModelFile | 字符串 |
| modelDiff / geometryParams | 原文存在矛盾，见 README |

## extremeValue — 获取最值

来源：DATA-API.md 第 101 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| coordSystemType | Integer | 否 | 坐标系类型 | 1 全局，2 局部，默认全局 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| stateIds | Array | 否 | 帧数数组 | [1, 2, 3]，[] = 全部帧数，默认[]，[-1]-最后一帧 |
| rtype | String | 是 | 极值类型(min/max) | "max" |
| entityType | String | 否 | 对象类型 | Part/Set，默认 Part |
| entitiesIds | Array | 否 | 对象 id | []，[] = 所有零件，默认[] |
| entitiesNames | Array | 否 | 对象名称 | ["setName"]，当 entityType 是 Set 时，entitiesNames 是必填项，不支持空数组,目前只支持节点集 |
| loadcaseName | String | 否 | 工况名 | "Step-1"(默认第一个工况) |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |

```json
{
  "name": "extremeValue",
  "args": {
    "filename": "p/test/model.odb",
    "rtype": "max",
    "varName": "S.EL.VonMises",
    "stateIds": [
      -1
    ]
  }
}
```

## measureValue — 获取测量结果值

来源：DATA-API.md 第 174 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| measureType | Number | 是 | 对象类型 | 1: 距离,2: 角度,3: 相对角度测量 |
| entitiesIds | Array | 是 | 对象 ID | [{partId:1,nodeId: 123}, {...}] 以列表形式传参，返回两 node 间距离 |
| stateId | Integer | 是 | 帧数，全况中第几帧 | 1,2,3...， |
| baseStateId | Integer | 否 | 相对角度测量的基准帧 | 0 |
| dispScale | Number | 否 | 缩放系数 | 默认 1 |
| dispOpts | Object | 否 | 缩放系数 | 默认 { scaleX: 1, scaleY: 1, scaleZ: 1 } |

```json
{
  "name": "measureValue",
  "args": {
    "filename": "p/test/model.odb",
    "measureType": 1,
    "entitiesIds": [
      {
        "partId": 0,
        "nodeId": 1
      },
      {
        "partId": 0,
        "nodeId": 2
      }
    ],
    "stateId": 1
  }
}
```

## contourImage — 获取云图

来源：DATA-API.md 第 240 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| loadcaseName | String | 否 | 工况名 | "Step-1"(默认第一个工况) |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| stateId | Integer | 否 | 帧数，工况中第几帧 | 1,2,3...，默认最后一帧 |
| entityType | String | 否 | 对象类型 | "Part"/"Element"/"Set"/"Node"(默认"Part",仅支持"Part"/"Set") |
| entitiesIds | Array | 否 | 对象 ID（oriId） | [1,2,3]([]表示不显示,不设置显示全部) |
| entitiesNames | Array | 否 | 对象名称 | ['part'](不设置显示全部, 优先级低于 entitiesIds) |
| transparentEntities | Array | 否 | 设置透明的零件 | [1,2](默认[]) |
| partDisplayMode | Integer | 否 | 零件渲染模式 | 1:仅着色, 2:轮廓线, 3:着色+轮廓线, 4:网格线, 5:着色+网格线(默认 3) |
| cameraViewport | String | 否 | 标准视角 | "front"/"back"/"left"/"right"/"top"/"bottom"/"home"/"iso" 默认"home",可支持"front(0, 45, 0)"格式调整视角旋转。三个数字参数分别表示绕 x,y,z 轴的旋转角度。 |
| cameraZoomLevel | Number | 否 | 视口缩放系数，大于 1 放大，小于 1 缩小，值必须大于 0，默认 1 | "2": 放大两倍 |
| dispScale | Number | 否 | 缩放系数 | 默认 1 |
| dispOpts | Object | 否 | 缩放系数 | 默认 { scaleX: 1, scaleY: 1, scaleZ: 1 } |
| scalarEntities | Array<Number> | 否 | 设置显示云图的零件oriId | 默认显示全部 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| legendVisile | Number | 否 | 图例是否显示，默认显示 | 1-显示， 0-不显示，默认 1 |
| gProbeNode | Object | 否 | 生成探针的节点信息 | {"nsetNames": ["RESPONSE"]}，节点集名称 |
| annotations | Array<Object> | 否 | 批注信息 | [{"nodeId": 12333, "title": "这里是标题", "content": "这里是正文", "attached": true}, {"setName": "SET-BALL", "title": "节点标题", "content": "这里是正文", "attached": false}]，setName-节点集名称 |
| legendConfig | Object | 否 | 图例设置 | {"defaultColorMap": {"11": "#969696"},"legendValDisplayExponential": false }，defaultColorMap-默认颜色设置，如果是 12 色段，那最后一段的 key 是'11',key 就是段落索引，legendValDisplayExponential-是否是科学计数法 |
| displayExtremeProbe | Integer | 否 | 是否显示最值探针 | 0-不显示，1-全显示，2-仅显示 max，3-仅显示 min，默认 0 |
| sceneName | String | 否 | 自定义视图名称 | "test" |
| preserveScene | Boolean | 否 | 是否保存截图视图 | true/false(默认 false) |
| switchMaxFrame | Integer | 否 | 是否跳转到最大值探针所在帧, 支持extremeValue参数 | 0-不跳转，1-跳转，默认 0 |

```json
{
  "name": "contourImage",
  "args": {
    "filename": "p/test/model.odb",
    "varName": "S.EL.VonMises"
  }
}
```

## modelImage — 获取模型截图

来源：DATA-API.md 第 295 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| loadcaseName | String | 否 | 工况名 | "Step-1"(默认第一个工况) |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| stateId | Integer | 否 | 帧数，工况中第几帧 | 1,2,3...，默认最后一帧 |
| entityType | String | 否 | 对象类型 | "Part"/"Element"/"Set"/"Node"(默认"Part",仅支持"Part"/"Set") |
| entitiesIds | Array | 否 | 对象 ID（oriId） | [1,2,3]([]表示不显示,不设置显示全部) |
| entitiesNames | Array | 否 | 对象名称 | ['part'](不设置显示全部, 优先级低于 entitiesIds) |
| transparentEntities | Array | 否 | 设置透明的零件 | [1,2](默认[]) |
| partDisplayMode | Integer | 否 | 零件渲染模式 | 1:仅着色, 2:轮廓线, 3:着色+轮廓线, 4:网格线, 5:着色+网格线(默认 3) |
| cameraViewport | String | 否 | 标准视角 | "front"/"back"/"left"/"right"/"top"/"bottom"/"home"/"iso" 默认"home",可支持"front(0, 45, 0)"格式调整视角旋转。三个数字参数分别表示绕 x,y,z 轴的旋转角度。 |
| cameraZoomLevel | Number | 否 | 视口缩放系数，大于 1 放大，小于 1 缩小，值必须大于 0，默认 1 | "2": 放大两倍 |
| dispScale | Number | 否 | 缩放系数 | 默认 1 |
| dispOpts | Object | 否 | 缩放系数 | 默认 { scaleX: 1, scaleY: 1, scaleZ: 1 } |
| scalarEntities | Array<Number> | 否 | 设置显示云图的零件oriId | 默认显示全部 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| legendVisile | Number | 否 | 图例是否显示，默认显示 | 1-显示， 0-不显示，默认 1 |
| gProbeNode | Object | 否 | 生成探针的节点信息 | {"nsetNames": ["RESPONSE"]}，节点集名称 |
| annotations | Array<Object> | 否 | 批注信息 | [{"nodeId": 12333, "title": "这里是标题", "content": "这里是正文", "attached": true}, {"setName": "SET-BALL", "title": "节点标题", "content": "这里是正文", "attached": false}]，setName-节点集名称 |
| legendConfig | Object | 否 | 图例设置 | {"defaultColorMap": {"11": "#969696"},"legendValDisplayExponential": false }，defaultColorMap-默认颜色设置，如果是 12 色段，那最后一段的 key 是'11',key 就是段落索引，legendValDisplayExponential-是否是科学计数法 |
| displayExtremeProbe | Integer | 否 | 是否显示最值探针 | 0-不显示，1-全显示，2-仅显示 max，3-仅显示 min，默认 0 |
| sceneName | String | 否 | 自定义视图名称 | "test" |
| preserveScene | Boolean | 否 | 是否保存截图视图 | true/false(默认 false) |

```json
{
  "name": "modelImage",
  "args": {
    "filename": "p/test/model.odb",
    "stateId": 1
  }
}
```

## animationGif — 获取云图动画

来源：DATA-API.md 第 347 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| loadcaseName | String | 否 | 工况名 | "Step-1"(默认第一个工况) |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| stateId | Integer | 否 | 帧数，工况中第几帧 | 1,2,3...，默认最后一帧 |
| entityType | String | 否 | 对象类型 | "Part"/"Element"/"Set"/"Node"(默认"Part",仅支持"Part"/"Set") |
| entitiesIds | Array | 否 | 对象 ID（oriId） | [1,2,3]([]表示不显示,不设置显示全部) |
| entitiesNames | Array | 否 | 对象名称 | ['part'](不设置显示全部, 优先级低于 entitiesIds) |
| transparentEntities | Array | 否 | 设置透明的零件 | [1,2](默认[]) |
| partDisplayMode | Integer | 否 | 零件渲染模式 | 1:仅着色, 2:轮廓线, 3:着色+轮廓线, 4:网格线, 5:着色+网格线(默认 3) |
| cameraViewport | String | 否 | 标准视角 | "front"/"back"/"left"/"right"/"top"/"bottom"/"home"/"iso" 默认"home",可支持"front(0, 45, 0)"格式调整视角旋转。三个数字参数分别表示绕 x,y,z 轴的旋转角度。 |
| cameraZoomLevel | Number | 否 | 视口缩放系数，大于 1 放大，小于 1 缩小，值必须大于 0，默认 1 | "2": 放大两倍 |
| dispScale | Number | 否 | 缩放系数 | 默认 1 |
| dispOpts | Object | 否 | 缩放系数 | 默认 { scaleX: 1, scaleY: 1, scaleZ: 1 } |
| scalarEntities | Array<Number> | 否 | 设置显示云图的零件oriId | 默认显示全部 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| legendVisile | Number | 否 | 图例是否显示，默认显示 | 1-显示， 0-不显示，默认 1 |
| gProbeNode | Object | 否 | 生成探针的节点信息 | {"nsetNames": ["RESPONSE"]}，节点集名称 |
| annotations | Array<Object> | 否 | 批注信息 | [{"nodeId": 12333, "title": "这里是标题", "content": "这里是正文", "attached": true}, {"setName": "SET-BALL", "title": "节点标题", "content": "这里是正文", "attached": false}]，setName-节点集名称 |
| legendConfig | Object | 否 | 图例设置 | {"defaultColorMap": {"11": "#969696"},"legendValDisplayExponential": false }，defaultColorMap-默认颜色设置，如果是 12 色段，那最后一段的 key 是'11',key 就是段落索引，legendValDisplayExponential-是否是科学计数法 |
| displayExtremeProbe | Integer | 否 | 是否显示最值探针 | 0-不显示，1-全显示，2-仅显示 max，3-仅显示 min，默认 0 |
| sceneName | String | 否 | 自定义视图名称 | "test" |
| preserveScene | Boolean | 否 | 是否保存截图视图 | true/false(默认 false) |

```json
{
  "name": "animationGif",
  "args": {
    "filename": "p/test/model.odb",
    "varName": "S.EL.VonMises"
  }
}
```

## XYCurveData1 — 获取节点随时间相关的 XY 曲线值 1

来源：DATA-API.md 第 397 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| coordSystemType | Integer | 否 | 坐标系类型 | 1 全局，2 局部，默认全局 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| loadcaseName | String | 是 | 工况名 | Step-1 |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| nodeIds | Array | 是 | 节点 id 数组 | [12715, 23223] |
| yVarName | String | 是 | y 轴结果变量名称 | 根据用户 vkigen.json 配置里面的变量名称 |
| xVarName | String | 否 | x 轴结果变量名称 | 根据用户 vkigen.json 配置里面的变量名称 , 不传是根据当前工况使用时间 var_time 或者频率 var_frequency 或者模态 var_mode |
| stateIds | Array | 否 | 帧数数组 | [1, 2, 3]，[] = 全部零件，默认[] |

```json
{
  "name": "XYCurveData1",
  "args": {
    "filename": "p/test/model.odb",
    "nodeIds": [
      1
    ],
    "yVarName": "D.N.X",
    "xVarName": "var_time",
    "stateIds": [
      1
    ],
    "loadcaseName": "Step-1"
  }
}
```

## freqValue — 获取模态

来源：DATA-API.md 第 474 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| method | String | 是 | 根据 阶数/频率范围 截取模态 | order/freq |
| range | Array | 是 | 阶数范围 | [1,3] |
| modalIndex | Integer | 否 | 模态分析步 | 0，1，2，默认为 0，（仅在 method=order 是生效） |

```json
{
  "name": "freqValue",
  "args": {
    "filename": "p/test/model.odb",
    "method": "order",
    "range": [
      1,
      3
    ],
    "modalIndex": 0
  }
}
```

## freqGif — 获取模态动画

来源：DATA-API.md 第 532 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| loadcaseName | String | 否 | 工况名 | "Step-1"(默认第一个工况) |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| stateId | Integer | 否 | 帧数，工况中第几帧 | 1,2,3...，默认最后一帧 |
| entityType | String | 否 | 对象类型 | "Part"/"Element"/"Set"/"Node"(默认"Part",仅支持"Part"/"Set") |
| entitiesIds | Array | 否 | 对象 ID（oriId） | [1,2,3]([]表示不显示,不设置显示全部) |
| entitiesNames | Array | 否 | 对象名称 | ['part'](不设置显示全部, 优先级低于 entitiesIds) |
| transparentEntities | Array | 否 | 设置透明的零件 | [1,2](默认[]) |
| partDisplayMode | Integer | 否 | 零件渲染模式 | 1:仅着色, 2:轮廓线, 3:着色+轮廓线, 4:网格线, 5:着色+网格线(默认 3) |
| cameraViewport | String | 否 | 标准视角 | "front"/"back"/"left"/"right"/"top"/"bottom"/"home"/"iso" 默认"home",可支持"front(0, 45, 0)"格式调整视角旋转。三个数字参数分别表示绕 x,y,z 轴的旋转角度。 |
| cameraZoomLevel | Number | 否 | 视口缩放系数，大于 1 放大，小于 1 缩小，值必须大于 0，默认 1 | "2": 放大两倍 |
| dispScale | Number | 否 | 缩放系数 | 默认 1 |
| dispOpts | Object | 否 | 缩放系数 | 默认 { scaleX: 1, scaleY: 1, scaleZ: 1 } |
| scalarEntities | Array<Number> | 否 | 设置显示云图的零件oriId | 默认显示全部 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| legendVisile | Number | 否 | 图例是否显示，默认显示 | 1-显示， 0-不显示，默认 1 |
| gProbeNode | Object | 否 | 生成探针的节点信息 | {"nsetNames": ["RESPONSE"]}，节点集名称 |
| annotations | Array<Object> | 否 | 批注信息 | [{"nodeId": 12333, "title": "这里是标题", "content": "这里是正文", "attached": true}, {"setName": "SET-BALL", "title": "节点标题", "content": "这里是正文", "attached": false}]，setName-节点集名称 |
| legendConfig | Object | 否 | 图例设置 | {"defaultColorMap": {"11": "#969696"},"legendValDisplayExponential": false }，defaultColorMap-默认颜色设置，如果是 12 色段，那最后一段的 key 是'11',key 就是段落索引，legendValDisplayExponential-是否是科学计数法 |
| displayExtremeProbe | Integer | 否 | 是否显示最值探针 | 0-不显示，1-全显示，2-仅显示 max，3-仅显示 min，默认 0 |
| sceneName | String | 否 | 自定义视图名称 | "test" |
| preserveScene | Boolean | 否 | 是否保存截图视图 | true/false(默认 false) |
| order | Integer | 否 | 阶数 | 默认 1 |
| modalIndex | Integer | 否 | 模态分析步 | 0，1，2，默认为 0，（仅在 method=order 是生效） |

```json
{
  "name": "freqGif",
  "args": {
    "filename": "p/test/model.odb",
    "order": 1
  }
}
```

## loadcases — 获取工况列表

来源：DATA-API.md 第 584 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |

```json
{
  "name": "loadcases",
  "args": {
    "filename": "p/test/model.odb"
  }
}
```

## variables — 获取变量列表

来源：DATA-API.md 第 635 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| loadcaseName | String | 否 | 文件名 | "Modal Analysis 1" |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |

```json
{
  "name": "variables",
  "args": {
    "filename": "p/test/model.odb"
  }
}
```

## curveInfo — 获取曲线变量

来源：DATA-API.md 第 694 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "p/test/binout/binout0000" |
| dataPath | String | 否 | 请求加载的当前目录 | 默认为"/", 也可以指定为"/group1/group1.1"这样的路径 |
| level | Number | 否 | 加载 hdf5 目录结构的深度 | 默认为-1 ,递归完全的 tree |

```json
{
  "name": "curveInfo",
  "args": {
    "filename": "p/test/model.odb",
    "dataPath": "/",
    "level": -1
  }
}
```

## curveData — 获取曲线变量数值

来源：DATA-API.md 第 761 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "p/test/binout/binout0000" |
| dataPath | Array<String> | 是 | 变量列表 | ["/loadstep/glstat/Mem_avl_GB_node644", "/loadstep/glstat/time"]，是变量列表接口叶子节点的 path 字段，仅支持叶子节点上的变量 |

```json
{
  "name": "curveData",
  "args": {
    "filename": "p/test/model.odb",
    "dataPath": [
      "/loadstep/glstat/time"
    ]
  }
}
```

## hitEntities — 碰撞检测

来源：DATA-API.md 第 826 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "p/yangshuo/ER.odb" |
| stateId | Number | 是 | 测试模型所在帧 | 4 |
| box | Arra<Number> | 是 | 测试包围盒 | [0 ,0 , 0, 2040, 2099, 2305],参数说明前面三个为包围盒中心位置，后面三个为包围盒长宽高 |
| checkLevel | Number | 否 | 测试级别 | 默认 1， 0: 只测试零件包围盒 1: 测试零件顶点 |
| contained | bool | 否 | 是否包含 | 默认 false，部分包含 |

```json
{
  "name": "hitEntities",
  "args": {
    "filename": "p/test/model.odb",
    "stateId": 1,
    "box": [
      0,
      0,
      0,
      10,
      10,
      10
    ]
  }
}
```

## nodeId — 根据位置获取 nodeId

来源：DATA-API.md 第 869 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "p/test/ER.odb" |
| nodePosList | Number[][] | 是 | 位置坐标 | [[100,100,100]] |
| loadcaseName | String | 否 | 工况名 | "" |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| stateId | Number | 否 | 模型所在帧 | 4 |

```json
{
  "name": "nodeId",
  "args": {
    "filename": "p/test/model.odb",
    "nodePosList": [
      [
        1,
        2,
        3
      ]
    ],
    "stateId": 1
  }
}
```

## probeGroupByPos — 根据位置生成探针组

来源：DATA-API.md 第 912 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "p/test/ER.odb" |
| nodePosList | Number[][] | 是 | 位置坐标 | [[100,100,100]] |
| namePrefix | String | 否 | 探针名称前缀 | "test" |
| loadcaseName | String | 否 | 工况名 | "" |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| stateId | Number | 否 | 模型所在帧 | 4 |

```json
{
  "name": "probeGroupByPos",
  "args": {
    "filename": "p/test/model.odb",
    "nodePosList": [
      [
        1,
        2,
        3
      ]
    ],
    "namePrefix": "sensor",
    "stateId": 1
  }
}
```

## nodeInfo — 获取节点信息

来源：DATA-API.md 第 1015 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| coordSystemType | Integer | 否 | 坐标系类型 | 1 全局，2 局部，默认全局 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| stateIds | Array | 否 | 帧数数组 | [1, 2, 3]，[] = 全部帧数，默认[] |
| nodeIds | Array | 是 | 节点 ID | [1204] |
| loadcaseName | String | 否 | 工况名 | "" |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| entityType | String | 否 | 对象类型 | "Part"/"Element"/"Set"/"Node"(默认"Part",仅支持"Part"/"Set") |
| entitiesIds | Array | 否 | 对象 ID（oriId） | [1,2,3]([]表示不显示,不设置显示全部) |
| entitiesNames | Array | 否 | 对象名称 | ['part'](不设置显示全部, 优先级低于 entitiesIds) |

```json
{
  "name": "nodeInfo",
  "args": {
    "filename": "p/test/model.odb",
    "nodeIds": [
      1
    ],
    "varName": "S.EL.11",
    "stateIds": [
      1
    ]
  }
}
```

## elementInfo — 获取单元信息

来源：DATA-API.md 第 1091 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "/u/test1/model.odb" |
| varName | String | 否 | 结果变量名称 | 根据用户 vkigen.json 配置 |
| coordSystemType | Integer | 否 | 坐标系类型 | 1 全局，2 局部，默认全局 |
| coordSystemName | String | 否 | 坐标系名称 | "坐标系 1","global","analysis"， 优先级低于 coordSystemId |
| coordSystemId | String | 否 | 坐标系 Id | "1","global","analysis" |
| useCornerData | Integer | 否 | 角点数据 | 0:不用角点数据, 1:用角点数据(默认 0) |
| shellLayer | String | 否 | 壳单元层 | "bottom"/"top"/n(第 n 层)(默认"bottom") |
| nodeAverage | Float | 否 | 节点平均值 | 0~1 区间(默认 0.75) |
| avgMethod | String | 否 | 节点平均方法 | 'none' \| 'simple' \| 'max' \| 'min'，默认'none' |
| avgRegion | String | 否 | 节点平均范围 | 'model' \| 'part' ,默认'model' |
| avgOrder | String | 否 | 节点平均计算顺序 | 'pre' \| 'post' ,默认'pre' |
| stateIds | Array | 否 | 帧数数组 | [1, 2, 3]，[] = 全部帧数，默认[] |
| elemIds | Array | 是 | 单元 ID | [12] |
| loadcaseName | String | 否 | 工况名 | "" |
| loadcaseIndex | Integer | 否 | 工况索引 | 0，1，2...(默认第一个工况) ,优先级大于 loadcaseName |
| entityType | String | 否 | 对象类型 | "Part"/"Element"/"Set"/"Node"(默认"Part",仅支持"Part"/"Set") |
| entitiesIds | Array | 否 | 对象 ID（oriId） | [1,2,3]([]表示不显示,不设置显示全部) |
| entitiesNames | Array | 否 | 对象名称 | ['part'](不设置显示全部, 优先级低于 entitiesIds) |

```json
{
  "name": "elementInfo",
  "args": {
    "filename": "p/test/model.odb",
    "elemIds": [
      1
    ],
    "varName": "S.EL.11",
    "stateIds": [
      1
    ]
  }
}
```

## assemble — 获取装配树信息

来源：DATA-API.md 第 1172 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | String | 是 | 文件名 | "p/test/ER.odb" |

```json
{
  "name": "assemble",
  "args": {
    "filename": "p/test/model.odb"
  }
}
```

## modelDiff — 获取对比结果

来源：DATA-API.md 第 1269 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| rdb1 | Object | 是 | 模型1信息 | {"rdbPath": "p/AT/convert/CAE_model/bdf/t1/Job-1000-1.inp" } |
| rdb2 | Object | 是 | 模型2信息 | {"rdbPath": "p/AT/convert/CAE_model/bdf/t1/Job-1000-2.inp" } |

```json
{
  "name": "modelDiff",
  "args": {
    "rdb1": {
      "rdbPath": "p/test/model1.odb"
    },
    "rdb2": {
      "rdbPath": "p/test/model2.odb"
    }
  }
}
```

## deleteModelFile — 删除指定模型文件

来源：DATA-API.md 第 1456 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | string | 是 | 模型参数 | 见[视图公共参数](#视图公共参数) |

```json
{
  "name": "deleteModelFile",
  "args": {
    "filename": "p/test/model.odb"
  }
}
```

## geometryParams — 根据颜色查询ids

来源：DATA-API.md 第 1498 行。

| 参数 | 类型 | 必填 | 说明 | 示例/约定 |
| --- | --- | --- | --- | --- |
| filename | string | 是 | 模型参数 | 见[视图公共参数](#视图公共参数) |
| colors | Array<Integer> | 是 | RGB颜色列表 | 单个颜色：[255,0,0]， 多个颜色：[255,0,0,255,55,55]每3个代表一个颜色 |
| type | string | 否 | 查询类型 | 'part','face'，默认是'part' |

```json
{
  "name": "geometryParams",
  "args": {
    "filename": "p/test/model.odb",
    "type": "face",
    "colors": [
      255,
      0,
      0
    ]
  }
}
```

## Excel 功能对应

查询入口：`POST /applications/3dlite/api/v1/query/query`，模型参数为 `fileName`。

| 行号 | 功能 | 请求 |
| --- | --- | --- |
| 2 | 获取仿真数据节点号 | `{
"name": "nodeId",
"args": {
"fileName": "1-0",
"nodePosList": [[1,2,3]]}
}` |
| 3 | 获取温度数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "TEMP.EL"
 }
}` |
| 4 | 获取位移u1数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "D.N.X"
 }
}` |
| 5 | 获取位移u2数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "D.N.Y"
 }
}` |
| 6 | 获取位移u3数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "D.N.Z"
 }
}` |
| 7 | 获取应变Max_princioal对应花片a通道数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "E.EL.MaxPrincipal"
 }
}` |
| 8 | 获取应变Mid_princioal对应花片a通道数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "E.EL.Midrincipal"
 }
}` |
| 9 | 获取应力 s11数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "S.EL.11"
 }
}` |
| 10 | 获取应力 s22数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "S.EL.22"
 }
}` |
| 11 | 获取热流数据 | `{
 "name": "XYCurveData1",
 "args": {
  "fileName": "1-0",
  "nodeIds": [
   1,
   2,
   3
  ],
  "xVarName": "var_time",
  "yVarName": "HEAT_FLUX.EL.Mag"
 }
}` |
| 12 | 删除模型文件 | `{
 "name": "deleteModelFile",
 "args": {
  "fileName": "1-0"
 }
}` |
| 13 | 提交模型转换任务 | `{"modelPath":"/home/file/a.odb","uuid":"1-0"}` |
| 14 | 查询模型转换任务 | `uuid=1-0` |
