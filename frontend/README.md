# 智能运维平台 · 前端控制台

Vue 3 + Vite + Ant Design Vue + Pinia 的多租户 AIOps 控制台，对接仓库内 `backend/` 的 FastAPI 接口。

## 路由

| 路由 | 页面 | 说明 |
|---|---|---|
| `/login` | 登录页 | 注册 / 登录，写入 JWT |
| `/systems` | 系统列表 | 查看系统、注册系统、配置服务预设 |
| `/systems/:id` | 系统详情 | 健康概览、指标监控、AI 诊断、通知配置 |

## 本地运行

```bash
# 终端 1：启动后端
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000

# 终端 2：启动前端
cd frontend
npm install
npm run dev
```

默认访问 `http://localhost:5173`。

默认 API 地址是 `http://localhost:8000`，可通过环境变量覆盖：

```bash
VITE_API_BASE=http://localhost:8000 npm run dev
```

## 构建

```bash
npm run build
```

## 目录说明

- `src/api/`: axios 客户端与鉴权拦截器
- `src/features/`: 按业务域拆分的页面能力
- `src/pages/`: 顶层路由页面
- `src/router/`: 路由与守卫
- `src/stores/`: Pinia 全局状态

更细的结构约束见 [ARCHITECTURE.md](./ARCHITECTURE.md)。
