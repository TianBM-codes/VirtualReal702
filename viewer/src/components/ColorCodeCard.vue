<template>
  <div class="card" v-if="store.meta && schemes.length > 0">
    <h2>Color Code</h2>

    <label>Scheme</label>
    <select v-model="scheme">
      <option value="">— None —</option>
      <option v-for="s in schemes" :key="s" :value="s">{{ SCHEME_LABELS[s] || s }}</option>
    </select>

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
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi }      from '../composables/useOdbApi'

const store = useViewerStore()
const api   = useOdbApi()
const emit  = defineEmits(['apply', 'clear'])

const SCHEME_LABELS = { etype:'Element Type', section:'Averaging Regions', material:'Material', section_type:'Section Type', elset:'Elset Highlight' }

const scheme         = ref('')
const schemes        = ref([])
const elsets         = ref([])
const selectedElsets = ref([])
const legend         = ref([])

const r = (v) => Math.round(v * 255)

// Load schemes when currentInstance changes
watch(() => store.currentInstance, async inst => {
  if (!inst) return
  try {
    const data = await api.fetchColorSchemes(inst)
    schemes.value = data.data?.schemes ?? []
    elsets.value  = data.data?.elsets  ?? []
  } catch (_) {}
}, { immediate: true })

function selectAllElsets() { selectedElsets.value = [...elsets.value] }

function apply() {
  if (!scheme.value) { store.setStatus('Select a color scheme', 'err'); return }
  if (scheme.value === 'elset' && selectedElsets.value.length === 0) { store.setStatus('Select at least one element set', 'err'); return }
  emit('apply', { scheme: scheme.value, setNames: scheme.value === 'elset' ? selectedElsets.value : [] })
}

// Receive legend back from parent (after applyColorCode returns)
function setLegend(items) { legend.value = items }

defineExpose({ setLegend })
</script>
