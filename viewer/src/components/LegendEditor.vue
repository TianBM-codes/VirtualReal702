<template>
  <Teleport to="body">
    <div v-if="visible" class="le-panel" :style="panelStyle">

      <!-- Header (drag handle) -->
      <div class="le-header" @mousedown="startDrag">
        <span class="le-title">Sets · {{ schemeName }}</span>
        <button class="le-close-btn" @mousedown.stop @click="emit('close')">✕</button>
      </div>

      <!-- Table -->
      <div class="le-scroll">
        <table class="le-table">
          <colgroup>
            <col style="width:26px" />
            <col style="width:90px;max-width:90px" />
            <col />
            <col style="width:52px" />
            <col style="width:32px" />
          </colgroup>
          <thead>
            <tr>
              <th></th>
              <th>ID</th>
              <th>名称</th>
              <th style="text-align:right">面数</th>
              <th>颜色</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in entries" :key="e.legend_key"
                :class="{ 'le-selected': selected.has(e.legend_key) }"
                @click="toggleRow(e)">
              <td @click.stop>
                <input type="checkbox" :checked="selected.has(e.legend_key)"
                       @change.stop="toggleRow(e)" />
              </td>
              <td class="le-key" :title="e.legend_key">{{ e.legend_key }}</td>
              <td @click.stop>
                <input class="le-name-input"
                       v-model="e._name"
                       :placeholder="e.default_title || e.legend_key" />
              </td>
              <td class="le-count">{{ e.face_count.toLocaleString() }}</td>
              <td @click.stop style="text-align:center">
                <div class="le-swatch"
                     :style="{ background: effectiveCss(e) }"
                     :title="e._color ? '自定义颜色（点击更改）' : '自动颜色（点击更改）'">
                  <input type="color"
                         class="le-color-input"
                         :value="toHex(e)"
                         @change="onColorChange($event, e)" />
                  <span v-if="e._color" class="le-swatch-dot" />
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="loading" class="le-loading">加载中…</div>
        <div v-if="!loading && entries.length === 0" class="le-loading">无数据</div>
      </div>

      <!-- Footer -->
      <div class="le-footer">
        <button class="le-btn-primary" :disabled="saving" @click.stop="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
        <button class="le-btn" @click.stop="resetColors" title="所有条目恢复自动调色板颜色">重置颜色</button>
        <button class="le-btn" @click.stop="clearSelection">清除选择</button>
      </div>

    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi } from '../composables/useOdbApi'

const props = defineProps({
  visible:  Boolean,
  scheme:   { type: String, default: '' },
  setNames: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'region-highlight', 'clear-region-highlight', 'saved'])

const store = useViewerStore()
const api   = useOdbApi()

const SCHEME_NAMES = {
  section:      'Averaging Regions',
  etype:        'Element Types',
  material:     'Materials',
  section_type: 'Section Types',
  elset:        'Element Sets',
  instance:     'Instances',
}

const entries  = ref([])
const selected = ref(new Set())
const loading  = ref(false)
const saving   = ref(false)

const schemeName = computed(() => SCHEME_NAMES[props.scheme] || props.scheme || '—')

// ── Color helpers ─────────────────────────────────────────────────────────
function toHex(e) {
  const [r, g, b] = e._color
    ?? [Math.round(e.color_r * 255), Math.round(e.color_g * 255), Math.round(e.color_b * 255)]
  return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('')
}
function effectiveCss(e) {
  if (e._color) return `rgb(${e._color[0]},${e._color[1]},${e._color[2]})`
  return `rgb(${Math.round(e.color_r*255)},${Math.round(e.color_g*255)},${Math.round(e.color_b*255)})`
}
function onColorChange(evt, e) {
  const hex = evt.target.value  // '#rrggbb'
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  e._color = [r, g, b]
}

// ── Panel position (draggable) ────────────────────────────────────────────
const panelPos = ref({ x: Math.max(0, window.innerWidth - 360), y: 80 })
const panelStyle = computed(() => ({
  left: panelPos.value.x + 'px',
  top:  panelPos.value.y + 'px',
}))
let dragOffset = null

function startDrag(e) {
  if (e.target.closest('button, input')) return
  dragOffset = { x: e.clientX - panelPos.value.x, y: e.clientY - panelPos.value.y }
  document.addEventListener('mousemove', onDrag)
  document.addEventListener('mouseup',  stopDrag)
}
function onDrag(e) {
  if (!dragOffset) return
  panelPos.value = {
    x: Math.max(0, Math.min(window.innerWidth  - 320, e.clientX - dragOffset.x)),
    y: Math.max(0, Math.min(window.innerHeight - 80,  e.clientY - dragOffset.y)),
  }
}
function stopDrag() {
  dragOffset = null
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup',  stopDrag)
}


// ── Load entries ─────────────────────────────────────────────────────────
// For section scheme we fetch all instances, so any valid instance key works as URL placeholder.
function anyInstance() {
  return store.currentInstance || Object.keys(store.instanceMeshes)[0] || null
}

watch([() => props.visible, () => props.scheme, () => store.currentInstance], async ([vis]) => {
  if (!vis || !props.scheme || !anyInstance()) return
  await loadEntries()
}, { immediate: true })

async function loadEntries() {
  const inst = anyInstance()
  if (!props.scheme || !inst) return
  loading.value = true
  try {
    const res = await api.fetchLegendEntries(inst, props.scheme, props.setNames)
    entries.value = (res?.data?.entries ?? []).map(e => ({
      ...e,
      _name:  e.display_name ?? e.default_title ?? e.legend_key,
      _color: e.user_color
        ? [Math.round(e.color_r * 255), Math.round(e.color_g * 255), Math.round(e.color_b * 255)]
        : null,
    }))
    selected.value = new Set()
  } catch (err) {
    console.error('[LegendEditor] load failed', err)
  } finally {
    loading.value = false
  }
}

// ── Row selection → region highlight ─────────────────────────────────────
function toggleRow(e) {
  const next = new Set(selected.value)
  next.has(e.legend_key) ? next.delete(e.legend_key) : next.add(e.legend_key)
  selected.value = next
  emitHighlight()
}

function clearSelection() {
  selected.value = new Set()
  emit('clear-region-highlight')
}

function emitHighlight() {
  const sel = entries.value.filter(e => selected.value.has(e.legend_key))
  if (sel.length === 0) { emit('clear-region-highlight'); return }
  const regions = sel.map(e => {
    const [r255, g255, b255] = e._color ?? [
      Math.round(e.color_r * 255),
      Math.round(e.color_g * 255),
      Math.round(e.color_b * 255),
    ]
    return { legend_key: e.legend_key, name: e.legend_key, r: r255/255, g: g255/255, b: b255/255 }
  })
  emit('region-highlight', { scheme: props.scheme, regions, types: ['outline'] })
}

// ── Save ──────────────────────────────────────────────────────────────────
async function save() {
  if (!store.currentInstance) return
  saving.value = true
  try {
    const body = entries.value.map(e => {
      const nameDefault = e.default_title ?? e.legend_key
      const nameVal     = (e._name && e._name !== nameDefault && e._name !== e.legend_key)
        ? e._name : null
      return {
        legend_key:   e.legend_key,
        display_name: nameVal,
        color_r:      e._color ? e._color[0] / 255 : null,
        color_g:      e._color ? e._color[1] / 255 : null,
        color_b:      e._color ? e._color[2] / 255 : null,
      }
    })
    await api.saveLegendEntries(store.currentInstance, props.scheme, body)
    emit('saved')
    await loadEntries()
  } catch (err) {
    console.error('[LegendEditor] save failed', err)
  } finally {
    saving.value = false
  }
}

function resetColors() {
  entries.value.forEach(e => { e._color = null })
}

// ── Lifecycle ─────────────────────────────────────────────────────────────
onBeforeUnmount(() => {
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup',  stopDrag)
})
</script>

<style scoped>
.le-panel {
  position: fixed;
  z-index: 10000;
  width: 340px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0,0,0,0.6);
  font-size: 12px;
  color: #c9d1d9;
  font-family: 'Consolas','Menlo',monospace;
  max-height: 70vh;
}
.le-header {
  display: flex;
  align-items: center;
  padding: 7px 10px;
  background: #21262d;
  border-bottom: 1px solid #30363d;
  border-radius: 6px 6px 0 0;
  cursor: move;
  user-select: none;
  gap: 6px;
}
.le-title {
  flex: 1;
  font-size: 12px;
  color: #58a6ff;
  font-weight: bold;
}
.le-close-btn {
  background: none;
  border: none;
  color: #8b949e;
  cursor: pointer;
  font-size: 13px;
  padding: 0 2px;
  line-height: 1;
}
.le-close-btn:hover { color: #f85149; }

.le-scroll {
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}
.le-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.le-table th {
  padding: 5px 6px;
  background: #0d1117;
  color: #8b949e;
  font-weight: normal;
  font-size: 11px;
  text-align: left;
  border-bottom: 1px solid #30363d;
  position: sticky;
  top: 0;
  z-index: 1;
}
.le-table td {
  padding: 3px 6px;
  border-bottom: 1px solid #21262d;
  vertical-align: middle;
  overflow: hidden;
}
.le-table tr:hover td { background: #1c2128; }
.le-selected td { background: #1f3a5c !important; }
.le-table tr { cursor: pointer; }

.le-key {
  color: #8b949e;
  font-size: 11px;
  text-overflow: ellipsis;
  overflow: hidden;
  white-space: nowrap;
}
.le-count {
  text-align: right;
  color: #8b949e;
  font-size: 11px;
}
.le-name-input {
  width: 100%;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 3px;
  color: #c9d1d9;
  font-size: 11px;
  font-family: inherit;
  padding: 1px 3px;
  box-sizing: border-box;
}
.le-name-input:focus {
  outline: none;
  border-color: #58a6ff;
  background: #0d1117;
}
.le-name-input::placeholder { color: #484f58; }

.le-swatch {
  width: 18px;
  height: 18px;
  border-radius: 3px;
  border: 1px solid #30363d;
  margin: 0 auto;
  position: relative;
  overflow: hidden;
  cursor: pointer;
}
.le-swatch:hover { border-color: #58a6ff; }
.le-color-input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
  padding: 0;
  border: none;
}
.le-swatch-dot {
  position: absolute;
  bottom: 1px;
  right: 1px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #fff;
  opacity: 0.7;
  pointer-events: none;
}

.le-loading {
  padding: 12px;
  text-align: center;
  color: #8b949e;
  font-size: 11px;
}

.le-footer {
  display: flex;
  gap: 6px;
  padding: 7px 10px;
  border-top: 1px solid #30363d;
  background: #0d1117;
  border-radius: 0 0 6px 6px;
}
.le-btn-primary {
  background: #238636;
  border: 1px solid #2ea043;
  color: #fff;
  border-radius: 4px;
  padding: 3px 10px;
  cursor: pointer;
  font-size: 11px;
}
.le-btn-primary:hover:not(:disabled) { background: #2ea043; }
.le-btn-primary:disabled { opacity: 0.5; cursor: default; }
.le-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  border-radius: 4px;
  padding: 3px 8px;
  cursor: pointer;
  font-size: 11px;
}
.le-btn:hover { background: #30363d; }

</style>
