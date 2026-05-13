<template>
  <div class="card" v-if="store.meta">
    <h2>Deformed Shape</h2>

    <label>Step</label>
    <select v-model="step" @change="onStepChange">
      <option v-for="s in store.meta.steps" :key="s.step_name" :value="s.step_name">{{ s.step_name }}</option>
    </select>

    <label>{{ isFrequency ? 'Mode' : 'Frame' }}: <span>{{ frameIdx }}</span></label>
    <input type="range" v-model.number="frameIdx" :min="0" :max="maxFrame" />

    <label>Scale Factor</label>
    <input type="number" v-model.number="scale" step="1" min="0" style="width:100%;box-sizing:border-box" />

    <div style="display:flex;gap:6px;margin-top:4px">
      <button class="primary" style="flex:1" @click="apply">Apply</button>
      <button style="flex:1" @click="reset">Reset</button>
    </div>

    <!-- 模态谐波动画专属控制（仅 FREQUENCY step 显示） -->
    <template v-if="isFrequency">
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid #30363d">
        <label style="font-size:11px;color:#8b949e">Modal Animation Mode</label>
        <div style="display:flex;gap:4px;margin-top:4px">
          <button
            @click="modalMode='shader'"
            style="flex:1;font-size:11px;padding:3px 0;border:1px solid #30363d;border-radius:3px;cursor:pointer"
            :style="modalMode==='shader' ? 'background:#1f6feb;color:#fff' : 'background:#21262d;color:#c9d1d9'"
          >GPU Shader</button>
          <button
            @click="modalMode='precompute'"
            style="flex:1;font-size:11px;padding:3px 0;border:1px solid #30363d;border-radius:3px;cursor:pointer"
            :style="modalMode==='precompute' ? 'background:#1f6feb;color:#fff' : 'background:#21262d;color:#c9d1d9'"
          >预计算</button>
        </div>
        <div v-if="modalMode==='precompute'" style="margin-top:6px">
          <label style="font-size:11px">帧数 (4–60)</label>
          <input type="number" v-model.number="nFrames" min="4" max="60" step="4"
            style="width:100%;box-sizing:border-box;font-size:11px" />
        </div>

        <div style="margin-top:6px">
          <label style="font-size:11px">速度 (cycles/s)</label>
          <input type="number" v-model.number="animSpeed" min="0.1" max="10" step="0.1"
            style="width:100%;box-sizing:border-box;font-size:11px" />
        </div>
      </div>
    </template>

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
import { ref, computed, watch } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi }      from '../composables/useOdbApi'

const store = useViewerStore()
const api   = useOdbApi()
const emit  = defineEmits(['apply', 'reset', 'play', 'stop', 'play-modal'])

const step      = ref('')
const frameIdx  = ref(0)
const maxFrame  = ref(0)
const scale     = ref(1.0)
const playing   = ref(false)
const modalMode = ref('shader')   // 'shader' | 'precompute'
const nFrames   = ref(20)
const animSpeed = ref(1.0)        // cycles per second

const isFrequency = computed(() => {
  const info = store.meta?.steps?.find(s => s.step_name === step.value)
  return info?.procedure === 'FREQUENCY'
})

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
    return
  }
  playing.value = true
  if (isFrequency.value) {
    emit('play-modal', {
      step: step.value,
      frameIdx: frameIdx.value,
      scale: scale.value,
      mode: modalMode.value,
      nFrames: nFrames.value,
      speed: animSpeed.value,
    })
  } else {
    emit('play', { step: step.value, totalFrames: maxFrame.value + 1, scale: scale.value })
  }
}

function onAnimFrame(frame) {
  frameIdx.value = frame
}

function onAnimStop() {
  playing.value = false
}

defineExpose({ onAnimFrame, onAnimStop })
</script>
