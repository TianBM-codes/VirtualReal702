<template>
  <Teleport to="body">
    <div v-if="visible" class="le-panel" :style="panelStyle" @click="closePalette">

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
                     :title="e.user_color || e._color ? '自定义颜色（点击更改）' : '自动颜色（点击更改）'"
                     @click.stop="togglePalette($event, e)">
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

      <!-- Palette popup -->
      <Teleport to="body">
        <div v-if="palette.visible" class="le-palette" :style="palette.style"
             @click.stop @mousedown.stop>
          <div v-for="(c, i) in PALETTE" :key="i"
               class="le-pal-swatch"
               :style="{ background: `rgb(${c[0]},${c[1]},${c[2]})` }"
               @click="pickColor(c)" />
          <div class="le-pal-swatch le-pal-reset" title="恢复自动颜色" @click="resetEntryColor">↺</div>
        </div>
      </Teleport>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi } from '../composables/useOdbApi'

const props = defineProps({
  visible: Boolean,
  scheme:  { type: String, default: '' },
})
const emit = defineEmits(['close', 'region-highlight', 'clear-region-highlight', 'saved'])

const store = useViewerStore()
const api   = useOdbApi()

// ── Palette definition (mirrors color_service._PALETTE, 0-255) ────────────
const PALETTE = [
  [69, 133, 242], [242,  99,  69], [ 69, 199, 112], [242, 199,  46],
  [161,  69, 242], [ 46, 209, 230], [242, 140,  46], [242,  69, 158],
  [120, 199,  46], [ 46, 120, 199], [199,  69,  69], [ 46, 161, 161],
  [199, 161,  46], [140,  46, 120], [ 99, 161,  46], [ 46,  69, 161],
]
const GREY_CSS = 'rgb(89,89,89)'

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

// ── Palette popup ─────────────────────────────────────────────────────────
const palette = ref({ visible: false, entry: null, style: {} })

function togglePalette(evt, e) {
  if (palette.value.visible && palette.value.entry === e) {
    palette.value.visible = false
    return
  }
  const rect = evt.currentTarget.getBoundingClientRect()
  palette.value = {
    visible: true,
    entry:   e,
    style: {
      position: 'fixed',
      left: Math.min(rect.left, window.innerWidth - 200) + 'px',
      top:  (rect.bottom + 4) + 'px',
      zIndex: 10002,
    },
  }
}
function closePalette() { palette.value.visible = false }
function pickColor(c) {
  if (!palette.value.entry) return
  palette.value.entry._color = c   // [r, g, b] 0-255
  palette.value.visible = false
}
function resetEntryColor() {
  if (!palette.value.entry) return
  palette.value.entry._color = null
  palette.value.visible = false
}

function onDocClick(e) {
  if (!palette.value.visible) return
  if (!e.target.closest('.le-palette') && !e.target.closest('.le-swatch')) {
    palette.value.visible = false
  }
}

// ── Color helpers ─────────────────────────────────────────────────────────
function toRgbCss(r, g, b) {
  return `rgb(${Math.round(r*255)},${Math.round(g*255)},${Math.round(b*255)})`
}
function effectiveCss(e) {
  if (e._color) return `rgb(${e._color[0]},${e._color[1]},${e._color[2]})`
  return toRgbCss(e.color_r, e.color_g, e.color_b)
}

// ── Load entries ─────────────────────────────────────────────────────────
watch([() => props.visible, () => props.scheme, () => store.currentInstance], async ([vis]) => {
  if (!vis || !props.scheme || !store.currentInstance) return
  await loadEntries()
}, { immediate: true })

async function loadEntries() {
  if (!props.scheme || !store.currentInstance) return
  loading.value = true
  try {
    const data = await api.fetchLegendEntries(store.currentInstance, props.scheme)
    entries.value = (data?.entries ?? []).map(e => ({
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
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
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
  cursor: pointer;
  border: 1px solid #30363d;
  margin: 0 auto;
  position: relative;
}
.le-swatch:hover { border-color: #58a6ff; }
.le-swatch-dot {
  position: absolute;
  bottom: 1px;
  right: 1px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #fff;
  opacity: 0.7;
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

/* Palette popup (teleported to body, no scoped needed — but scoped is ok here) */
.le-palette {
  display: grid;
  grid-template-columns: repeat(8, 20px);
  gap: 3px;
  padding: 6px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 5px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
}
.le-pal-swatch {
  width: 20px;
  height: 20px;
  border-radius: 3px;
  cursor: pointer;
  border: 1px solid transparent;
}
.le-pal-swatch:hover { border-color: #fff; }
.le-pal-reset {
  background: #30363d;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c9d1d9;
  font-size: 13px;
}
</style>
