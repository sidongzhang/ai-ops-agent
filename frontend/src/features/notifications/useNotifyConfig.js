import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'

import api from '../../api'

export function useNotifyConfig(systemId, reloadSystem) {
  const notifyCfg = reactive({
    type: 'none',
    app_id: '',
    app_secret: '',
    chat_id: '',
    webhook_url: '',
  })
  const notifySecretAlreadySet = ref(false)
  const notifyEditingSecret = ref(false)
  const notifyLoading = ref(false)
  const notifyTestLoading = ref(false)

  function syncNotifyFromSystem(system) {
    const notify = system?.notify || {}
    notifyCfg.type = notify.type || 'none'
    notifyCfg.app_id = notify.app_id || ''
    notifySecretAlreadySet.value = notify.app_secret === '***'
    notifyEditingSecret.value = false
    notifyCfg.app_secret = ''
    notifyCfg.chat_id = notify.chat_id || ''
    notifyCfg.webhook_url = notify.webhook_url || ''
  }

  function onNotifyTypeChange() {
    notifyEditingSecret.value = false
    notifyCfg.app_secret = ''
  }

  async function saveNotify() {
    notifyLoading.value = true
    try {
      const body = { ...notifyCfg }
      if (!body.app_secret && notifySecretAlreadySet.value) {
        body.app_secret = '***'
      }
      await api.put(`/systems/${systemId}/notify`, body)
      message.success('通知配置已保存')
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
      await api.post(`/systems/${systemId}/notify/test`)
      message.success('测试通知已发送，请查看群聊')
    } catch (error) {
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
    onNotifyTypeChange,
    saveNotify,
    syncNotifyFromSystem,
    testNotify,
  }
}
