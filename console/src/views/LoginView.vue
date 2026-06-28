<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const activeTab = ref('login')
const loading = ref(false)

const loginForm = reactive({ email: '', password: '' })
const regForm = reactive({ email: '', password: '', orgName: '' })

async function doLogin() {
  if (!loginForm.email || !loginForm.password) return message.warning('请填写邮箱和密码')
  loading.value = true
  try {
    await auth.login(loginForm.email, loginForm.password)
    message.success('登录成功')
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
    message.success('注册成功，已创建你的组织空间')
    router.push('/systems')
  } catch (e) {
    message.error(e?.response?.data?.detail || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div style="min-height: 100vh; display: flex; align-items: center; justify-content: center; background: #f0f2f5">
    <a-card style="width: 400px" title="🛠️ 智能运维平台">
      <a-tabs v-model:activeKey="activeTab" centered>
        <a-tab-pane key="login" tab="登录">
          <a-form layout="vertical" @submit.prevent="doLogin">
            <a-form-item label="邮箱">
              <a-input v-model:value="loginForm.email" placeholder="you@company.com" />
            </a-form-item>
            <a-form-item label="密码">
              <a-input-password v-model:value="loginForm.password" @pressEnter="doLogin" />
            </a-form-item>
            <a-button type="primary" block :loading="loading" @click="doLogin">登录</a-button>
          </a-form>
        </a-tab-pane>

        <a-tab-pane key="register" tab="注册">
          <a-form layout="vertical" @submit.prevent="doRegister">
            <a-form-item label="组织名称">
              <a-input v-model:value="regForm.orgName" placeholder="你的公司/团队" />
            </a-form-item>
            <a-form-item label="邮箱">
              <a-input v-model:value="regForm.email" placeholder="you@company.com" />
            </a-form-item>
            <a-form-item label="密码">
              <a-input-password v-model:value="regForm.password" />
            </a-form-item>
            <a-button type="primary" block :loading="loading" @click="doRegister">
              注册并创建空间
            </a-button>
          </a-form>
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>
