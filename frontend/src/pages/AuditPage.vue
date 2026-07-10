<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../api'
import AuditLogList from '../components/AuditLogList.vue'

const loading = ref(false)
const logs = ref([])
const systems = ref([])
const selectedSystemId = ref()
const selectedEventType = ref()

const eventOptions = [
  { value: 'openapi.alert.created', label: '接入告警' },
  { value: 'openapi.health.pushed', label: '健康上报' },
  { value: 'openapi.message.created', label: '接入消息' },
  { value: 'openapi.report.created', label: '接入报告' },
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

async function loadAudit() {
  loading.value = true
  try {
    const params = { limit: 150 }
    if (selectedSystemId.value) params.system_id = selectedSystemId.value
    if (selectedEventType.value) params.event_type = selectedEventType.value
    const [{ data: logData }, { data: systemData }] = await Promise.all([
      api.get('/audit', { params }),
      systems.value.length ? Promise.resolve({ data: systems.value }) : api.get('/systems'),
    ])
    logs.value = logData
    systems.value = systemData
  } catch (error) {
    message.error(error?.response?.data?.detail || '审计记录加载失败')
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  selectedSystemId.value = undefined
  selectedEventType.value = undefined
  loadAudit()
}

onMounted(loadAudit)
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2 class="page-title">审计记录</h2>
        <p class="page-sub">追踪系统接入、告警处理、消息分析和人工操作结果</p>
      </div>
      <a-button :loading="loading" @click="loadAudit">刷新</a-button>
    </div>

    <div class="filter-bar">
      <a-select
        v-model:value="selectedSystemId"
        class="filter-select"
        placeholder="全部系统"
        allow-clear
        :options="systemOptions"
        @change="loadAudit"
      />
      <a-select
        v-model:value="selectedEventType"
        class="filter-select"
        placeholder="全部事件"
        allow-clear
        :options="eventOptions"
        @change="loadAudit"
      />
      <a-button @click="resetFilters">清空筛选</a-button>
    </div>

    <AuditLogList :logs="logs" :systems="systems" :loading="loading" />
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 18px;
}
.page-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 2px;
}
.page-sub {
  color: var(--text-subtle);
  font-size: 13px;
}
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}
.filter-select {
  width: 220px;
}

@media (max-width: 720px) {
  .page-header {
    align-items: stretch;
    flex-direction: column;
  }
  .filter-select {
    width: 100%;
  }
  .filter-bar :deep(.ant-btn) {
    width: 100%;
  }
}
</style>
