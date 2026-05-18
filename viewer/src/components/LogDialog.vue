<template>
  <Teleport to="body">
    <div v-if="projectId" class="log-overlay" @click.self="$emit('close')">
      <div class="log-dialog">

        <!-- 标题栏 -->
        <div class="log-header">
          <span class="log-title">解析日志 <span class="title-id">{{ projectId }}</span></span>
          <div class="log-header-btns">
            <button @click="doRefresh" :disabled="loading" title="重新拉取全部日志">刷新</button>
            <button @click="doClear"   :disabled="loading" title="清空日志记录">清空</button>
            <button class="close-btn" @click="$emit('close')">✕</button>
          </div>
        </div>

        <!-- 进度条 -->
        <div class="progress-wrap">
          <div class="progress-track">
            <div
              class="progress-fill"
              :class="{ done: currentPercent >= 100 }"
              :style="{ width: (currentPercent ?? 0) + '%' }"
            ></div>
          </div>
          <span class="progress-label">{{ currentPercent !== null ? currentPercent + '%' : '—' }}</span>
        </div>

        <!-- 日志区 -->
        <div class="log-area" ref="logAreaRef">
          <div v-if="!logs.length && !loading" class="log-empty">暂无日志</div>
          <div v-if="!logs.length && loading"  class="log-empty">加载中…</div>

          <div
            v-for="row in logs"
            :key="row.id"
            class="log-row"
            :class="'level-' + row.level"
          >
            <span class="log-ts">{{ formatTs(row.ts) }}</span>
            <span class="log-badge">{{ badgeText(row.level) }}</span>
            <span class="log-msg" v-html="row.message"></span>
          </div>
        </div>

        <!-- 底栏 -->
        <div class="log-footer">
          <span>{{ logs.length }} 条日志</span>
          <span v-if="loading" class="spin">⟳</span>
          <span v-if="currentPercent !== null" style="margin-left:auto">进度 {{ currentPercent }}%</span>
        </div>

      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, watch, onUnmounted, nextTick } from 'vue'
import { useViewerStore } from '../store/viewer'
import http from '../utils/request'

const props = defineProps({
  projectId: { type: String, default: null },
})
const emit = defineEmits(['close'])

const store      = ref(useViewerStore())
const logAreaRef = ref(null)
const logs       = ref([])
const loading    = ref(false)
const currentPercent = ref(null)

let sinceId   = 0
let pollTimer = null

// ── 工具函数 ──────────────────────────────────────────────────────────────────

function formatTs(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleTimeString('zh-CN', {
      hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return ts.slice(11, 19)
  }
}

function badgeText(level) {
  return { step: 'STEP', info: 'INFO', warn: 'WARN', error: ' ERR' }[level]
    ?? level.slice(0, 4).toUpperCase()
}

function isNearBottom() {
  const el = logAreaRef.value
  return el ? el.scrollTop + el.clientHeight >= el.scrollHeight - 32 : true
}

function scrollToBottom() {
  nextTick(() => {
    const el = logAreaRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// ── 数据拉取 ──────────────────────────────────────────────────────────────────

async function fetchLogs(reset = false) {
  const id = props.projectId
  if (!id) return
  if (reset) { logs.value = []; sinceId = 0 }
  loading.value = true
  const atBottom = reset || isNearBottom()
  try {
    const url  = `${store.value.baseUrl}/api/projects/${id}/logs?since_id=${sinceId}&limit=500`
    const res  = await http.get(url)
    const data = res.data
    if (data.logs?.length) {
      logs.value.push(...data.logs)
      sinceId = data.next_since_id
    }
    if (data.current_percent !== null && data.current_percent !== undefined) {
      currentPercent.value = data.current_percent
    }
    if (atBottom) scrollToBottom()
  } catch (e) {
    store.value.setStatus('日志加载失败：' + e.message, 'err')
  } finally {
    loading.value = false
  }
}

// ── 按钮操作 ──────────────────────────────────────────────────────────────────

async function doRefresh() {
  stopPolling()
  await fetchLogs(true)
  startPolling()
}

async function doClear() {
  if (!props.projectId) return
  if (!confirm('确认清空该 project 的全部日志？')) return
  try {
    await http.post(`${store.value.baseUrl}/api/projects/${props.projectId}/logs/clear`)
    logs.value    = []
    sinceId       = 0
    currentPercent.value = null
    store.value.setStatus('日志已清空', 'ok')
  } catch (e) {
    store.value.setStatus('清空失败：' + e.message, 'err')
  }
}

// ── 轮询 ─────────────────────────────────────────────────────────────────────

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => fetchLogs(false), 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

// ── 生命周期 ──────────────────────────────────────────────────────────────────

watch(() => props.projectId, async (id) => {
  stopPolling()
  if (id) {
    await fetchLogs(true)
    startPolling()
  } else {
    logs.value = []
    sinceId    = 0
    currentPercent.value = null
  }
}, { immediate: true })

onUnmounted(stopPolling)
</script>

<style scoped>
/* ── 遮罩层 ────────────────────────────────────────────────────────────────── */
.log-overlay {
  position: fixed;
  inset: 0;
  z-index: 9000;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ── 弹窗主体 ──────────────────────────────────────────────────────────────── */
.log-dialog {
  width: min(780px, 90vw);
  height: min(560px, 85vh);
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 16px 48px rgba(0,0,0,0.7);
}

/* ── 标题栏 ────────────────────────────────────────────────────────────────── */
.log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: #0d1117;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
}

.log-title {
  font-size: 13px;
  font-weight: 600;
  color: #c9d1d9;
}

.title-id {
  font-size: 11px;
  color: #8b949e;
  font-weight: 400;
  margin-left: 8px;
  font-family: monospace;
}

.log-header-btns {
  display: flex;
  gap: 6px;
  align-items: center;
}

.log-header-btns button {
  background: #21262d;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
}

.log-header-btns button:hover:not(:disabled) {
  border-color: #58a6ff;
  color: #58a6ff;
}

.log-header-btns button:disabled {
  opacity: 0.4;
  cursor: default;
}

.close-btn {
  font-size: 14px !important;
  padding: 2px 8px !important;
  color: #8b949e !important;
}

.close-btn:hover {
  color: #f85149 !important;
  border-color: #f85149 !important;
}

/* ── 进度条 ────────────────────────────────────────────────────────────────── */
.progress-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: #0d1117;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
}

.progress-track {
  flex: 1;
  height: 6px;
  background: #21262d;
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: #1f6feb;
  border-radius: 3px;
  transition: width 0.4s ease;
}

.progress-fill.done {
  background: #3fb950;
}

.progress-label {
  font-size: 11px;
  color: #8b949e;
  min-width: 34px;
  text-align: right;
}

/* ── 日志区 ────────────────────────────────────────────────────────────────── */
.log-area {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
  font-family: 'Consolas', 'Menlo', monospace;
  font-size: 12px;
  line-height: 1.5;
}

.log-empty {
  color: #8b949e;
  padding: 20px;
  text-align: center;
}

.log-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 1px 14px;
  border-left: 3px solid transparent;
}

.log-row:hover {
  background: rgba(255,255,255,0.03);
}

.level-step  { border-left-color: #1f6feb; }
.level-warn  { border-left-color: #d29922; }
.level-error { border-left-color: #f85149; }
.level-info  { border-left-color: transparent; }

.log-ts {
  color: #6e7681;
  white-space: nowrap;
  flex-shrink: 0;
  font-size: 11px;
  padding-top: 1px;
}

.log-badge {
  font-size: 10px;
  font-weight: 600;
  border-radius: 3px;
  padding: 1px 4px;
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0.3px;
}

.level-step  .log-badge { background: #1f4e88; color: #79c0ff; }
.level-warn  .log-badge { background: #5a3e1e; color: #e3b341; }
.level-error .log-badge { background: #5a1e1e; color: #ff7b72; }
.level-info  .log-badge { background: #21262d; color: #6e7681; }

.log-msg {
  flex: 1;
  white-space: pre-wrap;
  word-break: break-word;
  color: #c9d1d9;
}

/* ── v-html 内部 span 高亮色 ──────────────────────────────────────────────── */
.log-msg :deep(.kw)   { color: #60a5fa; font-weight: 600; }
.log-msg :deep(.num)  { color: #34d399; }
.log-msg :deep(.good) { color: #4ade80; }
.log-msg :deep(.warn) { color: #fbbf24; }
.log-msg :deep(.bad)  { color: #f87171; }

/* ── 底栏 ──────────────────────────────────────────────────────────────────── */
.log-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 14px;
  background: #0d1117;
  border-top: 1px solid #30363d;
  font-size: 11px;
  color: #6e7681;
  flex-shrink: 0;
}

.spin {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
</style>
