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

function buildAgentMessageFromDiagnose(data) {
  return {
    role: 'agent',
    reportId: data.id,
    reportType: 'diagnose',
    status: data.status || 'success',
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

function buildAgentMessageFromReport(report) {
  return {
    role: 'agent',
    reportId: report.id,
    reportType: report.report_type,
    status: report.status || 'success',
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

const DIAGNOSIS_POLL_INTERVAL_MS = 2500
const DIAGNOSIS_POLL_TIMEOUT_MS = 10 * 60 * 1000
const DEFAULT_MODEL_OPTIONS = [
  { value: 'auto', label: '自动', description: '按问题自动选择模型', mode: '', model: '' },
  { value: 'local', label: '本地模型', description: '使用本地部署模型', mode: 'local', model: '' },
  { value: 'api', label: 'API 模型', description: '使用远程 API 模型', mode: 'api', model: '' },
  { value: 'advanced', label: '高级模型', description: '用于复杂故障分析', mode: '', model: '' },
]

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitForDiagnosisReport(systemId, reportId, onRunningUpdate) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < DIAGNOSIS_POLL_TIMEOUT_MS) {
    await sleep(DIAGNOSIS_POLL_INTERVAL_MS)
    const { data } = await api.get(`/systems/${systemId}/diagnosis-reports/${reportId}`)
    if (data.status === 'success' || data.status === 'failed') {
      return data
    }
    // 运行中：把后端逐事件落库的证据链透给 UI，实时渲染工具链
    if (onRunningUpdate) onRunningUpdate(data)
  }
  throw new Error('诊断超时，请稍后在诊断历史中查看结果')
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
  const lastReportId = ref(null)
  const followUp = ref(true)
  const markdownReady = ref(false)
  const lastQuestion = ref('')
  const modelChoice = ref(localStorage.getItem('diagnosis:model-choice') || 'auto')
  const modelOptions = ref(DEFAULT_MODEL_OPTIONS)

  async function loadModelOptions() {
    try {
      const { data } = await api.get(`/systems/${systemId}/diagnose/model-options`)
      if (Array.isArray(data?.options) && data.options.length) {
        modelOptions.value = data.options
        if (!data.options.some((item) => item.value === modelChoice.value)) {
          modelChoice.value = 'auto'
        }
      }
    } catch {
      modelOptions.value = DEFAULT_MODEL_OPTIONS
    }
  }

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
      const latestReport = [...reports].reverse().find((report) => report.status === 'success' && report.id)
      lastReportId.value = latestReport?.id || null
      const lastUser = [...rebuilt].reverse().find((item) => item.role === 'user')
      if (lastUser?.text) lastQuestion.value = lastUser.text
    } catch (error) {
      message.error(extractErrorMessage(error, '诊断历史加载失败'))
    } finally {
      historyLoading.value = false
    }
  }

  function startNewChat() {
    // 只清空本地对话视图；服务端诊断历史保留（点「历史」可随时查看）
    messages.value = []
    lastQuestion.value = ''
    lastReportId.value = null
    message.success('已开启新对话（服务端诊断历史仍保留）')
  }

  async function exportHistoryBackup() {
    // 清空服务端历史前，先把全量诊断报告导出为 JSON 备份（防误丢）
    try {
      const { data } = await api.get(`/systems/${systemId}/diagnosis-reports`, {
        params: { limit: 200 },
      })
      const reports = data || []
      const blob = new Blob([JSON.stringify(reports, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `diagnosis-history-backup-${systemId}-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
      return reports.length
    } catch (error) {
      message.error(extractErrorMessage(error, '导出诊断历史备份失败'))
      return -1
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
    localStorage.setItem('diagnosis:model-choice', modelChoice.value)
    lastQuestion.value = q
    messages.value.push({ role: 'user', text: q })
    question.value = ''
    diagnosing.value = true
    const pendingIndex = messages.value.length
    messages.value.push({
      role: 'agent',
      status: 'running',
      text: '诊断进行中，正在收集健康检查、日志和指标证据…',
    })
    await scrollBottom()
    try {
      const body = { question: q, model_mode: modelChoice.value }
      // 会话式追问：开启追问且有上一轮报告时，携带 follow_up_report_id
      if (followUp.value && lastReportId.value) {
        body.follow_up_report_id = lastReportId.value
      }
      const { data } = await api.post(`/systems/${systemId}/diagnose`, body)
      if (data.status === 'running' && data.id) {
        const report = await waitForDiagnosisReport(systemId, data.id, (partial) => {
          const steps = partial.evidence || []
          if (steps.length) {
            const done = steps.filter((s) => s.status !== 'started').length
            messages.value[pendingIndex] = {
              role: 'agent',
              status: 'running',
              text: `诊断进行中，已完成 ${done}/${steps.length} 步取证…`,
              liveSteps: steps,
            }
          }
        })
        messages.value[pendingIndex] = buildAgentMessageFromReport(report)
        if (report.status === 'failed') {
          message.error(report.error_message || '诊断失败')
        }
      } else {
        messages.value[pendingIndex] = buildAgentMessageFromDiagnose(data)
      }
      if (data.id) {
        lastReportId.value = data.id
      }
    } catch (error) {
      messages.value[pendingIndex] = {
        role: 'agent',
        status: 'failed',
        text: `⚠️ 诊断失败：${extractErrorMessage(error, '诊断失败')}`,
      }
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
    await loadModelOptions()
    await loadHistory()
    await scrollBottom()
  })

  return {
    ask,
    requestFix,
    decide,
    chatBox,
    loadHistory,
    loadModelOptions,
    diagnosing,
    fixing,
    historyLoading,
    messages,
    question,
    modelChoice,
    modelOptions,
    renderMd,
    lastQuestion,
    liveStepIcon,
    followUp,
    startNewChat,
    exportHistoryBackup,
    clearMessages,
  }
}

const LIVE_STEP_ICONS = { started: '⏳', success: '✅', error: '❌' }

function liveStepIcon(status) {
  return LIVE_STEP_ICONS[status] || '⏳'
}
