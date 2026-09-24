<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  services: { type: Array, default: () => [] },
  serviceColors: { type: Object, required: true },
  restartCapability: { type: Object, default: () => ({}) },
  actionLoading: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['add-service', 'delete-service', 'test-service', 'enable-service', 'enable-unavailable-service', 'restart-service'])

const sectionOpen = ref([])

const enabledCount = computed(() => props.services.filter((service) => service.enabled).length)

function joinHostPort(host, port) {
  if (host && port) return `${host}:${port}`
  return host || (port ? `:${port}` : '')
}

function formatServiceEndpoint(service) {
  const config = service.config || {}
  const connector = service.connector

  if (connector === 'tcp') {
    return joinHostPort(config.host, config.port) || 'TCP 已配置'
  }

  if (connector === 'http') {
    return config.health_url || config.url || joinHostPort(config.host, config.port) || 'HTTP 已配置'
  }

  if (connector === 'prometheus') {
    const url = config.url || config.health_url
    const query = config.up_query || config.query
    if (url && query) return `${url} · ${query}`
    return url || query || 'Prometheus 已配置'
  }

  if (connector === 'ssh') {
    const target = joinHostPort(config.host, config.port)
    return [config.user, target].filter(Boolean).join('@') || config.log_path || 'SSH 已配置'
  }

  if (connector === 'local') {
    return config.container || config.process || config.log_file || config.kind || '本地已配置'
  }

  return config.health_url || config.url || joinHostPort(config.host, config.port) || '已配置'
}

function servicePurpose(service) {
  if (service.connector === 'http') return '健康检查'
  if (service.connector === 'prometheus') return '指标采集'
  return ''
}

function canRestart(service, restartCapability) {
  if (!service?.enabled) return false
  if (!restartCapability?.enabled || !restartCapability?.has_permission) return false
  return (restartCapability.services || []).some((item) => item.name === service.name && item.restartable)
}

function restartMeta(service) {
  return (props.restartCapability?.services || []).find((item) => item.name === service.name) || null
}

function restartHint(service) {
  const meta = restartMeta(service)
  if (!service?.enabled) return '服务启用监控后，才能执行受控重启'
  if (!props.restartCapability?.has_permission) return '当前账号没有这个系统的重启权限'
  if (meta?.restartable) {
    const target = meta.container || meta.systemd_unit
    return target ? `受控目标：${target}` : '已具备受控重启能力'
  }
  if (props.restartCapability?.reason) return props.restartCapability.reason
  return '当前服务没有配置容器名或 systemd 单元'
}
</script>

<template>
  <div class="subsection">
    <a-collapse v-model:activeKey="sectionOpen" ghost expand-icon-position="start" class="subsection-collapse">
      <a-collapse-panel key="main">
        <template #header>
          <div class="card-title">
            <span>已注册服务</span>
            <a-tag color="blue" style="border:none;margin:0">{{ services.length }} 个 · {{ enabledCount }} 已启用</a-tag>
          </div>
        </template>
        <template #extra>
          <a-button type="primary" size="small" @click.stop="emit('add-service')">+ 添加服务</a-button>
        </template>

        <div class="svc-list">
          <div v-for="service in services" :key="service.id" class="svc-row">
            <div class="svc-row-left">
              <span class="svc-dot" :style="{ background: serviceColors[service.connector] || '#6B7280' }" />
              <span class="svc-row-name">{{ service.name }}</span>
              <a-tag style="border:none;font-size:11px">{{ service.connector }}</a-tag>
              <a-tag v-if="!service.enabled" color="warning" style="border:none;font-size:11px">
                {{ service.probe_status === 'failed' ? '测试失败' : service.probe_status === 'passed' ? '待启用' : service.probe_status === 'waiting_collector' ? '等待采集器' : '草稿' }}
              </a-tag>
              <a-tag v-if="servicePurpose(service)" class="purpose-tag">{{ servicePurpose(service) }}</a-tag>
            </div>
            <div class="svc-row-right">
              <span class="endpoint-pill" :title="formatServiceEndpoint(service)">
                {{ formatServiceEndpoint(service) }}
              </span>
              <span v-if="!service.enabled && service.probe_detail" class="probe-detail" :title="service.probe_detail">
                {{ service.probe_detail }}
              </span>
              <a-button v-if="!service.enabled" size="small" @click="emit('test-service', service)">测试</a-button>
              <a-button
                v-if="!service.enabled && service.probe_status === 'passed'"
                size="small"
                type="primary"
                :loading="actionLoading.enable === service.id"
                @click="emit('enable-service', service)"
              >
                启用
              </a-button>
              <a-popconfirm
                v-if="!service.enabled && service.probe_status === 'failed'"
                title="该服务当前不可达。启用后会持续监控并产生异常告警，确认纳入监控？"
                ok-text="纳入监控"
                cancel-text="暂不启用"
                @confirm="emit('enable-unavailable-service', service)"
              >
                <a-button size="small" danger :loading="actionLoading.enable === service.id">
                  仍启用监控
                </a-button>
              </a-popconfirm>
              <a-tooltip v-if="service.enabled" :title="restartHint(service)">
                <a-button
                  size="small"
                  :disabled="!canRestart(service, restartCapability)"
                  :loading="actionLoading.restart === service.id"
                  @click="emit('restart-service', service)"
                >
                  重启
                </a-button>
              </a-tooltip>
              <a-popconfirm title="确认删除这个服务吗？" ok-text="删除" ok-type="danger"
                            cancel-text="取消" @confirm="emit('delete-service', service.id, service.name)">
                <button class="del-svc-btn" title="删除">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /><path d="M9 6V4h6v2" />
                  </svg>
                </button>
              </a-popconfirm>
            </div>
          </div>
          <div v-if="services.length === 0" class="svc-empty">暂无服务，点击「添加服务」开始配置</div>
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
.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.svc-list { display: flex; flex-direction: column; gap: 6px; }
.svc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 9px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--body-bg);
  transition: border-color .15s;
}
.svc-row:hover { border-color: var(--primary); }
.svc-row-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.svc-row-right {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  min-width: 0;
  flex: 1;
}
.svc-row-right :deep(.ant-btn) { flex-shrink: 0; }
.svc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.svc-row-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
}
.svc-empty { text-align: center; padding: 20px; color: var(--text-subtle); font-size: 13px; }
.purpose-tag {
  border: none;
  font-size: 11px;
  color: var(--text-subtle);
  background: color-mix(in srgb, var(--primary) 7%, transparent);
}
.endpoint-pill {
  display: inline-block;
  max-width: min(520px, 56vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text);
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  padding: 4px 10px;
  border-radius: 999px;
}
.del-svc-btn {
  background: none;
  border: none;
  color: var(--text-subtle);
  opacity: .35;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  transition: opacity .15s, color .15s;
}
.probe-detail {
  max-width: 260px;
  color: var(--text-subtle);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.del-svc-btn:hover { opacity: 1; color: #ef4444; }

@media (max-width: 720px) {
  .svc-row {
    align-items: stretch;
    flex-direction: column;
    gap: 8px;
  }
  .svc-row-left,
  .svc-row-right {
    width: 100%;
  }
  .svc-row-left {
    flex-wrap: wrap;
  }
  .svc-row-right {
    justify-content: space-between;
  }
  .endpoint-pill {
    max-width: calc(100vw - 150px);
  }
}
</style>
