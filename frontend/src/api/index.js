import axios from 'axios'

// 生产走同域相对路径；开发默认直连本地控制面。
const baseURL =
  import.meta.env.VITE_API_BASE?.trim() ||
  (import.meta.env.PROD ? '' : 'http://localhost:8000')

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
