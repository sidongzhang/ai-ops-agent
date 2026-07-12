<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import api from '../../../api'

const router = useRouter()

const props = defineProps({
  systemId: { type: [String, Number], default: '' },
  templateDescription: { type: String, default: '' },
  model: { type: String, default: '' },
  durationMs: { type: Number, default: 0 },
  totalTokens: { type: Number, default: 0 },
  evidenceSources: { type: Array, default: () => [] },
  evidenceSteps: { type: Array, default: () => [] },
  evidence: { type: Array, default: () => [] },
  knowledgeRefs: { type: Array, default: () => [] },
  reportId: { type: [Number, String, null], default: null },
  reportType: { type: String, default: '' },
})

const expanded = ref(false)
const exporting = ref(false)
const exportOpen = ref(false)
const exportName = ref('')
const lastExportedDoc = ref('')

const STATUS_COLOR = {
  success: 'ok',
  error: 'fail',
  started: 'pending',
}

function formatInput(value) {
  if (!value) return '无'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

async function exportToKnowledge() {
  if (!props.systemId || !props.reportId) return
  exporting.value = true
  try {
    const { data } = await api.post(
      `/systems/${props.systemId}/diagnosis-reports/${props.reportId}/export-knowledge`,
      { doc_name: exportName.value.trim() },
    )
    lastExportedDoc.value = data.name
    exportOpen.value = false
    message.success(`已沉淀到知识库：${data.name}`)
  } catch (error) {
    message.error(error?.response?.data?.detail || '沉淀到知识库失败')
  } finally {
    exporting.value = false
  }
}

function openExportModal() {
  exportName.value = props.reportId ? `diagnosis-${props.reportId}` : 'diagnosis-report'
  exportOpen.value = true
}

function goKnowledgePage() {
  if (!props.systemId) return
  router.push(`/systems/${props.systemId}/knowledge`)
}
</script>

<template>
  <div v-if="templateDescription || evidenceSources.length || evidence.length || knowledgeRefs.length" class="evidence-chain">
    <button type="button" class="evidence-toggle" @click="expanded = !expanded">
      <div class="evidence-toggle-main">
        <span v-if="templateDescription" class="context-template">{{ templateDescription }}</span>
        <span v-if="reportId" class="meta-chip">报告 #{{ reportId }}</span>
        <span v-if="model" class="meta-chip">{{ model }}</span>
        <span v-if="durationMs" class="meta-chip">{{ (durationMs / 1000).toFixed(1) }}s</span>
        <span v-if="totalTokens" class="meta-chip">{{ totalTokens }} tokens</span>
        <span v-if="evidenceSources.length" class="meta-chip">依据 {{ evidenceSources.join(' · ') }}</span>
        <span v-else-if="evidence.length" class="meta-chip">{{ evidence.length }} 条证据</span>
        <span v-if="knowledgeRefs.length" class="meta-chip">{{ knowledgeRefs.length }} 篇知识库文档</span>
      </div>
      <span class="expand-icon" :class="{ rotated: expanded }">
        <DownOutlined />
        <span>{{ expanded ? '收起证据链' : '查看证据链' }}</span>
      </span>
    </button>

    <div v-show="expanded" class="evidence-body">
      <div v-if="evidenceSteps.length" class="steps-block">
        <div class="block-title">分析步骤</div>
        <ol class="steps-list">
          <li v-for="step in evidenceSteps" :key="step">{{ step }}</li>
        </ol>
      </div>

      <div v-if="evidence.length" class="items-block">
        <div class="block-title">证据明细</div>
        <div class="evidence-items">
          <article
            v-for="item in evidence"
            :key="`${item.step}-${item.type}`"
            :class="['evidence-card', STATUS_COLOR[item.status] || 'pending']"
          >
            <div class="evidence-card-head">
              <span class="step-no">{{ item.step || '·' }}</span>
              <span class="evidence-label">{{ item.label || item.type }}</span>
              <span class="evidence-status">{{ item.status || 'unknown' }}</span>
              <span v-if="item.duration_ms" class="evidence-duration">{{ item.duration_ms }}ms</span>
            </div>
            <p v-if="item.detail" class="evidence-detail">{{ item.detail }}</p>
            <details v-if="item.input && Object.keys(item.input || {}).length" class="evidence-extra">
              <summary>输入参数</summary>
              <pre>{{ formatInput(item.input) }}</pre>
            </details>
            <details v-if="item.output" class="evidence-extra">
              <summary>原始输出</summary>
              <pre>{{ item.output }}</pre>
            </details>
          </article>
        </div>
      </div>
      <div v-if="knowledgeRefs.length" class="knowledge-block">
        <div class="block-title">引用知识库文档</div>
        <article v-for="doc in knowledgeRefs" :key="doc.name" class="knowledge-doc">
          <div class="knowledge-doc-head">
            <span class="knowledge-doc-name">{{ doc.name }}</span>
            <span v-if="doc.truncated" class="knowledge-doc-tag">已截断</span>
          </div>
          <p v-if="doc.snippet" class="knowledge-doc-snippet">{{ doc.snippet }}</p>
          <pre v-if="doc.content" class="knowledge-doc-content">{{ doc.content }}</pre>
        </article>
      </div>

      <div v-if="reportId && systemId" class="export-row">
        <a-button size="small" @click.stop="openExportModal">沉淀到知识库</a-button>
        <a-button v-if="lastExportedDoc" size="small" type="link" @click.stop="goKnowledgePage">
          查看知识库
        </a-button>
      </div>
    </div>
  </div>

  <a-modal
    v-model:open="exportOpen"
    title="沉淀到知识库"
    ok-text="保存文档"
    cancel-text="取消"
    :confirm-loading="exporting"
    @ok="exportToKnowledge"
  >
    <p class="export-modal-tip">将当前诊断报告导出为 Markdown 文档，供后续诊断引用。</p>
    <a-input v-model:value="exportName" placeholder="文档名称，如 kafka-lag-runbook" />
  </a-modal>
</template>

<style scoped>
.evidence-chain {
  margin: 0 0 8px 2px;
  border: 1px solid color-mix(in srgb, var(--primary) 18%, var(--border-color));
  border-radius: 10px;
  background: color-mix(in srgb, var(--primary) 4%, var(--card-bg));
  overflow: hidden;
}

.evidence-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.evidence-toggle-main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.context-template {
  padding: 2px 7px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--primary) 12%, transparent);
  color: var(--primary);
  font-size: 12px;
  font-weight: 600;
}

.meta-chip {
  padding: 2px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--border-color) 35%, transparent);
  color: var(--text-subtle);
  font-size: 11px;
}

.expand-icon {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-subtle);
  font-size: 11px;
  flex-shrink: 0;
  transition: transform 0.2s;
}

.expand-icon.rotated {
  transform: rotate(180deg);
}

.evidence-body {
  padding: 0 10px 10px;
  border-top: 1px dashed color-mix(in srgb, var(--border-color) 80%, transparent);
}

.block-title {
  margin: 10px 0 6px;
  color: var(--text-subtle);
  font-size: 12px;
  font-weight: 700;
}

.steps-list {
  margin: 0;
  padding-left: 18px;
  color: var(--text);
  font-size: 12px;
  line-height: 1.6;
}

.evidence-items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.evidence-card {
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  padding: 8px 10px;
  border-left: 3px solid var(--primary);
}

.evidence-card.ok {
  border-left-color: var(--primary);
}

.evidence-card.fail {
  border-left-color: #dc2626;
}

.evidence-card.pending {
  border-left-color: #d97706;
}

.evidence-card-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 8px;
}

.step-no {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 12%, transparent);
  color: var(--primary);
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.evidence-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
}

.evidence-status {
  font-size: 11px;
  color: var(--text-subtle);
}

.evidence-duration {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-subtle);
}

.evidence-detail {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-subtle);
}

.evidence-extra {
  margin-top: 6px;
}

.evidence-extra summary {
  cursor: pointer;
  font-size: 11px;
  color: var(--primary);
}

.evidence-extra pre {
  margin: 6px 0 0;
  padding: 8px;
  border-radius: 6px;
  background: var(--body-bg);
  border: 1px solid var(--border-color);
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow: auto;
}

.knowledge-block {
  margin-top: 12px;
}

.knowledge-doc {
  border: 1px solid color-mix(in srgb, var(--primary) 16%, var(--border-color));
  border-radius: 10px;
  background: var(--card-bg);
  padding: 10px 12px;
  margin-top: 8px;
}

.knowledge-doc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.knowledge-doc-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}

.knowledge-doc-tag {
  font-size: 11px;
  color: #b45309;
  background: color-mix(in srgb, #d97706 12%, transparent);
  padding: 1px 6px;
  border-radius: 999px;
}

.knowledge-doc-snippet {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-subtle);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.knowledge-doc-content {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--body-bg);
  border: 1px solid var(--border-color);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
}

.export-row {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed color-mix(in srgb, var(--border-color) 70%, transparent);
  display: flex;
  gap: 8px;
  align-items: center;
}

.export-modal-tip {
  margin: 0 0 12px;
  color: var(--text-subtle);
  font-size: 13px;
  line-height: 1.6;
}
</style>
