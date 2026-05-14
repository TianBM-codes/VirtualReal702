<template>
  <div ref="viewportEl" style="flex:1;position:relative;overflow:hidden">
    <canvas ref="canvasEl" style="display:block;width:100%;height:100%" />
    <div ref="bboxOverlayEl" style="position:absolute;border:2px dashed #58a6ff;background:rgba(88,166,255,0.08);pointer-events:none;display:none" />
    <div ref="tooltipEl"  style="position:absolute;background:rgba(13,17,23,0.92);border:1px solid #58a6ff;border-radius:4px;padding:3px 8px;font-size:11px;color:#e6edf3;pointer-events:none;display:none;white-space:nowrap;z-index:10" />
    <div ref="coordEl"    style="position:absolute;bottom:10px;left:12px;font-size:11px;font-family:monospace;color:#8b949e;pointer-events:none;display:none" />
    <div style="position:absolute;bottom:10px;right:12px;font-size:10px;color:#484f58;pointer-events:none">
      Left-drag: rotate &nbsp;|&nbsp; Mid-drag: pan &nbsp;|&nbsp; Right-drag/Scroll: zoom &nbsp;|&nbsp; Click: pick &nbsp;|&nbsp; Shift+drag: box select
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { useViewerStore } from '../store/viewer'
import { useOdbApi } from '../composables/useOdbApi'
import { parseL3BE } from '../composables/useL3BE'
import http from '../utils/request'
import { acceleratedRaycast, computeBoundsTree, disposeBoundsTree } from 'three-mesh-bvh'

// Patch Three.js so every BufferGeometry/Mesh automatically supports BVH raycasting.
// computeBoundsTree() builds the index; after that all Raycaster calls on that
// geometry use O(log N) BVH traversal instead of O(N) brute-force triangle scan.
THREE.BufferGeometry.prototype.computeBoundsTree  = computeBoundsTree
THREE.BufferGeometry.prototype.disposeBoundsTree  = disposeBoundsTree
THREE.Mesh.prototype.raycast                      = acceleratedRaycast

const store = useViewerStore()
const api   = useOdbApi()
const emit  = defineEmits(['pick-result', 'bbox-result', 'colors-loaded', 'model-loaded', 'deform-anim-frame'])

const viewportEl    = ref(null)
const canvasEl      = ref(null)
const bboxOverlayEl = ref(null)
const tooltipEl     = ref(null)
const coordEl       = ref(null)

// Three.js objects
let renderer, scene, perspCam, orthoCam, camera, controls
let isOrtho = false
let pickMarker = null, hoverNodeMarker = null
// Per-instance edge lines (replaces single merged meshEdgesLine / featureEdgesLine).
// Splitting by instance lets us sync edge positions from per-instance mesh vertex buffers.
const meshEdgesLines    = {}   // instName → LineSegments
const featureEdgesLines = {}   // instName → LineSegments
const meshEdgeVtxIdxs    = {}  // instName → Uint32Array [E*2]  vertex indices for sync
const featureEdgeVtxIdxs = {}

// ── Region highlight lines (separate from global edge lines) ──────────────
const regionMeshEdgesLines = {}   // instName → LineSegments
const regionOutlineLines   = {}   // instName → LineSegments

// ── Line elements (beam / truss) ──────────────────────────────────────────
const lineMeshes     = {}   // instName → LineSegments (beam/truss)
const pointMeshes    = {}   // instName → Points (MASS)
const couplingMeshes = {}   // instName → LineSegments (RBE2 spiders)

// ── Orientation triads (model-level named CSYS) ───────────────────────────
let orientationLines = null   // single merged LineSegments for all triads

let _deformAnimActive = false  // animation loop guard

// Modal harmonic animation state
let _modalAnimRafId      = null   // requestAnimationFrame handle (shader mode)
let _modalAnimActive     = false  // precompute loop guard
let _modalAnimGeneration = 0      // 每次 stop 递增，用于中断正在加载的 startModalAnim
let _modalShaderRefs     = []     // { uniforms } refs, one per chunk (shader mode)
let _modalOrigMaterials  = []     // { inst, chunkIdx, mat } original materials to restore
let _modalFrameBuffers   = {}     // instName → Float32Array[] (precompute mode)
let _modalDispMap        = {}     // instName → Float32Array [Nv_global×3]，shader 模式边同步用

let clipPlane = null, modelBbox = null, axesGroup = null, modelGroup = null
let sectionMesh = null, sectionEdges = null, sectionAbortCtrl = null
let sectionFillVisible = true

// Hover state
let hoverFaceIdx = -1, savedHoverAttr = null
let hoverHighlightLine = null, hoverDebounceTimer = null

// BBox/Pick
let bboxStart = null, isBboxDrag = false, suppressNextClick = false
let bboxHighlight = null, pickHighlightLine = null, normalArrow = null

// Mouse-move throttle: store the latest event and process it once per animation frame.
let _pendingMouseMove = null

// Patch highlight state
let patchHighlightFaces = [], patchSavedAttrs = new Map(), patchRectLine = null

// Original positions per instance — saved at load time for deform reset
const origPositions = {}

// Colormap texture placeholder — wired up externally. Layout assumed:
//   uv.y ≈ 0.49 → normal data ramp (u drives color ramp)
//   uv.x = 0.5, uv.y = 0.49 → green (default loaded state)
//   uv.y  < 0.25 → grey (cleared / neutral)
//   uv.y  > 0.5  → red   (hover / pick / highlight)
let colormapTexture = null

  function makeTestColormapTexture() {                                                                                                                                                                                                      
    const W = 512, H = 128                                                                                                                                                                                                                  
    const canvas = document.createElement('canvas')                                                                                                                                                                                         
    canvas.width = W
    canvas.height = H                                                                                                                                                                                                                       
    const ctx = canvas.getContext('2d')

    // Three.js UV: v=0 → canvas 底部，v=1 → canvas 顶部                                                                                                                                                                                    
    // canvasY = (1 - v) * H
                                                                                                                                                                                                                                            
    // v > 0.5  → canvasY < H/2  → 顶半部 → 红色（hover/高亮）                                                                                                                                                                              
    ctx.fillStyle = 'hsl(0, 100%, 58%)'
    ctx.fillRect(0, 0, W, H / 2)                                                                                                                                                                                                            
                  
    // v < 0.25 → canvasY > 3H/4 → 底四分之一 → 灰色（cleared）                                                                                                                                                                             
    ctx.fillStyle = 'hsl(0, 0%, 55%)'
    ctx.fillRect(0, Math.round(H * 0.75), W, H)                                                                                                                                                                                             
                  
    // v 0.25..0.5 → canvasY H/2..3H/4 → 中间带 → 色谱（data ramp）                                                                                                                                                                         
    // v=0.49 → canvasY≈65 → 落在这个带里 ✓
    // u=0 → hue=240 蓝，u=0.5 → hue=120 绿，u=1 → hue=0 红                                                                                                                                                                                 
    const bandY = H / 2                                                                                                                                                                                                                     
    const bandH = Math.round(H / 4)                                                                                                                                                                                                         
    for (let x = 0; x < W; x++) {                                                                                                                                                                                                           
      const u = x / (W - 1)                                                                                                                                                                                                                 
      ctx.fillStyle = `hsl(${Math.round(240 * (1 - u))}, 100%, 50%)`
      ctx.fillRect(x, bandY, 1, bandH)                                                                                                                                                                                                      
    }
                                                                                                                                                                                                                                            
    const tex = new THREE.CanvasTexture(canvas)
    tex.needsUpdate = true
    return tex                                                                                                                                                                                                                              
  }

  colormapTexture = makeTestColormapTexture()

let animFrameId = null
let needsRender = false
function requestRender() { needsRender = true }

let resizeObserver = null
let currentResultCtx = null   // saved when applyColors succeeds; forwarded in ray-pick

// ── Init ──────────────────────────────────────────────────────────────────
onMounted(() => { initThree(); animate() })
onUnmounted(() => {
  if (animFrameId) cancelAnimationFrame(animFrameId)
  resizeObserver?.disconnect()
  if (renderer) {
    renderer.domElement.removeEventListener('click',     onCanvasClick)
    renderer.domElement.removeEventListener('mousedown', onMouseDown)
    renderer.domElement.removeEventListener('mousemove', onMouseMove)
    renderer.domElement.removeEventListener('mouseup',   onMouseUp)
  }
  controls?.dispose()
  scene?.traverse(obj => {
    obj.geometry?.dispose()
    if (obj.material) {
      if (Array.isArray(obj.material)) obj.material.forEach(m => m.dispose())
      else obj.material.dispose()
    }
  })
  renderer?.dispose()
})

function initThree() {
  const canvas = canvasEl.value
  const vp     = viewportEl.value

  renderer = new THREE.WebGLRenderer({ canvas, antialias: false })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1))
  renderer.localClippingEnabled = true

  // WebGL capability detection — open DevTools Console to read
  const gl = renderer.getContext()
  const isWebGL2 = gl instanceof WebGL2RenderingContext
  const hasUint32Idx = isWebGL2 || !!gl.getExtension('OES_element_index_uint')
  console.log('[WebGL] version:', isWebGL2 ? 'WebGL2' : 'WebGL1')
  console.log('[WebGL] OES_element_index_uint:', hasUint32Idx)
  console.log('[WebGL] renderer:', gl.getParameter(gl.RENDERER))
  console.log('[WebGL] uint32 index safe:', hasUint32Idx ? 'YES ✓' : 'NO ✗ — instances >65535 vertices will corrupt')

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x0d1117)

  // modelGroup holds all geometry meshes and edges so a single matrix controls the model transform.
  modelGroup = new THREE.Group()
  modelGroup.matrixAutoUpdate = false
  scene.add(modelGroup)

  scene.add(new THREE.AmbientLight(0xffffff, 0.6))
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8)
  dirLight.position.set(1, 1, 1).normalize()
  scene.add(dirLight)

  // Pick marker (red)
  pickMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.02, 16, 16),
    new THREE.MeshBasicMaterial({ color: 0xff0000, depthTest: false, transparent: true, opacity: 0.8 })
  )
  pickMarker.visible = false; pickMarker.renderOrder = 999; scene.add(pickMarker)

  // Hover node marker (cyan)
  hoverNodeMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.02, 8, 8),
    new THREE.MeshBasicMaterial({ color: 0x00aaff, depthTest: false, transparent: true, opacity: 0.8 })
  )
  hoverNodeMarker.visible = false; hoverNodeMarker.renderOrder = 998; scene.add(hoverNodeMarker)

  const aspect = vp.clientWidth / vp.clientHeight
  perspCam = new THREE.PerspectiveCamera(45, aspect, 0.001, 100000)
  perspCam.position.set(0, 0, 10)
  orthoCam = new THREE.OrthographicCamera(-5, 5, 5 / aspect, -5 / aspect, -100000, 100000)
  orthoCam.position.set(0, 0, 10)
  camera = perspCam

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.mouseButtons  = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.DOLLY }
  controls.screenSpacePanning = true

  resizeObserver = new ResizeObserver(() => {
    renderer.setSize(vp.clientWidth, vp.clientHeight)
    const a = vp.clientWidth / vp.clientHeight
    perspCam.aspect = a; perspCam.updateProjectionMatrix()
    if (isOrtho) {
      const h = orthoCam.top - orthoCam.bottom
      orthoCam.left = -h * a / 2; orthoCam.right = h * a / 2; orthoCam.updateProjectionMatrix()
    }
  })
  resizeObserver.observe(vp)
  renderer.setSize(vp.clientWidth, vp.clientHeight)

  renderer.domElement.addEventListener('click',     onCanvasClick)
  renderer.domElement.addEventListener('mousedown', onMouseDown)
  renderer.domElement.addEventListener('mousemove', onMouseMove)
  renderer.domElement.addEventListener('mouseup',   onMouseUp)
}

function animate() {
  animFrameId = requestAnimationFrame(animate)
  const moving = controls.update()   // damping 期间返回 true，保证旋转平滑

  // Process at most one mouse-move per frame.  The browser can fire 200+ mousemove
  // events per second; executing a full scene raycast for every one of them on a
  // large model is the main source of hover lag.
  if (_pendingMouseMove) {
    _processMouseMove(_pendingMouseMove)
    _pendingMouseMove = null
    needsRender = true
  }

  if (moving || needsRender) {
    renderer.render(scene, camera)
    needsRender = false
  }
}

// ── Camera ────────────────────────────────────────────────────────────────
function fitCameraToBox(box) {
  const center = new THREE.Vector3(), size = new THREE.Vector3()
  box.getCenter(center); box.getSize(size)
  const maxDim = Math.max(size.x, size.y, size.z) || 1
  const radius = size.length() || maxDim
  const near = Math.max(radius / 10000, 0.01)
  const far = Math.max(radius * 100, near * 1000)
  modelBbox = { min: box.min.clone(), max: box.max.clone() }

  const d = maxDim * 2.5
  perspCam.position.copy(center).add(new THREE.Vector3(0, 0, d))
  perspCam.near = near; perspCam.far = far; perspCam.zoom = 1
  perspCam.updateProjectionMatrix()

  const vp = viewportEl.value
  const aspect = vp.clientWidth / vp.clientHeight
  const h = 2 * d * Math.tan(perspCam.fov * Math.PI / 180 / 2)
  orthoCam.left = -h * aspect / 2; orthoCam.right = h * aspect / 2
  orthoCam.top = h / 2; orthoCam.bottom = -h / 2
  orthoCam.near = -far; orthoCam.far = far
  orthoCam.position.copy(perspCam.position); orthoCam.quaternion.copy(perspCam.quaternion)
  orthoCam.zoom = 1; orthoCam.updateProjectionMatrix()

  camera.position.copy(perspCam.position); camera.quaternion.copy(perspCam.quaternion)
  controls.target.copy(center); controls.update()
  hoverNodeMarker.scale.setScalar(maxDim * 0.075)  // hover 预览
  pickMarker.scale.setScalar(maxDim * 0.12)         // pick 持久标记，比 hover 大 60%
}

function toggleCamera() {
  const target = controls.target.clone()
  const vp = viewportEl.value, aspect = vp.clientWidth / vp.clientHeight
  if (!isOrtho) {
    const d = perspCam.position.distanceTo(target)
    const h = 2 * d * Math.tan(perspCam.fov * Math.PI / 180 / 2)
    orthoCam.left = -h * aspect / 2; orthoCam.right = h * aspect / 2
    orthoCam.top = h / 2; orthoCam.bottom = -h / 2
    orthoCam.position.copy(perspCam.position); orthoCam.quaternion.copy(perspCam.quaternion)
    orthoCam.zoom = 1; orthoCam.updateProjectionMatrix()
    camera = orthoCam; isOrtho = true
  } else {
    const visH   = (orthoCam.top - orthoCam.bottom) / orthoCam.zoom
    const d      = visH / (2 * Math.tan(perspCam.fov * Math.PI / 180 / 2))
    const dir    = orthoCam.position.clone().sub(target)
    if (dir.length() < 1e-6) dir.set(0, 0, 1)
    perspCam.position.copy(target).addScaledVector(dir.normalize(), d)
    perspCam.quaternion.copy(orthoCam.quaternion); perspCam.zoom = 1; perspCam.updateProjectionMatrix()
    camera = perspCam; isOrtho = false
  }
  controls.object = camera; controls.target.copy(target); controls.update()
  return isOrtho
}

// ── Axes ──────────────────────────────────────────────────────────────────
function _makeAxisLabel(letter, hexColor) {
  const canvas = document.createElement('canvas')
  canvas.width = 64; canvas.height = 64
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, 64, 64)
  ctx.font = 'bold 52px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
  ctx.fillStyle = hexColor; ctx.fillText(letter, 32, 32)
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false, transparent: true }))
  sprite.renderOrder = 1000
  return sprite
}

function buildAxes(length) {
  if (axesGroup) { scene.remove(axesGroup); axesGroup = null }
  axesGroup = new THREE.Group()
  const AXES = [
    { dir: new THREE.Vector3(1,0,0), color: 0xff3333, label:'X', hex:'#ff3333' },
    { dir: new THREE.Vector3(0,1,0), color: 0x33cc33, label:'Y', hex:'#33cc33' },
    { dir: new THREE.Vector3(0,0,1), color: 0x4499ff, label:'Z', hex:'#4499ff' },
  ]
  for (const ax of AXES) {
    const arrow = new THREE.ArrowHelper(ax.dir, new THREE.Vector3(0,0,0), length, ax.color, length*0.2, length*0.1)
    arrow.renderOrder = 999; axesGroup.add(arrow)
    const sprite = _makeAxisLabel(ax.label, ax.hex)
    sprite.position.copy(ax.dir).multiplyScalar(length * 1.18)
    sprite.scale.setScalar(length * 0.28); axesGroup.add(sprite)
  }
  scene.add(axesGroup)
  requestRender()
}

// ── Clip plane helpers ────────────────────────────────────────────────────
function _facePassesClip(fi, posArr, idxArr) {
  if (!clipPlane) return true
  const _v = new THREE.Vector3()
  for (let i = 0; i < 3; i++) {
    const vi = idxArr ? idxArr[fi*3+i] : fi*3+i
    if (clipPlane.distanceToPoint(_v.set(posArr[vi*3], posArr[vi*3+1], posArr[vi*3+2])) >= 0) return true
  }
  return false
}

function _applyClipping(planes) {
  Object.values(store.instanceMeshes).forEach(im => {
    for (const c of im.chunks) if (c.mesh.material) c.mesh.material.clippingPlanes = planes
  })
  Object.values(meshEdgesLines).forEach(l    => { if (l?.material) l.material.clippingPlanes = planes })
  Object.values(featureEdgesLines).forEach(l => { if (l?.material) l.material.clippingPlanes = planes })
  Object.values(lineMeshes).forEach(l        => { if (l?.material) l.material.clippingPlanes = planes })
  Object.values(pointMeshes).forEach(p       => { if (p?.material) p.material.clippingPlanes = planes })
  Object.values(couplingMeshes).forEach(l    => { if (l?.material) l.material.clippingPlanes = planes })
  if (orientationLines?.material) orientationLines.material.clippingPlanes = planes
  requestRender()
}

function updateClipPlane({ active, axis, position, flipped, sectionFill }) {
  sectionFillVisible = sectionFill
  if (sectionMesh) sectionMesh.visible = sectionFill

  if (!active || !modelBbox) {
    _applyClipping([]); clipPlane = null
    clearSectionMesh(); return
  }

  const sign = flipped ? -1 : 1
  clipPlane = new THREE.Plane(
    new THREE.Vector3(axis==='x'?sign:0, axis==='y'?sign:0, axis==='z'?sign:0),
    -sign * position
  )
  _applyClipping([clipPlane])
  fetchSectionMesh(axis, position, flipped)
}

// ── Section mesh (View Cut Phase 2) ───────────────────────────────────────
async function fetchSectionMesh(axis, position, flipped) {
  if (!store.currentInstance) return
  if (sectionAbortCtrl) sectionAbortCtrl.abort()
  sectionAbortCtrl = new AbortController()
  const url = store.getApiUrl(
    `results/section-mesh?instance=${encodeURIComponent(store.currentInstance)}&axis=${axis.toUpperCase()}&position=${position}`
  )
  try {
    const res = await http.get(url, { responseType: 'arraybuffer', signal: sectionAbortCtrl.signal })
    const triCount = parseInt(res.headers.get('X-Tri-Count') || '0')
    disposeSectionObjects()
    if (triCount === 0) return
    const sec = parseL3BE(res.data)

    const fillGeo = new THREE.BufferGeometry()
    fillGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(sec.vertices.data), 3))
    fillGeo.computeVertexNormals()
    const fillMat = new THREE.MeshBasicMaterial({ color: 0x6080a0, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 4 })
    sectionMesh = new THREE.Mesh(fillGeo, fillMat)
    sectionMesh.visible = sectionFillVisible
    scene.add(sectionMesh)

    if (sec.edge_verts?.data.length > 0) {
      const edgeGeo = new THREE.BufferGeometry()
      edgeGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(sec.edge_verts.data), 3))
      sectionEdges = new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial({ color: 0x222222 }))
      sectionEdges.renderOrder = 1; scene.add(sectionEdges)
    }
    requestRender()
  } catch (e) {
    if (e.name === 'AbortError') return
    store.setStatus('Section mesh: ' + e.message, 'err')
  }
}

function disposeSectionObjects() {
  if (sectionMesh)  { scene.remove(sectionMesh);  sectionMesh.geometry.dispose();  sectionMesh.material.dispose();  sectionMesh  = null }
  if (sectionEdges) { scene.remove(sectionEdges); sectionEdges.geometry.dispose(); sectionEdges.material.dispose(); sectionEdges = null }
  requestRender()
}

function clearSectionMesh() {
  if (sectionAbortCtrl) { sectionAbortCtrl.abort(); sectionAbortCtrl = null }
  disposeSectionObjects()
}

// ── Pick helpers ──────────────────────────────────────────────────────────
function faceNormal(posArr, fi, idxArr) {
  const [v0, v1, v2] = idxArr ? [idxArr[fi*3], idxArr[fi*3+1], idxArr[fi*3+2]] : [fi*3, fi*3+1, fi*3+2]
  const ex = posArr[v1*3]-posArr[v0*3], ey = posArr[v1*3+1]-posArr[v0*3+1], ez = posArr[v1*3+2]-posArr[v0*3+2]
  const fx = posArr[v2*3]-posArr[v0*3], fy = posArr[v2*3+1]-posArr[v0*3+1], fz = posArr[v2*3+2]-posArr[v0*3+2]
  const nx = ey*fz-ez*fy, ny = ez*fx-ex*fz, nz = ex*fy-ey*fx
  const len = Math.sqrt(nx*nx+ny*ny+nz*nz)
  return len > 0 ? [nx/len, ny/len, nz/len] : [0,0,0]
}

function facesCoplanar(posArr, fi1, fi2, idxArr) {
  const n1 = faceNormal(posArr, fi1, idxArr), n2 = faceNormal(posArr, fi2, idxArr)
  return Math.abs(n1[0]*n2[0]+n1[1]*n2[1]+n1[2]*n2[2]) > 0.9998
}

function buildElementEdges(faceIndices, elemIdPerFace) {
  const im = store.instanceMeshes[store.currentInstance]
  if (!im) return new Float32Array()
  const posArr = im.globalPositions
  const idxArr = im.globalIndices
  function vkey(vi) {
    return `${Math.round(posArr[vi*3]*1e4)},${Math.round(posArr[vi*3+1]*1e4)},${Math.round(posArr[vi*3+2]*1e4)}`
  }
  const edgeMap = new Map()
  for (let j = 0; j < faceIndices.length; j++) {
    const fi = faceIndices[j], eid = elemIdPerFace ? elemIdPerFace[j] : j
    const v = idxArr ? [idxArr[fi*3], idxArr[fi*3+1], idxArr[fi*3+2]] : [fi*3, fi*3+1, fi*3+2]
    for (const [a, b] of [[v[0],v[1]], [v[1],v[2]], [v[2],v[0]]]) {
      const ka = vkey(a), kb = vkey(b), key = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`
      if (edgeMap.has(key)) { const e = edgeMap.get(key); e.count++; e.fi2 = fi; e.eid2 = eid }
      else edgeMap.set(key, { count:1, a, b, fi1:fi, fi2:-1, eid1:eid, eid2:-1 })
    }
  }
  const pts = []
  for (const [, e] of edgeMap) {
    if (e.count === 2 && e.fi2 >= 0 && e.eid1 === e.eid2 && facesCoplanar(posArr, e.fi1, e.fi2, idxArr)) continue
    pts.push(posArr[e.a*3], posArr[e.a*3+1], posArr[e.a*3+2], posArr[e.b*3], posArr[e.b*3+1], posArr[e.b*3+2])
  }
  return new Float32Array(pts)
}

function clearNormalArrow() {
  if (normalArrow) { scene.remove(normalArrow); normalArrow = null; requestRender() }
}

function showNormalArrow(origin, normalVec, length = null) {
  if (length == null) length = arrowLength()
  clearNormalArrow()
  const dir = new THREE.Vector3(...normalVec).normalize()
  normalArrow = new THREE.ArrowHelper(dir, new THREE.Vector3(...origin), length, 0xff6600, length*0.3, length*0.15)
  normalArrow.renderOrder = 998; scene.add(normalArrow)
  requestRender()
}

function arrowLength() {
  const im = store.currentInstance && store.instanceMeshes[store.currentInstance]
  if (!im) return 0.01
  const box = new THREE.Box3().setFromObject(im.group)
  const size = new THREE.Vector3(); box.getSize(size)
  return Math.max(size.x, size.y, size.z) * 0.08
}

function clearPickHighlight() {
  if (pickHighlightLine) { scene.remove(pickHighlightLine); pickHighlightLine.geometry.dispose(); pickHighlightLine = null }
  if (pickMarker) pickMarker.visible = false
  clearNormalArrow()
  requestRender()
}

function clearBboxHighlight() {
  if (bboxHighlight) { scene.remove(bboxHighlight); bboxHighlight.geometry.dispose(); bboxHighlight = null; requestRender() }
}

// ── Face color helpers (indexed & non-indexed geometry) ───────────────────
// Colormap texture convention:
//   uv.y ≈ 0.49 → data ramp; uv.x drives color along ramp
//   uv.y  < 0.25 → grey (clear / neutral)
//   uv.y  > 0.5  → red (highlight / hover / pick)
const UV_DEFAULT_U    = 0.5
const UV_DATA_V       = 0.49
const UV_CLEAR_V      = 0.10   // < 0.25
const UV_HIGHLIGHT_V  = 0.90   // > 0.5

function faceVertices(geo, fi) {
  if (geo.index) { const a = geo.index.array; return [a[fi*3], a[fi*3+1], a[fi*3+2]] }
  return [fi*3, fi*3+1, fi*3+2]
}
function saveFaceColors(cf, fi, geo) {
  const [v0,v1,v2] = faceVertices(geo, fi)
  return [...cf.slice(v0*3,v0*3+3), ...cf.slice(v1*3,v1*3+3), ...cf.slice(v2*3,v2*3+3)]
}
function restoreFaceColors(cf, fi, geo, saved) {
  const [v0,v1,v2] = faceVertices(geo, fi)
  cf[v0*3]=saved[0]; cf[v0*3+1]=saved[1]; cf[v0*3+2]=saved[2]
  cf[v1*3]=saved[3]; cf[v1*3+1]=saved[4]; cf[v1*3+2]=saved[5]
  cf[v2*3]=saved[6]; cf[v2*3+1]=saved[7]; cf[v2*3+2]=saved[8]
}
function setFaceColor(cf, fi, geo, r, g, b) {
  const [v0,v1,v2] = faceVertices(geo, fi)
  cf[v0*3]=r; cf[v0*3+1]=g; cf[v0*3+2]=b
  cf[v1*3]=r; cf[v1*3+1]=g; cf[v1*3+2]=b
  cf[v2*3]=r; cf[v2*3+1]=g; cf[v2*3+2]=b
}

// Mode-aware helpers ---------------------------------------------------------
// Color mode is per-instance (all chunks share the same render mode), but
// material is per-chunk so mode switches must propagate to every chunk.

function toggleFaceOpacity() {
  const ims = Object.values(store.instanceMeshes)
  if (ims.length === 0) return
  const isOpaque = ims[0].chunks[0]?.mesh.material.opacity === 1.0
  const newOpacity = isOpaque ? 0.15 : 1.0
  for (const im of ims) {
    for (const c of im.chunks) {
      c.mesh.material.transparent = newOpacity < 1
      c.mesh.material.opacity = newOpacity
      c.mesh.material.needsUpdate = true
    }
  }
  requestRender()
}

function setColorMode(im, mode) {
  if (im.colorMode === mode) return
  im.colorMode = mode
  for (const c of im.chunks) {
    const mat = c.mesh.material
    if (mode === 'uv') { mat.map = colormapTexture; mat.vertexColors = false }
    else               { mat.map = null;            mat.vertexColors = true  }
    mat.needsUpdate = true
  }
}

function setFaceUV(uv, fi, geo, u, v) {
  const [v0,v1,v2] = faceVertices(geo, fi)
  uv[v0*2]=u; uv[v0*2+1]=v
  uv[v1*2]=u; uv[v1*2+1]=v
  uv[v2*2]=u; uv[v2*2+1]=v
}
function saveFaceUV(uv, fi, geo) {
  const [v0,v1,v2] = faceVertices(geo, fi)
  return [uv[v0*2], uv[v0*2+1], uv[v1*2], uv[v1*2+1], uv[v2*2], uv[v2*2+1]]
}
function restoreFaceUV(uv, fi, geo, saved) {
  const [v0,v1,v2] = faceVertices(geo, fi)
  uv[v0*2]=saved[0]; uv[v0*2+1]=saved[1]
  uv[v1*2]=saved[2]; uv[v1*2+1]=saved[3]
  uv[v2*2]=saved[4]; uv[v2*2+1]=saved[5]
}

// Save/restore/highlight all operate on a single chunk + chunk-LOCAL face idx.
// Callers go: const r = resolveFace(im, globalFi) → use r.chunk, r.localFi.
function saveFaceAttr(im, chunk, localFi) {
  const geo = chunk.mesh.geometry
  if (im.colorMode === 'uv') {
    return { mode: 'uv', data: saveFaceUV(chunk.uvAttr.array, localFi, geo) }
  }
  return { mode: 'vertex', data: saveFaceColors(chunk.colorAttr.array, localFi, geo) }
}
function restoreFaceAttr(chunk, localFi, saved) {
  const geo = chunk.mesh.geometry
  if (saved.mode === 'uv') {
    restoreFaceUV(chunk.uvAttr.array, localFi, geo, saved.data)
    chunk.uvAttr.needsUpdate = true
  } else {
    restoreFaceColors(chunk.colorAttr.array, localFi, geo, saved.data)
    chunk.colorAttr.needsUpdate = true
  }
}
// Paint a face with a highlight color. In 'uv' mode all highlight types
// collapse to red (v > 0.5); in 'vertex' mode the supplied RGB is used.
function setFaceHighlight(im, chunk, localFi, r, g, b) {
  const geo = chunk.mesh.geometry
  if (im.colorMode === 'uv') {
    setFaceUV(chunk.uvAttr.array, localFi, geo, UV_DEFAULT_U, UV_HIGHLIGHT_V)
    chunk.uvAttr.needsUpdate = true
  } else {
    setFaceColor(chunk.colorAttr.array, localFi, geo, r, g, b)
    chunk.colorAttr.needsUpdate = true
  }
}

// ── Hover highlight ───────────────────────────────────────────────────────
function clearHoverHighlight() {
  const im = store.currentInstance && store.instanceMeshes[store.currentInstance]
  if (hoverFaceIdx >= 0 && im && savedHoverAttr) {
    const r = resolveFace(im, hoverFaceIdx)
    if (r) restoreFaceAttr(r.chunk, r.localFi, savedHoverAttr)
  }
  hoverFaceIdx = -1; savedHoverAttr = null
  if (hoverHighlightLine) { scene.remove(hoverHighlightLine); hoverHighlightLine.geometry.dispose(); hoverHighlightLine.material.dispose(); hoverHighlightLine = null }
  if (hoverNodeMarker) hoverNodeMarker.visible = false
  if (hoverDebounceTimer) { clearTimeout(hoverDebounceTimer); hoverDebounceTimer = null }
  requestRender()
}

function applyHoverHighlight(fi, hitPoint) {
  hoverFaceIdx = fi
  const im = store.instanceMeshes[store.currentInstance]
  if (!im) return
  const r = resolveFace(im, fi); if (!r) return
  const { chunk, localFi } = r
  if (store.pickMode === 'node') {
    const geo = chunk.mesh.geometry
    const pos = geo.attributes.position
    const [vi0, vi1, vi2] = faceVertices(geo, localFi)
    const v0 = new THREE.Vector3().fromBufferAttribute(pos, vi0)
    const v1 = new THREE.Vector3().fromBufferAttribute(pos, vi1)
    const v2 = new THREE.Vector3().fromBufferAttribute(pos, vi2)
    let nearest = v0
    if (hitPoint) {
      const d0 = hitPoint.distanceTo(v0), d1 = hitPoint.distanceTo(v1), d2 = hitPoint.distanceTo(v2)
      if (d1 < d0 && d1 < d2) nearest = v1
      else if (d2 < d0 && d2 < d1) nearest = v2
    }
    hoverNodeMarker.position.copy(nearest); hoverNodeMarker.visible = true
    requestRender()
  } else {
    savedHoverAttr = saveFaceAttr(im, chunk, localFi)
    setFaceHighlight(im, chunk, localFi, 0.2, 0.8, 1.0)
    requestRender()
    if (hoverDebounceTimer) clearTimeout(hoverDebounceTimer)
    hoverDebounceTimer = setTimeout(() => fetchHoverOutline(fi), 120)
  }
}

async function fetchHoverOutline(fi) {
  hoverDebounceTimer = null
  if (fi !== hoverFaceIdx || !store.currentInstance) return
  try {
    const url = store.getApiUrl(`query/pick?instance=${encodeURIComponent(store.currentInstance)}&render_face_idx=${fi}&pick_mode=element&node_idx=0&include_coords=false`)
    const d = (await http.get(url)).data
    if (fi !== hoverFaceIdx) return
    const faceIndices = d.render_face_indices || [fi]
    const edgePts = buildElementEdges(faceIndices, new Array(faceIndices.length).fill(0))
    if (hoverHighlightLine) { scene.remove(hoverHighlightLine); hoverHighlightLine.geometry.dispose(); hoverHighlightLine.material.dispose(); hoverHighlightLine = null }
    if (edgePts.length > 0) {
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(edgePts, 3))
      hoverHighlightLine = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
        color: 0x00ccff, depthTest: false, transparent: true, opacity: 0.85,
        clippingPlanes: clipPlane ? [clipPlane] : [],
      }))
      hoverHighlightLine.renderOrder = 997; scene.add(hoverHighlightLine)
    }
    const im = store.instanceMeshes[store.currentInstance]
    if (fi === hoverFaceIdx && im && savedHoverAttr) {
      const r = resolveFace(im, fi)
      if (r) restoreFaceAttr(r.chunk, r.localFi, savedHoverAttr)
      savedHoverAttr = null
    }
    const tip = tooltipEl.value
    if (tip.style.display !== 'none' && d.odb?.elem_label != null) tip.textContent = `Element ${d.odb.elem_label}`
    requestRender()
  } catch (_) {}
}

// ── Patch highlight ───────────────────────────────────────────────────────
function _cross3(a,b){ return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]] }
function _norm3(a){ const l=Math.sqrt(a[0]*a[0]+a[1]*a[1]+a[2]*a[2]); return l>1e-12?[a[0]/l,a[1]/l,a[2]/l]:[0,0,1] }
function _dot3(a,b){ return a[0]*b[0]+a[1]*b[1]+a[2]*b[2] }

function clearPatchHighlight() {
  const im = store.currentInstance && store.instanceMeshes[store.currentInstance]
  if (patchHighlightFaces.length > 0 && im) {
    for (const fi of patchHighlightFaces) {
      const saved = patchSavedAttrs.get(fi)
      if (!saved) continue
      const r = resolveFace(im, fi)
      if (r) restoreFaceAttr(r.chunk, r.localFi, saved)
    }
  }
  patchHighlightFaces = []; patchSavedAttrs.clear()
  if (patchRectLine) { scene.remove(patchRectLine); patchRectLine.geometry.dispose(); patchRectLine.material.dispose(); patchRectLine = null }
}

function showPatchHighlight(faceIndices, center, normal, halfW, halfH) {
  clearPatchHighlight()
  const im = store.instanceMeshes[store.currentInstance]
  if (im && faceIndices.length > 0) {
    for (const fi of faceIndices) {
      const r = resolveFace(im, fi); if (!r) continue
      patchSavedAttrs.set(fi, saveFaceAttr(im, r.chunk, r.localFi))
      setFaceHighlight(im, r.chunk, r.localFi, 1.0, 0.55, 0.0)
    }
    patchHighlightFaces = [...faceIndices]
    requestRender()
  }
  const n = _norm3(normal)
  let up = [0,1,0]; if (Math.abs(_dot3(up,n))>0.999) up=[1,0,0]
  const u = _norm3(_cross3(up,n)), v = _norm3(_cross3(n,u))
  const corners = [[halfW,halfH],[-halfW,halfH],[-halfW,-halfH],[halfW,-halfH]]
    .map(([su,sv])=>[center[0]+u[0]*su+v[0]*sv, center[1]+u[1]*su+v[1]*sv, center[2]+u[2]*su+v[2]*sv])
  const pts = []
  for (let i=0;i<4;i++) { const a=corners[i],b=corners[(i+1)%4]; pts.push(...a,...b) }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pts), 3))
  patchRectLine = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color:0xff8800, depthTest:false, transparent:true, opacity:0.9 }))
  patchRectLine.renderOrder = 998; scene.add(patchRectLine)
  requestRender()
}

// ── Chunked geometry helpers ──────────────────────────────────────────────
// Backend always returns chunked render buffers (even single-chunk) so the
// frontend code path is uniform. Each instance becomes a THREE.Group of K
// chunk meshes, each with its own BufferGeometry + BVH but ≤ 60000 vertices
// (under the WebGL1 uint16 index limit).

function _disposeInstance(im) {
  modelGroup.remove(im.group)
  for (const c of im.chunks) {
    c.mesh.geometry.disposeBoundsTree?.()
    c.mesh.geometry.dispose()
    c.mesh.material.dispose()
  }
}

// Flatten all chunk meshes across instances for raycaster.intersectObjects.
function _allChunkMeshes() {
  const out = []
  for (const im of Object.values(store.instanceMeshes)) {
    for (const c of im.chunks) out.push(c.mesh)
  }
  return out
}

// Reverse-lookup from a hit chunk mesh to its (instance, chunk) pair.
function _findChunkOf(mesh) {
  const inst = mesh.userData?.instanceName
  if (inst == null) return null
  const im = store.instanceMeshes[inst]; if (!im) return null
  const chunk = im.chunks[mesh.userData.chunkIdx]; if (!chunk) return null
  return { im, instName: inst, chunk }
}

// Find the chunk containing global render_face_idx via binary search over
// faceBase. Returns { chunk, localFi, chunkIdx } or null if out of range.
function resolveFace(im, globalFi) {
  const chunks = im.chunks
  if (!chunks || chunks.length === 0) return null
  let lo = 0, hi = chunks.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >>> 1
    if (chunks[mid].faceBase <= globalFi) lo = mid
    else hi = mid - 1
  }
  const chunk = chunks[lo]
  const localFi = globalFi - chunk.faceBase
  if (localFi < 0 || localFi >= chunk.faceCount) return null
  return { chunk, localFi, chunkIdx: lo }
}

function _buildChunkedInstance(inst, sections) {
  const K = sections.chunk_count.data[0]
  const positionsConcat  = new Float32Array(sections.positions_concat.data)
  const positionsOffsets = new Int32Array(sections.positions_offsets.data)
  const indicesConcat    = new Int32Array(sections.indices_concat.data)
  const indicesOffsets   = new Int32Array(sections.indices_offsets.data)
  const faceIdxBase      = new Int32Array(sections.face_idx_base.data)
  const vertexGlobalId   = new Int32Array(sections.vertex_global_id.data)

  // Determine global vertex count for [Nv_global, 3] addressing (deform / scalars)
  let maxV = -1
  for (let i = 0; i < vertexGlobalId.length; i++) if (vertexGlobalId[i] > maxV) maxV = vertexGlobalId[i]
  const numVertsGlobal = maxV + 1
  const globalPositions = new Float32Array(numVertsGlobal * 3)
  // [Nt_total, 3] of GLOBAL vertex ids — lets edge / bbox / normal code work
  // uniformly without knowing about chunks
  const totalFacesGlobal = indicesOffsets[K] | 0
  const globalIndices = new Int32Array(totalFacesGlobal * 3)

  const group  = new THREE.Group()
  group.userData.instanceName = inst
  const chunks = []
  let totalFaces = 0

  for (let k = 0; k < K; k++) {
    const Nv_k = positionsOffsets[k+1] - positionsOffsets[k]
    const Nt_k = indicesOffsets[k+1]   - indicesOffsets[k]

    const posSlice  = positionsConcat.subarray(positionsOffsets[k]*3, positionsOffsets[k+1]*3)
    const idxSlice  = indicesConcat.subarray(indicesOffsets[k]*3,    indicesOffsets[k+1]*3)
    const vgidSlice = vertexGlobalId.subarray(positionsOffsets[k],   positionsOffsets[k+1])

    const posCopy = new Float32Array(posSlice)
    const idxCopy = new Int32Array(idxSlice)
    const vgidCopy = new Int32Array(vgidSlice)

    // Scatter into global positions buffer (used by edges sync + deform reset)
    for (let i = 0; i < Nv_k; i++) {
      const g = vgidCopy[i]
      globalPositions[g*3]     = posCopy[i*3]
      globalPositions[g*3 + 1] = posCopy[i*3 + 1]
      globalPositions[g*3 + 2] = posCopy[i*3 + 2]
    }
    // Build chunk's slice of globalIndices: each face → 3 global vertex ids
    const fbase = faceIdxBase[k]
    for (let fi = 0; fi < Nt_k; fi++) {
      globalIndices[(fbase+fi)*3]     = vgidCopy[idxCopy[fi*3]]
      globalIndices[(fbase+fi)*3 + 1] = vgidCopy[idxCopy[fi*3 + 1]]
      globalIndices[(fbase+fi)*3 + 2] = vgidCopy[idxCopy[fi*3 + 2]]
    }

    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(posCopy, 3))
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array(idxCopy.buffer), 1))

    const uvs = new Float32Array(Nv_k * 2)
    for (let i = 0; i < Nv_k; i++) { uvs[i*2] = UV_DEFAULT_U; uvs[i*2+1] = UV_DATA_V }
    const uvAttr = new THREE.BufferAttribute(uvs, 2)
    geo.setAttribute('uv', uvAttr)

    const cols = new Float32Array(Nv_k * 3); cols.fill(1)
    const colorAttr = new THREE.BufferAttribute(cols, 3)
    geo.setAttribute('color', colorAttr)

    geo.computeVertexNormals()
    geo.computeBoundsTree({ indirect: true })

    const mat = new THREE.MeshPhongMaterial({ map: colormapTexture, vertexColors: false, side: THREE.DoubleSide, flatShading: false, shininess: 30, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })
    if (clipPlane) mat.clippingPlanes = [clipPlane]
    const mesh = new THREE.Mesh(geo, mat)
    mesh.userData.instanceName = inst
    mesh.userData.chunkIdx     = k
    group.add(mesh)

    chunks.push({
      mesh, uvAttr, colorAttr,
      faceBase: faceIdxBase[k], faceCount: Nt_k,
      vertexGlobalId: vgidCopy,
    })
    totalFaces += Nt_k
  }

  return { group, chunks, colorMode: 'uv', numFaces: totalFaces, numVertsGlobal, globalPositions, globalIndices }
}

// ── Load Geometry ─────────────────────────────────────────────────────────
async function loadGeometry(instances) {
  // Dispose old
  for (const [k, l] of Object.entries(meshEdgesLines))    { modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete meshEdgesLines[k];    delete meshEdgeVtxIdxs[k] }
  for (const [k, l] of Object.entries(featureEdgesLines)) { modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete featureEdgesLines[k]; delete featureEdgeVtxIdxs[k] }
  for (const [k, l] of Object.entries(regionMeshEdgesLines)) { modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete regionMeshEdgesLines[k] }
  for (const [k, l] of Object.entries(regionOutlineLines))   { modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete regionOutlineLines[k] }
  for (const [k, l] of Object.entries(lineMeshes))           { modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete lineMeshes[k] }
  for (const [k, p] of Object.entries(pointMeshes))          { modelGroup.remove(p); p.geometry.dispose(); p.material.dispose(); delete pointMeshes[k] }
  for (const [k, l] of Object.entries(couplingMeshes))       { modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete couplingMeshes[k] }
  if (orientationLines) { modelGroup.remove(orientationLines); orientationLines.geometry.dispose(); orientationLines.material.dispose(); orientationLines = null }
  Object.values(store.instanceMeshes).forEach(im => _disposeInstance(im))
  Object.keys(store.instanceMeshes).forEach(k => delete store.instanceMeshes[k])
  Object.keys(origPositions).forEach(k => delete origPositions[k])

  store.setStatus(`Loading ${instances.length} instance(s)…`)
  let totalTris = 0
  const combinedBox = new THREE.Box3()

  await Promise.all(instances.map(async inst => {
    try {
      const res = await http.get(store.getApiUrl(`geometry/${encodeURIComponent(inst)}/render-buffers-chunked`), { responseType: 'arraybuffer' })
      const sections = parseL3BE(res.data)

      const im = _buildChunkedInstance(inst, sections)
      modelGroup.add(im.group)
      store.instanceMeshes[inst] = im
      origPositions[inst] = im.globalPositions.slice()  // save for deform reset
      totalTris += im.numFaces

      const box = new THREE.Box3()
      for (const c of im.chunks) {
        c.mesh.geometry.computeBoundingBox()
        box.union(c.mesh.geometry.boundingBox)
      }
      combinedBox.union(box)
    } catch (e) {
      store.setStatus(`${inst} 加载失败: ${e.message}`, 'err')
    }
  }))

  if (instances.length > 0) store.currentInstance = instances[0]

  if (!combinedBox.isEmpty()) {
    fitCameraToBox(combinedBox)
    const size = new THREE.Vector3(); combinedBox.getSize(size)
    buildAxes(Math.max(size.x, size.y, size.z) * 0.18)
    emit('model-loaded', { bbox: { min: combinedBox.min.toArray(), max: combinedBox.max.toArray(), axMin: combinedBox.min, axMax: combinedBox.max } })
  }
  store.setStatus(`Loaded ${instances.length} instance(s) — ${totalTris} triangles total`, 'ok')
  await Promise.all([
    loadLineElements(instances),
    loadPointElements(instances),
    loadCouplingLines(instances),
    loadOrientations(combinedBox),
  ])
  requestRender()
}

// ── Line Elements (beam / truss) ──────────────────────────────────────────
async function loadLineElements(instances) {
  await Promise.all(instances.map(async instName => {
    try {
      const res = await http.get(
        store.getApiUrl(`geometry/${encodeURIComponent(instName)}/lines`),
        { responseType: 'arraybuffer' }
      )
      const sec = parseL3BE(res.data)
      if (!sec.line_positions || sec.line_positions.data.length === 0) return
      const positions = new Float32Array(sec.line_positions.data)
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
      const mat = new THREE.LineBasicMaterial({ color: 0x4488ff, linewidth: 1 })
      if (clipPlane) mat.clippingPlanes = [clipPlane]
      const line = new THREE.LineSegments(geo, mat)
      modelGroup.add(line)
      lineMeshes[instName] = line
    } catch { /* instance has no line element data */ }
  }))
}

// ── Point Elements (MASS / ROTARYI) ──────────────────────────────────────
async function loadPointElements(instances) {
  await Promise.all(instances.map(async instName => {
    try {
      const res = await http.get(
        store.getApiUrl(`geometry/${encodeURIComponent(instName)}/points`),
        { responseType: 'arraybuffer' }
      )
      const sec = parseL3BE(res.data)
      if (!sec.point_positions || sec.point_positions.data.length === 0) return
      const positions = new Float32Array(sec.point_positions.data)
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
      const mat = new THREE.PointsMaterial({ color: 0xff8800, size: 6, sizeAttenuation: false })
      if (clipPlane) mat.clippingPlanes = [clipPlane]
      const pts = new THREE.Points(geo, mat)
      modelGroup.add(pts)
      pointMeshes[instName] = pts
    } catch { /* instance has no point element data */ }
  }))
}

// ── Coupling Lines (RBE2 / KINEMATIC) ────────────────────────────────────
async function loadCouplingLines(instances) {
  await Promise.all(instances.map(async instName => {
    try {
      const res = await http.get(
        store.getApiUrl(`geometry/${encodeURIComponent(instName)}/couplings`),
        { responseType: 'arraybuffer' }
      )
      const sec = parseL3BE(res.data)
      if (!sec.coupling_positions || sec.coupling_positions.data.length === 0) return
      const positions = new Float32Array(sec.coupling_positions.data)
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
      const mat = new THREE.LineBasicMaterial({ color: 0xff6600, linewidth: 1 })
      if (clipPlane) mat.clippingPlanes = [clipPlane]
      const line = new THREE.LineSegments(geo, mat)
      modelGroup.add(line)
      couplingMeshes[instName] = line
    } catch { /* instance has no coupling data */ }
  }))
}

// ── Orientation Triads (model-level named CSYS) ───────────────────────────
async function loadOrientations(bbox) {
  try {
    const res = await http.get(store.getApiUrl('geometry/orientations'))
    const { orientations } = res
    if (!orientations || orientations.length === 0) return

    // Axis line length = 5% of bbox diagonal, fallback to 1.0
    const scale = (bbox && !bbox.isEmpty())
      ? bbox.min.distanceTo(bbox.max) * 0.12
      : 1.0

    // If origin is at/near (0,0,0) and outside the model bbox, relocate to bbox center.
    // INP *ORIENTATION has no explicit position — global origin is a placeholder.
    const bboxCenter = (bbox && !bbox.isEmpty()) ? bbox.getCenter(new THREE.Vector3()) : new THREE.Vector3()
    const _resolveOrigin = (ori) => {
      const [ox, oy, oz] = ori.origin
      const atGlobalOrigin = Math.abs(ox) < 1e-6 && Math.abs(oy) < 1e-6 && Math.abs(oz) < 1e-6
      if (atGlobalOrigin && bbox && !bbox.isEmpty() && !bbox.containsPoint(new THREE.Vector3(ox, oy, oz))) {
        return [bboxCenter.x, bboxCenter.y, bboxCenter.z]
      }
      return [ox, oy, oz]
    }

    // Build interleaved line segments: 3 axes × 2 endpoints per orientation
    // Colour palette: red=e1, green=e2, blue=e3
    const palette = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    const posArr = new Float32Array(orientations.length * 3 * 2 * 3)
    const colArr = new Float32Array(orientations.length * 3 * 2 * 3)
    let pi = 0, ci = 0
    for (const ori of orientations) {
      const [ox, oy, oz] = _resolveOrigin(ori)
      for (let ax = 0; ax < 3; ax++) {
        const [ex, ey, ez] = ori.axes[ax]
        const [r, g, b]    = palette[ax]
        posArr[pi++] = ox;              posArr[pi++] = oy;              posArr[pi++] = oz
        posArr[pi++] = ox + ex * scale; posArr[pi++] = oy + ey * scale; posArr[pi++] = oz + ez * scale
        colArr[ci++] = r; colArr[ci++] = g; colArr[ci++] = b
        colArr[ci++] = r; colArr[ci++] = g; colArr[ci++] = b
      }
    }

    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(posArr, 3))
    geo.setAttribute('color',    new THREE.BufferAttribute(colArr, 3))
    const mat = new THREE.LineBasicMaterial({ vertexColors: true, linewidth: 2, depthTest: false })
    if (clipPlane) mat.clippingPlanes = [clipPlane]
    orientationLines = new THREE.LineSegments(geo, mat)
    orientationLines.renderOrder = 999
    modelGroup.add(orientationLines)
  } catch { /* no orientation data */ }
}

// ── Edge vertex index helpers ─────────────────────────────────────────────
// Build a map: rounded XYZ string → vertex index in the mesh position buffer.
// Used to sync edge endpoint positions after deformation.
function _buildEdgeVtxIndex(edgePosArr, meshPosArr) {
  const lookup = new Map()
  const Nv = meshPosArr.length / 3
  for (let i = 0; i < Nv; i++) {
    const k = `${Math.round(meshPosArr[i*3]*1e4)},${Math.round(meshPosArr[i*3+1]*1e4)},${Math.round(meshPosArr[i*3+2]*1e4)}`
    if (!lookup.has(k)) lookup.set(k, i)
  }
  const Ne = edgePosArr.length / 3
  const idx = new Uint32Array(Ne)
  for (let i = 0; i < Ne; i++) {
    const k = `${Math.round(edgePosArr[i*3]*1e4)},${Math.round(edgePosArr[i*3+1]*1e4)},${Math.round(edgePosArr[i*3+2]*1e4)}`
    idx[i] = lookup.get(k) ?? 0
  }
  return idx
}

// Copy deformed vertex positions to edge endpoint buffer via stored vertex indices.
function _syncEdgesForInst(instName, edgeLines, edgeVtxIdxs) {
  const line = edgeLines[instName]; if (!line) return
  const idxs = edgeVtxIdxs[instName]; if (!idxs) return
  const im   = store.instanceMeshes[instName]; if (!im) return
  const src  = im.globalPositions
  const dst  = line.geometry.attributes.position.array
  for (let i = 0; i < idxs.length; i++) {
    const vi = idxs[i]
    dst[i*3]=src[vi*3]; dst[i*3+1]=src[vi*3+1]; dst[i*3+2]=src[vi*3+2]
  }
  line.geometry.attributes.position.needsUpdate = true
}

// ── Load Edges ────────────────────────────────────────────────────────────
async function loadEdges(type) {
  const instNames = Object.keys(store.instanceMeshes)
  if (instNames.length === 0) return
  const endpoint  = type === 'mesh' ? 'element-mesh-edges' : 'feature-edges'
  const color     = type === 'mesh' ? 0x111111 : 0xffff00
  const linesMap  = type === 'mesh' ? meshEdgesLines  : featureEdgesLines
  const idxsMap   = type === 'mesh' ? meshEdgeVtxIdxs : featureEdgeVtxIdxs

  // Dispose existing lines for this type
  for (const [k, l] of Object.entries(linesMap)) {
    modelGroup.remove(l); l.geometry.dispose(); l.material.dispose()
    delete linesMap[k]; delete idxsMap[k]
  }

  store.setStatus(`Loading ${type} edges for ${instNames.length} instance(s)…`)
  let totalEdges = 0

  await Promise.all(instNames.map(async instName => {
    const im = store.instanceMeshes[instName]; if (!im) return
    try {
      const res = await http.get(
        store.getApiUrl(`geometry/${encodeURIComponent(instName)}/${endpoint}`),
        { responseType: 'arraybuffer' }
      )
      const sec        = parseL3BE(res.data)
      const edgePosArr = new Float32Array(sec.edge_positions.data)

      // Build vertex index map so edges can follow deformation
      idxsMap[instName] = _buildEdgeVtxIndex(edgePosArr, im.globalPositions)

      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(edgePosArr, 3))
      const mat = new THREE.LineBasicMaterial({ color, linewidth: 1 })
      if (clipPlane) mat.clippingPlanes = [clipPlane]
      const line = new THREE.LineSegments(geo, mat)
      modelGroup.add(line)
      linesMap[instName] = line
      totalEdges += edgePosArr.length / 6
    } catch { /* instance has no edge data */ }
  }))

  const label = type === 'mesh' ? 'Mesh grid' : 'Feature edges'
  store.setStatus(`${label} loaded: ${totalEdges} edges`, 'ok')
  requestRender()
}

// ── Apply Frame Colors (uses frame-scalars + colormap texture sampler) ────
async function applyColors({ field, componentVal, componentIdx, renderMode, step, frameIdx, resultGroup }) {
  const instNames = Object.keys(store.instanceMeshes)
  if (instNames.length === 0) { store.setStatus('Load geometry first', 'err'); return }
  const compParam = componentIdx != null ? `&component_idx=${componentIdx}` : ''
  // resultGroup override: per-call result_group takes precedence over store.activeResultGroup
  const resolvedRg = resultGroup !== undefined ? resultGroup : store.activeResultGroup
  try {
    // Phase 1: fetch global min/max across all currently loaded instances
    store.setStatus(`Fetching global range for ${instNames.length} instance(s)…`)
    const rangeData = await api.fetchScalarRange(instNames, step, frameIdx, field, {
      componentIdx,
      renderMode,
      resultGroup: resolvedRg,
    })
    const { global_min: globalMin, global_max: globalMax } = rangeData

    // Phase 2: fetch per-instance scalars normalized against the global range
    store.setStatus(`Fetching scalars for ${instNames.length} instance(s)…`)
    await Promise.all(instNames.map(async instName => {
      const base = `${store.baseUrl}/api/odb/${store.activeOdbId}/results/frame-scalars`
      const qstr = `instance=${encodeURIComponent(instName)}&step=${encodeURIComponent(step)}&frame=${frameIdx}&field=${field}${compParam}&mode=${renderMode}`
      const rgParam = resolvedRg ? `&result_group=${encodeURIComponent(resolvedRg)}` : ''
      const rangeParam = `&global_min=${globalMin}&global_max=${globalMax}`
      const url = `${base}?${qstr}${rgParam}${rangeParam}`
      const res = await http.get(url, { responseType: 'arraybuffer' })
      const sections = parseL3BE(res.data)
      const tValues = new Float32Array(sections.u_per_vertex.data)   // [Nv_global]
      const im = store.instanceMeshes[instName]; if (!im) return
      setColorMode(im, 'uv')
      // Scatter tValues into each chunk's uv via vertexGlobalId map.
      // NaN = element type has no data for this component → grey (UV_CLEAR_V).
      for (const c of im.chunks) {
        const uv = c.uvAttr.array
        const vgid = c.vertexGlobalId
        for (let i = 0; i < vgid.length; i++) {
          const t = tValues[vgid[i]]
          if (isNaN(t)) {
            uv[i*2]     = UV_DEFAULT_U
            uv[i*2 + 1] = UV_CLEAR_V
          } else {
            uv[i*2]     = t
            uv[i*2 + 1] = UV_DATA_V
          }
        }
        c.uvAttr.needsUpdate = true
      }
      requestRender()
    }))
    currentResultCtx = { step, field, frameIdx, componentIdx }
    emit('colors-loaded', { vMin: globalMin, vMax: globalMax })
    store.setStatus(`Colors applied — [${globalMin.toExponential(3)}, ${globalMax.toExponential(3)}]`, 'ok')
  } catch (e) {
    store.setStatus('Apply colors 失败: ' + e.message, 'err')
  }
}

// ── Apply Color Code ──────────────────────────────────────────────────────
const INSTANCE_PALETTE = [
  [0.27, 0.52, 0.95], [0.95, 0.42, 0.27], [0.27, 0.80, 0.50],
  [0.90, 0.80, 0.20], [0.70, 0.27, 0.90], [0.27, 0.85, 0.90],
  [0.95, 0.55, 0.80], [0.55, 0.75, 0.27], [0.90, 0.60, 0.27],
  [0.27, 0.45, 0.70],
]

async function applyColorCode({ scheme, setNames }) {
  const instNames = Object.keys(store.instanceMeshes)
  if (instNames.length === 0) { store.setStatus('Load geometry first', 'err'); return }

  if (scheme === 'instance') {
    const legend = instNames.map((name, i) => {
      const [r, g, b] = INSTANCE_PALETTE[i % INSTANCE_PALETTE.length]
      return { name, r, g, b }
    })
    instNames.forEach((instName, i) => {
      const im = store.instanceMeshes[instName]; if (!im) return
      const [r, g, b] = INSTANCE_PALETTE[i % INSTANCE_PALETTE.length]
      setColorMode(im, 'vertex')
      for (const c of im.chunks) {
        const cf = c.colorAttr.array
        for (let j = 0; j < cf.length; j += 3) { cf[j] = r; cf[j+1] = g; cf[j+2] = b }
        c.colorAttr.needsUpdate = true
      }
    })
    requestRender()
    emit('color-code-applied', { legend })
    store.setStatus(`Color code applied — ${instNames.length} instances`, 'ok')
    return
  }

  store.setStatus(`Applying color code to ${instNames.length} instance(s)…`)
  try {
    const legendMap = new Map()   // name → {id, name, r, g, b}  — merged across all instances
    await Promise.all(instNames.map(async instName => {
      let url = store.getApiUrl(`color-code/${encodeURIComponent(instName)}?scheme=${scheme}`)
      if (scheme === 'elset' && setNames?.length) url += `&set_names=${encodeURIComponent(setNames.join(','))}`
      const res = await http.get(url, { responseType: 'arraybuffer' })
      const sections = parseL3BE(res.data)
      const legendBytes = sections.legend?.data
      const instLegend = legendBytes ? JSON.parse(new TextDecoder().decode(legendBytes)) : []
      instLegend.forEach(entry => { if (!legendMap.has(entry.name)) legendMap.set(entry.name, entry) })
      const im = store.instanceMeshes[instName]; if (!im) return
      setColorMode(im, 'vertex')
      const src = new Float32Array(sections.color_per_vertex.data)   // [Nv_global, 3]
      // Scatter global colors into each chunk's color attr via vertexGlobalId
      for (const c of im.chunks) {
        const cf = c.colorAttr.array
        const vgid = c.vertexGlobalId
        for (let i = 0; i < vgid.length; i++) {
          const g = vgid[i]
          cf[i*3]     = src[g*3]
          cf[i*3 + 1] = src[g*3 + 1]
          cf[i*3 + 2] = src[g*3 + 2]
        }
        c.colorAttr.needsUpdate = true
      }
      requestRender()
    }))
    const legend = Array.from(legendMap.values())
    emit('color-code-applied', { legend })
    store.setStatus(`Color code applied — ${legend.length} categories`, 'ok')
  } catch (e) {
    store.setStatus('Color code 失败: ' + e.message, 'err')
  }
}

function clearColorCode() {
  Object.values(store.instanceMeshes).forEach(im => {
    setColorMode(im, 'uv')
    for (const c of im.chunks) {
      const uv = c.uvAttr.array
      const n = uv.length / 2
      for (let i = 0; i < n; i++) { uv[i*2] = UV_DEFAULT_U; uv[i*2+1] = UV_CLEAR_V }
      c.uvAttr.needsUpdate = true
    }
  })
  emit('color-code-applied', { legend: [] })
  store.setStatus('Color code cleared', 'ok')
}

function resetColorCode() {
  Object.values(store.instanceMeshes).forEach(im => {
    setColorMode(im, 'uv')
    for (const c of im.chunks) {
      const uv = c.uvAttr.array
      const n = uv.length / 2
      for (let i = 0; i < n; i++) { uv[i*2] = UV_DEFAULT_U; uv[i*2+1] = UV_DATA_V }
      c.uvAttr.needsUpdate = true
    }
  })
  emit('color-code-applied', { legend: [] })
  store.setStatus('Color code reset to initial', 'ok')
  requestRender()
}

// ── Region Highlight (mesh edges / outline for one or more averaging regions) ──
// regions: [{name, r, g, b}, ...]  — full legend entries so we can use their colors
// types:   ['mesh'] | ['outline'] | ['mesh', 'outline']
async function loadRegionHighlight(scheme, regions, types) {
  const instNames = Object.keys(store.instanceMeshes)
  if (instNames.length === 0) { store.setStatus('Load geometry first', 'err'); return }

  const TYPE_CFG = {
    mesh:    { endpoint: 'region-mesh-edges', linesMap: regionMeshEdgesLines, opacity: 0.55 },
    outline: { endpoint: 'region-outline',    linesMap: regionOutlineLines,   opacity: 1.0  },
  }

  // Clear all previous region highlights
  for (const cfg of Object.values(TYPE_CFG)) {
    for (const [k, l] of Object.entries(cfg.linesMap)) {
      modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete cfg.linesMap[k]
    }
  }

  const regionList = Array.isArray(regions) ? regions : [regions]
  const typeList   = Array.isArray(types)   ? types   : [types]
  let totalEdges = 0

  store.setStatus(`Loading region highlight for ${regionList.length} region(s)…`)

  for (const regionInfo of regionList) {
    // legend_key is the raw label used for the API call; name is the display name
    const region = regionInfo.legend_key ?? regionInfo.name
    const { r, g, b } = regionInfo
    const threeColor = new THREE.Color(r, g, b)

    for (const type of typeList) {
      const { endpoint, linesMap, opacity } = TYPE_CFG[type] ?? {}
      if (!endpoint) continue

      await Promise.all(instNames.map(async instName => {
        try {
          const params = new URLSearchParams({ scheme, region })
          const res = await http.get(
            store.getApiUrl(`color-code/${encodeURIComponent(instName)}/${endpoint}?${params}`),
            { responseType: 'arraybuffer' }
          )
          const sec        = parseL3BE(res.data)
          const edgePosArr = new Float32Array(sec.edge_positions.data)
          if (edgePosArr.length === 0) return

          const geo = new THREE.BufferGeometry()
          geo.setAttribute('position', new THREE.BufferAttribute(edgePosArr, 3))
          const mat = new THREE.LineBasicMaterial({
            color: threeColor, linewidth: 2,
            depthTest: false, transparent: opacity < 1, opacity,
          })
          if (clipPlane) mat.clippingPlanes = [clipPlane]
          const line = new THREE.LineSegments(geo, mat)
          line.renderOrder = 1
          modelGroup.add(line)
          linesMap[`${instName}__${region}`] = line
          totalEdges += edgePosArr.length / 6
        } catch { /* instance has no data for this region */ }
      }))
    }
  }

  store.setStatus(`Region highlight loaded: ${totalEdges} edges`, 'ok')
  requestRender()
}

function clearRegionHighlight() {
  for (const linesMap of [regionMeshEdgesLines, regionOutlineLines]) {
    for (const [k, l] of Object.entries(linesMap)) {
      modelGroup.remove(l); l.geometry.dispose(); l.material.dispose(); delete linesMap[k]
    }
  }
  store.setStatus('Region highlight cleared', 'ok')
  requestRender()
}

// ── BBox face collection ──────────────────────────────────────────────────
function collectBboxFaces(sx1, sy1, sx2, sy2) {
  const im = store.currentInstance && store.instanceMeshes[store.currentInstance]
  if (!im) return []
  const vRect  = renderer.domElement.getBoundingClientRect()
  const posArr = im.globalPositions
  const idxArr = im.globalIndices
  const Rf     = im.numFaces
  const result = [], _v = new THREE.Vector3()
  for (let fi = 0; fi < Rf; fi++) {
    if (!_facePassesClip(fi, posArr, idxArr)) continue
    for (let i = 0; i < 3; i++) {
      const vi = idxArr[fi*3+i]
      _v.fromArray(posArr, vi*3); _v.project(camera)
      if (_v.z < -1 || _v.z > 1) continue
      const px = ((_v.x+1)/2)*vRect.width+vRect.left
      const py = ((-_v.y+1)/2)*vRect.height+vRect.top
      if (px >= sx1 && px <= sx2 && py >= sy1 && py <= sy2) { result.push(fi); break }
    }
  }
  return result
}

// ── Mouse events ──────────────────────────────────────────────────────────
function onMouseDown(e) {
  if (!e.shiftKey || Object.keys(store.instanceMeshes).length === 0) return
  isBboxDrag = true; bboxStart = { x: e.clientX, y: e.clientY }
  controls.enabled = false
  const vRect = renderer.domElement.getBoundingClientRect()
  const ov = bboxOverlayEl.value
  ov.style.left = (bboxStart.x-vRect.left)+'px'; ov.style.top = (bboxStart.y-vRect.top)+'px'
  ov.style.width = '0px'; ov.style.height = '0px'; ov.style.display = 'block'
  e.preventDefault()
}

// Store the latest mouse-move event; actual work is done in _processMouseMove()
// which is called once per animation frame from animate() to avoid redundant raycasts.
function onMouseMove(e) {
  _pendingMouseMove = e

  // BBox overlay must update immediately for responsive rubber-band feel.
  if (isBboxDrag && bboxStart) {
    const vRect = renderer.domElement.getBoundingClientRect()
    const ov = bboxOverlayEl.value
    ov.style.left   = Math.min(bboxStart.x, e.clientX)-vRect.left+'px'
    ov.style.top    = Math.min(bboxStart.y, e.clientY)-vRect.top+'px'
    ov.style.width  = Math.abs(e.clientX-bboxStart.x)+'px'
    ov.style.height = Math.abs(e.clientY-bboxStart.y)+'px'
  }
}

// Called once per animation frame.  Does a single raycast and reuses the result
// for both coordinate display and hover highlight — previously two separate raycasts.
function _processMouseMove(e) {
  const rect     = renderer.domElement.getBoundingClientRect()
  const anyMesh  = Object.keys(store.instanceMeshes).length > 0
  const tip      = tooltipEl.value
  const el       = coordEl.value

  if (!anyMesh || isBboxDrag) {
    if (el)  el.style.display  = 'none'
    if (!isBboxDrag) { clearHoverHighlight(); if (tip) tip.style.display = 'none' }
    return
  }

  // Single raycast shared by coordinate display + hover highlight.
  const ndcX = ((e.clientX-rect.left)/rect.width)*2-1
  const ndcY = -((e.clientY-rect.top)/rect.height)*2+1
  const ray  = new THREE.Raycaster()
  ray.setFromCamera(new THREE.Vector2(ndcX, ndcY), camera)
  const allHits = ray.intersectObjects(_allChunkMeshes(), false)

  // ── Coordinate display (all hits, no clip filter) ─────────────────────────
  if (allHits.length > 0) {
    const p = allHits[0].point
    el.textContent = `X ${p.x.toFixed(5)}  Y ${p.y.toFixed(5)}  Z ${p.z.toFixed(5)}`
    el.style.display = 'block'
  } else {
    el.style.display = 'none'
  }

  // ── Hover pick (clip-filtered) ────────────────────────────────────────────
  if (store.mouseMode !== 'pick') { clearHoverHighlight(); if (tip) tip.style.display = 'none'; return }

  const hits = allHits.filter(h => !clipPlane || clipPlane.distanceToPoint(h.point) >= 0)
  if (hits.length === 0) { clearHoverHighlight(); if (tip) tip.style.display = 'none'; return }

  const ctx = _findChunkOf(hits[0].object); if (!ctx) return
  if (ctx.instName !== store.currentInstance) { clearHoverHighlight(); store.currentInstance = ctx.instName }

  const localFi = hits[0].faceIndex ?? (hits[0].face ? Math.floor(hits[0].face.a/3) : null)
  if (localFi == null) return
  const fi = ctx.chunk.faceBase + localFi

  if (tip) {
    tip.textContent  = store.pickMode === 'node' ? `Node (face #${fi})` : `Element (face #${fi})`
    tip.style.left   = (e.clientX-rect.left+14)+'px'
    tip.style.top    = (e.clientY-rect.top-28)+'px'
    tip.style.display = 'block'
  }
  if (fi !== hoverFaceIdx) { clearHoverHighlight(); applyHoverHighlight(fi, hits[0].point) }
}

async function onMouseUp(e) {
  if (!isBboxDrag) return
  isBboxDrag = false; controls.enabled = true; bboxOverlayEl.value.style.display = 'none'
  const sx1=Math.min(bboxStart.x,e.clientX), sx2=Math.max(bboxStart.x,e.clientX)
  const sy1=Math.min(bboxStart.y,e.clientY), sy2=Math.max(bboxStart.y,e.clientY)
  if (sx2-sx1 < 5 || sy2-sy1 < 5) return
  suppressNextClick = true; setTimeout(() => { suppressNextClick = false }, 300)
  if (!store.currentInstance) return
  const selectedFaces = collectBboxFaces(sx1, sy1, sx2, sy2)
  if (selectedFaces.length === 0) return
  clearBboxHighlight()
  store.setStatus(`框选中 ${selectedFaces.length} 个三角面，查询中…`)
  try {
    const d = (await http.post(store.getApiUrl('query/render-faces'), {
      instance: store.currentInstance, render_face_indices: selectedFaces, mode: store.pickMode
    })).data

    // Node mode: 用屏幕坐标二次过滤，只保留真正在框内投影的节点
    if (store.pickMode === 'node' && d.node_positions && d.node_positions.length > 0) {
      const vRect2 = renderer.domElement.getBoundingClientRect()
      const tv = new THREE.Vector3()
      const filteredPos = [], filteredLabels = []
      for (let i = 0; i < d.node_positions.length; i++) {
        const [nx, ny, nz] = d.node_positions[i]
        tv.set(nx, ny, nz).project(camera)
        if (tv.z < -1 || tv.z > 1) continue
        const px = ((tv.x + 1) / 2) * vRect2.width  + vRect2.left
        const py = ((-tv.y + 1) / 2) * vRect2.height + vRect2.top
        if (px >= sx1 && px <= sx2 && py >= sy1 && py <= sy2) {
          filteredPos.push(d.node_positions[i])
          if (d.node_labels) filteredLabels.push(d.node_labels[i])
        }
      }
      d.node_positions = filteredPos
      d.node_labels    = filteredLabels
      d.node_count     = filteredPos.length
    }

    // 画高亮覆盖层
    if (store.pickMode === 'element' && d.elem_face_indices && d.elem_face_indices.length > 0) {
      const FACE_LIMIT = 20000
      const facesToDraw = d.elem_face_indices.length <= FACE_LIMIT ? d.elem_face_indices : d.elem_face_indices.slice(0, FACE_LIMIT)
      const eidSlice = d.elem_ids_per_face
        ? (d.elem_face_indices.length <= FACE_LIMIT ? d.elem_ids_per_face : d.elem_ids_per_face.slice(0, FACE_LIMIT))
        : null
      const edgePts = buildElementEdges(facesToDraw, eidSlice)
      if (edgePts.length > 0) {
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.BufferAttribute(edgePts, 3))
        bboxHighlight = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
          color: 0xffaa00, depthTest: false, transparent: true, opacity: 0.75,
          clippingPlanes: clipPlane ? [clipPlane] : [],
        }))
        bboxHighlight.renderOrder = 998
        scene.add(bboxHighlight)
      }
    } else if (store.pickMode === 'node' && d.node_positions && d.node_positions.length > 0) {
      const pts = new Float32Array(d.node_positions.flat())
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(pts, 3))
      bboxHighlight = new THREE.Points(geo, new THREE.PointsMaterial({
        color: 0xff3333, size: 4, sizeAttenuation: false,
        depthTest: false, transparent: true, opacity: 0.9,
      }))
      bboxHighlight.renderOrder = 998
      scene.add(bboxHighlight)
    }
    requestRender()

    const countStr = store.pickMode === 'element' ? `${d.elem_count} 个单元` : `${d.node_count} 个节点`
    emit('bbox-result', {
      ...d,
      count:  store.pickMode === 'element' ? d.elem_count  : d.node_count,
      labels: store.pickMode === 'element' ? d.elem_labels : d.node_labels,
    })
    store.setStatus(`框选完成：${countStr}`, 'ok')
  } catch (e) { store.setStatus('BBox 查询失败: '+e.message, 'err') }
}

async function onCanvasClick(event) {
  if (suppressNextClick) { suppressNextClick = false; return }
  if (store.mouseMode !== 'pick' || Object.keys(store.instanceMeshes).length === 0) return

  const rect = renderer.domElement.getBoundingClientRect()

  // Three.js is used only to identify which instance mesh was clicked (handles
  // multi-instance occlusion). The actual face is determined server-side.
  const ray = new THREE.Raycaster()
  ray.setFromCamera(
    new THREE.Vector2(((event.clientX-rect.left)/rect.width)*2-1, -((event.clientY-rect.top)/rect.height)*2+1),
    camera
  )
  const hits = ray.intersectObjects(_allChunkMeshes(), false)
    .filter(h => !clipPlane || clipPlane.distanceToPoint(h.point) >= 0)
  if (hits.length === 0) return

  const ctx = _findChunkOf(hits[0].object); if (!ctx) return
  store.currentInstance = ctx.instName

  // Build combined view-projection matrix (Three.js column-major convention).
  // Backend uses this to reconstruct the world-space ray and find the exact face.
  const vpMat = new THREE.Matrix4().multiplyMatrices(
    camera.projectionMatrix,
    camera.matrixWorldInverse
  )

  store.setStatus('Picking…')
  try {
    const d = (await http.post(store.getApiUrl('query/ray-pick'), {
      instance: store.currentInstance,
      screen_x: event.clientX - rect.left,
      screen_y: event.clientY - rect.top,
      viewport_width:  rect.width,
      viewport_height: rect.height,
      view_projection_matrix: [...vpMat.elements],
      pick_mode:    store.pickMode,
      include_coords: true,
      deform_scale: store.deformScale,
      result_group: store.activeResultGroup ?? undefined,
      ...(currentResultCtx ? {
        step:          currentResultCtx.step,
        field:         currentResultCtx.field,
        frame_idx:     currentResultCtx.frameIdx,
        component_idx: currentResultCtx.componentIdx ?? undefined,
      } : {}),
    })).data

    const faceIdx = d.render_face_idx

    clearPickHighlight()

    if (store.pickMode === 'node') {
      // Backend returns the selected node's world coordinates — no local geometry lookup needed.
      const coords = d.odb?.def_coords || d.odb?.orig_coords
      if (coords) { pickMarker.position.set(...coords); pickMarker.visible = true }
    } else {
      const faceIndices = d.render_face_indices || [faceIdx]
      const edgePts = buildElementEdges(faceIndices, new Array(faceIndices.length).fill(0))
      if (edgePts.length > 0) {
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.BufferAttribute(edgePts, 3))
        pickHighlightLine = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
          color: 0xffdd00, depthTest: false, transparent: true, opacity: 0.9,
          clippingPlanes: clipPlane ? [clipPlane] : [],
        }))
        pickHighlightLine.renderOrder = 999; scene.add(pickHighlightLine)
      }
    }
    requestRender()

    // Normal arrow — read centroid and normal from global geometry using backend's faceIdx
    const im = ctx.im
    const posArr = im.globalPositions
    const idxN   = im.globalIndices
    const n = faceNormal(posArr, faceIdx, idxN)
    const [vn0, vn1, vn2] = [idxN[faceIdx*3], idxN[faceIdx*3+1], idxN[faceIdx*3+2]]
    showNormalArrow(
      [(posArr[vn0*3]+posArr[vn1*3]+posArr[vn2*3])/3, (posArr[vn0*3+1]+posArr[vn1*3+1]+posArr[vn2*3+1])/3, (posArr[vn0*3+2]+posArr[vn1*3+2]+posArr[vn2*3+2])/3],
      n, arrowLength()
    )

    // Append probe row
    const idLabel = store.pickMode === 'node' ? d.odb?.node_label : d.odb?.elem_label
    const fmtCoords = arr => arr ? `(${arr.map(v => v.toFixed(4)).join(', ')})` : '—'
    let valStr = '—', sourceStr = '—'
    if (d.result) {
      sourceStr = d.result.position
      valStr = store.pickMode === 'node'
        ? fmtNum(d.result.raw_value ?? d.result.display_value)
        : (d.result.display_value != null ? fmtNum(d.result.display_value)+' (avg)' : d.result.raw_values?.map(fmtNum).join(', '))
    }
    store.probeRows.push({
      mode: store.pickMode === 'node' ? 'Node' : 'Elem',
      instance: store.currentInstance,
      id: idLabel,
      type: d.odb?.elem_type || '—',
      coords: fmtCoords(d.odb?.orig_coords || d.odb?.def_coords),
      value: valStr,
    })

    emit('pick-result', { ...d, _faceIdx: faceIdx, _source: sourceStr, _value: valStr })
    store.setStatus(`Picked ${store.pickMode}: ${idLabel}`, 'ok')
  } catch (e) {
    store.setStatus('Pick failed: ' + e.message, 'err')
  }
}

function fmtNum(x) { if (x == null) return '—'; return typeof x === 'number' ? x.toExponential(4) : String(x) }
function _fmtAttached(labels) {
  if (labels == null) return '—（需重跑 L1）'
  if (labels.length === 0) return '(0)'
  if (labels.length <= 6) return labels.join(', ')
  return `${labels[0]}, ${labels[1]}, … (${labels.length} 个)`
}

// ── Set-based geometry filter ─────────────────────────────────────────────
// Subset geometry has different vertex count and chunking — we tear down
// the old chunked instance and rebuild from the chunked endpoint response.
async function filterGeometryBySet(setName) {
  const instNames = Object.keys(store.instanceMeshes)
  if (instNames.length === 0) { store.setStatus('Load geometry first', 'err'); return }
  store.setStatus(`Loading subset geometry for set "${setName}"…`)
  clearHoverHighlight()
  clearPatchHighlight()
  clearPickHighlight()
  try {
    await Promise.all(instNames.map(async inst => {
      const oldIm = store.instanceMeshes[inst]
      if (!oldIm) return
      try {
        const res = await http.get(
          store.getApiUrl(`geometry/${encodeURIComponent(inst)}/render-buffers-chunked?set=${encodeURIComponent(setName)}`),
          { responseType: 'arraybuffer' }
        )
        const nFacesHeader = parseInt(res.headers.get('X-Face-Count') || '0')
        if (nFacesHeader === 0) return   // instance has no elements in this set

        const sections = parseL3BE(res.data)
        _disposeInstance(oldIm)
        const im = _buildChunkedInstance(inst, sections)
        modelGroup.add(im.group)
        store.instanceMeshes[inst] = im
      } catch (_) {
        // Instance has no elements in this set — leave mesh unchanged
      }
    }))
    store.setStatus(`Subset geometry loaded — set "${setName}"`, 'ok')
    requestRender()
  } catch (e) {
    store.setStatus('Set geometry filter 失败: ' + e.message, 'err')
  }
}

async function clearGeometryFilter() {
  // Reload full geometry for all currently loaded instances
  const instNames = Object.keys(store.instanceMeshes)
  if (instNames.length === 0) return
  await loadGeometry(instNames)
}

// ── Deformed Shape ────────────────────────────────────────────────────────

// Core: fetch deformed positions for one frame and update all instance meshes + edges.
// During animation (_deformAnimActive=true) BVH rebuild is skipped — pick is disabled
// anyway while playing.  stopDeformAnim() / applyDeform() rebuild BVH once after.
async function _applyDeformPositions(step, frameIdx, scale) {
  const instNames  = Object.keys(store.instanceMeshes)
  const inAnim     = _deformAnimActive
  await Promise.all(instNames.map(async instName => {
    const res = await http.get(
      store.getApiUrl(`results/deformed-positions?instance=${encodeURIComponent(instName)}&step=${encodeURIComponent(step)}&frame=${frameIdx}&scale=${scale}`),
      { responseType: 'arraybuffer' }
    )
    const sections = parseL3BE(res.data)
    const im = store.instanceMeshes[instName]; if (!im) return

    // Backend returns global [Nv_global, 3] positions (and optionally normals).
    // Update im.globalPositions then scatter into each chunk via vertexGlobalId.
    const newPos = new Float32Array(sections.positions.data)
    im.globalPositions.set(newPos)
    const newNorm = sections.normals ? new Float32Array(sections.normals.data) : null

    for (const c of im.chunks) {
      const geo = c.mesh.geometry
      const posArr = geo.attributes.position.array
      const vgid = c.vertexGlobalId
      for (let i = 0; i < vgid.length; i++) {
        const g = vgid[i]
        posArr[i*3]     = newPos[g*3]
        posArr[i*3 + 1] = newPos[g*3 + 1]
        posArr[i*3 + 2] = newPos[g*3 + 2]
      }
      geo.attributes.position.needsUpdate = true

      if (newNorm) {
        let normalAttr = geo.attributes.normal
        if (!normalAttr) {
          normalAttr = new THREE.BufferAttribute(new Float32Array(vgid.length * 3), 3)
          geo.setAttribute('normal', normalAttr)
        }
        const nArr = normalAttr.array
        for (let i = 0; i < vgid.length; i++) {
          const g = vgid[i]
          nArr[i*3]     = newNorm[g*3]
          nArr[i*3 + 1] = newNorm[g*3 + 1]
          nArr[i*3 + 2] = newNorm[g*3 + 2]
        }
        normalAttr.needsUpdate = true
      } else {
        geo.computeVertexNormals()
      }

      if (!inAnim) {
        geo.disposeBoundsTree?.()
        geo.computeBoundsTree({ indirect: true })
        geo.computeBoundingBox()
      }
    }

    _syncEdgesForInst(instName, meshEdgesLines, meshEdgeVtxIdxs)
    _syncEdgesForInst(instName, featureEdgesLines, featureEdgeVtxIdxs)
  }))
  requestRender()
}

function _rebuildBvhAll() {
  for (const im of Object.values(store.instanceMeshes)) {
    for (const c of im.chunks) {
      c.mesh.geometry.disposeBoundsTree?.()
      c.mesh.geometry.computeBoundsTree({ indirect: true })
      c.mesh.geometry.computeBoundingBox()
    }
  }
}

async function applyDeform({ step, frameIdx, scale }) {
  if (Object.keys(store.instanceMeshes).length === 0) { store.setStatus('Load geometry first', 'err'); return }
  stopDeformAnim()
  store.setStatus(`Fetching deformed positions (scale=${scale})…`)
  try {
    await _applyDeformPositions(step, frameIdx, scale)
    store.deformScale = scale
    store.setStatus(`Deformed shape — frame ${frameIdx}, scale ${scale}`, 'ok')
  } catch (e) {
    store.setStatus('Deformed shape 失败: ' + e.message, 'err')
  }
}

function resetDeform() {
  stopDeformAnim()
  const instNames = Object.keys(store.instanceMeshes)
  for (const instName of instNames) {
    const orig = origPositions[instName]; if (!orig) continue
    const im   = store.instanceMeshes[instName]; if (!im) continue
    // Restore global positions, then scatter into each chunk
    im.globalPositions.set(orig)
    for (const c of im.chunks) {
      const geo = c.mesh.geometry
      const posArr = geo.attributes.position.array
      const vgid = c.vertexGlobalId
      for (let i = 0; i < vgid.length; i++) {
        const g = vgid[i]
        posArr[i*3]     = orig[g*3]
        posArr[i*3 + 1] = orig[g*3 + 1]
        posArr[i*3 + 2] = orig[g*3 + 2]
      }
      geo.attributes.position.needsUpdate = true
      geo.computeVertexNormals()
    }
    _syncEdgesForInst(instName, meshEdgesLines, meshEdgeVtxIdxs)
    _syncEdgesForInst(instName, featureEdgesLines, featureEdgeVtxIdxs)
  }
  _rebuildBvhAll()
  store.deformScale = 1.0
  store.setStatus('Deformed shape reset', 'ok')
  requestRender()
}

// ── Deformation Animation ─────────────────────────────────────────────────

async function startDeformAnim({ step, totalFrames, scale }) {
  if (Object.keys(store.instanceMeshes).length === 0) { store.setStatus('Load geometry first', 'err'); return }
  stopDeformAnim()
  _deformAnimActive = true
  store.deformScale = scale
  let frame = 0
  store.setStatus(`Deform animation playing — scale ${scale}`)
  while (_deformAnimActive) {
    try {
      await _applyDeformPositions(step, frame, scale)
    } catch (e) {
      store.setStatus('Animation error: ' + e.message, 'err')
      break
    }
    if (!_deformAnimActive) break
    emit('deform-anim-frame', frame)
    frame = (frame + 1) % totalFrames
    await new Promise(r => setTimeout(r, 0))   // yield — network RTT is actual bottleneck
  }
  _deformAnimActive = false
}

function stopDeformAnim() {
  if (_deformAnimActive) {
    _deformAnimActive = false
    _rebuildBvhAll()
    store.setStatus('Deform animation stopped', 'ok')
  }
}

// ── Modal Harmonic Animation ──────────────────────────────────────────────

function _stopModalAnim() {
  _modalAnimGeneration++            // 令正在加载中的 startModalAnim 放弃
  if (_modalAnimRafId !== null) { cancelAnimationFrame(_modalAnimRafId); _modalAnimRafId = null }
  _modalAnimActive = false

  // Restore original materials (shader mode)
  for (const { inst, chunkIdx, mat } of _modalOrigMaterials) {
    const im = store.instanceMeshes[inst]
    if (!im) continue
    const c = im.chunks[chunkIdx]
    if (!c) continue
    c.mesh.material.dispose()
    c.mesh.material = mat
    // Remove displacement attribute
    c.mesh.geometry.deleteAttribute('a_displacement')
  }
  _modalOrigMaterials = []
  _modalShaderRefs    = []
  _modalFrameBuffers  = {}
  _modalDispMap       = {}
}

async function startModalAnim({ step, frameIdx, scale, mode, nFrames, speed }) {
  if (Object.keys(store.instanceMeshes).length === 0) {
    store.setStatus('Load geometry first', 'err'); return
  }
  stopDeformAnim()
  _stopModalAnim()

  const instNames = Object.keys(store.instanceMeshes)
  const rg = store.activeResultGroup ?? undefined

  // speed：每秒完成的完整周期数，默认 1.0
  const cyclesPerSec = speed ?? 1.0
  // 记录当前 generation，加载完成后若已变化说明期间被 stop，直接放弃
  const myGen = ++_modalAnimGeneration

  if (mode === 'shader') {
    // ── GPU Shader mode ────────────────────────────────────────────────────
    // 1. 一次性拉取原始位移向量
    store.setStatus('Modal anim (shader): loading displacement…')
    try {
      await Promise.all(instNames.map(async inst => {
        const ab = await api.fetchModalShape(inst, step, frameIdx, rg)
        const sections = parseL3BE(ab)
        _modalDispMap[inst] = new Float32Array(sections.displacement.data)  // [Nv_global × 3]
      }))
    } catch (e) {
      store.setStatus('Modal shape fetch failed: ' + e.message, 'err'); return
    }

    if (myGen !== _modalAnimGeneration) return   // 加载期间被 stop，放弃

    // 2. 先把 chunk position 属性重置为原始坐标，避免之前 applyDeform 导致的双倍偏移
    for (const inst of instNames) {
      const im   = store.instanceMeshes[inst]
      const orig = origPositions[inst]
      if (!im || !orig) continue
      for (const c of im.chunks) {
        const posArr = c.mesh.geometry.attributes.position.array
        const vgid   = c.vertexGlobalId
        for (let i = 0; i < vgid.length; i++) {
          const g = vgid[i]
          posArr[i*3]     = orig[g*3]
          posArr[i*3 + 1] = orig[g*3 + 1]
          posArr[i*3 + 2] = orig[g*3 + 2]
        }
        c.mesh.geometry.attributes.position.needsUpdate = true
      }
    }

    // 3. 为每个 chunk 散射位移，注入 shader
    const sharedUniforms = { u_modal_scale: { value: scale }, u_modal_sin: { value: 0.0 } }

    for (const inst of instNames) {
      const im   = store.instanceMeshes[inst]
      const disp = _modalDispMap[inst]
      if (!im || !disp) continue

      for (let ci = 0; ci < im.chunks.length; ci++) {
        const c    = im.chunks[ci]
        const vgid = c.vertexGlobalId
        const Nv   = vgid.length

        const chunkDisp = new Float32Array(Nv * 3)
        for (let i = 0; i < Nv; i++) {
          const g = vgid[i]
          chunkDisp[i*3]     = disp[g*3]
          chunkDisp[i*3 + 1] = disp[g*3 + 1]
          chunkDisp[i*3 + 2] = disp[g*3 + 2]
        }
        c.mesh.geometry.setAttribute('a_displacement', new THREE.BufferAttribute(chunkDisp, 3))

        _modalOrigMaterials.push({ inst, chunkIdx: ci, mat: c.mesh.material })
        const patchedMat = c.mesh.material.clone()
        patchedMat.onBeforeCompile = shader => {
          Object.assign(shader.uniforms, sharedUniforms)
          shader.vertexShader =
            'attribute vec3 a_displacement;\n' +
            'uniform float u_modal_scale;\n' +
            'uniform float u_modal_sin;\n' +
            shader.vertexShader
          shader.vertexShader = shader.vertexShader.replace(
            '#include <begin_vertex>',
            'vec3 transformed = position + a_displacement * u_modal_scale * u_modal_sin;'
          )
          _modalShaderRefs.push(shader)
        }
        c.mesh.material = patchedMat
      }
    }

    // 3. RAF 循环：每帧更新一个 uniform float + 同步边到 CPU 侧坐标
    let phase = 0
    const deltaPhase = (2 * Math.PI * cyclesPerSec) / 60
    let lastRafTime = performance.now()

    function _rafLoop(now) {
      // 用实际帧间隔驱动 phase，让速度不依赖显示帧率
      const dt = (now - lastRafTime) / 1000
      lastRafTime = now
      phase += 2 * Math.PI * cyclesPerSec * dt

      const sinVal = Math.sin(phase)
      sharedUniforms.u_modal_scale.value = scale
      sharedUniforms.u_modal_sin.value   = sinVal

      // 更新 globalPositions 让边跟着动（CPU 侧，edges 读这个数组）
      for (const inst of instNames) {
        const im   = store.instanceMeshes[inst]
        const disp = _modalDispMap[inst]
        const orig = origPositions[inst]
        if (!im || !disp || !orig) continue
        const Nv3 = orig.length
        const s   = scale * sinVal
        for (let i = 0; i < Nv3; i++) im.globalPositions[i] = orig[i] + disp[i] * s
        _syncEdgesForInst(inst, meshEdgesLines, meshEdgeVtxIdxs)
        _syncEdgesForInst(inst, featureEdgesLines, featureEdgeVtxIdxs)
      }

      requestRender()
      _modalAnimRafId = requestAnimationFrame(_rafLoop)
    }
    _modalAnimRafId = requestAnimationFrame(_rafLoop)
    store.setStatus(`Modal anim (GPU shader) — mode ${frameIdx}, scale ${scale}, speed ${cyclesPerSec}x`)

  } else {
    // ── Precompute mode ────────────────────────────────────────────────────
    // 1. 一次性拉取 N 帧坐标
    store.setStatus(`Modal anim (预计算): loading ${nFrames} frames…`)
    try {
      await Promise.all(instNames.map(async inst => {
        const ab = await api.fetchModalAnimationFrames(inst, step, frameIdx, scale, nFrames, rg)
        const dv = new DataView(ab)
        const nF  = dv.getUint32(0, true)
        const nV  = dv.getUint32(4, true)
        const raw = new Float32Array(ab, 8, nF * nV * 3)
        const frames = []
        for (let i = 0; i < nF; i++) frames.push(raw.slice(i * nV * 3, (i + 1) * nV * 3))
        _modalFrameBuffers[inst] = frames
      }))
    } catch (e) {
      store.setStatus('Modal animation fetch failed: ' + e.message, 'err'); return
    }

    if (myGen !== _modalAnimGeneration) return   // 加载期间被 stop，放弃

    const totalFrames = Object.values(_modalFrameBuffers)[0]?.length ?? nFrames
    // 每帧间隔 ms = 1000ms / (cyclesPerSec * totalFrames)
    const frameIntervalMs = Math.max(0, Math.round(1000 / (cyclesPerSec * totalFrames)))

    _modalAnimActive = true
    let framePtr = 0
    store.setStatus(`Modal anim (预计算) — mode ${frameIdx}, ${totalFrames} frames, speed ${cyclesPerSec}x`)

    while (_modalAnimActive) {
      for (const inst of instNames) {
        const im     = store.instanceMeshes[inst]
        const frames = _modalFrameBuffers[inst]
        if (!im || !frames) continue
        const newPos = frames[framePtr % frames.length]
        im.globalPositions.set(newPos)

        for (const c of im.chunks) {
          const geo    = c.mesh.geometry
          const posArr = geo.attributes.position.array
          const vgid   = c.vertexGlobalId
          for (let i = 0; i < vgid.length; i++) {
            const g = vgid[i]
            posArr[i*3]     = newPos[g*3]
            posArr[i*3 + 1] = newPos[g*3 + 1]
            posArr[i*3 + 2] = newPos[g*3 + 2]
          }
          geo.attributes.position.needsUpdate = true
        }
        _syncEdgesForInst(inst, meshEdgesLines, meshEdgeVtxIdxs)
        _syncEdgesForInst(inst, featureEdgesLines, featureEdgeVtxIdxs)
      }
      requestRender()
      framePtr++
      await new Promise(r => setTimeout(r, frameIntervalMs))
    }
    _modalAnimActive = false
    _rebuildBvhAll()
  }
}

function stopModalAnim() {
  _stopModalAnim()
  // 归位 globalPositions、chunk position 属性和边线到原始坐标
  // shader 模式：step2 已重置 chunk position，shader 只在 GPU 侧位移，归位 globalPositions 即可
  // precompute 模式：loop 直接改了 chunk position 属性，停止时必须显式散射 origPositions 回去
  for (const inst of Object.keys(store.instanceMeshes)) {
    const im   = store.instanceMeshes[inst]
    const orig = origPositions[inst]
    if (!im || !orig) continue
    im.globalPositions.set(orig)
    for (const c of im.chunks) {
      const posArr = c.mesh.geometry.attributes.position.array
      const vgid   = c.vertexGlobalId
      for (let i = 0; i < vgid.length; i++) {
        const g = vgid[i]
        posArr[i*3]     = orig[g*3]
        posArr[i*3 + 1] = orig[g*3 + 1]
        posArr[i*3 + 2] = orig[g*3 + 2]
      }
      c.mesh.geometry.attributes.position.needsUpdate = true
    }
    _syncEdgesForInst(inst, meshEdgesLines, meshEdgeVtxIdxs)
    _syncEdgesForInst(inst, featureEdgesLines, featureEdgeVtxIdxs)
  }
  _rebuildBvhAll()
  requestRender()
  store.setStatus('Modal animation stopped', 'ok')
}

// ── Filter geometry by arbitrary element labels ───────────────────────────
// elem_labels: array of ODB element labels (integers).
// Caller is responsible for computing which labels to include
// (e.g. by value threshold, type filter, spatial query, etc.).
async function filterGeometryByElemLabels(instance, elemLabels) {
  const oldIm = store.instanceMeshes[instance]
  if (!oldIm) { store.setStatus('Instance not loaded', 'err'); return }
  if (!elemLabels || elemLabels.length === 0) { store.setStatus('No element labels provided', 'err'); return }

  store.setStatus(`Loading subset geometry for ${elemLabels.length} elements…`)
  clearHoverHighlight()
  clearPatchHighlight()
  clearPickHighlight()

  try {
    const res = await http.post(
      store.getApiUrl(`geometry/${encodeURIComponent(instance)}/render-buffers-subset-chunked`),
      { elem_labels: elemLabels },
      { responseType: 'arraybuffer' }
    )
    const nFacesHeader = parseInt(res.headers.get('X-Face-Count') || '0')
    if (nFacesHeader === 0) {
      store.setStatus('No surface triangles match the given element labels', 'err')
      return
    }

    const sections = parseL3BE(res.data)
    _disposeInstance(oldIm)
    const im = _buildChunkedInstance(instance, sections)
    modelGroup.add(im.group)
    store.instanceMeshes[instance] = im

    store.setStatus(`Subset geometry loaded — ${im.numFaces} triangles, ${elemLabels.length} elements`, 'ok')
    requestRender()
  } catch (e) {
    store.setStatus('Element subset geometry 失败: ' + e.message, 'err')
  }
}

// ── Model Transform ───────────────────────────────────────────────────────
// Accept 16 column-major floats (Three.js Matrix4 element order) and apply
// them as the model group's world matrix.  All geometry meshes and edges are
// children of modelGroup, so this single matrix controls the entire model.
function applyModelTransform(elems) {
  modelGroup.matrix.fromArray(elems)
  modelGroup.updateWorldMatrix(true, true)
  requestRender()
}

function resetModelTransform() {
  modelGroup.matrix.identity()
  modelGroup.updateWorldMatrix(true, true)
  requestRender()
}

defineExpose({ loadGeometry, loadEdges, applyColors, applyColorCode, clearColorCode, resetColorCode, toggleCamera, updateClipPlane, showPatchHighlight, clearPatchHighlight, showNormalArrow, clearNormalArrow, filterGeometryBySet, clearGeometryFilter, filterGeometryByElemLabels, applyDeform, resetDeform, startDeformAnim, stopDeformAnim, startModalAnim, stopModalAnim, applyModelTransform, resetModelTransform, loadRegionHighlight, clearRegionHighlight, toggleFaceOpacity })
</script>
