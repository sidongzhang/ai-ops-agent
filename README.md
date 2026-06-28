# AI 智能运维平台 (AIOps Platform)

把「AI 运维 Agent」从一套**单机 Demo**演进成一个**多租户的智能运维平台**:
别人只要把自己系统的服务信息(端口/host/健康检查方式/日志来源等)注册进来,
平台就能为其提供**智能监测 + 告警 + AI 诊断**,且每个用户/组织有独立空间。

> 核心理念:让 AI 充当 7×24 在线的运维工程师,完成「发现问题 → 分析根因 → 处置/建议 → 汇报」闭环;
> 平台把这套能力**泛化**给任意被注册的系统,而非写死某一套。

---

## 整体架构

```
                          我们托管的 SaaS(控制面)
┌──────────────────────────────────────────────────────────────────┐
│  console/  Vue3 SPA  ──REST──>  controlplane/  FastAPI            │
│   (登录/注册系统/AI对话)            ├─ 鉴权(JWT) + 多租户(org 行级隔离) │
│                                    ├─ 系统注册(systems/services → DB) │
│                                    ├─ 监控(经 connectors/ 探活)        │
│                                    └─ AI 诊断(Pydantic AI,模型可切)    │
└───────────────────────────┬──────────────────────────────────────┘
            出站 HTTPS(客户侧主动连出,平台不入站、不存高危凭据)
        ┌───────────────────┴───────────────────┐
   客户A 内网 [collector/]                客户B 内网 [collector/]
     └ 本地跑 connectors/ 探测回传          └ 本地跑 connectors/ 探测回传
```

**「可下载软件」+「SaaS」是一套架构的两半**:可下载的是 **Collector**(装在客户网络内,出站连平台);
SaaS 是**控制面平台**。平台永远不需要入站访问客户内网,也不保管客户高危凭据。

---

## 仓库结构

| 目录 | 角色 | 说明 |
|---|---|---|
| `registry/` | 核心·服务注册表 | 把「被监控系统」外置为可注册的 YAML/DB 描述符 |
| `connectors/` | 核心·连接器 | local/http/tcp/ssh/prometheus/k8s 六种探活能力,**平台与采集器共用** |
| `agent/` | 核心·AI Agent | ReAct + 工具 + RAG 知识库 + 场景 Skill(单机 Demo 用) |
| `controlplane/` | **SaaS 控制面后端** | FastAPI + SQLModel + Pydantic AI,多租户。见 `controlplane/README.md` |
| `console/` | **SaaS 控制台前端** | Vue3 + Vite + Ant Design Vue 三屏。见 `console/README.md` |
| `collector/` | **可下载采集器** | 出站连平台、本地探测上报。见 `collector/README.md` |
| `business/` | Demo 业务系统 | producer/consumer/frontend(被监控对象的样例) |
| `feishu_bot/` | Demo 飞书入口 | Webhook + 卡片 + 定时巡检(多系统路由) |
| `scripts/` | Demo 运维脚本 | start/stop/status/inject_fault(故障注入) |
| `docker-compose.yml` | Demo 基础设施 | Kafka / MySQL / Redis / Prometheus / Node-Exporter |

---

## 快速开始

### A. SaaS 平台(控制面 + 控制台)

```bash
# 1) 控制面后端(Python 3.12 + uv)
cd controlplane
uv venv --python python3.12 .venv
uv pip install --python .venv fastapi "uvicorn[standard]" sqlmodel pydantic-settings \
  "python-jose[cryptography]" bcrypt python-multipart email-validator httpx "pydantic-ai-slim[openai]"
.venv/bin/uvicorn app.main:app --reload --port 8000      # /docs 看 Swagger

# 2) 控制台前端(Node)
cd ../console
npm install
npm run dev                                              # http://localhost:5173
```

浏览器打开 `http://localhost:5173`:注册账号(即创建组织空间)→ 注册一套系统(填服务的连接器与地址)
→ 进详情页**健康探活** + 向 **AI 诊断**提问。

### B. 采集器(触达客户私有内网)

在控制台为某系统创建采集器拿到密钥,在客户网络内的机器上:

```bash
PLATFORM_URL=https://你的平台 COLLECTOR_KEY=xxx python collector/run.py      # --once 跑一轮
```

之后该系统的健康面板会显示「采集器上报」的快照。详见 `collector/README.md`。

### C. 单机 Demo(原始演示:飞书机器人 + 故障注入)

```bash
colima start && ./scripts/start.sh          # 起 Kafka/MySQL/Redis/Prometheus + 业务服务
cd agent && python3 agent.py                # CLI 交互;或在飞书给机器人发消息
./scripts/inject_fault.sh producer          # 注入故障,再问 AI 排查
```

故障注入类型:`producer/consumer/frontend/all/kafka/mysql/db-table/bad-data/cpu/log-flood`。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 控制面后端 | FastAPI + Pydantic + SQLModel(dev SQLite / 生产 PostgreSQL) |
| 鉴权/多租户 | JWT(bcrypt)+ 每表 `org_id` 行级隔离 |
| Agent 编排 | **Pydantic AI**(typed tools + 依赖注入 + 动态 system prompt);Demo 侧为自实现 ReAct |
| LLM | DeepSeek(`deepseek-chat`,OpenAI 兼容);硬核诊断可切最新 Claude |
| 知识库 RAG | ChromaDB + sentence-transformers(按系统隔离) |
| 前端 | Vue 3 + Vite + Ant Design Vue + Pinia |
| 连接器 | http / tcp / ssh / prometheus / k8s / local(本机) |
| Demo 基础设施 | Kafka(KRaft) / MySQL 8 / Redis 7 / Prometheus + Node-Exporter |
| Demo 飞书 | Webhook 事件订阅 + 卡片 API + Cloudflare Tunnel |

---

## 路线图

```
✅ 核心资产:registry/ + connectors/ + agent(RAG/Skill)——配置驱动、泛化
✅ 控制面后端:controlplane/(FastAPI + SQLModel + Pydantic AI,多租户)
✅ 控制台前端:console/(Vue3 三屏,已联调)
✅ 采集器:collector/(出站探测上报,健康路径已打通)
⬜ 采集器下行通道(WebSocket/任务队列):支持按需拉日志 + 远程动作
⬜ Celery 定时巡检、Postgres + Alembic 迁移、凭据加密(KMS/Vault)
⬜ Langfuse 可观测性、模型按难度路由、审批闸 + 可恢复修复工作流(LangGraph)
```

> 设计与演进思路另见 `设计思路.md`。各子系统的详细说明见各自目录下的 README。
