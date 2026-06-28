# AI 智能运维平台 (AIOps Platform)

多租户 SaaS 智能运维平台：用户把自己系统的服务信息注册进来，平台提供**智能监测 + 告警 + AI 诊断**，每个组织有独立隔离的空间。

> 核心理念：让 AI 充当 7×24 在线的运维工程师，完成「发现问题 → 分析根因 → 处置建议 → 汇报」闭环；平台把这套能力泛化给任意被注册的系统。

---

## 整体架构

```
                    我们托管的 SaaS（控制面）
┌──────────────────────────────────────────────────────────┐
│  console/  Vue3 SPA  ──REST──>  controlplane/  FastAPI    │
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

**「可下载软件」+ 「SaaS」是同一套架构的两半**：可下载的是 **Collector**（装在客户网络内，出站连平台）；SaaS 是**控制面平台**。平台永远不需要入站访问客户内网，也不保管客户高危凭据。

---

## 仓库结构

| 目录 | 角色 | 说明 |
|---|---|---|
| `connectors/` | 核心·连接器 | local / http / tcp / ssh / prometheus / k8s 六种探活能力，平台与采集器共用 |
| `controlplane/` | SaaS 控制面后端 | FastAPI + SQLModel + Pydantic AI + Alembic，多租户。见 `controlplane/README.md` |
| `console/` | SaaS 控制台前端 | Vue 3 + Vite + Ant Design Vue 三屏。见 `console/README.md` |
| `collector/` | 可下载采集器 | 出站连平台、本地探测上报。见 `collector/README.md` |
| `docker-compose.platform.yml` | 控制面基础设施 | PostgreSQL 16（控制面 DB） |

---

## 快速开始

### 1. 控制面后端（Python 3.12）

```bash
cd controlplane
uv venv --python python3.12 .venv
uv pip install --python .venv -e .
.venv/bin/alembic upgrade head          # 建表（dev 默认 SQLite）
.venv/bin/uvicorn app.main:app --reload --port 8000
```

打开 http://localhost:8000/docs 查看交互式 API 文档。

### 2. 控制台前端（Node）

```bash
cd console
npm install
npm run dev                             # http://localhost:5173
```

浏览器打开 `http://localhost:5173`：注册账号（即创建组织空间）→ 注册一套系统 → 健康探活 + AI 诊断。

### 3. 切换 PostgreSQL（生产）

```bash
# 启动 Postgres
docker compose -f docker-compose.platform.yml up -d

# 配置连接串（根 .env 或 shell 环境变量）
export DATABASE_URL=postgresql+psycopg2://ops:opspass@localhost:5432/controlplane

# 运行迁移
cd controlplane && .venv/bin/alembic upgrade head
```

### 4. 采集器（触达客户私有内网）

在控制台为某系统创建采集器拿到密钥，在客户网络内的机器上：

```bash
PLATFORM_URL=https://your-platform.example.com \
COLLECTOR_KEY=<创建时返回的一次性密钥> \
python collector/run.py          # --once 跑一轮即退出
```

之后该系统的健康面板会显示「采集器上报」的快照。详见 `collector/README.md`。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 控制面后端 | FastAPI + Pydantic + SQLModel（dev SQLite / 生产 PostgreSQL） |
| 数据库迁移 | Alembic（`migrations/`，autogenerate from SQLModel metadata） |
| 鉴权 / 多租户 | JWT（bcrypt）+ 每表 `org_id` 行级隔离 |
| Agent 编排 | **Pydantic AI**（typed tools + RunContext 依赖注入 + 动态 system prompt） |
| LLM | DeepSeek（`deepseek-chat`，OpenAI 兼容）；硬核诊断可切最新 Claude |
| 前端 | Vue 3 + Vite + Ant Design Vue + Pinia |
| 连接器 | http / tcp / ssh / prometheus / k8s / local |
| 采集器 | Python 轻量脚本，复用 `connectors/`，仅依赖 `requests` |
| 定时巡检 | Celery 5 + Redis（Broker）；Beat 调度，Worker 并发探活，Webhook 告警 |
| 控制面 DB | PostgreSQL 16（生产）/ SQLite（dev） |

---

## 路线图

```
✅ connectors/：local / http / tcp / ssh / prometheus / k8s 六种只读连接器
✅ controlplane/：FastAPI + SQLModel + Pydantic AI，多租户，JWT 鉴权
✅ console/：Vue3 三屏（登录 / 系统列表 / 详情+AI诊断），已联调
✅ collector/：出站探测上报，健康路径已打通
✅ Alembic 迁移：PostgreSQL 生产支持，首个 migration 已生成

✅ Celery 定时巡检：Beat 每 60s 发布任务，Worker 并发探活，边沿触发 Webhook 告警
⬜ 采集器下行通道（WebSocket）：平台向 Collector 下发命令（拉日志 / 远程动作）
✅ 凭据加密存储：Fernet 字段级加密落库，API 响应掩码，连接器自动解密
✅ Langfuse 可观测性：每次 AI 诊断追踪 token / 耗时 / 模型，LANGFUSE_PUBLIC_KEY 未配置时跳过
✅ 模型路由：关键词检测（P0/崩溃/宕机/数据丢失等）自动升档至高级模型（deepseek-reasoner 或任意 OpenAI 兼容）
⬜ LangGraph 审批闸 + 可恢复修复工作流
```
