<template>
  <div class="card" v-if="Object.keys(store.instanceMeshes).length > 0">
    <h2>View Cut</h2>
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12px;color:#c9d1d9">
      <input type="checkbox" v-model="active" @change="onChange" /> Activate
    </label>

    <label>Axis</label>
    <div class="row">
      <button v-for="ax in ['X','Y','Z']" :key="ax"
        :class="['toggle', axis === ax.toLowerCase() ? 'active' : '']"
        @click="setAxis(ax.toLowerCase())">{{ ax }}</button>
    </div>

    <label>Position: <span>{{ active ? position.toFixed(3) : '—' }}</span></label>
    <input type="range" v-model.number="position"
      :min="sliderMin" :max="sliderMax" :step="sliderStep" :disabled="!active"
      @input="onChange" @mouseup="onSliderCommit" @touchend="onSliderCommit" />

    <div class="row">
      <button :class="['toggle', flipped ? 'active' : '']" @click="flipped = !flipped; onChange()">Flip Direction</button>
      <button :class="['toggle', sectionFill ? 'active' : '']" @click="sectionFill = !sectionFill; onChange()">补面</button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useViewerStore } from '../store/viewer'

const store = useViewerStore()
const emit  = defineEmits(['update'])

const active      = ref(false)
const axis        = ref('x')
const position    = ref(0)
const flipped     = ref(false)
const sectionFill = ref(true)

// Slider range in world coordinates — updated when model-loaded fires
const sliderMin  = ref(0)
const sliderMax  = ref(1)
const sliderStep = ref(0.001)

function updateRange(bbox) {
  if (!bbox) return
  const ax = axis.value
  const mn = bbox.axMin[ax], mx = bbox.axMax[ax]
  sliderMin.value  = mn; sliderMax.value = mx
  sliderStep.value = (mx - mn) / 1000 || 0.001
  position.value   = (mn + mx) / 2
}

function setAxis(ax) {
  axis.value = ax
  // Re-range slider for new axis using last known bbox
  if (_lastBbox) updateRange(_lastBbox)
  onChange()
}

let _lastBbox = null
function onModelLoaded({ bbox }) { _lastBbox = bbox; updateRange(bbox) }

function onChange() {
  emit('update', {
    active:      active.value,
    axis:        axis.value,
    position:    position.value,
    flipped:     flipped.value,
    sectionFill: sectionFill.value,
  })
}

// Commit fires section-mesh fetch (same as mouseup on slider in original)
function onSliderCommit() {
  if (active.value) onChange()
}

defineExpose({ onModelLoaded })
</script>
