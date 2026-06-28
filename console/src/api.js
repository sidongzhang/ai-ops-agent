import axios from 'axios'

// 开发期直连控制面（控制面已开放 CORS）。生产用 VITE_API_BASE 指向网关。
const baseURL = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

const api = axios.create({ baseURL })

// 请求拦截器：自动带上 JWT
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截器：401 统一清登录态并跳登录
api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('token')
      if (location.hash !== '#/login') location.assign('#/login')
    }
    return Promise.reject(error)
  },
)

export default api
