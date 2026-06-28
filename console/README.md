# 智能运维平台 · 控制台 (Vue SPA)

多租户 AIOps 平台的前端控制台。**Vue 3 + Vite + Ant Design Vue + Pinia**,连 `controlplane/` 的 FastAPI 接口。

## 三个页面

| 路由 | 页面 | 功能 |
|---|---|---|
| `/login` | 登录/注册 | 注册即创建组织空间,JWT 登录 |
| `/systems` | 系统列表 | 列出本租户系统;弹窗注册系统(动态服务表单,按连接器填字段) |
| `/systems/:id` | 系统详情 | 连接器健康探活 + 🤖 AI 智能诊断对话 |

## 本地运行(前后端一起)

```bash
# 终端 1：起控制面后端
cd controlplane
.venv/bin/uvicorn app.main:app --reload --port 8000

# 终端 2：起前端
cd console
npm install        # 首次
npm run dev        # http://localhost:5173
```

打开 http://localhost:5173 → 注册一个账号 → 注册一套系统(例如 http 连接器 + `https://example.com`)→ 进详情页探活 + 问 AI。

> 后端地址默认 `http://localhost:8000`,可用 `VITE_API_BASE` 覆盖(生产指向网关)。后端已开放 CORS。

## 构建

```bash
npm run build      # 产物在 dist/
```

## 技术要点

- `src/api.js`:axios 实例,请求拦截器自动带 JWT,401 自动跳登录
- `src/stores/auth.js`:Pinia 鉴权 store,token 存 localStorage
- `src/router`:hash 路由 + 守卫(未登录跳 `/login`)
- 注册系统表单按连接器(http/tcp/ssh/prometheus/local)动态渲染对应字段
