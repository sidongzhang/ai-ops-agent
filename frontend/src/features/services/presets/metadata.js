export const SERVICE_PRESETS = {
  nginx: {
    label: 'Nginx',
    icon: '🌐',
    desc: 'Web 服务器',
    connector: 'http',
    inputs: [
      { key: 'host', label: '主机地址', placeholder: '10.0.0.1', span: 14 },
      { key: 'port', label: '端口', placeholder: '80', span: 10 },
    ],
    buildConfig: (fields) => ({ health_url: `http://${fields.host}:${fields.port || 80}` }),
  },
  springboot: {
    label: 'Spring Boot',
    icon: '🍃',
    desc: 'Java 后端',
    connector: 'http',
    inputs: [
      { key: 'host', label: '主机地址', placeholder: '10.0.0.1', span: 12 },
      { key: 'port', label: '端口', placeholder: '8080', span: 6 },
      { key: 'path', label: '健康端点', placeholder: '/actuator/health', span: 24 },
    ],
    buildConfig: (fields) => ({
      health_url: `http://${fields.host}:${fields.port || 8080}${fields.path || '/actuator/health'}`,
    }),
  },
  tomcat: {
    label: 'Tomcat',
    icon: '🐈',
    desc: '应用容器',
    connector: 'tcp',
    inputs: [
      { key: 'host', label: '主机地址', placeholder: '10.0.0.1', span: 14 },
      { key: 'port', label: '端口', placeholder: '8080', span: 10 },
    ],
    buildConfig: (fields) => ({ host: fields.host, port: Number(fields.port) || 8080 }),
  },
  mysql: {
    label: 'MySQL',
    icon: '🐬',
    desc: '关系数据库',
    connector: 'tcp',
    inputs: [
      { key: 'host', label: '主机地址', placeholder: '10.0.0.1', span: 14 },
      { key: 'port', label: '端口', placeholder: '3306', span: 10 },
    ],
    buildConfig: (fields) => ({ host: fields.host, port: Number(fields.port) || 3306 }),
  },
  redis: {
    label: 'Redis',
    icon: '⚡',
    desc: '缓存 / 队列',
    connector: 'tcp',
    inputs: [
      { key: 'host', label: '主机地址', placeholder: '10.0.0.1', span: 14 },
      { key: 'port', label: '端口', placeholder: '6379', span: 10 },
    ],
    buildConfig: (fields) => ({ host: fields.host, port: Number(fields.port) || 6379 }),
  },
  prometheus: {
    label: 'Prometheus',
    icon: '📊',
    desc: '指标采集',
    connector: 'prometheus',
    inputs: [
      { key: 'host', label: '主机地址', placeholder: '10.0.0.1', span: 14 },
      { key: 'port', label: '端口', placeholder: '9090', span: 10 },
    ],
    buildConfig: (fields) => ({ url: `http://${fields.host}:${fields.port || 9090}`, up_query: 'up' }),
  },
  kafka: {
    label: 'Kafka',
    icon: '📨',
    desc: '消息队列',
    connector: 'tcp',
    inputs: [
      { key: 'host', label: '主机地址', placeholder: '10.0.0.1', span: 14 },
      { key: 'port', label: '端口', placeholder: '9092', span: 10 },
    ],
    buildConfig: (fields) => ({ host: fields.host, port: Number(fields.port) || 9092 }),
  },
  custom: {
    label: '自定义',
    icon: '⚙️',
    desc: '手动配置',
    connector: null,
    inputs: [
      { key: 'health_url', label: 'HTTP 健康检查 URL', placeholder: 'http://host:port/health', span: 24 },
    ],
    buildConfig: (fields) => ({ health_url: fields.health_url }),
  },
}

export const PRESET_LABELS = new Set(Object.values(SERVICE_PRESETS).map((preset) => preset.label))

export const SERVICE_COLORS = {
  nginx: '#22C55E',
  springboot: '#16A34A',
  tomcat: '#F97316',
  mysql: '#3B82F6',
  redis: '#EF4444',
  prometheus: '#F59E0B',
  kafka: '#8B5CF6',
  custom: '#6B7280',
  http: '#22C55E',
  tcp: '#3B82F6',
}

export const CONNECTOR_FIELDS = {
  http: [['health_url', 'HTTP 健康检查 URL', 'https://example.com/health']],
  tcp: [['host', '主机', 'example.com'], ['port', '端口', '443']],
  prometheus: [['url', 'Prometheus 地址', 'http://prom:9090'], ['up_query', '探活 PromQL', 'up']],
  ssh: [['host', '主机', '1.2.3.4'], ['user', '用户', 'ops'], ['log_path', '日志路径', '/var/log/app.log']],
  local: [['kind', 'process / docker', 'process'], ['log_file', '日志文件', 'logs/app.log']],
}

export const CONNECTORS = Object.keys(CONNECTOR_FIELDS)
