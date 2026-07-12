<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'

const props = defineProps({
  systemId: { type: String, required: true },
  systemName: { type: String, default: '' },
  systemLocal: { type: Boolean, default: false },
  services: { type: Array, default: () => [] },
  restartCapability: { type: Object, default: () => ({}) },
})

const collectors = ref([])
const loading = ref(false)
const creating = ref(false)
const bundleLoading = ref(false)
const testingId = ref(null)
const deletingId = ref(null)
const createdKey = ref(null)
const createdName = ref('')
const createdCollectorId = ref(null)
const showDeploy = ref(false)
const formName = ref('')

// 优先使用显式配置；未配置时退回当前控制台地址，避免生产环境生成空 PLATFORM_URL。
const platformUrl = (
  import.meta.env.VITE_COLLECTOR_PLATFORM_URL
  || import.meta.env.VITE_API_BASE
  || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000')
).replace(/\/$/, '')
const hasCollectors = computed(() => collectors.value.length > 0)
const onlineCollectors = computed(() => collectors.value.filter((collector) => collector.online))
const hasOnlineCollectors = computed(() => onlineCollectors.value.length > 0)
const enabledServices = computed(() => props.services.filter((service) => service.enabled !== false))
const remoteCapabilityCards = computed(() => [
  {
    title: '远程探活',
    ready: hasOnlineCollectors.value && enabledServices.value.length > 0,
    detail: enabledServices.value.length > 0
      ? '可通过采集器检查已登记服务状态'
      : '先登记并启用服务，采集器才能执行远程探活',
  },
  {
    title: '服务日志',
    ready: hasOnlineCollectors.value && enabledServices.value.length > 0,
    detail: enabledServices.value.length > 0
      ? '可在系统概览中读取远程服务日志'
      : '至少需要一个已登记服务作为日志读取目标',
  },
  {
    title: '远程控制',
    ready: hasOnlineCollectors.value && !!props.restartCapability?.enabled,
    detail: props.restartCapability?.enabled
      ? '已具备受控重启或命令执行能力'
      : '需要先给服务配置可控的重启目标',
  },
])
const troubleshootingTips = computed(() => {
  const tips = []
  if (!hasCollectors.value) {
    tips.push('如果还没创建采集器，先生成一次性密钥，再把部署命令发给对方。')
  }
  if (hasCollectors.value && !hasOnlineCollectors.value) {
    tips.push('采集器未上线时，优先检查对方机器是否真的执行了命令，以及 `PLATFORM_URL` 是否能从客户网络访问。')
  }
  if (hasOnlineCollectors.value && enabledServices.value.length === 0) {
    tips.push('采集器已经在线，但还没有可探测服务。请先在“接入服务”里测试并启用至少一个服务。')
  }
  if (hasOnlineCollectors.value && !props.restartCapability?.enabled) {
    tips.push('如果希望远程重启服务，需要先给服务补充容器名或 systemd 单元等可控目标。')
  }
  return tips
})
const onboardingSteps = computed(() => [
  {
    title: '创建采集器',
    done: hasCollectors.value,
    detail: hasCollectors.value ? `已创建 ${collectors.value.length} 个采集器` : '先为这个远程系统生成一个一次性密钥',
  },
  {
    title: '在对方机器部署',
    done: !!createdKey.value || hasCollectors.value,
    detail: createdKey.value
      ? '密钥已生成，复制命令或下载采集包交给对方执行'
      : hasCollectors.value
        ? '采集器已创建，如需重新部署可删除后重建'
        : '创建后可下载采集包，或直接复制 Docker / Python 命令',
  },
  {
    title: '等待采集器上线',
    done: hasOnlineCollectors.value,
    detail: hasOnlineCollectors.value
      ? `已有 ${onlineCollectors.value.length} 个采集器在线`
      : hasCollectors.value
        ? '对方机器启动后，平台会自动显示在线状态'
        : '上线后平台才能读取日志、远程探活并执行受控命令',
  },
])
const onboardingAlert = computed(() => {
  if (hasOnlineCollectors.value) {
    return {
      type: 'success',
      message: '采集器已上线，远程探活、日志读取和受控操作链路可用。',
    }
  }
  if (hasCollectors.value) {
    return {
      type: 'warning',
      message: '采集器已创建但还未上线。请确认对方机器已执行部署命令，且能出站访问平台地址。',
    }
  }
  return {
    type: 'info',
    message: '远程系统建议先登记服务，再创建采集器，让对方机器主动连接平台。',
  }
})

function formatTime(value) {
  if (!value) return '尚未上报'
  const d = new Date(value)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return `${diff} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  return d.toLocaleString()
}

function deployCommand(key) {
  const sysName = props.systemName || '<系统名称>'
  return [
    '# ===== AIOps 采集器部署脚本 =====',
    `# 系统：${sysName}`,
    `# 采集器：${createdName.value || '<采集器名称>'}`,
    '',
    '# 方式一：Docker 运行（推荐）',
    '# 在仓库根目录构建采集器镜像',
    'docker build -f collector/Dockerfile -t aiops-collector:local .',
    `docker run -d --restart=unless-stopped \\`,
    '  --network host \\',
    `  -e PLATFORM_URL="${platformUrl}" \\`,
    `  -e COLLECTOR_KEY="${key}" \\`,
    `  -e COLLECTOR_INTERVAL="30" \\`,
    `  --name ${collectorContainerName()} \\`,
    `  aiops-collector:local`,
    '',
    '# 方式二：直接运行 Python 采集器',
    '# pip install requests websockets',
    `PLATFORM_URL="${platformUrl}" COLLECTOR_KEY="${key}" COLLECTOR_INTERVAL=30 python collector/run.py`,
    '',
    '# 方式三：单次运行测试',
    `PLATFORM_URL="${platformUrl}" COLLECTOR_KEY="${key}" python collector/run.py --once`,
  ].join('\n')
}

function collectorContainerName() {
  return `aiops-collector-${createdCollectorId.value || 'agent'}`
}

function deployCommandDocker(key) {
  return [
    'docker build -f collector/Dockerfile -t aiops-collector:local .',
    '',
    `docker run -d --restart=unless-stopped \\`,
    '  --network host \\',
    `  -e PLATFORM_URL="${platformUrl}" \\`,
    `  -e COLLECTOR_KEY="${key}" \\`,
    `  -e COLLECTOR_INTERVAL="30" \\`,
    `  --name ${collectorContainerName()} \\`,
    `  aiops-collector:local`,
  ].join('\n')
}

function deployCommandPython(key) {
  return `PLATFORM_URL="${platformUrl}" COLLECTOR_KEY="${key}" COLLECTOR_INTERVAL=30 python collector/run.py`
}

async function loadCollectors() {
  loading.value = true
  try {
    const { data } = await api.get(`/systems/${props.systemId}/collectors`)
    collectors.value = data
  } catch (error) {
    message.error(error?.response?.data?.detail || '采集器加载失败')
  } finally {
    loading.value = false
  }
}

async function createCollector() {
  const name = formName.value.trim() || `${props.systemName || '系统'} 采集器`
  creating.value = true
  createdKey.value = null
  showDeploy.value = false
  try {
    const { data } = await api.post(`/systems/${props.systemId}/collectors`, { name })
    createdKey.value = data.collector_key
    createdName.value = data.name
    createdCollectorId.value = data.id
    formName.value = ''
    message.success('采集器已创建，请立即保存密钥！')
    await loadCollectors()
    showDeploy.value = true
  } catch (error) {
    message.error(error?.response?.data?.detail || '采集器创建失败')
  } finally {
    creating.value = false
  }
}

async function downloadBundle() {
  if (!createdKey.value || !createdCollectorId.value) return message.warning('当前页面没有可用的采集器密钥')
  bundleLoading.value = true
  try {
    const { data } = await api.post(
      `/systems/${props.systemId}/collectors/${createdCollectorId.value}/bundle`,
      { collector_key: createdKey.value },
      { responseType: 'blob' },
    )
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = `collector-${createdCollectorId.value}.zip`
    link.click()
    URL.revokeObjectURL(url)
    message.success('采集器安装包已生成')
  } catch (error) {
    message.error(error?.response?.data?.detail || '安装包生成失败')
  } finally {
    bundleLoading.value = false
  }
}

async function deleteCollector(collector) {
  deletingId.value = collector.id
  try {
    await api.delete(`/systems/${props.systemId}/collectors/${collector.id}`)
    message.success('采集器已删除')
    await loadCollectors()
    if (collectors.value.length === 0) {
      showDeploy.value = false
      createdKey.value = null
      createdCollectorId.value = null
    }
  } catch (error) {
    message.error(error?.response?.data?.detail || '删除失败')
  } finally {
    deletingId.value = null
  }
}

async function testCollector(collector) {
  testingId.value = collector.id
  try {
    const { data } = await api.post(`/systems/${props.systemId}/collector/exec`, {
      cmd: 'health_check',
      args: {},
      collector_id: collector.id,
    })
    const results = Array.isArray(data.result) ? data.result : []
    const healthy = results.filter((item) => item.ok).length
    const failed = results.length - healthy
    message[failed ? 'warning' : 'success'](`采集链路正常：${healthy} 个服务正常${failed ? `，${failed} 个异常` : ''}`)
    await loadCollectors()
  } catch (error) {
    message.error(error?.response?.data?.detail || '采集链路测试失败，请确认采集器已连接')
  } finally {
    testingId.value = null
  }
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(
    () => message.success('已复制到剪贴板'),
    () => message.error('复制失败，请手动选中复制'),
  )
}

onMounted(loadCollectors)
</script>

<template>
  <a-card class="panel-card collector-card">
    <template #title>
      <div class="card-title">
        <span>采集器管理</span>
        <a-tag v-if="hasCollectors" color="blue" style="border:none;margin:0">{{ collectors.length }} 个</a-tag>
      </div>
    </template>

    <template #extra>
      <a-button size="small" :loading="loading" @click="loadCollectors">刷新</a-button>
    </template>

    <a-alert
      v-if="systemLocal"
      type="info"
      show-icon
      class="collector-guide"
      message="当前系统由平台本机托管，不需要安装采集器。"
      description="只有监控客户内网、平台无法直接访问的远程系统时，才需要在对方网络中部署采集器。"
    />

    <div v-if="!systemLocal" class="collector-onboarding">
      <a-alert :type="onboardingAlert.type" show-icon :message="onboardingAlert.message" />
      <div class="collector-steps">
        <div v-for="step in onboardingSteps" :key="step.title" class="collector-step">
          <span :class="['collector-step-dot', step.done ? 'done' : 'todo']" />
          <div class="collector-step-body">
            <div class="collector-step-title">{{ step.title }}</div>
            <div class="collector-step-detail">{{ step.detail }}</div>
          </div>
        </div>
      </div>
      <div class="collector-checklist">
        <div class="collector-check-title">上线前检查</div>
        <ul>
          <li>平台对外地址要填写成客户网络可访问的域名或 IP，不要留空。</li>
          <li>服务配置里的 `127.0.0.1` 指的是采集器所在主机，不是平台主机。</li>
          <li>对方环境如果不能用 Docker，可直接运行 Python 版本采集器。</li>
        </ul>
      </div>

      <div class="collector-checklist">
        <div class="collector-check-title">权限边界</div>
        <ul>
          <li>默认只做探活、拉日志、读取指标，不会直接修改对方系统。</li>
          <li>只有你在平台里显式配置了可控目标，并且人工审批通过，平台才会执行重启或受限命令。</li>
          <li>如果对方只愿意开放只读能力，也完全可以只部署采集器，不开启远程控制。</li>
        </ul>
      </div>

      <div v-if="hasCollectors" class="collector-capabilities">
        <div class="collector-check-title">上线后能力状态</div>
        <div class="collector-capability-grid">
          <div v-for="item in remoteCapabilityCards" :key="item.title" class="collector-capability-card">
            <div class="collector-capability-head">
              <span class="collector-capability-title">{{ item.title }}</span>
              <span :class="['collector-capability-badge', item.ready ? 'ready' : 'pending']">
                {{ item.ready ? '可用' : '待完成' }}
              </span>
            </div>
            <div class="collector-capability-detail">{{ item.detail }}</div>
          </div>
        </div>
      </div>

      <div v-if="troubleshootingTips.length" class="collector-checklist">
        <div class="collector-check-title">常见问题排查</div>
        <ul>
          <li v-for="tip in troubleshootingTips" :key="tip">{{ tip }}</li>
        </ul>
      </div>
    </div>

    <!-- 已有采集器列表 -->
    <div v-if="hasCollectors" class="collector-list">
      <div v-for="item in collectors" :key="item.id" class="collector-row">
        <div class="collector-info">
          <div class="collector-name">{{ item.name }}</div>
          <div class="collector-meta">
            <span :class="['status-dot', item.online ? 'online' : 'offline']" />
            {{ item.online ? '在线 · ' : item.last_seen ? '已离线 · ' : '未上线' }}{{ item.last_seen ? formatTime(item.last_seen) : '' }}
          </div>
        </div>
        <div class="collector-actions">
          <a-button
            size="small"
            :loading="testingId === item.id"
            :disabled="!item.online"
            @click.stop="testCollector(item)"
          >测试连接</a-button>
          <a-popconfirm
            title="确定删除此采集器？"
            ok-text="删除"
            cancel-text="取消"
            @confirm="deleteCollector(item)"
          >
            <a-button size="small" danger :loading="deletingId === item.id">删除</a-button>
          </a-popconfirm>
        </div>
      </div>
    </div>

    <a-divider style="margin:12px 0" />

    <!-- 创建采集器 -->
    <div v-if="!systemLocal" class="create-section">
      <div class="create-form">
        <a-input
          v-model:value="formName"
          :placeholder="`${props.systemName || '系统'} 采集器`"
          style="flex:1"
          @press-enter="createCollector"
        />
        <a-button type="primary" :loading="creating" @click="createCollector">
          创建采集器
        </a-button>
      </div>
    </div>

    <!-- 部署信息（创建后显示） -->
    <div v-if="createdKey" class="deploy-section">
      <a-divider style="margin:12px 0" />

      <a-alert type="warning" show-icon class="key-alert">
        <template #message>
          <strong>采集器密钥（仅此一次可见，请立即保存）</strong>
        </template>
        <template #description>
          <div class="key-box">
            <code class="key-value">{{ createdKey }}</code>
            <a-button size="small" type="link" @click="copyText(createdKey)">复制</a-button>
            <a-button size="small" type="primary" :loading="bundleLoading" @click="downloadBundle">下载采集包</a-button>
          </div>
        </template>
      </a-alert>

      <a-alert
        type="info"
        show-icon
        class="network-alert"
        message="服务地址必须从采集器所在网络访问"
        description="如果服务和采集器在同一台 Linux 主机上，推荐使用生成命令中的 host 网络；不要把 127.0.0.1 填成另一台机器的地址。"
      />

      <a-alert
        type="info"
        show-icon
        class="network-alert"
        message="推荐的远程接入方式"
        description="优先让对方在业务网络内部署采集器，并把 Prometheus、应用健康检查、日志读取都交给采集器完成。这样平台不需要直接打入对方内网，双方都更省事也更安全。"
      />

      <div class="deploy-info">
        <h4 class="deploy-title">部署方式</h4>

        <!-- Docker -->
        <div class="cmd-block">
          <div class="cmd-header">
            <span class="cmd-label">方式一：Docker 运行（推荐）</span>
            <a-button size="small" type="link" @click="copyText(deployCommandDocker(createdKey))">复制</a-button>
          </div>
          <pre class="cmd-code">{{ deployCommandDocker(createdKey) }}</pre>
        </div>

        <!-- Python -->
        <div class="cmd-block">
          <div class="cmd-header">
            <span class="cmd-label">方式二：Python 直接运行</span>
            <a-button size="small" type="link" @click="copyText(deployCommandPython(createdKey))">复制</a-button>
          </div>
          <pre class="cmd-code">{{ deployCommandPython(createdKey) }}</pre>
        </div>

        <a-collapse ghost>
          <a-collapse-panel key="full" header="查看完整部署脚本">
            <div class="cmd-header">
              <a-button size="small" type="link" @click="copyText(deployCommand(createdKey))">复制全部</a-button>
            </div>
            <pre class="cmd-code cmd-full">{{ deployCommand(createdKey) }}</pre>
          </a-collapse-panel>
        </a-collapse>
      </div>
    </div>

    <!-- 无采集器时的占位提示 -->
    <div v-if="!systemLocal && !hasCollectors && !createdKey" class="empty-hint">
      <p>采集器安装在您内网的一台机器上，出站连接平台，探活内网服务。</p>
      <p>输入名称并点击「创建采集器」后，将获得一次性密钥和部署指令。</p>
    </div>
  </a-card>
</template>

<style scoped>
.panel-card {
  margin-top: 16px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.collector-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.collector-onboarding {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 12px;
}
.collector-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.collector-step {
  display: flex;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--body-bg);
}
.collector-step-dot {
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 999px;
  flex-shrink: 0;
}
.collector-step-dot.done { background: #22c55e; }
.collector-step-dot.todo { background: #f59e0b; }
.collector-step-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text, #1A1206);
  margin-bottom: 4px;
}
.collector-step-detail {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-subtle, #888);
}
.collector-checklist {
  padding: 11px 12px;
  border: 1px dashed var(--border-color);
  border-radius: 10px;
  background: color-mix(in srgb, var(--body-bg) 86%, white);
}
.collector-check-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text, #1A1206);
  margin-bottom: 6px;
}
.collector-checklist ul {
  margin: 0;
  padding-left: 18px;
  color: var(--text-subtle, #888);
  font-size: 12px;
  line-height: 1.7;
}
.collector-capabilities {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.collector-capability-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.collector-capability-card {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--card-bg);
  padding: 12px;
}
.collector-capability-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}
.collector-capability-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text, #1A1206);
}
.collector-capability-badge {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 4px 7px;
  border-radius: 999px;
}
.collector-capability-badge.ready {
  color: #15803d;
  background: rgba(34, 197, 94, 0.12);
}
.collector-capability-badge.pending {
  color: #b45309;
  background: rgba(245, 158, 11, 0.14);
}
.collector-capability-detail {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-subtle, #888);
}
.collector-guide { margin-bottom: 12px; }
.collector-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-color, #eee);
}
.collector-row:last-child {
  border-bottom: none;
}
.collector-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.collector-actions { display:flex; align-items:center; gap:6px; }
.collector-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #1A1206);
}
.collector-meta {
  font-size: 11.5px;
  color: var(--text-subtle, #888);
  display: flex;
  align-items: center;
  gap: 5px;
}
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.online {
  background: #52c41a;
}
.status-dot.offline {
  background: #d9d9d9;
}

.create-section {
  margin: 0;
}
.create-form {
  display: flex;
  gap: 8px;
}

.deploy-section {
  margin-top: 4px;
}
.key-alert {
  margin-bottom: 16px;
}
.network-alert { margin-bottom: 16px; }
.key-box {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.key-value {
  font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  word-break: break-all;
  background: var(--hover-bg, #f5f5f5);
  padding: 4px 8px;
  border-radius: 4px;
  flex: 1;
}
.deploy-info {
  margin-top: 4px;
}
.deploy-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
  color: var(--text, #1A1206);
}
.cmd-block {
  margin-bottom: 10px;
}
.cmd-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.cmd-label {
  font-size: 12px;
  color: var(--text-subtle, #888);
}
.cmd-code {
  font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
  font-size: 11.5px;
  background: #1e1e2e;
  color: #cdd6f4;
  padding: 10px 14px;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
  line-height: 1.6;
  margin: 0;
}
.cmd-full {
  max-height: 300px;
  overflow-y: auto;
}
.empty-hint {
  padding: 8px 0;
  font-size: 13px;
  color: var(--text-subtle, #888);
  line-height: 1.7;
}
.empty-hint p {
  margin: 0 0 4px;
}
@media (max-width: 900px) {
  .collector-steps {
    grid-template-columns: 1fr;
  }
  .collector-capability-grid {
    grid-template-columns: 1fr;
  }
}
</style>
