<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { CopyOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
  services: { type: Array, default: () => [] },
  systemLocal: { type: Boolean, default: false },
})
const emit = defineEmits(['open-diagnose'])

const selectedService = ref('')
const keyword = ref('')
const lines = ref(200)
const loading = ref(false)
const content = ref('')
const source = ref('')
const queriedAt = ref(null)

const lineOptions = [
  { value: 50, label: '50 行' },
  { value: 200, label: '200 行' },
  { value: 500, label: '500 行' },
]

const quickKeywords = ['ERROR', 'WARN', 'Exception', 'FATAL']

const logServices = computed(() => props.services.filter((service) => service.enabled !== false))

const selectedServiceMeta = computed(() => (
  logServices.value.find((service) => service.name === selectedService.value) || null
))

const logLines = computed(() => {
  if (!content.value) return []
  return content.value.split('\n').map((text, index) => ({
    no: index + 1,
    text,
    level: classifyLine(text),
  }))
})

const errorLineCount = computed(() => logLines.value.filter((line) => line.level === 'error').length)
const warnLineCount = computed(() => logLines.value.filter((line) => line.level === 'warn').length)
const unavailableMessage = computed(() => (isUnavailableMessage(content.value) ? content.value : ''))
const hasReadableLogs = computed(() => Boolean(content.value) && !unavailableMessage.value)
const canOpenDiagnose = computed(() => Boolean(selectedService.value))

const sourceHint = computed(() => {
  if (props.systemLocal) return '平台将直接读取本机服务日志'
  return '远程系统需采集器在线，日志由采集器回传'
})

watch(logServices, (services) => {
  if (!selectedService.value && services.length) {
    selectedService.value = services[0].name
  }
}, { immediate: true })

watch(selectedService, (name) => {
  if (name) loadLogs()
})

function isUnavailableMessage(text) {
  if (!text) return true
  const patterns = [
    '未配置日志来源',
    '日志文件不存在',
    '无日志输出',
    '未找到 docker 命令',
    '获取容器',
    '日志读取失败',
  ]
  const lines = text.split('\n').filter(Boolean)
  return lines.length <= 2 && patterns.some((item) => text.includes(item))
}

function classifyLine(line) {
  const upper = line.toUpperCase()
  if (upper.includes('ERROR') || upper.includes('FATAL') || upper.includes('EXCEPTION') || upper.includes('TRACEBACK')) {
    return 'error'
  }
  if (upper.includes('WARN')) return 'warn'
  return 'normal'
}

function connectorLabel(service) {
  const connector = service?.connector || ''
  if (connector === 'http') return 'HTTP'
  if (connector === 'prometheus') return 'Prometheus'
  if (connector === 'tcp') return 'TCP'
  return connector || '服务'
}

function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function applyQuickKeyword(value) {
  keyword.value = value
  loadLogs()
}

function sendToDiagnose() {
  if (!selectedService.value) return
  const question = hasReadableLogs.value
    ? `请结合 ${selectedService.value} 的最近日志分析问题，重点看 ${keyword.value || '错误和异常'}`
    : `请帮我分析为什么 ${selectedService.value} 当前读不到日志或日志异常`
  emit('open-diagnose', question)
}

async function loadLogs() {
  if (!selectedService.value) return message.warning('请先选择服务')
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/logs`, {
      params: {
        service: selectedService.value,
        keyword: keyword.value.trim(),
        lines: lines.value,
      },
    })
    content.value = typeof data.content === 'string' ? data.content : JSON.stringify(data.content, null, 2)
    source.value = data.source === 'collector' ? '远程采集器' : '平台本机'
    queriedAt.value = new Date().toISOString()
  } catch (error) {
    content.value = ''
    source.value = ''
    queriedAt.value = null
    message.error(error?.response?.data?.detail || '日志读取失败')
  } finally {
    loading.value = false
  }
}

async function copyLogs() {
  if (!content.value) return
  try {
    await navigator.clipboard.writeText(content.value)
    message.success('日志已复制')
  } catch {
    message.error('复制失败，请手动选择文本')
  }
}
</script>

<template>
  <section v-if="logServices.length" class="logs-panel">
    <div class="logs-header">
      <div>
        <div class="logs-title">服务日志</div>
        <div class="logs-sub">{{ sourceHint }}</div>
      </div>
      <a-tag v-if="source" color="processing" class="source-tag">{{ source }}</a-tag>
    </div>

    <div class="service-picker">
      <span class="picker-label">目标服务</span>
      <div class="service-chips">
        <button
          v-for="service in logServices"
          :key="service.id"
          type="button"
          :class="['service-chip', { active: selectedService === service.name }]"
          @click="selectedService = service.name"
        >
          <span class="chip-name">{{ service.name }}</span>
          <span class="chip-type">{{ connectorLabel(service) }}</span>
        </button>
      </div>
    </div>

    <div class="filter-panel">
      <div class="filter-row">
        <a-input
          v-model:value="keyword"
          allow-clear
          placeholder="输入关键字过滤，如 ERROR、timeout"
          @press-enter="loadLogs"
        >
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <div class="quick-tags">
          <button
            v-for="tag in quickKeywords"
            :key="tag"
            type="button"
            class="quick-tag"
            @click="applyQuickKeyword(tag)"
          >
            {{ tag }}
          </button>
        </div>
      </div>

      <div class="filter-actions">
        <div class="line-picker">
          <span class="picker-label">读取行数</span>
          <div class="line-chips">
            <button
              v-for="option in lineOptions"
              :key="option.value"
              type="button"
              :class="['line-chip', { active: lines === option.value }]"
              @click="lines = option.value"
            >
              {{ option.label }}
            </button>
          </div>
        </div>
        <div class="action-buttons">
          <a-button :disabled="!canOpenDiagnose" @click="sendToDiagnose">带去 AI 诊断</a-button>
          <a-button type="primary" :loading="loading" @click="loadLogs">
            <template #icon><ReloadOutlined /></template>
            查看日志
          </a-button>
        </div>
      </div>
    </div>

    <div class="capability-strip">
      <span class="capability-item">{{ systemLocal ? '本机直读日志' : '采集器远程取日志' }}</span>
      <span class="capability-item">{{ keyword ? `当前过滤：${keyword}` : '支持关键字过滤' }}</span>
      <span class="capability-item">{{ selectedServiceMeta?.name ? `当前服务：${selectedServiceMeta.name}` : '先选择服务' }}</span>
    </div>

    <div class="viewer-shell">
      <div v-if="hasReadableLogs" class="viewer-meta">
        <div class="viewer-meta-left">
          <span class="meta-chip">{{ selectedServiceMeta?.name || selectedService }}</span>
          <span class="meta-text">{{ logLines.length }} 行</span>
          <span v-if="errorLineCount" class="meta-text meta-error">{{ errorLineCount }} 条错误</span>
          <span v-if="warnLineCount" class="meta-text meta-warn">{{ warnLineCount }} 条警告</span>
          <span v-if="queriedAt" class="meta-text">更新于 {{ formatTime(queriedAt) }}</span>
        </div>
        <a-button size="small" type="text" @click="copyLogs">
          <template #icon><CopyOutlined /></template>
          复制
        </a-button>
      </div>

      <a-spin v-if="loading" class="viewer-loading" />

      <div v-else-if="hasReadableLogs" class="log-viewer">
        <div
          v-for="line in logLines"
          :key="`${line.no}-${line.text.slice(0, 24)}`"
          :class="['log-line', `log-line--${line.level}`]"
        >
          <span class="line-no">{{ line.no }}</span>
          <span class="line-text">{{ line.text || ' ' }}</span>
        </div>
      </div>

      <div v-else-if="unavailableMessage" class="viewer-empty viewer-empty--warn">
        <div class="empty-icon">!</div>
        <div class="empty-title">暂时读不到日志</div>
        <div class="empty-sub">{{ unavailableMessage }}</div>
        <div class="empty-hint">
          若服务运行在 Docker 中，请确认容器名配置正确且容器正在运行。
          Platform-API 等纯 HTTP 探活服务通常没有独立日志文件。
        </div>
        <a-button size="small" :loading="loading" @click="loadLogs">重试</a-button>
      </div>

      <div v-else class="viewer-empty">
        <div class="empty-icon">志</div>
        <div class="empty-title">尚未加载日志</div>
        <div class="empty-sub">
          选择上方服务并点击「查看日志」。
          <template v-if="!systemLocal">远程系统需确保采集器在线。</template>
        </div>
        <a-button type="primary" size="small" :disabled="!selectedService" @click="loadLogs">
          立即查看
        </a-button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.logs-panel {
  margin-top: 16px;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--card-bg);
  overflow: hidden;
}

.logs-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 16px 18px 12px;
  border-bottom: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
}

.logs-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}

.logs-sub {
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.5;
}

.source-tag {
  border: none !important;
  margin: 0 !important;
  flex-shrink: 0;
}

.service-picker {
  padding: 14px 18px 0;
}

.picker-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-subtle);
  margin-bottom: 8px;
}

.service-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.service-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--body-bg) 50%, var(--card-bg));
  border-radius: 999px;
  padding: 6px 12px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}

.service-chip:hover,
.service-chip.active {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, transparent);
}

.chip-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.chip-type {
  font-size: 11px;
  color: var(--text-subtle);
  padding: 1px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--border-color) 40%, transparent);
}

.filter-panel {
  margin: 14px 18px 0;
  padding: 14px;
  border-radius: 12px;
  border: 1px solid color-mix(in srgb, var(--border-color) 75%, transparent);
  background: color-mix(in srgb, var(--body-bg) 55%, var(--card-bg));
}

.filter-row {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.quick-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.quick-tag {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-subtle);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.quick-tag:hover {
  border-color: #dc2626;
  color: #dc2626;
  background: color-mix(in srgb, #dc2626 6%, var(--card-bg));
}

.filter-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.action-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}

.line-picker {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.line-chips {
  display: flex;
  gap: 6px;
}

.line-chip {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-subtle);
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}

.line-chip.active {
  border-color: var(--primary);
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  font-weight: 600;
}

.capability-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 18px 0;
}

.capability-item {
  padding: 4px 10px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--text-subtle);
  font-size: 12px;
}

.viewer-shell {
  margin: 14px 18px 18px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
  background: #111827;
  min-height: 220px;
}

.viewer-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: #1f2937;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.viewer-meta-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.meta-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: #e5e7eb;
  font-size: 11px;
  font-weight: 600;
}

.meta-text {
  font-size: 11px;
  color: #9ca3af;
}

.meta-error { color: #fca5a5; }
.meta-warn { color: #fcd34d; }

.viewer-loading {
  display: block;
  margin: 80px auto;
}

.log-viewer {
  max-height: 360px;
  overflow: auto;
  padding: 10px 0;
  font: 12px/1.65 'SF Mono', Menlo, Consolas, monospace;
}

.log-line {
  display: flex;
  gap: 12px;
  padding: 1px 12px;
}

.log-line:hover {
  background: rgba(255, 255, 255, 0.04);
}

.line-no {
  width: 36px;
  flex-shrink: 0;
  text-align: right;
  color: #6b7280;
  user-select: none;
}

.line-text {
  flex: 1;
  color: #d1d5db;
  white-space: pre-wrap;
  word-break: break-word;
}

.log-line--error .line-text { color: #fca5a5; }
.log-line--warn .line-text { color: #fcd34d; }

.viewer-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  text-align: center;
  background: color-mix(in srgb, var(--body-bg) 40%, #111827);
}

.empty-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.04);
  color: #9ca3af;
  font-size: 20px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}

.empty-title {
  font-size: 15px;
  font-weight: 700;
  color: #e5e7eb;
  margin-bottom: 6px;
}

.empty-sub {
  max-width: 360px;
  font-size: 12px;
  line-height: 1.6;
  color: #9ca3af;
  margin-bottom: 10px;
}

.empty-hint {
  max-width: 420px;
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
  margin-bottom: 14px;
}

.viewer-empty--warn .empty-icon {
  color: #fcd34d;
  border-color: rgba(252, 211, 77, 0.25);
  background: rgba(252, 211, 77, 0.08);
}

@media (max-width: 720px) {
  .filter-actions {
    flex-direction: column;
    align-items: stretch;
  }

  .action-buttons {
    width: 100%;
    justify-content: stretch;
    flex-direction: column;
  }

  .line-picker {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
