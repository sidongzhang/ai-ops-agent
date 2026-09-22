<script setup>
import { computed, reactive, watch } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'
import {
  buildServicePayload,
  CONNECTOR_FIELDS,
  CONNECTORS,
  createSystemServiceDraft,
  getTargetAddressMeta,
  resolvePresetInput,
  SERVICE_COLORS,
  SERVICE_PRESETS,
  SYSTEM_TEMPLATES,
  syncPresetDraft,
} from '../../services'

const props = defineProps({
  open: { type: Boolean, required: true },
})

const emit = defineEmits(['update:open', 'created'])

const form = reactive({ key: '', name: '', local: false, services: [] })
const state = reactive({ submitting: false })
const selectedTemplate = reactive({ key: '' })
const accessModeOptions = [
  {
    value: 'local',
    title: '平台本机托管',
    desc: '服务就跑在当前平台机器上，探活地址可填 127.0.0.1。',
  },
  {
    value: 'remote',
    title: '远程系统接入',
    desc: '监控别人服务器/电脑上的服务。采集器部署在目标网络，平台通过采集器探活。',
  },
]
const addressMeta = computed(() => getTargetAddressMeta(form.local))
const filledServicesCount = computed(() => form.services.filter((service) => service.name).length)
const nextStepText = computed(() => (
  form.local
    ? '注册完成后可直接在详情页测试服务并启用监控。'
    : '注册完成后请到详情页创建采集器，让对方机器执行部署命令。'
))
const selectedTemplateInfo = computed(() => SYSTEM_TEMPLATES.find((item) => item.key === selectedTemplate.key) || null)

watch(
  () => props.open,
  (open) => {
    if (open) {
      Object.assign(form, { key: '', name: '', local: false, services: [createSystemServiceDraft()] })
      selectedTemplate.key = ''
    }
  },
  { immediate: true },
)

function close() {
  emit('update:open', false)
}

function addService() {
  form.services.push(createSystemServiceDraft())
}

function removeService(index) {
  form.services.splice(index, 1)
}

function onPresetChange(service) {
  syncPresetDraft(service)
}

function applyTemplate(templateKey) {
  selectedTemplate.key = templateKey
  const template = SYSTEM_TEMPLATES.find((item) => item.key === templateKey)
  if (!template) return
  form.services = template.presets.map((item) => {
    const svc = {
      ...createSystemServiceDraft(item.preset),
      name: item.name,
      preset: item.preset,
      connector: item.connector,
      fields: { ...(item.fields || {}) },
      customFields: { ...(item.customFields || {}) },
    }
    if (form.local && !svc.fields.host) {
      svc.fields.host = '127.0.0.1'
    }
    return svc
  })
}

function setAccessMode(mode) {
  form.local = mode === 'local'
}

async function submit() {
  if (!form.key || !form.name) return message.warning('请填写系统名称和 Key')
  const services = form.services.filter((service) => service.name).map(buildServicePayload)
  state.submitting = true
  try {
    const { data } = await api.post('/systems', { key: form.key, name: form.name, local: form.local, services })
    message.success({
      content: form.local
        ? '系统已注册，正在进入详情页继续接入'
        : '系统已注册，请在详情页创建采集器完成远程接入',
      duration: 4,
    })
    close()
    emit('created', data)
  } catch (error) {
    message.error(error?.response?.data?.detail || '注册失败')
  } finally {
    state.submitting = false
  }
}
</script>

<template>
  <a-modal
    :open="open"
    title="注册被监控系统"
    width="620px"
    :confirm-loading="state.submitting"
    ok-text="注册"
    cancel-text="取消"
    @update:open="emit('update:open', $event)"
    @ok="submit"
    @cancel="close"
  >
    <div class="modal-body">
      <div class="section">
        <div class="mode-grid">
          <button
            v-for="option in accessModeOptions"
            :key="option.value"
            class="mode-card"
            :class="{ active: form.local === (option.value === 'local') }"
            @click="setAccessMode(option.value)"
          >
            <div class="mode-title">{{ option.title }}</div>
            <div class="mode-desc">{{ option.desc }}</div>
          </button>
        </div>

        <a-row :gutter="12">
          <a-col :span="11">
            <a-form-item label="系统名称" required style="margin-bottom:0">
              <a-input v-model:value="form.name" placeholder="电商生产站点" size="middle" />
            </a-form-item>
          </a-col>
          <a-col :span="11">
            <a-form-item label="唯一 Key" required style="margin-bottom:0">
              <a-input v-model:value="form.key" placeholder="ecommerce-prod" size="middle" />
            </a-form-item>
          </a-col>
          <a-col :span="2" style="display:flex;flex-direction:column;align-items:center;padding-top:22px">
            <div style="font-size:10px;color:var(--text-subtle);margin-bottom:6px;white-space:nowrap">本机托管</div>
            <a-switch v-model:checked="form.local" size="small" />
          </a-col>
        </a-row>
      </div>

      <a-alert
        v-if="!form.local"
        type="info"
        show-icon
        message="远程系统：采集器部署在目标网络，不是平台服务器"
        :description="`${addressMeta.hint}。先在这里登记服务的真实 IP 和端口，再到详情页创建采集器并下载到目标机器运行。`"
        style="margin-bottom:16px"
      />

      <div class="section-divider">
        <span>系统模板</span>
      </div>

      <div class="template-grid">
        <button
          v-for="template in SYSTEM_TEMPLATES"
          :key="template.key"
          class="template-card"
          :class="{ active: selectedTemplate.key === template.key }"
          @click="applyTemplate(template.key)"
        >
          <div class="template-title">{{ template.label }}</div>
          <div class="template-desc">{{ template.description }}</div>
          <div class="template-meta">{{ template.presets.length }} 个服务草稿</div>
        </button>
      </div>

      <div v-if="selectedTemplateInfo" class="template-summary">
        <span class="template-summary-label">当前模板</span>
        <strong>{{ selectedTemplateInfo.label }}</strong>
        <span class="template-summary-detail">{{ selectedTemplateInfo.description }}</span>
      </div>

      <div class="section-divider">
        <span>服务配置</span>
      </div>

      <div class="services-list">
        <div v-for="(svc, index) in form.services" :key="index" class="svc-card">
          <div class="svc-head">
            <div class="svc-color-dot" :style="{ background: SERVICE_COLORS[svc.preset] || '#6B7280' }" />
            <a-input
              v-model:value="svc.name"
              placeholder="服务名称，如 Nginx"
              class="svc-name-input"
              bordered
            />
            <button class="del-btn" @click="removeService(index)" title="删除服务">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          <div class="type-grid">
            <button
              v-for="(preset, key) in SERVICE_PRESETS"
              :key="key"
              class="type-chip"
              :class="{ active: svc.preset === key }"
              :style="svc.preset === key ? {
                borderColor: SERVICE_COLORS[key] || 'var(--primary)',
                background: (SERVICE_COLORS[key] || 'var(--primary)') + '18',
                color: SERVICE_COLORS[key] || 'var(--primary)',
              } : {}"
              @click="svc.preset = key; onPresetChange(svc)"
            >
              <span class="chip-icon">{{ preset.icon }}</span>
              <span class="chip-label">{{ preset.label }}</span>
            </button>
          </div>

          <div class="svc-fields" v-if="SERVICE_PRESETS[svc.preset].buildConfig">
            <a-row :gutter="10">
              <a-col v-for="inp in SERVICE_PRESETS[svc.preset].inputs" :key="inp.key" :span="inp.span">
                <a-form-item :label="resolvePresetInput(inp, form.local).label" style="margin-bottom:0">
                  <a-input
                    v-model:value="svc.fields[inp.key]"
                    :placeholder="resolvePresetInput(inp, form.local).placeholder"
                    size="small"
                  />
                </a-form-item>
              </a-col>
            </a-row>
          </div>

          <div class="svc-fields" v-else>
            <a-row :gutter="10">
              <a-col :span="8">
                <a-form-item label="连接器" style="margin-bottom:8px">
                  <a-select v-model:value="svc.connector" size="small" :options="CONNECTORS.map((c) => ({ value: c, label: c }))" />
                </a-form-item>
              </a-col>
            </a-row>
            <a-row :gutter="10">
              <a-col v-for="[key, label, ph] in CONNECTOR_FIELDS[svc.connector] || []" :key="key" :span="12">
                <a-form-item :label="label" style="margin-bottom:0">
                  <a-input v-model:value="svc.customFields[key]" :placeholder="ph" size="small" />
                </a-form-item>
              </a-col>
            </a-row>
          </div>
        </div>
      </div>

      <button class="add-svc-btn" @click="addService">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="13" height="13">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        添加服务
      </button>

      <div class="summary-card">
        <div class="summary-item">
          <span class="summary-label">接入方式</span>
          <strong>{{ form.local ? '平台本机托管' : '远程采集接入' }}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">已填写服务</span>
          <strong>{{ filledServicesCount }} 个</strong>
        </div>
        <div class="summary-next">{{ nextStepText }}</div>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.modal-body { padding: 4px 0 0; }
.section { padding: 0 0 16px; }
.mode-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.mode-card {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--card-bg);
  padding: 12px 14px;
  text-align: left;
  cursor: pointer;
  transition: border-color .16s, box-shadow .16s, transform .16s;
}
.mode-card:hover {
  border-color: color-mix(in srgb, var(--primary) 50%, var(--border-color));
  transform: translateY(-1px);
}
.mode-card.active {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 12%, transparent);
}
.mode-title { font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
.mode-desc { font-size: 12px; line-height: 1.55; color: var(--text-subtle); }
.section-divider {
  display: flex; align-items: center; gap: 10px;
  margin: 4px 0 16px;
  font-size: 12px; font-weight: 600;
  color: var(--text-subtle);
  letter-spacing: .06em; text-transform: uppercase;
}
.section-divider::before,
.section-divider::after {
  content: ''; flex: 1;
  height: 1px; background: var(--border-color);
}
.template-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.template-card {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--card-bg);
  padding: 12px;
  text-align: left;
  cursor: pointer;
  transition: border-color .16s, box-shadow .16s, transform .16s;
}
.template-card:hover {
  border-color: color-mix(in srgb, var(--primary) 44%, var(--border-color));
  transform: translateY(-1px);
}
.template-card.active {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 10%, transparent);
}
.template-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}
.template-desc {
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-subtle);
  min-height: 56px;
}
.template-meta {
  margin-top: 8px;
  font-size: 11px;
  color: var(--primary);
  font-weight: 600;
}
.template-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--primary) 24%, var(--border-color));
  background: color-mix(in srgb, var(--primary) 5%, var(--card-bg));
}
.template-summary-label {
  font-size: 11px;
  color: var(--text-subtle);
}
.template-summary-detail {
  font-size: 12px;
  color: var(--text-subtle);
}
.services-list { display: flex; flex-direction: column; gap: 10px; }
.svc-card {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  overflow: hidden;
  background: var(--card-bg);
}
.svc-head {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px;
  background: var(--body-bg);
  border-bottom: 1px solid var(--border-color);
}
.svc-color-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.svc-name-input {
  flex: 1; border: none !important; background: transparent !important;
  box-shadow: none !important; font-size: 13px; font-weight: 600;
  padding: 0 !important; color: var(--text);
}
.del-btn {
  background: none; border: none;
  color: var(--text-subtle); opacity: .4;
  cursor: pointer; padding: 4px; border-radius: 4px;
  display: flex; align-items: center;
  transition: opacity .15s, color .15s;
  flex-shrink: 0;
}
.del-btn:hover { opacity: 1; color: #ef4444; }
.type-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-color);
}
.type-chip {
  display: flex; flex-direction: column; align-items: center;
  gap: 3px; padding: 8px 4px;
  border: 1.5px solid var(--border-color);
  border-radius: 8px;
  cursor: pointer;
  background: var(--card-bg);
  color: var(--text-subtle);
  transition: border-color .13s, background .13s, color .13s;
}
.type-chip:hover:not(.active) {
  border-color: var(--primary);
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 5%, var(--card-bg));
}
.chip-icon  { font-size: 18px; line-height: 1; }
.chip-label { font-size: 11px; font-weight: 600; line-height: 1.2; }
.svc-fields { padding: 10px 12px 12px; }
.add-svc-btn {
  width: 100%; margin-top: 8px;
  display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 9px;
  border: 1.5px dashed var(--border-color);
  border-radius: 8px;
  background: none;
  color: var(--text-subtle);
  font-size: 13px; font-weight: 500;
  cursor: pointer;
  transition: border-color .15s, color .15s, background .15s;
}
.add-svc-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 4%, var(--card-bg));
}
.summary-card {
  margin-top: 12px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: color-mix(in srgb, var(--body-bg) 82%, white);
  padding: 12px 14px;
}
.summary-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.summary-item + .summary-item { margin-top: 8px; }
.summary-label { color: var(--text-subtle); }
.summary-next {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border-color);
  font-size: 12px;
  color: var(--text-subtle);
  line-height: 1.6;
}
@media (max-width: 720px) {
  .mode-grid { grid-template-columns: 1fr; }
  .template-grid { grid-template-columns: 1fr; }
}
</style>
