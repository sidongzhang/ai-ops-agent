<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({ id: { type: String, required: true } })

const router = useRouter()
const loading = ref(false)
const uploading = ref(false)
const saving = ref(false)
const searching = ref(false)
const previewLoading = ref(false)
const docs = ref([])
const fileList = ref([])
const query = ref('')
const testQuery = ref('')
const searchResult = ref('')
const activeDoc = ref('')
const activeContent = ref('')
const form = ref({ name: '', content: '' })

const filteredDocs = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return docs.value
  return docs.value.filter((doc) => doc.name.toLowerCase().includes(q))
})

const totalSizeKb = computed(() => Math.max(0, Math.round(docs.value.reduce((sum, doc) => sum + doc.size, 0) / 1024)))
const stats = computed(() => [
  { label: '知识文档', value: docs.value.length, desc: '当前系统专属知识源' },
  { label: '总大小', value: `${totalSizeKb.value} KB`, desc: '已上传文档体积' },
  { label: '使用方式', value: '诊断参考', desc: '诊断时会优先参考知识库' },
])

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function loadDocs() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.id}/knowledge/docs`)
    docs.value = data
    if (!activeDoc.value && data.length) {
      activeDoc.value = data[0].name
    } else if (activeDoc.value && !data.some((doc) => doc.name === activeDoc.value)) {
      activeDoc.value = data[0]?.name || ''
    }
  } catch (error) {
    message.error(error?.response?.data?.detail || '知识库加载失败')
  } finally {
    loading.value = false
  }
}

async function loadDocContent(name) {
  if (!name) {
    activeContent.value = ''
    return
  }
  previewLoading.value = true
  try {
    const { data } = await api.get(`/systems/${props.id}/knowledge/docs/${encodeURIComponent(name)}`)
    activeContent.value = data.content || ''
  } catch (error) {
    activeContent.value = ''
    message.error(error?.response?.data?.detail || '文档读取失败')
  } finally {
    previewLoading.value = false
  }
}

async function saveDoc() {
  if (!form.value.name.trim() || !form.value.content.trim()) {
    return message.warning('请填写标题和内容')
  }
  saving.value = true
  try {
    const savedName = form.value.name.trim()
    await api.post(`/systems/${props.id}/knowledge/docs`, {
      name: savedName,
      content: form.value.content,
    })
    form.value = { name: '', content: '' }
    message.success('知识文档已保存')
    await loadDocs()
    activeDoc.value = savedName
  } catch (error) {
    message.error(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function uploadFiles() {
  if (!fileList.value.length) return message.warning('请选择要上传的文件')
  uploading.value = true
  try {
    const formData = new FormData()
    for (const file of fileList.value) formData.append('files', file)
    await api.post(`/systems/${props.id}/knowledge/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    message.success('文档已上传到知识库')
    fileList.value = []
    await loadDocs()
  } catch (error) {
    message.error(error?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function runSearchTest() {
  if (!testQuery.value.trim()) return message.warning('请输入要测试的问题')
  searching.value = true
  try {
    const { data } = await api.get(`/systems/${props.id}/knowledge/search`, { params: { q: testQuery.value } })
    searchResult.value = data.context || '未检索到相关知识片段'
  } catch (error) {
    message.error(error?.response?.data?.detail || '检索测试失败')
  } finally {
    searching.value = false
  }
}

async function removeDoc(name) {
  try {
    await api.delete(`/systems/${props.id}/knowledge/docs/${encodeURIComponent(name)}`)
    message.success('已删除文档')
    if (activeDoc.value === name) activeDoc.value = ''
    await loadDocs()
  } catch (error) {
    message.error(error?.response?.data?.detail || '删除失败')
  }
}

function onFileChange(event) {
  fileList.value = Array.from(event.target.files || [])
}

watch(activeDoc, (name) => {
  loadDocContent(name)
})

onMounted(async () => {
  await loadDocs()
  if (activeDoc.value) await loadDocContent(activeDoc.value)
})
</script>

<template>
  <div class="knowledge-page">
    <div class="page-header">
      <button class="back-btn" @click="router.push(`/systems/${id}`)">
        <span aria-hidden="true">←</span>
        返回系统详情
      </button>
      <div class="header-copy">
        <h2 class="page-title">系统知识库</h2>
        <p class="page-sub">上传操作手册、排障文档和业务说明，AI 诊断时会优先参考这些专属知识。</p>
      </div>
    </div>

    <div class="stats-grid">
      <div v-for="item in stats" :key="item.label" class="stat-card">
        <div class="stat-label">{{ item.label }}</div>
        <div class="stat-value">{{ item.value }}</div>
        <div class="stat-desc">{{ item.desc }}</div>
      </div>
    </div>

    <div class="action-grid">
      <section class="panel-card upload-card">
        <div class="card-head">
          <h3 class="card-title">上传知识源</h3>
        </div>
        <div class="card-body">
          <label class="dropzone">
            <div class="dropzone-icon">DOC</div>
            <div class="dropzone-title">点击选择 Markdown / TXT 文件</div>
            <div class="dropzone-sub">推荐上传操作手册、事故复盘、业务依赖说明。单个文件限制 2MB。</div>
            <input class="file-input" type="file" multiple accept=".md,.txt,text/plain,text/markdown" @change="onFileChange" />
          </label>
          <div v-if="fileList.length" class="selected-files">
            <div v-for="file in fileList" :key="file.name" class="selected-file">
              <span class="selected-file-name">{{ file.name }}</span>
              <span>{{ formatSize(file.size) }}</span>
            </div>
          </div>
          <div class="action-row">
            <a-button type="primary" :loading="uploading" @click="uploadFiles">上传并建立索引</a-button>
            <span class="muted">已选择 {{ fileList.length }} 个文件</span>
          </div>
        </div>
      </section>

      <section class="panel-card create-card">
        <div class="card-head">
          <h3 class="card-title">在线编写</h3>
        </div>
        <div class="card-body">
          <p class="helper-copy">直接在平台里写操作手册或故障记录，保存后诊断就能参考。</p>
          <a-input v-model:value="form.name" placeholder="文档标题，例如 Redis 内存排查" />
          <a-textarea
            v-model:value="form.content"
            :rows="6"
            placeholder="写下排障步骤、常见故障、业务背景……"
          />
          <div class="action-row">
            <a-button type="primary" :loading="saving" @click="saveDoc">保存文档</a-button>
          </div>
        </div>
      </section>
    </div>

    <section class="panel-card workspace-card">
      <div class="card-head workspace-head">
        <div>
          <h3 class="card-title">文档管理</h3>
          <p class="helper-copy">左侧选择文档，右侧即时预览内容。</p>
        </div>
        <div class="workspace-tools">
          <a-input v-model:value="query" size="small" placeholder="搜索文档" allow-clear />
          <a-button size="small" :loading="loading" @click="loadDocs">刷新</a-button>
        </div>
      </div>

      <div class="workspace-grid">
        <div class="doc-pane">
          <a-spin v-if="loading" class="pane-spin" />
          <div v-else-if="filteredDocs.length === 0" class="empty-copy">当前还没有匹配的文档。</div>
          <div v-else class="doc-list">
            <button
              v-for="doc in filteredDocs"
              :key="doc.name"
              type="button"
              :class="['doc-item', { active: activeDoc === doc.name }]"
              @click="activeDoc = doc.name"
            >
              <div class="doc-main">
                <div class="doc-name">{{ doc.name }}</div>
                <div class="doc-meta">{{ formatSize(doc.size) }} · 诊断可参考</div>
              </div>
              <a-button type="text" danger size="small" @click.stop="removeDoc(doc.name)">删除</a-button>
            </button>
          </div>
        </div>

        <div class="preview-pane">
          <div class="preview-head">
            <span class="preview-title">{{ activeDoc || '文档预览' }}</span>
          </div>
          <a-spin v-if="previewLoading" class="pane-spin" />
          <pre v-else-if="activeContent" class="doc-preview">{{ activeContent }}</pre>
          <div v-else class="empty-copy">选择左侧文档后查看内容。</div>
        </div>
      </div>
    </section>

    <section class="panel-card test-card">
      <div class="card-head">
        <h3 class="card-title">检索测试</h3>
      </div>
      <div class="card-body">
        <p class="helper-copy">输入一个运维问题，预览知识库会返回哪些参考片段。这些内容会进入后续诊断上下文。</p>
        <a-input-search
          v-model:value="testQuery"
          placeholder="例如：Redis 内存升高时怎么处理？"
          enter-button="测试检索"
          :loading="searching"
          @search="runSearchTest"
        />
        <pre v-if="searchResult" class="search-result">{{ searchResult }}</pre>
      </div>
    </section>
  </div>
</template>

<style scoped>
.knowledge-page {
  max-width: 1120px;
  min-width: 0;
  margin: 0 auto;
}

.page-header {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 18px;
}

.back-btn {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-subtle);
  border-radius: 8px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
}

.back-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}

.header-copy {
  min-width: 0;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 4px;
  color: var(--text);
}

.page-sub {
  color: var(--text-subtle);
  font-size: 13px;
  line-height: 1.6;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.stat-card {
  min-width: 0;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--card-bg);
  padding: 14px 16px;
}

.stat-label {
  font-size: 12px;
  color: var(--text-subtle);
  margin-bottom: 6px;
}

.stat-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
}

.stat-desc {
  margin-top: 6px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.5;
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.panel-card {
  min-width: 0;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--card-bg);
  overflow: hidden;
}

.card-head {
  padding: 14px 16px 0;
}

.workspace-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding-bottom: 0;
}

.card-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}

.card-body {
  padding: 12px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.helper-copy,
.muted,
.empty-copy,
.doc-meta {
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
}

.dropzone {
  display: block;
  border: 1.5px dashed var(--border-color);
  border-radius: 14px;
  background: color-mix(in srgb, var(--body-bg) 82%, var(--card-bg));
  padding: 20px 16px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.dropzone:hover {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 4%, var(--card-bg));
}

.dropzone-icon {
  width: 48px;
  height: 48px;
  margin: 0 auto 10px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--primary-bg);
  color: var(--primary);
  font-weight: 700;
  font-size: 12px;
}

.dropzone-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 6px;
}

.dropzone-sub {
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.6;
}

.file-input {
  display: none;
}

.selected-files {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selected-file {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: color-mix(in srgb, var(--body-bg) 80%, var(--card-bg));
  color: var(--text-subtle);
  font-size: 12px;
}

.selected-file-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}

.action-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.workspace-card {
  margin-bottom: 14px;
}

.workspace-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.workspace-tools :deep(.ant-input) {
  width: 180px;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
  gap: 0;
  min-height: 420px;
  border-top: 1px solid var(--border-color);
}

.doc-pane,
.preview-pane {
  min-width: 0;
  min-height: 420px;
}

.doc-pane {
  border-right: 1px solid var(--border-color);
  padding: 12px;
  overflow: auto;
}

.preview-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: color-mix(in srgb, var(--body-bg) 70%, var(--card-bg));
}

.preview-head {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-color);
  background: var(--card-bg);
}

.preview-title {
  display: block;
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pane-spin {
  display: block;
  margin: 40px auto;
}

.doc-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.doc-item {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--card-bg);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.doc-item:hover {
  border-color: color-mix(in srgb, var(--primary) 40%, var(--border-color));
}

.doc-item.active {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 10%, transparent);
}

.doc-main {
  min-width: 0;
  flex: 1;
}

.doc-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  word-break: break-word;
}

.doc-preview,
.search-result {
  flex: 1;
  margin: 0;
  padding: 14px 16px;
  border: none;
  background: transparent;
  white-space: pre-wrap;
  word-break: break-word;
  overflow: auto;
  color: var(--text);
  font-size: 13px;
  line-height: 1.7;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.search-result {
  margin-top: 12px;
  padding: 12px 14px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: color-mix(in srgb, var(--body-bg) 80%, var(--card-bg));
  max-height: 280px;
}

@media (max-width: 960px) {
  .stats-grid,
  .action-grid {
    grid-template-columns: 1fr;
  }

  .workspace-head {
    flex-direction: column;
    align-items: stretch;
  }

  .workspace-tools {
    width: 100%;
  }

  .workspace-tools :deep(.ant-input) {
    flex: 1;
    width: auto;
  }

  .workspace-grid {
    grid-template-columns: 1fr;
    min-height: auto;
  }

  .doc-pane {
    border-right: none;
    border-bottom: 1px solid var(--border-color);
    max-height: 280px;
    min-height: auto;
  }

  .preview-pane {
    min-height: 320px;
  }
}

@media (max-width: 640px) {
  .knowledge-page {
    padding-bottom: 8px;
  }

  .page-title {
    font-size: 20px;
  }

  .stat-value {
    font-size: 20px;
  }
}
</style>
