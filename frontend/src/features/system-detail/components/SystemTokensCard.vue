<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
  systemKey: { type: String, default: '' },
})

const tokens = ref([])
const loading = ref(false)
const creating = ref(false)
const revokingId = ref(null)
const deletingId = ref(null)
const createdToken = ref(null)
const guideOpen = ref([])
const form = reactive({
  name: '外部系统接入凭证',
  scopes: ['log:analyze'],
})
const apiBase = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
const apiExamples = computed(() => ({
  log: `curl -X POST ${apiBase}/openapi/v1/log-analysis \\
  -H "Authorization: Bearer <TOKEN>" \\
  -H "X-System-Code: ${props.systemKey || '<系统编码>'}" \\
  -F "file=@./log.json" \\
  -F "question=请分析这份日志，给出故障原因和处理措施"`,
  alert: `curl -X POST ${apiBase}/openapi/v1/alerts \\
  -H "Authorization: Bearer <TOKEN>" \\
  -H "X-System-Code: ${props.systemKey || '<系统编码>'}" \\
  -H "Content-Type: application/json" \\
  -d '{"request_id":"alert-001","title":"库存同步异常","severity":"warning"}'`,
  health: `curl -X POST ${apiBase}/openapi/v1/health \\
  -H "Authorization: Bearer <TOKEN>" \\
  -H "X-System-Code: ${props.systemKey || '<系统编码>'}" \\
  -H "Content-Type: application/json" \\
  -d '{"services":[{"name":"inventory-api","ok":true,"detail":"正常"}]}'`,
}))

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

async function loadTokens() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/tokens`)
    tokens.value = data
  } catch (error) {
    message.error(error?.response?.data?.detail || 'Token 加载失败')
  } finally {
    loading.value = false
  }
}

async function createToken() {
  if (!form.name.trim()) return message.warning('请输入 Token 名称')
  creating.value = true
  try {
    const { data } = await api.post(`/systems/${props.systemId}/tokens`, {
      name: form.name.trim(),
      scopes: form.scopes,
    })
    createdToken.value = data
    message.success('Token 已创建，请立即保存明文')
    await loadTokens()
  } catch (error) {
    message.error(error?.response?.data?.detail || 'Token 创建失败')
  } finally {
    creating.value = false
  }
}

async function revokeToken(token) {
  revokingId.value = token.id
  try {
    await api.post(`/systems/${props.systemId}/tokens/${token.id}/revoke`)
    message.success('Token 已禁用')
    await loadTokens()
  } catch (error) {
    message.error(error?.response?.data?.detail || '禁用失败')
  } finally {
    revokingId.value = null
  }
}

async function deleteToken(token) {
  deletingId.value = token.id
  try {
    await api.delete(`/systems/${props.systemId}/tokens/${token.id}`)
    message.success('Token 已永久删除')
    await loadTokens()
  } catch (error) {
    message.error(error?.response?.data?.detail || '删除失败')
  } finally {
    deletingId.value = null
  }
}

function copyText(value) {
  navigator.clipboard.writeText(value).then(
    () => message.success('命令已复制'),
    () => message.error('复制失败，请手动复制'),
  )
}

onMounted(loadTokens)
</script>

<template>
  <a-card class="panel-card config-section-card" title="开放接口">
    <template #extra>
      <a-button size="small" :loading="loading" @click="loadTokens">刷新</a-button>
    </template>

    <div class="token-body">
      <div class="token-summary">
        给<strong>对方业务系统</strong>发 API 凭证，让它们不用登录控制台，就能向平台推送日志、告警或健康状态。
        系统编码：<code>{{ systemKey || '—' }}</code>
      </div>

      <div v-if="createdToken" class="created-token">
        <div class="created-title">新 Token 明文（仅显示一次）</div>
        <code>{{ createdToken.token }}</code>
      </div>

      <div class="token-create">
        <a-input v-model:value="form.name" placeholder="凭证名称，例如：生产环境上报" />
        <a-select v-model:value="form.scopes" mode="multiple" style="min-width:240px">
          <a-select-option value="log:analyze">上传日志并分析</a-select-option>
          <a-select-option value="alert:create">上报告警</a-select-option>
          <a-select-option value="health:push">上报健康状态</a-select-option>
          <a-select-option value="message:read">查询消息结果</a-select-option>
          <a-select-option value="message:send">发送普通消息</a-select-option>
          <a-select-option value="report:submit">提交运行报告</a-select-option>
        </a-select>
        <a-button type="primary" :loading="creating" @click="createToken">创建 Token</a-button>
      </div>

      <a-table
        :data-source="tokens"
        :loading="loading"
        :pagination="false"
        row-key="id"
        size="small"
        :scroll="{ x: 760 }"
      >
        <a-table-column title="名称" data-index="name" />
        <a-table-column title="权限">
          <template #default="{ record }">
            <div class="scope-list"><a-tag v-for="scope in record.scopes" :key="scope" style="border:none">{{ scope }}</a-tag></div>
          </template>
        </a-table-column>
        <a-table-column title="状态">
          <template #default="{ record }"><a-tag :color="record.status === 'active' ? 'success' : 'default'" style="border:none">{{ record.status === 'active' ? '启用' : '已禁用' }}</a-tag></template>
        </a-table-column>
        <a-table-column title="最后使用"><template #default="{ record }">{{ formatTime(record.last_used_at) }}</template></a-table-column>
        <a-table-column title="操作" :width="190">
          <template #default="{ record }">
            <div class="token-actions">
              <a-popconfirm v-if="record.status === 'active'" title="确认禁用这个 Token 吗？" ok-text="禁用" ok-type="danger" cancel-text="取消" @confirm="revokeToken(record)">
                <a-button size="small" danger :loading="revokingId === record.id">禁用</a-button>
              </a-popconfirm>
              <span v-if="record.status === 'active'" class="action-hint">禁用后可删除</span>
              <a-popconfirm
                v-else
                title="永久删除这个 Token？"
                ok-text="删除"
                ok-type="danger"
                cancel-text="取消"
                @confirm="deleteToken(record)"
              >
                <a-button size="small" danger ghost :loading="deletingId === record.id">删除</a-button>
              </a-popconfirm>
            </div>
          </template>
        </a-table-column>
      </a-table>

      <a-collapse v-model:activeKey="guideOpen" ghost expand-icon-position="start" class="token-guide">
        <a-collapse-panel key="guide" header="调用方式（curl）">
          <div class="example-block">
            <div class="example-title"><span>上传日志并分析</span><a-button size="small" type="link" @click="copyText(apiExamples.log)">复制</a-button></div>
            <pre>{{ apiExamples.log }}</pre>
          </div>
          <div class="example-block">
            <div class="example-title"><span>上报告警</span><a-button size="small" type="link" @click="copyText(apiExamples.alert)">复制</a-button></div>
            <pre>{{ apiExamples.alert }}</pre>
          </div>
          <div class="example-block">
            <div class="example-title"><span>上报健康状态</span><a-button size="small" type="link" @click="copyText(apiExamples.health)">复制</a-button></div>
            <pre>{{ apiExamples.health }}</pre>
          </div>
        </a-collapse-panel>
      </a-collapse>
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  margin-top: 16px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
}
.token-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.token-summary {
  color: var(--text-subtle);
  font-size: 13px;
  line-height: 1.7;
}
.token-summary code {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--body-bg);
  color: var(--text);
  font-size: 12px;
}
.token-create {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.created-token {
  border: 1px solid rgba(22,119,255,.25);
  background: rgba(22,119,255,.06);
  border-radius: 8px;
  padding: 10px 12px;
}
.created-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-subtle);
  margin-bottom: 6px;
}
.created-token code {
  display: block;
  overflow-wrap: anywhere;
  font-size: 12px;
  color: var(--text);
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  padding: 8px;
}
.scope-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.token-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  flex-wrap: wrap;
}
.action-hint {
  font-size: 12px;
  color: var(--text-subtle);
}
.token-guide {
  border-top: 1px solid var(--border-color);
  padding-top: 4px;
}
.token-guide :deep(.ant-collapse-header) {
  padding: 8px 0 !important;
  color: var(--primary) !important;
  font-size: 12px;
}
.token-guide :deep(.ant-collapse-content-box) {
  padding: 0 0 8px !important;
}
.example-block {
  margin-top: 8px;
}
.example-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--text-subtle);
  font-size: 12px;
}
.example-block pre {
  margin: 4px 0 0;
  padding: 8px 10px;
  overflow-x: auto;
  border-radius: 6px;
  background: #1e1e2e;
  color: #cdd6f4;
  font: 11px/1.55 'SF Mono', 'Monaco', 'Consolas', monospace;
  white-space: pre-wrap;
}

@media (max-width: 768px) {
  .token-create {
    align-items: stretch;
  }
  .token-create > * {
    width: 100%;
  }
}
</style>
