<template>
  <div class="card" v-if="Object.keys(store.instanceMeshes).length > 0">
    <h2>Nearest Face</h2>
    <div style="display:grid;grid-template-columns:24px 1fr;gap:3px 6px;align-items:center;margin-bottom:6px">
      <label>X</label><input type="number" v-model.number="x" step="any" placeholder="0.0" />
      <label>Y</label><input type="number" v-model.number="y" step="any" placeholder="0.0" />
      <label>Z</label><input type="number" v-model.number="z" step="any" placeholder="0.0" />
    </div>
    <button class="primary" style="width:100%" @click="query" :disabled="loading">Query</button>

    <template v-if="result">
      <div style="margin-top:6px;padding-top:6px;border-top:1px solid #30363d;font-size:11px">
        <table style="width:100%;border-collapse:collapse">
          <tr><td style="color:#8b949e;width:70px">Distance</td><td>{{ result.distance?.toExponential(4) }} (模型单位)</td></tr>
          <tr><td style="color:#8b949e">Closest Pt</td><td>{{ fmt3(result.closest_point) }}</td></tr>
          <tr><td style="color:#8b949e">Normal</td><td>{{ fmt3(result.normal) }}</td></tr>
          <tr><td style="color:#8b949e">Elem</td><td>{{ result.elem_label ?? '—' }}</td></tr>
          <tr><td style="color:#8b949e">Type</td><td>{{ result.elem_type ?? '—' }}</td></tr>
        </table>
      </div>

      <!-- Surface Patch sub-panel -->
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid #30363d">
        <label style="font-size:11px;color:#8b949e;display:block;margin-bottom:4px">矩形选取</label>
        <div style="display:grid;grid-template-columns:24px 1fr;gap:3px 6px;align-items:center">
          <label>W</label><input type="number" v-model.number="patchW" step="any" placeholder="宽" />
          <label>H</label><input type="number" v-model.number="patchH" step="any" placeholder="高" />
        </div>
        <div class="row" style="margin-top:4px">
          <button class="primary" style="flex:2" @click="fetchPatch" :disabled="patchLoading">Select Patch</button>
          <button style="flex:1" @click="clearPatch">Clear</button>
        </div>
        <div v-if="patchInfo" style="margin-top:5px;font-size:11px;color:#8b949e;text-align:center">{{ patchInfo }}</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi }      from '../composables/useOdbApi'
import http from '../utils/request'

const store = useViewerStore()
const api   = useOdbApi()
const emit  = defineEmits(['show-patch', 'clear-patch'])

const x = ref(0), y = ref(0), z = ref(0)
const patchW = ref(0.02), patchH = ref(0.02)
const result      = ref(null)
const patchInfo   = ref('')
const loading     = ref(false)
const patchLoading = ref(false)

function fmt3(v) { return v ? v.map(n => n.toFixed(5)).join(', ') : '—' }

async function query() {
  if (!store.currentInstance) { store.setStatus('请先加载模型', 'err'); return }
  loading.value = true; result.value = null; patchInfo.value = ''
  try {
    const resp = await api.fetchNearestFace(store.currentInstance, x.value, y.value, z.value)
    const d = resp.data
    result.value = d
    store.setStatus(`最近面: 距离 ${d.distance.toExponential(3)}`, 'ok')
    emit('show-patch', { origin: d.closest_point, normal: d.normal, clear: false })
  } catch (e) {
    store.setStatus('Nearest face 查询失败: ' + e.message, 'err')
  } finally { loading.value = false }
}

async function fetchPatch() {
  if (!result.value) { store.setStatus('先执行 Nearest Face 查询', 'err'); return }
  if (patchW.value <= 0 || patchH.value <= 0) { store.setStatus('宽高必须大于 0', 'err'); return }
  patchLoading.value = true
  try {
    const patchResp = await http.post(store.getApiUrl('query/surface-patch'), {
      instance: store.currentInstance,
      center:   result.value.closest_point,
      normal:   result.value.normal,
      width:    patchW.value,
      height:   patchH.value,
    })
    const d = patchResp.data
    emit('show-patch', {
      faceIndices: d.render_face_indices,
      center: result.value.closest_point,
      normal: result.value.normal,
      halfW:  patchW.value / 2,
      halfH:  patchH.value / 2,
    })
    patchInfo.value = `${d.face_count} 面 · ${d.elem_count} 单元 · ${d.node_count} 节点`
    store.setStatus(`矩形选取完成：${d.face_count} 个面`, 'ok')
  } catch (e) {
    store.setStatus('Surface patch 失败: ' + e.message, 'err')
  } finally { patchLoading.value = false }
}

function clearPatch() {
  patchInfo.value = ''
  emit('clear-patch')
}
</script>
