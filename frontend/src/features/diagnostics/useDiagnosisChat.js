import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../api'
import DOMPurify from 'dompurify'
import { subscribeDiagnosis } from './answerStream'

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

function runningDiagnosisText(report) {
  const execution = (report.business_context || {}).execution || {}
  if (execution.phase === 'queued') return `排队中，已等待 ${execution.wait_seconds || 0} 秒（最多 15 分钟，可停止排队）`
  if (execution.phase === 'stopping' || report.cancel_requested_at) return '停止中，等待执行方确认…'
  const steps = report.evidence || []
  if (report.business_context?.interaction?.resolved === 'chat') return '执行中，正在回复…'
  return steps.length ? `执行中，已完成 ${steps.filter(s => s.status !== 'started').length}/${steps.length} 步取证…` : '执行中，正在收集实时证据…'
}

function buildAgentMessageFromDiagnose(data) {
  return {
    role: 'agent',
    reportId: data.id,
    reportType: 'diagnose',
    interaction: data.business_context?.interaction,
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
    interaction: (report.business_context || {}).interaction,
    performance: (report.business_context || {}).performance,
    evidenceValidation: (report.business_context || {}).evidence_validation,
    answerStream: (report.business_context || {}).answer_stream,
    text: report.status === 'running' ? runningDiagnosisText(report) : (report.error_message || report.answer),
    templateName: report.template_name,
    templateDescription: report.template_description,
    model: report.model,
    durationMs: report.duration_ms,
    evidenceSources: report.evidence_sources || [],
    evidenceSteps: report.evidence_steps || [],
    evidence: report.evidence || [],
    toolCalls: report.tool_calls || [],
    knowledgeRefs: report.knowledge_refs || [],
    imageMeta: (report.business_context || {}).images || [],
    createdAt: report.created_at,
    // 用量/成本：本次 vs 会话累计（cost 是价格表估算，界面标 ≈）
    totalTokens: report.total_tokens || 0,
    conversationTokens: report.conversation_tokens || 0,
    costUsd: report.cost_usd_estimated,
    conversationCostUsd: report.conversation_cost_usd,
    usageComplete: report.usage_complete !== false,
    usageGapReason: report.usage_gap_reason || '',
    // 派生标记：success 但没有做任何取证（无工具调用/证据，且答案未引用知识库文档）
    unverifiedReason: report.unverified_reason || '',
    cancelPhase: report.cancel_phase || '',
    canResume: !!report.can_resume,
    resumedFrom: report.resumed_from_report_id || null,
  }
}

const DIAGNOSIS_POLL_INTERVAL_MS = 2500
const DIAGNOSIS_POLL_TIMEOUT_MS = 27 * 60 * 1000
const DEFAULT_MODEL_OPTIONS = [
  { value: 'auto', label: '自动', description: '按问题自动选择模型', mode: '', model: '' },
  { value: 'local', label: '本地模型', description: '使用本地部署模型', mode: 'local', model: '' },
  { value: 'api', label: 'API 模型', description: '使用远程 API 模型', mode: 'api', model: '' },
  { value: 'advanced', label: '高级模型', description: '用于复杂故障分析', mode: '', model: '' },
]

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function pollDiagnosisReport(systemId, reportId, onRunningUpdate, signal) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < DIAGNOSIS_POLL_TIMEOUT_MS) {
    if (signal?.aborted) throw new Error('页面已关闭，任务仍在后台执行')
    await sleep(DIAGNOSIS_POLL_INTERVAL_MS)
    if (signal?.aborted) throw new Error('页面已关闭，任务仍在后台执行')
    const { data } = await api.get(`/systems/${systemId}/diagnosis-reports/${reportId}`)
    if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
      return data
    }
    // 运行中：把后端逐事件落库的证据链透给 UI，实时渲染工具链
    if (onRunningUpdate) onRunningUpdate(data)
  }
  throw new Error('诊断超时，请稍后在诊断历史中查看结果')
}

function openReportStream(systemId, reportId, onRunningUpdate, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new Error('页面已关闭，任务仍在后台执行')); return }
    let close = () => {}
    let settled = false
    const polling = new AbortController()
    const cleanup = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); close(); polling.abort() }
    const fail = error => { if (settled) return; settled = true; cleanup(); reject(error) }
    const abort = () => fail(new Error('页面已关闭，任务仍在后台执行'))
    const timer = setTimeout(() => fail(new Error('诊断等待超时，请从历史查看')), DIAGNOSIS_POLL_TIMEOUT_MS)
    signal.addEventListener('abort', abort, { once: true })
    const finish = report => { if (settled) return; settled = true; cleanup(); resolve(report) }
    const update = report => { if (!settled) onRunningUpdate?.(report) }
    const url = `${api.defaults.baseURL || ''}/systems/${systemId}/diagnosis-reports/${reportId}/stream`
    close = subscribeDiagnosis(url, report => {
      if (['success', 'failed', 'cancelled'].includes(report.status)) finish(report)
      else update(report)
    }, () => {
      if (!settled) pollDiagnosisReport(systemId, reportId, update, polling.signal).then(finish, fail)
    })
  })
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

export function useDiagnosisChat(systemId) {
  const streamLifecycle = new AbortController()
  onUnmounted(() => streamLifecycle.abort())
  const waitForDiagnosisReport = (sid, rid, callback) => openReportStream(sid, rid, callback, streamLifecycle.signal)
  const question = ref('')
  const messages = ref([])
  const historyLoading = ref(false)
  const historyReports = ref([])
  const chats = ref([])
  const activeChatId = ref(null)
  const chatManaging = ref(false)
  const workflowHistory = ref([])
  const imageReads = ref(0)
  const diagnosing = ref(false)
  const stopping = ref(false)
  const activeReportId = ref(null)
  const fixing = ref(false)
  const chatBox = ref(null)
  const lastReportId = ref(null)
  const followUp = ref(true)
  const markdownReady = ref(false)
  const lastQuestion = ref('')
  const modelChoice = ref(localStorage.getItem('diagnosis:model-choice') || 'auto')
  const modelOptions = ref(DEFAULT_MODEL_OPTIONS)
  // 多模态：随问题上传的截图（data URL），最多 MAX_IMAGES 张
  const images = ref([])
  let attachmentGeneration = 0
  const MAX_IMAGES = 3
  const MAX_IMAGE_BYTES = 2 * 1024 * 1024
  const ALLOWED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

  function removeImage(index) {
    images.value.splice(index, 1)
  }

  function clearImages() {
    attachmentGeneration += 1
    images.value = []
  }

  async function addImageFiles(fileList) {
    const generation = attachmentGeneration
    const files = Array.from(fileList || [])
    for (const file of files) {
      if (images.value.length + imageReads.value >= MAX_IMAGES) {
        message.warning(`最多上传 ${MAX_IMAGES} 张图片`)
        break
      }
      if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
        message.warning(`不支持的图片类型：${file.type || '未知'}（仅支持 PNG/JPEG/WebP/GIF）`)
        continue
      }
      if (file.size > MAX_IMAGE_BYTES) {
        message.warning(`图片「${file.name}」超过 ${MAX_IMAGE_BYTES / 1024 / 1024}MB`)
        continue
      }
      imageReads.value += 1
      try {
        const dataUrl = await new Promise((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result || ''))
          reader.onerror = () => reject(new Error('读取失败'))
          reader.readAsDataURL(file)
        })
        if (generation === attachmentGeneration) images.value.push({ name: file.name || '图片', dataUrl })
      } catch {
        message.warning(`图片「${file.name}」读取失败`)
      } finally {
        imageReads.value -= 1
      }
    }
  }

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

  async function openHistoryReport(id) {
    if (diagnosing.value || fixing.value || chatManaging.value) return
    chatManaging.value = true
    try {
      const { data: reports } = await api.get(`/systems/${systemId}/diagnosis-chats/${id}/reports`)
      activeChatId.value = id
      messages.value = (reports || []).flatMap((report) => [
        { role: 'user', text: report.question, imageMeta: report.business_context?.images || [] },
        buildAgentMessageFromReport(report),
      ])
      historyReports.value = reports || []
      const latest = [...(reports || [])].reverse().find((report) => report.status === 'success')
      lastReportId.value = latest?.id || null
      lastQuestion.value = reports.at(-1)?.question || ''
      question.value = ''; clearImages()
      const runningIndex = messages.value.findIndex((item) => item.role === 'agent' && item.status === 'running')
      if (runningIndex >= 0) watchExistingRun(messages.value[runningIndex].reportId, runningIndex)
      await scrollBottom()
    } catch (error) { message.error(extractErrorMessage(error, '加载对话失败')) }
    finally { chatManaging.value = false }
  }

  async function ensureActiveChat() {
    if (activeChatId.value) return
    const { data } = await api.post(`/systems/${systemId}/diagnosis-chats`, { title: '新对话' })
    activeChatId.value = data.id
    chats.value.unshift({ ...data, report_count: 0, running: false })
  }

  async function renameChat(id, title) {
    if (chatManaging.value) return false
    chatManaging.value = true
    try {
      const { data } = await api.patch(`/systems/${systemId}/diagnosis-chats/${id}`, { title: title.trim() })
      const chat = chats.value.find((item) => item.id === id)
      if (chat) Object.assign(chat, data)
      return true
    } catch (error) { message.error(extractErrorMessage(error, '改名失败')); return false }
    finally { chatManaging.value = false }
  }

  async function deleteChat(id) {
    if (chatManaging.value) return false
    chatManaging.value = true
    try {
      await api.delete(`/systems/${systemId}/diagnosis-chats/${id}`)
      chats.value = chats.value.filter((item) => item.id !== id)
      if (activeChatId.value === id) {
        activeChatId.value = null; messages.value = []; historyReports.value = []
        lastReportId.value = null; lastQuestion.value = ''; question.value = ''; clearImages()
      }
      message.success('对话及对应历史已删除')
      return true
    } catch (error) { message.error(extractErrorMessage(error, '删除失败')); return false }
    finally { chatManaging.value = false }
  }

  function openWorkflowHistory(id) {
    if (diagnosing.value || fixing.value) return
    const workflow = workflowHistory.value.find((item) => item.wfId === id)
    if (workflow) messages.value = [{ ...workflow }]
  }

  function showRunning(index, partial) {
    const box = chatBox.value
    const pinned = !box || box.scrollHeight - box.clientHeight - box.scrollTop < 80
    stopping.value = !!partial.stopping || !!partial.cancel_requested_at || stopping.value
    messages.value[index] = { ...buildAgentMessageFromReport(partial), liveSteps: partial.evidence || [] }
    if (pinned) scrollBottom()
  }

  async function watchExistingRun(reportId, messageIndex = 1) {
    diagnosing.value = true
    activeReportId.value = reportId
    try {
      const report = await waitForDiagnosisReport(systemId, reportId, (partial) => {
        showRunning(messageIndex, partial)
      })
      messages.value[messageIndex] = buildAgentMessageFromReport(report)
      if (report.status === 'success') lastReportId.value = reportId
      await loadHistory()
    } catch (error) {
      messages.value[messageIndex] = { role: 'agent', reportId, status: 'failed', text: extractErrorMessage(error, '读取进度失败，请从历史重新查看') }
    } finally {
      diagnosing.value = false
      stopping.value = false
      activeReportId.value = null
      await scrollBottom()
    }
  }

  async function loadHistory(selectLatest = false) {
    historyLoading.value = true
    try {
      const [{ data: list }, { data: workflows }] = await Promise.all([
        api.get(`/systems/${systemId}/diagnosis-chats`), api.get(`/systems/${systemId}/workflow`),
      ])
      chats.value = list || []
      workflowHistory.value = (workflows || []).filter((item) => item.proposed_action).map(buildActionMessageFromWorkflow)
      if (selectLatest === true && !diagnosing.value && !messages.value.length && chats.value.length) await openHistoryReport(chats.value[0].id)
    } catch (error) { message.error(extractErrorMessage(error, '对话历史加载失败')) }
    finally { historyLoading.value = false }
  }

  async function startNewChat() {
    if (diagnosing.value || fixing.value || chatManaging.value) return
    chatManaging.value = true
    try {
      const { data } = await api.post(`/systems/${systemId}/diagnosis-chats`, { title: '新对话' })
      chats.value.unshift({ ...data, report_count: 0, running: false })
      activeChatId.value = data.id
      messages.value = []; historyReports.value = []; question.value = ''; clearImages()
      lastQuestion.value = ''; lastReportId.value = null
    } catch (error) { message.error(extractErrorMessage(error, '创建对话失败')) }
    finally { chatManaging.value = false }
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
    lastReportId.value = null
    historyReports.value = []
    message.success('诊断历史已清空')
  }

  function persistWorkflowMessages() {
    // 工作流已持久化到服务端，保留空实现以兼容现有调用。
  }

  function renderMd(text) {
    if (markdownReady.value && markedParser) return DOMPurify.sanitize(markedParser.parse(text || ''))
    return escapeHtml(text || '').replaceAll('\n', '<br />')
  }

  async function scrollBottom() {
    await nextTick()
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
  }

  async function ask() {
    if (diagnosing.value || fixing.value || chatManaging.value || imageReads.value) return
    const q = question.value.trim()
    const attached = images.value.map((item) => item.dataUrl)
    const attachedMeta = images.value.map((item) => ({ name: item.name, dataUrl: item.dataUrl }))
    if (!q && !attached.length) return
    diagnosing.value = true
    try { await ensureActiveChat() }
    catch (error) { diagnosing.value = false; message.error(extractErrorMessage(error, '创建对话失败')); return }
    localStorage.setItem('diagnosis:model-choice', modelChoice.value)
    lastQuestion.value = q
    messages.value.push({ role: 'user', text: q, images: attachedMeta })
    question.value = ''
    clearImages()
    diagnosing.value = true
    const pendingIndex = messages.value.length
    messages.value.push({
      role: 'agent',
      status: 'running',
      text: '正在处理你的问题…',
    })
    await scrollBottom()
    try {
      const body = { question: q, model_mode: modelChoice.value, chat_id: activeChatId.value }
      if (attached.length) body.images = attached
      // 会话式追问：开启追问且有上一轮报告时，携带 follow_up_report_id
      if (followUp.value && lastReportId.value) {
        body.follow_up_report_id = lastReportId.value
      }
      const { data } = await api.post(`/systems/${systemId}/diagnose`, body)
      activeReportId.value = data.id || null
      if (data.status === 'running' && data.id) {
        const report = await waitForDiagnosisReport(systemId, data.id, (partial) => {
          showRunning(pendingIndex, partial)
        })
        messages.value[pendingIndex] = buildAgentMessageFromReport(report)
        if (report.status === 'failed') {
          message.error(report.error_message || '诊断失败')
        } else if (report.status === 'cancelled') {
          message.info('诊断已停止' + (report.can_resume ? '，可点击「继续」接着跑' : ''))
        }
      } else {
        messages.value[pendingIndex] = buildAgentMessageFromDiagnose(data)
      }
      const completed = messages.value[pendingIndex]
      if (completed.status === 'success' && completed.reportId) lastReportId.value = completed.reportId
      await loadHistory()
    } catch (error) {
      messages.value[pendingIndex] = { role: 'agent', status: 'failed', text: `⚠️ ${extractErrorMessage(error, '提交或读取失败，请先查看历史记录')}` }
    } finally {
      diagnosing.value = false
      stopping.value = false
      activeReportId.value = null
      await scrollBottom()
    }
  }

  /** 停止当前正在跑的诊断（幂等；后端保证重复点击只有一次生效）。 */
  async function cancelDiagnosis() {
    const rid = activeReportId.value
    if (!rid || stopping.value) return
    stopping.value = true
    try {
      await api.post(`/systems/${systemId}/diagnosis-reports/${rid}/cancel`)
      message.info('已发送停止请求，正在收尾…')
    } catch (error) {
      stopping.value = false
      message.error(extractErrorMessage(error, '停止失败'))
    }
  }

  /** 继续被中断的诊断（幂等：同一源报告只会真正启动一次）。 */
  async function resumeDiagnosis(msgIndex, forceRefresh = false) {
    const msg = messages.value[msgIndex]
    const srcId = msg?.reportId
    if (!srcId || diagnosing.value || fixing.value) return
    diagnosing.value = true
    const holder = { role: 'agent', status: 'running', text: '正在继续上次被中断的诊断…' }
    const targetIndex = messages.value.length
    messages.value.push({ role: 'user', text: forceRefresh ? '刷新证据后继续上次诊断' : '继续上次诊断' }, holder)
    const responseIndex = targetIndex + 1
    await scrollBottom()
    try {
      const { data } = await api.post(
        `/systems/${systemId}/diagnosis-reports/${srcId}/resume`,
        { force_refresh: forceRefresh },
      )
      activeReportId.value = data.id
      if (data.status === 'running' && data.id) {
        const report = await waitForDiagnosisReport(systemId, data.id, (partial) => {
          showRunning(responseIndex, partial)
        })
        messages.value[responseIndex] = buildAgentMessageFromReport(report)
        if (report.status === 'failed') message.error(report.error_message || '继续执行失败')
      } else {
        // 已经有人继续过：直接渲染那条报告
        messages.value[responseIndex] = buildAgentMessageFromReport(data)
      }
      const completed = messages.value[responseIndex]
      if (completed.status === 'success' && completed.reportId) lastReportId.value = completed.reportId
      await loadHistory()
    } catch (error) {
      messages.value[responseIndex] = {
        role: 'agent', status: 'failed', text: `⚠️ 继续失败：${extractErrorMessage(error, '继续失败')}`,
      }
    } finally {
      diagnosing.value = false
      stopping.value = false
      activeReportId.value = null
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
    await loadHistory(true)
    await scrollBottom()
  })

  return {
    ask,
    cancelDiagnosis,
    resumeDiagnosis,
    stopping,
    requestFix,
    decide,
    chatBox,
    loadHistory,
    loadModelOptions,
    diagnosing,
    fixing,
    historyLoading,
    historyReports,
    chats,
    activeChatId,
    chatManaging,
    renameChat,
    deleteChat,
    workflowHistory,
    openWorkflowHistory,
    openHistoryReport,
    activeReportId,
    imageReads,
    messages,
    question,
    images,
    addImageFiles,
    removeImage,
    clearImages,
    maxImages: MAX_IMAGES,
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
