<template>
  <div class="card" v-if="result">
    <h2>Pick Result</h2>
    <table>
      <tr><td>Instance</td>   <td class="val">{{ result.instance }}</td></tr>
      <tr><td>Triangle idx</td><td class="val">{{ result.render_face_idx }}</td></tr>
      <template v-if="store.pickMode === 'element'">
        <tr><td>Elem label</td> <td class="val">{{ result.odb?.elem_label }}</td></tr>
        <tr><td>Elem nodes</td> <td class="val">{{ result.odb?.elem_node_labels?.join(', ') }}</td></tr>
      </template>
      <template v-else>
        <tr><td>Node label</td> <td class="val">{{ result.odb?.node_label }}</td></tr>
        <tr><td>Face nodes</td> <td class="val">{{ result.odb?.candidate_node_labels?.join(', ') }}</td></tr>
      </template>
      <tr><td>Source</td>     <td class="val">{{ result._source }}</td></tr>
      <tr><td>Value</td>      <td class="val">{{ fmtNum(result._value) }}</td></tr>
    </table>
  </div>
</template>

<script setup>
import { useViewerStore } from '../store/viewer'

const store = useViewerStore()
const props = defineProps({ result: Object })

function fmtNum(x) {
  if (x == null) return '—'
  return typeof x === 'number' ? x.toExponential(4) : String(x)
}
</script>
