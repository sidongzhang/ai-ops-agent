<script setup>
import { useDiagnosisChat } from '../useDiagnosisChat'

const props = defineProps({
  systemId: { type: String, required: true },
})

const { ask, chatBox, clearMessages, diagnosing, messages, question, renderMd } = useDiagnosisChat(props.systemId)
</script>

<template>
  <a-card class="panel-card diagnose-card" title="AI 智能诊断">
    <template #extra>
      <a-tooltip title="清空对话">
        <a-button type="text" size="small" :disabled="messages.length === 0" @click="clearMessages">🧹</a-button>
      </a-tooltip>
    </template>
    <div ref="chatBox" class="chat-box chat-box--tall">
      <a-empty
        v-if="messages.length === 0"
        description="问问 AI：这个系统现在有没有问题？"
        :image-style="{ height: '48px' }"
        style="margin-top: 60px"
      />
      <div
        v-for="(message, index) in messages"
        :key="index"
        :class="['msg-row', message.role === 'user' ? 'msg-row--user' : 'msg-row--agent']"
      >
        <span v-if="message.role === 'user'" class="bubble bubble--user">{{ message.text }}</span>
        <div v-else class="bubble bubble--agent" v-html="renderMd(message.text)" />
      </div>
      <div v-if="diagnosing" class="thinking-row">
        <a-spin size="small" /><span>AI 正在分析…</span>
      </div>
    </div>
    <a-input-search
      v-model:value="question"
      placeholder="例如：Redis 内存多少？Kafka 有积压吗？"
      enter-button="发 送"
      :loading="diagnosing"
      @search="ask"
      class="chat-input"
    />
  </a-card>
</template>

<style scoped>
.panel-card {
  border-radius: 12px;
  border: 1px solid var(--border-color);
}
.diagnose-card :deep(.ant-card-body) {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}
.chat-box {
  height: 360px;
  overflow-y: auto;
  background: var(--body-bg);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 12px;
  scroll-behavior: smooth;
}
.chat-box--tall {
  height: calc(100vh - 360px);
  min-height: 400px;
}
.msg-row { margin: 8px 0; }
.msg-row--user { text-align: right; }
.msg-row--agent { text-align: left; }
.bubble { display: inline-block; max-width: 85%; font-size: 14px; line-height: 1.65; }
.bubble--user {
  padding: 9px 14px;
  border-radius: 18px 18px 4px 18px;
  background: var(--primary);
  color: #fff;
}
.bubble--agent {
  padding: 10px 14px;
  border-radius: 4px 18px 18px 18px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
  text-align: left;
}
.thinking-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-subtle);
  margin-top: 8px;
}
.chat-input { margin-top: 2px; }
.bubble--agent :deep(p) { margin: 0 0 8px; }
.bubble--agent :deep(p:last-child) { margin-bottom: 0; }
.bubble--agent :deep(h1),
.bubble--agent :deep(h2),
.bubble--agent :deep(h3) {
  font-size: 13px; font-weight: 700; margin: 12px 0 6px;
  color: var(--text); letter-spacing: .02em;
}
.bubble--agent :deep(h1):first-child,
.bubble--agent :deep(h2):first-child,
.bubble--agent :deep(h3):first-child { margin-top: 0; }
.bubble--agent :deep(table) {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
  margin: 10px 0;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border-color);
}
.bubble--agent :deep(thead tr) {
  background: var(--body-bg);
}
.bubble--agent :deep(th) {
  padding: 7px 12px;
  font-weight: 600;
  font-size: 12px;
  color: var(--text-subtle);
  text-transform: uppercase;
  letter-spacing: .04em;
  border-bottom: 2px solid var(--border-color);
  text-align: left;
  white-space: nowrap;
}
.bubble--agent :deep(td) {
  padding: 7px 12px;
  border-bottom: 1px solid var(--border-color);
  vertical-align: top;
  line-height: 1.55;
}
.bubble--agent :deep(tbody tr:last-child td) { border-bottom: none; }
.bubble--agent :deep(tbody tr:nth-child(even)) {
  background: color-mix(in srgb, var(--body-bg) 60%, transparent);
}
.bubble--agent :deep(code) {
  background: var(--body-bg); border: 1px solid var(--border-color);
  padding: 1px 5px; border-radius: 3px; font-size: 12px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.bubble--agent :deep(pre) {
  background: var(--body-bg); border: 1px solid var(--border-color);
  border-radius: 6px; padding: 10px; overflow-x: auto; margin: 8px 0;
}
.bubble--agent :deep(pre code) { background: none; border: none; padding: 0; }
.bubble--agent :deep(ul),
.bubble--agent :deep(ol) { padding-left: 20px; margin: 4px 0; }
.bubble--agent :deep(li) { margin: 2px 0; }
.bubble--agent :deep(strong) { font-weight: 600; color: var(--text); }
.bubble--agent :deep(blockquote) {
  border-left: 3px solid var(--primary);
  margin: 6px 0; padding: 4px 10px;
  color: var(--text-subtle);
  background: color-mix(in srgb, var(--primary) 6%, transparent);
  border-radius: 0 4px 4px 0;
}
.bubble--agent :deep(hr) { border: none; border-top: 1px solid var(--border-color); margin: 8px 0; }
</style>
