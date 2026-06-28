<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const showChrome = computed(() => route.path !== '/login')

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <a-layout v-if="showChrome" style="min-height: 100vh">
    <a-layout-header style="display: flex; align-items: center; justify-content: space-between">
      <div style="color: #fff; font-size: 18px; font-weight: 600">
        🛠️ 智能运维平台
      </div>
      <div style="color: rgba(255,255,255,.85)">
        <span v-if="auth.user" style="margin-right: 16px">
          {{ auth.user.email }}（org #{{ auth.user.org_id }}）
        </span>
        <a-button type="link" style="color: #fff" @click="logout">退出</a-button>
      </div>
    </a-layout-header>
    <a-layout-content style="padding: 24px; max-width: 1100px; margin: 0 auto; width: 100%">
      <router-view />
    </a-layout-content>
  </a-layout>

  <router-view v-else />
</template>
