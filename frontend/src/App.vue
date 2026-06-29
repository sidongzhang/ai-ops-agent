<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'
import { useThemeStore, THEMES } from './stores/theme'

const auth = useAuthStore()
const themeStore = useThemeStore()
const route = useRoute()
const router = useRouter()

const showShell = computed(() => route.path !== '/login')
const userInitial = computed(() => auth.user?.email?.[0]?.toUpperCase() ?? 'U')

// 侧边栏收起/展开
const collapsed = ref(localStorage.getItem('aiops_sidebar') === '1')
function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('aiops_sidebar', collapsed.value ? '1' : '0')
}

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <a-config-provider :theme="themeStore.antTheme">
    <router-view v-if="!showShell" />

    <div v-else class="shell">

      <!-- ── Sidebar ── -->
      <aside :class="['sidebar', { 'sidebar--collapsed': collapsed }]">

        <!-- Logo + Toggle -->
        <div class="logo-row">
          <div class="logo-mark" :style="collapsed ? 'cursor:pointer' : ''"
               :title="collapsed ? '展开侧边栏' : ''"
               @click="collapsed ? toggleSidebar() : undefined">⚡</div>
          <span v-show="!collapsed" class="logo-name">AIOps</span>
          <!-- 收起时 toggle-btn 绝对定位，始终可点 -->
          <button class="toggle-btn" @click="toggleSidebar" :title="collapsed ? '展开' : '收起'">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
                 width="14" height="14">
              <polyline v-if="!collapsed" points="15 18 9 12 15 6" />
              <polyline v-else points="9 18 15 12 9 6" />
            </svg>
          </button>
        </div>

        <!-- Nav -->
        <nav class="nav">
          <a-tooltip
            :title="collapsed ? '监控系统' : ''"
            placement="right"
            :mouse-enter-delay="0.1"
          >
            <router-link to="/systems" class="nav-link" active-class="nav-link--active">
              <svg class="nav-icon" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.8">
                <rect x="2" y="3" width="20" height="14" rx="2" />
                <path d="M8 21h8M12 17v4" />
              </svg>
              <span v-show="!collapsed" class="nav-label">监控系统</span>
            </router-link>
          </a-tooltip>
        </nav>

        <div style="flex:1" />

        <!-- Footer -->
        <div class="sidebar-footer">

          <!-- 展开时：主题选择器 -->
          <div v-show="!collapsed" class="theme-row">
            <span class="theme-label">主题</span>
            <div class="theme-dots">
              <a-tooltip
                v-for="(t, k) in THEMES" :key="k"
                :title="t.name + ' · ' + t.label"
                placement="top"
                :mouse-enter-delay="0"
              >
                <button
                  class="theme-dot"
                  :class="{ active: themeStore.key === k }"
                  :style="{ background: t.colorPrimary }"
                  @click="themeStore.setTheme(k)"
                />
              </a-tooltip>
            </div>
          </div>

          <!-- 收起时：主题点竖排 -->
          <div v-show="collapsed" class="theme-dots-v">
            <a-tooltip
              v-for="(t, k) in THEMES" :key="k"
              :title="t.name"
              placement="right"
              :mouse-enter-delay="0"
            >
              <button
                class="theme-dot theme-dot--sm"
                :class="{ active: themeStore.key === k }"
                :style="{ background: t.colorPrimary }"
                @click="themeStore.setTheme(k)"
              />
            </a-tooltip>
          </div>

          <!-- 分隔线 -->
          <div class="footer-divider" />

          <!-- 用户 -->
          <div class="user-row">
            <a-tooltip
              :title="collapsed ? (auth.user?.email ?? '') : ''"
              placement="right"
            >
              <div class="user-avatar">{{ userInitial }}</div>
            </a-tooltip>

            <div v-show="!collapsed" class="user-meta">
              <div class="user-email">{{ auth.user?.email }}</div>
              <div class="user-org">组织 #{{ auth.user?.org_id }}</div>
            </div>

            <a-tooltip title="退出登录" placement="right">
              <button class="logout-btn" @click="logout">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="1.8" width="15" height="15">
                  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                  <polyline points="16 17 21 12 16 7" />
                  <line x1="21" y1="12" x2="9" y2="12" />
                </svg>
              </button>
            </a-tooltip>
          </div>
        </div>
      </aside>

      <!-- ── Main content ── -->
      <main :class="['content', { 'content--wide': collapsed }]">
        <router-view />
      </main>
    </div>
  </a-config-provider>
</template>

<style>
/* ── 全局重置 ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html {
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  -webkit-font-smoothing: antialiased;
}
body {
  background: var(--body-bg, #FEFCF7);
  color: var(--text, #1A1206);
  min-height: 100vh;
}
</style>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

/* ── 侧边栏 ── */
.sidebar {
  width: 220px;
  min-height: 100vh;
  position: fixed;
  left: 0; top: 0; bottom: 0;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  padding-bottom: 16px;
  z-index: 100;
  overflow: hidden;
  transition: width 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}
.sidebar--collapsed {
  width: 56px;
}

/* Logo 行 */
.logo-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 14px 16px;
  border-bottom: 1px solid var(--sidebar-border);
  margin-bottom: 8px;
  position: relative;
  min-height: 62px;
}
.logo-mark {
  width: 30px; height: 30px;
  background: var(--sidebar-active);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 15px;
  flex-shrink: 0;
}
.logo-name {
  font-size: 15px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.04em;
  white-space: nowrap;
  flex: 1;
}
.toggle-btn {
  background: none;
  border: none;
  color: var(--sidebar-text);
  opacity: 0.5;
  cursor: pointer;
  padding: 4px;
  border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.15s, background 0.15s;
  flex-shrink: 0;
  /* 收起时绝对定位到右边，永远可点 */
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
}
.toggle-btn:hover { opacity: 1; background: rgba(255,255,255,0.12); }

/* Nav */
.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 8px;
}
.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 7px;
  color: var(--sidebar-text);
  text-decoration: none;
  font-size: 13.5px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s;
  overflow: hidden;
}
.nav-link:hover { background: var(--sidebar-hover); color: #fff; }
.nav-link--active {
  background: var(--sidebar-active-bg) !important;
  color: var(--sidebar-active) !important;
  font-weight: 600;
}
.nav-icon { width: 17px; height: 17px; flex-shrink: 0; }
.nav-label { white-space: nowrap; }

/* Footer */
.sidebar-footer {
  padding: 12px 10px 4px;
  border-top: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.footer-divider {
  height: 1px;
  background: var(--sidebar-border);
  margin: 2px 0;
}

/* 主题切换（横排） */
.theme-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px;
}
.theme-label {
  font-size: 10px;
  color: var(--sidebar-text);
  opacity: 0.4;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.theme-dots {
  display: flex;
  gap: 6px;
}
/* 主题切换（竖排，收起时） */
.theme-dots-v {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
}
.theme-dot {
  width: 16px; height: 16px;
  border-radius: 50%;
  border: 2px solid transparent;
  cursor: pointer;
  padding: 0;
  transition: transform 0.15s, border-color 0.15s;
  flex-shrink: 0;
}
.theme-dot--sm {
  width: 14px; height: 14px;
}
.theme-dot:hover { transform: scale(1.25); }
.theme-dot.active {
  border-color: rgba(255,255,255,0.75);
  transform: scale(1.15);
}

/* 用户行 */
.user-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 4px;
}
.user-avatar {
  width: 30px; height: 30px;
  border-radius: 50%;
  background: var(--sidebar-active);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  cursor: default;
}
.user-meta { flex: 1; min-width: 0; }
.user-email {
  font-size: 12px;
  color: var(--sidebar-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}
.user-org {
  font-size: 10px;
  color: var(--sidebar-text);
  opacity: 0.45;
  margin-top: 1px;
}
.logout-btn {
  background: none;
  border: none;
  color: var(--sidebar-text);
  opacity: 0.4;
  cursor: pointer;
  padding: 5px;
  border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.15s, background 0.15s;
  flex-shrink: 0;
}
.logout-btn:hover { opacity: 1; background: rgba(255,255,255,0.1); }

/* ── 主内容 ── */
.content {
  margin-left: 220px;
  flex: 1;
  min-height: 100vh;
  padding: 32px 36px;
  background: var(--body-bg);
  transition: margin-left 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}
.content--wide {
  margin-left: 56px;
}
</style>
