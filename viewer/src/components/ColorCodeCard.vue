<template>
  <div class="card" v-if="store.meta && schemes.length > 0">
    <div style="display:flex;align-items:center;margin-bottom:6px">
      <h2 style="margin:0;flex:1">Color Code</h2>
      <button v-if="scheme && scheme !== 'instance' && scheme !== 'elset'"
              @click="showEditor = !showEditor"
              style="font-size:10px;padding:2px 7px;background:#21262d;border:1px solid #30363d;border-radius:3px;color:#58a6ff;cursor:pointer">
        {{ showEditor ? '关闭编辑' : '编辑 Sets' }}
      </button>
    </div>

    <label>Scheme</label>
    <select v-model="scheme">
      <option value="">— None —</option>
      <option v-for="s in schemes" :key="s" :value="s">{{ SCHEME_LABELS[s] || s }}</option>
    </select>

    <LegendEditor
      :visible="showEditor && !!scheme && scheme !== 'instance' && scheme !== 'elset'"
      :scheme="scheme"
      @close="showEditor = false"
      @region-highlight="opts => emit('region-highlight', opts)"
      @clear-region-highlight="emit('clear-region-highlight')"
      @saved="apply"
    />

    <!-- Elset 多选框（只在 elset 方案时显示） -->
    <template v-if="scheme === 'elset'">
      <div style="display:flex;align-items:center;margin-top:4px;margin-bottom:3px">
        <label style="flex:1">Sets <span style="font-size:9px;color:#8b949e">（多选）</span></label>
        <button @click="selectAllElsets" style="font-size:10px;padding:1px 6px">全选</button>
        <button @click="selectedElsets = []" style="font-size:10px;padding:1px 6px;margin-left:4px">全不选</button>
      </div>
      <div style="max-height:120px;overflow-y:auto;border:1px solid #30363d;border-radius:4px;padding:4px 6px;font-size:11px">
        <label v-for="s in elsets" :key="s" style="display:flex;align-items:center;gap:5px;cursor:pointer;padding:2px 0">
          <input type="checkbox" :value="s" v-model="selectedElsets" />{{ s }}
        </label>
      </div>
    </template>

    <div class="row" style="margin-top:6px">
      <button class="primary" @click="apply">Apply</button>
      <button @click="emit('clear')">Clear</button>
      <button @click="emit('reset')" title="Reset to loaded color">Reset</button>
    </div>

    <!-- 图例 -->
    <template v-if="legend.length > 0">
      <div style="display:flex;align-items:center;margin-top:6px;margin-bottom:2px">
        <span style="font-size:10px;color:#8b949e;flex:1">Legend</span>
        <span style="font-size:10px;color:#8b949e">{{ legend.length }} regions</span>
      </div>
      <div style="max-height:120px;overflow-y:auto;border:1px solid #30363d;border-radius:4px;padding:3px 5px">
        <div v-for="item in legend" :key="item.name" style="display:flex;align-items:center;gap:5px;margin:2px 0;font-size:11px;color:#c9d1d9">
          <div :style="`width:11px;height:11px;border-radius:2px;flex-shrink:0;background:rgb(${r(item.r)},${r(item.g)},${r(item.b)})`" />
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ item.name }}</span>
        </div>
      </div>
    </template>

    <!-- Region Highlight (test) — only shown for section/etype schemes -->
    <template v-if="highlightableRegions.length > 0">
      <div style="border-top:1px solid #30363d;margin-top:8px;padding-top:8px">
        <div style="display:flex;align-items:center;margin-bottom:3px">
          <span style="font-size:10px;color:#8b949e;flex:1">Region Highlight</span>
          <button @click="selectedRegions = highlightableRegions.map(i=>i.name)" style="font-size:10px;padding:1px 6px">全选</button>
          <button @click="selectedRegions = []" style="font-size:10px;padding:1px 6px;margin-left:4px">全不选</button>
        </div>
        <div style="max-height:100px;overflow-y:auto;border:1px solid #30363d;border-radius:4px;padding:3px 5px">
          <label v-for="item in highlightableRegions" :key="item.name"
                 style="display:flex;align-items:center;gap:5px;cursor:pointer;padding:2px 0;font-size:11px">
            <input type="checkbox" :value="item.name" v-model="selectedRegions" />
            <div :style="`width:9px;height:9px;border-radius:1px;flex-shrink:0;background:rgb(${r(item.r)},${r(item.g)},${r(item.b)})`" />
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ item.name }}</span>
          </label>
        </div>
        <div style="display:flex;gap:10px;margin-top:5px;font-size:11px">
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" v-model="showMeshEdges" />
            <span style="color:#aaaaaa">Mesh Edges</span>
          </label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" v-model="showOutline" />
            <span style="color:#ffffff">Outline</span>
          </label>
        </div>
        <div class="row" style="margin-top:4px">
          <button class="primary" @click="applyRegionHighlight">Apply</button>
          <button @click="clearHighlight">Clear</button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi }      from '../composables/useOdbApi'
import LegendEditor       from './LegendEditor.vue'

const store = useViewerStore()
const api   = useOdbApi()
const emit  = defineEmits(['apply', 'clear', 'reset', 'region-highlight', 'clear-region-highlight'])

const SCHEME_LABELS = { instance:'Instance', etype:'Element Type', section:'Averaging Regions', material:'Material', section_type:'Section Type', elset:'Elset Highlight' }

const scheme         = ref('')
const schemes        = ref([])
const elsets         = ref([])
const selectedElsets = ref([])
const legend         = ref([])
const showEditor     = ref(false)

// Region highlight state
const selectedRegions      = ref([])
const showMeshEdges        = ref(false)
const showOutline          = ref(true)
const highlightableRegions = computed(() =>
  legend.value.filter(item => item.name !== '(none)' && item.name !== 'other')
)

const r = (v) => Math.round(v * 255)

// Close editor when scheme changes
watch(scheme, () => { showEditor.value = false; legend.value = []; selectedRegions.value = [] })

// Load schemes when currentInstance changes
watch(() => store.currentInstance, async inst => {
  if (!inst) return
  try {
    const data = await api.fetchColorSchemes(inst)
    const backendSchemes = data.data?.schemes ?? []
    schemes.value = ['instance', ...backendSchemes]
    elsets.value  = data.data?.elsets  ?? []
  } catch (_) {
    schemes.value = ['instance']
  }
}, { immediate: true })

function selectAllElsets() { selectedElsets.value = [...elsets.value] }

function apply() {
  if (!scheme.value) { store.setStatus('Select a color scheme', 'err'); return }
  if (scheme.value === 'elset' && selectedElsets.value.length === 0) { store.setStatus('Select at least one element set', 'err'); return }
  emit('apply', { scheme: scheme.value, setNames: scheme.value === 'elset' ? selectedElsets.value : [] })
}

// Receive legend back from parent (after applyColorCode returns)
function setLegend(items) { legend.value = items; selectedRegions.value = [] }

function applyRegionHighlight() {
  if (selectedRegions.value.length === 0) { store.setStatus('请至少选择一个区域', 'err'); return }
  if (!showMeshEdges.value && !showOutline.value) { store.setStatus('请至少勾选一种高亮类型', 'err'); return }
  const types = []
  if (showMeshEdges.value) types.push('mesh')
  if (showOutline.value)   types.push('outline')
  // Pass full region info (name + color) so viewport can use legend colors
  const regions = highlightableRegions.value.filter(item => selectedRegions.value.includes(item.name))
  emit('region-highlight', { scheme: scheme.value, regions, types })
}

function clearHighlight() {
  emit('clear-region-highlight')
}

defineExpose({ setLegend })
</script>
