# ODB Viewer — Vue 3 前端

> viewer.html 的 Vue 3 组件化版本，对应后端 L3 服务。

## 快速开始

```bash
cd viewer
npm install
npm run dev        # 开发模式，默认 http://localhost:5173
npm run build      # 生产构建，输出到 dist/
```

## 项目结构

```
viewer/
├── src/
│   ├── main.js                  # 入口，挂载 Pinia
│   ├── App.vue                  # 总布局
│   ├── store/
│   │   └── viewer.js            # 全局状态（Pinia）
│   ├── composables/
│   │   ├── useL3BE.js           # L3BE 二进制 payload 解析
│   │   └── useOdbApi.js         # 所有 L3 REST API 调用封装
│   └── components/
│       ├── ThreeViewport.vue    # Three.js 视口（几何/颜色/pick/hover/bbox/剖面/坐标轴）
│       ├── ConnectionCard.vue   # API URL + Connect
│       ├── OdbListCard.vue      # ODB 文件列表 + 提交表单 + 轮询
│       ├── InstanceCard.vue     # 多实例勾选 + 加载按钮
│       ├── ColorsCard.vue       # 结果云图（Field / Component / Step / Frame）
│       ├── ColorCodeCard.vue    # 颜色方案（Element Type / Material / Elset）
│       ├── MouseModeCard.vue    # Navigate / Pick 切换 + Deform Scale
│       ├── ViewCutCard.vue      # 剖面切割（世界坐标滑块）
│       ├── ProbeTableCard.vue   # 多行采样记录表
│       ├── NearestFaceCard.vue  # 最近面查询 + 矩形选取子面板
│       ├── BboxCard.vue         # 框选结果
│       ├── PickCard.vue         # 单击拾取结果
│       ├── LegendCard.vue       # 彩虹色谱条
│       └── StatusBar.vue        # 全局状态提示
├── index.html
├── vite.config.js
└── package.json
```

## 使用说明

1. 启动后端 L3 服务（默认端口 8100，已通过 OpenResty 反代到 `/api/odb/`）
2. 打开页面，在 **Connection** 卡片填写 API Base URL，点击 Connect
3. 从 **ODB 文件** 列表中选择已就绪（ready）的模型
4. 在 **Instance** 卡片勾选实例，点击 Load Geometry
5. 后续操作：Apply Colors / View Cut / Pick / Box Select 等

## 与 tools/viewer.html 的对应关系

| Vue 组件 | viewer.html 对应代码 |
|---|---|
| `ThreeViewport.vue` | `initThree` / `animate` / `fitCamera` / pick / hover / bbox / viewcut / patch |
| `composables/useL3BE.js` | `parseL3BE()` |
| `composables/useOdbApi.js` | 所有 `fetch(getApiUrl(...))` 调用 |
| `store/viewer.js` | 顶部所有 `let` 全局变量 |
| 各 `*Card.vue` | 左侧面板各 `.card` 区块 |

## 依赖

- [Vue 3](https://vuejs.org/) + [Pinia](https://pinia.vuejs.org/)
- [Three.js](https://threejs.org/) r165
- [Vite](https://vitejs.dev/) v8
