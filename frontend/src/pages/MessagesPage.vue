<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../api'

const loading = ref(false)
const actionId = ref(null)
const activeStatus = ref('all')
const messages = ref([])
const systems = ref([])

const statusOptions = [
  { key: 'all', label: '全部' },
  { key: 'unread', label: '未读' },
  { key: 'acknowledged', label: '已确认' },
  { key: 'resolved', label: '已解决' },
  { key: 'delivery_failed', label: '通知失败' },
]

const statusText = {
  unread: '未读',
  read: '已读',
  acknowledged: '已确认',
  resolved: '已解决',
}

const statusColor = {
  unread: 'error',
  read: 'default',
  acknowledged: 'warning',
  resolved: 'success',
}

const severityColor = {
  info: 'processing',
  warning: 'warning',
  critical: 'error',
}

const systemNameById = computed(() => {
  const map = new Map()
  for (const system of systems.value) map.set(system.id, system.name)
  return map
})

const unreadCount = computed(() => messages.value.filter((item) => item.status === 'unread').length)
const failedDeliveryCount = computed(() => messages.value.filter(hasFailedChannels).length)
const activeMessages = computed(() => {
  if (activeStatus.value === 'all') return messages.value
  if (activeStatus.value === 'delivery_failed') return messages.value.filter(hasFailedChannels)
  return messages.value.filter((item) => item.status === activeStatus.value)
})

const channelText = {
  web: '网页',
  feishu: '飞书',
  email: '邮件',
  webhook: 'Webhook',
}

function hasFailedChannels(item) {
  return item.channels?.some((channel) => channel.type !== 'web' && channel.status === 'failed')
}

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

async function loadMessages() {
  loading.value = true
  try {
    const [{ data: messageData }, { data: systemData }] = await Promise.all([
      api.get('/messages', { params: { limit: 100 } }),
      api.get('/systems'),
    ])
    messages.value = messageData
    systems.value = systemData
  } catch (error) {
    message.error(error?.response?.data?.detail || '消息加载失败')
  } finally {
    loading.value = false
  }
}

async function updateMessageStatus(item, action) {
  actionId.value = item.id
  try {
    const { data } = await api.post(`/messages/${item.id}/${action}`)
    messages.value = messages.value.map((current) => current.id === item.id ? data : current)
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
    messages.value = messages.value.map((current) => current.id === item.id ? data : current)
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
  <div>
    <div class="page-header">
      <div>
        <h2 class="page-title">消息中心</h2>
        <p class="page-sub">集中查看告警、诊断建议和处理状态</p>
      </div>
      <div class="header-actions">
        <a-tag v-if="unreadCount" color="error" class="unread-tag">{{ unreadCount }} 条未读</a-tag>
        <a-tag v-if="failedDeliveryCount" color="warning" class="unread-tag">
          {{ failedDeliveryCount }} 条通知失败
        </a-tag>
        <a-button :loading="loading" @click="loadMessages">刷新</a-button>
      </div>
    </div>

    <div class="filter-row">
      <button
        v-for="option in statusOptions"
        :key="option.key"
        :class="['filter-chip', { active: activeStatus === option.key }]"
        @click="activeStatus = option.key"
      >
        {{ option.label }}
      </button>
    </div>

    <a-spin v-if="loading" style="display:block;margin-top:80px;text-align:center" />

    <div v-else-if="activeMessages.length === 0" class="empty-state">
      <div class="empty-icon">✓</div>
      <div class="empty-title">暂无消息</div>
      <div class="empty-sub">系统异常、诊断建议和处理结果会出现在这里</div>
    </div>

    <div v-else class="message-list">
      <article v-for="item in activeMessages" :key="item.id" class="message-card">
        <div class="message-main">
          <div class="message-title-row">
            <span class="message-title">{{ item.title }}</span>
            <a-tag :color="statusColor[item.status] || 'default'" style="border:none;margin:0">
              {{ statusText[item.status] || item.status }}
            </a-tag>
          </div>

          <div class="message-meta">
            <span>{{ systemNameById.get(item.system_id) || `系统 #${item.system_id}` }}</span>
            <span>{{ formatTime(item.created_at) }}</span>
            <a-tag :color="severityColor[item.severity] || 'default'" style="border:none;margin:0">
              {{ item.severity }}
            </a-tag>
          </div>

          <p class="message-summary">{{ item.summary || item.content }}</p>

          <div v-if="item.diagnosis" class="message-block">
            <span class="block-label">初步判断</span>
            <span>{{ item.diagnosis }}</span>
          </div>

          <div v-if="item.suggestion?.length" class="suggestions">
            <span class="block-label">建议措施</span>
            <ol>
              <li v-for="suggestion in item.suggestion" :key="suggestion">{{ suggestion }}</li>
            </ol>
          </div>

          <div v-if="item.channels?.length" class="channel-row">
            <span class="block-label">通知结果</span>
            <a-tag
              v-for="channel in item.channels"
              :key="channel.type"
              :color="channel.status === 'success' ? 'success' : 'error'"
              :title="channel.detail || ''"
              style="border:none;margin:0"
            >
              {{ channelText[channel.type] || channel.type }} ·
              {{ channel.status === 'success' ? '成功' : '失败' }}
              <span v-if="channel.attempts > 1">（已尝试 {{ channel.attempts }} 次）</span>
            </a-tag>
            <a-button
              v-if="hasFailedChannels(item)"
              size="small"
              :loading="actionId === `retry-${item.id}`"
              @click="retryNotifications(item)"
            >重新发送</a-button>
            <span
              v-if="hasFailedChannels(item)"
              class="channel-error"
              :title="item.channels.filter((channel) => channel.status === 'failed').map((channel) => channel.detail).join('；')"
            >
              {{ item.channels.find((channel) => channel.status === 'failed')?.detail || '请检查通知配置' }}
            </span>
          </div>

          <div v-if="item.related?.processing_mode" class="channel-row">
            <span class="block-label">加工状态</span>
            <a-tag color="processing" style="border:none;margin:0">
              {{ item.related.processing_mode === 'rules' ? '规则分析' : item.related.processing_mode }}
            </a-tag>
            <span v-if="item.related?.processed_at" class="processed-time">
              {{ formatTime(item.related.processed_at) }}
            </span>
          </div>
        </div>

        <div class="message-actions">
          <a-button
            v-if="item.status !== 'acknowledged' && item.status !== 'resolved'"
            size="small"
            :loading="actionId === item.id"
            @click="updateMessageStatus(item, 'ack')"
          >
            确认
          </a-button>
          <a-button
            v-if="item.status !== 'resolved'"
            size="small"
            type="primary"
            :loading="actionId === item.id"
            @click="updateMessageStatus(item, 'resolve')"
          >
            解决
          </a-button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
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
.unread-tag {
  border: none;
  margin: 0;
}
.filter-row {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.filter-chip {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-subtle);
  border-radius: 999px;
  padding: 5px 13px;
  font-size: 13px;
  cursor: pointer;
  transition: border-color .15s, color .15s, background .15s;
}
.filter-chip:hover,
.filter-chip.active {
  border-color: var(--primary);
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, transparent);
}
.message-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.message-card {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 18px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  border-radius: 10px;
}
.message-main {
  min-width: 0;
  flex: 1;
}
.message-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.message-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}
.message-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  color: var(--text-subtle);
  font-size: 12px;
  margin-bottom: 10px;
}
.message-summary {
  color: var(--text);
  font-size: 13.5px;
  line-height: 1.65;
  margin-bottom: 10px;
}
.message-block,
.suggestions {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--text);
  font-size: 13px;
  line-height: 1.6;
}
.suggestions {
  margin-top: 8px;
}
.channel-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.processed-time {
  font-size: 12px;
  color: var(--text-subtle);
}
.channel-error {
  max-width: 420px;
  color: var(--danger, #dc2626);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.suggestions ol {
  padding-left: 20px;
  margin: 0;
}
.block-label {
  color: var(--text-subtle);
  font-size: 12px;
  font-weight: 700;
}
.message-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}
.empty-state {
  text-align: center;
  padding: 90px 0;
}
.empty-icon {
  font-size: 44px;
  opacity: .18;
  margin-bottom: 14px;
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

@media (max-width: 720px) {
  .page-header,
  .message-card {
    align-items: stretch;
    flex-direction: column;
  }
  .header-actions,
  .message-actions {
    flex-direction: row;
  }
}
</style>
