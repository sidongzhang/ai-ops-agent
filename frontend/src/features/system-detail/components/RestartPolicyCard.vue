<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
  restartCapability: { type: Object, default: null },
})

const loading = ref(false)
const saving = ref(false)
const policy = ref(null)
const orgUsers = ref([])
const selectedUserIds = ref([])

const canManage = computed(() => Boolean(policy.value?.can_manage))
const hasPermission = computed(() => Boolean(policy.value?.has_permission))
const restartableCount = computed(() => (
  props.restartCapability?.services?.filter((item) => item.restartable).length || 0
))

const userOptions = computed(() => orgUsers.value.map((user) => ({
  value: user.id,
  label: `${user.email}${user.role === 'owner' ? ' · owner' : ''}`,
})))

async function loadPolicy() {
  loading.value = true
  try {
    const requests = [
      api.get(`/systems/${props.systemId}/restart-policy`),
    ]
    if (!orgUsers.value.length) {
      requests.push(api.get('/auth/users'))
    }
    const [{ data: policyData }, usersResp] = await Promise.all(requests)
    policy.value = policyData
    selectedUserIds.value = [...(policyData.authorized_user_ids || [])]
    if (usersResp?.data) orgUsers.value = usersResp.data
  } catch (error) {
    message.error(error?.response?.data?.detail || '重启策略加载失败')
  } finally {
    loading.value = false
  }
}

async function savePolicy() {
  if (!canManage.value) return
  saving.value = true
  try {
    const { data } = await api.put(`/systems/${props.systemId}/restart-policy`, {
      authorized_user_ids: selectedUserIds.value,
    })
    policy.value = data
    selectedUserIds.value = [...(data.authorized_user_ids || [])]
    message.success('重启权限已更新')
  } catch (error) {
    message.error(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadPolicy)
</script>

<template>
  <div class="restart-policy-section">
    <div class="section-head">
      <div>
        <div class="section-title">重启权限</div>
        <div class="section-sub">
          只有被授权的用户才能批准 AI 提出的容器/systemd 重启提案
        </div>
      </div>
      <a-tag :color="hasPermission ? 'success' : 'default'" style="border:none">
        {{ hasPermission ? '你有权限' : '你无权限' }}
      </a-tag>
    </div>

    <a-spin :spinning="loading">
      <div class="summary-row">
        <div class="summary-item">
          <span class="summary-label">可重启服务</span>
          <strong>{{ restartableCount }} 个</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">授权用户</span>
          <strong>{{ selectedUserIds.length }} 人</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">执行模式</span>
          <strong>{{ restartCapability?.execution_mode || '不可用' }}</strong>
        </div>
      </div>

      <div v-if="canManage" class="editor-block">
        <div class="editor-label">授权审批人</div>
        <a-select
          v-model:value="selectedUserIds"
          mode="multiple"
          style="width:100%"
          placeholder="选择可批准重启提案的组织成员"
          :options="userOptions"
        />
        <div class="editor-hint">组织 owner 默认可管理此列表；保存后当前 owner 会自动保留在授权名单中。</div>
        <a-button type="primary" :loading="saving" style="margin-top:12px" @click="savePolicy">
          保存权限
        </a-button>
      </div>
      <div v-else class="readonly-block">
        <div class="editor-label">当前授权用户 ID</div>
        <div class="id-list">
          <a-tag v-for="userId in selectedUserIds" :key="userId">{{ userId }}</a-tag>
          <span v-if="!selectedUserIds.length" class="empty-note">尚未配置授权用户</span>
        </div>
        <div class="editor-hint">只有组织 owner 可以修改重启权限。</div>
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.restart-policy-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed var(--border-color);
}
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}
.section-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}
.section-sub {
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.55;
}
.summary-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.summary-item {
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: color-mix(in srgb, var(--body-bg) 70%, var(--card-bg));
  font-size: 13px;
}
.summary-label {
  display: block;
  font-size: 11px;
  color: var(--text-subtle);
  margin-bottom: 4px;
}
.editor-block,
.readonly-block {
  padding: 12px 14px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--card-bg);
}
.editor-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-subtle);
  margin-bottom: 8px;
}
.editor-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.55;
}
.id-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.empty-note {
  font-size: 12px;
  color: var(--text-subtle);
}
@media (max-width: 720px) {
  .summary-row { grid-template-columns: 1fr; }
}
</style>
