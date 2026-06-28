# 智能运维平台 · 控制面 (Control Plane)

多租户 AIOps SaaS 的后端骨架。技术栈:**FastAPI + SQLModel + Pydantic AI + Alembic + Celery**，复用仓库根目录的 `connectors/` 包做探活。

## 架构定位

```
Vue SPA ──REST/SSE──> [本控制面 FastAPI]
                         ├─ 鉴权(JWT) + 多租户(org_id 行级隔离)
                         ├─ 系统注册(systems/services → DB)
                         ├─ 监控(经 connectors/ 探活)
                         └─ AI 诊断(Pydantic AI，DeepSeek/Claude 可切)

Celery Beat ──每 60s──> Celery Worker
                         └─ 遍历所有系统 → collect_health() → 写快照 → 告警 Webhook
```

## 本地运行（dev，SQLite）

```bash
cd controlplane
uv venv --python python3.12 .venv          # 首次创建 venv
uv pip install --python .venv -e .         # 安装所有依赖（含 alembic + psycopg2-binary）
.venv/bin/alembic upgrade head             # 建表（dev 用 SQLite）
.venv/bin/uvicorn app.main:app --reload --port 8000
```

打开 http://localhost:8000/docs 看交互式 API 文档（Swagger）。

> dev 默认使用 `controlplane/dev.db`（SQLite），零配置启动，无需额外服务。

## 凭据字段加密

`Service.config` 和 `MonitoredSystem.infra` 中的敏感字段（`password`、`token`、`private_key`、`identity_file`、`kubeconfig` 等）在落库前自动加密（Fernet AES-128），API 响应中掩码为 `"***"`，连接器使用前自动解密。

**生成密钥：**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

写入根目录 `.env`：
```
ENCRYPTION_KEY=<生成的密钥>
```

- **dev 可不设置**（留空跳过加密，零摩擦本地开发）
- **生产必须设置**并妥善保管；密钥丢失则已加密数据无法恢复
- 密钥轮转：需对所有已加密记录重新用新密钥加密（`re-encrypt` 脚本后续提供）

## 切换 PostgreSQL（生产）

**1. 启动 Postgres**（使用仓库根目录提供的 compose 文件）：

```bash
docker compose -f docker-compose.platform.yml up -d
```

这会在本机 5432 启动 `postgres:16-alpine`，数据库名 `controlplane`，用户 `ops/opspass`。

**2. 配置 DATABASE_URL**（在仓库根 `.env` 或 shell 里）：

```bash
export DATABASE_URL=postgresql+psycopg2://ops:opspass@localhost:5432/controlplane
```

**3. 运行迁移**：

```bash
cd controlplane
.venv/bin/alembic upgrade head
```

**4. 启动控制面**：

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

### Alembic 常用命令

```bash
# 查看当前版本
.venv/bin/alembic current

# 查看迁移历史
.venv/bin/alembic history

# 生成新的迁移（修改 models.py 后）
.venv/bin/alembic revision --autogenerate -m "describe change"

# 升级到最新
.venv/bin/alembic upgrade head

# 回滚一步
.venv/bin/alembic downgrade -1
```

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 注册（首个用户创建其 Org） |
| POST | `/auth/login` | 登录拿 JWT（OAuth2 表单） |
| GET  | `/auth/me` | 当前用户 |
| POST | `/systems` | 注册一套被监控系统 + 服务 |
| GET  | `/systems` | 列出本租户系统 |
| GET  | `/systems/{id}` | 系统详情 |
| GET  | `/systems/{id}/health` | 经连接器并发探活 |
| POST | `/systems/{id}/diagnose` | AI 诊断（Pydantic AI） |
| POST | `/systems/{id}/collectors` | 为系统创建采集器（返回一次性密钥） |
| GET  | `/collector/config` | 采集器拉取探测配置（X-Collector-Key） |
| POST | `/collector/report` | 采集器上报健康快照（X-Collector-Key） |

## 数据模型（多租户）

`Org ──< User`,`Org ──< MonitoredSystem ──< Service`,`MonitoredSystem ──< Collector`。
每张业务表带 `org_id`，所有查询经 `get_current_org_id` 依赖按租户过滤（行级隔离）。

## 定时巡检（Celery）

`docker-compose.platform.yml` 已包含 Redis（端口 6380）。

```bash
# 启动 Postgres + Redis
docker compose -f ../docker-compose.platform.yml up -d

# Worker（处理任务）—— 新终端
cd controlplane
.venv/bin/celery -A app.celery_app worker --loglevel=info

# Beat（定时发布）—— 新终端
.venv/bin/celery -A app.celery_app beat --loglevel=info
```

每隔 `HEALTH_CHECK_INTERVAL`（默认 60s）秒，Worker 自动巡检所有系统，发现新异常时向 `system.notify.webhook_url` 发送告警（飞书/钉钉/Slack incoming webhook 格式兼容）。

**告警策略**：边沿触发（新出现的 FAIL 才告警）+ 冷却期（`ALERT_COOLDOWN_SECONDS`，默认 3600s）防刷屏。有采集器且近期有上报的系统跳过，避免重复探测。

可通过根 `.env` 覆盖：
```
CELERY_BROKER_URL=redis://...
HEALTH_CHECK_INTERVAL=30
ALERT_COOLDOWN_SECONDS=1800
```

## 生产化 TODO（下一阶段）

- 凭据加密存储（KMS/Vault）；审计日志
- Collector 下行通道（WebSocket）：平台主动向采集器下发命令
- Langfuse 可观测性；模型按难度路由（DeepSeek 分诊 / Claude 硬核诊断）
- 审批闸 + 可恢复修复工作流引入 LangGraph
- fastapi-users（OAuth/邮箱验证）；多副本部署（连接池 pgBouncer）
