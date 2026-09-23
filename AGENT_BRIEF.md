# AIOps 平台 · Agent 交接简报

> 本文档供外部 Agent 快速建立对本项目的完整认知，可直接作为上下文注入。
> 最后更新：2026-09-22

---

## 一、项目概览

**名称**：多租户智能运维平台（AIOps SaaS）  
**仓库根目录**：`/Users/zhangsidong/ai-ops-agent/`  
**目标**：让客户把自己的各种服务（MySQL/Redis/Kafka/API/...）注册进来，平台自动做健康探测、AI 诊断、飞书告警、人工审批-自动执行修复。

---

## 二、整体架构

```
Browser ──HTTPS──▶  frontend (Vue3 + Ant Design Vue 4)
                          │ HTTP/WS
                    backend (FastAPI, port 8000)
                      ├── Auth (JWT, 多租户 org 隔离)
                      ├── 系统/服务注册 CRUD + 探针草稿/测试后启用
                      ├── 健康探测（直连 or 采集器上报）
                      ├── AI 诊断（Pydantic AI + OpenAI 兼容 LLM + Playbook + RAG）
                      │     └── 知识库：pgvector 语义检索 + 关键词降级
                      ├── 审批工作流（LangGraph，AsyncPostgresSaver 持久化）
                      ├── 统一消息中心（站内/飞书/邮件/Webhook 并行 + 失败重试）
                      ├── 业务系统开放接入（Token + 告警/健康/日报上报 + 去重）
                      ├── 实时指标聚合（Prometheus/Redis/Kafka/MySQL）
                      ├── 事故(incidents)/审计(audit_logs)/效率分析
                      ├── 飞书 Webhook 接收（/feishu/webhook）
                      └── 采集器网关（WebSocket）

collector (Python 轻量程序，部署在客户内网)
  ├── 出站连平台 WebSocket 网关
  ├── 本地探活（复用 shared/connectors）
  └── 执行平台下发的指令（fetch_logs / health_check / search_logs 等）

shared/connectors/   ← 两端共用的连接器 SDK
  local / http / tcp / ssh / prometheus / k8s

持久层（2026-09-22 生产化改造后 dev/prod 统一）：
  Postgres 5432（pgvector/pgvector:pg16，含 pgvector 扩展）
    ├── 业务表（Alembic 迁移管理，启动时自动 upgrade head）
    ├── knowledge_chunks        # RAG 向量索引（HNSW 余弦）
    └── checkpoints 等          # LangGraph 审批状态（AsyncPostgresSaver）
  Redis 6379 —— Celery broker / 实时通道

基础设施（Docker 容器，已运行）：
  MySQL 3306 · Redis 6379 · Kafka 9092(外) / kafka:9092(内)
  Prometheus 9090 · Node-Exporter 9100
  kafka-exporter 9308 · mysqld-exporter 9104 · Ollama 11434（本地 LLM/Embedding）
  Cloudflare Tunnel → webhook.tiancaizhaozhao.dpdns.org → port 8000
```

---

## 三、目录结构

```
ai-ops-agent/
├── backend/                # FastAPI 控制面（SQLModel + Alembic + Postgres/pgvector）
│   ├── app/
│   │   ├── main.py         # 入口；lifespan: init_db(自动 alembic) + 审批 checkpointer 初始化
│   │   ├── core/
│   │   │   ├── config.py       # pydantic-settings，读 .env（LLM_*/EMBEDDING_DIM/…）
│   │   │   ├── database.py     # 引擎；PG: 启动自动 alembic upgrade，SQLite 兜底 create_all
│   │   │   ├── deps.py         # get_current_org_id 依赖注入
│   │   │   └── security.py     # hash/verify password, JWT, Fernet 字段加密
│   │   ├── models/         # SQLModel 表：auth/systems/messages/workflows/collectors/
│   │   │                   #   tokens/incidents/audit/notifications/diagnostics/common
│   │   ├── schemas/        # Pydantic 请求/响应模型（与 DB 分离）
│   │   ├── repositories/   # 数据访问层（audit/collectors/messages/systems/tokens/workflows）
│   │   ├── api/            # auth/systems/health/metrics/diagnose/messages/openapi/
│   │   │                   #   tokens/knowledge_base/incidents/audit/analytics/workflow/
│   │   │                   #   workflow_pending/collectors/collector_gateway/collector_exec/
│   │   │                   #   websocket/feishu_webhook
│   │   ├── agent/
│   │   │   ├── llm.py          # LLM_MODE(api/local) 统一端点 + embedding_endpoint
│   │   │   ├── diagnostics/    # Pydantic AI 诊断：runner/tools/skill_router/skills/
│   │   │   │                   #   tracing/knowledge(store: pgvector RAG + 关键词降级)
│   │   │   └── workflows/      # LangGraph 审批图：runner(AsyncPostgresSaver)/nodes/
│   │   │                       #   models/persistence/state
│   │   ├── services/
│   │   │   ├── diagnostics/    # 诊断服务编排：service/evidence/reports
│   │   │   ├── workflows/      # 审批：service/actions(能力目录)/execution/回查
│   │   │   ├── notifications/  # 多渠道：alerts/config/deliveries/email/feishu/webhook
│   │   │   ├── systems/ collectors/ incidents/ monitoring/ descriptors/ realtime/
│   │   │   ├── openapi.py      # 业务系统开放接入
│   │   │   ├── log_analysis.py · message_processing.py · messages.py
│   │   │   ├── data_analysis.py（只读 SQL 分析）· analytics.py（效率指标）
│   │   │   └── audit.py · tokens.py
│   │   └── workers/        # Celery：celery.py / tasks.py（定时巡检+告警）
│   ├── migrations/         # Alembic 迁移（15+，启动自动 upgrade head）
│   ├── scripts/            # migrate_sqlite_to_pg.py 等运维脚本
│   ├── archive/            # 历史 SQLite dev.db 备份
│   └── pyproject.toml
│
├── frontend/               # Vue 3 + Vite + Ant Design Vue 4（features/ 分模块）
├── shared/connectors/      # 前后端/采集器共用连接器 SDK
├── collector/              # 采集器（PyInstaller 可执行打包）
├── evals/                  # 诊断 Agent 离线评测（25 条用例/故障注入/消融/打分报告）
├── deploy/                 # platform-db(pgvector) + redis + api/worker/beat/web
├── docs/                   # 外部系统接入等文档
└── prometheus/             # 抓取配置
```

---

## 四、数据模型

| 表 | 关键字段 | 说明 |
|---|---|---|
| `orgs` / `users` | id, name / email, hashed_password, org_id, role | 租户与用户 |
| `systems` | org_id, key, name, notify(JSON), infra(JSON), last_health(JSON), restart_policy | 被监控系统 |
| `services` | system_id, name, connector, config(JSON), probe 状态 | 系统下的服务（草稿→测试通过→启用） |
| `collectors` | system_id, token_hash, last_seen | 客户内网采集器 |
| `action_workflows` | system_id, thread_id, status, proposed_action(JSON), 审批人/执行/回查字段 | 审批工作流 |
| `system_tokens` | system_id, scope, 过期/禁用 | 业务系统开放接入 Token |
| `system_messages` / `notification_deliveries` | 状态机(未读/已确认/已解决) / 渠道发送结果+重试 | 统一消息中心 |
| `incidents` / `audit_logs` | 事故聚合 / 全链路审计（诊断、审批、执行、通知） | 追溯与分析 |
| `diagnosis_reports` | 诊断报告持久化（含证据/知识引用/业务上下文） | 诊断留存 |
| `knowledge_chunks` | system_id, source, chunk_index, content, **embedding vector** | RAG 向量索引（pgvector HNSW） |
| `knowledge_index_meta` | system_id, fingerprint, chunk_count | 知识库索引指纹 |
| `checkpoints` 等 | LangGraph 官方表 | 审批工作流持久化状态 |

**租户隔离**：所有业务表带 org_id，`get_current_org_id()` 从 JWT 解析；知识库检索按 system_id 过滤（归属校验在 require_system）。

**敏感字段加密**：password/secret/token/key 等字段落库前 Fernet 加密（`enc:` 前缀），API 响应统一掩码 `"***"`。

---

## 五、后端 API 一览

### Auth
- `POST /auth/register` — 注册（创建 org + user）
- `POST /auth/login` — 登录，返回 Bearer JWT

### Systems
- `POST /systems` — 注册系统（含服务列表）
- `GET /systems` — 列出当前 org 所有系统
- `GET /systems/{id}` — 获取单个系统
- `POST /systems/{id}/services` — 追加服务
- `DELETE /systems/{id}/services/{svc_id}` — 删除服务
- `PUT /systems/{id}/notify` — 更新通知配置（feishu/webhook/none）
- `POST /systems/{id}/notify/test` — 发送测试通知

### Monitoring
- `GET /systems/{id}/health` — 健康探测（直连 or 采集器快照）
- `GET /systems/{id}/metrics` — 实时指标（Prometheus/Redis/Kafka/MySQL/HTTP）

### AI Agent
- `POST /systems/{id}/diagnose` — 同步 AI 诊断，返回 Markdown 报告
- `WS /ws/diagnose/{system_id}` — 流式 AI 诊断（WebSocket）

### Workflow（审批-执行）
- `POST /systems/{id}/workflow/start` — AI 诊断 + 提案动作，暂停等待审批
- `GET /systems/{id}/workflow` — 列出工作流
- `POST /workflow/{id}/decide` — 审批通过/拒绝 → 自动执行

### Collectors（采集器）
- `POST /systems/{id}/collectors` — 注册采集器，返回一次性 key
- `GET /systems/{id}/collectors` — 列出采集器
- `DELETE /collectors/{id}` — 吊销采集器
- `POST /systems/{id}/collectors/{collector_id}/bundle` — 下载带密钥的独立采集包
- `WS /ws/collector?key={collector_key}` — 采集器长连 WebSocket 网关
- `POST /systems/{id}/collector/exec` — 通过采集器远程执行（日志/健康/Prometheus 查询）
- `GET /systems/{id}/logs?service=...` — 查看已注册服务日志（本机或远程采集器）

### Feishu
- `POST /feishu/webhook` — 飞书事件接收（URL 验证 challenge + im.message.receive_v1）

---

## 六、AI 诊断 Agent（backend/app/agent/diagnostics/runner.py）

**框架**：Pydantic AI  
**模型路由**：
- 普通问题 → `deepseek-chat`（快且便宜）
- 含 P0/崩溃/宕机/数据丢失/根因分析等关键词 → `deepseek-reasoner`（高级推理）

**工具集**：
| 工具 | 功能 |
|---|---|
| `list_services` | 并发探活所有服务，返回健康矩阵 |
| `check_service` | 单服务健康检查 |
| `read_logs` | 读取最近 N 行日志 |
| `search_logs` | 关键词搜索日志 |
| `query_prometheus` | 执行 PromQL（9090 端口）|
| `run_kafka_command` | 在 Kafka 容器执行 consumer-groups/topics（只读白名单）|
| `run_redis_command` | Redis INFO/DBSIZE 等只读命令 |

**Kafka 指标名称**（kafka-exporter 格式，非 JMX）：
```
kafka_consumergroup_lag
kafka_consumergroup_lag_sum
kafka_consumergroup_current_offset
kafka_topic_partitions
kafka_brokers
kafka_topic_partition_under_replicated_partition
```

**Langfuse 追踪**：配置 `LANGFUSE_PUBLIC_KEY` 后自动上报每次诊断的问题/回答/token/耗时。

---

## 七、实时指标（backend/app/api/metrics.py）

`GET /systems/{id}/metrics` 返回结构：

```json
{
  "prometheus": {
    "available": true,
    "mem_total_mb": 1958.5, "mem_available_mb": 835.9, "mem_used_pct": 57.3,
    "cpu_usage_pct": 0.9, "targets_up": 4, "targets_total": 4
  },
  "redis": {
    "available": true,
    "used_memory_human": "1.07M", "connected_clients": 1,
    "total_keys": 0, "ops_per_sec": 0, "hit_rate_pct": 0, "uptime_days": 11
  },
  "kafka": {
    "available": true,
    "brokers": 1, "topics": 2, "total_lag": 0,
    "consumer_groups": [{"group": "ops-consumer-group", "topic": "sensor-data", "lag": 0}]
  },
  "mysql": {
    "available": true, "reachable": true,
    "connections": 4, "qps": 1.3, "uptime_hours": 75, "threads_running": 2
  },
  "http_services": [
    {"name": "Platform-API", "ok": true, "status_code": 200, "latency_ms": 25}
  ]
}
```

---

## 八、飞书集成

**自建应用**：`feishu_app_id` / `feishu_app_secret` 配置在 `.env`

**Webhook 接收**（`POST /feishu/webhook`）：
1. URL 验证：返回 `{"challenge": ...}`
2. 消息事件：`im.message.receive_v1`
3. 后台异步：加表情 → AI 诊断 → 删表情 → 发交互卡片回复
4. 事件去重：deque(maxlen=200) 按 event_id 去重

**主动推送**（`FeishuClient`）：
- `send_alert()` — 告警卡片
- `send_card()` — 通用 Markdown 转卡片
- `send_test()` — 测试通知

**域名**：`webhook.tiancaizhaozhao.dpdns.org`（Cloudflare Tunnel → `http://localhost:8000`）  
**事件 URL**：需在飞书开放平台设置为 `https://webhook.tiancaizhaozhao.dpdns.org/feishu/webhook`

---

## 九、前端（Vue 3 + Ant Design Vue 4）

**页面路由**：
- `/login` — 登录
- `/systems` — 系统列表（卡片展示，健康徽章）
- `/systems/:id` — 系统详情（4 Tab）
- /messages — 消息中心（告警、通知、渠道发送结果，支持筛选和重试）
- /audit — 审计记录（诊断、审批、执行、回查全过程追溯）
- /efficiency — 效率分析（告警解决率、处理时间、诊断质量、拦截效果）
- /docs — 接入文档（Token 指引、API 示例）

**系统详情 4 Tab**：
| Tab | 内容 |
|---|---|
| 概览 | 服务健康矩阵，每个服务的 ok/error 状态+详情 |
| 监控 | 系统资源(CPU/内存) + Redis + Kafka + MySQL + HTTP 服务 |
| 诊断 | AI 对话框（WebSocket 流式 or HTTP 同步），Markdown 渲染含表格 |
| 配置 | 服务列表管理 + 通知渠道配置(飞书/Webhook) + 采集器管理 |

**主题系统**（`stores/theme.js`）：
- Latte（暖米色）/ Cloud（纯白简约）/ Forest（深绿）/ Midnight（深蓝）
- 通过 CSS custom properties 全局切换，侧边栏颜色联动

**API 层**（`src/api/index.js`）：
- `baseURL = VITE_API_BASE ?? 'http://localhost:8000'`
- 自动注入 Bearer JWT，401 自动跳登录

---

## 十、共用连接器 SDK（shared/connectors/）

每个连接器实现 `Connector` 抽象基类三个方法：

```python
class Connector(ABC):
    def health(self) -> tuple[bool, str]: ...   # (ok, detail)
    def read_logs(self, lines: int) -> str: ...
    def search_logs(self, keyword: str, lines: int) -> str: ...
```

| connector 值 | 类 | 探活方式 |
|---|---|---|
| `local` | LocalConnector | `docker inspect` → Running 状态 |
| `http` | HttpConnector | HTTP GET health_url < 400 |
| `tcp` | TcpConnector | `socket.create_connection(host, port)` |
| `ssh` | SshConnector | SSH 执行 `echo ok` |
| `prometheus` | PrometheusConnector | PromQL `up` 查询 |
| `k8s` | K8sConnector | `kubectl get pods -l selector` |

---

## 十一、采集器（collector/run.py）

- 部署在客户内网，出站连平台 `wss://[host]/ws/collector?key=[collector_key]`
- 启动后立即上报一次健康快照，之后按 `COLLECTOR_INTERVAL` 上报（默认 30s）
- 接收平台下发的 JSON 指令并返回结果：
  - `fetch_logs` → `read_logs(service, lines)`
  - `search_logs` → `search_logs(service, keyword)`
  - `health_check` → `collect_health(descriptor)`
  - `query_prometheus` → 查询已注册 Prometheus 的 PromQL
  - `run_readonly_query` → 通过采集器在对方网络执行安全 SELECT
  - `run_redis_command` / `run_kafka_command` → 执行白名单只读运维查询
  - `restart_systemd` → 仅重启已注册的 systemd unit，需审批并自动回查

---

## 十二、环境配置（.env 关键字段）

```
# AI 模型：远程 API 或本地部署二选一
LLM_MODE=api
LLM_API_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-...
LLM_API_MODEL=deepseek-chat

# 本地部署示例（Ollama/vLLM/LM Studio 等 OpenAI 兼容服务）
LLM_MODE=local
LLM_LOCAL_BASE_URL=http://localhost:11434/v1
LLM_LOCAL_API_KEY=ollama
LLM_LOCAL_MODEL=qwen2.5:0.5b

# 当前 Docker 内存较小时默认使用 qwen2.5:0.5b；内存调到 6GB+ 后可切到 qwen2.5:3b/7b 提升诊断质量。

# 飞书
FEISHU_APP_ID=cli_aabbbd87b2b91cba
FEISHU_APP_SECRET=...
FEISHU_VERIFICATION_TOKEN=...
FEISHU_ALERT_CHAT_ID=oc_...

# 凭据加密（空 = dev 跳过）
ENCRYPTION_KEY=

# Langfuse 追踪（空 = 跳过）
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# 数据库（dev/prod 统一 Postgres；不设置则回退 SQLite 兜底）
DATABASE_URL=postgresql+psycopg2://ops:opspass@localhost:5432/controlplane
EMBEDDING_DIM=768   # 必须与 embedding 模型输出维度一致（nomic-embed-text=768）

# Celery 巡检（本地 dev 用 6379，deploy compose 内部 6380）
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
```

---

## 十三、当前运行状态

| 组件 | 地址 | 状态 |
|---|---|---|
| FastAPI backend | `http://localhost:8000` | ✅ 运行中（数据在 Postgres） |
| Vue frontend (dev) | `http://localhost:5173` | 需 `npm run dev` |
| **Postgres（平台库）** | `localhost:5432`（ops/controlplane） | ✅ deploy compose，pgvector 已启用 |
| MySQL（被监控栈） | `localhost:3306` | ✅ Docker |
| Redis | `localhost:6379` | ✅ Docker |
| Kafka | `localhost:9092`(外映射) | ✅ Docker |
| Prometheus | `http://localhost:9090` | ✅ Docker |
| Node-Exporter / kafka-exporter / mysqld-exporter | 9100 / 9308 / 9104 | ✅ Docker |
| Ollama | `localhost:11434`（含 nomic-embed-text） | ✅ 本地 LLM/Embedding |
| Cloudflare Tunnel | `webhook.tiancaizhaozhao.dpdns.org` | ✅ 运行中 |

**测试账号**：`admin@test.com` / `admin123`（org_id=1）

**已注册系统**：
- ID=1 `docker-real-local`（Docker真实本地环境）：MySQL/Redis/Kafka/Prometheus/NodeExporter/Platform-API 6 服务
- ID=2 `remote-validation`（远程接入验证系统）
- ID=3 `algp`

**数据迁移（2026-09-22）**：SQLite dev.db → Postgres 已完成（1046 行，12 表核对一致）；
历史库备份在 `backend/archive/`。知识库向量已回填 PG。

**接入方式**：远程系统可先登记服务，再在系统详情创建采集器；采集器通过出站 HTTP/WebSocket 连接平台，负责健康检查、日志、Prometheus 查询和审批后的受限操作。每个系统可单独设置巡检周期，通知支持站内、飞书和邮件。

---

## 十四、关键技术决策记录

1. **多租户行级隔离**：所有表带 org_id，JWT 解析 org，不用 schema 隔离（简单、SaaS 友好）
2. **连接器描述符模式**：DB → descriptor dict → 连接器，解耦存储和执行，采集器/控制面共用同一 SDK
3. **采集器 token**：SHA256 hash 落库，原文仅创建时返回一次，无法从 DB 反推（类似 GitHub PAT）
4. **Kafka 双监听**：`PLAINTEXT_INT://kafka:9092`（容器内互通）+ `PLAINTEXT_EXT://localhost:29092`（主机映射），避免 advertised-listener 循环问题
5. **Fernet 字段级加密**：敏感字段加 `enc:` 前缀，dev 无密钥直接跳过，向后兼容
6. **模型路由**：关键词检测升档至 Reasoner，控制成本同时保证关键告警质量
7. **LangGraph 审批闸**：AI 诊断后挂起等人工 approve，通过后自动执行修复动作
8. **持久化 checkpointer（2026-09）**：AsyncPostgresSaver 替代 MemorySaver，挂起审批跨重启存活；失败降级内存版
9. **pgvector RAG（2026-09）**：文档事实源留磁盘，向量索引入 PG（HNSW），embedding 失败降级关键词；runbook 与检索统一用数字 system_id

---

## 十五、下一步方向（待办）

- [x] Prometheus 告警规则 → 自动触发飞书告警（目前靠 Celery 定时探活）
- [x] 前端 WebSocket 流式诊断接入（已有后端 WS 端点，前端 SystemDetailView 诊断 tab 待改）
- [x] Alembic 迁移脚本补全（当前 dev 用 `create_all`）
- [x] 采集器打包为可执行二进制（PyInstaller + Docker）
- [x] 生产切换 Postgres（docker-compose + DATABASE_URL，依赖已安装）
- [x] Celery Worker 告警任务补全（Beat 定时触发，按系统周期巡检并触发站内/飞书/邮件告警）
- [x] 生产化改造（2026-09-22）：数据库全量切 Postgres（启动自动 Alembic）· LangGraph 审批状态 AsyncPostgresSaver 持久化 · RAG 迁移 pgvector 并修复 runbook 主键错位
- [x] Agent 离线评测真跑基线（三轮方法学迭代 + 数据集扩容 112 条，权威基线见 docs/evals/baseline-112-20260922.md；
      结论：RCA 0.90~0.93、成功率 96~97%、full 组正贡献坐实；下一靶点：跨轮上下文压缩与工具收口）

### 2026-09-23 增补
- **取证子代理**：DeepAgents 式上下文隔离（investigator.py），主代理 investigate() 按服务委派，
  子代理工具调用冒泡进主轨迹（评分/审计/UI 全可见）。112 条回归：tokens -16.4%（12.6k，达标）、
  成功率 98.2% 超基线、RCA 0.892。遗留：延迟 +2.6x（子代理串行），缓解方向为子代理内批量并行。
- **诊断实时工具链**：前端轮询报告 evidence 实时渲染取证步骤（✅/⏳ 图标）。
- 环境加固：colima 4CPU/6GB、MySQL mem_limit 512m。
- **Playbook 拒识路由**：关键词命中即采纳、零命中走 Embedding 语义兜底（≥0.62）、低于阈值拒识
  （不硬塞弱相关剧本）。路由基准：纯语义 42.3% 劣于关键词 59.8%——已记录为负结果。
  112 条回归：成功率 100% 首次满分、RCA 0.92、幻觉 0%（docs/evals/round-routing-20260923.md）。
- **诊断历史操作拆分**：🆕 新对话 / 🗑 清空历史（二次确认 + 自动 JSON 备份导出，防误清）。
- **MCP 工具层**：backend/app/agent/diagnostics/mcp_server.py 把 8 个只读运维工具
  （探活/容器状态/日志/PromQL/Redis/Kafka 白名单）暴露为标准 MCP 协议服务。
  启动：MCP_SYSTEM_ID=1 .venv/bin/python -m app.agent.diagnostics.mcp_server（stdio）
  或 --transport sse --port 8765。任何 MCP 客户端（Claude Desktop/Cursor 等）可直接使用。
  已实测：stdio 标准握手 + list_tools + 真实调用 + 危险命令拒绝。
