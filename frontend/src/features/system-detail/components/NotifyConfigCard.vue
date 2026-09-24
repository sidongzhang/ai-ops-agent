<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  system: { type: Object, required: true },
  notifyCfg: { type: Object, required: true },
  notifyEditingSecret: { type: Object, required: true },
  notifyLoading: { type: Boolean, default: false },
  notifySecretAlreadySet: { type: Object, required: true },
  notifyTestLoading: { type: Boolean, default: false },
  notifyTestResult: { type: Object, default: null },
  dailyReportEnabled: { type: Boolean, default: false },
  dailyReportLoading: { type: Boolean, default: false },
})

const emit = defineEmits([
  'toggle-channel',
  'edit-secret',
  'save',
  'test',
  'toggle-daily-report',
])

const guideOpen = ref([])

const channelOptions = [
  { label: '飞书机器人', value: 'feishu', desc: '发到飞书群' },
  { label: '邮件', value: 'email', desc: '发到告警邮箱' },
  { label: 'Webhook', value: 'webhook', desc: '转发到其他系统' },
]

const smtpPresets = [
  { key: '163', label: '网易 163', host: 'smtp.163.com', port: 465, tls: true },
  { key: 'qq', label: 'QQ 邮箱', host: 'smtp.qq.com', port: 465, tls: true },
  { key: 'exmail', label: '腾讯企业邮', host: 'smtp.exmail.qq.com', port: 465, tls: true },
  { key: 'gmail', label: 'Gmail', host: 'smtp.gmail.com', port: 587, tls: true },
]

const savedChannels = computed(() => {
  const notify = props.system.notify || {}
  if (notify.channels?.length) return notify.channels
  return notify.type && notify.type !== 'none' ? [notify.type] : []
})

const hasUnsavedChannelChange = computed(() => (
  [...props.notifyCfg.channels].sort().join(',') !== [...savedChannels.value].sort().join(',')
))
const activeChannelOptions = computed(() => channelOptions.filter((item) => isChannelActive(item.value)))
const allActiveChannelsReady = computed(() => activeChannelOptions.value.every((item) => channelReady(item.value)))
const emailRecipients = computed(() => splitEmails(props.notifyCfg.email_to))
const invalidEmailRecipients = computed(() => emailRecipients.value.filter((item) => !isEmail(item)))
const emailSummary = computed(() => {
  if (!emailRecipients.value.length) return '尚未填写收件邮箱'
  if (emailRecipients.value.length === 1) return emailRecipients.value[0]
  return `${emailRecipients.value[0]} 等 ${emailRecipients.value.length} 个邮箱`
})

function splitEmails(value) {
  return String(value || '')
    .split(/[,，;\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function isEmail(value) {
  return /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/.test(String(value || '').trim())
}

function applySmtpPreset(preset) {
  props.notifyCfg.smtp_host = preset.host
  props.notifyCfg.smtp_port = preset.port
  props.notifyCfg.smtp_tls = preset.tls
  fillEmailIdentity()
}

function fillEmailIdentity() {
  const first = emailRecipients.value[0]
  if (!first) return
  if (!props.notifyCfg.smtp_username) props.notifyCfg.smtp_username = first
  if (!props.notifyCfg.smtp_from) props.notifyCfg.smtp_from = first
}

function isChannelActive(value) {
  return props.notifyCfg.channels.includes(value)
}

function channelMissingFields(value) {
  if (value === 'feishu') {
    return [
      !props.notifyCfg.app_id && 'App ID',
      !(props.notifySecretAlreadySet.app || props.notifyCfg.app_secret) && 'App Secret',
      !props.notifyCfg.chat_id && '群聊 Chat ID',
    ].filter(Boolean)
  }
  if (value === 'email') {
    return [
      !props.notifyCfg.email_to && '告警收件邮箱',
      invalidEmailRecipients.value.length > 0 && `邮箱格式：${invalidEmailRecipients.value.join('、')}`,
      !props.notifyCfg.smtp_host && 'SMTP 主机',
      !props.notifyCfg.smtp_port && '端口',
      !props.notifyCfg.smtp_username && '用户名',
      !props.notifyCfg.smtp_from && '发件人',
      props.notifyCfg.smtp_from && !isEmail(props.notifyCfg.smtp_from) && '发件人邮箱格式',
      !(props.notifySecretAlreadySet.smtp || props.notifyCfg.smtp_password) && 'SMTP 密码',
    ].filter(Boolean)
  }
  if (value === 'webhook') {
    return [
      !props.notifyCfg.webhook_url && 'Webhook URL',
    ].filter(Boolean)
  }
  return []
}

function channelReady(value) {
  return channelMissingFields(value).length === 0
}
</script>

<template>
  <div class="notify-section">
    <div class="notify-head">
      <div class="notify-title-row">
        <span class="notify-title">告警通知</span>
        <a-tag v-if="savedChannels.length === 0" style="border:none;font-size:11px;color:var(--text-subtle)">仅站内消息</a-tag>
        <a-tag v-for="channel in savedChannels" :key="channel" color="blue" style="border:none;font-size:11px">
          {{ channelOptions.find((item) => item.value === channel)?.label || channel }}
        </a-tag>
      </div>
      <div class="notify-summary">勾选外部渠道后填写参数并保存。不选则告警只进消息中心。</div>
    </div>

    <div class="daily-report-setting">
      <div>
        <div class="daily-report-title">每日健康日报</div>
        <div class="daily-report-desc">每天 08:00 汇总昨日告警、恢复、诊断记录和当前服务状态，发送到站内及已配置通知渠道。</div>
      </div>
      <a-switch
        :checked="dailyReportEnabled"
        :loading="dailyReportLoading"
        checked-children="开"
        un-checked-children="关"
        @change="(checked) => emit('toggle-daily-report', checked)"
      />
    </div>

    <a-collapse v-model:activeKey="guideOpen" ghost expand-icon-position="start" class="notify-guide">
      <a-collapse-panel key="guide">
        <template #header>
          <span class="guide-header">配置说明</span>
        </template>

        <div class="guide-flow">
          <div class="guide-flow-step"><strong>1.</strong> 点上方卡片选择渠道（可多选）</div>
          <div class="guide-flow-step"><strong>2.</strong> 按下面说明填写参数</div>
          <div class="guide-flow-step"><strong>3.</strong> 保存配置 → 发送测试通知</div>
        </div>

        <div class="guide-block">
          <div class="guide-block-title">飞书机器人</div>
          <ol class="guide-list">
            <li>打开 <a href="https://open.feishu.cn/app" target="_blank" rel="noopener">飞书开放平台</a>，创建<strong>企业自建应用</strong></li>
            <li>在「凭证与基础信息」复制 <code>App ID</code>、<code>App Secret</code></li>
            <li>在「权限管理」开通：<code>im:message</code>、<code>im:message:send_as_bot</code></li>
            <li>发布应用版本，并把机器人<strong>拉进目标群聊</strong></li>
            <li>获取群 <code>Chat ID</code>（<code>oc_</code> 开头）：可在群设置里查看，或通过开放平台 API 查询</li>
            <li>回到这里选中「飞书」，填入三项 → 保存 → 测试</li>
          </ol>
        </div>

        <div class="guide-block">
          <div class="guide-block-title">邮件（以网易 163 为例）</div>
          <ol class="guide-list">
            <li>登录 <a href="https://mail.163.com" target="_blank" rel="noopener">mail.163.com</a> → 设置 → POP3/SMTP/IMAP</li>
            <li>开启 <strong>SMTP 服务</strong>，按提示生成<strong>授权码</strong>（不是登录密码）</li>
            <li>选中「邮件」后填写：</li>
          </ol>
          <div class="guide-table">
            <div class="guide-row"><span>告警收件邮箱</span><code>你的邮箱，多个用逗号分隔</code></div>
            <div class="guide-row"><span>SMTP 主机</span><code>smtp.163.com</code></div>
            <div class="guide-row"><span>端口</span><code>465</code>（163 必须用 SSL，587 容易失败）</div>
            <div class="guide-row"><span>用户名 / 发件人</span><code>你的完整邮箱地址</code></div>
            <div class="guide-row"><span>SMTP 密码</span><code>填授权码，不要填登录密码</code></div>
          </div>
        </div>

        <div class="guide-block">
          <div class="guide-block-title">Webhook</div>
          <ol class="guide-list">
            <li>在飞书 / 钉钉 / Slack 群聊里添加「自定义机器人」</li>
            <li>复制机器人提供的 Webhook 地址</li>
            <li>选中「Webhook」，粘贴 URL → 保存 → 测试</li>
          </ol>
        </div>

        <div class="guide-note">
          系统编码 <code>{{ system.key || '—' }}</code> 仅 OpenAPI 接入时需要；告警通知本身不需要填系统编码。
        </div>
      </a-collapse-panel>
    </a-collapse>

    <div class="channel-picker">
      <button
        v-for="option in channelOptions"
        :key="option.value"
        type="button"
        :class="['channel-pill', { active: isChannelActive(option.value) }]"
        @click="emit('toggle-channel', option.value)"
      >
        <span class="channel-pill-label-row">
          <span class="channel-pill-label">{{ option.label }}</span>
          <span v-if="isChannelActive(option.value)" :class="['channel-state', channelReady(option.value) ? 'ready' : 'missing']">
            {{ channelReady(option.value) ? '已配齐' : '待补充' }}
          </span>
        </span>
        <span class="channel-pill-desc">{{ option.desc }}</span>
      </button>
    </div>

    <div v-if="notifyCfg.channels.length" class="readiness-row">
      <span
        v-for="option in activeChannelOptions"
        :key="option.value"
        :class="['readiness-pill', channelReady(option.value) ? 'ready' : 'missing']"
      >
        {{ option.label }}：{{ channelReady(option.value) ? '可以测试发送' : '配置未完成' }}
      </span>
    </div>

    <div v-if="activeChannelOptions.some((item) => !channelReady(item.value))" class="missing-list">
      <div
        v-for="option in activeChannelOptions.filter((item) => !channelReady(item.value))"
        :key="option.value"
        class="missing-item"
      >
        <strong>{{ option.label }}</strong>
        <span>还缺：{{ channelMissingFields(option.value).join('、') }}</span>
      </div>
    </div>

    <template v-if="notifyCfg.channels.includes('feishu')">
      <div class="channel-block">
        <div class="channel-head">
          <div class="channel-title">飞书机器人</div>
          <div class="channel-sub">创建自建应用，把机器人拉进群后填写 Chat ID</div>
        </div>
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
            />
          </div>
          <div class="nfield">
            <span class="nfield-label">群聊 Chat ID</span>
            <a-input v-model:value="notifyCfg.chat_id" placeholder="oc_xxxxxxxxxxxxxxxxxx" />
          </div>
        </div>
      </div>
    </template>

    <template v-if="notifyCfg.channels.includes('webhook')">
      <div class="channel-block">
        <div class="channel-head">
          <div class="channel-title">Webhook 通知</div>
          <div class="channel-sub">支持飞书 / 钉钉 / Slack 群机器人 Webhook</div>
        </div>
        <div class="notify-fields">
          <div class="nfield">
            <span class="nfield-label">Webhook URL</span>
            <a-input v-model:value="notifyCfg.webhook_url" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
          </div>
        </div>
      </div>
    </template>

    <template v-if="notifyCfg.channels.includes('email')">
      <div class="channel-block">
        <div class="channel-head">
          <div class="channel-title">邮件告警</div>
          <div class="channel-sub">巡检异常时自动发到：{{ emailSummary }}</div>
        </div>
        <div class="notify-fields">
          <div class="nfield">
            <span class="nfield-label">告警收件邮箱</span>
            <a-input
              v-model:value="notifyCfg.email_to"
              placeholder="ops@example.com, owner@example.com"
              @blur="fillEmailIdentity"
            />
            <div v-if="emailRecipients.length" class="recipient-row">
              <span v-for="email in emailRecipients" :key="email" class="recipient-pill">{{ email }}</span>
            </div>
          </div>
          <div class="preset-row">
            <span class="preset-label">SMTP 快速填写</span>
            <button
              v-for="preset in smtpPresets"
              :key="preset.key"
              type="button"
              class="preset-chip"
              @click="applySmtpPreset(preset)"
            >
              {{ preset.label }}
            </button>
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
          <a-checkbox v-model:checked="notifyCfg.smtp_tls">
            启用 TLS / SSL（465 通常需要开启，密码一般填邮箱授权码）
          </a-checkbox>
        </div>
      </div>
    </template>

    <div class="notify-actions">
      <a-button type="primary" :loading="notifyLoading" @click="emit('save')">保存配置</a-button>
      <a-button
        v-if="notifyCfg.channels.length && !hasUnsavedChannelChange"
        :loading="notifyTestLoading"
        :disabled="!allActiveChannelsReady"
        @click="emit('test')"
      >
        发送测试通知
      </a-button>
    </div>

    <div v-if="notifyTestResult" class="test-result">
      <div class="test-result-title">最近一次测试结果</div>
      <div class="test-result-summary">
        {{ notifyTestResult.ok === false ? notifyTestResult.message : (notifyTestResult.message || '测试通知已发送') }}
      </div>
      <div v-if="notifyTestResult.channels?.length" class="test-result-channels">
        <span
          v-for="item in notifyTestResult.channels"
          :key="item.type"
          :class="['readiness-pill', item.status === 'success' ? 'ready' : 'missing']"
        >
          {{ channelOptions.find((option) => option.value === item.type)?.label || item.type }}：{{ item.status === 'success' ? '成功' : '失败' }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.notify-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
}
.daily-report-setting {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--surface-muted, rgba(120, 120, 120, .04));
}
.daily-report-title { font-size: 13px; font-weight: 650; color: var(--text); }
.daily-report-desc { margin-top: 3px; font-size: 12px; line-height: 1.5; color: var(--text-subtle); }
.notify-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.notify-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
}
.notify-summary {
  margin-top: 6px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
}
.channel-picker {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.channel-pill {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 12px;
  border: 1.5px solid var(--border-color);
  border-radius: 10px;
  background: var(--body-bg);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.channel-pill:hover {
  border-color: color-mix(in srgb, var(--primary) 35%, var(--border-color));
}
.channel-pill.active {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, var(--body-bg));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 10%, transparent);
}
.channel-pill-label-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.channel-pill-label {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}
.channel-state {
  margin-left: auto;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 999px;
}
.channel-state.ready {
  background: rgba(85,117,104,0.12);
  color: #557568;
}
.channel-state.missing {
  background: rgba(154,107,58,0.12);
  color: #9a6b3a;
}
.channel-pill-desc {
  font-size: 11px;
  color: var(--text-subtle);
}
.readiness-row,
.test-result-channels {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.missing-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(154,107,58,0.18);
  border-radius: 8px;
  background: rgba(154,107,58,0.05);
}
.missing-item {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: #9a6b3a;
  font-size: 12px;
  line-height: 1.5;
}
.missing-item strong {
  color: var(--text);
}
.readiness-pill {
  padding: 4px 9px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border-color);
}
.readiness-pill.ready {
  background: rgba(85,117,104,0.08);
  color: #557568;
}
.readiness-pill.missing {
  background: rgba(154,107,58,0.08);
  color: #9a6b3a;
}
.channel-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--card-bg);
}
.channel-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.channel-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}
.channel-sub {
  font-size: 12px;
  color: var(--text-subtle);
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
.recipient-row,
.preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.recipient-pill {
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--border-color);
  background: var(--body-bg);
  color: var(--text-subtle);
  font-size: 12px;
}
.preset-row {
  align-items: center;
}
.preset-label {
  color: var(--text-subtle);
  font-size: 12px;
  font-weight: 600;
}
.preset-chip {
  border: 1px solid var(--border-color);
  background: var(--body-bg);
  color: var(--text);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.preset-chip:hover {
  border-color: color-mix(in srgb, var(--primary) 35%, var(--border-color));
  color: var(--primary);
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
}
.test-result {
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--body-bg);
}
.test-result-title {
  color: var(--text);
  font-size: 13px;
  font-weight: 700;
}
.test-result-summary {
  margin-top: 4px;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.6;
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
.notify-guide {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--body-bg);
  padding: 0 12px;
}
.notify-guide :deep(.ant-collapse-header) {
  padding: 10px 0 !important;
  color: var(--primary) !important;
  font-size: 13px;
  font-weight: 600;
}
.notify-guide :deep(.ant-collapse-content-box) {
  padding: 0 0 12px !important;
}
.guide-header {
  color: var(--primary);
}
.guide-flow {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
}
.guide-flow-step {
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.6;
}
.guide-block {
  margin-top: 10px;
}
.guide-block-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 6px;
}
.guide-list {
  margin: 0 0 0 18px;
  padding: 0;
  color: var(--text-subtle);
  font-size: 12px;
  line-height: 1.7;
}
.guide-list li + li {
  margin-top: 4px;
}
.guide-list a {
  color: var(--primary);
}
.guide-list code,
.guide-table code,
.guide-note code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  font-size: 11px;
  color: var(--text);
}
.guide-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
}
.guide-row {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  font-size: 12px;
}
.guide-row span {
  color: var(--text-subtle);
  font-weight: 600;
}
.guide-note {
  margin-top: 12px;
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.6;
}

@media (max-width: 720px) {
  .channel-picker {
    grid-template-columns: 1fr;
  }
  .notify-grid {
    grid-template-columns: 1fr;
  }
  .guide-row {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
