<template>
  <div class="card" v-if="store.probeRows.length > 0">
    <div style="display:flex;align-items:center">
      <h2 style="flex:1">Probe Table</h2>
      <button @click="store.probeRows = []" style="font-size:10px;padding:1px 6px">Clear</button>
    </div>
    <div style="max-height:180px;overflow:auto">
      <table style="width:100%;border-collapse:collapse;font-size:10px">
        <thead>
          <tr>
            <th>#</th><th>Mode</th><th>Instance</th><th>ID</th><th>Type</th>
            <th>Coords</th><th>Value</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in store.probeRows" :key="i">
            <td>{{ i + 1 }}</td>
            <td>{{ row.mode }}</td>
            <td>{{ row.instance }}</td>
            <td>{{ row.id }}</td>
            <td>{{ row.type }}</td>
            <td>{{ row.coords }}</td>
            <td>{{ row.value }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { useViewerStore } from '../store/viewer'
const store = useViewerStore()
</script>
