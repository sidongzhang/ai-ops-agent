<script setup>
import { defineAsyncComponent, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'
const AddServiceModal = defineAsyncComponent(() => import('../components').then((module) => module.AddServiceModal))
const MetricsTab = defineAsyncComponent(() => import('../components').then((module) => module.MetricsTab))
const NotifyConfigCard = defineAsyncComponent(() => import('../components').then((module) => module.NotifyConfigCard))
const ServicesConfigCard = defineAsyncComponent(() => import('../components').then((module) => module.ServicesConfigCard))
const DiagnosisPanel = defineAsyncComponent(() => import('../../diagnostics').then((module) => module.DiagnosisPanel))
import { useMetricsPolling } from '../../monitoring'
import { useNotifyConfig } from '../../notifications'
import { SERVICE_COLORS } from '../../services'

const addSvcOpen = ref(false)

function openAddSvc() {
  addSvcOpen.value = true
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

const props = defineProps({ id: { type: String, required: true } })

const activeTab = ref('overview')

const system = ref(null)
const health = ref(null)
const healthLoading = ref(false)
const { metrics, metricsLoading } = useMetricsPolling(props.id)
const {
  notifyCfg,
  notifyEditingSecret,
  notifyLoading,
  notifySecretAlreadySet,
  notifyTestLoading,
  onNotifyTypeChange,
  saveNotify,
  syncNotifyFromSystem,
  testNotify,
} = useNotifyConfig(props.id, loadSystem)

async function loadSystem() {
  try {
    const { data } = await api.get(`/systems/${props.id}`)
    system.value = data
    syncNotifyFromSystem(data)
  } catch { message.error('加载系统失败') }
}

async function runHealth() {
  healthLoading.value = true
  try {
    const { data } = await api.get(`/systems/${props.id}/health`)
    health.value = data
  } catch { message.error('探活失败') }
  finally { healthLoading.value = false }
}

onMounted(() => {
  loadSystem()
  runHealth()
})
</script>

<template>
  <div v-if="system">
    <!-- 页头 -->
    <div class="page-header">
      <button class="back-btn" @click="$router.push('/systems')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             width="16" height="16"><polyline points="15 18 9 12 15 6" /></svg>
        返回
      </button>
      <div class="header-info">
        <h2 class="sys-name">{{ system.name }}</h2>
        <a-tag :color="system.local ? 'success' : 'processing'" style="border:none">
          {{ system.local ? '平台托管' : '远程只读' }}
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
        <a-card class="panel-card" title="服务健康状态">
          <template #extra>
            <a-button size="small" :loading="healthLoading" @click="runHealth">重新探活</a-button>
          </template>
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
      </a-tab-pane>

      <!-- ② 监控 -->
      <a-tab-pane key="metrics" tab="监控">
        <MetricsTab :metrics="metrics" :metrics-loading="metricsLoading" />
      </a-tab-pane>

      <!-- ③ 诊断 -->
      <a-tab-pane key="diagnose" tab="AI 诊断">
        <DiagnosisPanel :system-id="props.id" />
      </a-tab-pane>

      <!-- ④ 配置 -->
      <a-tab-pane key="config" tab="配置">
        <ServicesConfigCard
          :services="system.services"
          :service-colors="SERVICE_COLORS"
          @add-service="openAddSvc"
          @delete-service="deleteService"
        />
        <NotifyConfigCard
          :system="system"
          :notify-cfg="notifyCfg"
          :notify-editing-secret="notifyEditingSecret"
          :notify-loading="notifyLoading"
          :notify-secret-already-set="notifySecretAlreadySet"
          :notify-test-loading="notifyTestLoading"
          @update:notifyEditingSecret="notifyEditingSecret = $event; if ($event) notifyCfg.app_secret = ''"
          @notify-type-change="onNotifyTypeChange"
          @save="saveNotify"
          @test="testNotify"
        />
      </a-tab-pane>

    </a-tabs>

    <AddServiceModal v-model:open="addSvcOpen" :system-id="props.id" @created="loadSystem" />
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

</style>
