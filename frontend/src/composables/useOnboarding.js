import { computed } from 'vue'

export function useOnboarding(system) {
  const hasServices = computed(() => (system.value?.services?.length || 0) > 0)
  const hasEnabledServices = computed(() => (
    system.value?.services?.some((service) => service.enabled !== false) || false
  ))
  const monitoringEnabled = computed(() => Boolean(system.value?.monitoring?.enabled))
  const notifyConfigured = computed(() => {
    const notify = system.value?.notify
    if (!notify) return false
    return notify.type !== 'none' || (notify.channels?.length || 0) > 0
  })
  const isLocal = computed(() => Boolean(system.value?.local))
  const collectorReady = computed(() => {
    if (!system.value || system.value.local) return true
    return Boolean(system.value.restart_capability?.collector_online)
  })
  const hasKnowledgeDocs = computed(() => (system.value?.knowledge_doc_count || 0) > 0)

  const steps = computed(() => {
    if (!system.value) return []
    return [
      {
        key: 'services',
        title: '登记服务',
        done: hasServices.value,
        detail: hasServices.value
          ? `已登记 ${system.value.services.length} 个服务`
          : '先把前端、后端、数据库、Redis、Kafka 等服务登记进来',
        tab: 'config',
        action: 'config',
      },
      {
        key: 'collector',
        title: isLocal.value ? '测试并启用服务' : '部署采集器',
        done: isLocal.value ? hasEnabledServices.value : collectorReady.value,
        detail: isLocal.value
          ? (hasEnabledServices.value ? '已有服务启用监控' : '逐个测试服务，测试通过后启用监控')
          : (collectorReady.value ? '采集器已上线' : '在配置页创建采集器并部署到目标机器'),
        tab: 'config',
        action: 'config',
      },
      {
        key: 'monitoring',
        title: '开启巡检与告警',
        done: monitoringEnabled.value && notifyConfigured.value,
        detail: monitoringEnabled.value
          ? (notifyConfigured.value
            ? `巡检 ${system.value.monitoring.interval_seconds}s · 通知已配置`
            : '巡检已开，建议补充飞书/邮件通知渠道')
          : '开启自动巡检，并绑定站内、飞书或邮件通知',
        tab: 'config',
        action: 'config',
      },
      {
        key: 'knowledge',
        title: '补充知识库',
        done: hasKnowledgeDocs.value,
        detail: hasKnowledgeDocs.value
          ? '已有运维文档可供诊断引用'
          : '上传 runbook 或架构说明，提升 AI 诊断准确度',
        tab: 'knowledge',
        action: 'knowledge',
      },
    ]
  })

  const completedCount = computed(() => steps.value.filter((step) => step.done).length)
  const progressPct = computed(() => (
    steps.value.length ? Math.round((completedCount.value / steps.value.length) * 100) : 0
  ))
  const isComplete = computed(() => steps.value.length > 0 && completedCount.value === steps.value.length)
  const nextStep = computed(() => steps.value.find((step) => !step.done) || null)

  return {
    steps,
    completedCount,
    progressPct,
    isComplete,
    nextStep,
    hasServices,
    monitoringEnabled,
  }
}

export const QUICK_START_STEPS = [
  {
    title: '注册系统',
    detail: '填写系统名称，选择本机托管或远程采集模式',
    duration: '30 秒',
  },
  {
    title: '登记服务',
    detail: '把前端、后端、数据库、Redis、Kafka 等关键组件登记进来',
    duration: '1 分钟',
  },
  {
    title: '部署采集 / 启用监控',
    detail: '远程系统部署采集器；本机系统直接测试并启用服务监控',
    duration: '1 分钟',
  },
  {
    title: '开启巡检',
    detail: '配置巡检间隔和通知渠道，异常会自动进入消息中心',
    duration: '30 秒',
  },
]
