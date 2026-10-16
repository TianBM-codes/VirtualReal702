<template>
  <main style="height:100vh;display:flex;background:#0d1117;color:#c9d1d9;font:13px sans-serif">
    <aside style="width:330px;min-width:330px;padding:16px;overflow:auto;box-sizing:border-box;overflow-wrap:anywhere">
      <h1 style="font-size:18px">ODB Viewer 页面加载测试</h1>
      <p>复用已解析测试项目，串联现有轮廓和表面加载函数。</p>
      <label>API地址 <input v-model="apiBase" style="width:100%;box-sizing:border-box" /></label>
      <button @click="connect" :disabled="busy">读取已有项目</button>
      <p><label>测试项目 <select v-model="projectId" aria-label="测试项目" style="max-width:100%">
        <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_id }}</option>
      </select></label></p>
      <button @click="openModel" :disabled="busy || !projectId || report.complete">打开模型并计时</button>
      <p v-if="report.complete">重复测量请刷新页面，清空上次的模型画面。</p>
      <p role="status">{{ status }}</p>
      <p>帧计时：绘制提交、GPU完成后，下一个浏览器帧。</p>
      <table style="width:100%;font-size:12px">
        <tbody><tr v-for="(value, name) in report.marks" :key="name"><td>{{ name }}</td><td>{{ value.toFixed(1) }} ms</td></tr></tbody>
      </table>
      <p v-if="report.gpu">GPU：{{ report.gpu }}</p>
      <button @click="downloadReport" :disabled="busy || !report.complete">下载计时JSON</button>
      <button @click="verifyDeform" :disabled="busy || !report.complete">验证变形和恢复</button>
      <label><input type="checkbox" v-model="pickEnabled" @change="store.mouseMode = pickEnabled ? 'pick' : 'nav'" />拾取单元</label>
      <pre data-testid="pick-report">{{ JSON.stringify(lastPick) }}</pre>
      <pre data-testid="deform-report">{{ JSON.stringify(deformReport, null, 2) }}</pre>
      <pre data-testid="load-report" style="font-size:10px;white-space:pre-wrap">{{ JSON.stringify(report, null, 2) }}</pre>
    </aside>
    <ThreeViewport ref="viewport" @pick-result="lastPick = $event" />
  </main>
</template>

<script setup>
import { provide, ref, shallowRef } from 'vue'
import ThreeViewport from '../components/ThreeViewport.vue'
import { useViewerStore } from '../store/viewer'
import http from '../utils/request'

const store = useViewerStore()
const viewport = ref(null)
const apiBase = ref('http://127.0.0.1:5099')
const projects = ref([])
const projectId = ref('')
const busy = ref(false)
const status = ref('先读取现有测试项目。加载只用GET；拾取用POST查询，不创建或解析项目。')
const pickEnabled = ref(false)
const lastPick = ref(null)
const deformReport = shallowRef(null)
let syncSnapshots = []
const report = shallowRef({ marks: {}, durations: {}, complete: false })
let started = 0
let generation = 0
let frameWaiters = {}
let pendingFrames = new Set()
let renderedFrames = new Set()

function update(values) { report.value = { ...report.value, ...values } }
function mark(name, detail) {
  if (!started) return
  update({ marks: { ...report.value.marks, [name]: performance.now() - started } })
  if (detail) update({ [name + '_detail']: detail })
}
function duration(name, value) {
  if (!started) return
  update({ durations: { ...report.value.durations, [name]: (report.value.durations[name] || 0) + value } })
}
function afterRender(renderer) {
  if (!started) return
  const gl = renderer.getContext()
  if (!report.value.gpu) {
    const extension = gl.getExtension('WEBGL_debug_renderer_info')
    update({ gpu: extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      viewport: { width: renderer.domElement.width, height: renderer.domElement.height, pixelRatio: renderer.getPixelRatio() } })
  }
  for (const phase of ['outline', 'surface']) {
    if (report.value.marks[phase + '_ready'] == null || pendingFrames.has(phase) || renderedFrames.has(phase)) continue
    const token = generation
    pendingFrames.add(phase)
    const fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0)
    gl.flush()
    const deadline = performance.now() + 60000
    function pollGpu() {
      if (token !== generation) { if (fence) gl.deleteSync(fence); return }
      const state = fence ? gl.clientWaitSync(fence, 0, 0) : gl.CONDITION_SATISFIED
      if (state === gl.WAIT_FAILED || performance.now() > deadline) {
        if (fence) gl.deleteSync(fence)
        frameWaiters[phase]?.reject(new Error(phase + ' GPU fence failed or timed out'))
        return
      }
      if (state === gl.TIMEOUT_EXPIRED) { requestAnimationFrame(pollGpu); return }
      if (fence) gl.deleteSync(fence)
      requestAnimationFrame(() => {
        if (token !== generation) return
        renderedFrames.add(phase)
        mark(phase + '_presented')
        frameWaiters[phase]?.resolve()
      })
    }
    requestAnimationFrame(pollGpu)
  }
}
function edgesSynced({ instance, positions, originalPositions, edges, originalEdges, indices }) {
  let moved = 0, nonfinite = 0, edgeMismatch = 0, edgeMoved = 0, resetError = 0
  for (let i = 0; i < positions.length; i++) {
    if (!Number.isFinite(positions[i])) nonfinite++
    if (positions[i] !== originalPositions[i]) moved++
  }
  for (let i = 0; i < edges.length; i++) {
    if (edges[i] !== positions[indices[Math.floor(i / 3)] * 3 + i % 3]) edgeMismatch++
    if (Math.abs(edges[i] - originalEdges[i]) > 1e-4) edgeMoved++
    resetError = Math.max(resetError, Math.abs(edges[i] - originalEdges[i]))
  }
  syncSnapshots.push({ instance, movedCoordinates: moved, nonfinite, edgeMismatch,
    edgeMovedCoordinates: edgeMoved, maxOriginalEdgeDifference: resetError, endpoints: indices.length })
}
provide('viewerLoadDiagnostics', { mark, duration, afterRender, edgesSynced })

async function verifyDeform() {
  busy.value = true
  deformReport.value = { complete: false, phases: [] }
  try {
    const group = projects.value.find(p => p.project_id === projectId.value)?.result_groups?.find(g => g.status === 'ready')
    if (!group) throw new Error('没有已就绪结果组')
    store.setActiveOdb(projectId.value, group.result_group)
    store.meta = (await http.get(store.getApiUrl('meta/overview'))).data
    const step = store.meta.steps.find(s => s.num_frames > 1) || store.meta.steps[0]
    if (!step) throw new Error('没有结果步')
    const parameters = { step: step.step_name, frameIdx: step.num_frames - 1, scale: 1 }
    for (const phase of ['first_deform', 'repeat_deform', 'reset']) {
      status.value = '验证：' + phase
      syncSnapshots = []
      const start = performance.now()
      if (phase === 'reset') viewport.value.resetDeform()
      else await viewport.value.applyDeform(parameters)
      if (store.statusType === 'err' || !syncSnapshots.length) throw new Error(store.statusMsg)
      const failed = syncSnapshots.some(s => s.nonfinite || s.edgeMismatch ||
        (phase === 'reset' ? s.movedCoordinates || s.maxOriginalEdgeDifference > 1e-4 : !s.movedCoordinates || !s.edgeMovedCoordinates))
      if (failed) throw new Error('变形/轮廓同步校验失败：' + JSON.stringify(syncSnapshots))
      deformReport.value = { ...deformReport.value,
        phases: [...deformReport.value.phases, { phase, durationMs: performance.now() - start, snapshots: syncSnapshots }] }
    }
    deformReport.value = { ...deformReport.value, complete: true, parameters, resultGroup: group.result_group }
    status.value = '变形、重复变形及恢复校验通过。'
  } catch (error) {
    deformReport.value = { ...deformReport.value, error: String(error) }
    status.value = String(error)
  } finally { busy.value = false }
}

async function connect() {
  busy.value = true
  try {
    store.baseUrl = apiBase.value.replace(/\/+$/, '')
    const response = await http.get(store.baseUrl + '/api/projects')
    projects.value = (response.data?.projects || response.data || []).filter(p => p.project_id?.startsWith('bench_') && p.geom_status === 'ready')
    projectId.value = projects.value[0]?.project_id || ''
    status.value = projects.value.length ? '已有项目已读取，可直接打开。' : '没有已就绪的bench_测试项目。'
  } catch (error) { status.value = error.message }
  finally { busy.value = false }
}
async function openModel() {
  if (busy.value) return
  busy.value = true
  generation++
  started = performance.now()
  frameWaiters = {}
  pendingFrames = new Set()
  renderedFrames = new Set()
  report.value = { projectId: projectId.value, startedAt: new Date().toISOString(),
    visibility: document.visibilityState, userAgent: navigator.userAgent,
    marks: { click: 0 }, durations: {}, complete: false }
  const outlineFrame = new Promise((resolve, reject) => { frameWaiters.outline = { resolve, reject } })
  const surfaceFrame = new Promise((resolve, reject) => { frameWaiters.surface = { resolve, reject } })
  try {
    status.value = '读取模型信息…'
    store.setActiveOdb(projectId.value, null)
    const response = await http.get(store.getApiUrl('meta/overview'))
    store.meta = response.data
    const instances = store.meta.instances.map(instance => instance.instance_name)
    if (!instances.length) throw new Error('模型没有实例')
    mark('metadata_ready')
    status.value = '加载轮廓…'
    await viewport.value.loadEdges('feature', instances)
    if (!report.value.outline_ready_detail?.edges) throw new Error('没有轮廓边，不接受空结果计时')
    await outlineFrame
    status.value = '加载完整表面…'
    await viewport.value.loadGeometry(instances)
    if (!report.value.surface_ready_detail?.triangles) throw new Error('没有表面三角面，不接受空结果计时')
    await surfaceFrame
    mark('completed')
    update({ complete: true, finalVisibility: document.visibilityState,
      resources: performance.getEntriesByType('resource')
        .filter(entry => entry.name.startsWith(store.baseUrl) && entry.startTime >= started)
        .map(entry => ({ name: entry.name, startMs: entry.startTime - started,
          durationMs: entry.duration, responseStartMs: entry.responseStart ? entry.responseStart - started : null,
          responseEndMs: entry.responseEnd - started, transferBytes: entry.transferSize || null })) })
    status.value = '完整模型已绘制；计时完成。'
  } catch (error) {
    update({ error: String(error) })
    status.value = String(error)
  } finally { busy.value = false }
}
function downloadReport() {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([JSON.stringify(report.value, null, 2)], { type: 'application/json' }))
  link.download = `viewer-load-${report.value.projectId}-${Date.now()}.json`
  link.click()
  URL.revokeObjectURL(link.href)
}
</script>
