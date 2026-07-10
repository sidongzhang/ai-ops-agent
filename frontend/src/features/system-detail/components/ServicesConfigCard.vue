<script setup>
defineProps({
  services: { type: Array, default: () => [] },
  serviceColors: { type: Object, required: true },
})

const emit = defineEmits(['add-service', 'delete-service'])

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
</script>

<template>
  <a-card class="panel-card" title="已注册服务">
    <template #extra>
      <a-button type="primary" size="small" @click="emit('add-service')">+ 添加服务</a-button>
    </template>
    <div class="svc-list">
      <div v-for="service in services" :key="service.id" class="svc-row">
        <div class="svc-row-left">
          <span class="svc-dot" :style="{ background: serviceColors[service.connector] || '#6B7280' }" />
          <span class="svc-row-name">{{ service.name }}</span>
          <a-tag style="border:none;font-size:11px">{{ service.connector }}</a-tag>
          <a-tag v-if="!service.enabled" color="warning" style="border:none;font-size:11px">
            {{ service.probe_status === 'failed' ? '测试失败' : service.probe_status === 'passed' ? '待启用' : '草稿' }}
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
  </a-card>
</template>

<style scoped>
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
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
