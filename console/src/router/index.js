import { createRouter, createWebHashHistory } from 'vue-router'

import LoginView from '../views/LoginView.vue'
import SystemsView from '../views/SystemsView.vue'
import SystemDetailView from '../views/SystemDetailView.vue'

const routes = [
  { path: '/', redirect: '/systems' },
  { path: '/login', component: LoginView, meta: { public: true } },
  { path: '/systems', component: SystemsView },
  { path: '/systems/:id', component: SystemDetailView, props: true },
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
