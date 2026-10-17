<script setup>
import AnswerDraft from './AnswerDraft.vue'
import EvidenceValidation from './EvidenceValidation.vue'
import DiagnosisPerformance from './DiagnosisPerformance.vue'
import api from '../../../api'
import { computed, ref, watch } from 'vue'
import { useDiagnosisChat } from '../useDiagnosisChat'
import DiagnosisEvidenceChain from './DiagnosisEvidenceChain.vue'

const props = defineProps({
  systemId: { type: String, required: true },
  restartCapability: { type: Object, default: null },
  draftQuestion: { type: String, default: '' },
})

const {
  ask,
  startNewChat,
  historyReports,
  chats,
  activeChatId,
  chatManaging,
  renameChat,
  deleteChat,
  workflowHistory,
  openWorkflowHistory,
  openHistoryReport,
  activeReportId,
  imageReads,
  cancelDiagnosis,
  resumeDiagnosis,
  stopping,
  requestFix,
  decide,
  chatBox,
  clearMessages,
  exportHistoryBackup,
  loadHistory,
  loadModelOptions,
  diagnosing,
  fixing,
  historyLoading,
  messages,
  modelChoice,
  modelOptions,
  question,
  renderMd,
  lastQuestion,
  liveStepIcon,
  followUp,
  images,
  addImageFiles,
  removeImage,
  maxImages,
} = useDiagnosisChat(props.systemId)

import { message } from 'ant-design-vue'

const imageInput = ref(null)
const historyOpen = ref(false)
const previewUrl = ref('')
const orderedHistory = computed(() => [...chats.value].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)))
const activeTitle = computed(() => chats.value.find((item) => item.id === activeChatId.value)?.title || '新对话')
const editingChat = ref(null)
const editingTitle = ref('')
const deletingChat = ref(null)
function editTitle(chat) { editingChat.value = chat; editingTitle.value = chat.title }
async function saveTitle() { if (!editingChat.value) return; if (await renameChat(editingChat.value.id, editingTitle.value)) editingChat.value = null }
async function confirmDelete() { if (!deletingChat.value) return; if (await deleteChat(deletingChat.value.id)) deletingChat.value = null }
function chooseHistory(id) { openHistoryReport(id); historyOpen.value = false }
function onDropImages(event) { addImageFiles(event.dataTransfer?.files) }
function onComposerKey(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); ask() }
}

function triggerPickImage() {
  imageInput.value?.click()
}

function onPickImage(event) {
  addImageFiles(event.target.files)
  event.target.value = ''
}

// 支持直接粘贴截图（Ctrl/Cmd+V）
function onPasteImage(event) {
  const items = Array.from(event.clipboardData?.items || [])
  const files = items.filter((item) => item.type?.startsWith('image/')).map((item) => item.getAsFile())
  if (files.length) {
    event.preventDefault()
    addImageFiles(files)
  }
}

// 成本展示：0 且有用量 → 说明框架没给出成本（不能显示成"免费"）
function costText(cost, tokens) {
  if (cost == null) return '费用未知'
  if (!tokens) return ''
  if (cost > 0) return `≈$${Number(cost).toFixed(6)}`
  return '≈成本未知'
}

function previewImage(url) {
  if (url) previewUrl.value = url
}

const followUpAvailable = computed(() =>
  messages.value.some((m) => m.role === 'agent' && m.status === 'success' && m.reportId)
)

// 与 SVOM 浮窗一致的展示骨架：把回答拆成 结论/影响/建议/关键证据/待确认，
// 拆不出来时回退为整段 markdown（structured=false → null）。
async function onClearHistoryConfirmed() {
  // 二次确认通过后：先自动导出备份，再执行清空
  const count = await exportHistoryBackup()
  if (count < 0) {
    message.warning('备份导出失败，已取消清空操作（服务端诊断历史仍保留）')
    return
  }
  message.info(`已导出 ${count} 条诊断报告备份`)
  await clearMessages()
}

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
  unrecovered: '已执行·未恢复',
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

function modelOptionLabel(option) {
  if (!option?.model) return option?.label || option?.value
  const base = `${option.label} · ${option.model}`
  // 后端会为「本地模型」带 available/reason（就绪检查）：未就绪时直接标出来，
  // 避免用户选了之后才发现跑不起来（本地模型加载失败会长时间无响应）。
  return option.available === false ? `${base}（未就绪）` : base
}
</script>

<template>
  <section class="panel-card diagnose-card" aria-label="AI 诊断对话">
    <header class="diagnosis-header">
      <div><strong>{{ activeTitle }}</strong><span class="diagnosis-subtitle">AI 诊断</span></div>
      <div class="header-actions">
        <button type="button" class="toolbar-button" title="诊断历史" aria-label="诊断历史" :aria-expanded="historyOpen" @click="historyOpen = !historyOpen; historyOpen && loadHistory()">历史</button>
        <button type="button" class="toolbar-button" title="新对话" :disabled="diagnosing || fixing || chatManaging" @click="startNewChat(); historyOpen = false">新对话</button>
        <details class="chat-menu">
          <summary aria-label="更多操作" title="更多操作">···</summary>
          <div class="chat-menu__items">
            <a-button type="text" :loading="fixing" :disabled="diagnosing || fixing || !messages.length" @click="requestFix">申请修复</a-button>
            <a-button type="text" @click="exportHistoryBackup">导出历史</a-button>
            <a-button type="text" :disabled="!activeChatId || chatManaging" @click="editTitle(chats.find(item => item.id === activeChatId))">重命名</a-button>
            <a-button type="text" danger :disabled="!activeChatId || diagnosing || chatManaging" @click="deletingChat = chats.find(item => item.id === activeChatId)">删除对话</a-button>
          </div>
        </details>
      </div>
    </header>
    <div class="diagnosis-workspace">
      <aside v-if="historyOpen" class="diagnosis-history" aria-label="历史诊断">
        <div class="history-heading">最近诊断</div>
        <a-spin v-if="historyLoading" size="small" />
        <div v-for="chat in orderedHistory" :key="chat.id" :class="['history-chat', { 'is-active': chat.id === activeChatId }]">
          <button type="button" class="history-entry" :disabled="diagnosing || fixing || chatManaging" @click="chooseHistory(chat.id)">
            <span>{{ chat.title }}</span><small>{{ chat.running ? '执行中' : `${chat.report_count} 次诊断` }}</small>
          </button>
          <a-dropdown :trigger="['click']">
            <button type="button" class="history-chat__menu" :aria-label="`管理对话：${chat.title}`" title="对话操作" :disabled="chatManaging">···</button>
            <template #overlay><a-menu>
              <a-menu-item @click="editTitle(chat)">重命名</a-menu-item>
              <a-menu-item :disabled="chat.running" danger @click="deletingChat = chat">删除对话</a-menu-item>
            </a-menu></template>
          </a-dropdown>
        </div>
        <div v-if="workflowHistory.length" class="history-heading">修复提案</div>
        <button v-for="workflow in workflowHistory" :key="`workflow:${workflow.wfId}`" type="button" class="history-entry" :disabled="diagnosing || fixing" @click="openWorkflowHistory(workflow.wfId); historyOpen = false">
          <span>{{ workflow.action?.description || `修复提案 #${workflow.wfId}` }}</span><small>{{ STATUS_LABELS[workflow.status] || workflow.status }}</small>
        </button>
        <span v-if="!historyLoading && !orderedHistory.length" class="history-heading">暂无历史</span>
      </aside>
      <div class="diagnosis-conversation">
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
          <div v-if="message.role === 'user'" class="user-bubble-wrap">
            <div v-if="message.images?.length" class="user-images">
              <img
                v-for="(img, imgIndex) in message.images"
                :key="imgIndex"
                :src="img.dataUrl"
                :alt="img.name"
                class="user-image-thumb"
                @click="previewImage(img.dataUrl)"
              />
            </div>
            <span class="bubble bubble--user">{{ message.text }}</span>
            <div v-if="message.imageMeta?.length" class="user-images-note">
              含 {{ message.imageMeta.length }} 张图片附件
            </div>
          </div>
          <div v-else class="agent-response">
            <div v-if="message.unverifiedReason" class="unverified-hint">
              ⚠️ 本次未做任何工具取证（无工具调用、无证据步，结论也未引用知识库文档），
              内容可能缺少实时证据支撑，请人工核实后再采信。
            </div>
            <AnswerDraft :stream="message.answerStream" :status="message.status" :render-md="renderMd" />
            <div :class="['agent-answer', message.status === 'running' ? 'agent-answer--running' : '']">
              <template v-if="message.status === 'running'">
                <a-spin size="small" class="bubble__spin" />
                <span v-html="renderMd(message.text)" />
                <ul v-if="message.liveSteps?.length" class="bubble__live-steps">
                  <li
                    v-for="step in message.liveSteps"
                    :key="step.call_id || step.step"
                    :class="['live-step', `live-step--${step.status || 'started'}`]"
                  >
                    <span class="live-step__icon">{{ liveStepIcon(step.status) }}</span>
                    <span class="live-step__label">{{ step.label }}</span>
                    <span class="live-step__detail">{{ step.detail }}</span>
                  </li>
                </ul>
              </template>

              <div v-else class="md" v-html="renderMd(message.text)" />
            </div>

            <DiagnosisPerformance v-if="message.role === 'agent'" :key="message.reportId" :performance="message.performance" :load-details="async () => { const { data } = await api.get(`/systems/${systemId}/diagnosis-reports/${message.reportId}`); return data.business_context.performance }" />
            <EvidenceValidation v-if="message.status === 'success'" :validation="message.evidenceValidation" :report-id="message.reportId" />
            <DiagnosisEvidenceChain
              v-if="message.role === 'agent' && message.status !== 'running' && (message.evidence?.length || message.evidenceSources?.length || message.templateDescription || message.knowledgeRefs?.length)"
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

            <!-- 已停止：给「继续 / 强制刷新继续」；不可恢复时明确说明（不谎称可继续） -->
            <div v-if="message.status === 'cancelled'" class="cancelled-bar">
              <span class="cancelled-note">
                已停止<template v-if="message.cancelPhase === 'model_request'">（当时正在等模型回复）</template>
              </span>
              <template v-if="message.canResume">
                <button type="button" class="resume-button" :disabled="diagnosing || fixing" @click="resumeDiagnosis(index)">继续</button>
                <button type="button" class="resume-button" :disabled="diagnosing || fixing" @click="resumeDiagnosis(index, true)">刷新证据后继续</button>
              </template>
              <span v-else class="cancelled-note">未取得可恢复历史，无法继续</span>
            </div>

            <!-- 用量/成本：本次 vs 会话累计；cost 为价格表估算（界面标 ≈，含估算时标注） -->
            <div v-if="message.status !== 'running' && (message.totalTokens || message.usageComplete === false)" class="usage-line">
              <span v-if="message.totalTokens">本次 {{ message.totalTokens }} tokens</span>
              <span v-else>用量未确认</span>
              <template v-if="costText(message.costUsd, message.totalTokens)"> · {{ costText(message.costUsd, message.totalTokens) }}</template>
              <template v-if="message.conversationTokens && message.conversationTokens !== message.totalTokens">
                ｜本会话累计 {{ message.conversationTokens }} tokens
                <template v-if="costText(message.conversationCostUsd, message.conversationTokens)"> {{ costText(message.conversationCostUsd, message.conversationTokens) }}</template>
              </template>
              <span v-if="message.usageComplete === false" class="usage-warn" :title="message.usageGapReason">（用量或费用未完整确认）</span>
              <span class="usage-note">费用为估算值</span>
            </div>
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
    </div>

    <footer class="diagnosis-input" @dragover.prevent @drop.prevent="onDropImages">
      <div class="composer">
        <div v-if="images.length" class="image-tray">
          <div v-for="(img, index) in images" :key="index" class="image-chip">
            <img :src="img.dataUrl" :alt="img.name" @click="previewImage(img.dataUrl)" />
            <button type="button" class="image-chip__remove" aria-label="移除图片" @click="removeImage(index)">×</button>
          </div>
          <span class="image-tray__hint">{{ images.length }}/{{ maxImages }} 张图片</span>
        </div>
        <input ref="imageInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden @change="onPickImage" />
        <a-textarea v-model:value="question" :auto-size="{ minRows: 2, maxRows: 7 }" aria-label="诊断问题" :disabled="fixing"
          placeholder="描述问题，或直接粘贴、拖入图片…" @keydown="onComposerKey" @paste="onPasteImage" />
        <div class="composer-toolbar">
          <div class="composer-options">
            <button type="button" class="toolbar-button attachment-button" aria-label="添加图片" title="添加图片（也可粘贴或拖入）" :disabled="fixing || images.length + imageReads >= maxImages" @click="triggerPickImage">＋</button>
            <a-select v-model:value="modelChoice" class="model-select" :bordered="false" aria-label="选择模型" option-label-prop="label" :disabled="diagnosing || fixing" @dropdown-visible-change="(open) => open && loadModelOptions()">
              <a-select-option v-for="option in modelOptions" :key="option.value" :value="option.value" :label="modelOptionLabel(option)" :disabled="option.available === false">{{ modelOptionLabel(option) }}</a-select-option>
            </a-select>
            <label v-if="followUpAvailable" class="followup-switch"><input type="checkbox" v-model="followUp" />接着聊</label>
          </div>
          <button v-if="diagnosing" type="button" class="composer-send" :disabled="stopping || !activeReportId" :aria-label="stopping ? '正在停止' : '停止诊断'" :title="stopping ? '正在停止，等待执行结束' : '停止诊断'" @click="cancelDiagnosis"><span class="stop-square" /></button>
          <button v-else type="button" class="composer-send" aria-label="发送诊断" title="发送（Enter）" :disabled="fixing || chatManaging || imageReads > 0 || (!question.trim() && !images.length)" @click="ask">↑</button>
        </div>
      </div>
      <div class="composer-caption" aria-live="polite">{{ stopping ? '正在停止，等待执行方确认' : (diagnosing ? '诊断进行中 · 可以先写下一条问题' : (imageReads ? '正在读取图片…' : 'Enter 发送 · Shift+Enter 换行 · 图片随消息发送')) }}</div>
    </footer>
      </div>
    </div>
    <a-modal :open="!!editingChat" title="重命名对话" ok-text="保存" cancel-text="取消" :confirm-loading="chatManaging" :ok-button-props="{ disabled: !editingTitle.trim() }" @ok="saveTitle" @cancel="editingChat = null">
      <a-input v-model:value="editingTitle" :maxlength="120" aria-label="对话名称" placeholder="输入对话名称" @press-enter="saveTitle" />
    </a-modal>
    <a-modal :open="!!deletingChat" title="删除对话" ok-text="删除" cancel-text="取消" :confirm-loading="chatManaging" :ok-button-props="{ danger: true }" @ok="confirmDelete" @cancel="deletingChat = null">
      <p>删除「{{ deletingChat?.title }}」及全部消息、诊断报告和续跑历史？此操作无法恢复。</p>
    </a-modal>
    <a-modal :open="!!previewUrl" title="图片预览" :footer="null" width="min(900px, calc(100vw - 32px))" @cancel="previewUrl = ''">
      <img v-if="previewUrl" :src="previewUrl" alt="图片预览" style="display:block;max-width:100%;max-height:70vh;object-fit:contain;margin:auto" />
    </a-modal>
  </section>
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
  background: transparent;
  border-radius: 10px;
  padding: 4px 2px;
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
.agent-response { max-width: 100%; }
.bubble { display: inline-block; max-width: 85%; font-size: 14px; line-height: 1.7; }
.agent-response .bubble { max-width: 100%; }
.bubble--user {
  padding: 9px 14px;
  border-radius: 18px;
  background: var(--body-bg);
  color: var(--text);
}
.agent-answer { font-size: 14px; line-height: 1.7; color: var(--text); text-align: left; word-break: break-word; overflow-x: auto; }
.unverified-hint {
  margin: 0 0 10px;
  padding: 8px 12px;
  border: 1px solid #ffe08a;
  border-left: 3px solid #faad14;
  border-radius: 6px;
  background: #fffbe6;
  color: #8a6100;
  font-size: 13px;
  line-height: 1.6;
}
.agent-answer--running { display: flex; align-items: flex-start; gap: 8px; }

/* 结论：唯一焦点 */
.verdict-line { display: flex; align-items: flex-start; gap: 9px; }
.verdict-dot { flex-shrink: 0; width: 9px; height: 9px; margin-top: 8px; border-radius: 50%; background: var(--primary); }
.verdict-dot--high { background: #d48806; }
.verdict-dot--medium { background: var(--primary); }
.verdict-dot--low { background: #b9b9b9; }
.verdict-body { flex: 1; min-width: 0; }
.verdict-text { margin: 0; font-size: 14px; font-weight: 600; color: var(--text); }
.verdict-impact { margin: 4px 0 0; color: var(--text-subtle); font-size: 13px; font-weight: 400; }
.confidence-pill { flex-shrink: 0; padding: 1px 8px; border: 1px solid var(--border-color); border-radius: 999px; color: var(--text-subtle); font-size: 11px; white-space: nowrap; }

/* 建议 */
.answer-section { margin-top: 14px; }
.answer-section__title { margin-bottom: 4px; color: var(--text-subtle); font-size: 12px; font-weight: 600; }

/* 折叠披露 */
.answer-details { margin-top: 12px; }
.answer-details > summary { display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; margin-left: -8px; border-radius: 8px; color: var(--text-subtle); font-size: 12px; cursor: pointer; }
.answer-details > summary::-webkit-details-marker { display: none; }
.answer-details > summary::before { content: '▸'; font-size: 10px; color: var(--text-subtle); transition: transform .15s; }
.answer-details[open] > summary::before { transform: rotate(90deg); }
.answer-details > summary:hover { background: color-mix(in srgb, var(--border-color) 32%, transparent); color: var(--text); }
.answer-details__body { margin-top: 8px; padding-left: 14px; border-left: 2px solid var(--border-color); }
.bubble__spin {
  margin-top: 3px;
  flex-shrink: 0;
}
.bubble__live-steps {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  width: 100%;
  max-height: 260px;
  overflow-y: auto;
}
.live-step {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 0;
  font-size: 12.5px;
  line-height: 1.5;
  border-bottom: 1px dashed var(--border-color);
}
.live-step:last-child { border-bottom: none; }
.live-step__icon { flex-shrink: 0; }
.live-step--started .live-step__label { color: var(--primary, #1677ff); font-weight: 600; }
.live-step__label { flex-shrink: 0; }
.live-step__detail {
  color: var(--text-color-secondary, #888);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
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
.status-tag--unrecovered { background: rgba(250,173,20,.15); color: #d48806; }
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

.followup-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary, #888);
  white-space: nowrap;
  cursor: pointer;
}
.chat-input-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.image-tray {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  background: color-mix(in srgb, var(--border-color) 12%, transparent);
}
.image-tray__hint { font-size: 11px; color: var(--text-muted, #8a8f98); }
.image-chip { position: relative; width: 46px; height: 46px; border-radius: 6px; overflow: hidden; border: 1px solid var(--border-color); }
.image-chip img { width: 100%; height: 100%; object-fit: cover; display: block; }
.image-chip__remove {
  position: absolute; top: -1px; right: -1px; width: 16px; height: 16px; line-height: 14px;
  border: none; border-radius: 50%; background: rgba(0, 0, 0, .65); color: #fff; font-size: 12px; cursor: pointer; padding: 0;
}
.image-pick-btn { flex: 0 0 auto; }
.stop-btn { margin-left: 10px; }
.cancelled-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px;
  padding: 6px 10px; border-radius: 8px; background: color-mix(in srgb, #faad14 12%, transparent);
  border: 1px solid color-mix(in srgb, #faad14 35%, transparent); }
.cancelled-note { font-size: 12px; color: var(--text-muted, #8a8f98); }
.usage-line { margin-top: 6px; font-size: 11px; color: var(--text-muted, #8a8f98); }
.usage-warn { color: #faad14; margin-left: 4px; }
.usage-note { margin-left: 6px; opacity: .75; }
.user-bubble-wrap { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.user-images { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.user-image-thumb {
  width: 76px; height: 76px; object-fit: cover; border-radius: 8px;
  border: 1px solid var(--border-color); cursor: zoom-in;
}
.user-images-note { font-size: 11px; color: var(--text-muted, #8a8f98); }
.model-select {
  width: 220px;
  flex: 0 0 220px;
}
.model-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.25;
}
.model-option small {
  color: var(--text-subtle);
  font-size: 11px;
}
.chat-input {
  min-width: 0;
  flex: 1;
}
.agent-answer :deep(p) { margin: 0 0 8px; }
.agent-answer :deep(p:last-child) { margin-bottom: 0; }
.agent-answer :deep(h1),
.agent-answer :deep(h2),
.agent-answer :deep(h3) {
  font-size: 13px; font-weight: 700; margin: 12px 0 6px;
  color: var(--text); letter-spacing: .02em;
}
.agent-answer :deep(h1):first-child,
.agent-answer :deep(h2):first-child,
.agent-answer :deep(h3):first-child { margin-top: 0; }
.agent-answer :deep(table) {
  border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0;
  border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color);
}
.agent-answer :deep(thead tr) { background: var(--body-bg); }
.agent-answer :deep(th) {
  padding: 7px 12px; font-weight: 600; font-size: 12px;
  color: var(--text-subtle); text-transform: uppercase;
  letter-spacing: .04em; border-bottom: 2px solid var(--border-color);
  text-align: left; white-space: nowrap;
}
.agent-answer :deep(td) {
  padding: 7px 12px; border-bottom: 1px solid var(--border-color);
  vertical-align: top; line-height: 1.55; white-space: nowrap;
}
.agent-answer :deep(tbody tr:last-child td) { border-bottom: none; }
.agent-answer :deep(tbody tr:nth-child(even)) {
  background: color-mix(in srgb, var(--body-bg) 60%, transparent);
}
.agent-answer :deep(code) {
  background: var(--body-bg); border: 1px solid var(--border-color);
  padding: 1px 5px; border-radius: 3px; font-size: 12px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.agent-answer :deep(pre) {
  background: var(--body-bg); border: 1px solid var(--border-color);
  border-radius: 6px; padding: 10px; overflow-x: auto; margin: 8px 0;
}
.agent-answer :deep(pre code) { background: none; border: none; padding: 0; }
.agent-answer :deep(ul), .agent-answer :deep(ol) { padding-left: 20px; margin: 4px 0; }
.agent-answer :deep(li) { margin: 2px 0; }
.agent-answer :deep(strong) { font-weight: 600; color: var(--text); }
.agent-answer :deep(blockquote) {
  border-left: 3px solid var(--primary); margin: 6px 0; padding: 4px 10px;
  color: var(--text-subtle); background: color-mix(in srgb, var(--primary) 6%, transparent);
  border-radius: 0 4px 4px 0;
}
.agent-answer :deep(hr) { border: none; border-top: 1px solid var(--border-color); margin: 8px 0; }

@media (max-width: 720px) {
  .followup-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary, #888);
  white-space: nowrap;
  cursor: pointer;
}
.chat-input-row {
    flex-direction: column;
  }
  .model-select {
    width: 100%;
    flex-basis: auto;
  }
}

.diagnose-card { display: flex; flex-direction: column; height: min(820px, calc(100dvh - 230px)); min-height: 480px; overflow: hidden; background: var(--card-bg); }
.diagnosis-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 16px 20px; border-bottom: 1px solid var(--border-color); }
.diagnosis-header strong { font-size: 15px; }
.diagnosis-subtitle { color: var(--text-subtle); font-size: 12px; margin-left: 12px; }
.header-actions { display: flex; align-items: center; gap: 6px; }
.toolbar-button { padding: 5px 9px; border: 0; border-radius: 7px; background: transparent; color: var(--text-subtle); cursor: pointer; font-size: 12px; }
.toolbar-button:hover { background: var(--body-bg); color: var(--text); }
.toolbar-button:disabled { opacity: .4; cursor: not-allowed; }
.chat-menu { position: relative; }
.chat-menu summary { list-style: none; cursor: pointer; padding: 4px 8px; font-size: 18px; }
.chat-menu summary::-webkit-details-marker { display: none; }
.chat-menu__items { position: absolute; right: 0; top: 32px; z-index: 4; width: 150px; padding: 6px; border: 1px solid var(--border-color); border-radius: 8px; background: var(--card-bg); display: flex; flex-direction: column; }
.diagnosis-workspace { display: flex; flex: 1; min-height: 0; }
.diagnosis-history { flex: 0 0 190px; padding: 12px 8px; border-right: 1px solid var(--border-color); overflow-y: auto; background: var(--body-bg); }
.history-heading { display: block; font-size: 12px; color: var(--text-subtle); padding: 8px; }
.history-entry { display: flex; flex-direction: column; gap: 6px; width: 100%; padding: 10px; border: 0; border-radius: 8px; background: transparent; text-align: left; color: var(--text); cursor: pointer; }
.history-entry:hover { background: var(--card-bg); }
.history-entry span { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; }
.history-entry small { font-size: 11px; color: var(--text-subtle); }
.diagnosis-conversation { display: flex; flex-direction: column; min-width: 0; flex: 1; min-height: 0; }
.chat-box, .chat-box--tall { height: auto; min-height: 0; flex: 1; margin: 0; padding: 24px; }
.msg-row { margin: 0 0 24px; }
.agent-answer { font-size: 14px; line-height: 1.85; overflow: visible; }
.agent-answer--running { display: block; color: var(--text-subtle); }
.bubble__spin { margin-right: 8px; }
.bubble--user { white-space: pre-wrap; }
.cancelled-bar { border: 0; background: transparent; padding: 8px 0; }
.usage-line { line-height: 1.6; }
.diagnosis-input { padding: 12px 20px 14px; flex-shrink: 0; }
.composer { border: 1px solid var(--border-color); border-radius: 16px; background: var(--body-bg); padding: 12px; }
.composer:focus-within { border-color: var(--text-subtle); }
.composer :deep(textarea.ant-input) { background: transparent; border: 0; box-shadow: none; padding: 0 2px 10px; resize: none; }
.composer-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.composer-options { display: flex; align-items: center; gap: 6px; min-width: 0; flex-wrap: wrap; }
.attachment-button { font-size: 22px; padding: 0 8px; }
.model-select { width: 180px; flex: 0 1 180px; font-size: 12px; }
.composer-send { flex-shrink: 0; display: flex; align-items: center; justify-content: center; padding: 0; width: 34px; height: 34px; border: 0; border-radius: 50%; background: var(--text); color: var(--card-bg); font-size: 22px; cursor: pointer; }
.composer-send:disabled { opacity: .3; cursor: not-allowed; }
.stop-square { width: 11px; height: 11px; border-radius: 2px; background: currentColor; }
.composer-caption { text-align: center; color: var(--text-subtle); font-size: 10px; margin-top: 8px; }
.image-tray { border: 0; background: transparent; padding: 0 0 12px; }
.image-chip { width: 64px; height: 64px; border-radius: 10px; }
.image-chip__remove { width: 20px; height: 20px; line-height: 18px; }
.agent-answer :deep(table) { display: block; overflow-x: auto; }
.agent-answer :deep(td), .agent-answer :deep(th) { white-space: normal; min-width: 90px; }
.agent-answer :deep(h1), .agent-answer :deep(h2), .agent-answer :deep(h3) { font-size: 15px; margin: 18px 0 8px; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--text-subtle); outline-offset: 2px; }
@media (max-width: 720px) {
  .diagnose-card { height: calc(100dvh - 180px); min-height: 420px; }
  .diagnosis-subtitle { display: none; }
  .diagnosis-history { flex-basis: 140px; }
  .history-entry span { max-width: 120px; }
  .chat-box { padding: 16px; }
  .diagnosis-input { padding: 10px; }
  .model-select { width: 130px; flex-basis: 130px; }
}

.resume-button { border: 1px solid var(--border-color); border-radius: 8px; padding: 5px 10px; background: var(--card-bg); color: var(--text); font-size: 12px; cursor: pointer; }
.resume-button:disabled { opacity: .4; cursor: not-allowed; }
.diagnosis-workspace { position: relative; }
.agent-answer .md { overflow-x: auto; }
.agent-answer :deep(table) { display: table; width: 100%; }
@media (max-width: 720px) {
  .diagnosis-history { position: absolute; left: 0; top: 0; bottom: 0; z-index: 3; width: min(240px, 78%); }
  .history-entry span { max-width: 210px; }
}
.history-chat { display: flex; align-items: center; border-radius: 8px; }
.history-chat.is-active { background: var(--card-bg); }
.history-chat .history-entry { flex: 1; min-width: 0; }
.history-chat .history-entry span { max-width: 130px; }
.history-chat__menu { flex-shrink: 0; padding: 8px; border: 0; background: transparent; color: var(--text-subtle); cursor: pointer; font-size: 16px; }
</style>
