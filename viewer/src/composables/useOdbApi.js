/**
 * useOdbApi — 封装所有 L3 REST API 调用
 * 使用 axios（via src/utils/request.js）替代原生 fetch
 */
import http from '../utils/request'
import { useViewerStore } from '../store/viewer'

export function useOdbApi() {
  const store = useViewerStore()

  // ── Job list ──────────────────────────────────────────────────────────────
  function fetchJobs() {
    return http.get(`${store.baseUrl}/api/jobs`)
  }

  function submitJob(odbPath, name) {
    return http.post(`${store.baseUrl}/api/jobs`, { odb_path: odbPath, name })
  }

  // ── Metadata ──────────────────────────────────────────────────────────────
  async function fetchMeta() {
    const json = await http.get(store.getApiUrl('meta/overview'))
    const data = json.data
    data.fields = (data.fields ?? []).map(f => ({
      ...f,
      components: typeof f.components === 'string' ? JSON.parse(f.components) : f.components,
      positions:  typeof f.positions  === 'string' ? JSON.parse(f.positions)  : f.positions,
    }))
    return json
  }

  // ── Geometry ──────────────────────────────────────────────────────────────
  async function fetchGeometry(instance) {
    const res = await http.get(
      store.getApiUrl(`geometry/${encodeURIComponent(instance)}/render-buffers`),
      { responseType: 'arraybuffer' }
    )
    return res.data
  }

  async function fetchEdges(instance, type = 'mesh') {
    const endpoint = type === 'mesh'
      ? `geometry/${encodeURIComponent(instance)}/element-mesh-edges`
      : `geometry/${encodeURIComponent(instance)}/feature-edges`
    const res = await http.get(store.getApiUrl(endpoint), { responseType: 'arraybuffer' })
    return res.data
  }

  // ── Results / Colors ──────────────────────────────────────────────────────
  async function fetchFieldColors(instance, step, frameIdx, field, component, renderMode) {
    const params = new URLSearchParams({ instance, step, frame: frameIdx, field, component, mode: renderMode })
    const res = await http.get(
      store.getApiUrl(`results/frame-colors?${params}`),
      { responseType: 'arraybuffer' }
    )
    // res.headers 是 AxiosHeaders 对象，支持 .get('X-Val-Min') 大小写不敏感
    return { ab: res.data, headers: res.headers }
  }

  // ── Pick ──────────────────────────────────────────────────────────────────
  function fetchPick(instance, renderFaceIdx, nodeIdx, step, frameIdx, field, component, componentIdx, pickMode = 'element') {
    const params = new URLSearchParams({ instance, render_face_idx: renderFaceIdx, pick_mode: pickMode })
    if (nodeIdx     != null) params.set('node_idx',      nodeIdx)
    if (step)                params.set('step',          step)
    if (frameIdx    != null) params.set('frame_idx',     frameIdx)
    if (field)               params.set('field',         field)
    if (component)           params.set('component',     component)
    if (componentIdx != null) params.set('component_idx', componentIdx)
    return http.get(store.getApiUrl(`query/pick?${params}`))
  }

  async function fetchElementOutline(instance, renderFaceIdx) {
    const res = await http.get(
      store.getApiUrl(`geometry/${encodeURIComponent(instance)}/element-outline/${renderFaceIdx}`),
      { responseType: 'arraybuffer' }
    )
    return res.data
  }

  function fetchRenderFaces(instance, renderFaceIndices, mode) {
    return http.post(store.getApiUrl('query/render-faces'), {
      instance, render_face_indices: renderFaceIndices, mode
    })
  }

  // ── Nearest Face ──────────────────────────────────────────────────────────
  function fetchNearestFace(instance, x, y, z) {
    const params = new URLSearchParams({ instance, x, y, z })
    return http.get(store.getApiUrl(`query/nearest-face?${params}`))
  }

  // ── Surface Patch ─────────────────────────────────────────────────────────
  function fetchSurfacePatch(instance, renderFaceIdx, w, h) {
    return http.post(store.getApiUrl('query/surface-patch'), {
      instance, render_face_idx: renderFaceIdx, width: w, height: h
    })
  }

  // ── View Cut Section Mesh ─────────────────────────────────────────────────
  async function fetchSectionMesh(instance, axis, position, _flipped, signal) {
    const params = new URLSearchParams({ instance, axis, position })
    const res = await http.get(
      store.getApiUrl(`results/section-mesh?${params}`),
      { responseType: 'arraybuffer', signal }
    )
    return res.data
  }

  // ── Scalar Range (global normalization) ──────────────────────────────────
  async function fetchScalarRange(instances, step, frameIdx, field, {
    componentIdx = null,
    renderMode = 'smooth',
    resultGroup = null,
    set = null,
    featureAngle = null,
    averageThreshold = null,
    useGeometrySplit = null,
  } = {}) {
    const params = new URLSearchParams({
      instances: instances.join(','),
      step,
      frame: frameIdx,
      field,
      mode: renderMode,
    })
    if (componentIdx != null)      params.set('component_idx',      componentIdx)
    if (resultGroup)               params.set('result_group',        resultGroup)
    if (set)                       params.set('set',                 set)
    if (featureAngle != null)      params.set('feature_angle',       featureAngle)
    if (averageThreshold != null)  params.set('average_threshold',   averageThreshold)
    if (useGeometrySplit != null)  params.set('use_geometry_split',  useGeometrySplit)
    const res = await http.get(store.getApiUrl(`results/frame-scalar-range?${params}`))
    return res.data  // { global_min, global_max, instance_ranges }
  }

  // ── Deformed Shape ───────────────────────────────────────────────────────
  async function fetchDeformedPositions(instance, step, frameIdx, scale) {
    const params = new URLSearchParams({ instance, step, frame: frameIdx, scale })
    const res = await http.get(
      store.getApiUrl(`results/deformed-positions?${params}`),
      { responseType: 'arraybuffer' }
    )
    return res.data
  }

  async function fetchDeformSuggestScale(step, frameIdx, resultGroup) {
    const params = new URLSearchParams({ step, frame: frameIdx })
    if (resultGroup) params.set('result_group', resultGroup)
    const res = await http.get(store.getApiUrl(`results/deform-suggest-scale?${params}`))
    return res.data?.scale ?? 0
  }

  // ── Modal Harmonic Animation ──────────────────────────────────────────────
  async function fetchModalShape(instance, step, frameIdx, resultGroup) {
    const params = new URLSearchParams({ instance, step, frame: frameIdx })
    if (resultGroup) params.set('result_group', resultGroup)
    const res = await http.get(
      store.getApiUrl(`results/modal-shape?${params}`),
      { responseType: 'arraybuffer' }
    )
    return res.data
  }

  async function fetchModalAnimationFrames(instance, step, frameIdx, scale, nFrames, resultGroup) {
    const params = new URLSearchParams({ instance, step, frame: frameIdx, scale, n_frames: nFrames })
    if (resultGroup) params.set('result_group', resultGroup)
    const res = await http.get(
      store.getApiUrl(`results/modal-animation?${params}`),
      { responseType: 'arraybuffer' }
    )
    return res.data
  }

  // ── Color Code ────────────────────────────────────────────────────────────
  function fetchColorSchemes(instance) {
    return http.get(store.getApiUrl(`color-code/${encodeURIComponent(instance)}/schemes`))
  }

  async function fetchColorCode(instance, scheme, sets) {
    const params = new URLSearchParams({ scheme })
    if (sets && sets.length) params.set('set_names', sets.join(','))
    const res = await http.get(
      store.getApiUrl(`color-code/${encodeURIComponent(instance)}?${params}`),
      { responseType: 'arraybuffer' }
    )
    return { ab: res.data, headers: res.headers }
  }

  // ── Legend Entries ────────────────────────────────────────────────────────
  const ALL_SCHEMES = new Set(['section', 'section_assignment', 'etype', 'material', 'section_type'])

  function fetchLegendEntries(instance, scheme, setNames) {
    const params = new URLSearchParams({ scheme })
    if (setNames && setNames.length) params.set('set_names', setNames.join(','))
    if (ALL_SCHEMES.has(scheme)) params.set('all', 'true')
    return http.get(
      store.getApiUrl(`color-code/${encodeURIComponent(instance)}/legend-entries?${params}`)
    )
  }

  function saveLegendEntries(instance, scheme, entries) {
    const params = new URLSearchParams({ scheme })
    if (ALL_SCHEMES.has(scheme)) params.set('all', 'true')
    return http.post(
      store.getApiUrl(`color-code/${encodeURIComponent(instance)}/legend-entries?${params}`),
      entries
    )
  }

  function fetchLegend(instance, scheme, setNames) {
    const params = new URLSearchParams({ scheme })
    if (setNames && setNames.length) params.set('set_names', setNames.join(','))
    return http.get(
      store.getApiUrl(`color-code/${encodeURIComponent(instance)}/legend?${params}`)
    )
  }

  // ── Region Highlight (region-mesh-edges / region-outline) ────────────────
  async function fetchRegionMeshEdges(instance, scheme, region) {
    const params = new URLSearchParams({ scheme, region })
    const res = await http.get(
      store.getApiUrl(`color-code/${encodeURIComponent(instance)}/region-mesh-edges?${params}`),
      { responseType: 'arraybuffer' }
    )
    return res.data
  }

  async function fetchRegionOutline(instance, scheme, region) {
    const params = new URLSearchParams({ scheme, region })
    const res = await http.get(
      store.getApiUrl(`color-code/${encodeURIComponent(instance)}/region-outline?${params}`),
      { responseType: 'arraybuffer' }
    )
    return res.data
  }

  // ── Project API ───────────────────────────────────────────────────────────
  function fetchProjects() {
    return http.get(`${store.baseUrl}/api/projects`)
  }

  function fetchProject(projectId) {
    return http.get(`${store.baseUrl}/api/projects/${projectId}`)
  }

  function createProject(projectId, sourcePath) {
    return http.post(`${store.baseUrl}/api/projects`, {
      project_id: projectId,
      source_path: sourcePath,
    })
  }

  function addResultGroup(projectId, { sourcePath, resultGroup, displayName, parseOptions }) {
    return http.post(`${store.baseUrl}/api/projects/${projectId}/results`, {
      source_path:   sourcePath,
      result_group:  resultGroup,
      display_name:  displayName,
      parse_options: parseOptions,
    })
  }

  function renameResultGroup(projectId, resultGroup, displayName) {
    return http.patch(
      `${store.baseUrl}/api/projects/${projectId}/results/${encodeURIComponent(resultGroup)}`,
      { display_name: displayName }
    )
  }

  function deleteProject(projectId) {
    return http.delete(`${store.baseUrl}/api/projects/${projectId}`)
  }

  return {
    fetchJobs, submitJob,
    fetchMeta,
    fetchGeometry, fetchEdges,
    fetchFieldColors,
    fetchScalarRange,
    fetchPick, fetchElementOutline, fetchRenderFaces,
    fetchNearestFace, fetchSurfacePatch,
    fetchSectionMesh,
    fetchColorSchemes, fetchColorCode,
    fetchLegendEntries, saveLegendEntries, fetchLegend,
    fetchRegionMeshEdges, fetchRegionOutline,
    fetchDeformedPositions, fetchDeformSuggestScale,
    fetchModalShape, fetchModalAnimationFrames,
    fetchProjects, fetchProject, createProject,
    addResultGroup, renameResultGroup, deleteProject,
  }
}
