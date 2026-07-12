<script setup>
import { computed, ref } from 'vue'
import { DownOutlined } from '@ant-design/icons-vue'

const props = defineProps({
  logs: { type: Array, default: () => [] },
  systems: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  emptyTitle: { type: String, default: '暂无审计记录' },
  emptySub: { type: String, default: '系统接入、消息处理和人工操作记录会出现在这里' },
})

const expandedIds = ref(new Set())

const eventText = {
  'openapi.alert.created': '接入告警',
  'openapi.alert.duplicate': '重复告警',
  'openapi.health.pushed': '健康上报',
  'openapi.message.created': '接入消息',
  'openapi.message.duplicate': '重复消息',
  'openapi.report.created': '接入报告',
  'openapi.report.duplicate': '重复报告',
  'openapi.log_analysis.completed': '日志分析完成',
  'openapi.log_analysis.failed': '日志分析失败',
  'openapi.daily_report.created': '日报接入',
  'openapi.monthly_report.created': '月报接入',
  'message.processed': '消息分析',
  'message.read': '消息已读',
  'message.acknowledged': '消息确认',
  'message.resolved': '消息解决',
  'diagnosis.completed': '诊断完成',
  'diagnosis.failed': '诊断失败',
  'diagnostic_templates.updated': '诊断模板设置',
  'data_analysis.queried': '只读数据分析',
  'workflow.created': '修复方案已创建',
  'workflow.rejected': '修复方案已拒绝',
  'workflow.approved': '修复方案已批准',
  'workflow.executed': '修复操作已执行',
  'workflow.execution_failed': '修复操作失败',
  'notification.retried': '通知重试',
  'notification.delivered': '告警通知发送',
  'service.draft_created': '服务草稿创建',
  'service.draft_updated': '服务草稿更新',
  'service.probe_tested': '服务配置测试',
  'service.enabled': '服务监控启用',
  'service.deleted': '服务删除',
  'task_stuck.analyzed': '任务卡住分析',
}

const statusText = {
  success: '成功',
  ok: '成功',
  failed: '失败',
  error: '失败',
  skipped: '已跳过',
  done: '已完成',
}

const statusColor = {
  success: 'success',
  ok: 'success',
  failed: 'error',
  error: 'error',
  skipped: 'warning',
  done: 'processing',
}

const statusAccent = {
  success: 'accent-ok',
  ok: 'accent-ok',
  failed: 'accent-fail',
  error: 'accent-fail',
  skipped: 'accent-skip',
  done: 'accent-done',
}

const actorText = {
  user: '管理员',
  system_token: '系统令牌',
  system: '系统',
  rules: '规则引擎',
}

const systemNameById = computed(() => {
  const map = new Map()
  for (const system of props.systems) map.set(system.id, system.name)
  return map
})

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function labelEvent(eventType) {
  return eventText[eventType] || eventType
}

function labelActor(log) {
  const actor = actorText[log.actor_type] || log.actor_type || '未知来源'
  if (!log.actor_id) return actor
  return `${actor} ${log.actor_id}`
}

function labelSystem(log) {
  if (!log.system_id) return '全局'
  return systemNameById.value.get(log.system_id) || `系统 #${log.system_id}`
}

function targetText(log) {
  if (!log.target_type && !log.target_id) return '无特定对象'
  if (!log.target_id) return log.target_type
  return `${log.target_type || '对象'} #${log.target_id}`
}

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== '')
}

function brief(log) {
  const input = log.input || {}
  const output = log.output || {}
  return firstValue(
    output.message_title,
    output.title,
    output.detail,
    output.answer,
    output.execution_result,
    output.error,
    output.status,
    input.title,
    input.summary,
    input.status,
    input.message_type,
  ) || '已记录'
}

function detailItems(log) {
  const input = log.input || {}
  const output = log.output || {}
  const pairs = [
    ['请求 ID', input.request_id || output.request_id],
    ['严重级别', input.severity || output.severity],
    ['结果', output.result || output.status],
    ['关联消息', output.message_id ? `#${output.message_id}` : undefined],
    ['健康状态', input.healthy === undefined ? undefined : (input.healthy ? '正常' : '异常')],
    ['诊断模板', input.template_description || input.template_name],
    ['耗时', output.duration_ms === undefined ? undefined : `${(output.duration_ms / 1000).toFixed(1)} 秒`],
    ['证据来源', Array.isArray(output.evidence_sources) ? output.evidence_sources.join('、') : undefined],
    ['工具调用', Array.isArray(output.tool_calls) ? `${output.tool_calls.length} 次` : undefined],
    ['仍失败渠道', Array.isArray(output.still_failed) && output.still_failed.length ? output.still_failed.join('、') : undefined],
    ['发送失败', Array.isArray(output.failed_channels) && output.failed_channels.length ? output.failed_channels.join('、') : undefined],
  ]
  return pairs.filter(([, value]) => value !== undefined && value !== null && value !== '')
}

function hasExpandableContent(log) {
  const input = log.input || {}
  const output = log.output || {}
  return detailItems(log).length > 0 || Object.keys(input).length > 0 || Object.keys(output).length > 0
}

function isExpanded(id) {
  return expandedIds.value.has(id)
}

function toggleExpand(id) {
  const next = new Set(expandedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedIds.value = next
}

function formatJson(value) {
  if (!value || (typeof value === 'object' && !Object.keys(value).length)) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
</script>

<template>
  <a-spin v-if="loading" class="list-spin" />

  <div v-else-if="logs.length === 0" class="empty-state">
    <div class="empty-icon">审</div>
    <div class="empty-title">{{ emptyTitle }}</div>
    <div class="empty-sub">{{ emptySub }}</div>
  </div>

  <div v-else :class="['audit-feed', { 'audit-feed--compact': compact }]">
    <article
      v-for="log in logs"
      :key="log.id"
      :class="[
        'audit-card',
        statusAccent[log.status] || 'accent-done',
        { 'audit-card--expanded': isExpanded(log.id) },
      ]"
    >
      <button
        type="button"
        class="audit-head"
        :disabled="!hasExpandableContent(log)"
        :aria-expanded="isExpanded(log.id)"
        @click="hasExpandableContent(log) && toggleExpand(log.id)"
      >
        <div class="audit-head-main">
          <div class="audit-title-row">
            <a-tag :color="statusColor[log.status] || 'default'" class="flat-tag">
              {{ statusText[log.status] || log.status || '未知' }}
            </a-tag>
            <span class="audit-event">{{ labelEvent(log.event_type) }}</span>
            <span class="audit-time">{{ formatTime(log.created_at) }}</span>
          </div>

          <div class="audit-meta">
            <span class="meta-chip">{{ labelSystem(log) }}</span>
            <span class="meta-chip muted">{{ labelActor(log) }}</span>
            <span class="meta-chip muted">{{ targetText(log) }}</span>
          </div>

          <p class="audit-brief">{{ brief(log) }}</p>
        </div>

        <span
          v-if="hasExpandableContent(log)"
          class="expand-btn"
          :class="{ rotated: isExpanded(log.id) }"
        >
          <DownOutlined />
        </span>
      </button>

      <div v-show="isExpanded(log.id)" class="audit-body">
        <div v-if="detailItems(log).length" class="detail-grid">
          <div v-for="[key, value] in detailItems(log)" :key="key" class="detail-cell">
            <span class="detail-key">{{ key }}</span>
            <span class="detail-value">{{ value }}</span>
          </div>
        </div>

        <div v-if="formatJson(log.input)" class="json-block">
          <div class="json-title">输入参数</div>
          <pre class="json-pre">{{ formatJson(log.input) }}</pre>
        </div>

        <div v-if="formatJson(log.output)" class="json-block">
          <div class="json-title">输出结果</div>
          <pre class="json-pre">{{ formatJson(log.output) }}</pre>
        </div>
      </div>
    </article>
  </div>
</template>

<style scoped>
.list-spin {
  display: block;
  margin: 44px auto;
  text-align: center;
}

.audit-feed {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.audit-feed--compact {
  gap: 8px;
}

.audit-card {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  border-radius: 14px;
  overflow: hidden;
  border-left: 4px solid transparent;
  transition: box-shadow 0.15s;
}

.audit-card:hover {
  box-shadow: 0 4px 16px color-mix(in srgb, var(--text) 5%, transparent);
}

.audit-card--expanded {
  box-shadow: 0 6px 20px color-mix(in srgb, var(--text) 7%, transparent);
}

.audit-card.accent-ok {
  border-left-color: var(--primary);
}

.audit-card.accent-fail {
  border-left-color: #dc2626;
}

.audit-card.accent-skip {
  border-left-color: #d97706;
}

.audit-card.accent-done {
  border-left-color: var(--primary);
}

.audit-feed--compact .audit-card {
  border-radius: 10px;
}

.audit-head {
  width: 100%;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.audit-head:disabled {
  cursor: default;
}

.audit-head-main {
  min-width: 0;
  flex: 1;
}

.audit-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.audit-event {
  color: var(--text);
  font-size: 14px;
  font-weight: 700;
}

.audit-time {
  margin-left: auto;
  color: var(--text-subtle);
  font-size: 12px;
  white-space: nowrap;
}

.flat-tag {
  border: none !important;
  margin: 0 !important;
}

.audit-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.meta-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--text-subtle);
  font-size: 12px;
}

.meta-chip.muted {
  background: color-mix(in srgb, var(--border-color) 40%, transparent);
}

.audit-brief {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.audit-card--expanded .audit-brief {
  -webkit-line-clamp: unset;
}

.expand-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  color: var(--text-subtle);
  flex-shrink: 0;
  transition: background 0.15s, transform 0.2s, color 0.15s;
}

.audit-head:hover:not(:disabled) .expand-btn {
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--primary);
}

.expand-btn.rotated {
  transform: rotate(180deg);
}

.audit-body {
  padding: 0 16px 14px;
  border-top: 1px dashed color-mix(in srgb, var(--border-color) 80%, transparent);
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px;
  margin-top: 14px;
}

.detail-cell {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
  background: color-mix(in srgb, var(--body-bg, #fafafa) 70%, var(--card-bg));
}

.detail-key {
  display: block;
  color: var(--text-subtle);
  font-size: 11px;
  margin-bottom: 4px;
}

.detail-value {
  display: block;
  color: var(--text);
  font-size: 13px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.json-block {
  margin-top: 12px;
}

.json-title {
  color: var(--text-subtle);
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 6px;
}

.json-pre {
  margin: 0;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
  background: color-mix(in srgb, var(--body-bg, #fafafa) 80%, var(--card-bg));
  color: var(--text);
  font-size: 12px;
  line-height: 1.55;
  overflow: auto;
  max-height: 280px;
  white-space: pre-wrap;
  word-break: break-word;
}

.empty-state {
  text-align: center;
  padding: 64px 0;
}

.empty-icon {
  width: 56px;
  height: 56px;
  margin: 0 auto 14px;
  border-radius: 14px;
  border: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--card-bg) 86%, var(--primary-bg));
  color: var(--text-subtle);
  font-size: 24px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 6px;
}

.empty-sub {
  color: var(--text-subtle);
  font-size: 13px;
}

@media (max-width: 720px) {
  .audit-title-row {
    align-items: flex-start;
  }

  .audit-time {
    margin-left: 0;
    width: 100%;
  }

  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
