<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Modal, message } from 'ant-design-vue'
import api from '../../../api'
const AddServiceModal = defineAsyncComponent(() => import('../components').then((module) => module.AddServiceModal))
const DiagnosticTemplatesCard = defineAsyncComponent(() => import('../components').then((module) => module.DiagnosticTemplatesCard))
const MetricsTab = defineAsyncComponent(() => import('../components').then((module) => module.MetricsTab))
const NotifyConfigCard = defineAsyncComponent(() => import('../components').then((module) => module.NotifyConfigCard))
const ServicesConfigCard = defineAsyncComponent(() => import('../components').then((module) => module.ServicesConfigCard))
const SystemTokensCard = defineAsyncComponent(() => import('../components').then((module) => module.SystemTokensCard))
const CollectorDownloadCard = defineAsyncComponent(() => import('../components').then((module) => module.CollectorDownloadCard))
const MonitoringConfigCard = defineAsyncComponent(() => import('../components').then((module) => module.MonitoringConfigCard))
const RemoteActionsCard = defineAsyncComponent(() => import('../components').then((module) => module.RemoteActionsCard))
const RestartPolicyCard = defineAsyncComponent(() => import('../components').then((module) => module.RestartPolicyCard))
const ServiceLogsCard = defineAsyncComponent(() => import('../components').then((module) => module.ServiceLogsCard))
const KnowledgeBaseCard = defineAsyncComponent(() => import('../components').then((module) => module.KnowledgeBaseCard))
const AuditLogList = defineAsyncComponent(() => import('../../../components/AuditLogList.vue'))
const DiagnosisPanel = defineAsyncComponent(() => import('../../diagnostics').then((module) => module.DiagnosisPanel))
import { useMetricsPolling } from '../../monitoring'
import { useNotifyConfig } from '../../notifications'
import { SERVICE_COLORS } from '../../services'
import { useOnboarding } from '../../../composables/useOnboarding'

const addSvcOpen = ref(false)
const diagnosisDraftQuestion = ref('')
const serviceActionLoading = ref({
  enable: null,
  restart: null,
})

function openAddSvc() {
  addSvcOpen.value = true
}

function openDiagnoseWithQuestion(question) {
  diagnosisDraftQuestion.value = question || ''
  activeTab.value = 'diagnose'
}

async function deleteService(svcId, svcName) {
  try {
    await api.delete(`/systems/${props.id}/services/${svcId}`)
    message.success(`已删除「${svcName}」`)
    await loadSystem()
  } catch (e) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

async function testService(service) {
  try {
    const { data } = await api.post(`/systems/${props.id}/services/${service.id}/test`)
    message[data.ok ? 'success' : 'error'](data.ok ? `「${service.name}」测试通过` : `测试未通过：${data.detail}`)
    await loadSystem()
  } catch (error) {
    message.error(error?.response?.data?.detail || '服务测试失败')
  }
}

async function enableService(service) {
  serviceActionLoading.value.enable = service.id
  try {
    await api.post(`/systems/${props.id}/services/${service.id}/enable`)
    message.success(`服务「${service.name}」已启用监控`)
    await loadSystem()
  } catch (error) {
    message.error(error?.response?.data?.detail || '启用服务失败')
  } finally {
    serviceActionLoading.value.enable = null
  }
}

async function enableUnavailableService(service) {
  serviceActionLoading.value.enable = service.id
  try {
    await api.post(`/systems/${props.id}/services/${service.id}/enable-monitoring-unavailable`)
    message.warning(`已将「${service.name}」纳入监控；当前测试失败状态会持续告警，待服务恢复后重新测试。`)
    await loadSystem()
  } catch (error) {
    message.error(error?.response?.data?.detail || '纳入监控失败')
  } finally {
    serviceActionLoading.value.enable = null
  }
}

async function restartService(service) {
  const target = system.value?.restart_capability?.services?.find((item) => item.name === service.name)
  const targetText = target?.container || target?.systemd_unit || service.name
  Modal.confirm({
    title: `确认重启「${service.name}」？`,
    content: `平台将对 ${targetText} 执行受控重启。这个操作会短暂中断该服务。`,
    okText: '确认重启',
    cancelText: '取消',
    okButtonProps: { danger: true },
    async onOk() {
      serviceActionLoading.value.restart = service.id
      try {
        const { data } = await api.post(`/systems/${props.id}/services/restart`, {
          service: service.name,
        })
        message.success(data?.detail || `服务「${service.name}」已执行重启`)
        await Promise.all([loadSystem(), runHealth(), loadSystemAudit()])
      } catch (error) {
        message.error(error?.response?.data?.detail || '重启失败')
        throw error
      } finally {
        serviceActionLoading.value.restart = null
      }
    },
  })
}

const props = defineProps({ id: { type: String, required: true } })
const router = useRouter()
const route = useRoute()

const activeTab = ref('overview')
const servicesSectionRef = ref(null)
const collectorSectionRef = ref(null)
const monitoringSectionRef = ref(null)
const remoteActionsSectionRef = ref(null)

const system = ref(null)
const knowledgeDocCount = ref(0)
const dailyReportEnabled = ref(false)
const dailyReportLoading = ref(false)
const systemForOnboarding = computed(() => (
  system.value ? { ...system.value, knowledge_doc_count: knowledgeDocCount.value } : null
))
const { steps: onboardingSteps, progressPct, nextStep, isComplete } = useOnboarding(systemForOnboarding)

function goToOnboardingStep(step) {
  if (step.action === 'knowledge') {
    router.push(`/systems/${props.id}/knowledge`)
    return
  }
  focusConfigSection(step.key)
}
const health = ref(null)
const healthLoading = ref(false)
const systemMessages = ref([])
const messageActionId = ref(null)
const auditLogs = ref([])
const auditLoading = ref(false)
const systemMessageStatusText = {
  unread: '未读',
  read: '已读',
  acknowledged: '已确认',
}
const enabledServicesCount = computed(() => system.value?.services?.filter((service) => service.enabled).length || 0)
const needsCollectorSetup = computed(() => !system.value?.local && !system.value?.restart_capability?.collector_online)
const registeredServices = computed(() => system.value?.services || [])
const hasRegisteredService = (matcher) => registeredServices.value.some((service) => matcher(service))
const hasHttpServices = computed(() => hasRegisteredService((service) => service.connector === 'http'))
const hasPrometheusService = computed(() => hasRegisteredService((service) => {
  const config = service.config || {}
  const name = String(service.name || '').toLowerCase()
  return service.connector === 'prometheus' || name.includes('prometheus') || Number(config.port) === 9090
}))
const hasRedisService = computed(() => hasRegisteredService((service) => {
  const config = service.config || {}
  const name = String(service.name || '').toLowerCase()
  return service.connector === 'tcp' && (name.includes('redis') || Number(config.port) === 6379)
}))
const hasKafkaService = computed(() => hasRegisteredService((service) => {
  const config = service.config || {}
  const name = String(service.name || '').toLowerCase()
  return service.connector === 'tcp' && (name.includes('kafka') || Number(config.port) === 9092)
}))
const hasMysqlService = computed(() => hasRegisteredService((service) => {
  const config = service.config || {}
  const name = String(service.name || '').toLowerCase()
  return service.connector === 'tcp' && (name.includes('mysql') || Number(config.port) === 3306)
}))
const hasRestartTargets = computed(() => (system.value?.restart_capability?.services || []).some((item) => item.restartable))
const hasRemoteOpsCapabilities = computed(() => hasRestartTargets.value || hasRedisService.value || hasKafkaService.value)
const hasMetricsCapabilities = computed(() => (
  hasPrometheusService.value || hasRedisService.value || hasKafkaService.value || hasMysqlService.value || hasHttpServices.value
))
const hasAnyServices = computed(() => registeredServices.value.length > 0)
const showCollectorCard = computed(() => !system.value?.local)
const notifyChannelsSummary = computed(() => {
  const channels = system.value?.notify?.channels || []
  if (!channels.length) return '仅站内'
  return channels.join(' / ')
})
const overviewSummaryCards = computed(() => {
  if (!system.value) return []
  return [
    {
      label: '服务总数',
      value: system.value.services.length,
      detail: enabledServicesCount.value ? `${enabledServicesCount.value} 个已启用监控` : '尚未启用监控',
    },
    {
      label: '当前状态',
      value: health.value ? (health.value.healthy ? '全部正常' : '存在异常') : '待探活',
      detail: health.value ? '来自最新健康探测结果' : '点击后会自动刷新',
    },
    {
      label: '巡检周期',
      value: system.value.monitoring?.enabled ? `${system.value.monitoring.interval_seconds}s` : '已暂停',
      detail: system.value.monitoring?.enabled ? '按系统独立周期执行' : '开启后开始定时巡检',
    },
    {
      label: '通知渠道',
      value: notifyChannelsSummary.value,
      detail: system.value.notify?.type === 'none' ? '仅站内消息' : '外部渠道已启用',
    },
  ]
})

const quickActions = computed(() => [
  { label: '重新探活', type: 'default', action: runHealth },
  { label: '刷新消息', type: 'default', action: loadSystemMessages },
  { label: '知识库', type: 'primary', action: () => router.push(`/systems/${props.id}/knowledge`) },
])
const configGuideCards = computed(() => {
  if (!system.value) return []
  return [
    {
      key: 'services',
      title: '1. 接入服务',
      detail: system.value.services.length
        ? `已登记 ${system.value.services.length} 个服务，可继续测试与启用`
        : '先登记前端、后端、数据库、缓存、消息队列等服务',
      ready: system.value.services.length > 0,
      actionLabel: system.value.services.length ? '继续管理服务' : '去登记服务',
    },
    !system.value.local
      ? {
          key: 'collector',
          title: '2. 远程采集器',
          detail: system.value.restart_capability?.collector_online
            ? '采集器已在线，可读取日志并执行远程探活'
            : '创建采集器并发给对方机器部署，平台通过出站连接拿数据',
          ready: Boolean(system.value.restart_capability?.collector_online),
          actionLabel: system.value.restart_capability?.collector_online ? '查看采集器状态' : '去部署采集器',
        }
      : {
          key: 'services',
          title: '2. 测试启用',
          detail: enabledServicesCount.value
            ? `已有 ${enabledServicesCount.value} 个服务启用监控`
            : '对本机服务逐个测试，通过后再启用正式监控',
          ready: enabledServicesCount.value > 0,
          actionLabel: enabledServicesCount.value ? '继续测试服务' : '去测试启用',
        },
    {
      key: 'monitoring',
      title: '3. 巡检与告警',
      detail: system.value.monitoring?.enabled
        ? `巡检已开启，当前周期 ${system.value.monitoring.interval_seconds}s`
        : '设置巡检间隔，并绑定站内、飞书、邮件告警',
      ready: Boolean(system.value.monitoring?.enabled),
      actionLabel: system.value.monitoring?.enabled ? '查看巡检配置' : '去开启巡检',
    },
    hasRemoteOpsCapabilities.value ? {
      key: 'remote-actions',
      title: '4. 远程操作权限',
      detail: system.value.restart_capability?.enabled
        ? '已具备受控重启或远程命令能力'
        : '如需远程重启，请补充容器名或 systemd 单元等可控目标',
      ready: Boolean(system.value.restart_capability?.enabled),
      actionLabel: system.value.restart_capability?.enabled ? '查看远程操作' : '去补充权限',
    } : null,
  ].filter(Boolean)
})
const systemAccessTag = computed(() => {
  if (!system.value) return ''
  if (system.value.local) return '平台托管'
  return system.value.restart_capability?.enabled ? '远程可控' : '远程接入'
})
const { metrics, metricsLoading } = useMetricsPolling(props.id)

async function loadDailyReportConfig() {
  try {
    const { data } = await api.get(`/systems/${props.id}/daily-report`)
    dailyReportEnabled.value = Boolean(data.enabled)
  } catch {
    dailyReportEnabled.value = false
  }
}

async function updateDailyReport(enabled) {
  dailyReportLoading.value = true
  try {
    const { data } = await api.put(`/systems/${props.id}/daily-report`, { enabled })
    dailyReportEnabled.value = Boolean(data.enabled)
    message.success(enabled ? '已启用每日健康日报（每天 08:00）' : '已关闭每日健康日报')
  } catch (error) {
    message.error(error?.response?.data?.detail || '日报设置更新失败')
  } finally {
    dailyReportLoading.value = false
  }
}
const {
  notifyCfg,
  notifyEditingSecret,
  notifyLoading,
  notifySecretAlreadySet,
  notifyTestLoading,
  notifyTestResult,
  toggleNotifyChannel,
  saveNotify,
  startEditingNotifySecret,
  syncNotifyFromSystem,
  testNotify,
} = useNotifyConfig(props.id, loadSystem)

async function loadSystem() {
  try {
    const [{ data }, docsResult] = await Promise.all([
      api.get(`/systems/${props.id}`),
      api.get(`/systems/${props.id}/knowledge/docs`).catch(() => ({ data: [] })),
    ])
    system.value = data
    knowledgeDocCount.value = docsResult.data.length
    syncNotifyFromSystem(data)
    loadDailyReportConfig()
  } catch { message.error('加载系统失败') }
}

async function loadSystemMessages() {
  try {
    const { data } = await api.get(`/systems/${props.id}/messages`, { params: { limit: 20 } })
    systemMessages.value = data.filter((item) => item.status !== 'resolved')
  } catch {
    systemMessages.value = []
  }
}

async function loadSystemAudit() {
  auditLoading.value = true
  try {
    const { data } = await api.get('/audit', { params: { system_id: props.id, limit: 8 } })
    auditLogs.value = data.items || data
  } catch {
    auditLogs.value = []
  } finally {
    auditLoading.value = false
  }
}

async function updateSystemMessage(item, action) {
  messageActionId.value = item.id
  try {
    const { data } = await api.post(`/messages/${item.id}/${action}`)
    systemMessages.value = systemMessages.value
      .map((current) => current.id === item.id ? data : current)
      .filter((current) => current.status !== 'resolved')
    window.dispatchEvent(new Event('aiops:messages-changed'))
    loadSystemAudit()
    message.success(action === 'ack' ? '已确认消息' : '已标记解决')
  } catch (error) {
    message.error(error?.response?.data?.detail || '操作失败')
  } finally {
    messageActionId.value = null
  }
}

async function runHealth() {
  healthLoading.value = true
  try {
    const { data } = await api.get(`/systems/${props.id}/health`)
    health.value = data
  } catch { message.error('探活失败') }
  finally { healthLoading.value = false }
}

async function focusConfigSection(stepKey = 'services') {
  activeTab.value = 'config'
  await nextTick()
  const refMap = {
    services: servicesSectionRef,
    collector: collectorSectionRef,
    monitoring: monitoringSectionRef,
    'remote-actions': remoteActionsSectionRef,
  }
  const target = refMap[stepKey]?.value
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(() => {
  const tab = route.query.tab
  if (typeof tab === 'string' && ['overview', 'config', 'metrics', 'diagnose', 'audit'].includes(tab)) {
    activeTab.value = tab
  }
  loadSystem()
  loadSystemMessages()
  loadSystemAudit()
  runHealth()
  if (tab === 'config' && typeof route.query.step === 'string') {
    nextTick(() => focusConfigSection(route.query.step))
  }
})
</script>

<template>
  <div v-if="system">
    <!-- 页头 -->
    <div class="page-header">
      <button class="back-btn" @click="router.push('/systems')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             width="16" height="16"><polyline points="15 18 9 12 15 6" /></svg>
        返回
      </button>
      <div class="header-info">
        <h2 class="sys-name">{{ system.name }}</h2>
        <a-tag :color="system.local ? 'success' : 'processing'" style="border:none">
          {{ systemAccessTag }}
        </a-tag>
        <!-- 全局健康灯 -->
        <span v-if="health" :class="['health-badge', health.healthy ? 'health-badge--ok' : 'health-badge--err']">
          {{ health.healthy ? '● 全部正常' : '● 存在异常' }}
        </span>
      </div>
    </div>

    <!-- ── Tab 导航 ── -->
    <a-tabs v-model:activeKey="activeTab" class="main-tabs" :animated="false">

      <!-- ① 概览 -->
      <a-tab-pane key="overview" tab="概览">
        <div class="overview-layout">
          <div class="overview-hero panel-card">
            <div>
              <div class="overview-kicker">系统总览</div>
              <div class="overview-title">{{ system.name }}</div>
              <div class="overview-desc">
                {{ system.local ? '平台本机托管 · 直接探测与执行' : '远程系统接入 · 通过采集器回连平台' }}
              </div>
            </div>
            <div class="overview-badges">
              <a-tag :color="system.local ? 'success' : 'processing'" style="border:none;margin:0">
                {{ systemAccessTag }}
              </a-tag>
              <span v-if="health" :class="['health-badge', health.healthy ? 'health-badge--ok' : 'health-badge--err']">
                {{ health.healthy ? '● 全部正常' : '● 存在异常' }}
              </span>
            </div>
            <div class="overview-actions">
              <a-button v-for="action in quickActions" :key="action.label" size="small" :type="action.type" @click="action.action">{{ action.label }}</a-button>
            </div>
          </div>

          <div class="overview-summary-grid">
            <div v-for="card in overviewSummaryCards" :key="card.label" class="overview-summary-card">
              <div class="overview-summary-label">{{ card.label }}</div>
              <div class="overview-summary-value">{{ card.value }}</div>
              <div class="overview-summary-detail">{{ card.detail }}</div>
            </div>
          </div>

          <a-card class="panel-card onboarding-card" title="接入进度">
            <div class="onboarding-summary">
              <div>
                <div class="onboarding-title">{{ system.local ? '当前是平台本机托管系统' : '当前是远程接入系统' }}</div>
                <div class="onboarding-sub">
                  {{ isComplete
                    ? '接入步骤已完成，可以开始巡检和 AI 诊断。'
                    : (nextStep ? `下一步：${nextStep.title}` : '按步骤完成系统接入') }}
                </div>
                <a-progress :percent="progressPct" size="small" style="margin-top:10px;max-width:280px" />
              </div>
              <a-tag :color="isComplete ? 'success' : needsCollectorSetup ? 'warning' : 'processing'" style="border:none;margin:0">
                {{ isComplete ? '接入完成' : needsCollectorSetup ? '待部署采集器' : `${progressPct}%` }}
              </a-tag>
            </div>
            <div class="onboarding-steps">
              <button
                v-for="step in onboardingSteps"
                :key="step.title"
                type="button"
                class="onboarding-step onboarding-step--clickable"
                @click="goToOnboardingStep(step)"
              >
                <span :class="['onboarding-dot', step.done ? 'done' : 'todo']" />
                <div class="onboarding-step-body">
                  <div class="onboarding-step-title">{{ step.title }}</div>
                  <div class="onboarding-step-detail">{{ step.detail }}</div>
                </div>
                <span class="onboarding-step-action">{{ step.done ? '已完成' : '去完成' }}</span>
              </button>
            </div>
          </a-card>

          <a-card v-if="systemMessages.length" class="panel-card message-card" title="近期消息">
            <div class="system-message-list">
              <div v-for="item in systemMessages" :key="item.id" class="system-message-row">
                <div class="system-message-main">
                  <div class="system-message-title">
                    <a-tag :color="item.status === 'unread' ? 'error' : 'warning'" style="border:none;margin:0">
                      {{ systemMessageStatusText[item.status] || item.status }}
                    </a-tag>
                    <span>{{ item.title }}</span>
                  </div>
                  <p>{{ item.summary || item.content }}</p>
                  <div v-if="item.suggestion?.length" class="system-message-suggestion">
                    <div>建议措施：</div>
                    <ul>
                      <li v-for="suggestion in item.suggestion" :key="suggestion">{{ suggestion }}</li>
                    </ul>
                  </div>
                </div>
                <div class="system-message-actions">
                  <a-button
                    v-if="item.status !== 'acknowledged'"
                    size="small"
                    :loading="messageActionId === item.id"
                    @click="updateSystemMessage(item, 'ack')"
                  >确认</a-button>
                  <a-button
                    size="small"
                    type="primary"
                    :loading="messageActionId === item.id"
                    @click="updateSystemMessage(item, 'resolve')"
                  >解决</a-button>
                </div>
              </div>
            </div>
          </a-card>

          <a-card class="panel-card" title="服务健康状态">
          <template #extra>
            <a-button size="small" :loading="healthLoading" @click="runHealth">重新探活</a-button>
          </template>
          <a-alert
            v-if="system.restart_capability"
            style="margin-bottom:12px"
            :type="system.restart_capability.enabled ? 'info' : 'warning'"
            show-icon
            :message="system.restart_capability.enabled
              ? `支持容器重启：${system.restart_capability.execution_mode === 'local' ? '平台本机执行' : '采集器远程执行'}`
              : (system.restart_capability.reason || '当前系统暂不支持自动重启')"
          />
          <a-alert
            v-if="health"
            :type="health.healthy ? 'success' : 'error'"
            :message="health.healthy ? '所有服务正常' : '存在异常服务'"
            show-icon style="margin-bottom:12px"
          />
          <a-list :data-source="health?.services || []" size="small">
            <template #renderItem="{ item }">
              <a-list-item>
                <a-list-item-meta :title="item.name" :description="item.detail" />
                <template #extra>
                  <a-tag :color="item.ok ? 'success' : 'error'" style="border:none">
                    {{ item.ok ? '正常' : '异常' }}
                  </a-tag>
                </template>
              </a-list-item>
            </template>
          </a-list>
          </a-card>

          <a-card v-if="!hasAnyServices" class="panel-card empty-capability-card" title="下一步建议">
          <div class="empty-capability-copy">
            当前系统还没有登记任何服务。先在“配置”页添加前端、后端、数据库、Redis、Kafka 或 Prometheus，
            后续监控、日志、诊断和告警能力才会按已接入服务自动展开。
          </div>
          </a-card>

          <a-card class="panel-card audit-card" title="最近操作记录">
          <template #extra>
            <a-button size="small" :loading="auditLoading" @click="loadSystemAudit">刷新</a-button>
          </template>
          <AuditLogList
            compact
            :logs="auditLogs"
            :systems="[system]"
            :loading="auditLoading"
            empty-title="暂无操作记录"
            empty-sub="接入上报、告警处理和人工确认记录会出现在这里"
          />
          </a-card>
          <ServiceLogsCard
            v-if="hasAnyServices"
            :system-id="props.id"
            :services="system.services"
            :system-local="system.local"
            @open-diagnose="openDiagnoseWithQuestion"
          />
        </div>
      </a-tab-pane>

      <!-- ② 监控 -->
      <a-tab-pane v-if="hasMetricsCapabilities" key="metrics" tab="监控">
        <div class="section-hero panel-card">
          <div>
            <div class="section-kicker">实时监控</div>
            <div class="section-title">系统健康与指标趋势</div>
            <div class="section-desc">先看整体，再看单项指标，减少在配置中来回切换。</div>
          </div>
          <a-button size="small" :loading="metricsLoading" @click="loadSystem">刷新系统</a-button>
        </div>
        <MetricsTab :system-id="props.id" :metrics="metrics" :metrics-loading="metricsLoading" :services="system.services" />
      </a-tab-pane>

      <!-- ③ 诊断 -->
      <a-tab-pane v-if="hasAnyServices" key="diagnose" tab="AI 诊断">
        <div class="section-hero panel-card">
          <div>
            <div class="section-kicker">智能诊断</div>
            <div class="section-title">证据驱动的故障分析</div>
            <div class="section-desc">AI 会结合健康检查、日志、指标和知识库内容给出结论。</div>
          </div>
        </div>
        <DiagnosisPanel
          :system-id="props.id"
          :restart-capability="system.restart_capability"
          :draft-question="diagnosisDraftQuestion"
        />
      </a-tab-pane>

      <!-- ④ 配置 -->
      <a-tab-pane key="config" tab="配置">
        <div class="config-grid">
          <a-card class="panel-card config-section-card" title="接入向导">
            <div class="config-guide-grid">
              <button
                v-for="card in configGuideCards"
                :key="card.key"
                type="button"
                class="config-guide-card"
                @click="focusConfigSection(card.key)"
              >
                <div class="config-guide-card__top">
                  <span class="config-guide-card__title">{{ card.title }}</span>
                  <a-tag :color="card.ready ? 'success' : 'processing'" class="config-guide-card__tag">
                    {{ card.ready ? '已就绪' : '待完成' }}
                  </a-tag>
                </div>
                <div class="config-guide-card__detail">{{ card.detail }}</div>
                <div class="config-guide-card__action">{{ card.actionLabel }}</div>
              </button>
            </div>
          </a-card>

          <a-card v-if="!hasAnyServices || needsCollectorSetup" class="panel-card config-section-card" title="当前最该做什么">
            <div class="empty-capability-copy">
              <template v-if="!hasAnyServices">
                先把这个系统里的关键服务登记进来，例如前端入口、Spring Boot、MySQL、Redis、Kafka 或 Prometheus。
                只有登记后的服务，后面才会出现在监控、日志、诊断和告警里。
              </template>
              <template v-else-if="needsCollectorSetup">
                当前已经登记服务，但这是远程系统，平台还需要对方机器上的采集器上线后，才能稳定读取日志、执行远程探活和受控操作。
              </template>
            </div>
          </a-card>

          <div ref="servicesSectionRef">
          <a-card class="panel-card config-section-card" title="接入服务">
            <ServicesConfigCard
              :services="system.services"
              :service-colors="SERVICE_COLORS"
              :restart-capability="system.restart_capability"
              :action-loading="serviceActionLoading"
              @add-service="openAddSvc"
              @delete-service="deleteService"
              @test-service="testService"
              @enable-service="enableService"
              @enable-unavailable-service="enableUnavailableService"
              @restart-service="restartService"
            />
            <div v-if="showCollectorCard" ref="collectorSectionRef">
              <CollectorDownloadCard
                :system-id="props.id"
                :system-name="system?.name"
                :system-local="system?.local"
                :services="system?.services || []"
                :restart-capability="system?.restart_capability"
              />
            </div>
          </a-card>
          </div>

          <div v-if="hasAnyServices" ref="monitoringSectionRef">
          <a-card class="panel-card config-section-card" title="巡检与告警">
            <MonitoringConfigCard :system-id="props.id" :system="system" />
            <NotifyConfigCard
              :system="system"
              :daily-report-enabled="dailyReportEnabled"
              :daily-report-loading="dailyReportLoading"
              :notify-cfg="notifyCfg"
              :notify-editing-secret="notifyEditingSecret"
              :notify-loading="notifyLoading"
              :notify-secret-already-set="notifySecretAlreadySet"
              :notify-test-loading="notifyTestLoading"
              :notify-test-result="notifyTestResult"
              @toggle-channel="toggleNotifyChannel"
              @edit-secret="startEditingNotifySecret"
              @save="saveNotify"
              @test="testNotify"
              @toggle-daily-report="updateDailyReport"
            />
          </a-card>
          </div>

          <div v-if="hasAnyServices && hasRemoteOpsCapabilities" ref="remoteActionsSectionRef">
          <a-card class="panel-card config-section-card" title="诊断与执行">
            <DiagnosticTemplatesCard v-if="hasAnyServices" :system-id="props.id" :services="system.services" />
        <RemoteActionsCard
          v-if="hasAnyServices"
          :system-id="props.id"
          :system-local="system.local"
          :restart-capability="system.restart_capability"
          :services="system.services"
          @restart-service="restartService"
        />
        <RestartPolicyCard
          v-if="hasAnyServices"
          :system-id="props.id"
          :restart-capability="system.restart_capability"
        />
          </a-card>
          </div>

          <KnowledgeBaseCard :system-id="props.id" />

          <SystemTokensCard :system-id="props.id" :system-key="system?.key" />
        </div>
      </a-tab-pane>

    </a-tabs>

    <AddServiceModal
      v-model:open="addSvcOpen"
      :system-id="props.id"
      :system-local="!!system?.local"
      @created="loadSystem"
    />
  </div>

  <div v-else class="loading-center">
    <a-spin size="large" />
  </div>
</template>
<style scoped>
/* 页头 */
.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}
.back-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 7px;
  padding: 6px 12px;
  font-size: 13px;
  color: var(--text-subtle);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.back-btn:hover { border-color: var(--primary); color: var(--primary); }
.header-info {
  display: flex;
  align-items: center;
  gap: 10px;
}
.sys-name {
  font-size: 20px;
  font-weight: 700;
  color: var(--text);
}

/* 面板卡片 */
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
}
.overview-layout {
  display: grid;
  gap: 14px;
}
.overview-hero,
.section-hero,
.config-section-card {
  padding: 18px 20px;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--card-bg) 94%, white), var(--card-bg));
}
.overview-kicker,
.section-kicker {
  font-size: 12px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-subtle);
}
.overview-title,
.section-title {
  font-size: 22px;
  font-weight: 800;
  color: var(--text);
}
.overview-desc,
.section-desc {
  color: var(--text-subtle);
  font-size: 13px;
  line-height: 1.7;
}
.overview-badges,
.overview-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.overview-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.overview-summary-card {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: color-mix(in srgb, var(--card-bg) 88%, white);
  padding: 14px 16px;
  min-height: 108px;
}
.overview-summary-card:hover {
  box-shadow: 0 8px 22px rgba(23, 34, 40, .06);
}
.config-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14px;
}
.config-guide-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.config-guide-card {
  width: 100%;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: color-mix(in srgb, var(--card-bg) 92%, white);
  padding: 14px 16px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}
.config-guide-card:hover {
  border-color: var(--primary);
  box-shadow: 0 6px 18px color-mix(in srgb, var(--text) 7%, transparent);
  transform: translateY(-1px);
}
.config-guide-card__top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.config-guide-card__title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
}
.config-guide-card__tag {
  border: none !important;
  margin: 0 !important;
}
.config-guide-card__detail {
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-subtle);
  min-height: 40px;
}
.config-guide-card__action {
  margin-top: 10px;
  font-size: 12px;
  font-weight: 700;
  color: var(--primary);
}
.overview-summary-label {
  font-size: 12px;
  color: var(--text-subtle);
  margin-bottom: 8px;
}
.overview-summary-value {
  font-size: 20px;
  line-height: 1.1;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 6px;
}
.overview-summary-detail {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-subtle);
}
.onboarding-card {
  margin-bottom: 16px;
}
.onboarding-summary {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}
.onboarding-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}
.onboarding-sub {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-subtle);
}
.onboarding-steps {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.onboarding-step {
  display: flex;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: color-mix(in srgb, var(--card-bg) 88%, white);
  align-items: flex-start;
}
.onboarding-step--clickable {
  width: 100%;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.onboarding-step--clickable:hover {
  border-color: var(--primary);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--text) 6%, transparent);
}
.onboarding-step-body { flex: 1; min-width: 0; }
.onboarding-step-action {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--primary);
  padding-top: 2px;
}
.onboarding-dot {
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 999px;
  flex-shrink: 0;
}
.onboarding-dot.done { background: #22c55e; }
.onboarding-dot.todo { background: #f59e0b; }
.onboarding-step-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}
.onboarding-step-detail {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-subtle);
}
.message-card {
  margin-bottom: 16px;
}
.config-section-label {
  margin: 18px 0 10px;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-subtle);
  letter-spacing: .06em;
  text-transform: uppercase;
}
.config-section-label:first-child {
  margin-top: 0;
}
.empty-capability-card {
  margin-top: 16px;
}
.empty-capability-copy {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-subtle);
}
.audit-card {
  margin-top: 16px;
}
.system-message-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.system-message-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--body-bg);
}
.system-message-main {
  flex: 1;
  min-width: 0;
}
.system-message-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 5px;
}
.system-message-main p {
  font-size: 13px;
  color: var(--text-subtle);
  line-height: 1.55;
  margin: 0;
}
.system-message-suggestion {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--text);
  line-height: 1.5;
}
.system-message-suggestion ul {
  margin: 4px 0 0;
  padding-left: 18px;
}
.system-message-suggestion li { margin-bottom: 3px; }
.system-message-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* ── Tab 布局 ── */
.main-tabs {
  margin-top: 4px;
}
.main-tabs :deep(.ant-tabs-nav) {
  margin-bottom: 16px;
}
.main-tabs :deep(.ant-tabs-tab) {
  font-size: 14px;
  padding: 8px 4px;
}

/* 页头健康灯 */
.health-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
}
.health-badge--ok { color: #52c41a; background: rgba(82,196,26,.1); }
.health-badge--err { color: #f5222d; background: rgba(245,34,45,.1); }

/* Loading */
.loading-center {
  display: flex; justify-content: center;
  padding-top: 120px;
}

@media (max-width: 720px) {
  .overview-summary-grid {
    grid-template-columns: 1fr;
  }
  .config-guide-grid {
    grid-template-columns: 1fr;
  }
  .onboarding-summary {
    flex-direction: column;
  }
  .onboarding-steps {
    grid-template-columns: 1fr;
  }
  .system-message-row {
    flex-direction: column;
  }
  .system-message-actions {
    width: 100%;
  }
}

</style>
