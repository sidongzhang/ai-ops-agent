import { nextTick, onMounted, ref } from 'vue'
import api from '../../api'

let markedParser = null

function extractErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item?.msg) return item.msg
        return JSON.stringify(item)
      })
      .join('；')
  }
  return error?.message || fallback
}

function escapeHtml(text) {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

async function ensureMarked() {
  if (!markedParser) {
    const { marked } = await import('marked')
    marked.setOptions({ breaks: true, gfm: true })
    markedParser = marked
  }
  return markedParser
}

export function useDiagnosisChat(systemId) {
  const storageKey = () => `aiops_chat_${systemId}`
  const question = ref('')
  const messages = ref(JSON.parse(localStorage.getItem(storageKey()) || '[]'))
  const diagnosing = ref(false)
  const analyzingData = ref(false)
  const fixing = ref(false)
  const chatBox = ref(null)
  const markdownReady = ref(false)
  const lastQuestion = ref('')
  const lastAnalysisContext = ref(null)

  function saveMessages() {
    localStorage.setItem(storageKey(), JSON.stringify(messages.value))
  }

  function clearMessages() {
    messages.value = []
    lastQuestion.value = ''
    lastAnalysisContext.value = null
    localStorage.removeItem(storageKey())
  }

  function renderMd(text) {
    if (markdownReady.value && markedParser) return markedParser.parse(text)
    return escapeHtml(text).replaceAll('\n', '<br />')
  }

  async function scrollBottom() {
    await nextTick()
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
  }

  async function ask() {
    const q = question.value.trim()
    if (!q) return
    lastQuestion.value = q
    lastAnalysisContext.value = null
    messages.value.push({ role: 'user', text: q })
    saveMessages()
    question.value = ''
    diagnosing.value = true
    await scrollBottom()
    try {
      const { data } = await api.post(`/systems/${systemId}/diagnose`, { question: q })
      messages.value.push({
        role: 'agent',
        text: data.answer,
        templateName: data.template_name,
        templateDescription: data.template_description,
        durationMs: data.duration_ms,
        evidenceSources: data.evidence_sources || [],
      })
    } catch (error) {
      messages.value.push({
        role: 'agent',
        text: `⚠️ 诊断失败：${extractErrorMessage(error, '诊断失败')}`,
      })
    } finally {
      saveMessages()
      diagnosing.value = false
      await scrollBottom()
    }
  }

  function formatDataEvidence(evidence) {
    if (!evidence) return ''
    if (evidence.analysis_type === 'stuck_tasks') {
      const lines = [
        '',
        '专项分析依据：',
        `- 数据源：${evidence.data_source || '-'}`,
        `- 表：${evidence.table || '-'}`,
        `- 卡住标准：${evidence.stuck_threshold_minutes} 分钟未更新`,
        `- 卡住任务：${evidence.stuck_count ?? 0} 条`,
      ]
      if (evidence.worker_health?.length) {
        lines.push(`- Worker：${evidence.worker_health.map((item) => `${item.name}${item.ok ? '正常' : '异常'}`).join('、')}`)
      }
      if (evidence.sample_rows?.length) {
        lines.push(`- 样例：返回 ${evidence.sample_rows.length} 条，敏感字段已脱敏`)
      }
      return lines.join('\n')
    }
    const lines = [
      '',
      '查询依据：',
      `- 数据源：${evidence.data_source || '-'}`,
      `- 表：${evidence.table || '-'}`,
      `- 查询范围：${evidence.today_only ? '今天' : '当前条件'}`,
      `- 记录数：${evidence.total ?? '-'}`,
    ]
    if (evidence.sample_rows?.length) {
      lines.push(`- 样例：返回 ${evidence.sample_rows.length} 条，敏感字段已脱敏`)
    }
    return lines.join('\n')
  }

  async function analyzeData() {
    const q = question.value.trim()
    if (!q) return
    lastQuestion.value = q
    lastAnalysisContext.value = null
    messages.value.push({ role: 'user', text: q })
    saveMessages()
    question.value = ''
    analyzingData.value = true
    await scrollBottom()
    try {
      const { data } = await api.post(`/systems/${systemId}/data-analysis`, { question: q })
      lastAnalysisContext.value = data.evidence?.analysis_type === 'stuck_tasks'
        ? {
            analysis_type: 'stuck_tasks',
            stuck_count: data.evidence.stuck_count,
            stuck_threshold_minutes: data.evidence.stuck_threshold_minutes,
            worker_health: data.evidence.worker_health || [],
          }
        : null
      messages.value.push({ role: 'agent', text: `${data.answer}${formatDataEvidence(data.evidence)}` })
    } catch (error) {
      messages.value.push({
        role: 'agent',
        text: `只读数据分析失败：${extractErrorMessage(error, '数据分析失败')}`,
      })
    } finally {
      saveMessages()
      analyzingData.value = false
      await scrollBottom()
    }
  }

  // 申请自动修复：调 workflow API，AI 分析并给出可执行提案
  async function requestFix() {
    const q = lastQuestion.value || '请分析当前系统状态并提出修复方案'
    fixing.value = true
    messages.value.push({ role: 'fix-loading', text: '' })
    await scrollBottom()
    try {
      const { data } = await api.post(`/systems/${systemId}/workflow`, {
        question: q,
        context: lastAnalysisContext.value || {},
      })
      messages.value.splice(messages.value.length - 1, 1, {
        role: 'action',
        wfId: data.id,
        diagnosis: data.diagnosis,
        action: data.proposed_action,
        status: data.status,
        result: data.execution_result || '',
      })
    } catch (error) {
      messages.value.splice(messages.value.length - 1, 1, {
        role: 'agent',
        text: `⚠️ 修复分析失败：${extractErrorMessage(error, '修复分析失败')}`,
      })
    } finally {
      fixing.value = false
      saveMessages()
      await scrollBottom()
    }
  }

  // 批准或拒绝修复提案
  async function decide(msgIndex, approve) {
    const msg = messages.value[msgIndex]
    if (!msg || msg.role !== 'action') return
    try {
      const { data } = await api.post(
        `/systems/${systemId}/workflow/${msg.wfId}/decision`,
        { approved: approve },
      )
      messages.value[msgIndex] = { ...msg, status: data.status, result: data.execution_result || '' }
    } catch (error) {
      messages.value[msgIndex] = {
        ...msg,
        status: 'error',
        result: `操作失败：${extractErrorMessage(error, '操作失败')}`,
      }
    } finally {
      saveMessages()
      await scrollBottom()
    }
  }

  onMounted(async () => {
    await ensureMarked()
    markdownReady.value = true
  })

  return {
    ask,
    analyzeData,
    requestFix,
    decide,
    chatBox,
    clearMessages,
    diagnosing,
    analyzingData,
    fixing,
    messages,
    question,
    renderMd,
    lastQuestion,
  }
}
