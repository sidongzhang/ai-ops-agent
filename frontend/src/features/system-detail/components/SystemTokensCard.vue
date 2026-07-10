<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
})

const tokens = ref([])
const loading = ref(false)
const creating = ref(false)
const revokingId = ref(null)
const createdToken = ref(null)
const form = reactive({
  name: '业务系统接入 Token',
  scopes: ['alert:create', 'health:push', 'message:read', 'message:send', 'report:submit'],
})

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

onMounted(loadTokens)
</script>

<template>
  <a-card class="panel-card token-card" title="开放接入 Token">
    <template #extra>
      <a-button size="small" :loading="loading" @click="loadTokens">刷新</a-button>
    </template>

    <div class="token-body">
      <a-alert
        type="info"
        show-icon
        message="业务系统可使用 Token 调用开放接口上报告警。明文 Token 只在创建后显示一次。"
      />

      <div v-if="createdToken" class="created-token">
        <div class="created-title">新 Token 明文</div>
        <code>{{ createdToken.token }}</code>
        <div class="created-tip">请现在保存，离开页面后无法再次查看。</div>
      </div>

      <div class="token-create">
        <a-input v-model:value="form.name" placeholder="Token 名称" />
        <a-select v-model:value="form.scopes" mode="multiple" style="min-width:260px">
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
      >
        <a-table-column title="名称" data-index="name" />
        <a-table-column title="权限">
          <template #default="{ record }">
            <div class="scope-list">
              <a-tag v-for="scope in record.scopes" :key="scope" style="border:none">{{ scope }}</a-tag>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="状态">
          <template #default="{ record }">
            <a-tag :color="record.status === 'active' ? 'success' : 'default'" style="border:none">
              {{ record.status === 'active' ? '启用' : '已禁用' }}
            </a-tag>
          </template>
        </a-table-column>
        <a-table-column title="最后使用">
          <template #default="{ record }">{{ formatTime(record.last_used_at) }}</template>
        </a-table-column>
        <a-table-column title="操作" width="90">
          <template #default="{ record }">
            <a-popconfirm
              v-if="record.status === 'active'"
              title="确认禁用这个 Token 吗？"
              ok-text="禁用"
              ok-type="danger"
              cancel-text="取消"
              @confirm="revokeToken(record)"
            >
              <a-button size="small" danger :loading="revokingId === record.id">禁用</a-button>
            </a-popconfirm>
          </template>
        </a-table-column>
      </a-table>
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
}
.token-card {
  margin-top: 16px;
}
.token-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
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
.created-tip {
  margin-top: 6px;
  font-size: 12px;
  color: #d97706;
}
.scope-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
</style>
