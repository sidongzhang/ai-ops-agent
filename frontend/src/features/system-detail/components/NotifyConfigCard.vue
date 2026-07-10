<script setup>
import { computed } from 'vue'

const props = defineProps({
  system: { type: Object, required: true },
  notifyCfg: { type: Object, required: true },
  notifyEditingSecret: { type: Object, required: true },
  notifyLoading: { type: Boolean, default: false },
  notifySecretAlreadySet: { type: Object, required: true },
  notifyTestLoading: { type: Boolean, default: false },
})

const emit = defineEmits([
  'notify-type-change',
  'edit-secret',
  'save',
  'test',
])

const channelOptions = [
  { label: '飞书机器人', value: 'feishu' },
  { label: '邮件', value: 'email' },
  { label: 'Webhook URL', value: 'webhook' },
]

const savedChannels = computed(() => {
  const notify = props.system.notify || {}
  if (notify.channels?.length) return notify.channels
  return notify.type && notify.type !== 'none' ? [notify.type] : []
})

const hasUnsavedChannelChange = computed(() => (
  [...props.notifyCfg.channels].sort().join(',') !== [...savedChannels.value].sort().join(',')
))
</script>

<template>
  <a-card class="panel-card" style="margin-top:16px">
    <template #title>
      <div style="display:flex;align-items:center;gap:8px">
        <span>告警通知</span>
        <a-tag v-if="savedChannels.includes('feishu')" color="blue" style="border:none;font-size:11px">飞书</a-tag>
        <a-tag v-if="savedChannels.includes('email')" color="green" style="border:none;font-size:11px">邮件</a-tag>
        <a-tag v-if="savedChannels.includes('webhook')" color="orange" style="border:none;font-size:11px">Webhook</a-tag>
        <a-tag v-if="savedChannels.length === 0" style="border:none;font-size:11px;color:var(--text-subtle)">仅网页内提醒</a-tag>
      </div>
    </template>
    <div class="notify-body">
      <div class="notify-row">
        <span class="notify-label">告警渠道</span>
        <a-checkbox-group
          v-model:value="notifyCfg.channels"
          :options="channelOptions"
          @change="emit('notify-type-change')"
        />
      </div>
      <div v-if="notifyCfg.channels.length === 0" class="notify-tip">
        网页内消息始终保留。未选择外部渠道时，告警只进入消息中心。
      </div>
      <template v-if="notifyCfg.channels.includes('feishu')">
        <div class="notify-tip">在飞书开放平台创建自建应用，获取 App ID 和 App Secret；把机器人拉入目标群聊后填入 Chat ID（oc_ 开头）。</div>
        <div class="notify-fields">
          <div class="nfield">
            <span class="nfield-label">App ID</span>
            <a-input v-model:value="notifyCfg.app_id" placeholder="cli_xxxxxxxxxx" />
          </div>
          <div class="nfield">
            <span class="nfield-label">App Secret</span>
            <div v-if="notifySecretAlreadySet.app && !notifyEditingSecret.app" class="secret-row">
              <a-input-password value="••••••••••••••••" disabled class="secret-filled" />
              <a-button size="small" @click="emit('edit-secret', 'app')">修改</a-button>
            </div>
            <a-input-password
              v-else
              v-model:value="notifyCfg.app_secret"
              :placeholder="notifySecretAlreadySet.app ? '输入新密钥以替换' : 'App Secret'"
              autofocus
            />
          </div>
          <div class="nfield">
            <span class="nfield-label">群聊 Chat ID</span>
            <a-input v-model:value="notifyCfg.chat_id" placeholder="oc_xxxxxxxxxxxxxxxxxx" />
          </div>
        </div>
      </template>
      <template v-if="notifyCfg.channels.includes('webhook')">
        <div class="notify-tip">填入飞书/钉钉/Slack 群机器人的 Webhook 地址，告警触发时平台会 POST 一条文本消息。</div>
        <div class="notify-fields">
          <div class="nfield">
            <span class="nfield-label">Webhook URL</span>
            <a-input v-model:value="notifyCfg.webhook_url" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
          </div>
        </div>
      </template>
      <template v-if="notifyCfg.channels.includes('email')">
        <div class="notify-tip">填入收件人和 SMTP 信息。SMTP 密码会加密保存，API 返回时只显示已配置。</div>
        <div class="notify-fields">
          <div class="nfield">
            <span class="nfield-label">收件人</span>
            <a-input v-model:value="notifyCfg.email_to" placeholder="ops@example.com, owner@example.com" />
          </div>
          <div class="notify-grid">
            <div class="nfield">
              <span class="nfield-label">SMTP 主机</span>
              <a-input v-model:value="notifyCfg.smtp_host" placeholder="smtp.example.com" />
            </div>
            <div class="nfield">
              <span class="nfield-label">端口</span>
              <a-input-number v-model:value="notifyCfg.smtp_port" :min="1" :max="65535" style="width:100%" />
            </div>
          </div>
          <div class="notify-grid">
            <div class="nfield">
              <span class="nfield-label">用户名</span>
              <a-input v-model:value="notifyCfg.smtp_username" placeholder="ops@example.com" />
            </div>
            <div class="nfield">
              <span class="nfield-label">发件人</span>
              <a-input v-model:value="notifyCfg.smtp_from" placeholder="ops@example.com" />
            </div>
          </div>
          <div class="nfield">
            <span class="nfield-label">SMTP 密码</span>
            <div v-if="notifySecretAlreadySet.smtp && !notifyEditingSecret.smtp" class="secret-row">
              <a-input-password value="••••••••••••••••" disabled class="secret-filled" />
              <a-button size="small" @click="emit('edit-secret', 'smtp')">修改</a-button>
            </div>
            <a-input-password
              v-else
              v-model:value="notifyCfg.smtp_password"
              :placeholder="notifySecretAlreadySet.smtp ? '输入新密码以替换' : 'SMTP 密码或授权码'"
            />
          </div>
          <a-checkbox v-model:checked="notifyCfg.smtp_tls">启用 TLS</a-checkbox>
        </div>
      </template>
      <div class="notify-actions">
        <a-button type="primary" :loading="notifyLoading" @click="emit('save')">保存配置</a-button>
        <a-button v-if="notifyCfg.channels.length && !hasUnsavedChannelChange"
                  :loading="notifyTestLoading" @click="emit('test')">发送测试通知</a-button>
      </div>
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
}
.notify-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.notify-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.notify-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-subtle);
  width: 60px;
  flex-shrink: 0;
}
.notify-tip {
  font-size: 12px;
  color: var(--text-subtle);
  background: var(--body-bg);
  border: 1px solid var(--border-color);
  border-radius: 7px;
  padding: 8px 12px;
  line-height: 1.6;
}
.notify-fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.notify-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 160px;
  gap: 10px;
}
.nfield {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.nfield-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-subtle);
}
.notify-actions {
  display: flex;
  gap: 10px;
  padding-top: 4px;
}
.secret-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.secret-filled {
  flex: 1;
  color: var(--text-subtle) !important;
  background: var(--body-bg) !important;
}

@media (max-width: 720px) {
  .notify-row {
    align-items: flex-start;
    flex-direction: column;
  }
  .notify-grid {
    grid-template-columns: 1fr;
  }
}
</style>
