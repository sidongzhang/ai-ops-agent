<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const active = ref('entry')

const sections = [
  { id: 'entry', title: '接入路径' },
  { id: 'local', title: '本机接入' },
  { id: 'remote', title: '远程接入' },
  { id: 'monitor', title: '巡检与告警' },
  { id: 'diagnosis', title: '诊断与处置' },
  { id: 'faq', title: '常见问题' },
]

const connectorColumns = [
  { title: '服务类型', dataIndex: 'service', width: 120 },
  { title: '推荐接入', dataIndex: 'connector', width: 120 },
  { title: '填写示例', dataIndex: 'example' },
]

const connectorRows = [
  { key: 'api', service: '后端 / API', connector: 'HTTP', example: 'http://10.0.0.12:8080/actuator/health' },
  { key: 'mysql', service: 'MySQL', connector: 'TCP', example: '10.0.0.12:3306' },
  { key: 'redis', service: 'Redis', connector: 'TCP', example: '10.0.0.15:6379' },
  { key: 'kafka', service: 'Kafka', connector: 'TCP', example: '10.0.0.21:9092' },
  { key: 'prom', service: 'Prometheus', connector: 'HTTP', example: 'http://10.0.0.30:9090/-/healthy' },
]

const remotePlan = [
  {
    title: '先登记系统和服务',
    desc: '先把业务系统名称、环境和核心服务录入平台，平台据此生成监控视图与采集范围。',
  },
  {
    title: '在对方环境部署采集器',
    desc: '采集器主动连回平台，不要求你能直接 SSH 到对方内网，适合客户机房、云主机和办公电脑。',
  },
  {
    title: '按授权读取日志与状态',
    desc: '优先读取健康检查、Prometheus、只读日志和只读查询，减少对业务系统的打扰。',
  },
  {
    title: '需要动作时走审批',
    desc: '重启、受控命令等动作先给建议，再审批执行，并留下审计记录。',
  },
]

const monitorCards = [
  { key: 'health', title: '定时巡检', desc: '按系统独立设置巡检周期，自动刷新服务健康状态。' },
  { key: 'notify', title: '三路告警', desc: '支持站内、飞书、邮件。未配外部渠道时，消息仍保留在平台内。' },
  { key: 'incident', title: '事故归并', desc: '同类异常可汇总为事故，避免消息四散，方便统一跟进。' },
]

const diagnosisCards = [
  { key: 'metrics', title: '指标分析', desc: '优先利用 Prometheus、Redis、Kafka 等已注册能力做快速定位。' },
  { key: 'logs', title: '日志汇总', desc: '已接入服务才能展示日志和运行状态，未接入的不占界面位置。' },
  { key: 'ai', title: 'AI 建议', desc: '诊断结果需要附带证据来源，建议可继续追问，并沉淀到知识库。' },
  { key: 'ops', title: '受控处置', desc: '在得到授权的前提下，可做重启和受限运维动作，不直接开放高危写操作。' },
]

const faqItems = [
  {
    q: '一个业务系统里有前端、Spring、MySQL、Redis，应该怎么登记？',
    a: '先创建一个系统，再把这些服务逐个加到该系统下。平台按系统汇总视图，按服务做探测和诊断。',
  },
  {
    q: '对方服务不在平台本机，怎么接？',
    a: '优先用远程采集器。它从对方环境主动连回平台，适合不能开放入站端口的场景。',
  },
  {
    q: '为什么有的卡片看不到？',
    a: '平台按已注册服务动态显示功能。没有接入 Prometheus、日志或数据库时，对应模块不会出现。',
  },
  {
    q: '邮件告警怎么用？',
    a: '在系统详情的告警通知里填写收件邮箱和 SMTP 参数，保存后即可发送测试通知和正式告警。',
  },
]

const quickSummary = computed(() => [
  '先把系统和核心服务接进来',
  '本机服务直接探测，远程服务走采集器',
  '先启用巡检和告警，再做诊断与处置',
])

function scrollTo(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onScroll() {
  const visible = sections
    .map((item) => ({ ...item, el: document.getElementById(item.id) }))
    .filter((item) => item.el)
  for (let i = visible.length - 1; i >= 0; i -= 1) {
    if (visible[i].el.getBoundingClientRect().top < 130) {
      active.value = visible[i].id
      return
    }
  }
  active.value = sections[0].id
}

onMounted(() => window.addEventListener('scroll', onScroll, { passive: true }))
onUnmounted(() => window.removeEventListener('scroll', onScroll))
</script>

<template>
  <div class="guide-page">
    <header class="guide-hero">
      <div>
        <p class="hero-eyebrow">接入与使用</p>
        <h1>把系统接进来，再把巡检和处置跑通</h1>
        <p class="hero-sub">
          这页只保留真正有用的操作路径：怎么登记系统、怎么接本机或远程服务、怎么开巡检、怎么做诊断和受控处置。
        </p>
      </div>
      <div class="hero-card">
        <span class="hero-card-label">当前推荐顺序</span>
        <ol>
          <li v-for="item in quickSummary" :key="item">{{ item }}</li>
        </ol>
      </div>
    </header>

    <div class="guide-layout">
      <nav class="guide-nav">
        <button
          v-for="item in sections"
          :key="item.id"
          type="button"
          :class="['nav-item', { active: active === item.id }]"
          @click="scrollTo(item.id)"
        >
          {{ item.title }}
        </button>
      </nav>

      <main class="guide-content">
        <section id="entry" class="guide-section">
          <div class="section-head">
            <h2>先定清楚系统边界</h2>
            <p>平台管理的是“系统”下面的一组服务，不是单独几个端口。先把业务系统独立出来，后续巡检、告警、日志和诊断才有归属。</p>
          </div>
          <div class="entry-grid">
            <article class="entry-card">
              <span class="entry-step">01</span>
              <h3>创建系统</h3>
              <p>用业务名称创建系统，例如“慧眼系统”“Kafka 文件系统”，一个系统对应一套独立业务。</p>
            </article>
            <article class="entry-card">
              <span class="entry-step">02</span>
              <h3>登记核心服务</h3>
              <p>优先登记前端、后端、数据库、Redis、Kafka、Prometheus 这些会直接影响业务可用性的组件。</p>
            </article>
            <article class="entry-card">
              <span class="entry-step">03</span>
              <h3>按服务显示能力</h3>
              <p>平台会根据已注册服务决定展示哪些功能，没有接进来的服务不会占界面空间。</p>
            </article>
          </div>
          <a-table :data-source="connectorRows" :columns="connectorColumns" :pagination="false" size="small" />
        </section>

        <section id="local" class="guide-section">
          <div class="section-head">
            <h2>本机接入</h2>
            <p>平台能直接访问的服务，优先走本机托管。部署最轻，验证最快，适合先跑通第一套系统。</p>
          </div>
          <div class="plan-card">
            <ul>
              <li>服务和平台在同机或同网段时，直接填服务地址即可。</li>
              <li>测试通过后再启用监控，避免把错误配置带进巡检。</li>
              <li>Prometheus 已部署时，优先接入 Prometheus，后续很多分析都能直接复用。</li>
            </ul>
          </div>
        </section>

        <section id="remote" class="guide-section">
          <div class="section-head">
            <h2>远程接入</h2>
            <p>远程系统不要求你能直接进入对方网络。更合理的方案是让采集器从对方环境主动连回平台，双方都更省事。</p>
          </div>
          <div class="timeline">
            <article v-for="item in remotePlan" :key="item.title" class="timeline-item">
              <div class="timeline-dot" />
              <div>
                <h3>{{ item.title }}</h3>
                <p>{{ item.desc }}</p>
              </div>
            </article>
          </div>
          <a-alert
            type="info"
            show-icon
            message="推荐做法"
            description="远程场景下，平台不直接要求开放 SSH 或数据库写权限。先用采集器读取状态、日志和指标，只有在你明确授权时才执行受控动作。"
          />
        </section>

        <section id="monitor" class="guide-section">
          <div class="section-head">
            <h2>巡检与告警</h2>
            <p>一套系统接进来后，真正让它可运营的是定时巡检和告警闭环，而不是只把服务名字挂在页面上。</p>
          </div>
          <div class="capability-grid">
            <article v-for="item in monitorCards" :key="item.key" class="capability-card">
              <h3>{{ item.title }}</h3>
              <p>{{ item.desc }}</p>
            </article>
          </div>
        </section>

        <section id="diagnosis" class="guide-section">
          <div class="section-head">
            <h2>诊断与处置</h2>
            <p>平台的价值不只是发现问题，还要把证据、建议和动作串起来，并把高风险动作控制住。</p>
          </div>
          <div class="capability-grid diagnosis-grid">
            <article v-for="item in diagnosisCards" :key="item.key" class="capability-card">
              <h3>{{ item.title }}</h3>
              <p>{{ item.desc }}</p>
            </article>
          </div>
          <div class="plan-card subtle">
            <strong>推荐闭环</strong>
            <p>巡检发现异常 -> 站内 / 飞书 / 邮件提醒 -> 查看系统状态、指标和日志 -> AI 汇总原因和建议 -> 审批后执行重启或受限动作 -> 再次回查是否恢复。</p>
          </div>
        </section>

        <section id="faq" class="guide-section">
          <div class="section-head">
            <h2>常见问题</h2>
            <p>这里保留最常会卡住接入进度的几个点，避免在页面里堆很多没必要的说明。</p>
          </div>
          <a-collapse :bordered="false" class="faq-collapse">
            <a-collapse-panel v-for="item in faqItems" :key="item.q" :header="item.q">
              <p>{{ item.a }}</p>
            </a-collapse-panel>
          </a-collapse>
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.guide-page {
  max-width: 1120px;
  margin: 0 auto;
}

.guide-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(260px, 0.9fr);
  gap: 16px;
  margin-bottom: 28px;
}

.hero-eyebrow {
  font-size: 12px;
  font-weight: 700;
  color: var(--primary);
  margin-bottom: 8px;
}

.guide-hero h1 {
  font-size: 28px;
  line-height: 1.2;
  color: var(--text);
  margin-bottom: 10px;
}

.hero-sub {
  color: var(--text-subtle);
  font-size: 14px;
  line-height: 1.7;
  max-width: 720px;
}

.hero-card {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  border-radius: 12px;
  padding: 18px;
  align-self: start;
}

.hero-card-label {
  display: block;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-subtle);
  margin-bottom: 10px;
}

.hero-card ol {
  margin: 0;
  padding-left: 18px;
  color: var(--text);
  font-size: 13px;
  line-height: 1.8;
}

.guide-layout {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 28px;
}

.guide-nav {
  position: sticky;
  top: 24px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-self: start;
}

.nav-item {
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-subtle);
  border-radius: 10px;
  padding: 9px 12px;
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}

.nav-item:hover {
  background: color-mix(in srgb, var(--primary) 4%, var(--card-bg));
}

.nav-item.active {
  border-color: color-mix(in srgb, var(--primary) 20%, var(--border-color));
  background: color-mix(in srgb, var(--primary) 8%, var(--card-bg));
  color: var(--primary);
  font-weight: 700;
}

.guide-content {
  min-width: 0;
}

.guide-section {
  margin-bottom: 44px;
  scroll-margin-top: 90px;
}

.section-head {
  margin-bottom: 16px;
}

.section-head h2 {
  font-size: 19px;
  color: var(--text);
  margin-bottom: 8px;
}

.section-head p {
  color: var(--text-subtle);
  font-size: 14px;
  line-height: 1.7;
}

.entry-grid,
.capability-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.diagnosis-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.entry-card,
.capability-card,
.plan-card {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  border-radius: 12px;
}

.entry-card,
.capability-card {
  padding: 16px;
}

.entry-step {
  display: inline-flex;
  min-width: 36px;
  height: 24px;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary) 10%, var(--card-bg));
  color: var(--primary);
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 10px;
}

.entry-card h3,
.capability-card h3,
.timeline-item h3 {
  font-size: 15px;
  color: var(--text);
  margin-bottom: 6px;
}

.entry-card p,
.capability-card p,
.timeline-item p,
.plan-card p {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-subtle);
}

.plan-card {
  padding: 16px 18px;
}

.plan-card ul {
  margin: 0;
  padding-left: 18px;
  color: var(--text);
  font-size: 13px;
  line-height: 1.8;
}

.plan-card.subtle strong {
  display: block;
  margin-bottom: 8px;
  color: var(--text);
  font-size: 14px;
}

.timeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 16px;
}

.timeline-item {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding: 2px 0;
}

.timeline-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--primary);
  margin-top: 6px;
}

.faq-collapse {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--card-bg);
  overflow: hidden;
}

.faq-collapse :deep(.ant-collapse-header) {
  font-weight: 600;
}

.faq-collapse :deep(.ant-collapse-content-box) {
  color: var(--text-subtle);
  line-height: 1.7;
}

@media (max-width: 960px) {
  .guide-hero,
  .guide-layout,
  .entry-grid,
  .capability-grid,
  .diagnosis-grid {
    grid-template-columns: 1fr;
  }

  .guide-nav {
    position: static;
    flex-direction: row;
    flex-wrap: wrap;
  }
}
</style>
