<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../api'

const loading = ref(false)
const days = ref(30)
const systemId = ref()
const data = ref(null)
const systems = ref([])

const systemOptions = computed(() => systems.value.map((system) => ({
  value: system.id,
  label: system.name,
})))

const maxTrend = computed(() => Math.max(
  1,
  ...recentTrend.value.map((item) => Math.max(item.alerts, item.resolved, item.diagnoses)),
))

const recentTrend = computed(() => {
  const trend = data.value?.trend || []
  return trend.slice(Math.max(0, trend.length - 14))
})

function formatMinutes(value) {
  if (value === null || value === undefined) return '暂无数据'
  if (value < 1) return `${Math.round(value * 60)} 秒`
  if (value < 60) return `${value.toFixed(1)} 分钟`
  return `${(value / 60).toFixed(1)} 小时`
}

function formatSeconds(value) {
  if (value === null || value === undefined) return '暂无数据'
  return `${value.toFixed(1)} 秒`
}

function shortDate(value) {
  return value?.slice(5) || '-'
}

function barHeight(value) {
  return value > 0 ? `${Math.max(4, value / maxTrend.value * 100)}%` : '0'
}

async function loadAnalytics() {
  loading.value = true
  try {
    const params = { days: days.value }
    if (systemId.value) params.system_id = systemId.value
    const [{ data: analytics }, { data: systemData }] = await Promise.all([
      api.get('/analytics/efficiency', { params }),
      systems.value.length ? Promise.resolve({ data: systems.value }) : api.get('/systems'),
    ])
    data.value = analytics
    systems.value = systemData
  } catch (error) {
    message.error(error?.response?.data?.detail || '效率数据加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadAnalytics)
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2 class="page-title">效率分析</h2>
        <p class="page-sub">用实际处理记录衡量告警、诊断、通知和修复闭环效率</p>
      </div>
      <div class="filters">
        <a-select
          v-model:value="systemId"
          class="system-select"
          placeholder="全部系统"
          allow-clear
          :options="systemOptions"
          @change="loadAnalytics"
        />
        <a-segmented v-model:value="days" :options="[7, 30, 90]" @change="loadAnalytics" />
        <a-button :loading="loading" @click="loadAnalytics">刷新</a-button>
      </div>
    </div>

    <a-spin v-if="loading && !data" style="display:block;margin-top:100px;text-align:center" />

    <template v-else-if="data">
      <section class="metric-grid">
        <div class="metric-item">
          <span class="metric-label">告警解决率</span>
          <strong>{{ data.alert_resolution_rate_pct }}%</strong>
          <span>{{ data.alerts_resolved }}/{{ data.alerts_total }} 条已解决</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">平均确认时间</span>
          <strong>{{ formatMinutes(data.avg_ack_minutes) }}</strong>
          <span>{{ data.alerts_unresolved }} 条仍未解决</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">平均解决时间</span>
          <strong>{{ formatMinutes(data.avg_resolution_minutes) }}</strong>
          <span>从告警创建到标记解决</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">诊断平均耗时</span>
          <strong>{{ formatSeconds(data.avg_diagnosis_seconds) }}</strong>
          <span>成功率 {{ data.diagnosis_success_rate_pct }}%</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">诊断证据覆盖</span>
          <strong>{{ data.diagnosis_evidence_rate_pct }}%</strong>
          <span>{{ data.diagnoses_total }} 次诊断</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">通知成功率</span>
          <strong>{{ data.notification_success_rate_pct }}%</strong>
          <span>{{ data.notification_failures }} 次仍失败，自动重试 {{ data.notification_retries }} 次</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">操作闭环率</span>
          <strong>{{ data.operation_closure_rate_pct }}%</strong>
          <span>{{ data.workflows_verified }}/{{ data.workflows_executed }} 次已闭环</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">自动补全信息</span>
          <strong>{{ data.auto_completion_rate_pct }}%</strong>
          <span>{{ data.alerts_with_context }}/{{ data.alerts_total }} 条告警含上下文</span>
        </div>
      </section>

      <section class="panel trend-panel">
        <div class="panel-header">
          <div>
            <h3>近 {{ Math.min(days, 14) }} 天处理趋势</h3>
            <p>告警产生、解决和诊断次数</p>
          </div>
          <div class="legend">
            <span><i class="dot dot-alert" />告警</span>
            <span><i class="dot dot-resolved" />解决</span>
            <span><i class="dot dot-diagnosis" />诊断</span>
          </div>
        </div>
        <div class="trend-chart">
          <div v-for="item in recentTrend" :key="item.date" class="trend-day">
            <div class="bars">
              <span class="bar bar-alert" :style="{ height: barHeight(item.alerts) }" :title="`告警 ${item.alerts}`" />
              <span class="bar bar-resolved" :style="{ height: barHeight(item.resolved) }" :title="`解决 ${item.resolved}`" />
              <span class="bar bar-diagnosis" :style="{ height: barHeight(item.diagnoses) }" :title="`诊断 ${item.diagnoses}`" />
            </div>
            <span class="trend-date">{{ shortDate(item.date) }}</span>
          </div>
        </div>
      </section>

      <section class="outcome-grid">
        <div class="panel outcome-panel">
          <h3>平台拦截效果</h3>
          <div class="outcome-row"><span>重复请求已拦截</span><strong>{{ data.duplicate_requests_blocked }}</strong></div>
          <div class="outcome-row"><span>重复请求占比</span><strong>{{ data.duplicate_request_rate_pct }}%</strong></div>
          <div class="outcome-row"><span>无效服务配置已拦截</span><strong>{{ data.invalid_configs_blocked }}</strong></div>
        </div>
        <div class="panel outcome-panel">
          <h3>修复执行闭环</h3>
          <div class="outcome-row"><span>执行次数</span><strong>{{ data.workflow_executions }}</strong></div>
          <div class="outcome-row"><span>执行成功率</span><strong>{{ data.workflow_success_rate_pct }}%</strong></div>
          <div class="outcome-row"><span>外部通知发送</span><strong>{{ data.notification_deliveries }}</strong></div>
        </div>
      </section>

      <section class="panel system-panel">
        <div class="panel-header">
          <div>
            <h3>系统处理情况</h3>
            <p>优先关注未解决告警较多的系统</p>
          </div>
        </div>
        <a-table
          :data-source="data.systems"
          :pagination="false"
          row-key="system_id"
          size="middle"
          :scroll="{ x: 680 }"
        >
          <a-table-column title="系统" data-index="system_name" />
          <a-table-column title="告警" data-index="alerts" width="100" />
          <a-table-column title="未解决" data-index="unresolved" width="100" />
          <a-table-column title="解决率" width="120">
            <template #default="{ record }">{{ record.resolved_rate_pct }}%</template>
          </a-table-column>
          <a-table-column title="平均解决时间" width="160">
            <template #default="{ record }">{{ formatMinutes(record.avg_resolution_minutes) }}</template>
          </a-table-column>
        </a-table>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page-header { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; margin-bottom:20px; }
.page-title { color:var(--text); font-size:22px; font-weight:700; margin-bottom:2px; }
.page-sub { color:var(--text-subtle); font-size:13px; }
.filters { display:flex; align-items:center; gap:10px; }
.system-select { width:190px; }
.metric-grid { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); border:1px solid var(--border-color); border-radius:8px; background:var(--card-bg); margin-bottom:16px; }
.metric-item { display:flex; flex-direction:column; min-width:0; padding:18px; border-right:1px solid var(--border-color); border-bottom:1px solid var(--border-color); }
.metric-item:nth-child(3n) { border-right:0; }
.metric-item:nth-child(n+4) { border-bottom:0; }
.metric-label { color:var(--text-subtle); font-size:12px; font-weight:600; }
.metric-item strong { color:var(--text); font-size:24px; line-height:1.25; margin:8px 0 5px; }
.metric-item > span:last-child { color:var(--text-subtle); font-size:12px; line-height:1.5; }
.panel { border:1px solid var(--border-color); border-radius:8px; background:var(--card-bg); }
.panel-header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:16px 18px; border-bottom:1px solid var(--border-color); }
.panel h3 { color:var(--text); font-size:15px; font-weight:700; margin:0; }
.panel-header p { color:var(--text-subtle); font-size:12px; margin-top:3px; }
.legend { display:flex; gap:14px; color:var(--text-subtle); font-size:12px; }
.legend span { display:flex; align-items:center; gap:5px; }
.dot { width:7px; height:7px; border-radius:2px; }
.dot-alert,.bar-alert { background:#ef4444; }
.dot-resolved,.bar-resolved { background:#22c55e; }
.dot-diagnosis,.bar-diagnosis { background:#3b82f6; }
.trend-chart { display:flex; align-items:stretch; gap:8px; height:220px; padding:22px 18px 14px; overflow-x:auto; }
.trend-day { display:flex; flex:1; min-width:42px; flex-direction:column; align-items:center; }
.bars { display:flex; align-items:flex-end; justify-content:center; gap:3px; width:100%; height:165px; border-bottom:1px solid var(--border-color); }
.bar { width:min(9px, 24%); min-height:2px; border-radius:3px 3px 0 0; opacity:.85; }
.trend-date { color:var(--text-subtle); font-size:10px; margin-top:7px; }
.outcome-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:16px 0; }
.outcome-panel { padding:16px 18px; }
.outcome-panel h3 { margin-bottom:10px; }
.outcome-row { display:flex; justify-content:space-between; gap:12px; padding:10px 0; border-top:1px solid var(--border-color); color:var(--text-subtle); font-size:13px; }
.outcome-row strong { color:var(--text); font-size:14px; }
.system-panel { overflow:hidden; }
@media (max-width: 900px) {
  .page-header { align-items:stretch; flex-direction:column; }
  .filters { flex-wrap:wrap; }
  .system-select { flex:1; min-width:180px; }
  .metric-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
  .metric-item:nth-child(3n) { border-right:1px solid var(--border-color); }
  .metric-item:nth-child(2n) { border-right:0; }
  .metric-item:nth-child(n+4) { border-bottom:1px solid var(--border-color); }
  .metric-item:nth-child(n+5) { border-bottom:0; }
}
@media (max-width: 620px) {
  .filters > * { width:100%; }
  .metric-grid,.outcome-grid { grid-template-columns:1fr; }
  .metric-item,.metric-item:nth-child(3n),.metric-item:nth-child(2n),.metric-item:nth-child(n+5) { border-right:0; border-bottom:1px solid var(--border-color); }
  .metric-item:last-child { border-bottom:0; }
  .outcome-grid { gap:12px; }
  .legend { flex-wrap:wrap; gap:6px 10px; }
}
</style>
