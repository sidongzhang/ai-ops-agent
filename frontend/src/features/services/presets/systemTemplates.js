import { createSystemServiceDraft, syncPresetDraft } from './drafts'

function draft(preset, name, fields = {}) {
  const item = createSystemServiceDraft(preset)
  item.name = name
  item.fields = { ...item.fields, ...fields }
  syncPresetDraft(item, { preserveName: true, mergeFields: true })
  item.name = name
  return item
}

// 模板只预填端口/路径，不预填 IP。本机托管注册时可自动补 127.0.0.1，远程系统必须由用户填写目标 IP。
export const SYSTEM_TEMPLATES = [
  {
    key: 'spring-web',
    label: 'Java Web 系统',
    description: '适合前端 + Spring Boot + MySQL + Redis + Prometheus 的常见业务系统。',
    presets: [
      draft('nginx', '前端服务', { port: '80' }),
      draft('springboot', 'Spring Boot API', { port: '8080', path: '/actuator/health' }),
      draft('mysql', 'MySQL', { port: '3306' }),
      draft('redis', 'Redis', { port: '6379' }),
      draft('prometheus', 'Prometheus', { port: '9090', query: 'up' }),
    ],
  },
  {
    key: 'data-pipeline',
    label: '数据 / 文件处理系统',
    description: '适合 Kafka、处理服务、数据库和指标采集一起接入的场景。',
    presets: [
      draft('springboot', '处理服务', { port: '8080', path: '/actuator/health' }),
      draft('kafka', 'Kafka', { port: '9092' }),
      draft('mysql', '业务数据库', { port: '3306' }),
      draft('redis', 'Redis', { port: '6379' }),
      draft('prometheus', 'Prometheus', { port: '9090', query: 'up' }),
    ],
  },
  {
    key: 'minimal-remote',
    label: '远程轻量系统',
    description: '适合先登记核心服务，再在目标网络部署采集器逐步补充日志、指标和远程操作。',
    presets: [
      draft('nginx', '前端入口', { port: '80' }),
      draft('springboot', '应用接口', { port: '8080', path: '/healthz' }),
      draft('prometheus', 'Prometheus', { port: '9090', query: 'up' }),
    ],
  },
]
