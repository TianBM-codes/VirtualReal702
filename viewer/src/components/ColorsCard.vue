<template>
  <div class="card" v-if="store.meta">
    <h2>Frame Colors</h2>

    <!-- ── 普通字段模式 ── -->
    <template v-if="!sensitivityMode">
      <label>Field</label>
      <select v-model="field" @change="onFieldChange">
        <option v-for="f in store.meta.fields" :key="f.field_name" :value="f.field_name">{{ f.field_name }}</option>
      </select>

      <label>Component</label>
      <select v-model="selectedComp">
        <option v-for="c in compOptions" :key="c.value" :value="c.value">{{ c.label }}</option>
      </select>

      <label>Render Mode</label>
      <select v-model="renderMode">
        <option value="smooth">Smooth（节点插值）</option>
        <option value="flat">Flat（单元均色）</option>
      </select>

      <label>Step</label>
      <select v-model="step" @change="onStepChange">
        <option v-for="s in store.meta.steps" :key="s.step_name" :value="s.step_name">{{ s.step_name }}</option>
      </select>

      <label>Frame: <span>{{ frameIdx }}</span></label>
      <input type="range" v-model.number="frameIdx" :min="0" :max="maxFrame" />

      <button class="secondary" style="margin-top:6px" @click="sensitivityMode = true">
        切换到灵敏度云图 →
      </button>
      <button class="primary" @click="applyNormal">Apply Colors</button>
    </template>

    <!-- ── 灵敏度合并场模式 ── -->
    <template v-else>
      <label>灵敏度结果组</label>
      <select v-model="sensResultGroup" @change="onSensGroupChange">
        <option value="">-- 请选择 --</option>
        <option v-for="rg in sensResultGroups" :key="rg" :value="rg">{{ rg }}</option>
      </select>
      <button style="font-size:11px;margin-top:2px" @click="loadSensGroups">刷新列表</button>

      <template v-if="sensResultGroup">
        <label>字段（响应节点_分量）</label>
        <select v-model="sensField">
          <option value="">-- 请选择 --</option>
          <option v-for="f in sensFields" :key="f.field_name" :value="f.field_name">
            {{ f.field_name }}（节点 {{ f.response_node_label }} · {{ f.component }}）
          </option>
        </select>
      </template>

      <label>Step</label>
      <select v-model="step" @change="onStepChange">
        <option v-for="s in store.meta.steps" :key="s.step_name" :value="s.step_name">{{ s.step_name }}</option>
      </select>

      <label>Frame: <span>{{ frameIdx }}</span></label>
      <input type="range" v-model.number="frameIdx" :min="0" :max="maxFrame" />

      <span style="font-size:10px;color:#8b949e">固定 Flat（ELEMENT_NODAL，单元均色）</span>

      <button class="secondary" style="margin-top:6px" @click="sensitivityMode = false">
        ← 切换到普通字段
      </button>
      <button class="primary" :disabled="!sensField" @click="applySensitivity">Apply Colors</button>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useViewerStore } from '../store/viewer'
import http from '../utils/request'

const store = useViewerStore()
const emit  = defineEmits(['applyColors'])

// ── 通用 ──────────────────────────────────────────────────────────────────
const step     = ref('')
const frameIdx = ref(0)
const maxFrame = ref(0)

watch(() => store.meta, meta => {
  if (!meta) return
  if (meta.steps?.length) { step.value = meta.steps[0].step_name; onStepChange() }
  if (meta.fields?.length) { field.value = meta.fields[0].field_name; onFieldChange() }
}, { immediate: true })

function onStepChange() {
  const stepInfo = store.meta?.steps?.find(s => s.step_name === step.value)
  if (!stepInfo) return
  maxFrame.value = (stepInfo.num_frames || 1) - 1
  if (frameIdx.value > maxFrame.value) frameIdx.value = maxFrame.value
}

// ── 普通字段模式 ─────────────────────────────────────────────────────────
const sensitivityMode = ref(false)
const field       = ref('')
const renderMode  = ref('smooth')
const selectedComp = ref('')
const compOptions  = ref([])

function onFieldChange() {
  const fieldInfo = store.meta?.fields?.find(f => f.field_name === field.value)
  if (!fieldInfo) { compOptions.value = []; return }
  const opts = []
  const comps = fieldInfo.components || []
  const invs  = fieldInfo.invariants  || []
  if (comps.length === 0 && invs.length === 0) {
    opts.push({ value: '__scalar__', label: '— scalar —', idx: null, isInvariant: false })
  } else {
    comps.forEach((c, idx) => opts.push({ value: c, label: c, idx, isInvariant: false }))
    invs.forEach(inv => opts.push({ value: inv, label: inv, idx: null, isInvariant: true, invariantField: `${field.value}_${inv}` }))
  }
  compOptions.value = opts
  selectedComp.value = opts[0]?.value ?? ''
}

function applyNormal() {
  const opt = compOptions.value.find(o => o.value === selectedComp.value)
  emit('applyColors', {
    field: opt?.isInvariant ? opt.invariantField : field.value,
    componentVal: selectedComp.value,
    componentIdx: opt?.isInvariant ? null : (opt?.idx ?? null),
    renderMode: renderMode.value,
    step: step.value,
    frameIdx: frameIdx.value,
  })
}

// ── 灵敏度合并场模式 ──────────────────────────────────────────────────────
const sensResultGroups = ref([])
const sensResultGroup  = ref('')
const sensFields       = ref([])
const sensField        = ref('')

async function loadSensGroups() {
  if (!store.activeOdbId) return
  try {
    const res = await http.get(`${store.baseUrl}/api/odb/${store.activeOdbId}/sensitivity/result_groups`)
    sensResultGroups.value = res.data?.data?.result_groups ?? []
  } catch (e) {
    console.warn('loadSensGroups failed', e)
  }
}

async function onSensGroupChange() {
  sensField.value = ''
  sensFields.value = []
  if (!sensResultGroup.value) return
  try {
    const params = new URLSearchParams({ result_group: sensResultGroup.value })
    if (step.value) params.set('step', step.value)
    const res = await http.get(
      `${store.baseUrl}/api/odb/${store.activeOdbId}/sensitivity/fields?${params}`
    )
    sensFields.value = res.data?.data?.fields ?? []
  } catch (e) {
    console.warn('onSensGroupChange failed', e)
  }
}

// 切换到灵敏度模式时自动加载 result_groups
watch(sensitivityMode, val => {
  if (val) loadSensGroups()
})

function applySensitivity() {
  if (!sensField.value) return
  emit('applyColors', {
    field: sensField.value,
    componentVal: 'value',
    componentIdx: 0,
    renderMode: 'flat',
    step: step.value,
    frameIdx: frameIdx.value,
    resultGroup: sensResultGroup.value,
  })
}
</script>
