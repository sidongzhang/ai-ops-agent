import { PRESET_LABELS, SERVICE_PRESETS } from './metadata'

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
