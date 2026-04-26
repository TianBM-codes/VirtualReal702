<template>
  <div class="card" v-if="result">
    <h2>Box Select</h2>
    <p style="font-size:11px;color:#e6edf3">{{ result.count ?? 0 }} 个结果</p>
    <div v-if="result.labels?.length" style="max-height:140px;overflow-y:auto;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:4px 6px;font-size:10px;color:#8b949e;line-height:1.6;word-break:break-all">
      {{ result.labels.join(', ') }}
    </div>
    <button @click="emit('clear')">Clear</button>
  </div>
</template>

<script setup>
defineProps({ result: Object })
defineEmits(['clear'])
</script>
