<script setup>
import { reactive } from 'vue'
import { message } from 'ant-design-vue'
import api from '../../../api'
import {
  buildServicePayload,
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

const state = reactive({ loading: false })
const newSvc = reactive(createDetailServiceDraft())

function close() {
  emit('update:open', false)
}

function onNewPresetChange() {
  syncPresetDraft(newSvc)
}

async function submitAddSvc() {
  if (!newSvc.name.trim()) return message.warning('请输入服务名称')
  state.loading = true
  try {
    await api.post(`/systems/${props.systemId}/services`, buildServicePayload(newSvc))
    message.success(`服务「${newSvc.name}」已添加`)
    emit('created')
    close()
    Object.assign(newSvc, createDetailServiceDraft())
  } catch (error) {
    message.error(error?.response?.data?.detail || '添加失败')
  } finally {
    state.loading = false
  }
}
</script>

<template>
  <a-modal
    :open="open"
    title="添加被监控服务"
    width="520px"
    :confirm-loading="state.loading"
    ok-text="添加"
    cancel-text="取消"
    @update:open="emit('update:open', $event)"
    @ok="submitAddSvc"
    @cancel="close"
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

      <div v-if="SERVICE_PRESETS[newSvc.preset].inputs.length" class="add-field">
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
    </div>
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
