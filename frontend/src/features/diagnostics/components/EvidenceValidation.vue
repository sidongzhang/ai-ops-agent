<script setup>
import { computed } from 'vue'
const props = defineProps({ validation: Object, reportId: [String, Number] })
const checks = computed(() => (props.validation?.claim_consistency?.checks || []).filter(c => c.status !== 'matches_tool_text'))
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) + '（北京时间）' : '未记录' }
function scopeLabel(value) { return { registered_services: '已注册服务范围', single_service: '单个服务', legacy_unknown: '历史记录，范围未明确' }[value] || '范围未明确' }
function openSource(event) { const node = event.currentTarget.nextElementSibling; if (node) { node.open = true; node.scrollIntoView({ block: 'nearest' }) } }
const labels = { conflicts_tool_text: '与工具观测矛盾', unavailable: '缺少对应证据', stale_or_undated: '证据过期或时间不明', ambiguous: '证据口径不明', scope_insufficient: '证据范围不足' }
</script>
<template>
  <aside v-if="validation?.flagged" class="validation" aria-label="回答证据校验">
    <strong>{{ validation.reason === 'validation_unavailable' ? '校验暂不可用，回答未经本项校验' : '部分结论需要核对证据' }}</strong>
    <p v-if="!checks.length && validation.reason !== 'validation_unavailable'">未取得支持这些结论的有效证据；不代表结论已被判定错误。</p>
    <details v-for="(check, index) in checks" :key="index">
      <summary>{{ labels[check.status] || '需要核对' }}：{{ check.claim || `${check.subject}.${check.field} = ${check.claimed}` }}</summary>
      <p v-if="check.observed_values?.length">实际观测：{{ check.observed_values.join('；') }}</p>
      <p v-else>尚无可比较的有效观测。</p>
      <template v-for="source in check.observations || []" :key="source.call_id">
        <a @click.prevent="openSource" :href="`#validation-${reportId}-${index}-${source.call_id}`">查看证据 {{ source.call_id }}</a>
        <details :id="`validation-${reportId}-${index}-${source.call_id}`">
          <summary>检查时间：{{ formatTime(source.observed_at) }} · {{ scopeLabel(source.scope) }}</summary>
          <ul><li v-for="(service, si) in source.services || []" :key="si">{{ service.service }}：{{ service.ok === true ? '探活通过' : service.ok === false ? '探活未通过' : '状态未知' }}<span v-if="service.http_status"> · HTTP {{ service.http_status }}</span></li></ul>
        </details>
      </template>
      <p v-if="!check.observations?.length && check.tool_call_ids?.length">关联工具调用：{{ check.tool_call_ids.join('、') }}</p>
    </details>
  </aside>
</template>
<style scoped>
.validation { border-left: 3px solid #c99235; padding: 10px 14px; margin: 12px 0; background: rgba(190,140,50,.07); font-size: 13px; overflow-wrap: anywhere; }
.validation p { margin: 6px 0; } summary { cursor: pointer; margin: 7px 0; } pre { white-space: pre-wrap; font-size: 12px; }
</style>
