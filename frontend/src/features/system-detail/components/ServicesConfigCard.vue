<script setup>
defineProps({
  services: { type: Array, default: () => [] },
  serviceColors: { type: Object, required: true },
})

const emit = defineEmits(['add-service', 'delete-service'])
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
        </div>
        <div class="svc-row-right">
          <code class="config-code">{{ JSON.stringify(service.config) }}</code>
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
  padding: 9px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--body-bg);
  transition: border-color .15s;
}
.svc-row:hover { border-color: var(--primary); }
.svc-row-left { display: flex; align-items: center; gap: 8px; }
.svc-row-right { display: flex; align-items: center; gap: 10px; }
.svc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.svc-row-name { font-size: 13px; font-weight: 600; color: var(--text); }
.svc-empty { text-align: center; padding: 20px; color: var(--text-subtle); font-size: 13px; }
.config-code {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 11px;
  background: var(--body-bg);
  border: 1px solid var(--border-color);
  padding: 2px 6px;
  border-radius: 4px;
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
.del-svc-btn:hover { opacity: 1; color: #ef4444; }
</style>
