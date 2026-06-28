<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import api from '../api'

const router = useRouter()
const systems = ref([])
const loading = ref(false)

const modalOpen = ref(false)
const submitting = ref(false)
const form = reactive({ key: '', name: '', local: false, services: [] })

// 不同连接器要填的字段
const CONNECTOR_FIELDS = {
  http: [['health_url', 'HTTP 健康检查 URL', 'https://example.com']],
  tcp: [['host', '主机', 'example.com'], ['port', '端口', '443']],
  prometheus: [['url', 'Prometheus 地址', 'http://prom:9090'], ['up_query', '探活 PromQL', 'up']],
  ssh: [['host', '主机', '1.2.3.4'], ['user', '用户', 'ops'], ['log_path', '日志路径', '/var/log/app.log'], ['systemd_unit', 'systemd 服务名', 'my-api']],
  local: [['kind', 'kind(process/docker)', 'process'], ['log_file', '日志文件', 'logs/app.log']],
}
const CONNECTORS = Object.keys(CONNECTOR_FIELDS)

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: 'key', dataIndex: 'key', key: 'key' },
  { title: '服务数', dataIndex: 'serviceCount', key: 'serviceCount' },
  { title: '接入模式', dataIndex: 'mode', key: 'mode' },
  { title: '操作', key: 'action' },
]

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/systems')
    systems.value = data
  } catch (e) {
    message.error('加载系统列表失败')
  } finally {
    loading.value = false
  }
}
onMounted(load)

function openCreate() {
  form.key = ''
  form.name = ''
  form.local = false
  form.services = [newService()]
  modalOpen.value = true
}

function newService() {
  return { name: '', connector: 'http', fields: {} }
}
function addService() {
  form.services.push(newService())
}
function removeService(i) {
  form.services.splice(i, 1)
}

function buildConfig(svc) {
  const cfg = {}
  for (const [key] of CONNECTOR_FIELDS[svc.connector] || []) {
    let v = svc.fields[key]
    if (v === undefined || v === '') continue
    if (key === 'port') v = Number(v)
    cfg[key] = v
  }
  return cfg
}

async function submit() {
  if (!form.key || !form.name) return message.warning('请填写 key 和名称')
  const services = form.services
    .filter((s) => s.name)
    .map((s) => ({ name: s.name, connector: s.connector, config: buildConfig(s) }))
  submitting.value = true
  try {
    await api.post('/systems', {
      key: form.key,
      name: form.name,
      local: form.local,
      services,
    })
    message.success('系统已注册')
    modalOpen.value = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '注册失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px">
      <h2 style="margin: 0">我的被监控系统</h2>
      <a-button type="primary" @click="openCreate">+ 注册系统</a-button>
    </div>

    <a-table :columns="columns" :data-source="systems" :loading="loading" row-key="id">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'serviceCount'">{{ record.services.length }}</template>
        <template v-else-if="column.key === 'mode'">
          <a-tag :color="record.local ? 'green' : 'blue'">
            {{ record.local ? '平台托管' : '远程只读' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'action'">
          <a @click="router.push(`/systems/${record.id}`)">查看 / 诊断</a>
        </template>
      </template>
    </a-table>

    <a-empty v-if="!loading && systems.length === 0" description="还没有注册任何系统" />

    <a-modal v-model:open="modalOpen" title="注册一套被监控系统" width="720px"
             :confirm-loading="submitting" ok-text="注册" @ok="submit">
      <a-form layout="vertical">
        <a-row :gutter="16">
          <a-col :span="8">
            <a-form-item label="key（组织内唯一）">
              <a-input v-model:value="form.key" placeholder="web-prod" />
            </a-form-item>
          </a-col>
          <a-col :span="10">
            <a-form-item label="系统名称">
              <a-input v-model:value="form.name" placeholder="生产站点" />
            </a-form-item>
          </a-col>
          <a-col :span="6">
            <a-form-item label="平台托管（可写动作）">
              <a-switch v-model:checked="form.local" />
            </a-form-item>
          </a-col>
        </a-row>

        <a-divider orientation="left">服务</a-divider>
        <div v-for="(svc, i) in form.services" :key="i"
             style="border: 1px solid #f0f0f0; border-radius: 8px; padding: 12px; margin-bottom: 12px">
          <a-row :gutter="12">
            <a-col :span="9">
              <a-form-item label="服务名">
                <a-input v-model:value="svc.name" placeholder="website" />
              </a-form-item>
            </a-col>
            <a-col :span="9">
              <a-form-item label="连接器">
                <a-select v-model:value="svc.connector" :options="CONNECTORS.map((c) => ({ value: c, label: c }))" />
              </a-form-item>
            </a-col>
            <a-col :span="6" style="display: flex; align-items: flex-end; padding-bottom: 24px">
              <a-button danger type="link" @click="removeService(i)">删除</a-button>
            </a-col>
          </a-row>
          <a-row :gutter="12">
            <a-col :span="12" v-for="[key, label, ph] in CONNECTOR_FIELDS[svc.connector]" :key="key">
              <a-form-item :label="label">
                <a-input v-model:value="svc.fields[key]" :placeholder="ph" />
              </a-form-item>
            </a-col>
          </a-row>
        </div>
        <a-button dashed block @click="addService">+ 添加服务</a-button>
      </a-form>
    </a-modal>
  </div>
</template>
