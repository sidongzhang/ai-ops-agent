import { createRouter, createWebHashHistory } from 'vue-router'

const LoginPage = () => import('../pages/LoginPage.vue')
const SystemsPage = () => import('../features/systems/pages/SystemsPage.vue')
const SystemDetailPage = () => import('../features/system-detail/pages/SystemDetailPage.vue')
const SystemKnowledgePage = () => import('../features/system-detail/pages/SystemKnowledgePage.vue')
const DocsPage = () => import('../pages/DocsPage.vue')
const MessagesPage = () => import('../pages/MessagesPage.vue')
const IncidentsPage = () => import('../pages/IncidentsPage.vue')
const ApprovalsPage = () => import('../pages/ApprovalsPage.vue')
const AuditPage = () => import('../pages/AuditPage.vue')
const EfficiencyPage = () => import('../pages/EfficiencyPage.vue')
const MonitoringCenterPage = () => import('../pages/MonitoringCenterPage.vue')

const routes = [
  { path: '/', redirect: '/systems' },
  { path: '/login', component: LoginPage, meta: { public: true } },
  { path: '/systems', component: SystemsPage },
  { path: '/systems/:id', component: SystemDetailPage, props: true },
  { path: '/systems/:id/knowledge', component: SystemKnowledgePage, props: true },
  { path: '/messages', component: MessagesPage, props: { defaultView: 'messages' } },
  { path: '/incidents', component: IncidentsPage },
  { path: '/approvals', component: ApprovalsPage },
  { path: '/audit', component: AuditPage },
  { path: '/efficiency', component: EfficiencyPage },
  { path: '/monitoring', component: MonitoringCenterPage },
  { path: '/docs', component: DocsPage },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 路由守卫：未登录访问受保护页 → 跳登录
router.beforeEach((to) => {
  const authed = !!localStorage.getItem('token')
  if (!to.meta.public && !authed) return '/login'
  if (to.path === '/login' && authed) return '/systems'
  return true
})

export default router
