<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({ id: { type: String, required: true } })

const loading = ref(false)
const memories = ref([])

const VALIDITY_LABELS = {
  confirmed_success: { text: '已验证有效', cls: 'memory-tag--ok' },
  unconfirmed: { text: '待验证', cls: 'memory-tag--warn' },
  failed: { text: '已失效', cls: 'memory-tag--bad' },
}

async function loadMemories() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.id}/knowledge/memories`)
    memories.value = data
  } catch (error) {
    message.error(error?.response?.data?.detail || 'AI 记忆加载失败')
  } finally {
    loading.value = false
  }
}

async function confirmMemory(id) {
  try {
    await api.post(`/systems/knowledge/memories/${id}/confirm`)
    message.success('已确认有效（置信度 1.0，检索权重最高）')
    await loadMemories()
  } catch (error) {
    message.error(error?.response?.data?.detail || '确认失败')
  }
}

async function invalidateMemory(id) {
  try {
    await api.post(`/systems/knowledge/memories/${id}/invalidate`)
    message.info('已标记失效（该经验将不再被检索引用）')
    await loadMemories()
  } catch (error) {
    message.error(error?.response?.data?.detail || '操作失败')
  }
}

onMounted(loadMemories)
</script>

<template>
  <a-card class="memories-card" title="AI 经验记忆">
    <template #extra>
      <a-button size="small" :loading="loading" @click="loadMemories">刷新</a-button>
    </template>
    <p class="memories-hint">
      AI 诊断完成后自动沉淀的结构化经验（症状 → 根因 → 处置）。
      带来源与置信度：人工确认过的经验检索权重最高；回查失败的经验自动失效，防止错误经验循环污染。
    </p>

    <a-empty v-if="!loading && memories.length === 0" description="还没有沉淀的记忆。完成几次诊断后，有效经验会自动出现在这里。"
      :image-style="{ height: '48px' }" />

    <ul class="memory-list">
      <li v-for="m in memories" :key="m.id" class="memory-item">
        <div class="memory-item__head">
          <span class="memory-item__symptom">{{ m.symptom.slice(0, 80) }}</span>
          <span :class="['memory-tag', VALIDITY_LABELS[m.validity]?.cls || 'memory-tag--warn']">
            {{ VALIDITY_LABELS[m.validity]?.text || m.validity }}
          </span>
        </div>
        <div class="memory-item__body">
          <div class="memory-line"><span class="memory-k">根因</span>{{ m.root_cause.slice(0, 120) }}</div>
          <div v-if="m.remedy" class="memory-k" style="display:none"></div>
          <div v-if="m.remedy" class="memory-line"><span class="memory-k">处置</span>{{ m.remedy.slice(0, 120) }}</div>
          <div class="memory-meta">来源 {{ m.source }} · 置信度 {{ Number(m.confidence).toFixed(1) }}</div>
        </div>
        <div class="memory-item__actions">
          <a-button v-if="m.validity !== 'confirmed_success'" size="small" type="primary"
            @click="confirmMemory(m.id)">确认有效</a-button>
          <a-popconfirm v-if="m.validity !== 'failed'" title="标记失效后该经验将不再被检索引用，确认？"
            @confirm="invalidateMemory(m.id)">
            <a-button size="small" danger>标记失效</a-button>
          </a-popconfirm>
        </div>
      </li>
    </ul>
  </a-card>
</template>

<style scoped>
.memories-card { margin-top: 16px; }
.memories-hint { color: var(--text-color-secondary, #888); font-size: 13px; margin-bottom: 12px; }
.memory-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.memory-item { border: 1px solid var(--border-color, #e5e5e5); border-radius: 10px; padding: 12px 14px; }
.memory-item__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.memory-item__symptom { font-weight: 600; font-size: 14px; }
.memory-tag { padding: 1px 8px; border-radius: 10px; font-size: 12px; flex-shrink: 0; }
.memory-tag--ok { background: rgba(82,196,26,.15); color: #389e0d; }
.memory-tag--warn { background: rgba(250,173,20,.15); color: #d48806; }
.memory-tag--failed { background: rgba(255,77,79,.15); color: #cf1322; }
.memory-line { font-size: 13px; margin: 3px 0; display: flex; gap: 6px; align-items: baseline; }
.memory-k { flex-shrink: 0; font-size: 12px; color: var(--primary, #1677ff); font-weight: 600; }
.memory-meta { font-size: 12px; color: var(--text-color-secondary, #999); margin-top: 4px; }
.memory-item__actions { display: flex; gap: 8px; margin-top: 8px; }
</style>
