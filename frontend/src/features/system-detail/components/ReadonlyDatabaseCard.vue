<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
})

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const editingUrl = ref(false)
const testResult = ref(null)

const form = reactive({
  enabled: false,
  name: '',
  database_url: '',
  table: '',
  timestamp_column: '',
  sensitive_fields: [],
  max_rows: 20,
  timeout_seconds: 5,
  task_analysis_enabled: false,
  task_id_column: '',
  status_column: '',
  updated_at_column: '',
  worker_column: '',
  processing_values: ['processing', '处理中'],
  stuck_threshold_minutes: 30,
  worker_service_names: [],
})

const urlAlreadySet = computed(() => form.database_url === '***')

function syncForm(data) {
  Object.assign(form, {
    enabled: !!data?.enabled,
    name: data?.name || '',
    database_url: data?.database_url || '',
    table: data?.table || '',
    timestamp_column: data?.timestamp_column || '',
    sensitive_fields: data?.sensitive_fields || [],
    max_rows: data?.max_rows || 20,
    timeout_seconds: data?.timeout_seconds || 5,
    task_analysis_enabled: !!data?.task_analysis_enabled,
    task_id_column: data?.task_id_column || '',
    status_column: data?.status_column || '',
    updated_at_column: data?.updated_at_column || '',
    worker_column: data?.worker_column || '',
    processing_values: data?.processing_values || ['processing', '处理中'],
    stuck_threshold_minutes: data?.stuck_threshold_minutes || 30,
    worker_service_names: data?.worker_service_names || [],
  })
  editingUrl.value = !form.database_url || form.database_url !== '***'
}

async function loadConfig() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/readonly-database`)
    syncForm(data)
  } catch (error) {
    message.error(error?.response?.data?.detail || '只读数据源配置加载失败')
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  saving.value = true
  testResult.value = null
  try {
    const { data } = await api.put(`/systems/${props.systemId}/readonly-database`, { ...form })
    syncForm(data)
    message.success('只读数据源配置已保存')
  } catch (error) {
    message.error(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function testConfig() {
  testing.value = true
  testResult.value = null
  try {
    const { data } = await api.post(`/systems/${props.systemId}/readonly-database/test`)
    testResult.value = data
    message.success(`测试通过，${data.table} 当前 ${data.total} 条记录`)
  } catch (error) {
    message.error(error?.response?.data?.detail || '测试失败')
  } finally {
    testing.value = false
  }
}

onMounted(loadConfig)
</script>

<template>
  <a-card class="panel-card readonly-card">
    <template #title>
      <div class="title-row">
        <span>只读数据分析</span>
        <a-tag v-if="form.enabled" color="green" style="border:none;font-size:11px">已启用</a-tag>
        <a-tag v-else style="border:none;font-size:11px;color:var(--text-subtle)">未启用</a-tag>
      </div>
    </template>

    <a-spin v-if="loading" style="display:block;margin:32px 0;text-align:center" />

    <div v-else class="readonly-body">
      <div class="readonly-tip">
        只用于回答“数据是否到达、数量是否异常、最近记录是什么”等问题。平台只执行 SELECT 查询，并限制返回行数。
      </div>

      <div class="switch-row">
        <span class="field-label">启用分析</span>
        <a-switch v-model:checked="form.enabled" />
      </div>

      <template v-if="form.enabled">
        <div class="field">
          <span class="field-label">数据源名称</span>
          <a-input v-model:value="form.name" placeholder="例如：任务业务库" />
        </div>

        <div class="field">
          <span class="field-label">只读连接串</span>
          <div v-if="urlAlreadySet && !editingUrl" class="secret-row">
            <a-input-password value="••••••••••••••••" disabled class="secret-filled" />
            <a-button size="small" @click="editingUrl = true; form.database_url = ''">修改</a-button>
          </div>
          <a-input-password
            v-else
            v-model:value="form.database_url"
            :placeholder="urlAlreadySet ? '输入新连接串以替换' : 'sqlite:////path/db 或 postgresql://readonly:***@host/db'"
          />
        </div>

        <div class="field-grid">
          <div class="field">
            <span class="field-label">分析表名</span>
            <a-input v-model:value="form.table" placeholder="tasks" />
          </div>
          <div class="field">
            <span class="field-label">时间字段</span>
            <a-input v-model:value="form.timestamp_column" placeholder="created_at，可选" />
          </div>
        </div>

        <div class="subsection-title">
          <div>
            <strong>任务卡住专项分析</strong>
            <span>用于回答“任务为什么一直处理中”</span>
          </div>
          <a-switch v-model:checked="form.task_analysis_enabled" size="small" />
        </div>

        <template v-if="form.task_analysis_enabled">
          <div class="field-grid">
            <div class="field">
              <span class="field-label">任务 ID 字段</span>
              <a-input v-model:value="form.task_id_column" placeholder="id，可选" />
            </div>
            <div class="field">
              <span class="field-label">状态字段</span>
              <a-input v-model:value="form.status_column" placeholder="status" />
            </div>
          </div>
          <div class="field-grid">
            <div class="field">
              <span class="field-label">最后更新时间字段</span>
              <a-input v-model:value="form.updated_at_column" placeholder="updated_at" />
            </div>
            <div class="field">
              <span class="field-label">Worker 字段</span>
              <a-input v-model:value="form.worker_column" placeholder="worker_name，可选" />
            </div>
          </div>
          <div class="field-grid">
            <div class="field">
              <span class="field-label">处理中状态值</span>
              <a-select
                v-model:value="form.processing_values"
                mode="tags"
                placeholder="processing、处理中"
                style="width:100%"
              />
            </div>
            <div class="field">
              <span class="field-label">多久未更新算卡住（分钟）</span>
              <a-input-number
                v-model:value="form.stuck_threshold_minutes"
                :min="1"
                :max="10080"
                style="width:100%"
              />
            </div>
          </div>
          <div class="field">
            <span class="field-label">关联 Worker 服务</span>
            <a-select
              v-model:value="form.worker_service_names"
              mode="tags"
              placeholder="填写已注册服务名称，例如 Task-Worker；留空时自动识别"
              style="width:100%"
            />
          </div>
        </template>

        <div class="field-grid">
          <div class="field">
            <span class="field-label">敏感字段</span>
            <a-select
              v-model:value="form.sensitive_fields"
              mode="tags"
              placeholder="例如：phone、email、id_card"
              style="width:100%"
            />
          </div>
          <div class="field">
            <span class="field-label">最大返回行数</span>
            <a-input-number v-model:value="form.max_rows" :min="1" :max="100" style="width:100%" />
          </div>
        </div>
      </template>

      <div class="actions">
        <a-button type="primary" :loading="saving" @click="saveConfig">保存配置</a-button>
        <a-button v-if="form.enabled" :loading="testing" @click="testConfig">测试查询</a-button>
      </div>

      <a-alert
        v-if="testResult"
        type="success"
        show-icon
        :message="`测试通过：${testResult.table} 当前 ${testResult.total} 条记录${testResult.task_analysis_ready ? '，任务专项分析已就绪' : ''}`"
      />
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
}
.readonly-card {
  margin-top: 16px;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.readonly-body {
  display: flex;
  flex-direction: column;
  gap: 13px;
}
.readonly-tip {
  color: var(--text-subtle);
  background: var(--body-bg);
  border: 1px solid var(--border-color);
  border-radius: 7px;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.6;
}
.switch-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.field-label {
  color: var(--text-subtle);
  font-size: 12px;
  font-weight: 600;
}
.field-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
}
.subsection-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 2px;
  padding: 11px 0 8px;
  border-top: 1px solid var(--border-color);
}
.subsection-title > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.subsection-title strong {
  color: var(--text);
  font-size: 13px;
}
.subsection-title span {
  color: var(--text-subtle);
  font-size: 11px;
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
.actions {
  display: flex;
  gap: 10px;
  padding-top: 2px;
}

@media (max-width: 720px) {
  .field-grid {
    grid-template-columns: 1fr;
  }
  .actions {
    flex-direction: column;
  }
}
</style>
