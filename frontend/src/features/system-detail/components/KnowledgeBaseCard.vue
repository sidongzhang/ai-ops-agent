<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
})

const router = useRouter()
const loading = ref(false)
const docs = ref([])

const totalSizeKb = computed(() => Math.max(0, Math.round(docs.value.reduce((sum, doc) => sum + doc.size, 0) / 1024)))
const recentDocs = computed(() => docs.value.slice(0, 3))

async function loadDocs() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/knowledge/docs`)
    docs.value = data
  } catch (error) {
    message.error(error?.response?.data?.detail || '知识库加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadDocs)
</script>

<template>
  <a-card class="panel-card config-section-card" title="系统知识库">
    <template #extra>
      <a-button size="small" type="primary" @click="router.push(`/systems/${systemId}/knowledge`)">
        进入知识库
      </a-button>
    </template>

    <div class="summary-copy">
      为当前系统维护操作手册、故障记录和业务说明，诊断时会优先参考这些内容。
    </div>

    <a-spin v-if="loading" style="display:block;margin:16px auto" />
    <div v-else-if="docs.length === 0" class="empty-copy">
      还没有知识文档。上传操作手册或在线编写后，诊断时就能直接参考。
    </div>
    <div v-else class="summary-body">
      <div class="summary-stats">
        <span>{{ docs.length }} 篇文档</span>
        <span>{{ totalSizeKb }} KB</span>
      </div>
      <div v-if="recentDocs.length" class="recent-docs">
        <div v-for="doc in recentDocs" :key="doc.name" class="recent-doc">{{ doc.name }}</div>
        <div v-if="docs.length > recentDocs.length" class="recent-more">还有 {{ docs.length - recentDocs.length }} 篇…</div>
      </div>
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  margin-top: 16px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
}
.summary-copy,
.empty-copy,
.recent-more {
  color: var(--text-subtle);
  font-size: 13px;
  line-height: 1.7;
}
.summary-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.summary-stats {
  display: flex;
  gap: 16px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.recent-docs {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.recent-doc {
  padding: 8px 10px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--body-bg);
  font-size: 12px;
  color: var(--text);
}
</style>
