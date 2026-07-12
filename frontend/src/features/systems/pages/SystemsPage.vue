<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import api from '../../../api'
import { CreateSystemModal } from '../components'
import { QUICK_START_STEPS } from '../../../composables/useOnboarding'

const router = useRouter()
const systems = ref([])
const loading = ref(false)
const modalOpen = ref(false)
const deletingId = ref(null)
const summaryCards = computed(() => {
  const total = systems.value.length
  const local = systems.value.filter((system) => system.local).length
  const remote = total - local
  const services = systems.value.reduce((count, system) => count + system.services.length, 0)
  return [
    { label: '已接入系统', value: total, detail: '当前已登记的业务系统总数' },
    { label: '平台本机托管', value: local, detail: '由平台直接探测和执行操作' },
    { label: '远程采集系统', value: remote, detail: '依赖采集器上报和受控执行' },
    { label: '已登记服务', value: services, detail: '前端、后端、数据库等全部服务' },
  ]
})

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/systems')
    systems.value = data
  } catch { message.error('加载失败') }
  finally { loading.value = false }
}
onMounted(load)

function openCreate() {
  modalOpen.value = true
}

function onSystemCreated(system) {
  load()
  if (system?.id) {
    router.push({ path: `/systems/${system.id}`, query: { tab: 'config' } })
  }
}

function getSystemNextStep(system) {
  if (system.services.length === 0) return '先补充前端、后端、数据库等服务'
  if (system.local) return '进入详情页测试服务并启用监控'
  return '进入详情页创建采集器并让对方机器上线'
}

function getSystemAccessProgress(system) {
  const hasServices = (system.services?.length || 0) > 0
  const enabledServices = system.services?.filter((service) => service.enabled !== false).length || 0
  const collectorReady = system.local ? true : Boolean(system.restart_capability?.collector_online)
  const monitoringReady = Boolean(system.monitoring?.enabled)
  const notifyReady = (system.notify?.type && system.notify.type !== 'none') || (system.notify?.channels?.length || 0) > 0
  const steps = [hasServices, system.local ? enabledServices > 0 : collectorReady, monitoringReady && notifyReady]
  return Math.round((steps.filter(Boolean).length / steps.length) * 100)
}

function getPrimaryAction(system) {
  if (system.services.length === 0) return { label: '去登记服务', query: { tab: 'config', step: 'services' } }
  if (!system.local && !system.restart_capability?.collector_online) return { label: '去部署采集器', query: { tab: 'config', step: 'collector' } }
  if (!system.local && !system.restart_capability?.enabled) return { label: '补充远程控制', query: { tab: 'config', step: 'remote-actions' } }
  if (!system.monitoring?.enabled || !((system.notify?.type && system.notify.type !== 'none') || (system.notify?.channels?.length || 0) > 0)) {
    return { label: '开启巡检告警', query: { tab: 'config', step: 'monitoring' } }
  }
  return { label: '查看总览', query: { tab: 'overview' } }
}

async function destroySystem(system) {
  deletingId.value = system.id
  try {
    await api.delete(`/systems/${system.id}`)
    message.success(`已销毁「${system.name}」`)
    await load()
  } catch (error) {
    message.error(error?.response?.data?.detail || '销毁失败')
  } finally {
    deletingId.value = null
  }
}
</script>

<template>
  <div>
    <!-- 页头 -->
    <div class="page-header">
      <div>
        <h2 class="page-title">监控系统</h2>
        <p class="page-sub">管理所有已接入的被监控系统</p>
      </div>
      <a-button type="primary" size="large" @click="openCreate">+ 注册系统</a-button>
    </div>

    <!-- 空状态 -->
    <div v-if="!loading && systems.length === 0" class="empty-state">
      <div class="empty-icon">系</div>
      <div class="empty-title">3 分钟完成首次接入</div>
      <div class="empty-sub">按下面步骤注册系统，即可开始监控、巡检和 AI 诊断</div>
      <div class="quick-start-grid">
        <div v-for="(step, index) in QUICK_START_STEPS" :key="step.title" class="quick-start-card">
          <div class="quick-start-index">{{ index + 1 }}</div>
          <div class="quick-start-body">
            <div class="quick-start-title">{{ step.title }}</div>
            <div class="quick-start-detail">{{ step.detail }}</div>
            <div class="quick-start-duration">约 {{ step.duration }}</div>
          </div>
        </div>
      </div>
      <a-button type="primary" @click="openCreate" style="margin-top:16px">注册第一个系统</a-button>
    </div>

    <div v-else-if="systems.length" class="summary-grid">
      <div v-for="card in summaryCards" :key="card.label" class="summary-card">
        <div class="summary-label">{{ card.label }}</div>
        <div class="summary-value">{{ card.value }}</div>
        <div class="summary-detail">{{ card.detail }}</div>
      </div>
    </div>

    <!-- 系统卡片 -->
    <div class="systems-grid">
      <div
        v-for="sys in systems" :key="sys.id"
        class="sys-card"
      >
        <div class="sys-card-inner">
          <div class="sys-head">
            <span class="sys-name">{{ sys.name }}</span>
            <a-tag :color="sys.local ? 'success' : 'processing'" style="border:none;margin:0">
              {{ sys.local ? '平台托管' : (sys.restart_capability?.enabled ? '远程可控' : '远程采集') }}
            </a-tag>
          </div>
          <div class="sys-key">{{ sys.key }}</div>
          <div class="sys-progress-row">
            <span class="sys-progress-label">接入进度</span>
            <span class="sys-progress-value">{{ getSystemAccessProgress(sys) }}%</span>
          </div>
          <a-progress
            :percent="getSystemAccessProgress(sys)"
            size="small"
            :show-info="false"
            :stroke-color="getSystemAccessProgress(sys) === 100 ? '#557568' : undefined"
            class="sys-progress-bar"
          />
          <div class="sys-next-step">{{ getSystemNextStep(sys) }}</div>
          <div class="sys-foot">
            <div class="sys-foot-left">
              <span class="sys-count">{{ sys.services.length }} 个服务</span>
              <a-popconfirm
                v-if="sys.services.length === 0"
                title="确认销毁这个空系统吗？"
                ok-text="销毁"
                ok-type="danger"
                cancel-text="取消"
                @confirm.stop="destroySystem(sys)"
              >
                <a-button
                  type="text"
                  danger
                  size="small"
                  class="destroy-btn"
                  :loading="deletingId === sys.id"
                  @click.stop
                >
                  销毁
                </a-button>
              </a-popconfirm>
            </div>
            <div class="sys-actions">
              <a-button
                size="small"
                @click.stop="router.push({ path: `/systems/${sys.id}`, query: getPrimaryAction(sys).query })"
              >
                {{ getPrimaryAction(sys).label }}
              </a-button>
              <button class="sys-goto" @click.stop="router.push(`/systems/${sys.id}`)">查看详情 →</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <CreateSystemModal v-model:open="modalOpen" @created="onSystemCreated" />
  </div>
</template>

<style scoped>
/* ── 页头 ── */
.page-header {
  display: flex; justify-content: space-between; align-items: flex-end;
  margin-bottom: 28px;
}
.page-title { font-size: 22px; font-weight: 700; color: var(--text); margin-bottom: 2px; }
.page-sub   { font-size: 13px; color: var(--text-subtle); }

/* ── 空状态 ── */
.empty-state { text-align: center; padding: 80px 0; }
.empty-icon  {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  border-radius: 14px;
  border: 1px solid var(--border-color);
  background: color-mix(in srgb, var(--card-bg) 86%, var(--primary-bg));
  color: var(--text-subtle);
  font-size: 24px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.empty-title { font-size: 18px; font-weight: 600; color: var(--text); margin-bottom: 6px; }
.empty-sub   { font-size: 14px; color: var(--text-subtle); max-width: 520px; margin: 0 auto; }
.quick-start-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  max-width: 720px;
  margin: 20px auto 0;
  text-align: left;
}
.quick-start-card {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--card-bg);
}
.quick-start-index {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--primary) 10%, transparent);
  color: var(--primary);
  font-weight: 700;
  font-size: 13px;
}
.quick-start-title { font-size: 14px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
.quick-start-detail { font-size: 12px; color: var(--text-subtle); line-height: 1.55; margin-bottom: 6px; }
.quick-start-duration { font-size: 11px; color: var(--primary); font-weight: 600; }
@media (max-width: 640px) { .quick-start-grid { grid-template-columns: 1fr; } }

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.summary-card {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 14px 16px;
  background: var(--card-bg);
  min-height: 98px;
}
.summary-label { font-size: 12px; color: var(--text-subtle); margin-bottom: 6px; }
.summary-value { font-size: 24px; line-height: 1; font-weight: 700; color: var(--text); }
.summary-detail { margin-top: 10px; font-size: 12px; line-height: 1.5; color: var(--text-subtle); }

/* ── 系统卡片网格 ── */
.systems-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.sys-card {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  cursor: pointer;
  transition: box-shadow .18s, transform .18s, border-color .18s;
  overflow: hidden;
}
.sys-card:hover {
  box-shadow: 0 8px 22px rgba(23, 34, 40, .08);
  transform: translateY(-1px);
  border-color: var(--primary);
}
.sys-card-inner { padding: 18px 20px; }
.sys-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.sys-name { font-size: 15px; font-weight: 600; color: var(--text); }
.sys-key  { font-size: 12px; color: var(--text-subtle); font-family: 'SF Mono', Menlo, monospace; margin-bottom: 10px; }
.sys-next-step {
  min-height: 38px;
  margin-bottom: 14px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-subtle);
}
.sys-progress-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.sys-progress-label,
.sys-progress-value {
  font-size: 12px;
  color: var(--text-subtle);
}
.sys-progress-value {
  color: var(--text);
  font-weight: 700;
}
.sys-progress-bar { margin-bottom: 10px; }
.sys-foot { display: flex; justify-content: space-between; align-items: center; gap: 10px; font-size: 13px; padding-top: 12px; border-top: 1px solid var(--border-color); }
.sys-foot-left { display: flex; align-items: center; gap: 8px; }
.sys-count { color: var(--text-subtle); font-weight: 500; }
.sys-actions { display: flex; align-items: center; gap: 8px; }
.sys-goto  { color: var(--primary); font-weight: 500; border: none; background: transparent; cursor: pointer; padding: 0; }
.destroy-btn { padding-inline: 4px; }
@media (max-width: 960px) {
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .page-header { flex-direction: column; align-items: stretch; gap: 12px; }
  .summary-grid { grid-template-columns: 1fr; }
  .sys-foot { flex-direction: column; align-items: stretch; }
  .sys-actions { justify-content: space-between; }
}
</style>
