<script setup>
import { computed } from 'vue'

const props = defineProps({
  points: { type: Array, default: () => [] },
  width: { type: Number, default: 280 },
  height: { type: Number, default: 72 },
  stroke: { type: String, default: '#0EA5E9' },
  fill: { type: String, default: 'rgba(14, 165, 233, 0.12)' },
  unit: { type: String, default: '' },
})

const viewBox = computed(() => `0 0 ${props.width} ${props.height}`)

const chart = computed(() => {
  const values = props.points.map((point) => Number(point.value)).filter((value) => Number.isFinite(value))
  if (values.length < 2) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 6
  const innerW = props.width - pad * 2
  const innerH = props.height - pad * 2
  const coords = values.map((value, index) => {
    const x = pad + (index / (values.length - 1)) * innerW
    const y = pad + innerH - ((value - min) / span) * innerH
    return { x, y, value }
  })
  const line = coords.map((point) => `${point.x},${point.y}`).join(' ')
  const area = `${pad},${props.height - pad} ${line} ${props.width - pad},${props.height - pad}`
  return {
    line,
    area,
    latest: coords[coords.length - 1]?.value ?? 0,
    min,
    max,
  }
})
</script>

<template>
  <div v-if="chart" class="sparkline">
    <div class="sparkline-meta">
      <span class="sparkline-latest">{{ chart.latest }}{{ unit }}</span>
      <span class="sparkline-range">{{ chart.min }}{{ unit }} – {{ chart.max }}{{ unit }}</span>
    </div>
    <svg :viewBox="viewBox" class="sparkline-svg" preserveAspectRatio="none">
      <polygon :points="chart.area" :fill="fill" />
      <polyline :points="chart.line" fill="none" :stroke="stroke" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
    </svg>
  </div>
  <div v-else class="sparkline-empty">暂无历史数据</div>
</template>

<style scoped>
.sparkline { width: 100%; }
.sparkline-meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 6px;
  font-size: 12px;
}
.sparkline-latest { font-size: 18px; font-weight: 700; color: var(--text); }
.sparkline-range { color: var(--text-subtle); }
.sparkline-svg { width: 100%; height: 72px; display: block; }
.sparkline-empty {
  color: var(--text-subtle);
  font-size: 12px;
  padding: 24px 0;
  text-align: center;
}
</style>
