# AI 智能运维平台 (AIOps Platform)

多租户 SaaS 智能运维平台：用户把自己系统的服务信息注册进来，平台提供**智能监测 + 告警 + AI 诊断**，每个组织有独立隔离的空间。

> 核心理念：让 AI 充当 7×24 在线的运维工程师，完成「发现问题 → 分析根因 → 处置建议 → 汇报」闭环；平台把这套能力泛化给任意被注册的系统。

---

## 整体架构

```
                    我们托管的 SaaS（控制面）
┌──────────────────────────────────────────────────────────┐
│  frontend/  Vue3 SPA  ──REST──>  backend/  FastAPI        │
│  （登录 / 注册系统 / AI 对话）      ├─ JWT 鉴权 + org 行级隔离   │
│                                    ├─ 系统 / 服务注册 CRUD     │
│                                    ├─ 连接器探活               │
│                                    └─ Pydantic AI 诊断        │
└──────────────────────┬───────────────────────────────────┘
         出站 HTTPS（客户侧主动连出，平台永不入站）
     ┌────────────────┴────────────────┐
客户A 内网 [collector/]         客户B 内网 [collector/]
  └ 本地跑 connectors/ 探测回传    └ 本地跑 connectors/ 探测回传
```

---

## 仓库结构

```
ai-ops-agent/
├── backend/            # 独立服务：SaaS 控制面（FastAPI + SQLModel + Pydantic AI）
│   ├── app/
│   │   ├── core/       # 基础设施：config · database · deps · security
│   │   ├── models/     # SQLModel 数据表定义
│   │   ├── schemas/    # Pydantic API 请求/响应模型
│   │   ├── api/        # HTTP 路由层（auth · systems · health · diagnose · ...）
│   │   ├── services/   # 业务服务层（descriptor · websocket）
│   │   ├── workers/    # 异步任务层（Celery · tasks）
│   │   ├── agent/      # AI Agent 层（diagnose · workflow）
│   │   └── main.py     # FastAPI 入口
│   ├── migrations/     # Alembic 迁移脚本
│   ├── tests/          # 测试（预留）
│   └── pyproject.toml
├── frontend/           # 独立服务：SaaS 控制台（Vue 3 + Vite + Ant Design Vue）
│   └── src/
│       ├── api/        # API 客户端封装
│       ├── router/     # Vue Router
│       ├── stores/     # Pinia 状态管理
│       └── views/      # 页面组件
├── collector/          # 独立服务：可下载采集器（装在客户网络内，出站连平台）
│   ├── run.py          # 采集器主进程入口
│   ├── ws_client.py    # WebSocket 下行通道
│   └── tests/          # 测试（预留）
├── shared/             # 共享库（被 backend 和 collector 同时引用，不独立运行）
│   ├── connectors/     # 连接器 SDK（local / http / tcp / ssh / prometheus / k8s）
│   └── tests/          # 测试（预留）
└── deploy/             # 部署配置（docker-compose）
```

---

## 快速开始

### 1. 后端（Python 3.12）

```bash
cd backend
uv venv --python python3.12 .venv
uv pip install --python .venv -e .
.venv/bin/alembic upgrade head          # 建表（dev 默认 SQLite）
.venv/bin/uvicorn app.main:app --reload --port 8000
```

打开 http://localhost:8000/docs 查看交互式 API 文档。

### 2. 前端（Node）

```bash
cd frontend
npm install
npm run dev                             # http://localhost:5173
```

注册账号（即创建组织空间）→ 注册一套系统 → 健康探活 + AI 诊断。

### 3. 切换 PostgreSQL（生产）

```bash
# 启动 Postgres
docker compose -f deploy/docker-compose.yml up -d

# 配置连接串
export DATABASE_URL=postgresql+psycopg2://ops:opspass@localhost:5432/controlplane

# 运行迁移
cd backend && .venv/bin/alembic upgrade head
```

### 4. 采集器（触达客户私有内网）

在控制台为某系统创建采集器拿到密钥，在客户网络内的机器上：

```bash
PLATFORM_URL=https://your-platform.example.com \
COLLECTOR_KEY=<创建时返回的一次性密钥> \
python collector/run.py          # --once 跑一轮即退出
```

---

## 技术栈

| 层 | 技术 |
|---|---|
| 控制面后端 | FastAPI + Pydantic + SQLModel（dev SQLite / 生产 PostgreSQL） |
| 数据库迁移 | Alembic（`migrations/`，autogenerate from SQLModel metadata） |
| 鉴权 / 多租户 | JWT（bcrypt）+ 每表 `org_id` 行级隔离 |
| Agent 编排 | **Pydantic AI**（typed tools + RunContext 依赖注入 + 动态 system prompt） |
| LLM | DeepSeek（`deepseek-chat`，OpenAI 兼容）；硬核诊断自动升档高级模型 |
| 前端 | Vue 3 + Vite + Ant Design Vue + Pinia |
| 连接器 | http / tcp / ssh / prometheus / k8s / local |
| 采集器 | Python 轻量脚本，复用 `connectors/`，仅依赖 `requests` |
| 定时巡检 | Celery 5 + Redis（Broker）；Beat 调度，Worker 并发探活，Webhook 告警 |
| 控制面 DB | PostgreSQL 16（生产）/ SQLite（dev） |

---

## 路线图

```
✅ connectors/：local / http / tcp / ssh / prometheus / k8s 六种只读连接器
✅ backend/：FastAPI + SQLModel + Pydantic AI，多租户，JWT 鉴权
✅ frontend/：Vue3 三屏（登录 / 系统列表 / 详情+AI诊断），已联调
✅ collector/：出站探测上报，健康路径已打通
✅ Alembic 迁移：PostgreSQL 生产支持，首个 migration 已生成
✅ Celery 定时巡检：Beat 每 60s 发布任务，Worker 并发探活，边沿触发 Webhook 告警
✅ 采集器下行通道（WebSocket）：平台通过 POST /systems/{id}/collector/exec 实时下发命令
✅ 凭据加密存储：Fernet 字段级加密落库，API 响应掩码，连接器自动解密
✅ Langfuse 可观测性：每次 AI 诊断追踪 token / 耗时 / 模型
✅ 模型路由：关键词检测自动升档至高级模型
✅ LangGraph 审批闸：AI 诊断+提案 → 人工审批 → 自动执行
✅ Prometheus + Redis 实时指标面板（30s 自动刷新）
✅ 对话历史持久化（localStorage，🧹 一键清空）
```
