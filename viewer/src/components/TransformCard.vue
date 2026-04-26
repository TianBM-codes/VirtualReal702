<template>
  <div class="card" v-if="hasModel">
    <h2>Model Transform</h2>
    <button class="toggle" style="width:100%;margin-bottom:4px" @click="onReset">↺ Reset to Identity</button>

    <!-- Translate -->
    <label>Translate</label>
    <div class="row">
      <button v-for="ax in ['X','Y','Z']" :key="ax"
        :class="['toggle', tAxis===ax?'active':'']" @click="tAxis=ax">{{ ax }}</button>
    </div>
    <div class="row">
      <input type="number" v-model.number="tDelta" style="flex:1;width:0;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:3px 6px;font-size:12px"
        placeholder="distance" step="0.1" />
      <button class="toggle" @click="onTranslate">Apply</button>
    </div>

    <!-- Rotate -->
    <label>Rotate (degrees)</label>
    <div class="row">
      <button v-for="ax in ['X','Y','Z']" :key="ax"
        :class="['toggle', rAxis===ax?'active':'']" @click="rAxis=ax">{{ ax }}</button>
    </div>
    <div class="row">
      <input type="number" v-model.number="rAngle" style="flex:1;width:0;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:3px 6px;font-size:12px"
        placeholder="angle °" step="5" />
      <button class="toggle" @click="onRotate">Apply</button>
    </div>

    <!-- Scale -->
    <label>Scale (uniform)</label>
    <div class="row">
      <input type="number" v-model.number="sFactor" style="flex:1;width:0;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:3px 6px;font-size:12px"
        placeholder="factor" step="0.1" min="0.0001" />
      <button class="toggle" @click="onScale">Apply</button>
    </div>

    <!-- Matrix display -->
    <details style="margin-top:6px">
      <summary style="font-size:11px;color:#8b949e;cursor:pointer;user-select:none">Current Matrix4</summary>
      <pre style="font-size:9px;color:#58a6ff;margin:4px 0;overflow-x:auto;white-space:pre;line-height:1.5;font-family:monospace">{{ matrixDisplay }}</pre>
      <button class="toggle" style="width:100%;font-size:11px" @click="copyMatrix">{{ copyLabel }}</button>
    </details>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import * as THREE from 'three'
import { useViewerStore } from '../store/viewer'

const store = useViewerStore()
const emit  = defineEmits(['transform', 'reset'])

const hasModel = computed(() => Object.keys(store.instanceMeshes).length > 0)

// Per-operation inputs
const tAxis   = ref('X')
const tDelta  = ref(0)
const rAxis   = ref('X')
const rAngle  = ref(0)
const sFactor = ref(1)

// Accumulated transform matrix — 16 floats, column-major (Three.js convention)
const elements = ref([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])

// Premultiply op into the accumulated matrix (world-space accumulation).
// M_new = op * M_old means each new operation is applied in world space,
// independent of the model's current orientation.
function _applyOp(op) {
  const m = new THREE.Matrix4().fromArray(elements.value)
  m.premultiply(op)
  elements.value = [...m.elements]
  emit('transform', [...elements.value])
}

function onTranslate() {
  const d = tDelta.value || 0
  _applyOp(new THREE.Matrix4().makeTranslation(
    tAxis.value === 'X' ? d : 0,
    tAxis.value === 'Y' ? d : 0,
    tAxis.value === 'Z' ? d : 0,
  ))
}

function onRotate() {
  const rad = (rAngle.value || 0) * Math.PI / 180
  let op
  if      (rAxis.value === 'X') op = new THREE.Matrix4().makeRotationX(rad)
  else if (rAxis.value === 'Y') op = new THREE.Matrix4().makeRotationY(rad)
  else                          op = new THREE.Matrix4().makeRotationZ(rad)
  _applyOp(op)
}

function onScale() {
  const s = sFactor.value || 1
  _applyOp(new THREE.Matrix4().makeScale(s, s, s))
}

function onReset() {
  elements.value = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
  emit('reset')
}

// Display: rows × cols so humans can read it (elements are column-major internally)
const matrixDisplay = computed(() => {
  const e = elements.value
  const rows = []
  for (let row = 0; row < 4; row++) {
    rows.push(
      [e[row], e[row+4], e[row+8], e[row+12]]
        .map(v => v.toFixed(5).padStart(10))
        .join('')
    )
  }
  return rows.join('\n')
})

const copyLabel = ref('Copy JSON (for backend)')
function copyMatrix() {
  const json = JSON.stringify(elements.value)
  navigator.clipboard?.writeText(json).then(() => {
    copyLabel.value = 'Copied!'
    setTimeout(() => { copyLabel.value = 'Copy JSON (for backend)' }, 1500)
  })
}

// Expose for parent to read the current matrix without waiting for an event
defineExpose({ elements })
</script>
