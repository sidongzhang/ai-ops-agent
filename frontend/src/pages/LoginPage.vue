<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useAuthStore } from '../stores/auth'
import { useThemeStore, THEMES } from '../stores/theme'

const auth = useAuthStore()
const themeStore = useThemeStore()
const router = useRouter()
const tab = ref('login')
const loading = ref(false)

const loginForm = reactive({ email: '', password: '' })
const regForm = reactive({ email: '', password: '', orgName: '' })

async function doLogin() {
  if (!loginForm.email || !loginForm.password) return message.warning('请填写邮箱和密码')
  loading.value = true
  try {
    await auth.login(loginForm.email, loginForm.password)
    router.push('/systems')
  } catch (e) {
    message.error(e?.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  if (!regForm.email || !regForm.password || !regForm.orgName)
    return message.warning('请填写完整信息')
  loading.value = true
  try {
    await auth.register(regForm.email, regForm.password, regForm.orgName)
    router.push('/systems')
  } catch (e) {
    message.error(e?.response?.data?.detail || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <a-config-provider :theme="themeStore.antTheme">
    <div class="login-bg">

      <!-- 右上角主题切换 -->
      <div class="theme-bar">
        <button
          v-for="(t, k) in THEMES" :key="k"
          class="tdot"
          :class="{ active: themeStore.key === k }"
          :style="{ background: t.colorPrimary }"
          :title="t.name"
          @click="themeStore.setTheme(k)"
        />
      </div>

      <!-- 卡片 -->
      <div class="card">
        <!-- 品牌区 -->
        <div class="brand">
          <div class="brand-icon">⚡</div>
          <h1 class="brand-title">AIOps Platform</h1>
          <p class="brand-sub">智能运维 · 全局洞察 · AI 驱动</p>
        </div>

        <!-- Tabs -->
        <a-tabs v-model:activeKey="tab" centered class="auth-tabs">

          <!-- 登录 -->
          <a-tab-pane key="login" tab="登录">
            <a-form layout="vertical" class="form" @submit.prevent="doLogin">
              <a-form-item label="邮箱">
                <a-input
                  v-model:value="loginForm.email"
                  placeholder="you@company.com"
                  size="large"
                  autocomplete="email"
                />
              </a-form-item>
              <a-form-item label="密码">
                <a-input-password
                  v-model:value="loginForm.password"
                  placeholder="••••••••"
                  size="large"
                  @press-enter="doLogin"
                />
              </a-form-item>
              <a-button
                type="primary" block size="large"
                :loading="loading" class="submit-btn"
                @click="doLogin"
              >
                登录
              </a-button>
            </a-form>
          </a-tab-pane>

          <!-- 注册 -->
          <a-tab-pane key="register" tab="注册">
            <a-form layout="vertical" class="form" @submit.prevent="doRegister">
              <a-form-item label="组织 / 团队名称">
                <a-input
                  v-model:value="regForm.orgName"
                  placeholder="我的公司"
                  size="large"
                />
              </a-form-item>
              <a-form-item label="邮箱">
                <a-input
                  v-model:value="regForm.email"
                  placeholder="you@company.com"
                  size="large"
                />
              </a-form-item>
              <a-form-item label="密码">
                <a-input-password
                  v-model:value="regForm.password"
                  placeholder="••••••••"
                  size="large"
                />
              </a-form-item>
              <a-button
                type="primary" block size="large"
                :loading="loading" class="submit-btn"
                @click="doRegister"
              >
                注册并创建空间
              </a-button>
            </a-form>
          </a-tab-pane>

        </a-tabs>

        <!-- 底部说明 -->
        <p class="footer-tip">注册即创建独立的组织空间，数据完全隔离</p>
      </div>
    </div>
  </a-config-provider>
</template>

<style scoped>
.login-bg {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--body-bg, #FAFAF8);
  position: relative;
  /* 微纹理：细格网 */
  background-image: radial-gradient(circle, var(--border-color, #E7E5E4) 1px, transparent 1px);
  background-size: 28px 28px;
}

/* 主题切换条 */
.theme-bar {
  position: fixed;
  top: 20px; right: 24px;
  display: flex;
  gap: 8px;
  z-index: 10;
}
.tdot {
  width: 18px; height: 18px;
  border-radius: 50%;
  border: 2px solid transparent;
  cursor: pointer;
  transition: transform 0.15s, border-color 0.15s;
  padding: 0;
}
.tdot:hover { transform: scale(1.25); }
.tdot.active {
  border-color: var(--text, #1C1917);
  transform: scale(1.15);
}

/* 卡片 */
.card {
  width: 400px;
  background: var(--card-bg, #fff);
  border: 1px solid var(--border-color, #E7E5E4);
  border-radius: 16px;
  padding: 36px 36px 24px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.07), 0 1px 4px rgba(0,0,0,0.04);
}

/* 品牌区 */
.brand {
  text-align: center;
  margin-bottom: 28px;
}
.brand-icon {
  width: 52px; height: 52px;
  background: var(--primary, #D97706);
  border-radius: 14px;
  display: flex; align-items: center; justify-content: center;
  font-size: 26px;
  margin: 0 auto 14px;
  box-shadow: 0 4px 12px color-mix(in srgb, var(--primary, #D97706) 30%, transparent);
}
.brand-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--text, #1C1917);
  letter-spacing: -0.01em;
  margin-bottom: 6px;
}
.brand-sub {
  font-size: 13px;
  color: var(--text-subtle, #78716C);
}

/* 表单 */
.form {
  padding-top: 8px;
}
.submit-btn {
  margin-top: 6px;
  height: 42px;
  font-size: 15px;
  font-weight: 600;
  border-radius: 8px;
}

/* Tabs 微调 */
.auth-tabs :deep(.ant-tabs-tab) {
  font-size: 14px;
  font-weight: 500;
}

/* 底部提示 */
.footer-tip {
  text-align: center;
  font-size: 12px;
  color: var(--text-subtle, #78716C);
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border-color, #E7E5E4);
}
</style>
