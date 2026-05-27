# L3 GPU 变形方案设计

**状态**：待实现  
**Three.js 版本**：r161  
**相关文件**：`src/l3/services/result_service.py`、`src/l3/api/routes/results.py`、`viewer/src/components/ThreeViewport.vue`、`viewer/src/composables/useOdbApi.js`

---

## 1. 动机

### 现有问题

当前变形渲染的链路：

```
切帧 / 调 scale → fetch 变形后坐标 → CPU scatter 到每个 chunk → needsUpdate → GPU 重绘
```

每次调整 scale 都必须重新请求服务端，拿到新坐标后再做 CPU scatter，响应慢、带宽浪费。

### 目标

```
切帧   → fetch 原始位移量 U → scatter 到 displacement attribute → GPU 重绘
调 scale → material.uniforms.uDeformScale.value = newScale    → GPU 立即重绘（无网络、无 CPU scatter）
```

scale 调整完全在 GPU 侧完成，零网络开销，零 CPU scatter。

---

## 2. 整体架构

### 数据流

```
后端 result_service
  └─ 返回原始位移量 U [Nv_global, 3] float32（不乘 scale）

前端 ThreeViewport（切帧时）
  ├─ scatter U 到每个 mesh chunk 的 displacement BufferAttribute
  └─ scatter U 到每条 edge LineSegments 的 displacement BufferAttribute

前端（调 scale 时）
  └─ sharedMaterial.uniforms.uDeformScale.value = scale   ← 仅此一行，GPU 侧生效
```

### 法向量

采用方案 A：接受 GPU 变形后法向量略偏（不重算）。可视化场景下变形量通常远小于模型尺寸，视觉误差可接受。

---

## 3. 后端改动

### `result_service.py` — `frame_deformed_positions()`

**改动**：去掉 `positions + scale * disp_vertex` 这一步，直接返回原始位移量。

```python
# 改前
deformed = (positions + np.float32(scale) * disp_vertex).astype(np.float32)
normals  = _compute_vertex_normals(deformed, indices) if indices is not None else ...
return deformed, normals

# 改后（函数改名为 frame_displacement）
disp_vertex = disp_node[vtx_nr]   # [Nv, 3] float32，不乘 scale
return disp_vertex
```

函数签名移除 `scale` 参数；法向量不再由服务端计算。

### `results.py` — 路由

- 端点路径保持 `GET /results/deformed-positions`（或改为 `GET /results/displacement`，前端同步）
- payload key 从 `positions` 改为 `displacements`
- 移除 `normals` 字段
- 移除 `scale` query 参数（scale 交给前端 uniform）
- Response header 移除 `X-Scale`

---

## 4. 前端 — Shader 注入

### 4.1 共享 Material 设置

所有 mesh chunk 共享同一个 material 实例（`sharedDeformMaterial`），所有 chunk 的 scale 相同，uniform 共享无冲突。

```js
function buildDeformMaterial(baseMaterial) {
  const mat = baseMaterial.clone()

  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uDeformScale = { value: 0.0 }

    // 在 vertex shader 顶部声明 attribute 和 uniform
    shader.vertexShader =
      'attribute vec3 displacement;\nuniform float uDeformScale;\n' +
      shader.vertexShader

    // 在 transformed 赋值之后叠加位移
    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
       transformed += displacement * uDeformScale;`
    )

    // 保存 uniform 引用供后续修改
    mat.userData.shader = shader
  }

  // 确保 shader 程序唯一，不与其他 mesh 共享编译缓存
  mat.customProgramCacheKey = () => 'l3-deform-v1'

  return mat
}
```

> **为什么用 `#include <begin_vertex>` 注入**：Three.js r161 的标准 vertex shader 在这里把 `position` 赋给 `transformed`，之后经过蒙皮、位移贴图等处理，最终 `project_vertex` 把 `transformed` 投影到裁剪空间。在 `begin_vertex` 之后叠加 `displacement` 是最简洁且兼容性最好的位置。

### 4.2 scale 更新

```js
function setDeformScale(scale) {
  const shader = sharedDeformMaterial.userData.shader
  if (shader) shader.uniforms.uDeformScale.value = scale
  requestRender()
}
```

整个操作只改一个 float，GPU 下一帧自动生效。

---

## 5. 前端 — Displacement Attribute 管理

### 5.1 初始化（几何体加载时）

每个 chunk geometry 初始化零位移 attribute，确保首次渲染前 shader 不报错：

```js
const zeroDisp = new Float32Array(chunkVertexCount * 3)  // 全 0
geo.setAttribute('displacement', new THREE.BufferAttribute(zeroDisp, 3))
```

### 5.2 切帧时（scatter 位移）

```js
async function _applyDeformDisplacements(step, frameIdx) {
  const res = await http.get(
    store.getApiUrl(`results/deformed-positions?instance=${inst}&step=${step}&frame=${frameIdx}`),
    { responseType: 'arraybuffer' }
  )
  const sections = parseL3BE(res.data)
  const globalDisp = new Float32Array(sections.displacements.data)  // [Nv_global * 3]

  for (const c of im.chunks) {
    const dispAttr = c.mesh.geometry.attributes.displacement
    const dispArr  = dispAttr.array
    const vgid     = c.vertexGlobalId
    for (let i = 0; i < vgid.length; i++) {
      const g = vgid[i]
      dispArr[i*3]     = globalDisp[g*3]
      dispArr[i*3 + 1] = globalDisp[g*3 + 1]
      dispArr[i*3 + 2] = globalDisp[g*3 + 2]
    }
    dispAttr.needsUpdate = true
  }
}
```

`im.globalPositions` 不再需要在变形时更新（它保留的是原始坐标，供 resetDeform 恢复用）。

### 5.3 重置变形

```js
function resetDeform() {
  setDeformScale(0.0)   // GPU 侧清零，不需要替换坐标
  // 或者也可以保留 displacement attribute，只需把 scale 归零
}
```

---

## 6. 边线（Feature Edges / Mesh Edges）

边线的 `LineSegments` 也需要 GPU 位移支持，否则 scale 调整时边线不跟随网格变形。

### 6.1 边线 displacement attribute 初始化

与 mesh chunk 相同，在 feature edge / mesh edge geometry 创建时加零 displacement attribute。

### 6.2 边线 vertexGlobalId 映射

边线端点本身就是顶点全局 ID 的切片（`featureEdgeVtxIdxs` / `meshEdgeVtxIdxs`），scatter 逻辑与 mesh chunk 完全一致：

```js
for (const [edgeLines, edgeVtxIdxs] of [
  [featureEdgesLines[instName], featureEdgeVtxIdxs[instName]],
  [meshEdgesLines[instName],    meshEdgeVtxIdxs[instName]],
]) {
  if (!edgeLines || !edgeVtxIdxs) continue
  const dispAttr = edgeLines.geometry.attributes.displacement
  const dispArr  = dispAttr.array
  for (let i = 0; i < edgeVtxIdxs.length; i++) {
    const g = edgeVtxIdxs[i]
    dispArr[i*3]     = globalDisp[g*3]
    dispArr[i*3 + 1] = globalDisp[g*3 + 1]
    dispArr[i*3 + 2] = globalDisp[g*3 + 2]
  }
  dispAttr.needsUpdate = true
}
```

边线 material 同样需要注入相同 shader patch（或共用 `sharedDeformMaterial`，若材质类型兼容）。

### 6.3 移除 `_syncEdgesForInst`

原来的边线 CPU 同步函数（直接替换 position array）在新方案下不再需要，可以删除。

---

## 7. BVH / 拾取策略

GPU 位移不更新 CPU 侧坐标，BVH 无法感知变形状态，拾取精度在变形时会偏移。

**策略**：**变形激活时禁用拾取，仅在 scale=0（或切回未变形状态）时重建 BVH 并开启拾取。**

```js
function setDeformScale(scale) {
  const shader = sharedDeformMaterial.userData.shader
  if (shader) shader.uniforms.uDeformScale.value = scale

  if (scale === 0) {
    _rebuildBvhAll()         // 恢复原始形态后重建
    store.pickEnabled = true
  } else {
    store.pickEnabled = false  // 变形状态禁用拾取
  }
  requestRender()
}
```

切帧时（displacement 更新但 scale 不变）：不重建 BVH，拾取仍禁用。

---

## 8. 实现 Checklist

### 后端
- [ ] `result_service.py`：`frame_deformed_positions` → 改为返回原始 U，移除 scale 参数和 normals 计算
- [ ] `results.py`：payload key 改为 `displacements`，移除 `normals` 和 `scale` query 参数

### 前端
- [ ] `useOdbApi.js`：`fetchDeformedPositions` 移除 `scale` 参数
- [ ] `ThreeViewport.vue`：
  - [ ] `buildDeformMaterial()` — 创建带 `onBeforeCompile` 的 material，注入 displacement attribute + uDeformScale uniform
  - [ ] mesh chunk 初始化时加零 displacement attribute，替换为 `sharedDeformMaterial`
  - [ ] feature edge / mesh edge geometry 初始化时加零 displacement attribute，注入相同 shader
  - [ ] `_applyDeformDisplacements()` — 替换现有 `_applyDeformPositions()`，scatter displacement 到 mesh chunk + edge geometry
  - [ ] `setDeformScale()` — 只改 uniform，控制 pickEnabled 和 BVH 重建
  - [ ] `resetDeform()` — 改为 `setDeformScale(0)`
  - [ ] 删除 `_syncEdgesForInst()`
  - [ ] `applyDeform()` 入口改为先 `setDeformScale(scale)` 再 `_applyDeformDisplacements(step, frameIdx)`
