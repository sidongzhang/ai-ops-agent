<script setup>
defineProps({
  system: { type: Object, required: true },
  notifyCfg: { type: Object, required: true },
  notifyEditingSecret: { type: Boolean, default: false },
  notifyLoading: { type: Boolean, default: false },
  notifySecretAlreadySet: { type: Boolean, default: false },
  notifyTestLoading: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:notifyEditingSecret',
  'notify-type-change',
  'save',
  'test',
])
</script>

<template>
  <a-card class="panel-card" style="margin-top:16px">
    <template #title>
      <div style="display:flex;align-items:center;gap:8px">
        <span>告警通知</span>
        <a-tag v-if="system.notify?.type === 'feishu'" color="blue" style="border:none;font-size:11px">飞书机器人</a-tag>
        <a-tag v-else-if="system.notify?.type === 'webhook'" color="orange" style="border:none;font-size:11px">Webhook</a-tag>
        <a-tag v-else style="border:none;font-size:11px;color:var(--text-subtle)">未配置</a-tag>
      </div>
    </template>
    <div class="notify-body">
      <div class="notify-row">
        <span class="notify-label">告警渠道</span>
        <a-radio-group v-model:value="notifyCfg.type" button-style="solid" size="small" @change="emit('notify-type-change')">
          <a-radio-button value="none">关闭</a-radio-button>
          <a-radio-button value="feishu">飞书机器人</a-radio-button>
          <a-radio-button value="webhook">Webhook URL</a-radio-button>
        </a-radio-group>
      </div>
      <div v-if="notifyCfg.type !== 'none' && notifyCfg.type !== (system.notify?.type || 'none')" class="notify-switch-tip">
        ⚠️ 切换后保存将覆盖已保存的
        <b>{{ system.notify?.type === 'feishu' ? '飞书机器人' : system.notify?.type === 'webhook' ? 'Webhook' : '' }}</b> 配置
      </div>
      <template v-if="notifyCfg.type === 'feishu'">
        <div class="notify-tip">在飞书开放平台创建自建应用，获取 App ID 和 App Secret；把机器人拉入目标群聊后填入 Chat ID（oc_ 开头）。</div>
        <div class="notify-fields">
          <div class="nfield">
            <span class="nfield-label">App ID</span>
            <a-input v-model:value="notifyCfg.app_id" placeholder="cli_xxxxxxxxxx" />
          </div>
          <div class="nfield">
            <span class="nfield-label">App Secret</span>
            <div v-if="notifySecretAlreadySet && !notifyEditingSecret" class="secret-row">
              <a-input-password value="••••••••••••••••" disabled class="secret-filled" />
              <a-button size="small" @click="emit('update:notifyEditingSecret', true)">修改</a-button>
            </div>
            <a-input-password
              v-else
              v-model:value="notifyCfg.app_secret"
              :placeholder="notifySecretAlreadySet ? '输入新密钥以替换' : 'App Secret'"
              autofocus
            />
          </div>
          <div class="nfield">
            <span class="nfield-label">群聊 Chat ID</span>
            <a-input v-model:value="notifyCfg.chat_id" placeholder="oc_xxxxxxxxxxxxxxxxxx" />
          </div>
        </div>
      </template>
      <template v-if="notifyCfg.type === 'webhook'">
        <div class="notify-tip">填入飞书/钉钉/Slack 群机器人的 Webhook 地址，告警触发时平台会 POST 一条文本消息。</div>
        <div class="notify-fields">
          <div class="nfield">
            <span class="nfield-label">Webhook URL</span>
            <a-input v-model:value="notifyCfg.webhook_url" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
          </div>
        </div>
      </template>
      <div class="notify-actions">
        <a-button type="primary" :loading="notifyLoading" @click="emit('save')">保存配置</a-button>
        <a-button v-if="notifyCfg.type !== 'none' && notifyCfg.type === (system.notify?.type || 'none')"
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
.notify-switch-tip {
  font-size: 12px;
  color: #D97706;
  background: rgba(251,191,36,.08);
  border: 1px solid rgba(251,191,36,.3);
  border-radius: 7px;
  padding: 7px 12px;
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
</style>
