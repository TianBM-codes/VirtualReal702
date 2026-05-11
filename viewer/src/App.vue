<template>
  <div style="display:flex;height:100vh;overflow:hidden;background:#0d1117;color:#c9d1d9;font-family:'Consolas','Menlo',monospace;font-size:13px">

    <!-- ── Left Panel ─────────────────────────────────────────────── -->
    <div style="width:260px;min-width:260px;background:#161b22;border-right:1px solid #30363d;display:flex;flex-direction:column;overflow-y:auto;padding:12px 10px;gap:10px">

      <h1 style="font-size:14px;color:#58a6ff;letter-spacing:1px;padding-bottom:6px;border-bottom:1px solid #30363d;margin:0">
        ⬡ ODB Viewer
      </h1>
      <div style="font-size:10px;color:#8b949e;margin-top:-8px;margin-bottom:4px">FastAPI + Three.js Backend</div>

      <ConnectionCard @connected="onConnected" />

      <OdbListCard ref="odbListRef" :visible="connected" @odb-selected="onOdbSelected" />

      <InstanceCard
        @load-geometry="insts => viewport?.loadGeometry(insts)"
        @load-mesh-edges="() => viewport?.loadEdges('mesh')"
        @load-features="() => viewport?.loadEdges('feature')"
      />
      <button
        @click="viewport?.toggleFaceOpacity()"
        style="background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:12px"
      >面透明切换</button>

      <ColorsCard @apply-colors="opts => viewport?.applyColors(opts)" />

      <DeformCard
        ref="deformCardRef"
        @apply="opts => viewport?.applyDeform(opts)"
        @reset="() => viewport?.resetDeform()"
        @play="opts => viewport?.startDeformAnim(opts)"
        @stop="() => viewport?.stopDeformAnim()"
      />

      <ColorCodeCard
        ref="colorCodeRef"
        @apply="opts => viewport?.applyColorCode(opts)"
        @clear="() => viewport?.clearColorCode()"
        @reset="() => viewport?.resetColorCode()"
        @region-highlight="opts => viewport?.loadRegionHighlight(opts.scheme, opts.regions, opts.types)"
        @clear-region-highlight="() => viewport?.clearRegionHighlight()"
      />

      <MouseModeCard ref="mouseModeRef" @toggle-camera="onToggleCamera" />

      <TransformCard
        @transform="elems => viewport?.applyModelTransform(elems)"
        @reset="() => viewport?.resetModelTransform()"
      />

      <ViewCutCard ref="viewCutRef" @update="state => viewport?.updateClipPlane(state)" />

      <ProbeTableCard />

      <NearestFaceCard
        @show-patch="onShowPatch"
        @clear-patch="() => { viewport?.clearPatchHighlight(); viewport?.clearNormalArrow() }"
      />

      <BboxCard :result="bboxResult" @clear="bboxResult = null" />
      <PickCard :result="pickResult" />
      <LegendCard :v-min="legendMin" :v-max="legendMax" :visible="legendMin != null" />
      <StatusBar />
    </div>

    <!-- ── 3D Viewport ─────────────────────────────────────────────── -->
    <ThreeViewport
      ref="viewport"
      @pick-result="r => { pickResult = r }"
      @bbox-result="r => { bboxResult = r }"
      @colors-loaded="({ vMin, vMax }) => { legendMin = vMin; legendMax = vMax }"
      @color-code-applied="({ legend }) => colorCodeRef?.setLegend(legend)"
      @model-loaded="onModelLoaded"
      @deform-anim-frame="f => deformCardRef?.onAnimFrame(f)"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useViewerStore } from './store/viewer'
import { useOdbApi }      from './composables/useOdbApi'

import ConnectionCard  from './components/ConnectionCard.vue'
import OdbListCard     from './components/OdbListCard.vue'
import InstanceCard    from './components/InstanceCard.vue'
import ColorsCard      from './components/ColorsCard.vue'
import DeformCard      from './components/DeformCard.vue'
import ColorCodeCard   from './components/ColorCodeCard.vue'
import MouseModeCard   from './components/MouseModeCard.vue'
import TransformCard   from './components/TransformCard.vue'
import ViewCutCard     from './components/ViewCutCard.vue'
import ProbeTableCard  from './components/ProbeTableCard.vue'
import NearestFaceCard from './components/NearestFaceCard.vue'
import BboxCard        from './components/BboxCard.vue'
import PickCard        from './components/PickCard.vue'
import LegendCard      from './components/LegendCard.vue'
import StatusBar       from './components/StatusBar.vue'
import ThreeViewport   from './components/ThreeViewport.vue'

const store  = useViewerStore()
const api    = useOdbApi()

const viewport     = ref(null)
const odbListRef   = ref(null)
const mouseModeRef = ref(null)
const viewCutRef   = ref(null)
const colorCodeRef = ref(null)
const deformCardRef = ref(null)

const connected  = ref(false)
const pickResult = ref(null)
const bboxResult = ref(null)
const legendMin  = ref(null)
const legendMax  = ref(null)

async function onConnected() {
  connected.value = true
  // 如果 localStorage 中有上次的 activeOdbId，恢复加载 meta
  if (store.activeOdbId) {
    store.setStatus(`恢复上次 ODB: ${store.activeOdbId.slice(0,8)}…`)
    try {
      const data = await api.fetchMeta()
      store.meta = data.data
      store.setStatus('Meta loaded，选择 Instance 后点 Load Geometry。', 'ok')
    } catch (_) {}
  }
  odbListRef.value?.refresh()
}

async function onOdbSelected(odbId) {
  store.setStatus('Loading meta…')
  try {
    const data = await api.fetchMeta()
    store.meta = data.data
    store.setStatus('Meta loaded，选择 Instance 后点 Load Geometry。', 'ok')
  } catch (e) {
    store.setStatus('Meta 加载失败: ' + e.message, 'err')
  }
}

function onToggleCamera() {
  const isOrtho = viewport.value?.toggleCamera()
  if (mouseModeRef.value) mouseModeRef.value.isOrtho = isOrtho
}

function onModelLoaded(info) {
  // 通知 ViewCutCard 更新滑块范围
  viewCutRef.value?.onModelLoaded(info)
}

function onShowPatch({ faceIndices, center, normal, halfW, halfH, origin }) {
  if (faceIndices) {
    viewport.value?.showPatchHighlight(faceIndices, center, normal, halfW, halfH)
  }
  if (origin) {
    // Nearest Face query: show orange normal arrow at closest point
    viewport.value?.showNormalArrow(origin, normal)
  }
}
</script>
