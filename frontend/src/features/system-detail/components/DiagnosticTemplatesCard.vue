<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
  services: { type: Array, default: () => [] },
})

const templates = ref([])
const loading = ref(false)
const savingName = ref('')
const sectionOpen = ref([])
const expandedItems = ref([])

const enabledCount = computed(() => templates.value.filter((item) => item.enabled).length)
const registeredServices = computed(() => props.services.filter((service) => service.enabled !== false))
const capabilityMap = computed(() => {
  const has = (namePart, port) => registeredServices.value.some((service) => {
    const name = String(service.name || '').toLowerCase()
    const config = service.config || {}
    return name.includes(namePart) || Number(config.port) === port
  })
  return {
    http: registeredServices.value.some((service) => service.connector === 'http'),
    redis: has('redis', 6379),
    kafka: has('kafka', 9092),
    mysql: has('mysql', 3306),
    database: has('mysql', 3306) || has('postgres', 5432) || has('db', 0),
  }
})
const visibleTemplates = computed(() => templates.value.filter((item) => {
  if (item.name === 'redis_analysis') return capabilityMap.value.redis
  if (item.name === 'kafka_analysis') return capabilityMap.value.kafka
  if (item.name === 'http_health_failure') return capabilityMap.value.http
  if (item.name === 'database_analysis') return capabilityMap.value.database
  return true
}))

const templateTitles = {
  database_analysis: '数据库状态',
  health_check: '系统健康',
  http_health_failure: 'HTTP 接口异常',
  kafka_analysis: 'Kafka 消息积压',
  log_investigation: '日志错误排查',
  performance_analysis: '资源性能瓶颈',
  redis_analysis: 'Redis 状态',
  service_unreachable: '服务不可达',
}

function titleFor(item) {
  return templateTitles[item.name] || item.description.split('，')[0] || item.name
}

async function loadTemplates() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/diagnostic-templates`)
    templates.value = data
  } catch (error) {
    message.error(error?.response?.data?.detail || '诊断模板加载失败')
  } finally {
    loading.value = false
  }
}

async function toggleTemplate(template, enabled) {
  const previous = template.enabled
  template.enabled = enabled
  savingName.value = template.name
  try {
    const disabledNames = templates.value
      .filter((item) => !item.enabled)
      .map((item) => item.name)
    const { data } = await api.put(`/systems/${props.systemId}/diagnostic-templates`, {
      disabled_names: disabledNames,
    })
    templates.value = data
    message.success(enabled ? '诊断模板已启用' : '诊断模板已停用')
  } catch (error) {
    template.enabled = previous
    message.error(error?.response?.data?.detail || '诊断模板更新失败')
  } finally {
    savingName.value = ''
  }
}

onMounted(loadTemplates)
</script>

<template>
  <div class="subsection">
    <a-collapse v-model:activeKey="sectionOpen" ghost expand-icon-position="start" class="subsection-collapse">
      <a-collapse-panel key="main">
        <template #header>
          <div class="card-title">
            <span>诊断模板</span>
            <a-tag color="blue" style="border:none;margin:0">{{ enabledCount }}/{{ visibleTemplates.length }} 已启用</a-tag>
          </div>
        </template>

        <a-spin v-if="loading" style="display:block;margin:24px 0;text-align:center" />
        <div v-else-if="visibleTemplates.length === 0" class="empty-copy">
          当前系统还没有对应的中间件或接口服务，暂时不展示专项诊断模板。
        </div>
        <a-collapse
          v-else
          v-model:activeKey="expandedItems"
          ghost
          expand-icon-position="start"
          class="template-list"
        >
          <a-collapse-panel v-for="item in visibleTemplates" :key="item.name">
            <template #header>
              <div class="template-header">
                <span class="template-name">{{ titleFor(item) }}</span>
                <a-tag v-if="item.enabled" color="green" style="border:none;margin:0">可用于诊断</a-tag>
              </div>
            </template>
            <template #extra>
              <div class="template-switch" @click.stop>
                <a-switch
                  :checked="item.enabled"
                  :loading="savingName === item.name"
                  :disabled="!!savingName && savingName !== item.name"
                  size="small"
                  @change="(enabled) => toggleTemplate(item, enabled)"
                />
              </div>
            </template>

            <div class="template-body">
              <div v-if="item.triggers?.length" class="template-trigger">
                适用场景：{{ item.triggers.slice(0, 5).join('、') }}
              </div>
              <p class="template-description">{{ item.description }}</p>
              <pre class="template-steps">{{ item.steps }}</pre>
            </div>
          </a-collapse-panel>
        </a-collapse>
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>

<style scoped>
.subsection {
  border-top: 1px solid var(--border-color);
  padding-top: 4px;
}
.subsection-collapse :deep(.ant-collapse-header),
.template-list :deep(.ant-collapse-header) {
  align-items: center !important;
  padding: 10px 0 !important;
}
.subsection-collapse :deep(.ant-collapse-content-box),
.template-list :deep(.ant-collapse-content-box) {
  padding: 0 0 8px !important;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.template-header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.template-name {
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
}
.template-switch {
  display: flex;
  align-items: center;
  margin-right: 4px;
}
.template-body {
  padding: 0 0 4px 22px;
}
.template-trigger {
  margin-bottom: 8px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
}
.template-description {
  margin: 0 0 8px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
}
.template-steps {
  max-height: 230px;
  overflow: auto;
  margin: 0;
  padding: 9px 10px;
  border-radius: 6px;
  background: var(--body-bg);
  color: var(--text-subtle);
  font: 11px/1.6 'SF Mono', 'Monaco', 'Consolas', monospace;
  white-space: pre-wrap;
}
.empty-copy {
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
  padding: 0 0 8px 22px;
}

@media (max-width: 720px) {
  .template-body {
    padding-left: 0;
  }
  .empty-copy {
    padding-left: 0;
  }
}
</style>
