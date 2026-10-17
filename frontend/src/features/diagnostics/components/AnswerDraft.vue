<script setup>
defineProps({ stream: Object, status: String, renderMd: Function })
</script>
<template>
  <div v-if="stream?.messages?.some(m => m.text)" class="answer-draft">
    <p class="draft-label">{{ status === 'running' ? '生成中，尚未完成校验' : status === 'success' ? '生成过程（以正式报告为准）' : '未完成草稿，尚未完成校验' }}</p>
    <p v-if="stream.truncated" class="draft-label">草稿预览已达长度上限，完成后查看完整报告。</p>
    <template v-for="item in stream.messages" :key="item.id">
      <details v-if="item.text && (item.state === 'failed' || item.state === 'process' || status === 'success')">
        <summary>{{ item.state === 'failed' ? '此前尝试失败' : '过程消息' }}</summary>
        <div class="md" v-html="renderMd(item.text)" />
      </details>
      <div v-else-if="item.text" class="md" v-html="renderMd(item.text)" />
    </template>
  </div>
</template>
<style scoped>
.answer-draft { margin: 12px 0; line-height: 1.7; overflow-wrap: anywhere; }
.draft-label, summary { color: var(--text-subtle, #777); font-size: 12px; }
details { margin: 8px 0; } summary { cursor: pointer; }
</style>
