import { CONNECTOR_FIELDS, SERVICE_PRESETS } from './metadata'

export function buildServicePayload(service) {
  const preset = SERVICE_PRESETS[service.preset]
  if (preset.buildConfig) {
    return {
      name: service.name,
      connector: preset.connector,
      config: preset.buildConfig(service.fields),
    }
  }

  const config = {}
  for (const [key] of CONNECTOR_FIELDS[service.connector] || []) {
    let value = service.customFields[key]
    if (value === undefined || value === '') continue
    if (key === 'port') value = Number(value)
    config[key] = value
  }

  return { name: service.name, connector: service.connector, config }
}
