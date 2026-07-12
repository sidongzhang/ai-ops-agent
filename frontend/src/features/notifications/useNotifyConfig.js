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
