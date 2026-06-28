<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import api from '../api'

const props = defineProps({ id: { type: String, required: true } })

const system = ref(null)
const health = ref(null)
const healthLoading = ref(false)

// 诊断对话
const question = ref('')
const messages = ref([]) // { role: 'user'|'agent', text }
const diagnosing = ref(false)

async function loadSystem() {
  try {
    const { data } = await api.get(`/systems/${props.id}`)
    system.value = data
  } catch (e) {
    message.error('加载系统失败')
  }
}

async function runHealth() {
  healthLoading.value = true
  try {
    const { data } = await api.get(`/systems/${props.id}/health`)
    health.value = data
  } catch (e) {
    message.error('探活失败')
  } finally {
    healthLoading.value = false
  }
}

async function ask() {
  const q = question.value.trim()
  if (!q) return
  messages.value.push({ role: 'user', text: q })
  question.value = ''
  diagnosing.value = true
  try {
    const { data } = await api.post(`/systems/${props.id}/diagnose`, { question: q })
    messages.value.push({ role: 'agent', text: data.answer })
  } catch (e) {
    messages.value.push({ role: 'agent', text: '诊断失败：' + (e?.response?.data?.detail || e.message) })
  } finally {
    diagnosing.value = false
  }
}

onMounted(() => {
  loadSystem()
  runHealth()
})
</script>

<template>
  <div v-if="system">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px">
      <a-button @click="$router.push('/systems')">← 返回</a-button>
      <h2 style="margin: 0">{{ system.name }}</h2>
      <a-tag :color="system.local ? 'green' : 'blue'">
        {{ system.local ? '平台托管' : '远程只读' }}
      </a-tag>
    </div>

    <a-row :gutter="16">
      <!-- 健康状态 -->
      <a-col :span="12">
        <a-card title="健康状态">
          <template #extra>
            <a-button size="small" :loading="healthLoading" @click="runHealth">重新探活</a-button>
          </template>
          <a-alert
            v-if="health"
            :type="health.healthy ? 'success' : 'error'"
            :message="health.healthy ? '所有服务正常' : '存在异常服务'"
            show-icon
            style="margin-bottom: 12px"
          />
          <a-list :data-source="health?.services || []" size="small">
            <template #renderItem="{ item }">
              <a-list-item>
                <a-list-item-meta :title="item.name" :description="item.detail" />
                <template #extra>
                  <a-tag :color="item.ok ? 'green' : 'red'">{{ item.ok ? '正常' : '异常' }}</a-tag>
                </template>
              </a-list-item>
            </template>
          </a-list>
        </a-card>
      </a-col>

      <!-- AI 诊断对话 -->
      <a-col :span="12">
        <a-card title="🤖 AI 智能诊断">
          <div style="height: 320px; overflow-y: auto; background: #fafafa; border-radius: 8px; padding: 12px; margin-bottom: 12px">
            <a-empty v-if="messages.length === 0" description="问问 AI：这个系统现在有没有问题？" />
            <div v-for="(m, i) in messages" :key="i"
                 :style="{ textAlign: m.role === 'user' ? 'right' : 'left', margin: '8px 0' }">
              <span :style="{
                display: 'inline-block', maxWidth: '85%', padding: '8px 12px', borderRadius: '8px',
                whiteSpace: 'pre-wrap', textAlign: 'left',
                background: m.role === 'user' ? '#1677ff' : '#fff',
                color: m.role === 'user' ? '#fff' : '#000',
                border: m.role === 'user' ? 'none' : '1px solid #eee',
              }">{{ m.text }}</span>
            </div>
            <div v-if="diagnosing" style="color: #999; font-size: 13px">AI 正在诊断中…</div>
          </div>
          <a-input-search
            v-model:value="question"
            placeholder="例如：consumer 是不是挂了？"
            enter-button="诊断"
            :loading="diagnosing"
            @search="ask"
          />
        </a-card>
      </a-col>
    </a-row>

    <!-- 已注册服务 -->
    <a-card title="已注册服务" style="margin-top: 16px">
      <a-table :data-source="system.services" row-key="id" size="small"
               :columns="[
                 { title: '服务名', dataIndex: 'name' },
                 { title: '连接器', dataIndex: 'connector' },
                 { title: '配置', dataIndex: 'config' },
               ]">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'config'">
            <code style="font-size: 12px">{{ JSON.stringify(record.config) }}</code>
          </template>
        </template>
      </a-table>
    </a-card>
  </div>

  <a-spin v-else style="display: block; margin-top: 80px" />
</template>
