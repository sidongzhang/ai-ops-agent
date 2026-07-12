<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  BellOutlined,
  CheckCircleOutlined,
  DownOutlined,
  ReloadOutlined,
  RocketOutlined,
  WarningOutlined,
} from '@ant-design/icons-vue'
import api from '../api'

const props = defineProps({
  defaultView: { type: String, default: 'messages' },
})

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const actionId = ref(null)
const viewMode = ref(props.defaultView === 'incidents' ? 'incidents' : 'messages')
const activeStatus = ref('all')
const activeIncidentStatus = ref('all')
const expandedIds = ref(new Set())
const expandedIncidentIds = ref(new Set())
const incidentDetails = ref({})
const incidentActionId = ref(null)
const messages = ref([])
const incidents = ref([])
const incidentSummary = ref(null)
const pendingWorkflows = ref([])
const systems = ref([])
const messagePage = ref(1)
const messagePageSize = ref(20)
const messageTotal = ref(0)
const unreadTotal = ref(0)

const statusOptions = [
  { key: 'all', label: '全部' },
  { key: 'unread', label: '未读' },
  { key: 'acknowledged', label: '已确认' },
  { key: 'resolved', label: '已解决' },
  { key: 'delivery_failed', label: '通知失败' },
]

const incidentStatusOptions = [
  { key: 'all', label: '全部' },
  { key: 'open', label: '进行中' },
  { key: 'acknowledged', label: '已确认' },
  { key: 'resolved', label: '已解决' },
]

const incidentStatusText = {
  open: '进行中',
  acknowledged: '已确认',
  resolved: '已解决',
}

const statusText = {
  unread: '未读',
  read: '已读',
  acknowledged: '已确认',
  resolved: '已解决',
}

const statusColor = {
  unread: 'error',
  read: 'default',
  acknowledged: 'processing',
  resolved: 'success',
}

const severityText = {
  info: '信息',
  warning: '警告',
  critical: '严重',
}

const severityAccent = {
  info: 'accent-info',
  warning: 'accent-warning',
  critical: 'accent-critical',
}

const channelText = {
  web: '网页',
  feishu: '飞书',
  email: '邮件',
  webhook: 'Webhook',
}

const systemNameById = computed(() => {
  const map = new Map()
  for (const system of systems.value) map.set(system.id, system.name)
  return map
})

const unreadCount = computed(() => unreadTotal.value)
const failedDeliveryCount = computed(() => messages.value.filter(hasFailedChannels).length)
const resolvedCount = computed(() => messages.value.filter((item) => item.status === 'resolved').length)
const openIncidentCount = computed(() => incidents.value.filter((item) => item.status === 'open').length)
const pendingWorkflowCount = computed(() => pendingWorkflows.value.length)

const activeIncidents = computed(() => {
  if (activeIncidentStatus.value === 'all') return incidents.value
  return incidents.value.filter((item) => item.status === activeIncidentStatus.value)
})

const incidentSummaryCards = computed(() => {
  if (!incidentSummary.value) return []
  return [
    { key: 'open', label: '进行中', value: incidentSummary.value.open, tone: 'warn' },
    { key: 'acknowledged', label: '已确认', value: incidentSummary.value.acknowledged, tone: 'neutral' },
    { key: 'resolved', label: '已解决', value: incidentSummary.value.resolved, tone: 'ok' },
    { key: 'total', label: '累计事故', value: incidentSummary.value.total, tone: 'neutral' },
  ]
})

function incidentDuration(incident) {
  const start = new Date(incident.first_seen).getTime()
  const end = incident.resolved_at ? new Date(incident.resolved_at).getTime() : Date.now()
  const minutes = Math.max(1, Math.round((end - start) / 60000))
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.round(minutes / 60)
  return `${hours} 小时`
}

const summaryCards = computed(() => [
  {
    key: 'total',
    label: '消息总数',
    value: messageTotal.value,
    detail: '告警、诊断与处理结果',
    tone: 'neutral',
    icon: BellOutlined,
  },
  {
    key: 'unread',
    label: '未读消息',
    value: unreadCount.value,
    detail: '待人工确认',
    tone: unreadCount.value ? 'warn' : 'neutral',
    icon: WarningOutlined,
  },
  {
    key: 'failed',
    label: '通知失败',
    value: failedDeliveryCount.value,
    detail: '外部渠道发送失败',
    tone: failedDeliveryCount.value ? 'warn' : 'neutral',
    icon: WarningOutlined,
  },
  {
    key: 'resolved',
    label: '已解决',
    value: resolvedCount.value,
    detail: '已标记处理完成',
    tone: 'ok',
    icon: CheckCircleOutlined,
  },
  {
    key: 'pending-workflows',
    label: '待审批修复',
    value: pendingWorkflowCount.value,
    detail: 'AI 已给出处置提案',
    tone: pendingWorkflowCount.value ? 'warn' : 'neutral',
    icon: RocketOutlined,
  },
])

const activeMessages = computed(() => {
  if (activeStatus.value === 'all') return messages.value
  if (activeStatus.value === 'delivery_failed') return messages.value.filter(hasFailedChannels)
  return messages.value.filter((item) => item.status === activeStatus.value)
})

function hasFailedChannels(item) {
  return item.channels?.some((channel) => channel.type !== 'web' && channel.status === 'failed')
}

function workflowStatusText(status) {
  return {
    pending: '待审批',
    approved: '执行中',
    done: '已执行',
    rejected: '已拒绝',
    error: '执行失败',
  }[status] || status
}

function workflowStatusColor(status) {
  return {
    pending: 'warning',
    approved: 'processing',
    done: 'success',
    rejected: 'default',
    error: 'error',
  }[status] || 'default'
}

function workflowActionText(item) {
  const action = item?.proposed_action || {}
  return {
    fetch_logs: '拉取日志',
    health_check: '健康检查',
    restart_container: '重启容器',
    restart_systemd: '重启服务',
    run_redis_command: 'Redis 命令',
    manual: '人工处理',
  }[action.type] || action.type || '修复提案'
}

function workflowSummary(item) {
  const parts = [
    workflowActionText(item),
    item?.target_service,
    item?.target_resource,
  ].filter(Boolean)
  return parts.join(' · ') || 'AI 修复提案'
}

function messageWorkflows(item) {
  return pendingWorkflows.value.filter((workflow) => workflow.system_id === item.system_id)
}

function incidentWorkflows(incident) {
  const failed = new Set((incident.failed_services || []).map((name) => String(name).toLowerCase()))
  return pendingWorkflows.value.filter((workflow) => {
    if (workflow.system_id !== incident.system_id) return false
    const target = String(workflow.target_service || '').toLowerCase()
    if (!target) return true
    return failed.has(target) || [...failed].some((name) => target.includes(name) || name.includes(target))
  })
}

function incidentActionLoading(incident, action) {
  return incidentActionId.value === `${incident.id}-${action}`
}

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function previewText(item) {
  return item.summary || item.content || '暂无摘要'
}

function isExpanded(id) {
  return expandedIds.value.has(id)
}

function isIncidentExpanded(id) {
  return expandedIncidentIds.value.has(id)
}

function toggleExpand(id) {
  const next = new Set(expandedIds.value)
  const expanding = !next.has(id)
  if (expanding) {
    next.add(id)
    expandedIds.value = next
    const item = messages.value.find((current) => current.id === id)
    if (item?.status === 'unread') {
      api.post(`/messages/${id}/read`).then(({ data }) => {
        messages.value = messages.value.map((current) => (current.id === id ? data : current))
        if (unreadTotal.value > 0) unreadTotal.value -= 1
        window.dispatchEvent(new Event('aiops:messages-changed'))
      }).catch(() => {})
    }
    return
  }
  next.delete(id)
  expandedIds.value = next
}

async function toggleIncidentExpand(id) {
  const next = new Set(expandedIncidentIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
    if (!incidentDetails.value[id]) {
      try {
        const { data } = await api.get(`/incidents/${id}`)
        incidentDetails.value = { ...incidentDetails.value, [id]: data }
      } catch (error) {
        message.error(error?.response?.data?.detail || '事故详情加载失败')
        return
      }
    }
  }
  expandedIncidentIds.value = next
}

async function loadMessages() {
  loading.value = true
  try {
    const messageParams = {
      limit: messagePageSize.value,
      offset: (messagePage.value - 1) * messagePageSize.value,
    }
    if (['unread', 'read', 'acknowledged', 'resolved'].includes(activeStatus.value)) {
      messageParams.status = activeStatus.value
    }
    const [
      { data: messageData },
      { data: systemData },
      { data: incidentData },
      { data: summaryData },
      { data: pendingWorkflowData },
      { data: unreadData },
    ] = await Promise.all([
      api.get('/messages', { params: messageParams }),
      api.get('/systems'),
      api.get('/incidents', { params: { limit: 100 } }),
      api.get('/incidents/summary'),
      api.get('/workflows/pending', { params: { limit: 100 } }),
      api.get('/messages/unread-count'),
    ])
    messages.value = messageData.items || messageData
    messageTotal.value = messageData.total ?? messages.value.length
    unreadTotal.value = unreadData.count || 0
    systems.value = systemData
    incidents.value = incidentData
    incidentSummary.value = summaryData
    pendingWorkflows.value = pendingWorkflowData.items || pendingWorkflowData
  } catch (error) {
    message.error(error?.response?.data?.detail || '消息加载失败')
  } finally {
    loading.value = false
  }
}

function onMessagePageChange(page, pageSize) {
  messagePage.value = page
  messagePageSize.value = pageSize
  loadMessages()
}

function syncViewMode(nextView) {
  const targetView = nextView === 'incidents' ? 'incidents' : 'messages'
  viewMode.value = targetView
  if (targetView === 'incidents' && route.path !== '/incidents') {
    router.replace('/incidents')
    return
  }
  if (targetView === 'messages' && route.path !== '/messages') {
    router.replace('/messages')
  }
}

watch(activeStatus, () => {
  messagePage.value = 1
  loadMessages()
})

watch(
  () => props.defaultView,
  (value) => {
    const nextView = value === 'incidents' ? 'incidents' : 'messages'
    if (nextView !== viewMode.value) {
      viewMode.value = nextView
    }
  },
)

watch(
  () => route.path,
  (path) => {
    const nextView = path === '/incidents' ? 'incidents' : 'messages'
    if (nextView !== viewMode.value) {
      viewMode.value = nextView
    }
  },
)

async function updateIncidentStatus(incident, action) {
  incidentActionId.value = `${incident.id}-${action}`
  try {
    const { data } = await api.post(`/incidents/${incident.id}/${action}`)
    incidents.value = incidents.value.map((current) => (current.id === incident.id ? data : current))
    if (incidentDetails.value[incident.id]) {
      const { data: detail } = await api.get(`/incidents/${incident.id}`)
      incidentDetails.value = { ...incidentDetails.value, [incident.id]: detail }
    }
    await loadMessages()
    window.dispatchEvent(new Event('aiops:messages-changed'))
    message.success(action === 'ack' ? '事故已确认' : '事故已解决')
  } catch (error) {
    message.error(error?.response?.data?.detail || '操作失败')
  } finally {
    incidentActionId.value = null
  }
}

async function createIncidentWorkflow(incident) {
  incidentActionId.value = `${incident.id}-workflow`
  try {
    const { data } = await api.post(`/incidents/${incident.id}/workflow`)
    pendingWorkflows.value = [data, ...pendingWorkflows.value.filter((item) => item.id !== data.id)]
    message.success('已生成修复提案，请到审批中心确认执行')
    window.dispatchEvent(new Event('aiops:approvals-changed'))
  } catch (error) {
    message.error(error?.response?.data?.detail || '修复提案生成失败')
  } finally {
    incidentActionId.value = null
  }
}

async function updateMessageStatus(item, action) {
  actionId.value = item.id
  try {
    const { data } = await api.post(`/messages/${item.id}/${action}`)
    messages.value = messages.value.map((current) => (current.id === item.id ? data : current))
    window.dispatchEvent(new Event('aiops:messages-changed'))
    message.success(action === 'ack' ? '已确认告警' : '已标记解决')
  } catch (error) {
    message.error(error?.response?.data?.detail || '操作失败')
  } finally {
    actionId.value = null
  }
}

async function retryNotifications(item) {
  actionId.value = `retry-${item.id}`
  try {
    const { data } = await api.post(`/messages/${item.id}/retry-notifications`)
    messages.value = messages.value.map((current) => (current.id === item.id ? data : current))
    const failed = data.channels?.filter((channel) => channel.status === 'failed') || []
    if (failed.length) {
      message.warning(`仍有 ${failed.length} 个渠道发送失败，请检查配置`)
    } else {
      message.success('失败通知已重试成功')
    }
  } catch (error) {
    message.error(error?.response?.data?.detail || '通知重试失败')
  } finally {
    actionId.value = null
  }
}

onMounted(loadMessages)
</script>

<template>
  <div class="messages-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">消息中心</h2>
        <p class="page-sub">集中查看告警、诊断建议和处理状态</p>
      </div>
      <div class="header-actions">
        <a-tag v-if="unreadCount" color="error" class="header-tag">{{ unreadCount }} 条未读</a-tag>
        <a-tag v-if="failedDeliveryCount" color="warning" class="header-tag">
          {{ failedDeliveryCount }} 条通知失败
        </a-tag>
        <a-button :loading="loading" @click="loadMessages">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </div>
    </div>

    <div class="summary-grid">
      <div
        v-for="card in summaryCards"
        :key="card.key"
        :class="['summary-card', `summary-card--${card.tone}`]"
      >
        <div class="summary-icon">
          <component :is="card.icon" />
        </div>
        <div class="summary-body">
          <span class="summary-label">{{ card.label }}</span>
          <strong class="summary-value">{{ card.value }}</strong>
          <span class="summary-detail">{{ card.detail }}</span>
        </div>
      </div>
    </div>

    <div v-if="pendingWorkflows.length" class="workflow-spotlight">
      <div class="workflow-spotlight__head">
        <div>
          <div class="workflow-spotlight__title">待处理修复提案</div>
          <div class="workflow-spotlight__sub">AI 已根据当前异常生成建议动作，适合从这里直接进入审批</div>
        </div>
        <a-button size="small" @click="router.push('/approvals')">前往审批中心</a-button>
      </div>
      <div class="workflow-spotlight__list">
        <button
          v-for="item in pendingWorkflows.slice(0, 3)"
          :key="item.id"
          type="button"
          class="workflow-spotlight__item"
          @click="router.push('/approvals')"
        >
          <div class="workflow-spotlight__item-top">
            <span class="workflow-spotlight__item-title">{{ workflowSummary(item) }}</span>
            <a-tag :color="workflowStatusColor(item.status)" class="flat-tag">
              {{ workflowStatusText(item.status) }}
            </a-tag>
          </div>
          <div class="workflow-spotlight__meta">
            <span class="meta-chip">{{ item.system_name || `系统 #${item.system_id}` }}</span>
            <span class="meta-time">{{ formatTime(item.created_at) }}</span>
          </div>
          <p class="workflow-spotlight__diag">{{ item.diagnosis || item.question }}</p>
        </button>
      </div>
    </div>

    <div class="filter-row view-switch">
      <button
        :class="['filter-chip', { active: viewMode === 'messages' }]"
        @click="syncViewMode('messages')"
      >
        消息
        <span v-if="unreadCount" class="chip-count">{{ unreadCount }}</span>
      </button>
      <button
        :class="['filter-chip', { active: viewMode === 'incidents' }]"
        @click="syncViewMode('incidents')"
      >
        事故
        <span v-if="openIncidentCount" class="chip-count warn">{{ openIncidentCount }}</span>
      </button>
    </div>

    <div v-if="viewMode === 'messages'" class="filter-row">
      <button
        v-for="option in statusOptions"
        :key="option.key"
        :class="['filter-chip', { active: activeStatus === option.key }]"
        @click="activeStatus = option.key"
      >
        {{ option.label }}
        <span v-if="option.key === 'unread' && unreadCount" class="chip-count">{{ unreadCount }}</span>
        <span v-if="option.key === 'delivery_failed' && failedDeliveryCount" class="chip-count warn">
          {{ failedDeliveryCount }}
        </span>
      </button>
    </div>

    <a-spin v-if="loading" class="page-spin" />

    <template v-else-if="viewMode === 'incidents'">
      <div v-if="incidentSummaryCards.length" class="summary-grid incident-summary-grid">
        <div
          v-for="card in incidentSummaryCards"
          :key="card.key"
          :class="['summary-card', card.tone === 'warn' ? 'summary-card--warn' : card.tone === 'ok' ? 'summary-card--ok' : '']"
        >
          <span class="summary-label">{{ card.label }}</span>
          <strong class="summary-value">{{ card.value }}</strong>
        </div>
      </div>

      <div class="filter-row">
        <button
          v-for="option in incidentStatusOptions"
          :key="option.key"
          :class="['filter-chip', { active: activeIncidentStatus === option.key }]"
          @click="activeIncidentStatus = option.key"
        >
          {{ option.label }}
          <span v-if="option.key === 'open' && openIncidentCount" class="chip-count warn">{{ openIncidentCount }}</span>
        </button>
      </div>

      <div v-if="activeIncidents.length === 0" class="empty-state">
        <div class="empty-icon">事</div>
        <div class="empty-title">暂无事故</div>
        <div class="empty-sub">相关告警会自动聚合为事故，便于统一确认和处理</div>
      </div>

      <div v-else class="message-feed">
        <article
          v-for="incident in activeIncidents"
          :key="incident.id"
          :class="[
            'message-card',
            incident.severity === 'critical' ? 'accent-critical' : incident.severity === 'warning' ? 'accent-warning' : 'accent-info',
            { 'message-card--expanded': isIncidentExpanded(incident.id), 'message-card--resolved': incident.status === 'resolved' },
          ]"
        >
          <button
            type="button"
            class="message-head"
            :aria-expanded="isIncidentExpanded(incident.id)"
            @click="toggleIncidentExpand(incident.id)"
          >
            <div class="message-head-main">
              <div class="message-title-row">
                <span class="message-title">{{ incident.title }}</span>
                <a-tag :color="incident.status === 'resolved' ? 'success' : incident.status === 'acknowledged' ? 'processing' : 'error'" class="flat-tag">
                  {{ incidentStatusText[incident.status] || incident.status }}
                </a-tag>
                <a-tag :color="incident.severity === 'critical' ? 'error' : 'warning'" class="flat-tag">
                  {{ severityText[incident.severity] || incident.severity }}
                </a-tag>
              </div>
              <div class="message-meta">
                <button type="button" class="meta-link" @click.stop="router.push(`/systems/${incident.system_id}`)">
                  {{ incident.system_name || systemNameById.get(incident.system_id) || `系统 #${incident.system_id}` }}
                </button>
                <span class="meta-time">{{ formatTime(incident.first_seen) }} – {{ formatTime(incident.last_seen) }}</span>
                <span class="meta-chip">持续 {{ incidentDuration(incident) }}</span>
                <span class="meta-chip">{{ incident.message_count }} 条关联消息</span>
              </div>
              <p v-if="!isIncidentExpanded(incident.id) && incident.failed_services?.length" class="message-preview">
                影响服务：{{ incident.failed_services.join('、') }}
              </p>
            </div>
            <span class="expand-btn" :class="{ rotated: isIncidentExpanded(incident.id) }">
              <DownOutlined />
              <span class="expand-label">{{ isIncidentExpanded(incident.id) ? '收起' : '展开' }}</span>
            </span>
          </button>

          <div v-show="isIncidentExpanded(incident.id)" class="message-body">
            <div v-if="incident.failed_services?.length" class="detail-block">
              <div class="detail-block-title">影响服务</div>
              <div class="service-tags">
                <span v-for="service in incident.failed_services" :key="service" class="meta-chip">{{ service }}</span>
              </div>
            </div>

            <div v-if="incidentDetails[incident.id]?.messages?.length" class="detail-block">
              <div class="detail-block-title">关联消息</div>
              <div class="incident-message-list">
                <div
                  v-for="msg in incidentDetails[incident.id].messages"
                  :key="msg.id"
                  class="incident-message-item"
                >
                  <div class="incident-message-head">
                    <span class="incident-message-title">{{ msg.title }}</span>
                    <a-tag :color="statusColor[msg.status] || 'default'" class="flat-tag">
                      {{ statusText[msg.status] || msg.status }}
                    </a-tag>
                  </div>
                  <p class="incident-message-summary">{{ previewText(msg) }}</p>
                  <span class="meta-time">{{ formatTime(msg.created_at) }}</span>
                </div>
              </div>
            </div>

            <div v-if="incidentWorkflows(incident).length" class="detail-block">
              <div class="detail-block-title">相关修复提案</div>
              <div class="workflow-inline-list">
                <button
                  v-for="workflow in incidentWorkflows(incident)"
                  :key="workflow.id"
                  type="button"
                  class="workflow-inline-card"
                  @click="router.push('/approvals')"
                >
                  <div class="workflow-inline-head">
                    <span class="workflow-inline-title">{{ workflowSummary(workflow) }}</span>
                    <a-tag :color="workflowStatusColor(workflow.status)" class="flat-tag">
                      {{ workflowStatusText(workflow.status) }}
                    </a-tag>
                  </div>
                  <p class="workflow-inline-text">{{ workflow.diagnosis || workflow.question }}</p>
                </button>
              </div>
            </div>
          </div>

          <div v-if="incident.status !== 'resolved' || isIncidentExpanded(incident.id)" class="message-footer">
            <a-button
              v-if="incident.status !== 'resolved' && !incidentWorkflows(incident).length"
              size="small"
              :loading="incidentActionLoading(incident, 'workflow')"
              @click.stop="createIncidentWorkflow(incident)"
            >
              生成修复提案
            </a-button>
            <a-button
              v-else-if="incident.status !== 'resolved' && incidentWorkflows(incident).length"
              size="small"
              @click.stop="router.push('/approvals')"
            >
              查看修复提案
            </a-button>
            <a-button
              v-if="incident.status !== 'acknowledged' && incident.status !== 'resolved'"
              size="small"
              :loading="incidentActionLoading(incident, 'ack')"
              @click.stop="updateIncidentStatus(incident, 'ack')"
            >
              确认事故
            </a-button>
            <a-button
              v-if="incident.status !== 'resolved'"
              size="small"
              type="primary"
              :loading="incidentActionLoading(incident, 'resolve')"
              @click.stop="updateIncidentStatus(incident, 'resolve')"
            >
              标记解决
            </a-button>
          </div>
        </article>
      </div>
    </template>

    <div v-else-if="activeMessages.length === 0" class="empty-state">
      <div class="empty-icon">消</div>
      <div class="empty-title">暂无消息</div>
      <div class="empty-sub">系统异常、诊断建议和处理结果会出现在这里</div>
    </div>

    <div v-else class="message-feed">
      <article
        v-for="item in activeMessages"
        :key="item.id"
        :class="[
          'message-card',
          severityAccent[item.severity] || 'accent-info',
          { 'message-card--expanded': isExpanded(item.id), 'message-card--resolved': item.status === 'resolved' },
        ]"
      >
        <button
          type="button"
          class="message-head"
          :aria-expanded="isExpanded(item.id)"
          @click="toggleExpand(item.id)"
        >
          <div class="message-head-main">
            <div class="message-title-row">
              <span class="message-title">{{ item.title }}</span>
              <a-tag :color="statusColor[item.status] || 'default'" class="flat-tag">
                {{ statusText[item.status] || item.status }}
              </a-tag>
              <a-tag :color="item.severity === 'critical' ? 'error' : item.severity === 'warning' ? 'warning' : 'processing'" class="flat-tag">
                {{ severityText[item.severity] || item.severity }}
              </a-tag>
            </div>

            <div class="message-meta">
              <span class="meta-chip">{{ systemNameById.get(item.system_id) || `系统 #${item.system_id}` }}</span>
              <span class="meta-time">{{ formatTime(item.created_at) }}</span>
              <span v-if="hasFailedChannels(item)" class="meta-fail">通知失败</span>
            </div>

            <p v-if="!isExpanded(item.id)" class="message-preview">{{ previewText(item) }}</p>
          </div>

          <span class="expand-btn" :class="{ rotated: isExpanded(item.id) }">
            <DownOutlined />
            <span class="expand-label">{{ isExpanded(item.id) ? '收起' : '展开' }}</span>
          </span>
        </button>

        <div v-show="isExpanded(item.id)" class="message-body">
          <p class="message-summary">{{ previewText(item) }}</p>

          <div v-if="item.diagnosis" class="detail-block">
            <div class="detail-block-title">初步判断</div>
            <p class="detail-block-text">{{ item.diagnosis }}</p>
          </div>

          <div v-if="item.suggestion?.length" class="detail-block">
            <div class="detail-block-title">建议措施</div>
            <ol class="suggestion-list">
              <li v-for="suggestion in item.suggestion" :key="suggestion">{{ suggestion }}</li>
            </ol>
          </div>

          <div v-if="item.channels?.length" class="detail-block">
            <div class="detail-block-title">通知结果</div>
            <div class="channel-grid">
              <div
                v-for="channel in item.channels"
                :key="channel.type"
                :class="['channel-item', channel.status === 'success' ? 'ok' : 'fail']"
                :title="channel.detail || ''"
              >
                <span class="channel-name">{{ channelText[channel.type] || channel.type }}</span>
                <span class="channel-status">
                  {{ channel.status === 'success' ? '成功' : '失败' }}
                  <template v-if="channel.attempts > 1"> · {{ channel.attempts }} 次</template>
                </span>
                <span v-if="channel.detail && channel.status === 'failed'" class="channel-detail">{{ channel.detail }}</span>
              </div>
            </div>
            <a-button
              v-if="hasFailedChannels(item)"
              size="small"
              class="retry-btn"
              :loading="actionId === `retry-${item.id}`"
              @click.stop="retryNotifications(item)"
            >
              重新发送失败通知
            </a-button>
          </div>

          <div v-if="item.related?.processing_mode" class="detail-block detail-block--inline">
            <span class="detail-block-title">加工状态</span>
            <a-tag color="processing" class="flat-tag">
              {{ item.related.processing_mode === 'rules' ? '规则分析' : item.related.processing_mode }}
            </a-tag>
            <span v-if="item.related?.processed_at" class="processed-time">{{ formatTime(item.related.processed_at) }}</span>
          </div>

          <div v-if="messageWorkflows(item).length" class="detail-block">
            <div class="detail-block-title">待处理修复提案</div>
            <div class="workflow-inline-list">
              <button
                v-for="workflow in messageWorkflows(item)"
                :key="workflow.id"
                type="button"
                class="workflow-inline-card"
                @click="router.push('/approvals')"
              >
                <div class="workflow-inline-head">
                  <span class="workflow-inline-title">{{ workflowSummary(workflow) }}</span>
                  <a-tag :color="workflowStatusColor(workflow.status)" class="flat-tag">
                    {{ workflowStatusText(workflow.status) }}
                  </a-tag>
                </div>
                <p class="workflow-inline-text">{{ workflow.diagnosis || workflow.question }}</p>
              </button>
            </div>
          </div>
        </div>

        <div v-if="item.status !== 'resolved' || isExpanded(item.id)" class="message-footer">
          <a-button
            v-if="item.status !== 'acknowledged' && item.status !== 'resolved'"
            size="small"
            :loading="actionId === item.id"
            @click.stop="updateMessageStatus(item, 'ack')"
          >
            确认
          </a-button>
          <a-button
            v-if="item.status !== 'resolved'"
            size="small"
            type="primary"
            :loading="actionId === item.id"
            @click.stop="updateMessageStatus(item, 'resolve')"
          >
            标记解决
          </a-button>
        </div>
      </article>
    </div>

    <div
      v-if="viewMode === 'messages' && !loading && activeStatus !== 'delivery_failed' && messageTotal > messagePageSize"
      class="pagination-row"
    >
      <a-pagination
        :current="messagePage"
        :page-size="messagePageSize"
        :total="messageTotal"
        :show-size-changer="true"
        :page-size-options="['10', '20', '50']"
        show-less-items
        @change="onMessagePageChange"
        @showSizeChange="onMessagePageChange"
      />
    </div>
  </div>
</template>

<style scoped>
.messages-page {
  max-width: 960px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 20px;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 2px;
}

.page-sub {
  font-size: 13px;
  color: var(--text-subtle);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-tag {
  border: none;
  margin: 0;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.workflow-spotlight {
  margin-bottom: 18px;
  padding: 16px;
  border-radius: 14px;
  border: 1px solid color-mix(in srgb, #d97706 24%, var(--border-color));
  background: color-mix(in srgb, #d97706 4%, var(--card-bg));
}

.workflow-spotlight__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.workflow-spotlight__title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}

.workflow-spotlight__sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-subtle);
}

.workflow-spotlight__list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.workflow-spotlight__item,
.workflow-inline-card {
  width: 100%;
  padding: 12px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--border-color) 76%, transparent);
  background: var(--card-bg);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}

.workflow-spotlight__item:hover,
.workflow-inline-card:hover {
  border-color: color-mix(in srgb, var(--primary) 34%, var(--border-color));
  box-shadow: 0 6px 18px color-mix(in srgb, var(--text) 7%, transparent);
  transform: translateY(-1px);
}

.workflow-spotlight__item-top,
.workflow-inline-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.workflow-spotlight__item-title,
.workflow-inline-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.5;
}

.workflow-spotlight__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.workflow-spotlight__diag,
.workflow-inline-text {
  margin: 8px 0 0;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
}

.workflow-inline-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.summary-card {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--card-bg);
  padding: 14px 16px;
  min-height: 88px;
}

.summary-card--warn {
  border-color: color-mix(in srgb, #d97706 28%, var(--border-color));
  background: color-mix(in srgb, #d97706 5%, var(--card-bg));
}

.summary-card--ok {
  border-color: color-mix(in srgb, var(--primary) 28%, var(--border-color));
  background: color-mix(in srgb, var(--primary) 5%, var(--card-bg));
}

.summary-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--primary) 10%, transparent);
  color: var(--primary);
  font-size: 16px;
  flex-shrink: 0;
}

.summary-body {
  min-width: 0;
}

.summary-label {
  display: block;
  color: var(--text-subtle);
  font-size: 12px;
  margin-bottom: 4px;
}

.summary-value {
  display: block;
  color: var(--text);
  font-size: 26px;
  line-height: 1;
  font-weight: 700;
}

.summary-detail {
  display: block;
  margin-top: 8px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.4;
}

.filter-row {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.view-switch {
  margin-bottom: 10px;
}

.meta-link {
  border: none;
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--primary);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
}

.incident-summary-grid {
  margin-bottom: 14px;
}

.incident-summary-grid .summary-card {
  min-height: 72px;
}

.incident-summary-grid .summary-value {
  font-size: 22px;
}

.summary-card--warn {
  border-color: color-mix(in srgb, #d97706 28%, var(--border-color));
  background: color-mix(in srgb, #d97706 5%, var(--card-bg));
}

.summary-card--ok {
  border-color: color-mix(in srgb, #557568 28%, var(--border-color));
  background: color-mix(in srgb, #557568 5%, var(--card-bg));
}

.service-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.incident-message-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.incident-message-item {
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
  background: var(--card-bg);
}

.incident-message-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.incident-message-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.incident-message-summary {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.5;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-subtle);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.filter-chip:hover,
.filter-chip.active {
  border-color: var(--primary);
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, transparent);
}

.chip-count {
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 14%, transparent);
  color: var(--primary);
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}

.chip-count.warn {
  background: color-mix(in srgb, #d97706 16%, transparent);
  color: #b45309;
}

.page-spin {
  display: block;
  margin: 80px auto;
}

.message-feed {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.message-card {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  border-radius: 14px;
  overflow: hidden;
  border-left: 4px solid transparent;
  transition: box-shadow 0.15s, border-color 0.15s;
}

.message-card:hover {
  box-shadow: 0 4px 18px color-mix(in srgb, var(--text) 6%, transparent);
}

.message-card--expanded {
  box-shadow: 0 6px 22px color-mix(in srgb, var(--text) 8%, transparent);
}

.message-card--resolved {
  opacity: 0.88;
}

.message-card.accent-critical {
  border-left-color: #dc2626;
}

.message-card.accent-warning {
  border-left-color: #d97706;
}

.message-card.accent-info {
  border-left-color: var(--primary);
}

.message-head {
  width: 100%;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 16px;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.message-head-main {
  min-width: 0;
  flex: 1;
}

.message-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.message-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.4;
}

.flat-tag {
  border: none !important;
  margin: 0 !important;
}

.message-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
}

.meta-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--text-subtle);
  font-size: 12px;
}

.meta-time {
  color: var(--text-subtle);
  font-size: 12px;
}

.meta-fail {
  color: #dc2626;
  font-size: 12px;
  font-weight: 600;
}

.message-preview {
  margin: 0;
  color: var(--text-subtle);
  font-size: 13px;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.expand-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 4px 8px;
  border-radius: 8px;
  color: var(--text-subtle);
  font-size: 12px;
  transition: background 0.15s, transform 0.2s;
}

.expand-btn.rotated {
  transform: rotate(180deg);
}

.message-head:hover .expand-btn {
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--primary);
}

.expand-label {
  transform: rotate(0deg);
}

.expand-btn.rotated .expand-label {
  transform: rotate(180deg);
}

.message-body {
  padding: 0 16px 14px;
  border-top: 1px dashed color-mix(in srgb, var(--border-color) 80%, transparent);
}

.message-summary {
  margin: 14px 0 0;
  color: var(--text);
  font-size: 13.5px;
  line-height: 1.65;
}

.detail-block {
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--body-bg, #fafafa) 70%, var(--card-bg));
  border: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
}

.detail-block--inline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px;
}

.detail-block-title {
  color: var(--text-subtle);
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 6px;
}

.detail-block--inline .detail-block-title {
  margin-bottom: 0;
}

.detail-block-text {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  line-height: 1.6;
}

.suggestion-list {
  margin: 0;
  padding-left: 18px;
  color: var(--text);
  font-size: 13px;
  line-height: 1.65;
}

.suggestion-list li + li {
  margin-top: 4px;
}

.channel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
}

.channel-item {
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
}

.channel-item.ok {
  border-color: color-mix(in srgb, var(--primary) 30%, var(--border-color));
}

.channel-item.fail {
  border-color: color-mix(in srgb, #dc2626 30%, var(--border-color));
  background: color-mix(in srgb, #dc2626 4%, var(--card-bg));
}

.channel-name {
  display: block;
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
}

.channel-status {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-subtle);
}

.channel-detail {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  color: #dc2626;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.retry-btn {
  margin-top: 10px;
}

.processed-time {
  font-size: 12px;
  color: var(--text-subtle);
}

.message-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px 14px;
  border-top: 1px solid color-mix(in srgb, var(--border-color) 60%, transparent);
  background: color-mix(in srgb, var(--body-bg, #fafafa) 40%, var(--card-bg));
}

.empty-state {
  text-align: center;
  padding: 88px 0;
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
  font-size: 13px;
  color: var(--text-subtle);
}

@media (max-width: 900px) {
  .workflow-spotlight__head {
    flex-direction: column;
    align-items: stretch;
  }
}

@media (max-width: 720px) {
  .page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .message-head {
    flex-direction: column;
  }

  .expand-btn {
    align-self: flex-end;
  }

  .message-footer {
    flex-wrap: wrap;
  }
}
</style>
