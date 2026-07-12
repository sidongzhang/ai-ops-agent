<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
  system: { type: Object, default: null },
})
const loading = ref(false)
const saving = ref(false)
const checking = ref(false)
const lastCheck = ref(null)
const sectionOpen = ref([])
const form = reactive({ enabled: true, interval_seconds: 60 })

const intervalOptions = [
  { label: '15 秒', value: 15 },
  { label: '30 秒', value: 30 },
  { label: '1 分钟', value: 60 },
  { label: '5 分钟', value: 300 },
  { label: '15 分钟', value: 900 },
  { label: '1 小时', value: 3600 },
]

function intervalLabel(seconds) {
  const hit = intervalOptions.find((item) => item.value === seconds)
  return hit?.label || `${seconds} 秒`
}

const monitoredServices = computed(() => (props.system?.services || []).filter((item) => item.enabled !== false))
const pendingServices = computed(() => (props.system?.services || []).filter((item) => item.enabled === false))
const healthItems = computed(() => lastCheck.value?.services || [])
const healthSummary = computed(() => {
  if (!healthItems.value.length) return ''
  const failed = healthItems.value.filter((item) => !item.ok).length
  return failed ? `${failed} 个服务异常` : '全部正常'
})

async function loadConfig() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/monitoring`)
    Object.assign(form, data)
  } catch (error) {
    message.error(error?.response?.data?.detail || '巡检配置加载失败')
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    await api.put(`/systems/${props.systemId}/monitoring`, form)
    message.success(form.enabled ? '巡检配置已保存' : '巡检已暂停')
  } catch (error) {
    message.error(error?.response?.data?.detail || '巡检配置保存失败')
  } finally {
    saving.value = false
  }
}

async function runCheckNow() {
  checking.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/health`)
    lastCheck.value = data
    const failed = data.services?.filter((item) => !item.ok).length || 0
    message[failed ? 'warning' : 'success'](failed ? `巡检完成：${failed} 个服务异常` : '巡检完成：当前全部正常')
  } catch (error) {
    message.error(error?.response?.data?.detail || '立即巡检失败')
  } finally {
    checking.value = false
  }
}

onMounted(loadConfig)
</script>

<template>
  <div class="subsection">
    <a-collapse v-model:activeKey="sectionOpen" ghost expand-icon-position="start" class="subsection-collapse">
      <a-collapse-panel key="main">
        <template #header>
          <div class="title-row">
            <span>自动巡检</span>
            <a-tag :color="form.enabled ? 'green' : 'default'" style="border:none">
              {{ form.enabled ? '运行中' : '已暂停' }}
            </a-tag>
            <a-tag style="border:none;color:var(--text-subtle)">{{ intervalLabel(form.interval_seconds) }}</a-tag>
          </div>
        </template>

        <div class="monitoring-body">
          <div class="monitoring-summary">
            平台只检查已经注册并启用的服务；没有注册的服务不会展示，也不会纳入巡检和告警。
          </div>
          <div class="scope-box">
            <div class="scope-head">
              <div>
                <div class="field-label">本系统巡检范围</div>
                <div class="field-help">当前会检查 {{ monitoredServices.length }} 个已启用服务</div>
              </div>
              <a-tag v-if="pendingServices.length" color="default" style="border:none">
                {{ pendingServices.length }} 个待启用
              </a-tag>
            </div>
            <div v-if="monitoredServices.length" class="service-chip-list">
              <span v-for="service in monitoredServices" :key="service.id || service.name" class="service-chip">
                {{ service.name }}
                <small>{{ service.connector }}</small>
              </span>
            </div>
            <div v-else class="empty-scope">暂无已启用服务，开启服务后才会进入巡检。</div>
          </div>
          <div class="monitoring-row">
            <div>
              <div class="field-label">启用巡检</div>
              <div class="field-help">暂停后不会产生新的巡检告警</div>
            </div>
            <a-switch v-model:checked="form.enabled" />
          </div>
          <div class="monitoring-row">
            <div>
              <div class="field-label">巡检间隔</div>
              <div class="field-help">远程系统建议 1 分钟以上</div>
            </div>
            <a-select v-model:value="form.interval_seconds" :options="intervalOptions" style="width:130px" />
          </div>
          <div class="monitoring-actions">
            <a-button type="primary" :loading="saving" @click="saveConfig">保存巡检配置</a-button>
            <a-button :loading="checking" :disabled="!monitoredServices.length" @click="runCheckNow">立即巡检</a-button>
          </div>
          <div v-if="lastCheck" class="check-result">
            <div class="check-result-head">
              <strong>最近一次立即巡检</strong>
              <a-tag :color="lastCheck.healthy ? 'green' : 'error'" style="border:none">
                {{ healthSummary }}
              </a-tag>
            </div>
            <div class="health-list">
              <div v-for="item in healthItems" :key="item.name" :class="['health-item', item.ok ? 'ok' : 'bad']">
                <span>{{ item.name }}</span>
                <small>{{ item.detail }}</small>
              </div>
            </div>
          </div>
        </div>
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>

<style scoped>
.subsection-collapse :deep(.ant-collapse-header) {
  align-items: center !important;
  padding: 10px 0 !important;
}
.subsection-collapse :deep(.ant-collapse-content-box) {
  padding: 0 0 8px !important;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.monitoring-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.monitoring-summary {
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
}
.scope-box {
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--body-bg);
}
.scope-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 10px;
}
.service-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.service-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border-radius: 999px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  color: var(--text);
  font-size: 12px;
}
.service-chip small {
  color: var(--text-subtle);
  font-size: 11px;
}
.empty-scope {
  color: var(--text-subtle);
  font-size: 12px;
}
.monitoring-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.monitoring-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.check-result {
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--body-bg);
}
.check-result-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
  color: var(--text);
  font-size: 13px;
}
.health-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.health-item {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  font-size: 12px;
}
.health-item span {
  color: var(--text);
  font-weight: 600;
}
.health-item small {
  color: var(--text-subtle);
  overflow-wrap: anywhere;
}
.health-item.bad {
  border-color: color-mix(in srgb, #dc2626 30%, var(--border-color));
  background: color-mix(in srgb, #dc2626 4%, var(--card-bg));
}
.health-item.ok {
  border-color: color-mix(in srgb, #557568 24%, var(--border-color));
}
.field-label {
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}
.field-help {
  color: var(--text-subtle);
  font-size: 11px;
  margin-top: 3px;
}
@media (max-width: 720px) {
  .monitoring-row,
  .scope-head,
  .check-result-head {
    align-items: stretch;
    flex-direction: column;
  }
  .health-item {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
