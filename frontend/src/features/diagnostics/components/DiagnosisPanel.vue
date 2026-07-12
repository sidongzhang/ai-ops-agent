<script setup>
import { watch } from 'vue'
import { useDiagnosisChat } from '../useDiagnosisChat'
import DiagnosisEvidenceChain from './DiagnosisEvidenceChain.vue'

const props = defineProps({
  systemId: { type: String, required: true },
  restartCapability: { type: Object, default: null },
  draftQuestion: { type: String, default: '' },
})

const {
  ask,
  requestFix,
  decide,
  chatBox,
  clearMessages,
  loadHistory,
  diagnosing,
  fixing,
  historyLoading,
  messages,
  question,
  renderMd,
  lastQuestion,
} = useDiagnosisChat(props.systemId)

watch(
  () => props.draftQuestion,
  (value) => {
    if (value && value.trim()) question.value = value
  },
  { immediate: true },
)

const EVIDENCE_LABELS = {
  health_check: '健康检查',
  read_logs: '日志分析',
  search_logs: '日志检索',
  query_prometheus: 'Prometheus 指标',
  run_redis_command: 'Redis 只读命令',
  run_kafka_command: 'Kafka 只读命令',
  run_readonly_query: '只读 SQL',
  query_business_data: '只读业务数据',
}

const ACTION_LABELS = {
  fetch_logs:        { icon: '📋', label: '拉取日志' },
  health_check:      { icon: '🩺', label: '健康检查' },
  restart_container: { icon: '🔄', label: '重启容器' },
  restart_systemd:  { icon: '🔄', label: '重启 systemd 服务' },
  run_redis_command: { icon: '⚡', label: '执行 Redis 命令' },
  manual:            { icon: '📝', label: '人工操作' },
}

const STATUS_LABELS = {
  pending: '待审批',
  approved: '执行中',
  done: '已执行',
  rejected: '已拒绝',
  error: '执行失败',
}

function isRestartAction(message) {
  return ['restart_container', 'restart_systemd'].includes(message?.action?.type)
}

function canApproveAction(message) {
  if (!isRestartAction(message)) return true
  if (message?.action?.restart_ready === false) return false
  return !!props.restartCapability?.has_permission
}

function approvalHint(message) {
  if (!isRestartAction(message)) return ''
  if (message?.action?.restart_ready === false) {
    return message.action.restart_blocker || '该提案当前无法执行重启'
  }
  if (!props.restartCapability?.has_permission) {
    return '当前账号没有该系统的重启权限'
  }
  return ''
}

function restartModeLabel(message) {
  const mode = message?.action?.execution_mode
  if (mode === 'local') return '平台本机'
  if (mode === 'collector') return '远程采集器'
  return '未确定'
}
</script>

<template>
  <a-card class="panel-card diagnose-card" title="AI 智能诊断">
    <template #extra>
      <a-tooltip title="重新加载服务端诊断历史">
        <a-button
          size="small"
          :loading="historyLoading"
          @click="loadHistory"
          style="margin-right:6px"
        >历史</a-button>
      </a-tooltip>
      <a-tooltip title="AI 分析并提出修复方案（需审批后执行）">
        <a-button
          size="small"
          :loading="fixing"
          :disabled="diagnosing || fixing || messages.length === 0"
          @click="requestFix"
          style="margin-right:6px"
        >🔧 申请修复</a-button>
      </a-tooltip>
      <a-tooltip title="清空服务端诊断历史">
        <a-button type="text" size="small" :disabled="messages.length === 0" @click="clearMessages">🧹</a-button>
      </a-tooltip>
    </template>

    <div ref="chatBox" class="chat-box chat-box--tall">
      <a-spin v-if="historyLoading && messages.length === 0" class="history-spin" tip="加载诊断历史..." />

      <a-alert
        v-if="restartCapability"
        class="restart-alert"
        :type="restartCapability.enabled ? 'info' : 'warning'"
        show-icon
        :message="restartCapability.enabled
          ? `重启能力已就绪：${restartCapability.execution_mode === 'local' ? '平台本机执行' : '采集器远程执行'}`
          : (restartCapability.reason || '当前系统暂不支持自动重启')"
      />

      <a-empty
        v-if="messages.length === 0"
        description="问问 AI：这个系统现在有没有问题？"
        :image-style="{ height: '48px' }"
        style="margin-top: 60px"
      />

      <template v-for="(message, index) in messages" :key="index">

        <!-- 普通对话消息 -->
        <div v-if="message.role === 'user' || message.role === 'agent'"
          :class="['msg-row', message.role === 'user' ? 'msg-row--user' : 'msg-row--agent']">
          <span v-if="message.role === 'user'" class="bubble bubble--user">{{ message.text }}</span>
          <div v-else class="agent-response">
            <DiagnosisEvidenceChain
              v-if="message.role === 'agent' && (message.evidence?.length || message.evidenceSources?.length || message.templateDescription || message.knowledgeRefs?.length)"
              :system-id="systemId"
              :template-description="message.templateDescription"
              :model="message.model"
              :duration-ms="message.durationMs"
              :total-tokens="message.totalTokens"
              :evidence-sources="message.evidenceSources"
              :evidence-steps="message.evidenceSteps"
              :evidence="message.evidence"
              :knowledge-refs="message.knowledgeRefs"
              :report-id="message.reportId"
              :report-type="message.reportType"
            />
            <div class="bubble bubble--agent" v-html="renderMd(message.text)" />
          </div>
        </div>

        <!-- 修复方案加载中 -->
        <div v-else-if="message.role === 'fix-loading'" class="thinking-row">
          <a-spin size="small" /><span>AI 正在分析修复方案…</span>
        </div>

        <!-- 修复提案卡 -->
        <div v-else-if="message.role === 'action'" class="action-card">
          <div class="action-card__header">
            <span class="action-badge">
              {{ ACTION_LABELS[message.action?.type]?.icon }} {{ ACTION_LABELS[message.action?.type]?.label }}
            </span>
            <span :class="['status-tag', `status-tag--${message.status}`]">
              {{ STATUS_LABELS[message.status] }}
            </span>
          </div>

          <p class="action-card__diagnosis">{{ message.diagnosis }}</p>

          <div v-if="message.evidence?.length" class="evidence-block">
            <div class="evidence-block__title">诊断证据</div>
            <div class="evidence-list">
              <div v-for="(item, idx) in message.evidence" :key="idx" class="evidence-item">
                <span class="evidence-item__type">{{ EVIDENCE_LABELS[item.type] || item.type }}</span>
                <span class="evidence-item__content">{{ item.detail }}</span>
              </div>
            </div>
          </div>

          <div class="action-card__desc">
            <strong>建议操作：</strong>{{ message.action?.description }}
          </div>

          <div v-if="message.action?.service" class="action-meta">
            <span>服务：{{ message.action.service }}</span>
            <span v-if="message.action?.args?.container">容器：{{ message.action.args.container }}</span>
            <span v-if="isRestartAction(message)">执行位置：{{ restartModeLabel(message) }}</span>
          </div>

          <a-alert
            v-if="isRestartAction(message) && message.action?.restart_ready === false"
            type="warning"
            show-icon
            :message="message.action?.restart_blocker || '当前提案不满足重启条件'"
          />

          <!-- 人工步骤 -->
          <ol v-if="message.action?.type === 'manual' && message.action?.manual_steps?.length" class="manual-steps">
            <li v-for="step in message.action.manual_steps" :key="step">{{ step }}</li>
          </ol>

          <!-- 待审批：显示批准/拒绝 -->
          <div v-if="message.status === 'pending'" class="action-card__btns">
            <a-button type="primary" size="small" :disabled="!canApproveAction(message)" @click="decide(index, true)">✅ 批准执行</a-button>
            <a-button size="small" danger @click="decide(index, false)">❌ 拒绝</a-button>
          </div>

          <div v-if="message.status === 'pending' && approvalHint(message)" class="action-card__hint">
            {{ approvalHint(message) }}
          </div>

          <!-- 执行结果 -->
          <div v-if="message.result" :class="['action-card__result', message.status === 'error' ? 'action-card__result--error' : '']">
            <pre>{{ message.result }}</pre>
          </div>
        </div>

      </template>

      <div v-if="diagnosing" class="thinking-row">
        <a-spin size="small" /><span>AI 正在分析…</span>
      </div>
    </div>

    <div class="chat-input-row">
      <a-input-search
        v-model:value="question"
        placeholder="例如：Redis 内存多少？Kafka 有积压吗？今天任务数据到了没？"
        enter-button="发 送"
        :loading="diagnosing"
        :disabled="fixing"
        @search="ask"
        class="chat-input"
      />
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
}
.diagnose-card :deep(.ant-card-body) {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}
.chat-box {
  height: 360px;
  overflow-y: auto;
  background: var(--body-bg);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 12px;
  scroll-behavior: smooth;
}
.chat-box--tall {
  height: calc(100vh - 360px);
  min-height: 400px;
}
.history-spin {
  display: block;
  margin: 80px auto;
}
.restart-alert {
  margin-bottom: 12px;
}
.msg-row { margin: 8px 0; }
.msg-row--user { text-align: right; }
.msg-row--agent { text-align: left; }
.agent-response { max-width: 88%; }
.bubble { display: inline-block; max-width: 85%; font-size: 14px; line-height: 1.65; }
.agent-response .bubble { max-width: 100%; }
.bubble--user {
  padding: 9px 14px;
  border-radius: 18px 18px 4px 18px;
  background: var(--primary);
  color: #fff;
}
.bubble--agent {
  padding: 10px 14px;
  border-radius: 4px 18px 18px 18px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
  text-align: left;
}
.thinking-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-subtle);
  margin-top: 8px;
}

/* ── 修复提案卡 ── */
.action-card {
  background: var(--card-bg);
  border: 1.5px solid var(--primary);
  border-radius: 12px;
  padding: 14px 16px;
  margin: 10px 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.action-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.action-badge {
  font-size: 13px;
  font-weight: 600;
  color: var(--primary);
}
.status-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.status-tag--pending  { background: rgba(250,173,20,.15); color: #d48806; }
.status-tag--approved { background: rgba(22,119,255,.12); color: #1677ff; }
.status-tag--done     { background: rgba(82,196,26,.15);  color: #389e0d; }
.status-tag--rejected { background: rgba(0,0,0,.06);       color: #999; }
.status-tag--error    { background: rgba(255,77,79,.12);   color: #cf1322; }

.action-card__diagnosis {
  font-size: 13px;
  color: var(--text-subtle);
  margin: 0;
  line-height: 1.6;
}
.action-card__desc {
  font-size: 13.5px;
  line-height: 1.6;
}
.evidence-block {
  border: 1px solid color-mix(in srgb, var(--primary) 20%, var(--border-color));
  background: color-mix(in srgb, var(--primary) 5%, var(--card-bg));
  border-radius: 10px;
  padding: 10px 12px;
}
.evidence-block__title {
  font-size: 12px;
  font-weight: 700;
  color: var(--primary);
  margin-bottom: 8px;
}
.evidence-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.evidence-item {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  font-size: 12.5px;
  line-height: 1.55;
}
.evidence-item__type {
  font-weight: 700;
  color: var(--text);
}
.evidence-item__content {
  color: var(--text-subtle);
}
.action-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  font-size: 12px;
  color: var(--text-subtle);
}
.manual-steps {
  padding-left: 20px;
  font-size: 13px;
  color: var(--text);
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.action-card__btns {
  display: flex;
  gap: 8px;
}
.action-card__hint {
  font-size: 12px;
  color: #cf1322;
}
.action-card__result {
  background: var(--body-bg);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12.5px;
}
.action-card__result pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.action-card__result--error { border-color: rgba(255,77,79,.4); }

.chat-input-row {
  display: flex;
  gap: 8px;
}
.chat-input {
  min-width: 0;
  flex: 1;
}
.bubble--agent :deep(p) { margin: 0 0 8px; }
.bubble--agent :deep(p:last-child) { margin-bottom: 0; }
.bubble--agent :deep(h1),
.bubble--agent :deep(h2),
.bubble--agent :deep(h3) {
  font-size: 13px; font-weight: 700; margin: 12px 0 6px;
  color: var(--text); letter-spacing: .02em;
}
.bubble--agent :deep(h1):first-child,
.bubble--agent :deep(h2):first-child,
.bubble--agent :deep(h3):first-child { margin-top: 0; }
.bubble--agent :deep(table) {
  border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0;
  border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color);
}
.bubble--agent :deep(thead tr) { background: var(--body-bg); }
.bubble--agent :deep(th) {
  padding: 7px 12px; font-weight: 600; font-size: 12px;
  color: var(--text-subtle); text-transform: uppercase;
  letter-spacing: .04em; border-bottom: 2px solid var(--border-color);
  text-align: left; white-space: nowrap;
}
.bubble--agent :deep(td) {
  padding: 7px 12px; border-bottom: 1px solid var(--border-color);
  vertical-align: top; line-height: 1.55;
}
.bubble--agent :deep(tbody tr:last-child td) { border-bottom: none; }
.bubble--agent :deep(tbody tr:nth-child(even)) {
  background: color-mix(in srgb, var(--body-bg) 60%, transparent);
}
.bubble--agent :deep(code) {
  background: var(--body-bg); border: 1px solid var(--border-color);
  padding: 1px 5px; border-radius: 3px; font-size: 12px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.bubble--agent :deep(pre) {
  background: var(--body-bg); border: 1px solid var(--border-color);
  border-radius: 6px; padding: 10px; overflow-x: auto; margin: 8px 0;
}
.bubble--agent :deep(pre code) { background: none; border: none; padding: 0; }
.bubble--agent :deep(ul), .bubble--agent :deep(ol) { padding-left: 20px; margin: 4px 0; }
.bubble--agent :deep(li) { margin: 2px 0; }
.bubble--agent :deep(strong) { font-weight: 600; color: var(--text); }
.bubble--agent :deep(blockquote) {
  border-left: 3px solid var(--primary); margin: 6px 0; padding: 4px 10px;
  color: var(--text-subtle); background: color-mix(in srgb, var(--primary) 6%, transparent);
  border-radius: 0 4px 4px 0;
}
.bubble--agent :deep(hr) { border: none; border-top: 1px solid var(--border-color); margin: 8px 0; }

@media (max-width: 720px) {
  .chat-input-row {
    flex-direction: column;
  }
}
</style>
