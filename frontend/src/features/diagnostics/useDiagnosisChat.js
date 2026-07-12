import { nextTick, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
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

function buildAgentMessageFromReport(report) {
  return {
    role: 'agent',
    reportId: report.id,
    reportType: report.report_type,
    text: report.answer,
    templateName: report.template_name,
    templateDescription: report.template_description,
    model: report.model,
    durationMs: report.duration_ms,
    totalTokens: report.total_tokens,
    evidenceSources: report.evidence_sources || [],
    evidenceSteps: report.evidence_steps || [],
    evidence: report.evidence || [],
    toolCalls: report.tool_calls || [],
    knowledgeRefs: report.knowledge_refs || [],
    createdAt: report.created_at,
  }
}

function buildAgentMessageFromDiagnose(data) {
  return {
    role: 'agent',
    reportId: data.id,
    reportType: 'diagnose',
    text: data.answer,
    templateName: data.template_name,
    templateDescription: data.template_description,
    model: data.model,
    durationMs: data.duration_ms,
    totalTokens: data.total_tokens,
    evidenceSources: data.evidence_sources || [],
    evidenceSteps: data.evidence_steps || [],
    evidence: data.evidence || [],
    toolCalls: data.tool_calls || [],
    knowledgeRefs: data.knowledge_refs || [],
  }
}

function buildActionMessageFromWorkflow(workflow) {
  return {
    role: 'action',
    wfId: workflow.id,
    diagnosis: workflow.diagnosis,
    action: workflow.proposed_action,
    status: workflow.status,
    result: workflow.execution_result || '',
    createdAt: workflow.created_at,
  }
}

function mergeTimelineMessages(reportMessages, workflowMessages) {
  const merged = [...reportMessages, ...workflowMessages]
  merged.sort((left, right) => {
    const leftTime = new Date(left.createdAt || 0).getTime()
    const rightTime = new Date(right.createdAt || 0).getTime()
    return leftTime - rightTime
  })
  return merged
}

export function useDiagnosisChat(systemId) {
  const question = ref('')
  const messages = ref([])
  const historyLoading = ref(false)
  const diagnosing = ref(false)
  const fixing = ref(false)
  const chatBox = ref(null)
  const markdownReady = ref(false)
  const lastQuestion = ref('')

  async function loadHistory() {
    historyLoading.value = true
    try {
      const [{ data: reports }, { data: workflows }] = await Promise.all([
        api.get(`/systems/${systemId}/diagnosis-reports`, { params: { limit: 50 } }),
        api.get(`/systems/${systemId}/workflow`),
      ])
      const rebuilt = []
      for (const report of reports) {
        rebuilt.push({ role: 'user', text: report.question, createdAt: report.created_at })
        rebuilt.push({ ...buildAgentMessageFromReport(report), createdAt: report.created_at })
      }
      const workflowMessages = workflows.map(buildActionMessageFromWorkflow)
      messages.value = mergeTimelineMessages(rebuilt, workflowMessages)
      const lastUser = [...rebuilt].reverse().find((item) => item.role === 'user')
      if (lastUser?.text) lastQuestion.value = lastUser.text
    } catch (error) {
      message.error(extractErrorMessage(error, '诊断历史加载失败'))
    } finally {
      historyLoading.value = false
    }
  }

  async function clearMessages() {
    try {
      await api.delete(`/systems/${systemId}/diagnosis-reports`)
    } catch (error) {
      message.error(extractErrorMessage(error, '清空诊断历史失败'))
      return
    }
    messages.value = []
    lastQuestion.value = ''
    message.success('诊断历史已清空')
  }

  function persistWorkflowMessages() {
    // 工作流已持久化到服务端，保留空实现以兼容现有调用。
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
    messages.value.push({ role: 'user', text: q })
    question.value = ''
    diagnosing.value = true
    await scrollBottom()
    try {
      const { data } = await api.post(`/systems/${systemId}/diagnose`, { question: q })
      messages.value.push(buildAgentMessageFromDiagnose(data))
    } catch (error) {
      messages.value.push({
        role: 'agent',
        text: `⚠️ 诊断失败：${extractErrorMessage(error, '诊断失败')}`,
      })
    } finally {
      diagnosing.value = false
      await scrollBottom()
    }
  }

  async function requestFix() {
    const q = lastQuestion.value || '请分析当前系统状态并提出修复方案'
    fixing.value = true
    messages.value.push({ role: 'fix-loading', text: '' })
    persistWorkflowMessages()
    await scrollBottom()
    try {
      const { data } = await api.post(`/systems/${systemId}/workflow`, {
        question: q,
        context: {},
      })
      messages.value.splice(messages.value.length - 1, 1, {
        role: 'action',
        wfId: data.id,
        diagnosis: data.diagnosis,
        action: data.proposed_action,
        status: data.status,
        result: data.execution_result || '',
        createdAt: data.created_at,
      })
    } catch (error) {
      messages.value.splice(messages.value.length - 1, 1, {
        role: 'agent',
        text: `⚠️ 修复分析失败：${extractErrorMessage(error, '修复分析失败')}`,
      })
    } finally {
      fixing.value = false
      persistWorkflowMessages()
      await scrollBottom()
    }
  }

  async function decide(msgIndex, approve) {
    const msg = messages.value[msgIndex]
    if (!msg || msg.role !== 'action') return
    try {
      const { data } = await api.post(
        `/systems/${systemId}/workflow/${msg.wfId}/decision`,
        { approved: approve },
      )
      messages.value[msgIndex] = { ...msg, status: data.status, result: data.execution_result || '' }
      window.dispatchEvent(new Event('aiops:approvals-changed'))
    } catch (error) {
      messages.value[msgIndex] = {
        ...msg,
        status: 'error',
        result: `操作失败：${extractErrorMessage(error, '操作失败')}`,
      }
    } finally {
      persistWorkflowMessages()
      await scrollBottom()
    }
  }

  onMounted(async () => {
    await ensureMarked()
    markdownReady.value = true
    await loadHistory()
    await scrollBottom()
  })

  return {
    ask,
    requestFix,
    decide,
    chatBox,
    clearMessages,
    loadHistory,
    diagnosing,
    fixing,
    historyLoading,
    messages,
    question,
    renderMd,
    lastQuestion,
  }
}
