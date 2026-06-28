# 智能运维平台 · 控制面 (Control Plane)

多租户 AIOps SaaS 的后端骨架。技术栈:**FastAPI + SQLModel + Pydantic AI**,复用仓库根目录的 `connectors/` 包做探活。

## 架构定位

```
Vue SPA ──REST/SSE──> [本控制面 FastAPI]
                         ├─ 鉴权(JWT) + 多租户(org_id 行级隔离)
                         ├─ 系统注册(systems/services → DB)
                         ├─ 监控(经 connectors/ 探活)
                         └─ AI 诊断(Pydantic AI,DeepSeek/Claude 可切)
```

> 当前阶段 = SaaS 控制面 + agentless 直连探测。下一跃:可下载 Collector(出站长连)以触达客户私有内网。

## 本地运行

```bash
cd controlplane
uv venv --python python3.12 .venv          # 首次
uv pip install --python .venv -r <(uv pip compile pyproject.toml)   # 或直接装 pyproject 依赖
.venv/bin/uvicorn app.main:app --reload --port 8000
```

打开 http://localhost:8000/docs 看交互式 API 文档(Swagger)。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 注册(首个用户创建其 Org) |
| POST | `/auth/login` | 登录拿 JWT(OAuth2 表单) |
| GET  | `/auth/me` | 当前用户 |
| POST | `/systems` | 注册一套被监控系统 + 服务 |
| GET  | `/systems` | 列出本租户系统 |
| GET  | `/systems/{id}` | 系统详情 |
| GET  | `/systems/{id}/health` | 经连接器并发探活 |
| POST | `/systems/{id}/diagnose` | AI 诊断(Pydantic AI) |

## 数据模型(多租户)

`Org ──< User`,`Org ──< MonitoredSystem ──< Service`。每张业务表带 `org_id`,所有查询经 `get_current_org_id` 依赖按租户过滤。

## 生产化 TODO(下一阶段)

- DB 换 PostgreSQL(改 `DATABASE_URL`)+ Alembic 迁移
- 鉴权可升级 fastapi-users(OAuth/邮箱验证)
- 定时巡检接 Celery + Redis;告警渠道抽象
- 凭据加密存储(KMS/Vault);审计日志
- Langfuse 可观测性;模型按难度路由(DeepSeek 分诊 / Claude 硬核诊断)
- 审批闸 + 可恢复修复工作流引入 LangGraph
