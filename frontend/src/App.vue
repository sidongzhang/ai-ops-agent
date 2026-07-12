<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from './api'
import { useAuthStore } from './stores/auth'
import { useThemeStore, THEMES } from './stores/theme'

const auth = useAuthStore()
const themeStore = useThemeStore()
const route = useRoute()
const router = useRouter()

const showShell = computed(() => route.path !== '/login')
const userInitial = computed(() => auth.user?.email?.[0]?.toUpperCase() ?? 'U')
const unreadMessages = ref(0)
const pendingApprovals = ref(0)
let unreadTimer = null

const navSections = [
  {
    key: 'access',
    label: '接入',
    items: [
      { to: '/systems', label: '监控系统', title: '监控系统', icon: 'systems' },
      { to: '/docs', label: '接入指南', title: '接入指南', icon: 'docs' },
    ],
  },
  {
    key: 'operations',
    label: '运行',
    items: [
      { to: '/monitoring', label: '巡检中心', title: '巡检中心', icon: 'monitoring' },
      { to: '/incidents', label: '事故中心', title: '事故中心', icon: 'incidents' },
      { to: '/messages', label: '消息中心', title: '消息中心', icon: 'messages', badge: unreadMessages },
      { to: '/approvals', label: '审批中心', title: '待审批', icon: 'approvals', badge: pendingApprovals },
    ],
  },
  {
    key: 'insight',
    label: '分析',
    items: [
      { to: '/efficiency', label: '效率分析', title: '效率分析', icon: 'efficiency' },
      { to: '/audit', label: '审计记录', title: '审计记录', icon: 'audit' },
    ],
  },
]

function iconPath(icon) {
  const icons = {
    systems: { box: '<rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8M12 17v4" />' },
    docs: { box: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /><polyline points="10 9 9 9 8 9" />' },
    monitoring: { box: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4" /><circle cx="12" cy="12" r="5" />' },
    incidents: { box: '<path d="M12 9v4" /><path d="M12 17h.01" /><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.72 3h16.92a2 2 0 0 0 1.72-3L13.71 3.86a2 2 0 0 0-3.42 0z" />' },
    messages: { box: '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" /><line x1="8" y1="9" x2="16" y2="9" /><line x1="8" y1="13" x2="14" y2="13" />' },
    approvals: { box: '<rect x="4" y="3" width="16" height="18" rx="2" /><path d="M9 7h6M9 11h6M9 15h4" />' },
    efficiency: { box: '<path d="M4 19V9M10 19V5M16 19v-7M22 19V2" /><path d="M2 19h22" />' },
    audit: { box: '<path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />' },
  }
  return icons[icon]?.box || ''
}

async function loadUnreadMessages() {
  if (!showShell.value || !localStorage.getItem('token')) {
    unreadMessages.value = 0
    pendingApprovals.value = 0
    return
  }
  try {
    const [{ data: messageData }, { data: pendingData }] = await Promise.all([
      api.get('/messages/unread-count'),
      api.get('/workflows/pending', { params: { limit: 100 } }),
    ])
    unreadMessages.value = messageData.count || 0
    pendingApprovals.value = pendingData.total ?? (pendingData.items || pendingData).length ?? 0
  } catch {
    unreadMessages.value = 0
    pendingApprovals.value = 0
  }
}

// 桌面端：收起/展开
const collapsed = ref(localStorage.getItem('aiops_sidebar') === '1')
function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('aiops_sidebar', collapsed.value ? '1' : '0')
}

// 移动端：抽屉开关
const isMobile = ref(window.innerWidth < 768)
const drawerOpen = ref(false)
function onResize() { isMobile.value = window.innerWidth < 768 }
onMounted(() => {
  if (auth.token) {
    auth.fetchMe().catch(() => {})
  }
  window.addEventListener('resize', onResize)
  window.addEventListener('aiops:messages-changed', loadUnreadMessages)
  window.addEventListener('aiops:approvals-changed', loadUnreadMessages)
  loadUnreadMessages()
  unreadTimer = window.setInterval(loadUnreadMessages, 30000)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('aiops:messages-changed', loadUnreadMessages)
  window.removeEventListener('aiops:approvals-changed', loadUnreadMessages)
  if (unreadTimer) window.clearInterval(unreadTimer)
})
function openDrawer() { drawerOpen.value = true }
function closeDrawer() { drawerOpen.value = false }

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <a-config-provider :theme="themeStore.antTheme">
    <router-view v-if="!showShell" />

    <div v-else class="shell">

      <!-- 移动端遮罩 -->
      <div v-if="isMobile && drawerOpen" class="sidebar-backdrop" @click="closeDrawer" />

      <!-- ── Sidebar ── -->
      <aside :class="['sidebar',
        isMobile ? (drawerOpen ? 'sidebar--drawer-open' : 'sidebar--drawer') : (collapsed ? 'sidebar--collapsed' : '')
      ]">

        <!-- Logo + Toggle -->
        <div class="logo-row">
          <div class="logo-mark" :style="collapsed ? 'cursor:pointer' : ''"
               :title="collapsed ? '展开侧边栏' : ''"
               @click="collapsed ? toggleSidebar() : undefined">运</div>
          <span v-show="!collapsed" class="logo-name">智能运维平台</span>
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
          <section v-for="section in navSections" :key="section.key" class="nav-section">
            <div v-show="!collapsed || isMobile" class="nav-section-label">{{ section.label }}</div>
            <a-tooltip
              v-for="item in section.items"
              :key="item.to"
              :title="(collapsed && !isMobile) ? item.title : ''"
              placement="right"
              :mouse-enter-delay="0.1"
            >
              <router-link :to="item.to" class="nav-link" active-class="nav-link--active" @click="closeDrawer">
                <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" v-html="iconPath(item.icon)" />
                <span v-show="!collapsed || isMobile" class="nav-label">{{ item.label }}</span>
                <span v-if="item.badge?.value" class="nav-badge">{{ item.badge.value > 99 ? '99+' : item.badge.value }}</span>
              </router-link>
            </a-tooltip>
          </section>
        </nav>

        <div style="flex:1" />

        <!-- Footer -->
        <div class="sidebar-footer">

          <div v-show="!collapsed || isMobile" class="theme-row">
            <span class="theme-label">主题</span>
            <div class="theme-dots">
              <button
                v-for="(item, themeKey) in THEMES"
                :key="themeKey"
                class="theme-dot"
                :class="{ active: themeStore.key === themeKey }"
                :style="{ background: item.colorPrimary }"
                :title="item.name"
                @click="themeStore.setTheme(themeKey)"
              />
            </div>
          </div>

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
      <main :class="['content', { 'content--wide': collapsed && !isMobile, 'content--mobile': isMobile }]">
        <!-- 移动端顶栏 -->
        <div v-if="isMobile" class="mobile-topbar">
          <button class="hamburger" @click="openDrawer">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" width="20" height="20">
              <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <span class="mobile-title">智能运维平台</span>
        </div>
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
  background: var(--body-bg, #F4F9FF);
  color: var(--text, #0F172A);
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
  width: 214px;
  min-height: 100vh;
  position: fixed;
  left: 0; top: 0; bottom: 0;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  box-shadow: 4px 0 24px rgba(15, 23, 42, 0.04);
  display: flex;
  flex-direction: column;
  padding-bottom: 16px;
  z-index: 100;
  overflow: hidden;
  transition: width 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}
.sidebar--collapsed {
  width: 58px;
}

/* Logo 行 */
.logo-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 14px 14px;
  border-bottom: 1px solid var(--sidebar-border);
  margin-bottom: 10px;
  position: relative;
  min-height: 62px;
}
.logo-mark {
  width: 30px; height: 30px;
  background: var(--primary-bg);
  border: 1px solid color-mix(in srgb, var(--primary) 22%, transparent);
  border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px;
  color: var(--primary);
  font-weight: 700;
  flex-shrink: 0;
}
.logo-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  flex: 1;
}
.toggle-btn {
  background: none;
  border: none;
  color: var(--sidebar-text);
  opacity: 0.72;
  cursor: pointer;
  padding: 4px;
  border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.15s, background 0.15s;
  flex-shrink: 0;
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
}
.toggle-btn:hover { opacity: 1; background: var(--sidebar-hover); }

/* Nav */
.nav {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 8px;
}
.nav-section {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-bottom: 8px;
  border-bottom: 1px solid color-mix(in srgb, var(--sidebar-border) 76%, transparent);
}
.nav-section:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.nav-section-label {
  padding: 8px 10px 4px;
  font-size: 11px;
  line-height: 1;
  color: var(--text-subtle);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 10px;
  color: var(--sidebar-text);
  text-decoration: none;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, transform 0.15s;
  overflow: hidden;
  position: relative;
}
.nav-link:hover { background: var(--sidebar-hover); color: var(--text); transform: translateX(1px); }
.nav-link--active {
  background: var(--sidebar-active-bg) !important;
  color: var(--sidebar-active) !important;
  font-weight: 600;
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary) 14%, transparent);
}
.nav-icon { width: 17px; height: 17px; flex-shrink: 0; }
.nav-label { white-space: nowrap; }
.nav-badge {
  margin-left: auto;
  min-width: 18px;
  height: 18px;
  border-radius: 999px;
  background: #ef4444;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 5px;
  line-height: 1;
  flex-shrink: 0;
}
.sidebar--collapsed .nav-badge {
  position: absolute;
  right: 5px;
  top: 4px;
  min-width: 15px;
  height: 15px;
  font-size: 9px;
  padding: 0 4px;
}

/* Footer */
.sidebar-footer {
  padding: 12px 10px 6px;
  border-top: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.footer-divider {
  height: 1px;
  background: var(--sidebar-border);
  margin: 0;
}
.theme-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 6px 2px;
}
.theme-label {
  font-size: 11px;
  color: var(--text-subtle);
  font-weight: 600;
  letter-spacing: 0.04em;
}
.theme-dots {
  display: flex;
  gap: 6px;
}
.theme-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid transparent;
  cursor: pointer;
  padding: 0;
  transition: transform 0.15s, border-color 0.15s;
}
.theme-dot:hover { transform: scale(1.12); }
.theme-dot.active {
  border-color: var(--text);
  transform: scale(1.08);
}
.sidebar--collapsed .theme-row {
  justify-content: center;
  padding-bottom: 4px;
}
.sidebar--collapsed .theme-label { display: none; }

/* 用户行 */
.user-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 4px;
}
.user-avatar {
  width: 30px; height: 30px;
  border-radius: 9px;
  background: var(--primary-bg);
  border: 1px solid color-mix(in srgb, var(--primary) 22%, transparent);
  color: var(--primary);
  font-size: 12px;
  font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  cursor: default;
}
.user-meta { flex: 1; min-width: 0; }
.user-email {
  font-size: 12px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}
.user-org {
  font-size: 10px;
  color: var(--text-subtle);
  margin-top: 1px;
}
.logout-btn {
  background: none;
  border: none;
  color: var(--sidebar-text);
  opacity: 0.65;
  cursor: pointer;
  padding: 5px;
  border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.15s, background 0.15s;
  flex-shrink: 0;
}
.logout-btn:hover { opacity: 1; background: var(--sidebar-hover); }

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

/* ── 移动端 ── */
.sidebar--drawer {
  transform: translateX(-100%);
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  width: 220px !important;
  z-index: 300;
}
.sidebar--drawer-open {
  transform: translateX(0);
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  width: 220px !important;
  z-index: 300;
}
.sidebar-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.28);
  z-index: 299;
}
.content--mobile {
  margin-left: 0 !important;
  padding: 0 16px 24px;
}
.mobile-topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 0 16px;
  border-bottom: 1px solid var(--sidebar-border, #eee);
  margin-bottom: 20px;
}
.hamburger {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text);
  display: flex;
  align-items: center;
  padding: 4px;
  border-radius: 6px;
}
.hamburger:hover { background: rgba(0,0,0,0.06); }
.mobile-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
}

@media (max-width: 767px) {
  .content {
    padding: 0 16px 24px;
  }
}
</style>
