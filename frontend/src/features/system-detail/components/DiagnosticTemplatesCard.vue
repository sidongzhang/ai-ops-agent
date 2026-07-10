<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
})

const templates = ref([])
const loading = ref(false)
const savingName = ref('')

const enabledCount = computed(() => templates.value.filter((item) => item.enabled).length)

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
  <a-card class="panel-card template-card">
    <template #title>
      <div class="card-title">
        <span>诊断模板</span>
        <a-tag color="blue" style="border:none;margin:0">{{ enabledCount }}/{{ templates.length }} 已启用</a-tag>
      </div>
    </template>

    <a-spin v-if="loading" style="display:block;margin:34px 0;text-align:center" />
    <div v-else class="template-list">
      <div v-for="item in templates" :key="item.name" class="template-row">
        <div class="template-main">
          <div class="template-name">{{ item.description }}</div>
          <div class="template-trigger">
            适用问题：{{ item.triggers.slice(0, 5).join('、') }}
          </div>
        </div>
        <a-switch
          :checked="item.enabled"
          :loading="savingName === item.name"
          :disabled="!!savingName && savingName !== item.name"
          @change="(enabled) => toggleTemplate(item, enabled)"
        />
      </div>
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  margin-top: 16px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.template-list {
  display: flex;
  flex-direction: column;
}
.template-row {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
  padding: 13px 0;
  border-bottom: 1px solid var(--border-color);
}
.template-row:first-child {
  padding-top: 0;
}
.template-row:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}
.template-main {
  flex: 1;
  min-width: 0;
}
.template-name {
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
}
.template-trigger {
  margin-top: 4px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 720px) {
  .template-trigger {
    white-space: normal;
  }
}
</style>
