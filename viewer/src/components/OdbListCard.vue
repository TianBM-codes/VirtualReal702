<template>
  <div class="card" v-if="visible">
    <div class="card-header">
      <h2>ODB 文件</h2>
      <div class="tab-bar">
        <button :class="{ active: mode === 'legacy' }" @click="mode = 'legacy'">独立 ODB</button>
        <button :class="{ active: mode === 'project' }" @click="mode = 'project'">Project 模式</button>
      </div>
    </div>

    <template v-if="mode === 'legacy'">
      <div class="odb-list">
        <div
          v-for="job in jobs"
          :key="job.odb_id"
          class="odb-item"
          :class="{ active: store.activeOdbId === job.odb_id && !store.activeResultGroup }"
          @click="selectJob(job)"
        >
          <span class="odb-name">{{ job.display_name || job.odb_id.slice(0, 8) }}</span>
          <span class="odb-status" :class="job.status">{{ job.status }}</span>
        </div>
        <div v-if="!jobs.length" class="empty-hint">暂无 ODB 文件</div>
      </div>

      <div class="action-row">
        <button @click="showLegacySubmit = !showLegacySubmit" style="font-size: 11px">+ 提交 ODB</button>
      </div>

      <div v-if="showLegacySubmit" class="submit-form">
        <h2>提交新 ODB</h2>
        <label>ODB 路径（服务器绝对路径）</label>
        <div class="path-row">
          <input type="text" v-model="legacyPath" placeholder="/data/raw/model.odb" />
          <div class="path-actions">
            <button type="button" @click="openLocalFilePicker('legacyPath', '.odb')">选文件</button>
            <button type="button" @click="openLocalDirectoryPicker('legacyPath', ['.odb'])">选目录</button>
          </div>
        </div>
        <div v-if="pickerHints.legacyPath" class="path-hint" :class="pickerHintTypes.legacyPath">
          {{ pickerHints.legacyPath }}
        </div>
        <label>显示名称</label>
        <input type="text" v-model="legacyName" placeholder="车身模型-v3" />
        <div class="row">
          <button @click="showLegacySubmit = false">取消</button>
          <button class="primary" @click="onSubmitLegacy">提交</button>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="odb-list">
        <div v-for="proj in projects" :key="proj.project_id" class="project-item">
          <div
            class="project-header"
            :class="{ active: store.activeOdbId === proj.project_id && !store.activeResultGroup }"
            @click="onSelectProject(proj)"
          >
            <span class="expand-icon" @click.stop="toggleProject(proj.project_id)">
              {{ expandedProjects.has(proj.project_id) ? '▾' : '▸' }}
            </span>
            <span class="odb-name">{{ proj.project_id.slice(0, 12) }}…</span>
            <span class="odb-status" :class="proj.geom_status">{{ proj.geom_status }}</span>
            <button class="icon-btn danger" @click.stop="onDeleteProject(proj.project_id)" title="删除 project">✕</button>
          </div>

          <div v-if="expandedProjects.has(proj.project_id)" class="rg-list">
            <div
              v-for="rg in (proj.result_groups ?? [])"
              :key="rg.result_group"
              class="rg-item"
              :class="{ active: store.activeOdbId === proj.project_id && store.activeResultGroup === rg.result_group }"
            >
              <span
                class="rg-name"
                @click="selectResultGroup(proj.project_id, rg)"
                :title="rg.error_message || ''"
              >
                {{ rg.display_name }}
              </span>
              <span class="odb-status" :class="rg.status">{{ rg.status }}</span>
              <button class="icon-btn" @click="startRename(proj.project_id, rg)" title="重命名">✎</button>
            </div>
            <div v-if="!proj.result_groups?.length" class="empty-hint empty-indent">暂无结果组</div>

            <div v-if="renamingKey === proj.project_id" class="rename-form">
              <input
                v-model="renameValue"
                @keyup.enter="confirmRename"
                @keyup.escape="cancelRename"
                placeholder="新显示名"
              />
              <button @click="confirmRename">确认</button>
              <button @click="cancelRename">取消</button>
            </div>

            <button class="add-rg-btn" @click="openAddRg(proj.project_id)">+ 追加 ODB</button>
          </div>
        </div>
        <div v-if="!projects.length" class="empty-hint">暂无 Project</div>
      </div>

      <div class="action-row">
        <button @click="showCreateProject = !showCreateProject" style="font-size: 11px">+ 新建 Project</button>
      </div>

      <div v-if="showCreateProject" class="submit-form">
        <h2>新建 Project</h2>
        <label>Project ID（UUID，由调用方提供）</label>
        <input type="text" v-model="newProjectId" placeholder="proj-xxxxxxxx-xxxx-xxxx" />
        <label>源文件路径（服务器绝对路径，支持 .inp / .odb）</label>
        <div class="path-row">
          <input type="text" v-model="newProjectInp" placeholder="/data/model.inp 或 /data/model.odb" />
          <div class="path-actions">
            <button type="button" @click="openLocalFilePicker('newProjectInp', '.inp,.odb')">选文件</button>
            <button type="button" @click="openLocalDirectoryPicker('newProjectInp', ['.inp', '.odb'])">选目录</button>
          </div>
        </div>
        <div v-if="pickerHints.newProjectInp" class="path-hint" :class="pickerHintTypes.newProjectInp">
          {{ pickerHints.newProjectInp }}
        </div>
        <div class="row">
          <button @click="showCreateProject = false">取消</button>
          <button class="primary" @click="onCreateProject">提交</button>
        </div>
      </div>

      <div v-if="addRgProjectId" class="submit-form">
        <h2>追加 ODB 到 {{ addRgProjectId.slice(0, 12) }}…</h2>
        <label>ODB 路径（服务器绝对路径）</label>
        <div class="path-row">
          <input type="text" v-model="addRgPath" placeholder="/data/static.odb" />
          <div class="path-actions">
            <button type="button" @click="openLocalFilePicker('addRgPath', '.odb')">选文件</button>
            <button type="button" @click="openLocalDirectoryPicker('addRgPath', ['.odb'])">选目录</button>
          </div>
        </div>
        <div v-if="pickerHints.addRgPath" class="path-hint" :class="pickerHintTypes.addRgPath">
          {{ pickerHints.addRgPath }}
        </div>
        <label>结果组名（目录 key，提交后不可改）</label>
        <input type="text" v-model="addRgName" placeholder="静力工况A" />
        <label>显示名（可后续修改）</label>
        <input type="text" v-model="addRgDisplay" placeholder="静力工况A" />
        <div class="row">
          <button @click="addRgProjectId = null">取消</button>
          <button class="primary" @click="onAddResultGroup">提交</button>
        </div>
      </div>
    </template>

    <input ref="filePickerRef" type="file" class="picker-input" @change="onLocalFilePicked" />
    <input
      ref="directoryPickerRef"
      type="file"
      class="picker-input"
      webkitdirectory
      directory
      multiple
      @change="onLocalDirectoryPicked"
    />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, watch } from 'vue'
import { useViewerStore } from '../store/viewer'
import { useOdbApi } from '../composables/useOdbApi'

const props = defineProps({ visible: Boolean })
const emit = defineEmits(['odbSelected'])

const store = useViewerStore()
const api = useOdbApi()

const mode = ref('legacy')

const jobs = ref([])
const showLegacySubmit = ref(false)
const legacyPath = ref('')
const legacyName = ref('')
let legacyPollTimer = null

const projects = ref([])
const expandedProjects = reactive(new Set())
const showCreateProject = ref(false)
const newProjectId = ref('')
const newProjectInp = ref('')
const addRgProjectId = ref(null)
const addRgPath = ref('')
const addRgName = ref('')
const addRgDisplay = ref('')
const renamingKey = ref(null)
const renamingRg = ref(null)
const renameValue = ref('')
const filePickerRef = ref(null)
const directoryPickerRef = ref(null)
const pickerContext = ref(null)
const pickerHints = reactive({
  legacyPath: '',
  newProjectInp: '',
  addRgPath: '',
})
const pickerHintTypes = reactive({
  legacyPath: '',
  newProjectInp: '',
  addRgPath: '',
})
let projectPollTimer = null

function pathRefFor(targetKey) {
  const pathRefs = {
    legacyPath,
    newProjectInp,
    addRgPath,
  }
  return pathRefs[targetKey] || null
}

function pathLabelFor(targetKey) {
  const labels = {
    legacyPath: 'ODB 路径',
    newProjectInp: '源文件路径',
    addRgPath: '结果 ODB 路径',
  }
  return labels[targetKey] || '路径'
}

function normalizePickerPath(rawPath) {
  if (typeof rawPath !== 'string') return ''
  const text = rawPath.trim()
  if (!text) return ''
  if (text.toLowerCase().startsWith('file:///')) {
    try {
      return decodeURIComponent(text.slice('file:///'.length)).replace(/\//g, '\\')
    } catch {
      return text
    }
  }
  return text
}

function setPickerHint(targetKey, message, type = '') {
  if (!(targetKey in pickerHints)) return
  pickerHints[targetKey] = message
  pickerHintTypes[targetKey] = type
}

function resolveAbsolutePath(file, inputEl) {
  const candidates = [
    file?.path,
    file?.filepath,
    file?.fullPath,
    file?._path,
    inputEl?.files?.[0]?.path,
    inputEl?.files?.[0]?.filepath,
    inputEl?.files?.[0]?.fullPath,
    inputEl?.files?.[0]?._path,
    inputEl?.value,
  ]
  for (const candidate of candidates) {
    const normalized = normalizePickerPath(candidate)
    if (!normalized) continue
    if (normalized.toLowerCase().startsWith('c:\\fakepath\\')) continue
    return normalized
  }
  return null
}

function matchExtensions(fileName, extensions) {
  if (!extensions?.length) return true
  const lower = fileName.toLowerCase()
  return extensions.some(ext => lower.endsWith(ext))
}

function setPathFromPicker(targetKey, absolutePath, sourceLabel) {
  const targetRef = pathRefFor(targetKey)
  if (!targetRef) return
  targetRef.value = absolutePath
  setPickerHint(targetKey, `已从${sourceLabel}填入：${absolutePath}`, 'ok')
  store.setStatus(`${pathLabelFor(targetKey)}已通过${sourceLabel}填入`, 'ok')
}

function showPathAccessHint(targetKey, file) {
  const fileLabel = file?.webkitRelativePath || file?.name || '所选文件'
  setPickerHint(
    targetKey,
    `已选中 ${fileLabel}，但当前运行环境没有暴露本地绝对路径，请手动粘贴绝对路径。`,
    'err'
  )
  store.setStatus(
    `${pathLabelFor(targetKey)}无法自动读取本地绝对路径。当前浏览器可能屏蔽了真实路径，请手动粘贴，或使用支持暴露本地路径的桌面壳/本地客户端。`,
    'err'
  )
}

function openLocalFilePicker(targetKey, accept) {
  pickerContext.value = {
    targetKey,
    extensions: accept.split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
  }
  setPickerHint(targetKey, '', '')
  if (!filePickerRef.value) return
  filePickerRef.value.accept = accept
  filePickerRef.value.value = ''
  filePickerRef.value.click()
}

function openLocalDirectoryPicker(targetKey, extensions) {
  pickerContext.value = {
    targetKey,
    extensions: (extensions || []).map(s => s.trim().toLowerCase()).filter(Boolean),
  }
  setPickerHint(targetKey, '', '')
  if (!directoryPickerRef.value) return
  directoryPickerRef.value.value = ''
  directoryPickerRef.value.click()
}

function resetPickerInput(inputEl) {
  if (inputEl) inputEl.value = ''
}

function onLocalFilePicked(event) {
  const ctx = pickerContext.value
  const inputEl = event.target
  const file = inputEl?.files?.[0]
  if (!ctx || !file) {
    resetPickerInput(inputEl)
    return
  }
  if (!matchExtensions(file.name, ctx.extensions)) {
    store.setStatus(`所选文件格式不匹配：${file.name}`, 'err')
    resetPickerInput(inputEl)
    return
  }
  const absolutePath = resolveAbsolutePath(file, inputEl)
  if (!absolutePath) {
    showPathAccessHint(ctx.targetKey, file)
    resetPickerInput(inputEl)
    return
  }
  setPathFromPicker(ctx.targetKey, absolutePath, '本地文件选择')
  resetPickerInput(inputEl)
}

function pickSingleFileFromDirectory(files, extensions) {
  const matches = files.filter(file => matchExtensions(file.name, extensions))
  if (matches.length === 0) {
    return { file: null, error: '所选目录下没有匹配文件' }
  }
  if (matches.length === 1) {
    return { file: matches[0], error: null }
  }

  const rootLevelMatches = matches.filter(file => {
    const rel = file.webkitRelativePath || ''
    const parts = rel.split('/').filter(Boolean)
    return parts.length === 2
  })
  if (rootLevelMatches.length === 1) {
    return { file: rootLevelMatches[0], error: null }
  }

  return { file: null, error: '目录下匹配文件不唯一，请改用“选文件”或缩小目录范围' }
}

function onLocalDirectoryPicked(event) {
  const ctx = pickerContext.value
  const inputEl = event.target
  const files = Array.from(inputEl?.files || [])
  if (!ctx || files.length === 0) {
    resetPickerInput(inputEl)
    return
  }

  const { file, error } = pickSingleFileFromDirectory(files, ctx.extensions)
  if (!file) {
    store.setStatus(`${pathLabelFor(ctx.targetKey)}选择失败：${error}`, 'err')
    resetPickerInput(inputEl)
    return
  }

  const absolutePath = resolveAbsolutePath(file, inputEl)
  if (!absolutePath) {
    showPathAccessHint(ctx.targetKey, file)
    resetPickerInput(inputEl)
    return
  }
  setPathFromPicker(ctx.targetKey, absolutePath, '本地目录选择')
  resetPickerInput(inputEl)
}

async function refreshJobs() {
  try {
    const data = await api.fetchJobs()
    jobs.value = data.data
    const hasRunning = jobs.value.some(j => j.status.includes('running') || j.status === 'submitted')
    if (!hasRunning && legacyPollTimer) {
      clearInterval(legacyPollTimer)
      legacyPollTimer = null
    } else if (hasRunning && !legacyPollTimer) {
      legacyPollTimer = setInterval(refreshJobs, 3000)
    }
  } catch (e) {
    store.setStatus('刷新 job 列表失败：' + e.message, 'err')
  }
}

async function selectJob(job) {
  if (job.status !== 'ready') {
    store.setStatus(`ODB ${job.odb_id.slice(0, 8)} 状态为 ${job.status}，尚未就绪`, 'err')
    return
  }
  store.setActiveOdb(job.odb_id, null)
  emit('odbSelected', job.odb_id)
}

async function onSubmitLegacy() {
  try {
    await api.submitJob(legacyPath.value, legacyName.value)
    showLegacySubmit.value = false
    legacyPath.value = ''
    legacyName.value = ''
    store.setStatus('ODB 已提交，等待处理…', 'ok')
    if (!legacyPollTimer) legacyPollTimer = setInterval(refreshJobs, 3000)
    await refreshJobs()
  } catch (e) {
    store.setStatus('提交失败：' + e.message, 'err')
  }
}

async function refreshProjects() {
  try {
    const pending = projects.value.filter(p =>
      p.geom_status === 'pending' || p.geom_status === 'running' ||
      p.result_groups?.some(r => r.status === 'pending' || r.status === 'running')
    )
    for (const p of pending) {
      const res = await api.fetchProject(p.project_id)
      const idx = projects.value.findIndex(x => x.project_id === p.project_id)
      if (idx >= 0) projects.value[idx] = res.data
    }
    const stillPending = projects.value.some(p =>
      p.geom_status === 'pending' || p.geom_status === 'running' ||
      p.result_groups?.some(r => r.status === 'pending' || r.status === 'running')
    )
    if (!stillPending && projectPollTimer) {
      clearInterval(projectPollTimer)
      projectPollTimer = null
    }
  } catch (e) {
    store.setStatus('刷新 project 失败：' + e.message, 'err')
  }
}

function toggleProject(projectId) {
  if (expandedProjects.has(projectId)) expandedProjects.delete(projectId)
  else expandedProjects.add(projectId)
}

function onSelectProject(proj) {
  toggleProject(proj.project_id)
  if (proj.geom_status === 'pending' || proj.geom_status === 'running') {
    store.setStatus(`Project 几何解析中（${proj.geom_status}），请稍候…`, 'ok')
  } else if (proj.geom_status === 'error') {
    store.setStatus('Project 几何解析失败，无法加载', 'err')
  } else if (proj.geom_status === 'ready') {
    store.setActiveOdb(proj.project_id, null)
    emit('odbSelected', proj.project_id)
    store.setStatus('几何已加载。展开后点击结果组可叠加结果。', '')
  }
}

function selectResultGroup(projectId, rg) {
  if (rg.status !== 'ready') {
    store.setStatus(`结果组“${rg.display_name}”状态为 ${rg.status}，尚未就绪`, 'err')
    return
  }
  store.setActiveOdb(projectId, rg.result_group)
  emit('odbSelected', projectId)
}

async function onCreateProject() {
  try {
    await api.createProject(newProjectId.value, newProjectInp.value)
    const res = await api.fetchProject(newProjectId.value)
    projects.value.unshift(res.data)
    expandedProjects.add(newProjectId.value)
    showCreateProject.value = false
    newProjectId.value = ''
    newProjectInp.value = ''
    store.setStatus('Project 已创建，正在解析源文件…', 'ok')
    startProjectPolling()
  } catch (e) {
    store.setStatus('创建失败：' + e.message, 'err')
  }
}

function openAddRg(projectId) {
  addRgProjectId.value = projectId
  addRgPath.value = ''
  addRgName.value = ''
  addRgDisplay.value = ''
}

async function onAddResultGroup() {
  try {
    await api.addResultGroup(addRgProjectId.value, {
      sourcePath: addRgPath.value,
      resultGroup: addRgName.value,
      displayName: addRgDisplay.value || addRgName.value,
    })
    const res = await api.fetchProject(addRgProjectId.value)
    const idx = projects.value.findIndex(p => p.project_id === addRgProjectId.value)
    if (idx >= 0) projects.value[idx] = res.data
    addRgProjectId.value = null
    store.setStatus(`结果组“${addRgName.value}”已提交，等待解析…`, 'ok')
    startProjectPolling()
  } catch (e) {
    store.setStatus('提交失败：' + e.message, 'err')
  }
}

function startRename(projectId, rg) {
  renamingKey.value = projectId
  renamingRg.value = rg.result_group
  renameValue.value = rg.display_name
}

async function confirmRename() {
  if (!renamingKey.value || !renamingRg.value) return
  try {
    await api.renameResultGroup(renamingKey.value, renamingRg.value, renameValue.value)
    const res = await api.fetchProject(renamingKey.value)
    const idx = projects.value.findIndex(p => p.project_id === renamingKey.value)
    if (idx >= 0) projects.value[idx] = res.data
    store.setStatus('显示名已更新', 'ok')
  } catch (e) {
    store.setStatus('重命名失败：' + e.message, 'err')
  }
  cancelRename()
}

function cancelRename() {
  renamingKey.value = null
  renamingRg.value = null
  renameValue.value = ''
}

async function onDeleteProject(projectId) {
  if (!confirm(`确认删除 project ${projectId.slice(0, 12)}… 及其所有数据？`)) return
  try {
    await api.deleteProject(projectId)
    projects.value = projects.value.filter(p => p.project_id !== projectId)
    expandedProjects.delete(projectId)
    if (store.activeOdbId === projectId) store.setActiveOdb(null, null)
    store.setStatus('Project 已删除', 'ok')
  } catch (e) {
    store.setStatus('删除失败：' + e.message, 'err')
  }
}

function startProjectPolling() {
  if (projectPollTimer) return
  projectPollTimer = setInterval(refreshProjects, 3000)
}

watch(mode, async (val) => {
  if (val === 'project' && projects.value.length === 0) {
    try {
      const res = await api.fetchProjects()
      projects.value = res.data || []
      const hasPending = projects.value.some(p =>
        p.geom_status === 'pending' || p.geom_status === 'running' ||
        p.result_groups?.some(r => r.status === 'pending' || r.status === 'running')
      )
      if (hasPending) startProjectPolling()
    } catch {
      // /api/projects 列表接口为可选，失败静默
    }
  }
})

onMounted(refreshJobs)
onUnmounted(() => {
  if (legacyPollTimer) clearInterval(legacyPollTimer)
  if (projectPollTimer) clearInterval(projectPollTimer)
})

defineExpose({ refresh: refreshJobs })
</script>

<style scoped>
.tab-bar {
  display: flex;
  gap: 4px;
}

.tab-bar button {
  padding: 2px 8px;
  font-size: 11px;
  opacity: 0.5;
  background: transparent;
  border: 1px solid #444;
  border-radius: 3px;
  cursor: pointer;
  color: inherit;
}

.tab-bar button.active {
  opacity: 1;
  border-color: #58a6ff;
  color: #58a6ff;
}

.project-item {
  border-bottom: 1px solid #2a2a2a;
}

.project-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  cursor: pointer;
  user-select: none;
}

.project-header:hover {
  background: #1c2a3a;
}

.project-header.active {
  background: #1c3a5a;
}

.expand-icon {
  font-size: 10px;
  color: #8b949e;
}

.rg-list {
  padding-left: 16px;
}

.rg-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  cursor: pointer;
  border-radius: 3px;
}

.rg-item:hover {
  background: #1c2a3a;
}

.rg-item.active {
  background: #1c3a5a;
}

.rg-name {
  flex: 1;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.icon-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: #8b949e;
  padding: 1px 4px;
  font-size: 12px;
}

.icon-btn:hover {
  color: #58a6ff;
}

.icon-btn.danger:hover {
  color: #f85149;
}

.add-rg-btn {
  margin: 4px 0 6px;
  font-size: 11px;
  background: transparent;
  border: 1px dashed #444;
  border-radius: 3px;
  color: #8b949e;
  cursor: pointer;
  padding: 2px 8px;
  width: 100%;
}

.add-rg-btn:hover {
  border-color: #58a6ff;
  color: #58a6ff;
}

.rename-form {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}

.rename-form input {
  flex: 1;
  font-size: 11px;
  padding: 2px 4px;
  background: #0d1117;
  border: 1px solid #444;
  color: inherit;
  border-radius: 3px;
}

.action-row {
  padding: 4px 6px;
  border-top: 1px solid #2a2a2a;
}

.path-row {
  display: flex;
  gap: 6px;
  align-items: stretch;
}

.path-row > input {
  flex: 1;
  min-width: 0;
}

.path-actions {
  display: flex;
  gap: 4px;
}

.path-actions button {
  white-space: nowrap;
  font-size: 11px;
  padding: 2px 8px;
  background: transparent;
  border: 1px solid #444;
  border-radius: 3px;
  color: #8b949e;
  cursor: pointer;
}

.path-actions button:hover {
  border-color: #58a6ff;
  color: #58a6ff;
}

.path-hint {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.4;
  color: #8b949e;
  word-break: break-all;
}

.path-hint.ok {
  color: #3fb950;
}

.path-hint.err {
  color: #f0883e;
}

.picker-input {
  display: none;
}

.empty-hint {
  font-size: 11px;
  color: #8b949e;
  padding: 6px;
}

.empty-indent {
  padding-left: 8px;
}
</style>
