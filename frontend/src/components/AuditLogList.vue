<script setup>
import { computed } from 'vue'

const props = defineProps({
  logs: { type: Array, default: () => [] },
  systems: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  emptyTitle: { type: String, default: '暂无审计记录' },
  emptySub: { type: String, default: '系统接入、消息处理和人工操作记录会出现在这里' },
})

const eventText = {
  'openapi.alert.created': '接入告警',
  'openapi.alert.duplicate': '重复告警',
  'openapi.health.pushed': '健康上报',
  'openapi.message.created': '接入消息',
  'openapi.message.duplicate': '重复消息',
  'openapi.report.created': '接入报告',
  'openapi.report.duplicate': '重复报告',
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
  return new Date(value).toLocaleString()
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
    ['请求', input.request_id || output.request_id],
    ['严重级别', input.severity || output.severity],
    ['结果', output.result || output.status],
    ['消息', output.message_id ? `#${output.message_id}` : undefined],
    ['健康', input.healthy === undefined ? undefined : (input.healthy ? '正常' : '异常')],
    ['诊断模板', input.template_description || input.template_name],
    ['耗时', output.duration_ms === undefined ? undefined : `${(output.duration_ms / 1000).toFixed(1)} 秒`],
    ['证据', Array.isArray(output.evidence_sources) ? output.evidence_sources.join('、') : undefined],
    ['工具调用', Array.isArray(output.tool_calls) ? `${output.tool_calls.length} 次` : undefined],
    ['失败渠道', Array.isArray(output.still_failed) && output.still_failed.length ? output.still_failed.join('、') : undefined],
    ['发送失败', Array.isArray(output.failed_channels) && output.failed_channels.length ? output.failed_channels.join('、') : undefined],
  ]
  return pairs.filter(([, value]) => value !== undefined && value !== null && value !== '')
}
</script>

<template>
  <a-spin v-if="loading" style="display:block;margin:44px 0;text-align:center" />

  <div v-else-if="logs.length === 0" class="empty-state">
    <div class="empty-title">{{ emptyTitle }}</div>
    <div class="empty-sub">{{ emptySub }}</div>
  </div>

  <div v-else :class="['audit-list', { 'audit-list--compact': compact }]">
    <article v-for="log in logs" :key="log.id" class="audit-row">
      <div class="audit-main">
        <div class="audit-title-row">
          <a-tag :color="statusColor[log.status] || 'default'" style="border:none;margin:0">
            {{ statusText[log.status] || log.status || '未知' }}
          </a-tag>
          <span class="audit-event">{{ labelEvent(log.event_type) }}</span>
          <span class="audit-time">{{ formatTime(log.created_at) }}</span>
        </div>

        <div class="audit-meta">
          <span>{{ labelSystem(log) }}</span>
          <span>{{ labelActor(log) }}</span>
          <span>{{ targetText(log) }}</span>
        </div>

        <p class="audit-brief">{{ brief(log) }}</p>

        <div v-if="detailItems(log).length" class="audit-details">
          <span v-for="[key, value] in detailItems(log)" :key="key" class="detail-pill">
            {{ key }}：{{ value }}
          </span>
        </div>
      </div>
    </article>
  </div>
</template>

<style scoped>
.audit-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.audit-list--compact {
  gap: 10px;
}
.audit-row {
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  border-radius: 10px;
}
.audit-list--compact .audit-row {
  padding: 10px 12px;
  background: var(--body-bg);
  border-radius: 8px;
}
.audit-main {
  min-width: 0;
}
.audit-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
  min-width: 0;
}
.audit-event {
  color: var(--text);
  font-size: 14px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.audit-time {
  margin-left: auto;
  color: var(--text-subtle);
  font-size: 12px;
  white-space: nowrap;
}
.audit-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
  color: var(--text-subtle);
  font-size: 12px;
  margin-bottom: 8px;
}
.audit-brief {
  color: var(--text);
  font-size: 13px;
  line-height: 1.55;
  margin: 0;
  overflow-wrap: anywhere;
}
.audit-details {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 9px;
}
.detail-pill {
  max-width: 100%;
  padding: 3px 8px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 6%, transparent);
  color: var(--text-subtle);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty-state {
  text-align: center;
  padding: 64px 0;
}
.empty-title {
  color: var(--text);
  font-size: 17px;
  font-weight: 700;
  margin-bottom: 6px;
}
.empty-sub {
  color: var(--text-subtle);
  font-size: 13px;
}

@media (max-width: 720px) {
  .audit-title-row {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .audit-time {
    margin-left: 0;
    width: 100%;
  }
}
</style>
