<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import api from '../api'

const router = useRouter()
const loading = ref(false)
const actionKey = ref(null)
const activeTab = ref('pending')
const pending = ref([])
const processed = ref([])
const pendingPage = ref(1)
const processedPage = ref(1)
const pageSize = ref(20)
const pendingTotal = ref(0)
const processedTotal = ref(0)

const ACTION_LABELS = {
  fetch_logs: '拉取日志',
  health_check: '健康检查',
  restart_container: '重启容器',
  restart_systemd: '重启 systemd',
  run_redis_command: 'Redis 命令',
  manual: '人工操作',
}

const STATUS_LABELS = {
  pending: '待审批',
  approved: '执行中',
  done: '已执行',
  rejected: '已拒绝',
  error: '执行失败',
}

const STATUS_COLOR = {
  pending: 'warning',
  approved: 'processing',
  done: 'success',
  rejected: 'default',
  error: 'error',
}

const RISK_LABELS = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  manual: '人工确认',
}

const RISK_COLOR = {
  low: 'success',
  medium: 'warning',
  high: 'error',
  manual: 'default',
}

const visibleItems = computed(() => (activeTab.value === 'pending' ? pending.value : processed.value))

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function actionSummary(item) {
  const action = item.proposed_action || {}
  const parts = [
    action.display_name || ACTION_LABELS[action.type] || action.type || '修复提案',
    item.target_service,
    item.target_resource,
    item.execution_mode ? `模式：${item.execution_mode}` : '',
  ].filter(Boolean)
  return parts.join(' · ')
}

function actionRisk(item) {
  return item.proposed_action?.risk || item.proposed_action?.catalog?.risk || ''
}

function actionVerification(item) {
  return item.proposed_action?.verification || item.proposed_action?.catalog?.verification || ''
}

async function load() {
  loading.value = true
  try {
    const [{ data: pendingData }, { data: processedData }] = await Promise.all([
      api.get('/workflows/pending', {
        params: {
          limit: pageSize.value,
          offset: (pendingPage.value - 1) * pageSize.value,
        },
      }),
      api.get('/workflows', {
        params: {
          status: 'processed',
          limit: pageSize.value,
          offset: (processedPage.value - 1) * pageSize.value,
        },
      }),
    ])
    pending.value = pendingData.items || pendingData
    processed.value = processedData.items || processedData
    pendingTotal.value = pendingData.total ?? pending.value.length
    processedTotal.value = processedData.total ?? processed.value.length
  } catch (error) {
    message.error(error?.response?.data?.detail || '审批列表加载失败')
  } finally {
    loading.value = false
  }
}

function onPendingPageChange(page, size) {
  pendingPage.value = page
  pageSize.value = size
  load()
}

function onProcessedPageChange(page, size) {
  processedPage.value = page
  pageSize.value = size
  load()
}

async function decide(item, approved) {
  actionKey.value = `${item.id}-${approved ? 'approve' : 'reject'}`
  try {
    const { data } = await api.post(`/systems/${item.system_id}/workflow/${item.id}/decision`, { approved })
    message.success(approved ? '已批准并执行' : '已拒绝该提案')
    if (approved) {
      pending.value = pending.value.filter((current) => current.id !== item.id)
      processed.value = [data, ...processed.value.filter((current) => current.id !== data.id)]
    } else {
      pending.value = pending.value.filter((current) => current.id !== item.id)
      processed.value = [data, ...processed.value.filter((current) => current.id !== data.id)]
    }
    window.dispatchEvent(new Event('aiops:approvals-changed'))
  } catch (error) {
    message.error(error?.response?.data?.detail || '操作失败')
  } finally {
    actionKey.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="approvals-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">审批中心</h2>
        <p class="page-sub">集中处理 AI 修复提案，查看历史审批与执行结果</p>
      </div>
      <div class="header-actions">
        <a-tag v-if="pending.length" color="warning" class="header-tag">{{ pending.length }} 条待处理</a-tag>
        <a-button :loading="loading" @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </div>
    </div>

    <div class="tab-row">
      <button :class="['tab-chip', { active: activeTab === 'pending' }]" @click="activeTab = 'pending'">
        待审批
        <span v-if="pending.length" class="chip-count">{{ pending.length }}</span>
      </button>
      <button :class="['tab-chip', { active: activeTab === 'processed' }]" @click="activeTab = 'processed'">
        已处理
        <span v-if="processed.length" class="chip-count muted">{{ processed.length }}</span>
      </button>
    </div>

    <a-spin v-if="loading" class="page-spin" />

    <div v-else-if="visibleItems.length === 0" class="empty-state">
      <div class="empty-icon">{{ activeTab === 'pending' ? '批' : '历' }}</div>
      <div class="empty-title">{{ activeTab === 'pending' ? '暂无待审批提案' : '暂无已处理记录' }}</div>
      <div class="empty-sub">
        {{ activeTab === 'pending'
          ? '在系统详情页发起「请求修复」后，提案会出现在这里'
          : '批准、拒绝或执行完成的提案会保留在这里' }}
      </div>
    </div>

    <div v-else class="approval-feed">
      <article v-for="item in visibleItems" :key="item.id" class="approval-card">
        <div class="approval-head">
          <div class="approval-title-row">
            <span class="approval-action">{{ actionSummary(item) }}</span>
            <a-tag :color="STATUS_COLOR[item.status] || 'default'" class="flat-tag">
              {{ STATUS_LABELS[item.status] || item.status }}
            </a-tag>
          </div>
          <div class="approval-meta">
            <button type="button" class="meta-link" @click="router.push(`/systems/${item.system_id}`)">
              {{ item.system_name || `系统 #${item.system_id}` }}
            </button>
            <span class="meta-time">{{ formatTime(item.created_at) }}</span>
            <span v-if="item.approved_at" class="meta-chip">审批于 {{ formatTime(item.approved_at) }}</span>
          </div>
        </div>

        <div v-if="item.question" class="question-block">
          <div class="detail-label">触发问题</div>
          <p>{{ item.question }}</p>
        </div>

        <p class="approval-diagnosis">{{ item.diagnosis }}</p>

        <div class="action-info">
          <a-tag v-if="actionRisk(item)" :color="RISK_COLOR[actionRisk(item)] || 'default'" class="flat-tag">
            {{ RISK_LABELS[actionRisk(item)] || actionRisk(item) }}
          </a-tag>
          <span v-if="actionVerification(item)" class="verification-text">
            恢复确认：{{ actionVerification(item) }}
          </span>
        </div>

        <div v-if="item.execution_result && activeTab === 'processed'" class="result-block">
          <div class="detail-label">执行结果</div>
          <pre>{{ item.execution_result }}</pre>
        </div>

        <div v-if="activeTab === 'pending'" class="approval-footer">
          <a-button
            size="small"
            danger
            :loading="actionKey === `${item.id}-reject`"
            @click="decide(item, false)"
          >
            <template #icon><CloseOutlined /></template>
            拒绝
          </a-button>
          <a-button
            size="small"
            type="primary"
            :loading="actionKey === `${item.id}-approve`"
            @click="decide(item, true)"
          >
            <template #icon><CheckOutlined /></template>
            批准执行
          </a-button>
        </div>
      </article>
    </div>

    <div v-if="!loading && activeTab === 'pending' && pendingTotal > pageSize" class="pagination-row">
      <a-pagination
        :current="pendingPage"
        :page-size="pageSize"
        :total="pendingTotal"
        show-less-items
        @change="onPendingPageChange"
      />
    </div>
    <div v-if="!loading && activeTab === 'processed' && processedTotal > pageSize" class="pagination-row">
      <a-pagination
        :current="processedPage"
        :page-size="pageSize"
        :total="processedTotal"
        show-less-items
        @change="onProcessedPageChange"
      />
    </div>
  </div>
</template>

<style scoped>
.approvals-page { max-width: 960px; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 16px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 20px; }
.page-title { font-size: 22px; font-weight: 700; color: var(--text); margin-bottom: 2px; }
.page-sub { font-size: 13px; color: var(--text-subtle); }
.header-actions { display: flex; align-items: center; gap: 10px; }
.header-tag { border: none; margin: 0; }
.tab-row { display: flex; gap: 8px; margin-bottom: 16px; }
.tab-chip {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid var(--border-color); background: var(--card-bg);
  color: var(--text-subtle); border-radius: 999px; padding: 6px 14px;
  font-size: 13px; cursor: pointer;
}
.tab-chip.active { border-color: var(--primary); color: var(--primary); background: color-mix(in srgb, var(--primary) 8%, transparent); }
.chip-count {
  min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 14%, transparent);
  color: var(--primary); font-size: 11px; font-weight: 700; line-height: 18px; text-align: center;
}
.chip-count.muted { background: color-mix(in srgb, var(--border-color) 40%, transparent); color: var(--text-subtle); }
.page-spin { display: block; margin: 80px auto; }
.approval-feed { display: flex; flex-direction: column; gap: 12px; }
.approval-card {
  border: 1px solid var(--border-color); background: var(--card-bg);
  border-radius: 14px; overflow: hidden; border-left: 4px solid #d97706;
}
.approval-head { padding: 14px 16px 0; }
.approval-title-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.approval-action { font-size: 15px; font-weight: 700; color: var(--text); }
.flat-tag { border: none !important; margin: 0 !important; }
.approval-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.meta-link {
  border: none; background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--primary); border-radius: 999px; padding: 2px 10px; font-size: 12px; cursor: pointer;
}
.meta-time, .meta-chip { color: var(--text-subtle); font-size: 12px; }
.question-block, .result-block {
  margin: 0 16px 12px; padding: 10px 12px; border-radius: 10px;
  background: color-mix(in srgb, var(--body-bg, #fafafa) 70%, var(--card-bg));
  border: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
}
.detail-label { color: var(--text-subtle); font-size: 12px; font-weight: 700; margin-bottom: 6px; }
.question-block p { margin: 0; font-size: 13px; line-height: 1.6; color: var(--text); }
.approval-diagnosis {
  margin: 0; padding: 0 16px 12px; color: var(--text); font-size: 13.5px; line-height: 1.65;
}
.action-info {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin: -2px 16px 12px;
  color: var(--text-subtle);
  font-size: 12px;
}
.verification-text { line-height: 22px; }
.result-block pre {
  margin: 0; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; color: var(--text);
}
.approval-footer {
  display: flex; justify-content: flex-end; gap: 8px; padding: 10px 16px 14px;
  border-top: 1px solid color-mix(in srgb, var(--border-color) 60%, transparent);
  background: color-mix(in srgb, var(--body-bg, #fafafa) 40%, var(--card-bg));
}
.empty-state { text-align: center; padding: 88px 0; }
.empty-icon {
  width: 56px; height: 56px; margin: 0 auto 14px; border-radius: 14px;
  border: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--card-bg) 86%, var(--primary-bg));
  color: var(--text-subtle); font-size: 24px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.empty-title { font-size: 18px; font-weight: 700; color: var(--text); margin-bottom: 6px; }
.empty-sub { font-size: 13px; color: var(--text-subtle); }
@media (max-width: 720px) {
  .page-header { flex-direction: column; align-items: stretch; }
  .approval-footer { flex-wrap: wrap; }
}
</style>
