<template>
  <details class="diagnosis-performance" @toggle="opened">
    <summary>诊断性能 · {{ stateLabel }}</summary>
    <div v-if="performance && performance.version" class="performance-body">
      <p>总等待 {{ time(performance.total_ms) }} · 排队 {{ time(performance.queue_ms) }} · 执行 {{ time(performance.execution_ms) }}</p>
      <p>诊断模型 {{ count(performance.model_requests) }} 轮 · 首段文字 {{ time(performance.first_text_ms) }} · 最终报告 {{ performance.final_report_available === false ? '未生成' : time(performance.report_ready_ms) }} <small>（以上文字和报告时间从开始执行计算）</small></p>
      <p>工具请求 {{ count(performance.tool_requests) }} · 实际执行 {{ count(performance.tool_executions) }} · 缓存命中 {{ count(performance.cache_hits) }} · 同参数重复请求 {{ count(performance.repeated_requests) }} · 传输重放 {{ count(performance.replays) }}</p>
      <p class="performance-note">缓存命中仍可能占用工具预算。阶段及父子代理可能并行，耗时不可直接相加。</p>
      <div v-for="stage in stageRows" :key="stage.key" class="performance-row"><span>{{ stage.label }}</span><span>{{ stage.value.count === 0 ? '未发生' : time(stage.value.occupied_ms) }} · {{ stage.value.completed }}/{{ stage.value.count }} 段已结束</span></div>
      <p v-for="(budget, key) in performance.budgets" :key="key">{{ key === 'request' ? '模型请求' : '工具' }}预算：{{ budget.limit_scope === 'attempt' ? budget.attempt_used : budget.used }} / {{ budget.limit == null ? '不限' : budget.limit }}（{{ budget.limit_scope === 'attempt' ? '本次口径' : '含历史累计口径' }}，历史基线 {{ budget.baseline }}，框架报告 {{ budget.framework_reported == null ? '未取得' : budget.framework_reported }}）</p>
      <p v-if="performance.gaps && performance.gaps.length" class="performance-note">采集不完整：{{ performance.gaps.join('、') }}；缺失时间不按 0 处理。</p>
      <p v-if="loading">正在读取受权限保护的时间线…</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="!performance.detail_available">详细时间线已过期或未取得，报告摘要保留。</p>
      <details v-if="timeline && timeline.length"><summary>展开 {{ timeline.length }} 条时间线（最多展示前 200 条）</summary>
        <ol class="performance-timeline"><li v-for="event in timeline.slice(0, 200)" :key="event.id">
          <b>{{ event.stage }} · {{ event.name || '—' }}</b> {{ event.agent }} · {{ event.status }} · 起点 {{ time(event.start_ms) }} · 耗时 {{ time(event.duration_ms) }}
          <small v-if="event.clock === 'pi'">Pi 进程相对时间，与 worker 时间线不直接对齐</small>
          <small v-if="event.call_id">调用 {{ event.call_id }}<template v-if="event.parent_call_id"> · 父调用 {{ event.parent_call_id }}</template></small>
          <small v-if="event.arguments_digest">参数摘要 {{ event.arguments_digest }} · {{ event.cache || '缓存状态未取得' }}</small>
        </li></ol>
      </details>
    </div>
    <p v-else>{{ stateLabel }}；缺失指标不表示耗时为 0。</p>
  </details>
</template>
<script>
export default {
  name: 'DiagnosisPerformance',
  props: { performance: Object, loadDetails: Function },
  data() { return { timeline: null, loading: false, error: '' } },
  computed: {
    stateLabel() { return ({queued:'排队中',complete:'已采集',incomplete:'采集不完整',collecting:'采集中',unavailable:'暂不可用',not_collected:'未采集'})[(this.performance || {}).state] || '未采集' },
    stageRows() { const labels={context:'上下文准备',model:'模型请求',tool:'工具等待',tool_execution:'工具实际执行',report:'报告处理'};return Object.keys(labels).map(key=>({key,label:labels[key],value:((this.performance || {}).stages || {})[key] || {}})) }
  },
  methods: {
    count(value) { return typeof value !== 'number' ? '未取得' : (this.performance || {}).counts_complete === false ? '已记录 ' + value : value },
    time(value) { return typeof value === 'number' ? (value / 1000).toFixed(2) + ' 秒' : '未取得' },
    async opened(event) {
      if (!event.target.open || this.loading || this.timeline) return
      if (this.performance && this.performance.timeline) { this.timeline=this.performance.timeline;return }
      if (!this.loadDetails || !(this.performance || {}).detail_available) return
      this.loading=true;this.error=''
      try { const value=await this.loadDetails();this.timeline=value && value.timeline } catch { this.error='读取失败，请确认登录和访问权限后重新展开。' } finally { this.loading=false }
    }
  }
}
</script>
<style scoped>
.diagnosis-performance{margin:12px 0;padding:10px 12px;border:1px solid var(--border-color,#dce5e3);border-radius:10px;font-size:12px;color:inherit;background:transparent}
.diagnosis-performance summary{cursor:pointer;font-weight:500}.performance-body p{margin:8px 0;line-height:1.6}.performance-note{opacity:.72}.performance-row{display:flex;justify-content:space-between;gap:16px;margin:5px 0}.performance-timeline{padding-left:20px;max-height:360px;overflow:auto}.performance-timeline li{margin:10px 0;overflow-wrap:anywhere}.performance-timeline small{display:block;opacity:.72}
</style>
