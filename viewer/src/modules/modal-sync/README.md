# modal-sync — 试验网格 / FEM 模型同屏同步动画模块

独立模块,**不接入主 viewer 的任何现有逻辑**,面向"要往别处集成"的前端:
整个 `modal-sync/` 目录可直接拷贝到其他项目,只依赖 `three`(ES Module),
不依赖 Vue / pinia / axios。

## 文件

| 文件 | 作用 |
|---|---|
| `api.js` | 后端调用:syncAnimation(JSON)、FEM modal-animation(二进制)、meta/overview、render-buffers;内含独立的 L3BE 解析器 |
| `ModalSyncPlayer.js` | 播放器:构建试验网格(测点 Points + 线框 LineSegments)、绑定/自建 FEM 网格、共享帧计数器驱动两侧 buffer |
| `demo/main.js` + `viewer/modal-sync-demo.html` | 联调 demo 页,`npm run dev` 后浏览 `/modal-sync-demo.html`,与主 viewer 互不影响 |

## 同步原理(与后端配套,详见 docs/l3/L3-API-Quick-Reference.md §13)

1. **幅度**:`POST /api/model/testMesh/syncAnimation` 把两侧放大倍数统一到同一个
   目标最大变形量(两模型包围盒并集最大边 / 10 / coefficient)。试验侧返回的
   `test.frames` 已乘好;FEM 侧用返回的 `fem.scale` 去请求
   `GET /api/odb/{projectId}/results/modal-animation`。
2. **相位**:两侧都是 `n_frames` 帧、第 i 帧相位 `sin(2πi/n)` 的循环序列。
   播放器用**同一个帧计数器**同时写两边的 position buffer → 严格逐帧同步。

## 快速上手

```js
import { ModalSyncPlayer } from './modal-sync/ModalSyncPlayer.js'

const player = new ModalSyncPlayer({
  baseUrl: 'http://127.0.0.1:5000',
  projectId: '18',
  resultGroup: null,          // OP2 结果组名,单结果组时可不传
})
scene.add(player.group)

const info = await player.load({ order: 1 })   // 试验阶次;FEM 帧缺省 = order-1
player.play(1.0)                               // 每秒 1 个周期
// player.pause() / player.stop()(复位未变形) / player.dispose()
```

`load()` 返回接口原始响应 `{ test, fem, sync }`;`player.warnings` 里是
非致命问题(某实例顶点数不匹配、FEM 不可用原因等)。

## 三种集成形态

**A. 全自建(demo 用法)** — `femInstances: 'auto'`(默认):播放器自己拉
实例列表 + render-buffers 建 FEM 网格。适合独立页面。

**B. 驱动宿主已有的 FEM 网格(推荐)** — 宿主页面已经在渲染 FEM 模型时,
把宿主的 `BufferGeometry` 交给播放器,不重复建模:

```js
await player.load({
  order: 1,
  femInstances: [
    { instance: 'PART-1-1', geometry: hostFemMesh.geometry },
  ],
})
```

要求宿主 geometry 与 L3 `render-buffers` 是同一份 indexed 顶点 buffer
(顶点数一致);不一致的实例会跳过并记入 `warnings`。宿主网格分 chunk 时,
改用形态 C 自己散射。

**C. 宿主自己驱动时钟 / 自己写 buffer** — 宿主已有全局动画循环,或 FEM
顶点 buffer 结构特殊(分 chunk 等):

```js
await player.load({ order: 1, femInstances: [] })   // 只要试验侧
// 宿主自己按 fem.scale + 相同 n_frames 拉 FEM 帧(api.js 的 fetchFemModalFrames)
// 然后在宿主的循环里:
hostLoop(i => player.setFrame(i))                    // 试验侧跟着宿主的帧计数器走
// 或反过来:player.play(); player.onFrame(i => 宿主更新自己的 FEM buffer)
```

只要两侧用同一个 `i`,就是同步的——这是唯一的硬约束。

## 注意事项

- **单独显示试验模型**:FEM 数据没就绪时接口自动降级(`fem.available=false`),
  播放器照常显示/播放试验网格,scale 退化为按试验包围盒归一化。
- **换阶次**:直接再次 `await player.load({ order: n })`,旧几何/帧会自动释放。
- **法线**:自建 FEM 网格只在加载时算一次法线,播放中不重算(变形是小幅
  谐波,视觉差异可忽略;要精确可在 `onFrame` 里自行 `computeVertexNormals()`)。
- **坐标系**:两模型都在模型全局坐标系下,叠加不需要额外变换;试验网格
  使用 `depthTest: false` + `renderOrder` 保证叠加时线框不被 FEM 面片遮挡。
- **测试数据**:没有真实试验 UNV 时用 `tools/gen_test_unv_from_op2.py` 生成
  (现成样例 `tools/sol103_test.unv`,走 `POST /import/unv` 导入)。
