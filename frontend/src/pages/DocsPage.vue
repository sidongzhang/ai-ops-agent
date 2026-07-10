<template>
  <div class="docs-wrap">
    <div class="docs-header">
      <h1 class="docs-title">辅助文档</h1>
      <p class="docs-subtitle">平台介绍 · 快速上手 · 常见问题</p>
    </div>

    <div class="docs-body">

      <!-- 左侧目录 -->
      <nav class="docs-toc">
        <div
          v-for="sec in sections" :key="sec.id"
          :class="['toc-item', { 'toc-item--active': active === sec.id }]"
          @click="scrollTo(sec.id)"
        >{{ sec.title }}</div>
      </nav>

      <!-- 右侧内容 -->
      <article class="docs-content">

        <section id="intro" class="doc-section">
          <h2>什么是 AIOps 平台？</h2>
          <p>AIOps 是一个多租户智能运维平台，帮助你把分散在各处的服务（MySQL、Redis、Kafka、API 等）统一纳管，通过 AI 对话完成故障诊断，并在服务异常时自动推送告警。</p>
          <div class="feature-grid">
            <div class="feature-card">
              <span class="feature-icon">🖥️</span>
              <strong>统一监控</strong>
              <p>一个界面看所有服务的健康状态，无需分别登录各个系统。</p>
            </div>
            <div class="feature-card">
              <span class="feature-icon">🤖</span>
              <strong>AI 诊断</strong>
              <p>用自然语言提问，AI 自动调用工具检查服务状态、分析日志、给出建议。</p>
            </div>
            <div class="feature-card">
              <span class="feature-icon">🔔</span>
              <strong>自动告警</strong>
              <p>服务异常时自动检测，通过飞书或 Webhook 推送通知，1 小时内不重复告警。</p>
            </div>
            <div class="feature-card">
              <span class="feature-icon">🏢</span>
              <strong>多租户隔离</strong>
              <p>每个账号独立组织空间，数据完全隔离，互相看不到。</p>
            </div>
          </div>
        </section>

        <section id="quickstart" class="doc-section">
          <h2>快速上手</h2>
          <div class="steps">
            <div class="step">
              <div class="step-num">1</div>
              <div class="step-body">
                <strong>注册账号</strong>
                <p>在登录页点击「注册」，填入邮箱和密码，系统自动为你创建独立的组织空间。</p>
              </div>
            </div>
            <div class="step">
              <div class="step-num">2</div>
              <div class="step-body">
                <strong>新建系统</strong>
                <p>进入「监控系统」页面，点击右上角「新建系统」，填入名称和 Key（英文标识符）。</p>
              </div>
            </div>
            <div class="step">
              <div class="step-num">3</div>
              <div class="step-body">
                <strong>添加服务</strong>
                <p>进入系统详情 → 配置 → 添加服务，选择连接器类型（TCP / HTTP），填入 Host 和 Port。</p>
                <a-table
                  :data-source="connectorExamples"
                  :columns="connectorCols"
                  size="small"
                  :pagination="false"
                  style="margin-top:12px"
                />
              </div>
            </div>
            <div class="step">
              <div class="step-num">4</div>
              <div class="step-body">
                <strong>AI 诊断</strong>
                <p>切换到「AI 诊断」Tab，用自然语言描述你的问题，AI 会自动检查并给出分析报告。</p>
                <div class="example-queries">
                  <span v-for="q in exampleQueries" :key="q" class="query-chip">{{ q }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="connector" class="doc-section">
          <h2>连接器说明</h2>
          <p>连接器决定平台如何探活和采集指标。平台服务器会发起连接，所以填写的地址需要从<strong>平台服务器</strong>角度可达。</p>
          <a-table
            :data-source="connectorDetail"
            :columns="connectorDetailCols"
            size="small"
            :pagination="false"
          />
          <a-alert
            message="注意：如果你的服务在平台所在的同一台机器上，host 填 localhost 即可。如果在其他机器，填该机器的内网 IP 或域名。"
            type="info"
            show-icon
            style="margin-top:16px"
          />
        </section>

        <section id="ai" class="doc-section">
          <h2>AI 诊断使用技巧</h2>
          <div class="tip-list">
            <div class="tip" v-for="tip in aiTips" :key="tip.title">
              <strong>{{ tip.title }}</strong>
              <p>{{ tip.desc }}</p>
              <code v-if="tip.example">{{ tip.example }}</code>
            </div>
          </div>
        </section>

        <section id="autofix" class="doc-section">
          <h2>AI 辅助修复</h2>
          <p>诊断发现问题后，点击右上角「🔧 申请修复」，AI 会分析情况并提出一个可执行的修复方案，等待你审批后再执行。</p>

          <div class="flow-steps">
            <div class="flow-step">
              <div class="flow-icon">💬</div>
              <div><strong>提问诊断</strong><p>先用 AI 诊断确认问题</p></div>
            </div>
            <div class="flow-arrow">→</div>
            <div class="flow-step">
              <div class="flow-icon">🔧</div>
              <div><strong>申请修复</strong><p>AI 分析并提出方案</p></div>
            </div>
            <div class="flow-arrow">→</div>
            <div class="flow-step">
              <div class="flow-icon">✅</div>
              <div><strong>审批执行</strong><p>你确认后平台执行</p></div>
            </div>
            <div class="flow-arrow">→</div>
            <div class="flow-step">
              <div class="flow-icon">📊</div>
              <div><strong>回报结果</strong><p>执行结果直接显示</p></div>
            </div>
          </div>

          <a-table
            :data-source="fixActions"
            :columns="fixActionCols"
            size="small"
            :pagination="false"
            style="margin-top:20px"
          />

          <a-alert
            message="安全说明：所有自动执行操作均需你点击「批准执行」才会运行。重启操作限于已注册的服务容器，Redis 命令限于运维类命令（不含删库等高危操作）。"
            type="warning"
            show-icon
            style="margin-top:16px"
          />
        </section>

        <section id="alert" class="doc-section">
          <h2>告警配置</h2>
          <p>进入系统详情 → 配置 → 告警通知，支持两种方式：</p>
          <div class="alert-cards">
            <div class="alert-card">
              <strong>🔵 飞书</strong>
              <p>填入飞书应用的 App ID、App Secret 和目标群的 Chat ID，服务异常时自动发送卡片消息。</p>
            </div>
            <div class="alert-card">
              <strong>🟣 Webhook</strong>
              <p>填入任意 HTTP 接口地址，平台 POST JSON 格式的告警数据，可接入钉钉、企业微信、自建系统等。</p>
            </div>
          </div>
          <p style="margin-top:12px">默认冷却时间为 <strong>1 小时</strong>，同一系统在冷却期内不会重复发送告警。</p>
        </section>

        <section id="faq" class="doc-section">
          <h2>常见问题</h2>
          <a-collapse :bordered="false">
            <a-collapse-panel v-for="faq in faqs" :key="faq.q" :header="faq.q">
              <p>{{ faq.a }}</p>
            </a-collapse-panel>
          </a-collapse>
        </section>

      </article>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const active = ref('intro')

const sections = [
  { id: 'intro',      title: '平台介绍' },
  { id: 'quickstart', title: '快速上手' },
  { id: 'connector',  title: '连接器说明' },
  { id: 'ai',         title: 'AI 诊断技巧' },
  { id: 'autofix',    title: 'AI 辅助修复' },
  { id: 'alert',      title: '告警配置' },
  { id: 'faq',        title: '常见问题' },
]

function scrollTo(id) {
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onScroll() {
  const els = sections.map(s => document.getElementById(s.id)).filter(Boolean)
  for (let i = els.length - 1; i >= 0; i--) {
    if (els[i].getBoundingClientRect().top < 120) {
      active.value = sections[i].id
      return
    }
  }
  active.value = sections[0].id
}

onMounted(() => window.addEventListener('scroll', onScroll, { passive: true }))
onUnmounted(() => window.removeEventListener('scroll', onScroll))

const connectorCols = [
  { title: '服务', dataIndex: 'svc', width: 100 },
  { title: '连接器', dataIndex: 'conn', width: 90 },
  { title: 'Host', dataIndex: 'host' },
  { title: 'Port', dataIndex: 'port', width: 80 },
]
const connectorExamples = [
  { key: '1', svc: 'Redis',      conn: 'TCP',  host: 'localhost', port: 6379 },
  { key: '2', svc: 'MySQL',      conn: 'TCP',  host: 'localhost', port: 3306 },
  { key: '3', svc: 'Kafka',      conn: 'TCP',  host: 'localhost', port: 9092 },
  { key: '4', svc: 'Prometheus', conn: 'HTTP', host: 'http://localhost:9090/metrics', port: '-' },
]

const connectorDetailCols = [
  { title: '类型', dataIndex: 'type', width: 80 },
  { title: '探活方式', dataIndex: 'probe' },
  { title: '适用场景', dataIndex: 'usecase' },
]
const connectorDetail = [
  { key: '1', type: 'TCP',  probe: '建立 TCP 连接，成功即为健康', usecase: 'MySQL、Redis、Kafka、自定义端口' },
  { key: '2', type: 'HTTP', probe: 'GET 请求，2xx 响应为健康',     usecase: 'API 服务、Prometheus、自定义 HTTP 端点' },
]

const exampleQueries = [
  'Redis 内存使用情况怎么样？',
  'Kafka 有没有消息积压？',
  '系统整体健康状态',
  'MySQL 连接数正常吗？',
  '最近有没有报错日志？',
]

const aiTips = [
  {
    title: '直接描述问题现象',
    desc: '不需要懂技术术语，直接说你观察到的现象，AI 会自行推断检查方向。',
    example: '感觉系统变慢了，帮我查一下',
  },
  {
    title: '问整体概览',
    desc: '不确定从哪里查起时，先问整体状态，AI 会扫描所有服务给出全局报告。',
    example: '系统整体健康状态怎么样？',
  },
  {
    title: '追问细节',
    desc: 'AI 给出报告后，可以继续追问，它会保留上下文进一步深入分析。',
    example: 'Redis 峰值为什么这么高？和哪些操作有关？',
  },
  {
    title: '服务专项分析',
    desc: '针对某个服务单独提问，AI 会重点检查该服务的各项指标和日志。',
    example: 'Kafka 消费者组 ops-consumer-group 的 lag 情况',
  },
]

const fixActionCols = [
  { title: '动作类型', dataIndex: 'type', width: 140 },
  { title: '触发场景', dataIndex: 'when' },
  { title: '风险等级', dataIndex: 'risk', width: 90 },
]
const fixActions = [
  { key: '1', type: '🔄 重启容器',         when: '服务崩溃、端口不通、进程无响应',      risk: '⬇️ 低' },
  { key: '2', type: '⚡ Redis 命令',        when: '内存过高需清理、配置调整',             risk: '⬆️ 中' },
  { key: '3', type: '📋 拉取日志',          when: '需要查看服务最近报错详情',             risk: '✅ 无' },
  { key: '4', type: '🩺 健康检查',          when: '修复后验证服务是否恢复',               risk: '✅ 无' },
  { key: '5', type: '📝 人工操作指引',      when: '需要数据迁移、配置修改等复杂操作',    risk: '⚠️ 人工' },
]

const faqs = [
  {
    q: '注册后看不到任何系统，怎么办？',
    a: '注册后系统是空的，需要自己新建。进入「监控系统」→「新建系统」，填入名称和要监控的服务地址即可。',
  },
  {
    q: '填 localhost 能连上服务吗？',
    a: '可以。localhost 指的是平台服务器（运行 AIOps 的那台机器），不是你自己的电脑。如果你的服务和平台在同一台机器上，填 localhost 即可访问。',
  },
  {
    q: 'AI 诊断需要多长时间？',
    a: '通常 10~30 秒，取决于问题复杂度和需要调用的工具数量。复杂问题（如全面健康巡检）可能需要 30~60 秒。',
  },
  {
    q: '告警一直在发，怎么停？',
    a: '默认冷却 1 小时，同一系统不会重复告警。如果持续发，说明服务一直处于不同的异常状态。修复服务后告警会自动停止。',
  },
  {
    q: '我的数据和其他用户隔离吗？',
    a: '完全隔离。每个账号注册时自动创建独立的组织（Org），系统、服务、诊断记录都属于该组织，其他用户无法访问。',
  },
]
</script>

<style scoped>
.docs-wrap {
  max-width: 1100px;
  margin: 0 auto;
}
.docs-header {
  margin-bottom: 32px;
}
.docs-title {
  font-size: 26px;
  font-weight: 700;
  margin-bottom: 6px;
}
.docs-subtitle {
  color: var(--text-secondary, #888);
  font-size: 14px;
}

/* 两栏布局 */
.docs-body {
  display: flex;
  gap: 32px;
  align-items: flex-start;
}

/* 目录 */
.docs-toc {
  width: 150px;
  flex-shrink: 0;
  position: sticky;
  top: 24px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.toc-item {
  padding: 7px 12px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text-secondary, #666);
  transition: background 0.15s, color 0.15s;
}
.toc-item:hover { background: var(--sidebar-hover, rgba(0,0,0,0.05)); }
.toc-item--active {
  background: var(--primary-bg);
  color: var(--primary);
  font-weight: 600;
}

/* 内容区 */
.docs-content {
  flex: 1;
  min-width: 0;
}
.doc-section {
  margin-bottom: 52px;
  scroll-margin-top: 80px;
}
.doc-section h2 {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border, #f0f0f0);
}
.doc-section p {
  font-size: 14px;
  line-height: 1.8;
  color: var(--text, #333);
  margin-bottom: 12px;
}

/* 特性卡片 */
.feature-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
  margin-top: 16px;
}
.feature-card {
  background: var(--card-bg, #fafafa);
  border: 1px solid var(--border, #f0f0f0);
  border-radius: 10px;
  padding: 16px;
}
.feature-card p { margin: 6px 0 0; font-size: 13px; color: var(--text-secondary, #666); }
.feature-icon { font-size: 22px; display: block; margin-bottom: 8px; }

/* 步骤 */
.steps { display: flex; flex-direction: column; gap: 20px; }
.step { display: flex; gap: 16px; }
.step-num {
  width: 30px; height: 30px; border-radius: 50%;
  background: var(--primary);
  color: #fff; font-weight: 700; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; margin-top: 2px;
}
.step-body strong { font-size: 15px; }
.step-body p { margin: 4px 0 0; font-size: 13px; color: var(--text-secondary, #666); }

/* 示例问题 */
.example-queries { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.query-chip {
  background: var(--primary-bg);
  color: var(--primary);
  border-radius: 20px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: default;
}

/* AI 技巧 */
.tip-list { display: flex; flex-direction: column; gap: 20px; }
.tip { background: var(--card-bg, #fafafa); border-radius: 8px; padding: 14px 16px; border-left: 3px solid var(--primary); }
.tip strong { font-size: 14px; }
.tip p { margin: 4px 0 8px; font-size: 13px; color: var(--text-secondary, #666); }
.tip code { background: var(--code-bg, #f5f5f5); padding: 4px 10px; border-radius: 5px; font-size: 12px; }

/* 告警卡片 */
.alert-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.alert-card { background: var(--card-bg, #fafafa); border: 1px solid var(--border, #f0f0f0); border-radius: 10px; padding: 14px 16px; }
.alert-card p { margin: 6px 0 0; font-size: 13px; color: var(--text-secondary, #666); }

/* 修复流程图 */
.flow-steps {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 16px;
}
.flow-step {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 12px 14px;
  flex: 1;
  min-width: 120px;
}
.flow-step p { margin: 2px 0 0; font-size: 12px; color: var(--text-subtle); }
.flow-step strong { font-size: 13px; }
.flow-icon { font-size: 22px; flex-shrink: 0; }
.flow-arrow { font-size: 18px; color: var(--text-subtle); flex-shrink: 0; }

/* 移动端 */
@media (max-width: 767px) {
  .docs-toc { display: none; }
  .feature-grid { grid-template-columns: 1fr; }
  .alert-cards { grid-template-columns: 1fr; }
}
</style>
