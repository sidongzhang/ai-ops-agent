import { PRESET_LABELS, SERVICE_PRESETS } from './metadata'

export function getTargetAddressMeta(isLocal) {
  return isLocal
    ? {
        label: '主机地址',
        placeholder: '127.0.0.1',
        hint: '服务跑在平台本机时填 127.0.0.1',
      }
    : {
        label: '目标服务 IP',
        placeholder: '10.0.0.1',
        hint: '填采集器所在网络能访问到的 IP，不是平台地址，也不是你电脑的公网 IP',
      }
}

export function resolvePresetInput(input, isLocal) {
  if (input.key !== 'host') return input
  const meta = getTargetAddressMeta(isLocal)
  return { ...input, label: meta.label, placeholder: meta.placeholder }
}

export function createSystemServiceDraft(preset = 'nginx') {
  return { name: '', preset, connector: SERVICE_PRESETS[preset]?.connector || 'http', fields: {}, customFields: {} }
}

export function createDetailServiceDraft(preset = 'nginx') {
  return {
    preset,
    name: SERVICE_PRESETS[preset]?.label || 'Nginx',
    connector: SERVICE_PRESETS[preset]?.connector || 'http',
    fields: {},
    customFields: {},
  }
}

export function syncPresetDraft(draft, options = {}) {
  const preset = SERVICE_PRESETS[draft.preset]
  const previousFields = draft.fields || {}
  draft.connector = preset.connector || 'http'
  draft.fields = options.mergeFields ? { ...previousFields } : {}
  if ('customFields' in draft) {
    draft.customFields = {}
  }
  if (!options.preserveName && (!draft.name || PRESET_LABELS.has(draft.name))) {
    draft.name = preset.label
  }
}
