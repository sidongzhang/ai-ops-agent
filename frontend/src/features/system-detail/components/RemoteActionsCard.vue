<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
  systemLocal: { type: Boolean, default: false },
  restartCapability: { type: Object, default: null },
  services: { type: Array, default: () => [] },
})
const emit = defineEmits(['restart-service'])

const sectionOpen = ref([])

const actions = [
  { key: 'memory', label: '查看内存', command: 'INFO memory', detail: '检查内存占用、峰值和淘汰情况' },
  { key: 'clients', label: '查看连接', command: 'INFO clients', detail: '检查当前连接数和阻塞客户端' },
  { key: 'stats', label: '查看统计', command: 'INFO stats', detail: '检查命中率、拒绝连接和淘汰次数' },
  { key: 'slowlog', label: '查看慢日志', command: 'SLOWLOG GET 10', detail: '查看最近 10 条慢命令' },
]
const kafkaActions = [
  { key: 'topics', label: '查看 Topic 列表', command: 'topics --list', detail: '确认当前 Kafka 中已有的 topic' },
  { key: 'groups', label: '查看消费组积压', command: 'consumer-groups --describe --all-groups', detail: '查看消费组 offset 和 lag' },
]

const runningKey = ref('')
const resultMap = reactive({})
const workflowLoading = ref(false)
const workflowRows = ref([])

const redisServices = computed(() => props.services.filter((service) => {
  const config = service.config || {}
  const name = String(service.name || '').toLowerCase()
  return service.enabled && service.connector === 'tcp' && (name.includes('redis') || Number(config.port) === 6379)
}))
const kafkaServices = computed(() => props.services.filter((service) => {
  const config = service.config || {}
  const name = String(service.name || '').toLowerCase()
  return service.enabled && (
    name.includes('kafka') ||
    Number(config.port) === 9092 ||
    Boolean(config.container && String(config.container).toLowerCase().includes('kafka'))
  )
}))

const canOperate = computed(() => !props.systemLocal && !!props.restartCapability?.collector_online)
const restartableServices = computed(() => (props.restartCapability?.services || []).filter((item) => item.restartable))
const hasAnyActionCapabilities = computed(() => (
  restartableServices.value.length > 0 || redisServices.value.length > 0 || kafkaServices.value.length > 0
))

const summaryText = computed(() => {
  if (props.systemLocal) return '本机系统暂不支持'
  if (!props.restartCapability?.collector_online) return '采集器未在线'
  const total = redisServices.value.length + kafkaServices.value.length
  if (!total) return '暂无 Redis / Kafka 服务'
  return `${total} 个服务可操作`
})
const permissionSummary = computed(() => {
  if (props.systemLocal) return '本机场景暂不走采集器远程命令'
  if (!props.restartCapability?.collector_online) return '采集器离线，远程命令与重启都不可执行'
  if (!props.restartCapability?.enabled) return '当前只开放只读运维命令，未开放重启目标'
  if (!props.restartCapability?.has_permission) return '当前账号可以查看能力，但审批执行重启时仍会校验权限'
  return '采集器在线，且当前账号具备受控重启权限'
})

function parseRecovery(resultText) {
  const text = String(resultText || '')
  const line = text.split('\n').find((item) => item.includes('恢复回查：'))
  return line ? line.replace('恢复回查：', '').trim() : ''
}

function workflowActionLabel(item) {
  const action = item?.proposed_action || {}
  return {
    restart_container: '重启容器',
    restart_systemd: '重启服务',
    fetch_logs: '抓取日志',
    health_check: '健康检查',
    run_redis_command: 'Redis 运维命令',
    manual: '人工处理',
  }[action.type] || action.type || '修复提案'
}

function workflowStatusLabel(status) {
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

function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function loadRecentWorkflows() {
  workflowLoading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/workflow`)
    workflowRows.value = Array.isArray(data) ? data.slice(0, 5) : []
  } catch {
    workflowRows.value = []
  } finally {
    workflowLoading.value = false
  }
}

async function runRedisCommand(service, action) {
  runningKey.value = `${service.id}:${action.key}`
  try {
    const { data } = await api.post(`/systems/${props.systemId}/collector/exec`, {
      cmd: 'run_redis_command',
      args: {
        service: service.name,
        command: action.command,
      },
    })
    resultMap[service.id] = {
      command: action.command,
      text: String(data?.result || ''),
    }
    message.success(`已执行 ${service.name} · ${action.label}`)
  } catch (error) {
    message.error(error?.response?.data?.detail || '执行失败')
  } finally {
    runningKey.value = ''
  }
}

async function runKafkaCommand(service, action) {
  runningKey.value = `${service.id}:${action.key}`
  try {
    const { data } = await api.post(`/systems/${props.systemId}/collector/exec`, {
      cmd: 'run_kafka_command',
      args: {
        service: service.name,
        command: action.command,
      },
    })
    resultMap[service.id] = {
      command: action.command,
      text: String(data?.result || ''),
    }
    message.success(`已执行 ${service.name} · ${action.label}`)
  } catch (error) {
    message.error(error?.response?.data?.detail || '执行失败')
  } finally {
    runningKey.value = ''
  }
}

onMounted(loadRecentWorkflows)
</script>

<template>
  <div class="subsection">
    <a-collapse v-model:activeKey="sectionOpen" ghost expand-icon-position="start" class="subsection-collapse">
      <a-collapse-panel key="main">
        <template #header>
          <div class="card-title">
            <span>受控运维操作</span>
            <a-tag style="border:none;margin:0">{{ summaryText }}</a-tag>
          </div>
        </template>

        <div class="remote-actions-body">
          <div class="remote-actions-tip">
            {{ permissionSummary }}
          </div>

          <div v-if="restartableServices.length" class="service-action-card">
            <div class="service-action-head">
              <div>
                <div class="service-action-title">受控重启能力</div>
                <div class="service-action-sub">已注册且具备重启条件的服务会出现在这里，支持容器和 systemd 两种目标。</div>
              </div>
              <a-tag color="gold" style="border:none;margin:0">重启</a-tag>
            </div>

            <div class="restart-list">
              <div v-for="item in restartableServices" :key="item.name" class="restart-item">
                <div class="action-copy">
                  <div class="action-label">{{ item.name }}</div>
                  <div class="action-detail">
                    {{ item.target_type === 'systemd' ? `systemd：${item.systemd_unit}` : `容器：${item.container}` }}
                  </div>
                </div>
                <a-button size="small" @click="emit('restart-service', { name: item.name })">重启</a-button>
              </div>
            </div>
          </div>

          <div class="remote-actions-tip">
            这里只展示已经注册且已启用监控的 Redis / Kafka 服务。所有操作都是受控只读查询，不包含删库、删 topic 或重置 offset 这类高风险命令。
          </div>

          <div v-if="systemLocal" class="empty-copy">
            当前系统是平台本机托管，后面会补充本机运维命令入口；现在先通过远程采集器链路承接受控操作。
          </div>
          <div v-else-if="!restartCapability?.collector_online" class="empty-copy">
            远程采集器还没在线，暂时不能执行受控命令。先让对方机器上的采集器上线。
          </div>
          <div v-else-if="redisServices.length === 0 && kafkaServices.length === 0" class="empty-copy">
            当前系统还没有已启用的 Redis 或 Kafka 服务，所以这里不展示受控运维操作。
          </div>

          <div v-else class="service-action-list">
            <div v-for="service in redisServices" :key="service.id" class="service-action-card">
              <div class="service-action-head">
                <div>
                  <div class="service-action-title">{{ service.name }}</div>
                  <div class="service-action-sub">可直接查看 Redis 运行状态和慢命令</div>
                </div>
                <a-tag color="blue" style="border:none;margin:0">Redis</a-tag>
              </div>

              <div class="action-grid">
                <div v-for="action in actions" :key="action.key" class="action-item">
                  <div class="action-copy">
                    <div class="action-label">{{ action.label }}</div>
                    <div class="action-detail">{{ action.detail }}</div>
                  </div>
                  <a-button
                    size="small"
                    :disabled="!canOperate"
                    :loading="runningKey === `${service.id}:${action.key}`"
                    @click="runRedisCommand(service, action)"
                  >
                    执行
                  </a-button>
                </div>
              </div>

              <div v-if="resultMap[service.id]" class="result-box">
                <div class="result-title">{{ resultMap[service.id].command }}</div>
                <pre>{{ resultMap[service.id].text }}</pre>
              </div>
            </div>

            <div v-for="service in kafkaServices" :key="service.id" class="service-action-card">
              <div class="service-action-head">
                <div>
                  <div class="service-action-title">{{ service.name }}</div>
                  <div class="service-action-sub">可直接查看 topic、消费组和积压情况</div>
                </div>
                <a-tag color="purple" style="border:none;margin:0">Kafka</a-tag>
              </div>

              <div class="action-grid">
                <div v-for="action in kafkaActions" :key="action.key" class="action-item">
                  <div class="action-copy">
                    <div class="action-label">{{ action.label }}</div>
                    <div class="action-detail">{{ action.detail }}</div>
                  </div>
                  <a-button
                    size="small"
                    :disabled="!canOperate"
                    :loading="runningKey === `${service.id}:${action.key}`"
                    @click="runKafkaCommand(service, action)"
                  >
                    执行
                  </a-button>
                </div>
              </div>

              <div v-if="resultMap[service.id]" class="result-box">
                <div class="result-title">{{ resultMap[service.id].command }}</div>
                <pre>{{ resultMap[service.id].text }}</pre>
              </div>
            </div>
          </div>

          <div class="service-action-card">
            <div class="service-action-head">
              <div>
                <div class="service-action-title">最近修复执行与回查</div>
                <div class="service-action-sub">显示这个系统最近的审批、执行结果和恢复回查结论。</div>
              </div>
              <a-button size="small" :loading="workflowLoading" @click="loadRecentWorkflows">刷新</a-button>
            </div>

            <div v-if="workflowRows.length" class="workflow-history-list">
              <div v-for="item in workflowRows" :key="item.id" class="workflow-history-item">
                <div class="workflow-history-top">
                  <div class="workflow-history-title">
                    {{ workflowActionLabel(item) }}
                    <span v-if="item.target_service">· {{ item.target_service }}</span>
                  </div>
                  <a-tag :color="workflowStatusColor(item.status)" style="border:none;margin:0">
                    {{ workflowStatusLabel(item.status) }}
                  </a-tag>
                </div>
                <div class="workflow-history-meta">
                  <span>{{ formatTime(item.created_at) || '刚刚' }}</span>
                  <span v-if="item.target_resource">目标：{{ item.target_resource }}</span>
                </div>
                <div class="workflow-history-text">{{ item.diagnosis || item.question }}</div>
                <div v-if="item.execution_result" class="workflow-history-result">
                  <div class="workflow-history-result-title">执行结果</div>
                  <div class="workflow-history-result-text">{{ item.execution_result }}</div>
                </div>
                <div v-if="parseRecovery(item.execution_result)" class="workflow-history-recovery">
                  <span class="recovery-label">回查结论</span>
                  <span>{{ parseRecovery(item.execution_result) }}</span>
                </div>
              </div>
            </div>
            <div v-else class="empty-copy">
              这个系统还没有形成审批修复记录。后续从 AI 诊断或数据分析发起修复提案后，这里会显示执行和回查结果。
            </div>
          </div>
        </div>
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>

<style scoped>
.subsection {
  border-top: 1px solid var(--border-color);
  padding-top: 4px;
}
.subsection-collapse :deep(.ant-collapse-header) {
  align-items: center !important;
  padding: 10px 0 !important;
}
.subsection-collapse :deep(.ant-collapse-content-box) {
  padding: 0 0 8px !important;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.remote-actions-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.remote-actions-tip,
.empty-copy {
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--body-bg);
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
}
.service-action-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.service-action-card {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--card-bg);
  padding: 12px;
}
.service-action-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.service-action-title {
  color: var(--text);
  font-size: 14px;
  font-weight: 700;
}
.service-action-sub {
  margin-top: 4px;
  color: var(--text-subtle);
  font-size: 12px;
}
.action-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.action-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 10px;
  background: var(--body-bg);
}
.restart-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.restart-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 10px;
  background: var(--body-bg);
}
.action-copy {
  min-width: 0;
}
.action-label {
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}
.action-detail {
  margin-top: 3px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.5;
}
.result-box {
  margin-top: 10px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: color-mix(in srgb, var(--primary-bg) 55%, var(--card-bg));
  color: var(--text);
  overflow: hidden;
}
.result-title {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color);
  font-size: 12px;
  color: var(--text-subtle);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.result-box pre {
  margin: 0;
  padding: 10px;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  font: 11px/1.6 'SF Mono', Menlo, Consolas, monospace;
}
.workflow-history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.workflow-history-item {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--body-bg);
  padding: 12px;
}
.workflow-history-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}
.workflow-history-title {
  color: var(--text);
  font-size: 13px;
  font-weight: 700;
}
.workflow-history-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-subtle);
}
.workflow-history-text {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-subtle);
}
.workflow-history-result {
  margin-top: 10px;
  padding: 10px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
}
.workflow-history-result-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-subtle);
  margin-bottom: 6px;
}
.workflow-history-result-text {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
.workflow-history-recovery {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  color: var(--text);
  font-size: 12px;
}
.recovery-label {
  font-weight: 700;
  color: var(--primary);
}

@media (max-width: 720px) {
  .action-grid {
    grid-template-columns: 1fr;
  }
  .action-item,
  .restart-item {
    align-items: flex-start;
    flex-direction: column;
  }
  .workflow-history-top {
    flex-direction: column;
  }
}
</style>
