import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'

import api from '../../api'

export function useNotifyConfig(systemId, reloadSystem) {
  const notifyCfg = reactive({
    type: 'none',
    channels: [],
    app_id: '',
    app_secret: '',
    chat_id: '',
    webhook_url: '',
    email_to: '',
    smtp_host: '',
    smtp_port: 587,
    smtp_username: '',
    smtp_password: '',
    smtp_from: '',
    smtp_tls: true,
  })
  const notifySecretAlreadySet = reactive({ app: false, smtp: false })
  const notifyEditingSecret = reactive({ app: false, smtp: false })
  const notifyLoading = ref(false)
  const notifyTestLoading = ref(false)
  const notifyTestResult = ref(null)

  function syncNotifyFromSystem(system) {
    const notify = system?.notify || {}
    notifyCfg.type = notify.type || 'none'
    notifyCfg.channels = notify.channels?.length
      ? [...notify.channels]
      : (notify.type && notify.type !== 'none' ? [notify.type] : [])
    notifyCfg.app_id = notify.app_id || ''
    notifySecretAlreadySet.app = notify.app_secret === '***'
    notifySecretAlreadySet.smtp = notify.smtp_password === '***'
    notifyEditingSecret.app = false
    notifyEditingSecret.smtp = false
    notifyCfg.app_secret = ''
    notifyCfg.chat_id = notify.chat_id || ''
    notifyCfg.webhook_url = notify.webhook_url || ''
    notifyCfg.email_to = notify.email_to || ''
    notifyCfg.smtp_host = notify.smtp_host || ''
    notifyCfg.smtp_port = notify.smtp_port || 587
    notifyCfg.smtp_username = notify.smtp_username || ''
    notifyCfg.smtp_password = ''
    notifyCfg.smtp_from = notify.smtp_from || ''
    notifyCfg.smtp_tls = notify.smtp_tls !== false
    notifyTestResult.value = null
  }

  function toggleNotifyChannel(channel) {
    const next = new Set(notifyCfg.channels)
    if (next.has(channel)) next.delete(channel)
    else next.add(channel)
    notifyCfg.channels = [...next]
    notifyCfg.type = notifyCfg.channels[0] || 'none'
  }

  function startEditingNotifySecret(secretType) {
    notifyEditingSecret[secretType] = true
    if (secretType === 'app') notifyCfg.app_secret = ''
    if (secretType === 'smtp') notifyCfg.smtp_password = ''
  }

  function splitEmails(value) {
    return String(value || '')
      .split(/[,，;\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  }

  function isEmail(value) {
    return /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/.test(String(value || '').trim())
  }

  function validateNotifyBeforeSave(body) {
    if (body.channels.includes('feishu')) {
      const missing = [
        !body.app_id && 'App ID',
        !body.app_secret && 'App Secret',
        !body.chat_id && '群聊 Chat ID',
      ].filter(Boolean)
      if (missing.length) return `飞书配置还缺：${missing.join('、')}`
    }
    if (body.channels.includes('webhook')) {
      if (!body.webhook_url) return 'Webhook 配置还缺：Webhook URL'
      if (!/^https?:\/\//.test(body.webhook_url)) return 'Webhook URL 必须以 http:// 或 https:// 开头'
    }
    if (body.channels.includes('email')) {
      const recipients = splitEmails(body.email_to)
      const invalid = recipients.filter((item) => !isEmail(item))
      if (!recipients.length) return '邮件配置还缺：告警收件邮箱'
      if (invalid.length) return `收件邮箱格式不正确：${invalid.join('、')}`
      const missing = [
        !body.smtp_host && 'SMTP 主机',
        !body.smtp_port && '端口',
        !body.smtp_username && '用户名',
        !body.smtp_from && '发件人',
        !body.smtp_password && 'SMTP 密码',
      ].filter(Boolean)
      if (missing.length) return `邮件配置还缺：${missing.join('、')}`
      if (!isEmail(body.smtp_from)) return '发件人邮箱格式不正确'
    }
    return ''
  }

  async function saveNotify() {
    notifyLoading.value = true
    try {
      const body = { ...notifyCfg }
      body.channels = [...notifyCfg.channels]
      body.type = body.channels[0] || 'none'
      if (!body.app_secret && notifySecretAlreadySet.app && !notifyEditingSecret.app) {
        body.app_secret = '***'
      }
      if (!body.smtp_password && notifySecretAlreadySet.smtp && !notifyEditingSecret.smtp) {
        body.smtp_password = '***'
      }
      const validationError = validateNotifyBeforeSave(body)
      if (validationError) {
        message.warning(validationError)
        return
      }
      await api.put(`/systems/${systemId}/notify`, body)
      message.success('通知配置已保存')
      notifyTestResult.value = null
      await reloadSystem()
    } catch (error) {
      message.error(error?.response?.data?.detail || '保存失败')
    } finally {
      notifyLoading.value = false
    }
  }

  async function testNotify() {
    notifyTestLoading.value = true
    try {
      const { data } = await api.post(`/systems/${systemId}/notify/test`)
      notifyTestResult.value = data
      const successCount = data?.channels?.filter((item) => item.status === 'success').length || 0
      message.success(`测试通知已发送：${successCount} 个渠道成功`)
    } catch (error) {
      notifyTestResult.value = {
        ok: false,
        message: error?.response?.data?.detail || '发送失败，请检查配置',
        channels: [],
      }
      message.error(error?.response?.data?.detail || '发送失败，请检查配置')
    } finally {
      notifyTestLoading.value = false
    }
  }

  return {
    notifyCfg,
    notifyEditingSecret,
    notifyLoading,
    notifySecretAlreadySet,
    notifyTestLoading,
    notifyTestResult,
    toggleNotifyChannel,
    saveNotify,
    startEditingNotifySecret,
    syncNotifyFromSystem,
    testNotify,
  }
}
