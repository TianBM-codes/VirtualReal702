import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'

export const useViewerStore = defineStore('viewer', () => {
  // ── Connection ──────────────────────────────────────────────────────────
  const baseUrl = ref('http://127.0.0.1:5000')
  const activeOdbId = ref(localStorage.getItem('activeOdbId') || null)
  const activeResultGroup = ref(localStorage.getItem('activeResultGroup') || null)

  function setActiveOdb(odbId, resultGroup = null) {
    activeOdbId.value = odbId
    activeResultGroup.value = resultGroup
    if (odbId) localStorage.setItem('activeOdbId', odbId)
    else localStorage.removeItem('activeOdbId')
    if (resultGroup) localStorage.setItem('activeResultGroup', resultGroup)
    else localStorage.removeItem('activeResultGroup')
  }

  function getApiUrl(path) {
    if (!activeOdbId.value) throw new Error('No ODB loaded')
    const base = `${baseUrl.value}/api/odb/${activeOdbId.value}/${path}`
    if (!activeResultGroup.value) return base
    const sep = base.includes('?') ? '&' : '?'
    return `${base}${sep}result_group=${encodeURIComponent(activeResultGroup.value)}`
  }

  // ── Metadata ─────────────────────────────────────────────────────────────
  const meta = ref(null)   // { instances, steps, fields }

  // ── Instances ────────────────────────────────────────────────────────────
  // name → { mesh, colorAttr, numFaces }  (populated by ThreeViewport)
  const instanceMeshes = reactive({})
  const currentInstance = ref(null)

  // ── Mouse / Pick ─────────────────────────────────────────────────────────
  const mouseMode = ref('nav')   // 'nav' | 'pick'
  const pickMode  = ref('element')  // 'element' | 'node'
  const deformScale = ref(1.0)

  // ── Probe table ──────────────────────────────────────────────────────────
  const probeRows = ref([])

  // ── Status bar ───────────────────────────────────────────────────────────
  const statusMsg  = ref('Ready — enter API URL and click Connect.')
  const statusType = ref('')  // '' | 'ok' | 'err'

  function setStatus(msg, type = '') {
    statusMsg.value  = msg
    statusType.value = type
  }

  return {
    baseUrl, activeOdbId, activeResultGroup, setActiveOdb, getApiUrl,
    meta,
    instanceMeshes, currentInstance,
    mouseMode, pickMode, deformScale,
    probeRows,
    statusMsg, statusType, setStatus,
  }
})
