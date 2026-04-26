<template>
  <div class="card" v-if="store.meta">
    <h2>Instance</h2>
    <div class="instance-list">
      <label
        v-for="inst in store.meta.instances"
        :key="inst.instance_name"
        class="inst-row"
      >
        <input type="checkbox" :value="inst.instance_name" v-model="selected" />
        {{ inst.instance_name }}
      </label>
    </div>
    <div class="row" style="margin-bottom:4px">
      <button @click="selectAll">全选</button>
      <button @click="selected = []">清空</button>
    </div>
    <button @click="emit('loadGeometry', selected)">Load Geometry</button>
    <div class="row">
      <button @click="emit('loadMeshEdges')">Mesh Grid</button>
      <button @click="emit('loadFeatures')">Features</button>
    </div>
    <div style="font-size:9px;color:#8b949e">* Grid/Features 作用于当前活跃 Instance</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useViewerStore } from '../store/viewer'

const store = useViewerStore()
const emit  = defineEmits(['loadGeometry', 'loadMeshEdges', 'loadFeatures'])
const selected = ref([])

function selectAll() {
  selected.value = store.meta?.instances?.map(i => i.instance_name) ?? []
}
</script>
