# AIOps 平台 · Agent 交接简报

> 本文档供外部 Agent 快速建立对本项目的完整认知，可直接作为上下文注入。
> 最后更新：2026-06-29

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
                     ├── Auth (JWT)
                     ├── 系统/服务注册 CRUD
                     ├── 健康探测（直连 or 采集器上报）
                     ├── AI 诊断（Pydantic AI + DeepSeek）
                     ├── 审批工作流（LangGraph）
                     ├── 实时指标聚合（Prometheus/Redis/Kafka/MySQL）
                     ├── 飞书 Webhook 接收（/feishu/webhook）
                     └── 采集器网关（WebSocket）

collector (Python 轻量程序，部署在客户内网)
  ├── 出站连平台 WebSocket 网关
  ├── 本地探活（复用 shared/connectors）
  └── 执行平台下发的指令（fetch_logs / health_check / search_logs）

shared/connectors/   ← 两端共用的连接器 SDK
  local / http / tcp / ssh / prometheus / k8s

基础设施（Docker 容器，已运行）：
  MySQL 3306 · Redis 6379 · Kafka 9092(外) / kafka:9092(内)
  Prometheus 9090 · Node-Exporter 9100
  kafka-exporter 9308 · mysqld-exporter 9104
  Cloudflare Tunnel → webhook.tiancaizhaozhao.dpdns.org → port 8000
```

---

## 三、目录结构

```
ai-ops-agent/
├── backend/              # FastAPI 控制面
│   ├── app/
│   │   ├── main.py       # 入口，include_router 所有路由
│   │   ├── core/
│   │   │   ├── config.py     # pydantic-settings，读 .env
│   │   │   ├── database.py   # SQLModel + SQLite(dev) / Postgres(prod)
│   │   │   ├── deps.py       # get_current_org_id 依赖注入
│   │   │   └── security.py   # hash/verify password, JWT, Fernet 字段加密
│   │   ├── models/
│   │   │   └── tables.py     # Org / User / MonitoredSystem / Service / Collector / ActionWorkflow
│   │   ├── schemas/
│   │   │   └── schemas.py    # Pydantic 请求/响应模型（与 DB 分离）
│   │   ├── api/
│   │   │   ├── auth.py           # POST /auth/register, /auth/login
│   │   │   ├── systems.py        # CRUD /systems, 加/删 service, 通知配置
│   │   │   ├── health.py         # GET /systems/{id}/health
│   │   │   ├── diagnose.py       # POST /systems/{id}/diagnose
│   │   │   ├── metrics.py        # GET /systems/{id}/metrics  ← 本次扩展
│   │   │   ├── collectors.py     # 采集器注册/列表/删除
│   │   │   ├── collector_gateway.py  # WS /ws/collector/{token}
│   │   │   ├── collector_exec.py     # POST /systems/{id}/collector/exec
│   │   │   ├── workflow.py       # 审批工作流 CRUD
│   │   │   ├── websocket.py      # WS /ws/diagnose/{system_id}（流式诊断）
│   │   │   └── feishu_webhook.py # POST /feishu/webhook（飞书事件接收）
│   │   ├── agent/
│   │   │   ├── diagnose.py       # Pydantic AI Agent + 工具集 + 模型路由
│   │   │   └── workflow.py       # LangGraph 审批-执行工作流
│   │   ├── services/
│   │   │   ├── descriptor.py     # DB → 连接器描述符，build_prompt()
│   │   │   ├── feishu.py         # FeishuClient（发卡片/告警/交互）
│   │   │   └── websocket.py      # WebSocket 连接管理
│   │   └── workers/
│   │       ├── celery.py         # Celery 配置
│   │       └── tasks.py          # 定时巡检任务
│   ├── migrations/           # Alembic 迁移
│   ├── dev.db                # SQLite 开发库
│   └── pyproject.toml
│
├── frontend/             # Vue 3 + Vite + Ant Design Vue 4
│   └── src/
│       ├── App.vue           # Shell：侧边栏 + 主题切换 + 路由出口
│       ├── views/
│       │   ├── LoginView.vue
│       │   ├── SystemsView.vue      # 系统列表页
│       │   └── SystemDetailView.vue # 系统详情（4 Tab：概览/监控/诊断/配置）
│       ├── api/index.js      # axios 封装，自动注入 JWT，401 跳登录
│       ├── stores/
│       │   ├── auth.js       # Pinia auth store（token + user）
│       │   └── theme.js      # 多主题（Latte/Cloud/Forest/Midnight）
│       └── router/index.js   # Hash 路由 + 登录守卫
│
├── shared/               # 前后端/采集器共用
│   └── connectors/
│       ├── base.py           # 抽象基类 Connector(health/read_logs/search_logs)
│       ├── local.py          # docker inspect + docker logs（平台托管）
│       ├── http.py           # HTTP GET 健康检测
│       ├── tcp.py            # TCP 端口连通性
│       ├── ssh.py            # SSH 远程执行
│       ├── prometheus.py     # PromQL up 查询
│       └── k8s.py            # kubectl get pods
│
├── collector/            # 可下载采集器（轻量 Python）
│   ├── run.py            # 入口：WS 长连 + 本地探活 + 指令响应
│   └── ws_client.py      # WebSocket 客户端封装
│
├── deploy/
│   ├── docker-compose.yml        # platform-db(Postgres) + platform-redis
│   └── mysqld-exporter/my.cnf   # mysqld-exporter 连 MySQL 配置
│
└── prometheus/
    └── prometheus.yml    # 抓取：prometheus/node-exporter/kafka-exporter/mysqld-exporter
```

---

## 四、数据模型（tables.py）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `orgs` | id, name | 租户组织 |
| `users` | email, hashed_password, org_id, role | 每用户属于一个 org |
| `systems` | org_id, key, name, local, notify(JSON), infra(JSON), last_health(JSON) | 被监控系统 |
| `services` | system_id, name, connector, config(JSON) | 系统下的一个服务 |
| `collectors` | system_id, token_hash, last_seen | 客户内网采集器 |
| `action_workflows` | system_id, thread_id, status, proposed_action(JSON) | 审批工作流 |

**租户隔离**：所有查询经 `org_id` 行级过滤，`get_current_org_id()` 依赖注入从 JWT 解析。

**敏感字段加密**：password/secret/token/key 等字段落库前 Fernet 加密（`enc:` 前缀），读出后解密再传连接器，API 响应统一掩码为 `"***"`。

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

## 六、AI 诊断 Agent（backend/app/agent/diagnose.py）

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
# AI 模型
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
AGENT_MODEL=deepseek-chat
ADVANCED_AGENT_MODEL=deepseek-reasoner

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

# Celery 巡检（Redis 6380）
CELERY_BROKER_URL=redis://localhost:6380/0
```

---

## 十三、当前运行状态

| 组件 | 地址 | 状态 |
|---|---|---|
| FastAPI backend | `http://localhost:8000` | ✅ 运行中 |
| Vue frontend (dev) | `http://localhost:5173` | 需 `npm run dev` |
| MySQL | `localhost:3306` | ✅ Docker |
| Redis | `localhost:6379` | ✅ Docker |
| Kafka | `localhost:9092`(外映射) | ✅ Docker |
| Prometheus | `http://localhost:9090` | ✅ Docker |
| Node-Exporter | `localhost:9100` | ✅ Docker |
| kafka-exporter | `localhost:9308` | ✅ Docker |
| mysqld-exporter | `localhost:9104` | ✅ Docker |
| Cloudflare Tunnel | `webhook.tiancaizhaozhao.dpdns.org` | ✅ 运行中 |

**测试账号**：`admin@test.com` / `admin123`（org_id=1）

**已注册系统**：ID=1，Docker真实本地环境，6个服务：MySQL(127.0.0.1:3306) / Redis(127.0.0.1:6379) / Kafka(127.0.0.1:9092) / Prometheus(http://127.0.0.1:9090) / NodeExporter(http://127.0.0.1:9100/metrics) / Platform-API(http://127.0.0.1:8000/healthz)

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

---

## 十五、下一步方向（待办）

- [x] Prometheus 告警规则 → 自动触发飞书告警（目前靠 Celery 定时探活）
- [x] 前端 WebSocket 流式诊断接入（已有后端 WS 端点，前端 SystemDetailView 诊断 tab 待改）
- [x] Alembic 迁移脚本补全（当前 dev 用 `create_all`）
- [x] 采集器打包为可执行二进制（PyInstaller + Docker）
- [x] 生产切换 Postgres（docker-compose + DATABASE_URL，依赖已安装）
- [x] Celery Worker 告警任务补全（Beat 定时触发，按系统周期巡检并触发站内/飞书/邮件告警）
