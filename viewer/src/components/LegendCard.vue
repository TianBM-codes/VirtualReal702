<template>
  <div class="card" v-if="visible">
    <h2>Colormap Legend</h2>
    <canvas ref="canvasEl" width="230" height="14" style="width:100%;height:14px;border-radius:3px;border:1px solid #30363d" />
    <div style="display:flex;justify-content:space-between;font-size:10px;color:#8b949e">
      <span>{{ fmtNum(vMin) }}</span>
      <span>{{ fmtNum(midVal) }}</span>
      <span>{{ fmtNum(vMax) }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'

const props  = defineProps({ vMin: Number, vMax: Number, visible: Boolean })
const canvasEl = ref(null)

const midVal = computed(() => props.vMin != null && props.vMax != null ? (props.vMin + props.vMax) / 2 : null)

function fmtNum(x) { return x != null ? x.toExponential(3) : '—' }

// Same rainbow algorithm as viewer.html drawLegend()
function draw() {
  const canvas = canvasEl.value; if (!canvas) return
  const ctx = canvas.getContext('2d'), W = canvas.width, H = canvas.height
  for (let x = 0; x < W; x++) {
    const t = x / (W - 1)
    const r = Math.min(1, Math.max(0, 1.5 - Math.abs(4*t - 3)))
    const g = Math.min(1, Math.max(0, 1.5 - Math.abs(4*t - 2)))
    const b = Math.min(1, Math.max(0, 1.5 - Math.abs(4*t - 1)))
    ctx.fillStyle = `rgb(${(r*255)|0},${(g*255)|0},${(b*255)|0})`
    ctx.fillRect(x, 0, 1, H)
  }
}

watch([() => props.vMin, () => props.vMax, () => props.visible], () => {
  if (!props.visible) return
  nextTick(draw)
})
</script>
