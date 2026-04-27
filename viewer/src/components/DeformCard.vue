<template>
  <div class="card" v-if="store.meta">
    <h2>Deformed Shape</h2>

    <label>Step</label>
    <select v-model="step" @change="onStepChange">
      <option v-for="s in store.meta.steps" :key="s.step_name" :value="s.step_name">{{ s.step_name }}</option>
    </select>

    <label>Frame: <span>{{ frameIdx }}</span></label>
    <input type="range" v-model.number="frameIdx" :min="0" :max="maxFrame" />

    <label>Scale Factor</label>
    <input type="number" v-model.number="scale" step="1" min="0" style="width:100%;box-sizing:border-box" />

    <div style="display:flex;gap:6px;margin-top:4px">
      <button class="primary" style="flex:1" @click="apply">Apply</button>
      <button style="flex:1" @click="reset">Reset</button>
    </div>

    <div style="display:flex;gap:6px;margin-top:4px">
      <button
        class="primary"
        style="flex:1"
        :style="playing ? 'background:#c0392b' : ''"
        @click="togglePlay"
      >{{ playing ? 'Stop' : 'Play' }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi }      from '../composables/useOdbApi'

const store = useViewerStore()
const api   = useOdbApi()
const emit  = defineEmits(['apply', 'reset', 'play', 'stop'])

const step     = ref('')
const frameIdx = ref(0)
const maxFrame = ref(0)
const scale    = ref(1.0)
const playing  = ref(false)

watch(() => store.meta, meta => {
  if (!meta?.steps?.length) return
  step.value = meta.steps[0].step_name
  onStepChange()
}, { immediate: true })

function onStepChange() {
  const stepInfo = store.meta?.steps?.find(s => s.step_name === step.value)
  if (!stepInfo) return
  maxFrame.value = (stepInfo.num_frames || 1) - 1
  if (frameIdx.value > maxFrame.value) frameIdx.value = maxFrame.value
  autoFillScale()
}

// Auto-fill scale when frame slider moves
watch(frameIdx, autoFillScale)

async function autoFillScale() {
  if (!step.value) return
  try {
    const suggested = await api.fetchDeformSuggestScale(
      step.value, frameIdx.value,
      store.activeResultGroup ?? undefined
    )
    scale.value = suggested
  } catch (_) {}
}

function apply() {
  playing.value = false
  emit('stop')
  store.deformScale = scale.value
  emit('apply', { step: step.value, frameIdx: frameIdx.value, scale: scale.value })
}

function reset() {
  playing.value = false
  emit('stop')
  emit('reset')
}

function togglePlay() {
  if (playing.value) {
    playing.value = false
    emit('stop')
  } else {
    playing.value = true
    emit('play', { step: step.value, totalFrames: maxFrame.value + 1, scale: scale.value })
  }
}

// Called by parent when animation reaches a new frame — update slider
function onAnimFrame(frame) {
  frameIdx.value = frame
}

// Called by parent when animation stops externally
function onAnimStop() {
  playing.value = false
}

defineExpose({ onAnimFrame, onAnimStop })
</script>
