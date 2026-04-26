<template>
  <div class="card" v-if="Object.keys(store.instanceMeshes).length > 0">
    <h2>Mouse Mode</h2>
    <div class="row">
      <button :class="['primary', store.mouseMode === 'nav' ? '' : 'inactive']" @click="store.mouseMode = 'nav'">Navigate</button>
      <button :class="[store.mouseMode === 'pick' ? 'primary' : '']"           @click="store.mouseMode = 'pick'">Pick</button>
    </div>

    <template v-if="store.mouseMode === 'pick'">
      <label>Pick Mode</label>
      <div class="row">
        <button :class="['toggle', store.pickMode === 'element' ? 'active' : '']" @click="store.pickMode = 'element'">Element</button>
        <button :class="['toggle', store.pickMode === 'node' ? 'active' : '']" @click="store.pickMode = 'node'">Node</button>
      </div>
      <div style="margin-top:4px">
        <label>Deform Scale: <span>{{ store.deformScale.toFixed(1) }}</span></label>
        <input type="range" v-model.number="store.deformScale" min="0" max="100" step="0.1" />
      </div>
    </template>

    <div style="margin-top:4px">
      <label>Projection</label>
      <button class="toggle" :class="{ active: isOrtho }" @click="emit('toggleCamera')" style="width:100%">
        {{ isOrtho ? 'Ortho' : 'Persp' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useViewerStore } from '../store/viewer'

const store  = useViewerStore()
const emit   = defineEmits(['toggleCamera'])
const isOrtho = ref(false)

defineExpose({ isOrtho })
</script>
