/**
 * modal-sync/api — 试验网格 / FEM 模型同步动画的后端调用。
 *
 * 独立模块:只用原生 fetch,不依赖 axios / pinia / 本 viewer 的任何代码,
 * 可整目录拷贝到其他前端项目。
 */

async function _json(resp) {
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text().catch(() => '')}`)
  const body = await resp.json()
  // 后端统一信封 { code, data, message }
  if (body && typeof body === 'object' && 'data' in body) return body.data
  return body
}

async function _arrayBuffer(resp) {
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text().catch(() => '')}`)
  return resp.arrayBuffer()
}

/**
 * POST /api/model/testMesh/syncAnimation
 * 返回 { test, fem, sync },详见 docs/l3/L3-API-Quick-Reference.md §13。
 */
export async function postSyncAnimation(baseUrl, body) {
  return _json(await fetch(`${baseUrl}/api/model/testMesh/syncAnimation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }))
}

/**
 * GET /api/odb/{projectId}/results/modal-animation
 * 解析二进制 [n_frames u32][n_verts u32][n_frames×n_verts×3 f32],
 * 返回 { nFrames, nVerts, frames: Float32Array[] }。
 */
export async function fetchFemModalFrames(baseUrl, projectId,
    { instance, step, frame, scale, nFrames, resultGroup }) {
  const params = new URLSearchParams({ instance, step, frame, scale, n_frames: nFrames })
  if (resultGroup) params.set('result_group', resultGroup)
  const ab = await _arrayBuffer(await fetch(
    `${baseUrl}/api/odb/${encodeURIComponent(projectId)}/results/modal-animation?${params}`))
  const dv = new DataView(ab)
  const nF = dv.getUint32(0, true)
  const nV = dv.getUint32(4, true)
  const raw = new Float32Array(ab, 8, nF * nV * 3)
  const frames = []
  for (let i = 0; i < nF; i++) frames.push(raw.slice(i * nV * 3, (i + 1) * nV * 3))
  return { nFrames: nF, nVerts: nV, frames }
}

/**
 * GET /api/odb/{projectId}/meta/overview — 取实例列表等元数据。
 */
export async function fetchMetaOverview(baseUrl, projectId, resultGroup) {
  const params = resultGroup ? `?result_group=${encodeURIComponent(resultGroup)}` : ''
  return _json(await fetch(
    `${baseUrl}/api/odb/${encodeURIComponent(projectId)}/meta/overview${params}`))
}

/**
 * GET /api/odb/{projectId}/geometry/{instance}/render-buffers
 * 返回 { positions: Float32Array [Nv*3], indices: Int32Array|null }。
 */
export async function fetchRenderBuffers(baseUrl, projectId, instance) {
  const ab = await _arrayBuffer(await fetch(
    `${baseUrl}/api/odb/${encodeURIComponent(projectId)}/geometry/${encodeURIComponent(instance)}/render-buffers`))
  const sections = parseL3BE(ab)
  return {
    positions: sections.positions?.data ?? null,
    indices: sections.indices?.data ?? null,
  }
}

/**
 * 最小 L3BE 解析器(与 viewer 的 useL3BE 相同格式,复制一份保持模块独立)。
 */
export function parseL3BE(ab) {
  const dv = new DataView(ab)
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3))
  if (magic !== 'L3BE') throw new Error('Invalid L3BE magic: ' + magic)
  const sectionCount = dv.getUint32(12, true)
  const sectionTableStart = dv.getUint32(16, true)
  const ENTRY_SIZE = 80
  const decoder = new TextDecoder('ascii')
  const sections = {}
  for (let i = 0; i < sectionCount; i++) {
    const base = sectionTableStart + i * ENTRY_SIZE
    const name = decoder.decode(new Uint8Array(ab, base, 32)).replace(/\0+$/, '')
    const dtypeCode = dv.getUint16(base + 32, true)
    const ndim = dv.getUint16(base + 34, true)
    const shape = []
    for (let d = 0; d < ndim; d++) shape.push(dv.getUint32(base + 36 + d * 4, true))
    const dataOffset = dv.getUint32(base + 52, true)
    const dataNBytes = dv.getUint32(base + 60, true)
    let data
    switch (dtypeCode) {
      case 2:  data = new Uint8Array(ab, dataOffset, dataNBytes); break
      case 5:  data = new Int32Array(ab, dataOffset, dataNBytes / 4); break
      case 9:  data = new Float32Array(ab, dataOffset, dataNBytes / 4); break
      default: data = new Uint8Array(ab, dataOffset, dataNBytes)
    }
    sections[name] = { data, shape, dtype: dtypeCode }
  }
  return sections
}
