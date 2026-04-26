<template>
  <div class="card">
    <h2>Connection</h2>
    <label>API Base URL</label>
    <input type="text" v-model="store.baseUrl" />
    <button class="primary" @click="onConnect" :disabled="connecting">
      {{ connecting ? 'Connecting…' : 'Connect' }}
    </button>
    <div style="font-size:11px;color:#8b949e;margin-top:4px">
      {{ store.activeOdbId ? `当前: ${store.activeOdbId.slice(0,8)}…` : '未选择' }}
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useViewerStore } from '../store/viewer'
import http from '../utils/request'

const store = useViewerStore()
const emit  = defineEmits(['connected'])
const connecting = ref(false)

async function onConnect() {
  connecting.value = true
  store.baseUrl = store.baseUrl.replace(/\/+$/, '')  // 去掉末尾所有斜杠
  store.setStatus('Connecting…')
  try {
    const url = `${store.baseUrl}/api/health/live`
    await http.get(url)
    store.setStatus('已连接，请从 ODB 列表中选择文件', 'ok')
    emit('connected')
  } catch (e) {
    store.setStatus('连接失败: ' + e.message, 'err')
  } finally {
    connecting.value = false
  }
}
</script>
