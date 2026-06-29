import { PRESET_LABELS, SERVICE_PRESETS } from './metadata'

export function createSystemServiceDraft() {
  return { name: '', preset: 'nginx', connector: 'http', fields: {}, customFields: {} }
}

export function createDetailServiceDraft() {
  return { preset: 'nginx', name: 'Nginx', connector: 'http', fields: {} }
}

export function syncPresetDraft(draft) {
  const preset = SERVICE_PRESETS[draft.preset]
  draft.connector = preset.connector || 'http'
  draft.fields = {}
  if ('customFields' in draft) {
    draft.customFields = {}
  }
  if (!draft.name || PRESET_LABELS.has(draft.name)) {
    draft.name = preset.label
  }
}
