import { createRouter, createWebHashHistory } from 'vue-router'

const LoginPage = () => import('../pages/LoginPage.vue')
const SystemsPage = () => import('../features/systems/pages/SystemsPage.vue')
const SystemDetailPage = () => import('../features/system-detail/pages/SystemDetailPage.vue')
const DocsPage = () => import('../pages/DocsPage.vue')
const MessagesPage = () => import('../pages/MessagesPage.vue')
const AuditPage = () => import('../pages/AuditPage.vue')
const EfficiencyPage = () => import('../pages/EfficiencyPage.vue')

const routes = [
  { path: '/', redirect: '/systems' },
  { path: '/login', component: LoginPage, meta: { public: true } },
  { path: '/systems', component: SystemsPage },
  { path: '/systems/:id', component: SystemDetailPage, props: true },
  { path: '/messages', component: MessagesPage },
  { path: '/audit', component: AuditPage },
  { path: '/efficiency', component: EfficiencyPage },
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
