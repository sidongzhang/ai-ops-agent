# AI 智能运维平台 (AIOps Platform)

多租户 SaaS 智能运维平台：用户把自己的服务注册进来，平台提供**智能监测、统一告警、模板化诊断、只读数据分析、审批修复和全程审计**，每个组织有独立隔离的空间。

> 核心理念：让 AI 充当 7×24 在线的运维工程师，完成「发现问题 → 分析根因 → 提出修复方案 → 人工审批 → 自动执行 → 汇报」完整闭环；平台把这套能力泛化给任意被注册的系统。

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

一套系统可以包含前端、Spring Boot、数据库、Redis、Kafka、Prometheus 等多个服务。远程系统注册时不要求平台直接访问对方内网：先登记服务，再在系统详情页创建采集器；采集器由对方网络主动出站连接平台，负责探活、拉日志和在审批后执行受限操作。
采集器创建后，控制台可直接下载带有本次密钥的采集包；对方解压即可运行，不需要手工拷贝整套仓库。
生产部署时请将前端构建变量 `VITE_COLLECTOR_PLATFORM_URL` 设置为平台对外访问地址（例如 `https://ops.example.com`），让远程采集器能够从客户网络连回平台。

### 3. 启动真实 Docker 基础服务

仓库根目录的 `docker-compose.yml` 会启动 MySQL、Redis、Kafka、Prometheus、Node Exporter 及两个 exporter。首次启动完成后，在平台中注册真实地址：

```bash
docker compose up -d
```

默认端口：MySQL `3306`、Redis `6379`、Kafka `9092`、Prometheus `9090`、Node Exporter `9100`。平台不会自动伪造业务任务或告警数据，页面内容来自真实探测、真实指标和外部系统接入。

### 4. 外部系统上传日志分析

外部系统不需要接入平台页面，只需保存平台生成的接口 Token，并上传日志文件：

```bash
curl -X POST http://localhost:8000/openapi/v1/log-analysis \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-System-Code: docker-real-local" \
  -F "file=@./log.json" \
  -F "request_id=log-001" \
  -F "question=请分析这份日志，给出故障原因和处理措施"
```

接口支持 UTF-8 的 JSON 或文本日志，单个文件最大 `2 MB`。平台会先返回已接收的 `message_id` 和处理状态，后台分析完成后把报告回写到消息中心。对方系统可以用同一个 `request_id` 查询结果：

```bash
curl http://localhost:8000/openapi/v1/messages/log-001 \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-System-Code: docker-real-local"
```

Token 只用于确认调用方身份和权限；原始日志不写入平台数据库或模型追踪，分析记录会进入审计日志。

### 5. 切换 PostgreSQL（生产）

```bash
# 启动 Postgres
docker compose -f deploy/docker-compose.yml up -d

# 配置连接串
export DATABASE_URL=postgresql+psycopg2://ops:opspass@localhost:5432/controlplane

# 运行迁移
cd backend && .venv/bin/alembic upgrade head
```

也可以直接启动完整的平台容器（控制面、Worker、Beat 和前端）：

```bash
export JWT_SECRET="请替换为随机长字符串"
export ENCRYPTION_KEY="$(python3 - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
)"
docker compose -f deploy/docker-compose.yml up -d --build
```

平台入口为 `http://localhost:8080`。公网部署时只需将该入口放到 HTTPS 网关后，
远程采集器即可通过同一个地址建立出站 HTTPS/WebSocket 连接；Worker 和 Beat 会持续执行巡检。

### 6. 采集器（触达客户私有内网）

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
| LLM | OpenAI 兼容接口；支持远程 API（DeepSeek/OpenAI 等）或本地部署（Ollama/vLLM/LM Studio） |
| 前端 | Vue 3 + Vite + Ant Design Vue + Pinia |
| 连接器 | http / tcp / ssh / prometheus / k8s / local |
| 采集器 | Python 轻量脚本，复用 `connectors/`，依赖 `requests` 与 `websockets` |
| 定时巡检 | Celery 5 + Redis（Broker）；Beat 调度，Worker 并发探活，Webhook 告警 |
| 控制面 DB | PostgreSQL 16（生产）/ SQLite（dev） |

---

## 路线图

```
✅ connectors/：local / http / tcp / ssh / prometheus / k8s 六种只读连接器
✅ backend/：FastAPI + SQLModel + Pydantic AI，多租户，JWT 鉴权
✅ frontend/：Vue3 控制台（系统、消息、审计、接入文档和系统详情），已联调
✅ collector/：出站探测上报，健康路径已打通
✅ Alembic 迁移：PostgreSQL 生产支持，首个 migration 已生成
✅ Celery 定时巡检：Beat 每 15s 触发，按系统独立周期执行，Worker 并发探活并触发站内/飞书/邮件告警
✅ 采集器下行通道（WebSocket）：平台通过 POST /systems/{id}/collector/exec 实时下发命令
✅ 凭据加密存储：Fernet 字段级加密落库，API 响应掩码，连接器自动解密
✅ Langfuse 可观测性：每次 AI 诊断追踪 token / 耗时 / 模型
✅ 模型路由：关键词检测自动升档至高级模型
✅ LangGraph 审批闸：AI 诊断+提案 → 人工审批 → 自动执行
✅ AI 辅助修复：诊断后申请修复 → 用户审批 → 平台执行 → 自动回查恢复结果
✅ Prometheus + Redis 实时指标面板（30s 自动刷新）
✅ 统一消息中心：告警、接入消息和报告统一入库，支持已读、确认、解决和渠道发送结果
✅ 开放接入：系统 Token、权限范围、过期/禁用、告警/健康/报告接口和请求去重
✅ 外部日志分析：业务系统通过 `multipart/form-data` 上传 `log.json`，平台返回带原因和处理措施的分析报告
✅ 通知渠道：网页内、飞书、邮件和 Webhook 可同时启用，支持测试、失败自动重试和消息中心手动重试
✅ 诊断模板：8 类排查模板，覆盖服务不可达、HTTP、Redis、Kafka、MySQL、性能、日志和整体巡检，可按系统启停
✅ 只读数据分析：查询限制、超时、返回行数限制和敏感字段脱敏
✅ 全链路审计：记录接入、消息处理、诊断模板、实际工具调用、审批、执行和回查结果
✅ 探针启用门槛：服务草稿、测试报告留存、修改后重新测试，后端强制测试通过才能进入监控
✅ Prometheus 指标校验：测试地址、PromQL、指标是否存在及当前状态，避免空指标进入正式监控
✅ 效率分析：按系统和周期查看告警解决率、确认/解决时间、诊断证据覆盖、通知成功率和拦截效果
✅ 任务卡住专项分析：只读统计卡住任务和状态分布，联合 Worker 健康与异常日志，并衔接审批修复和恢复回查
✅ RAG 知识库：API Embedding + JSON 向量存储，每次诊断自动追加 runbook，降级关键词检索
✅ 对话历史持久化（localStorage，🧹 一键清空）
✅ Cloudflare Tunnel 公网访问 + Caddy 反向代理（前端静态文件 + API 统一入口）
✅ 移动端响应式：侧边栏抽屉模式，辅助文档页自适应
```
