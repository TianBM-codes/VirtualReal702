/**
 * modal-sync demo 页入口(viewer/modal-sync-demo.html)。
 *
 * 仅供本地联调:npm run dev 后浏览 http://localhost:5173/modal-sync-demo.html。
 * 与主 viewer(index.html / App.vue)完全独立,互不影响。
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { ModalSyncPlayer } from '../ModalSyncPlayer.js'

// ── 场景 ────────────────────────────────────────────────────────────────────
const viewport = document.getElementById('viewport')
const scene = new THREE.Scene()
scene.background = new THREE.Color(0x0d1117)
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1e7)
camera.position.set(500, 400, 800)
const renderer = new THREE.WebGLRenderer({ antialias: true })
viewport.appendChild(renderer.domElement)
const controls = new OrbitControls(camera, renderer.domElement)
scene.add(new THREE.AmbientLight(0xffffff, 0.55))
const dirLight = new THREE.DirectionalLight(0xffffff, 1.2)
dirLight.position.set(1, 2, 3)
scene.add(dirLight)

function resize() {
  const w = viewport.clientWidth, h = viewport.clientHeight
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
}
window.addEventListener('resize', resize)
resize()
;(function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera) })()

// ── 播放器 ──────────────────────────────────────────────────────────────────
let player = null
const $ = id => document.getElementById(id)
const statusEl = $('status')

function setStatus(html, cls = '') { statusEl.innerHTML = cls ? `<span class="${cls}">${html}</span>` : html }
function setButtons(loaded) {
  for (const id of ['btnPlay', 'btnPause', 'btnStop']) $(id).disabled = !loaded
}

function fitCamera(group) {
  const box = new THREE.Box3().setFromObject(group)
  if (box.isEmpty()) return
  const center = box.getCenter(new THREE.Vector3())
  const size = box.getSize(new THREE.Vector3()).length()
  camera.position.copy(center).add(new THREE.Vector3(size * 0.6, size * 0.5, size * 0.8))
  camera.near = size / 1000
  camera.far = size * 100
  camera.updateProjectionMatrix()
  controls.target.copy(center)
}

$('btnLoad').onclick = async () => {
  const baseUrl = $('baseUrl').value.trim().replace(/\/+$/, '')
  const projectId = $('projectId').value.trim()
  if (!projectId) { setStatus('请填 Project ID', 'err'); return }

  player?.dispose()
  player = new ModalSyncPlayer({
    baseUrl, projectId,
    resultGroup: $('resultGroup').value.trim() || null,
  })
  scene.add(player.group)

  setStatus('加载中…')
  try {
    const femFrameRaw = $('femFrame').value.trim()
    const data = await player.load({
      order: parseInt($('order').value, 10) || 0,
      femFrame: femFrameRaw === '' ? null : parseInt(femFrameRaw, 10),
      nFrames: parseInt($('nFrames').value, 10) || 20,
      coefficient: parseFloat($('coefficient').value) || 1.0,
      flip: $('flip').checked,
    })
    fitCamera(player.group)
    setButtons(true)

    const lines = [
      `试验:${data.test.ids.length} 测点,` +
        (data.test.frequency ? `${data.test.frequency} ${data.test.unit}` : '未变形') +
        `,scaleFactor=${data.test.scaleFactor.toPrecision(4)}`,
      data.fem.available
        ? `FEM:step=${data.fem.step} frame=${data.fem.frame},` +
          `${data.fem.frequency?.toFixed?.(2) ?? '?'} Hz,scale=${data.fem.scale.toPrecision(4)}`
        : `<span class="warn">FEM 不可用:${data.fem.reason}</span>`,
      `同步:${data.sync.n_frames} 帧,目标最大变形=${data.sync.targetDeform.toPrecision(4)}`,
    ]
    for (const w of player.warnings) lines.push(`<span class="warn">⚠ ${w}</span>`)
    setStatus(lines.join('<br/>'))
  } catch (e) {
    setStatus('加载失败:' + e.message, 'err')
    setButtons(false)
  }
}

$('btnPlay').onclick = () => player?.play(parseFloat($('speed').value) || 1.0)
$('btnPause').onclick = () => player?.pause()
$('btnStop').onclick = () => player?.stop()
