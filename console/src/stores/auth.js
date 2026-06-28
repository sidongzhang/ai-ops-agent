import { defineStore } from 'pinia'
import api from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: null,
  }),
  getters: {
    isAuthenticated: (s) => !!s.token,
  },
  actions: {
    _setToken(token) {
      this.token = token
      localStorage.setItem('token', token)
    },
    async login(email, password) {
      // /auth/login 走 OAuth2 表单：username + password
      const body = new URLSearchParams({ username: email, password })
      const { data } = await api.post('/auth/login', body, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      })
      this._setToken(data.access_token)
      await this.fetchMe()
    },
    async register(email, password, orgName) {
      const { data } = await api.post('/auth/register', {
        email,
        password,
        org_name: orgName,
      })
      this._setToken(data.access_token)
      await this.fetchMe()
    },
    async fetchMe() {
      const { data } = await api.get('/auth/me')
      this.user = data
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
    },
  },
})
