import { onMounted, onUnmounted, ref } from 'vue'

import api from '../../api'

export function useMetricsPolling(systemId, intervalMs = 30000) {
  const metrics = ref(null)
  const metricsLoading = ref(false)
  let metricsTimer = null

  async function loadMetrics() {
    metricsLoading.value = true
    try {
      const { data } = await api.get(`/systems/${systemId}/metrics`)
      metrics.value = data
    } catch {
      // Keep the last successful snapshot instead of interrupting the page.
    } finally {
      metricsLoading.value = false
    }
  }

  onMounted(() => {
    loadMetrics()
    metricsTimer = setInterval(loadMetrics, intervalMs)
  })

  onUnmounted(() => {
    clearInterval(metricsTimer)
  })

  return { loadMetrics, metrics, metricsLoading }
}
