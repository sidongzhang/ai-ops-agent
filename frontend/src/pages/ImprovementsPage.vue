<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import api from '../api'

const loading = ref(false)
const systems = ref([])
const systemId = ref(null)
const status = ref('pending')
const rows = ref([])
const stats = ref({ pending: 0, approved: 0, rejected: 0 })

const approveOpen = ref(false)
const rejectOpen = ref(false)
const acting = ref(false)
const current = ref(null)
const form = ref({ symptom: '', root_cause: '', remedy: '', review_note: '' })
const rejectNote = ref('')

const SOURCE_LABELS = {
  feedback_down: '用户反馈没用',
  failed: '诊断失败',
  manual: '人工登记',
}

const STATUS_LABELS = { pending: '待审核', approved: '已采纳', rejected: '已拒绝' }
const STATUS_COLOR = { pending: 'warning', approved: 'success', rejected: 'default' }

const columns = [
  { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true },
  { title: '来源', dataIndex: 'source', key: 'source', width: 130 },
  { title: '拟沉淀根因', dataIndex: 'root_cause', key: 'root_cause', ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 100 },
  { title: '操作', key: 'actions', width: 170 },
]

const tabs = computed(() => [
  { key: 'pending', label: `待审核 (${stats.value.pending})` },
  { key: 'approved', label: `已采纳 (${stats.value.approved})` },
  { key: 'rejected', label: `已拒绝 (${stats.value.rejected})` },
])

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function loadSystems() {
  try {
    const { data } = await api.get('/systems')
    systems.value = data || []
    if (!systemId.value && systems.value.length) systemId.value = systems.value[0].id
  } catch {
    message.error(error?.response?.data?.detail || '系统列表加载失败')
  }
}

async function loadStats() {
  if (!systemId.value) return
  try {
    const { data } = await api.get(`/systems/${systemId.value}/improvements/stats`)
    stats.value = data
  } catch {
    /* 统计失败不阻断列表 */
  }
}

async function load() {
  if (!systemId.value) return
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${systemId.value}/improvements`, {
      params: { status: status.value },
    })
    rows.value = data || []
    await loadStats()
  } catch (error) {
    message.error(error?.response?.data?.detail || '候选列表加载失败')
  } finally {
    loading.value = false
  }
}

function openApprove(item) {
  current.value = item
  form.value = {
    symptom: item.symptom,
    root_cause: item.root_cause,
    remedy: item.remedy,
    review_note: '',
  }
  approveOpen.value = true
}

function openReject(item) {
  current.value = item
  rejectNote.value = ''
  rejectOpen.value = true
}

async function submitApprove() {
  acting.value = true
  try {
    await api.post(
      `/systems/${systemId.value}/improvements/${current.value.id}/approve`,
      form.value,
    )
    message.success('已采纳，写入知识库（下次同类问题会命中）')
    approveOpen.value = false
    await load()
  } catch (error) {
    message.error(error?.response?.data?.detail || '采纳失败')
  } finally {
    acting.value = false
  }
}

async function submitReject() {
  acting.value = true
  try {
    await api.post(`/systems/${systemId.value}/improvements/${current.value.id}/reject`, {
      review_note: rejectNote.value,
    })
    message.success('已拒绝')
    rejectOpen.value = false
    await load()
  } catch (error) {
    message.error(error?.response?.data?.detail || '操作失败')
  } finally {
    acting.value = false
  }
}

onMounted(async () => {
  await loadSystems()
  await load()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">自改进</h2>
        <p class="page-sub">
          把「用户反馈没用 / 诊断失败」的案例沉淀成候选；<b>人工采纳</b>后写入知识库，
          下次同类问题直接命中经验。
        </p>
      </div>
      <div class="page-actions">
        <a-select
          v-model:value="systemId"
          style="width: 220px"
          placeholder="选择系统"
          @change="load"
        >
          <a-select-option v-for="s in systems" :key="s.id" :value="s.id">
            {{ s.name }}
          </a-select-option>
        </a-select>
        <a-button :loading="loading" @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </div>
    </div>

    <a-tabs v-model:activeKey="status" @change="load">
      <a-tab-pane v-for="t in tabs" :key="t.key" :tab="t.label" />
    </a-tabs>

    <a-table
      :columns="columns"
      :data-source="rows"
      :loading="loading"
      row-key="id"
      size="middle"
      :pagination="{ pageSize: 10 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'question'">
          <div class="cell-strong">{{ record.question || '-' }}</div>
          <div class="cell-sub">{{ formatTime(record.created_at) }}</div>
        </template>
        <template v-else-if="column.key === 'source'">
          <a-tag>{{ SOURCE_LABELS[record.source] || record.source }}</a-tag>
        </template>
        <template v-else-if="column.key === 'root_cause'">
          <div class="cell-sub">{{ record.root_cause || '（尚未抽取到根因，可在采纳时人工补全）' }}</div>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="STATUS_COLOR[record.status]">{{ STATUS_LABELS[record.status] || record.status }}</a-tag>
        </template>
        <template v-else-if="column.key === 'actions'">
          <template v-if="record.status === 'pending'">
            <a-button type="link" size="small" @click="openApprove(record)">
              <template #icon><CheckOutlined /></template>采纳
            </a-button>
            <a-button type="link" size="small" danger @click="openReject(record)">
              <template #icon><CloseOutlined /></template>拒绝
            </a-button>
          </template>
          <span v-else-if="record.memory_id" class="cell-sub">记忆 #{{ record.memory_id }}</span>
          <span v-else class="cell-sub">—</span>
        </template>
      </template>
    </a-table>

    <a-modal
      v-model:open="approveOpen"
      title="采纳经验（写入知识库）"
      :confirm-loading="acting"
      ok-text="采纳并写入"
      cancel-text="取消"
      width="720px"
      @ok="submitApprove"
    >
      <div class="field">
        <label>症状 / 触发条件</label>
        <a-textarea v-model:value="form.symptom" :rows="2" />
      </div>
      <div class="field">
        <label>根因（建议人工核对后修正）</label>
        <a-textarea v-model:value="form.root_cause" :rows="3" />
      </div>
      <div class="field">
        <label>处置建议</label>
        <a-textarea v-model:value="form.remedy" :rows="3" />
      </div>
      <div class="field">
        <label>审核备注</label>
        <a-textarea v-model:value="form.review_note" :rows="2" placeholder="可选" />
      </div>
      <a-collapse v-if="current?.answer_excerpt" ghost>
        <a-collapse-panel key="a" header="查看当次原始回答">
          <pre class="answer">{{ current.answer_excerpt }}</pre>
        </a-collapse-panel>
      </a-collapse>
    </a-modal>

    <a-modal
      v-model:open="rejectOpen"
      title="拒绝候选"
      :confirm-loading="acting"
      ok-text="确认拒绝"
      cancel-text="取消"
      @ok="submitReject"
    >
      <div class="field">
        <label>拒绝原因</label>
        <a-textarea v-model:value="rejectNote" :rows="3" placeholder="可选：说明为什么不采纳" />
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.page { padding: 20px 24px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 8px; }
.page-title { margin: 0 0 4px; font-size: 20px; font-weight: 600; }
.page-sub { margin: 0; color: var(--text-muted, #8a8f98); font-size: 13px; max-width: 720px; }
.page-actions { display: flex; gap: 10px; align-items: center; }
.cell-strong { font-weight: 500; }
.cell-sub { color: var(--text-muted, #8a8f98); font-size: 12px; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 13px; margin-bottom: 6px; color: var(--text-muted, #8a8f98); }
.answer { white-space: pre-wrap; font-size: 12px; max-height: 260px; overflow: auto; margin: 0; }
</style>
