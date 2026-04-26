<template>
  <div class="card" v-if="store.meta">
    <h2>Frame Colors</h2>

    <!-- Result Group 覆盖（留空 = 用当前激活的 result_group） -->
    <label>Result Group <span style="font-size:10px;color:#8b949e">（留空用默认）</span></label>
    <input v-model="resultGroupOverride" placeholder="e.g. merged_dsa" style="font-size:12px" />

    <label>Field</label>
    <select v-model="field" @change="onFieldChange">
      <option v-for="f in store.meta.fields" :key="f.field_name" :value="f.field_name">{{ f.field_name }}</option>
    </select>
    <!-- 合并场支持：手动输入字段名（如 d_999_U1_T） -->
    <input v-model="fieldOverride" placeholder="或手动输入字段名 d_999_U1_T" style="font-size:12px;margin-top:2px" />

    <label>Component</label>
    <select v-model="selectedComp" :disabled="isMergedField">
      <option v-for="c in compOptions" :key="c.value" :value="c.value">{{ c.label }}</option>
    </select>
    <span v-if="isMergedField" style="font-size:10px;color:#8b949e">合并场固定 component_idx=0</span>

    <label>Render Mode</label>
    <select v-model="renderMode" :disabled="isMergedField">
      <option value="smooth">Smooth（节点插值）</option>
      <option value="flat">Flat（单元均色）</option>
    </select>
    <span v-if="isMergedField" style="font-size:10px;color:#8b949e">合并场固定 Flat（ELEMENT_NODAL）</span>

    <label>Step</label>
    <select v-model="step" @change="onStepChange">
      <option v-for="s in store.meta.steps" :key="s.step_name" :value="s.step_name">{{ s.step_name }}</option>
    </select>

    <label>Frame: <span>{{ frameIdx }}</span></label>
    <input type="range" v-model.number="frameIdx" :min="0" :max="maxFrame" />

    <button class="primary" @click="apply">Apply Colors</button>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useViewerStore } from '../store/viewer'

const store = useViewerStore()
const emit  = defineEmits(['applyColors'])

const field               = ref('')
const fieldOverride       = ref('')   // 手动输入，优先级高于下拉
const resultGroupOverride = ref('')   // 留空 = 用 store.activeResultGroup
const renderMode          = ref('smooth')
const step                = ref('')
const frameIdx            = ref(0)
const maxFrame            = ref(0)
const selectedComp        = ref('')

// 判断是否是合并场（d_{label}_U1_T 格式）
const MERGED_FIELD_RE = /^d_\d+_U[123]_T$/i
const isMergedField = computed(() => {
  const fn = fieldOverride.value.trim() || field.value
  return MERGED_FIELD_RE.test(fn)
})

// Each option carries: { value, label, idx, isInvariant, invariantField }
const compOptions = ref([])

watch(() => store.meta, meta => {
  if (!meta?.fields?.length) return
  field.value = meta.fields[0].field_name
  onFieldChange()
  if (meta.steps?.length) { step.value = meta.steps[0].step_name; onStepChange() }
}, { immediate: true })

// 当切换到合并场时，自动设置 result_group、component、renderMode
watch(isMergedField, val => {
  if (val) {
    if (!resultGroupOverride.value) resultGroupOverride.value = 'merged_dsa'
    compOptions.value = [{ value: 'value', label: 'value', idx: 0, isInvariant: false }]
    selectedComp.value = 'value'
    renderMode.value = 'flat'   // 合并场是 ELEMENT_NODAL，强制单元均色
  }
})

function onFieldChange() {
  fieldOverride.value = ''  // 切换下拉时清除手动输入
  const fieldInfo = store.meta?.fields?.find(f => f.field_name === field.value)
  if (!fieldInfo) { compOptions.value = []; return }
  const opts = []
  const comps = fieldInfo.components || []
  const invs  = fieldInfo.invariants  || []
  if (comps.length === 0 && invs.length === 0) {
    opts.push({ value: '__scalar__', label: '— scalar —', idx: null, isInvariant: false })
  } else {
    comps.forEach((c, idx) => opts.push({ value: c, label: c, idx, isInvariant: false }))
    invs.forEach(inv => opts.push({
      value: inv,
      label: inv,
      idx: null,
      isInvariant: true,
      invariantField: `${field.value}_${inv}`,
    }))
  }
  compOptions.value = opts
  selectedComp.value = opts[0]?.value ?? ''
}

function onStepChange() {
  const stepInfo = store.meta?.steps?.find(s => s.step_name === step.value)
  if (!stepInfo) return
  maxFrame.value = (stepInfo.num_frames || 1) - 1
  if (frameIdx.value > maxFrame.value) frameIdx.value = maxFrame.value
}

function apply() {
  const activeField = fieldOverride.value.trim() || field.value
  const rg = resultGroupOverride.value.trim() || undefined  // undefined = 用 store 默认

  if (isMergedField.value) {
    // 合并场：固定 component_idx=0，单分量
    emit('applyColors', {
      field: activeField,
      componentVal: 'value',
      componentIdx: 0,
      renderMode: renderMode.value,
      step: step.value,
      frameIdx: frameIdx.value,
      resultGroup: rg,
    })
    return
  }

  const opt = compOptions.value.find(o => o.value === selectedComp.value)
  emit('applyColors', {
    field: opt?.isInvariant ? opt.invariantField : activeField,
    componentVal: selectedComp.value,
    componentIdx: opt?.isInvariant ? null : (opt?.idx ?? null),
    renderMode: renderMode.value,
    step: step.value,
    frameIdx: frameIdx.value,
    resultGroup: rg,
  })
}
</script>
