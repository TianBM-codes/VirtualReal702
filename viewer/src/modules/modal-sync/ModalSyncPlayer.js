/**
 * ModalSyncPlayer — 试验网格 + FEM 模型同屏同步模态动画播放器。
 *
 * 独立模块:只依赖 three.js,不依赖 Vue / pinia / 本 viewer 的其他代码,
 * 可整目录拷贝到其他前端项目使用。
 *
 * 同步原理(与后端 syncAnimation 接口配套):
 *   - 幅度:后端把两侧放大倍数统一到同一个目标最大变形量,试验帧已乘好,
 *     FEM 帧用返回的 fem.scale 请求。
 *   - 相位:两侧都是 n_frames 帧、第 i 帧相位 sin(2πi/n) 的循环序列,
 *     本播放器用同一个帧计数器同时翻两边的 buffer,严格逐帧同步。
 *
 * 用法(最简,几何全部由播放器自建):
 *   const player = new ModalSyncPlayer({ baseUrl, projectId })
 *   scene.add(player.group)
 *   await player.load({ order: 1 })       // femInstances 缺省 'auto'
 *   player.play(1.0)                      // 每秒 1 个周期
 *
 * 宿主已有 FEM 网格时(不重复建模,直接驱动宿主的 BufferGeometry):
 *   await player.load({ order: 1, femInstances: [
 *     { instance: 'PART-1-1', geometry: hostMesh.geometry },
 *   ]})
 * 要求宿主 geometry 与 L3 render-buffers 是同一份 indexed 顶点 buffer
 * (顶点数一致);不一致时该实例跳过并在 warnings 里说明。
 *
 * 宿主想自己驱动时钟(比如已有全局动画循环):
 *   不调 play(),改为每帧调 player.setFrame(i);或 player.onFrame(cb)
 *   在本播放器的时钟里挂回调。
 */
import * as THREE from 'three'
import { postSyncAnimation, fetchFemModalFrames, fetchMetaOverview, fetchRenderBuffers } from './api.js'

export class ModalSyncPlayer {
  /**
   * @param {object} opts
   * @param {string} opts.baseUrl    后端地址,如 http://host:5000
   * @param {string|number} opts.projectId
   * @param {string} [opts.resultGroup]  FEM 结果组(project 分支 OP2 结果组)
   */
  constructor({ baseUrl, projectId, resultGroup = null }) {
    this.baseUrl = baseUrl
    this.projectId = projectId
    this.resultGroup = resultGroup

    this.group = new THREE.Group()          // 挂到宿主 scene 即可
    this.group.name = 'modal-sync'
    this.testGroup = new THREE.Group()
    this.testGroup.name = 'modal-sync-test'
    this.femGroup = new THREE.Group()
    this.femGroup.name = 'modal-sync-fem'
    this.group.add(this.testGroup, this.femGroup)

    this.info = null          // load() 后 = 接口返回的 { test, fem, sync }
    this.nFrames = 0
    this.warnings = []

    this._testAttr = null     // 试验网格共享的 position BufferAttribute
    this._testFrames = []     // Float32Array[n_frames],试验侧预计算帧
    this._testOrigin = null   // Float32Array,未变形坐标
    this._femTargets = []     // { geometry, frames, origin, owned }
    this._frameCbs = []
    this._playing = false
    this._raf = null
    this._frameIdx = 0
  }

  /**
   * 拉取同步数据并构建/绑定几何。可重复调用(切换阶次时先自动清理旧帧)。
   *
   * @param {object} opts
   * @param {number} opts.order            试验模态阶次(1-based;0=未变形)
   * @param {string} [opts.step]           FEM 步名,缺省后端自动取第一个 FREQUENCY 步
   * @param {number} [opts.femFrame]       FEM 模态帧号,缺省 order-1
   * @param {number} [opts.nFrames=20]     动画帧数(两侧一致)
   * @param {number} [opts.coefficient=1]  用户缩放系数(越大变形越小)
   * @param {string} [opts.component='usum'] 试验云图分量
   * @param {boolean} [opts.flip=false]    试验振型符号翻转
   * @param {'auto'|Array} [opts.femInstances='auto']
   *        'auto' = 从 meta/overview 取全部实例并用 render-buffers 自建网格;
   *        数组元素为 'INST' 或 { instance, geometry }(带 geometry 时驱动宿主几何)
   * @returns {Promise<object>} 接口返回的 { test, fem, sync }
   */
  async load({ order, step = null, femFrame = null, nFrames = 20, coefficient = 1.0,
               component = 'usum', flip = false, femInstances = 'auto' } = {}) {
    this.stop()
    this._disposeBuilt()
    this.warnings = []

    const data = await postSyncAnimation(this.baseUrl, {
      project_id: this.projectId,
      order,
      step,
      fem_frame: femFrame,
      result_group: this.resultGroup,
      n_frames: nFrames,
      coefficient,
      component,
      flip,
      include_frames: true,
    })
    this.info = data
    this.nFrames = data.sync.n_frames

    this._buildTestMesh(data.test)

    if (data.fem?.available) {
      await this._bindFemTargets(data.fem, femInstances)
    } else if (data.fem?.reason) {
      this.warnings.push(`FEM 侧不可用:${data.fem.reason}`)
    }

    this.setFrame(0)
    return data
  }

  // ── 播放控制 ──────────────────────────────────────────────────────────────

  /** 用内置时钟播放;cyclesPerSec = 每秒完整周期数。 */
  play(cyclesPerSec = 1.0) {
    if (this._playing || this.nFrames === 0) return
    this._playing = true
    let last = performance.now()
    const msPerFrame = 1000 / (cyclesPerSec * this.nFrames)
    const tick = (now) => {
      if (!this._playing) return
      if (now - last >= msPerFrame) {
        last = now
        this.setFrame((this._frameIdx + 1) % this.nFrames)
      }
      this._raf = requestAnimationFrame(tick)
    }
    this._raf = requestAnimationFrame(tick)
  }

  /** 暂停(停在当前帧)。 */
  pause() {
    this._playing = false
    if (this._raf !== null) { cancelAnimationFrame(this._raf); this._raf = null }
  }

  /** 停止并复位到未变形状态。 */
  stop() {
    this.pause()
    this._frameIdx = 0
    if (this._testAttr && this._testOrigin) {
      this._testAttr.array.set(this._testOrigin)
      this._testAttr.needsUpdate = true
    }
    for (const t of this._femTargets) {
      if (t.origin) {
        t.geometry.attributes.position.array.set(t.origin)
        t.geometry.attributes.position.needsUpdate = true
      }
    }
  }

  /** 手动切到第 i 帧(宿主自己驱动时钟时用)。两侧同一个 i,天然同步。 */
  setFrame(i) {
    this._frameIdx = i
    if (this._testFrames.length) {
      this._testAttr.array.set(this._testFrames[i % this._testFrames.length])
      this._testAttr.needsUpdate = true
    }
    for (const t of this._femTargets) {
      if (!t.frames.length) continue
      t.geometry.attributes.position.array.set(t.frames[i % t.frames.length])
      t.geometry.attributes.position.needsUpdate = true
    }
    for (const cb of this._frameCbs) cb(i, this.nFrames)
  }

  /** 注册每帧回调 (frameIdx, nFrames) => void,宿主可在这里同步其他对象。 */
  onFrame(cb) { this._frameCbs.push(cb) }

  get playing() { return this._playing }
  get frameIdx() { return this._frameIdx }

  /** 释放全部 GPU 资源并从父节点摘除。 */
  dispose() {
    this.stop()
    this._disposeBuilt()
    this.group.removeFromParent()
  }

  // ── 内部:试验网格 ────────────────────────────────────────────────────────

  _buildTestMesh(test) {
    const pos = new Float32Array(test.originPos)
    this._testOrigin = pos.slice()
    this._testFrames = (test.frames ?? []).map(f => new Float32Array(f))
    this._testAttr = new THREE.BufferAttribute(pos, 3)
    this._testAttr.setUsage(THREE.DynamicDrawUsage)

    // 测点(Points)与线框(indexed LineSegments)共享同一个 position attribute,
    // 每帧只需写一次
    const ptsGeo = new THREE.BufferGeometry()
    ptsGeo.setAttribute('position', this._testAttr)
    const points = new THREE.Points(ptsGeo, new THREE.PointsMaterial({
      color: 0xffcc00, size: 6, sizeAttenuation: false,
      depthTest: false,
    }))
    points.renderOrder = 2
    points.name = 'test-points'

    const lineGeo = new THREE.BufferGeometry()
    lineGeo.setAttribute('position', this._testAttr)
    lineGeo.setIndex(test.elementsIndex ?? [])
    const lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({
      color: 0xff8800, depthTest: false,
    }))
    lines.renderOrder = 1
    lines.name = 'test-wireframe'

    this.testGroup.add(points, lines)
  }

  // ── 内部:FEM 侧 ─────────────────────────────────────────────────────────

  async _bindFemTargets(fem, femInstances) {
    let specs = femInstances
    if (specs === 'auto') {
      const meta = await fetchMetaOverview(this.baseUrl, this.projectId, this.resultGroup)
      specs = (meta.instances ?? []).map(it => (typeof it === 'string' ? it : it.instance_name))
    }

    await Promise.all(specs.map(async (spec) => {
      const instance = typeof spec === 'string' ? spec : spec.instance
      const hostGeometry = typeof spec === 'string' ? null : (spec.geometry ?? null)
      try {
        const { nVerts, frames } = await fetchFemModalFrames(this.baseUrl, this.projectId, {
          instance,
          step: fem.step,
          frame: fem.frame,
          scale: fem.scale,
          nFrames: this.nFrames,
          resultGroup: this.resultGroup,
        })

        if (hostGeometry) {
          const hostN = hostGeometry.attributes.position.count
          if (hostN !== nVerts) {
            this.warnings.push(
              `实例 ${instance}:宿主几何顶点数 ${hostN} ≠ 动画帧顶点数 ${nVerts},已跳过`)
            return
          }
          this._femTargets.push({
            geometry: hostGeometry, frames,
            origin: hostGeometry.attributes.position.array.slice(),
            owned: false,
          })
          return
        }

        // 自建网格(demo / 宿主没有现成 FEM 网格时)
        const { positions, indices } = await fetchRenderBuffers(this.baseUrl, this.projectId, instance)
        if (!positions) { this.warnings.push(`实例 ${instance}:render-buffers 无 positions`); return }
        const geo = new THREE.BufferGeometry()
        const attr = new THREE.BufferAttribute(positions.slice(), 3)
        attr.setUsage(THREE.DynamicDrawUsage)
        geo.setAttribute('position', attr)
        if (indices) geo.setIndex(new THREE.BufferAttribute(new Uint32Array(indices), 1))
        geo.computeVertexNormals()
        const mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({
          color: 0x7aa2f7, side: THREE.DoubleSide,
        }))
        mesh.name = `fem-${instance}`
        this.femGroup.add(mesh)
        this._femTargets.push({ geometry: geo, frames, origin: positions.slice(), owned: true })
      } catch (e) {
        this.warnings.push(`实例 ${instance}:FEM 动画加载失败 ${e.message}`)
      }
    }))
  }

  _disposeBuilt() {
    for (const g of [this.testGroup, this.femGroup]) {
      for (const obj of [...g.children]) {
        obj.geometry?.dispose()
        obj.material?.dispose()
        g.remove(obj)
      }
    }
    this._testAttr = null
    this._testFrames = []
    this._testOrigin = null
    this._femTargets = []
  }
}
