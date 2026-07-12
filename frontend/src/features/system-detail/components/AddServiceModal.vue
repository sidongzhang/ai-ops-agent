<script setup>
import { reactive, watch } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'
import {
  buildServicePayload,
  CONNECTOR_FIELDS,
  createDetailServiceDraft,
  SERVICE_COLORS,
  SERVICE_PRESETS,
  syncPresetDraft,
} from '../../services'

const props = defineProps({
  open: { type: Boolean, required: true },
  systemId: { type: String, required: true },
})

const emit = defineEmits(['update:open', 'created'])

const state = reactive({
  loading: false,
  testing: false,
  probePassed: false,
  probeResult: null,
  probeSignature: '',
  draftId: null,
})
const newSvc = reactive(createDetailServiceDraft())

function payloadSignature() {
  return JSON.stringify(buildServicePayload(newSvc))
}

function resetProbe() {
  state.probePassed = false
  state.probeResult = null
}

function close() {
  emit('update:open', false)
}

async function cancel() {
  const draftId = state.draftId
  state.draftId = null
  close()
  if (draftId) {
    try {
      await api.delete(`/systems/${props.systemId}/services/${draftId}`)
      emit('created')
    } catch {
      // The persisted draft remains visible in the service list and can be deleted there.
    }
  }
  Object.assign(newSvc, createDetailServiceDraft())
  resetProbe()
}

function onNewPresetChange() {
  syncPresetDraft(newSvc)
}

async function testService() {
  if (!newSvc.name.trim()) return message.warning('请输入服务名称')
  state.testing = true
  state.probeResult = null
  try {
    const payload = buildServicePayload(newSvc)
    if (state.draftId) {
      await api.put(`/systems/${props.systemId}/services/${state.draftId}`, payload)
    } else {
      const { data: draft } = await api.post(`/systems/${props.systemId}/services`, payload)
      state.draftId = draft.id
      emit('created')
    }
    const { data } = await api.post(
      `/systems/${props.systemId}/services/${state.draftId}/test`,
    )
    state.probeResult = data
    state.probePassed = !!data.ok
    state.probeSignature = payloadSignature()
    if (data.ok) message.success(`测试通过：${data.detail}`)
    else message.error(`测试未通过：${data.detail}`)
  } catch (error) {
    state.probePassed = false
    state.probeResult = { ok: false, detail: error?.response?.data?.detail || '测试失败' }
    message.error(state.probeResult.detail)
  } finally {
    state.testing = false
  }
}

async function submitAddSvc() {
  if (!newSvc.name.trim()) return message.warning('请输入服务名称')
  if (!state.probePassed || state.probeSignature !== payloadSignature()) {
    return message.warning('请先测试通过后再添加服务')
  }
  state.loading = true
  try {
    await api.post(`/systems/${props.systemId}/services/${state.draftId}/enable`)
    message.success(`服务「${newSvc.name}」已测试并启用`)
    state.draftId = null
    emit('created')
    close()
    Object.assign(newSvc, createDetailServiceDraft())
    resetProbe()
  } catch (error) {
    message.error(error?.response?.data?.detail || '添加失败')
  } finally {
    state.loading = false
  }
}

watch(
  () => payloadSignature(),
  (signature) => {
    if (signature !== state.probeSignature) resetProbe()
  },
)
</script>

<template>
  <a-modal
    :open="open"
    title="添加被监控服务"
    width="520px"
    :confirm-loading="state.loading"
    @update:open="emit('update:open', $event)"
    @cancel="cancel"
  >
    <div class="add-svc-body">
      <div class="add-field">
        <label class="add-label">服务名称 <span class="req-star">*</span></label>
        <a-input v-model:value="newSvc.name" placeholder="如 Nginx、主库 MySQL" size="large" />
      </div>

      <div class="add-field">
        <label class="add-label">服务类型</label>
        <div class="add-type-grid">
          <button
            v-for="(preset, key) in SERVICE_PRESETS"
            :key="key"
            class="add-type-chip"
            :class="{ active: newSvc.preset === key }"
            :style="newSvc.preset === key ? {
              borderColor: SERVICE_COLORS[key] || 'var(--primary)',
              background: (SERVICE_COLORS[key] || 'var(--primary)') + '18',
              color: SERVICE_COLORS[key] || 'var(--primary)',
            } : {}"
            @click="newSvc.preset = key; onNewPresetChange()"
          >
            <span class="chip-icon">{{ preset.icon }}</span>
            <span class="chip-label">{{ preset.label }}</span>
          </button>
        </div>
      </div>

      <div v-if="SERVICE_PRESETS[newSvc.preset].buildConfig && SERVICE_PRESETS[newSvc.preset].inputs.length" class="add-field">
        <label class="add-label">连接参数</label>
        <div class="params-row">
          <div
            v-for="inp in SERVICE_PRESETS[newSvc.preset].inputs"
            :key="inp.key"
            class="param-field"
            :style="{ flex: inp.span }"
          >
            <span class="param-label">{{ inp.label }}</span>
            <a-input v-model:value="newSvc.fields[inp.key]" :placeholder="inp.placeholder" />
          </div>
        </div>
      </div>

      <div v-else class="add-field">
        <label class="add-label">连接器与访问参数</label>
        <div class="params-row">
          <div class="param-field connector-field">
            <span class="param-label">连接器</span>
            <a-select v-model:value="newSvc.connector" :options="Object.keys(CONNECTOR_FIELDS).map((key) => ({ value: key, label: key }))" />
          </div>
          <div
            v-for="[key, label, placeholder] in CONNECTOR_FIELDS[newSvc.connector] || []"
            :key="key"
            class="param-field"
            style="flex:1"
          >
            <span class="param-label">{{ label }}</span>
            <a-input v-model:value="newSvc.customFields[key]" :placeholder="placeholder" />
          </div>
        </div>
      </div>

      <a-alert
        v-if="state.probeResult"
        :type="state.probeResult.ok ? 'success' : 'error'"
        show-icon
        :message="state.probeResult.ok ? '配置测试通过，可以添加服务' : '配置测试未通过'"
        :description="state.probeResult.detail"
      />
    </div>

    <template #footer>
      <a-button @click="cancel">取消</a-button>
      <a-button :loading="state.testing" :disabled="state.loading" @click="testService">测试配置</a-button>
      <a-button
        type="primary"
        :loading="state.loading"
        :disabled="state.testing || !state.probePassed || state.probeSignature !== payloadSignature()"
        @click="submitAddSvc"
      >
        启用服务
      </a-button>
    </template>
  </a-modal>
</template>

<style scoped>
.add-svc-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 8px 0 4px;
}
.add-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.add-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-subtle);
  text-transform: uppercase;
  letter-spacing: .06em;
}
.req-star { color: var(--primary); }
.add-type-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 7px;
}
.add-type-chip {
  display: flex; flex-direction: column; align-items: center;
  gap: 4px; padding: 10px 6px;
  border: 1.5px solid var(--border-color);
  border-radius: 10px; background: var(--card-bg);
  color: var(--text-subtle); cursor: pointer;
  transition: border-color .15s, background .15s, color .15s;
}
.add-type-chip:hover:not(.active) {
  border-color: var(--primary);
  color: var(--primary);
}
.chip-icon { font-size: 20px; line-height: 1; }
.chip-label { font-size: 11px; font-weight: 600; }
.params-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.param-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.param-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-subtle);
}
</style>
