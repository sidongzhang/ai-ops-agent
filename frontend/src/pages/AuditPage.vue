<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  AuditOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  ReloadOutlined,
} from '@ant-design/icons-vue'
import api from '../api'
import AuditLogList from '../components/AuditLogList.vue'

const loading = ref(false)
const logs = ref([])
const systems = ref([])
const selectedSystemId = ref()
const selectedEventType = ref()
const auditPage = ref(1)
const auditPageSize = ref(30)
const auditTotal = ref(0)

const eventOptions = [
  { value: 'openapi.alert.created', label: '接入告警' },
  { value: 'openapi.health.pushed', label: '健康上报' },
  { value: 'openapi.message.created', label: '接入消息' },
  { value: 'openapi.report.created', label: '接入报告' },
  { value: 'openapi.log_analysis.completed', label: '日志分析完成' },
  { value: 'openapi.log_analysis.failed', label: '日志分析失败' },
  { value: 'message.processed', label: '消息分析' },
  { value: 'message.acknowledged', label: '消息确认' },
  { value: 'message.resolved', label: '消息解决' },
  { value: 'diagnosis.completed', label: '诊断完成' },
  { value: 'diagnosis.failed', label: '诊断失败' },
  { value: 'data_analysis.queried', label: '只读数据分析' },
  { value: 'workflow.created', label: '修复方案创建' },
  { value: 'workflow.approved', label: '修复方案批准' },
  { value: 'workflow.rejected', label: '修复方案拒绝' },
  { value: 'workflow.executed', label: '修复操作执行' },
  { value: 'workflow.execution_failed', label: '修复操作失败' },
  { value: 'notification.retried', label: '通知重试' },
  { value: 'notification.delivered', label: '告警通知发送' },
  { value: 'service.probe_tested', label: '服务配置测试' },
  { value: 'service.enabled', label: '服务监控启用' },
  { value: 'service.deleted', label: '服务删除' },
  { value: 'task_stuck.analyzed', label: '任务卡住分析' },
]

const systemOptions = computed(() => systems.value.map((system) => ({
  value: system.id,
  label: system.name,
})))
const successCount = computed(() => logs.value.filter((log) => ['success', 'ok', 'done'].includes(log.status)).length)
const failedCount = computed(() => logs.value.filter((log) => ['failed', 'error'].includes(log.status)).length)

const summaryCards = computed(() => [
  {
    key: 'total',
    label: '记录总数',
    value: auditTotal.value,
    detail: '当前筛选条件下的审计记录',
    tone: 'neutral',
    icon: AuditOutlined,
  },
  {
    key: 'systems',
    label: '涉及系统',
    value: systems.value.length,
    detail: '组织下已登记的系统',
    tone: 'neutral',
    icon: DatabaseOutlined,
  },
  {
    key: 'success',
    label: '成功事件',
    value: successCount.value,
    detail: '执行成功或正常上报',
    tone: 'ok',
    icon: CheckCircleOutlined,
  },
  {
    key: 'failed',
    label: '失败事件',
    value: failedCount.value,
    detail: '执行失败或发送失败',
    tone: failedCount.value ? 'warn' : 'neutral',
    icon: CloseCircleOutlined,
  },
])

async function loadAudit() {
  loading.value = true
  try {
    const params = {
      limit: auditPageSize.value,
      offset: (auditPage.value - 1) * auditPageSize.value,
    }
    if (selectedSystemId.value) params.system_id = selectedSystemId.value
    if (selectedEventType.value) params.event_type = selectedEventType.value
    const [{ data: logData }, { data: systemData }] = await Promise.all([
      api.get('/audit', { params }),
      systems.value.length ? Promise.resolve({ data: systems.value }) : api.get('/systems'),
    ])
    logs.value = logData.items || logData
    auditTotal.value = logData.total ?? logs.value.length
    systems.value = systemData
  } catch (error) {
    message.error(error?.response?.data?.detail || '审计记录加载失败')
  } finally {
    loading.value = false
  }
}

function onAuditPageChange(page, pageSize) {
  auditPage.value = page
  auditPageSize.value = pageSize
  loadAudit()
}

function resetFilters() {
  selectedSystemId.value = undefined
  selectedEventType.value = undefined
  auditPage.value = 1
  loadAudit()
}

onMounted(loadAudit)
</script>

<template>
  <div class="audit-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">审计记录</h2>
        <p class="page-sub">追踪系统接入、告警处理、消息分析和人工操作结果</p>
      </div>
      <a-button :loading="loading" @click="loadAudit">
        <template #icon><ReloadOutlined /></template>
        刷新
      </a-button>
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

    <div class="filter-bar">
      <a-select
        v-model:value="selectedSystemId"
        class="filter-select"
        placeholder="全部系统"
        allow-clear
        :options="systemOptions"
        @change="() => { auditPage = 1; loadAudit() }"
      />
      <a-select
        v-model:value="selectedEventType"
        class="filter-select"
        placeholder="全部事件"
        allow-clear
        :options="eventOptions"
        @change="() => { auditPage = 1; loadAudit() }"
      />
      <a-button @click="resetFilters">清空筛选</a-button>
    </div>

    <AuditLogList :logs="logs" :systems="systems" :loading="loading" />

    <div v-if="auditTotal > auditPageSize" class="pagination-row">
      <a-pagination
        :current="auditPage"
        :page-size="auditPageSize"
        :total="auditTotal"
        :show-size-changer="true"
        :page-size-options="['20', '30', '50']"
        show-less-items
        @change="onAuditPageChange"
        @showSizeChange="onAuditPageChange"
      />
    </div>
  </div>
</template>

<style scoped>
.audit-page {
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
  margin-bottom: 18px;
}

.page-title {
  margin: 0 0 2px;
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
}

.page-sub {
  margin: 0;
  color: var(--text-subtle);
  font-size: 13px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
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

.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--card-bg);
}

.filter-select {
  width: 220px;
}

@media (max-width: 900px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
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

  .filter-select {
    width: 100%;
  }

  .filter-bar :deep(.ant-btn) {
    width: 100%;
  }
}
</style>
