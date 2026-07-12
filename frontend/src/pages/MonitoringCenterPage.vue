<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import api from '../api'

const router = useRouter()
const loading = ref(false)
const systems = ref([])
const health = ref({})
const activeFilter = ref('all')
const lastRefreshed = ref(null)
const incidentSummary = ref(null)

const healthyCount = computed(() => Object.values(health.value).filter((item) => item?.healthy).length)
const alertCount = computed(() => Object.values(health.value).filter((item) => item && !item.healthy).length)
const pausedCount = computed(() => systems.value.filter((system) => !system.monitoring?.enabled).length)

const filterOptions = computed(() => [
  { key: 'all', label: '全部', count: systems.value.length },
  { key: 'healthy', label: '正常', count: healthyCount.value },
  { key: 'alert', label: '异常', count: alertCount.value },
  { key: 'paused', label: '暂停', count: pausedCount.value },
])

const filteredSystems = computed(() => {
  if (activeFilter.value === 'healthy') {
    return systems.value.filter((system) => health.value[system.id]?.healthy)
  }
  if (activeFilter.value === 'alert') {
    return systems.value.filter((system) => health.value[system.id] && !health.value[system.id]?.healthy)
  }
  if (activeFilter.value === 'paused') {
    return systems.value.filter((system) => !system.monitoring?.enabled)
  }
  return systems.value
})

function intervalText(seconds) {
  if (seconds < 60) return `${seconds} 秒`
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`
  return `${Math.round(seconds / 60)} 分钟`
}

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function load() {
  loading.value = true
  try {
    const [{ data }, { data: summary }] = await Promise.all([
      api.get('/systems'),
      api.get('/incidents/summary').catch(() => ({ data: null })),
    ])
    systems.value = data
    incidentSummary.value = summary
    const entries = await Promise.all(data.map(async (system) => {
      try {
        const response = await api.get(`/systems/${system.id}/health`)
        return [system.id, response.data]
      } catch {
        return [system.id, { healthy: false, services: [], source: 'unavailable' }]
      }
    }))
    health.value = Object.fromEntries(entries)
    lastRefreshed.value = new Date().toISOString()
  } catch (error) {
    message.error(error?.response?.data?.detail || '巡检数据加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="monitoring-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">巡检中心</h2>
        <p class="page-sub">
          集中查看所有已注册系统的运行状态和巡检安排
          <span v-if="lastRefreshed" class="refresh-note">· 上次刷新 {{ formatTime(lastRefreshed) }}</span>
        </p>
      </div>
      <div class="header-actions">
        <a-button v-if="incidentSummary?.open" type="link" @click="router.push('/incidents')">
          {{ incidentSummary.open }} 个进行中事故
        </a-button>
        <a-button :loading="loading" @click="load">刷新状态</a-button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-card">
        <span class="summary-label">已注册系统</span>
        <strong class="summary-value">{{ systems.length }}</strong>
        <span class="summary-detail">当前纳入巡检视图的系统总数</span>
      </div>
      <div class="summary-card summary-card--ok">
        <span class="summary-label">当前正常</span>
        <strong class="summary-value ok">{{ healthyCount }}</strong>
        <span class="summary-detail">最近一次巡检结果健康的系统</span>
      </div>
      <div class="summary-card summary-card--warn">
        <span class="summary-label">需要关注</span>
        <strong class="summary-value warn">{{ alertCount }}</strong>
        <span class="summary-detail">存在异常或采集失败的系统</span>
      </div>
      <div class="summary-card summary-card--alert">
        <span class="summary-label">进行中事故</span>
        <strong class="summary-value warn">{{ incidentSummary?.open || 0 }}</strong>
        <span class="summary-detail">
          <button v-if="incidentSummary?.open" type="button" class="inline-link" @click="router.push('/incidents')">
            前往消息中心处理
          </button>
          <template v-else>暂无未关闭事故</template>
        </span>
      </div>
      <div class="summary-card">
        <span class="summary-label">暂停巡检</span>
        <strong class="summary-value">{{ pausedCount }}</strong>
        <span class="summary-detail">已关闭自动巡检的系统</span>
      </div>
    </div>

    <div class="filter-row">
      <button
        v-for="option in filterOptions"
        :key="option.key"
        :class="['filter-chip', { active: activeFilter === option.key }]"
        @click="activeFilter = option.key"
      >
        {{ option.label }}
        <span v-if="option.count" class="chip-count">{{ option.count }}</span>
      </button>
    </div>

    <a-spin v-if="loading && !systems.length" class="page-spin" />

    <div v-else-if="!systems.length" class="empty-state">
      <div class="empty-icon">巡</div>
      <div class="empty-title">还没有注册系统</div>
      <div class="empty-sub">系统接入后，这里会集中展示巡检状态和服务健康结果</div>
    </div>

    <div v-else-if="filteredSystems.length === 0" class="empty-state compact">
      <div class="empty-title">当前筛选下暂无系统</div>
      <div class="empty-sub">切换其他筛选条件，或刷新巡检状态</div>
    </div>

    <div v-else class="system-grid">
      <article
        v-for="system in filteredSystems"
        :key="system.id"
        :class="['system-card', health[system.id]?.healthy ? 'system-card--ok' : 'system-card--warn']"
      >
        <div class="system-card-head">
          <div>
            <div class="system-title">{{ system.name }}</div>
            <div class="system-meta">
              <span>{{ system.local ? '本机托管' : '远程采集器' }}</span>
              <span>{{ system.services.length }} 个已登记服务</span>
            </div>
          </div>
          <a-tag :color="health[system.id]?.healthy ? 'success' : 'error'" class="status-tag">
            {{ health[system.id]?.healthy ? '正常' : '异常' }}
          </a-tag>
        </div>

        <div class="system-meta">
          <span>巡检</span>
          <span>{{ system.monitoring?.enabled ? `每 ${intervalText(system.monitoring.interval_seconds)}` : '已暂停' }}</span>
        </div>

        <div class="service-list">
          <div v-for="item in (health[system.id]?.services || [])" :key="item.name" class="service-row">
            <span :class="['status-dot', item.ok ? 'ok' : 'bad']" />
            <span class="service-name">{{ item.name }}</span>
            <span class="service-detail" :title="item.detail">{{ item.detail }}</span>
          </div>
          <div v-if="!health[system.id]?.services?.length" class="service-empty">暂无已启用服务</div>
        </div>

        <div class="system-card-footer">
          <a-button type="link" size="small" @click="router.push(`/systems/${system.id}`)">进入系统</a-button>
          <a-button type="link" size="small" @click="router.push('/messages')">查看消息</a-button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.monitoring-page { max-width: 1120px; }
.page-header { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:24px; gap:16px; }
.header-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.page-title { font-size:22px; font-weight:700; color:var(--text); margin-bottom:2px; }
.page-sub { font-size:13px; color:var(--text-subtle); }
.refresh-note { color: var(--text-subtle); }
.summary-grid { display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:12px; margin-bottom:16px; }
.summary-card { border:1px solid var(--border-color); border-radius:14px; background:var(--card-bg); padding:14px 16px; min-height:98px; }
.summary-card--ok { border-color: color-mix(in srgb, var(--primary) 28%, var(--border-color)); background: color-mix(in srgb, var(--primary) 5%, var(--card-bg)); }
.summary-card--warn { border-color: color-mix(in srgb, #d97706 28%, var(--border-color)); background: color-mix(in srgb, #d97706 5%, var(--card-bg)); }
.summary-card--alert { border-color: color-mix(in srgb, #dc2626 24%, var(--border-color)); background: color-mix(in srgb, #dc2626 4%, var(--card-bg)); }
.inline-link {
  border: none; background: none; padding: 0; color: var(--primary);
  font-size: 12px; cursor: pointer; text-decoration: underline;
}
.summary-label { display:block; color:var(--text-subtle); font-size:12px; margin-bottom:6px; }
.summary-value { display:block; color:var(--text); font-size:24px; line-height:1; font-weight:700; }
.summary-value.ok { color: var(--primary); }
.summary-value.warn { color:#9a6b3a; }
.summary-detail { display:block; margin-top:10px; color:var(--text-subtle); font-size:12px; line-height:1.5; }
.filter-row { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
.filter-chip {
  display:inline-flex; align-items:center; gap:6px;
  border:1px solid var(--border-color); background:var(--card-bg);
  color:var(--text-subtle); border-radius:999px; padding:6px 14px;
  font-size:13px; cursor:pointer;
}
.filter-chip.active { border-color:var(--primary); color:var(--primary); background:color-mix(in srgb, var(--primary) 8%, transparent); }
.chip-count {
  min-width:18px; height:18px; padding:0 5px; border-radius:999px;
  background:color-mix(in srgb, var(--primary) 14%, transparent);
  color:var(--primary); font-size:11px; font-weight:700; line-height:18px; text-align:center;
}
.page-spin { display:block; margin:60px auto; }
.system-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; }
.system-card {
  border:1px solid var(--border-color);
  border-radius:14px;
  background:var(--card-bg);
  padding:16px;
  border-left:4px solid transparent;
}
.system-card--ok { border-left-color: var(--primary); }
.system-card--warn { border-left-color:#dc2626; }
.system-card-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:10px; }
.system-title { font-size:16px; font-weight:700; color:var(--text); margin-bottom:4px; }
.status-tag { border:none !important; margin:0 !important; }
.system-meta { display:flex; justify-content:space-between; color:var(--text-subtle); font-size:12px; margin-bottom:8px; }
.service-list { margin-top:12px; padding-top:10px; border-top:1px dashed var(--border-color); min-height:120px; }
.service-row { display:flex; align-items:center; gap:7px; padding:6px 0; font-size:12px; }
.status-dot { width:7px; height:7px; border-radius:50%; flex:0 0 auto; }
.status-dot.ok { background: var(--primary); }
.status-dot.bad { background:#a65d55; }
.service-name { font-weight:600; min-width:80px; }
.service-detail { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-subtle); }
.service-empty { color:var(--text-subtle); font-size:13px; text-align:center; padding:24px 0; }
.system-card-footer { display:flex; justify-content:flex-end; gap:4px; margin-top:12px; padding-top:10px; border-top:1px solid color-mix(in srgb, var(--border-color) 60%, transparent); }
.empty-state { text-align:center; padding:88px 0; }
.empty-state.compact { padding:48px 0; }
.empty-icon {
  width:56px; height:56px; margin:0 auto 16px; border-radius:14px;
  border:1px solid var(--border-color);
  background:color-mix(in srgb, var(--card-bg) 86%, var(--primary-bg));
  color:var(--text-subtle); font-size:24px; font-weight:600;
  display:flex; align-items:center; justify-content:center;
}
.empty-title { font-size:18px; font-weight:700; color:var(--text); margin-bottom:6px; }
.empty-sub { color:var(--text-subtle); font-size:13px; }
@media (max-width: 1100px) {
  .summary-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width:720px) {
  .page-header { align-items:flex-start; flex-direction:column; }
  .summary-grid { grid-template-columns:1fr; }
}
</style>
