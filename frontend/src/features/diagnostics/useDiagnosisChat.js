import { nextTick, onMounted, ref } from 'vue'

import api from '../../api'

let markedParser = null

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
  const chatBox = ref(null)
  const markdownReady = ref(false)

  function saveMessages() {
    localStorage.setItem(storageKey(), JSON.stringify(messages.value))
  }

  function clearMessages() {
    messages.value = []
    localStorage.removeItem(storageKey())
  }

  function renderMd(text) {
    if (markdownReady.value && markedParser) {
      return markedParser.parse(text)
    }
    return escapeHtml(text).replaceAll('\n', '<br />')
  }

  async function scrollBottom() {
    await nextTick()
    if (chatBox.value) {
      chatBox.value.scrollTop = chatBox.value.scrollHeight
    }
  }

  async function ask() {
    const currentQuestion = question.value.trim()
    if (!currentQuestion) return
    messages.value.push({ role: 'user', text: currentQuestion })
    saveMessages()
    question.value = ''
    diagnosing.value = true
    await scrollBottom()
    try {
      const { data } = await api.post(`/systems/${systemId}/diagnose`, { question: currentQuestion })
      messages.value.push({ role: 'agent', text: data.answer })
    } catch (error) {
      messages.value.push({
        role: 'agent',
        text: `⚠️ 诊断失败：${error?.response?.data?.detail || error.message}`,
      })
    } finally {
      saveMessages()
      diagnosing.value = false
      await scrollBottom()
    }
  }

  onMounted(async () => {
    await ensureMarked()
    markdownReady.value = true
  })

  return {
    ask,
    chatBox,
    clearMessages,
    diagnosing,
    messages,
    question,
    renderMd,
  }
}
