<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import api from '../../../api'
import { CreateSystemModal } from '../components'

const router = useRouter()
const systems = ref([])
const loading = ref(false)
const modalOpen = ref(false)

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/systems')
    systems.value = data
  } catch { message.error('加载失败') }
  finally { loading.value = false }
}
onMounted(load)

function openCreate() {
  modalOpen.value = true
}
</script>

<template>
  <div>
    <!-- 页头 -->
    <div class="page-header">
      <div>
        <h2 class="page-title">监控系统</h2>
        <p class="page-sub">管理所有已接入的被监控系统</p>
      </div>
      <a-button type="primary" size="large" @click="openCreate">+ 注册系统</a-button>
    </div>

    <!-- 空状态 -->
    <div v-if="!loading && systems.length === 0" class="empty-state">
      <div class="empty-icon">⬡</div>
      <div class="empty-title">还没有监控系统</div>
      <div class="empty-sub">注册第一套系统，开始 AI 智能运维</div>
      <a-button type="primary" @click="openCreate" style="margin-top:16px">注册系统</a-button>
    </div>

    <!-- 系统卡片 -->
    <div class="systems-grid">
      <div
        v-for="sys in systems" :key="sys.id"
        class="sys-card"
        @click="router.push(`/systems/${sys.id}`)"
      >
        <div class="sys-card-inner">
          <div class="sys-head">
            <span class="sys-name">{{ sys.name }}</span>
            <a-tag :color="sys.local ? 'success' : 'processing'" style="border:none;margin:0">
              {{ sys.local ? '平台托管' : '远程只读' }}
            </a-tag>
          </div>
          <div class="sys-key">{{ sys.key }}</div>
          <div class="sys-foot">
            <span class="sys-count">{{ sys.services.length }} 个服务</span>
            <span class="sys-goto">查看详情 →</span>
          </div>
        </div>
      </div>
    </div>

    <CreateSystemModal v-model:open="modalOpen" @created="load" />
  </div>
</template>

<style scoped>
/* ── 页头 ── */
.page-header {
  display: flex; justify-content: space-between; align-items: flex-end;
  margin-bottom: 28px;
}
.page-title { font-size: 22px; font-weight: 700; color: var(--text); margin-bottom: 2px; }
.page-sub   { font-size: 13px; color: var(--text-subtle); }

/* ── 空状态 ── */
.empty-state { text-align: center; padding: 80px 0; }
.empty-icon  { font-size: 48px; opacity: .15; margin-bottom: 16px; }
.empty-title { font-size: 18px; font-weight: 600; color: var(--text); margin-bottom: 6px; }
.empty-sub   { font-size: 14px; color: var(--text-subtle); }

/* ── 系统卡片网格 ── */
.systems-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.sys-card {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  cursor: pointer;
  transition: box-shadow .18s, transform .18s, border-color .18s;
  overflow: hidden;
}
.sys-card:hover {
  box-shadow: 0 6px 20px rgba(0,0,0,.09);
  transform: translateY(-2px);
  border-color: var(--primary);
}
.sys-card-inner { padding: 18px 20px; }
.sys-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.sys-name { font-size: 15px; font-weight: 600; color: var(--text); }
.sys-key  { font-size: 12px; color: var(--text-subtle); font-family: 'SF Mono', Menlo, monospace; margin-bottom: 14px; }
.sys-foot { display: flex; justify-content: space-between; font-size: 13px; padding-top: 12px; border-top: 1px solid var(--border-color); }
.sys-count { color: var(--text-subtle); }
.sys-goto  { color: var(--primary); font-weight: 500; }
</style>
