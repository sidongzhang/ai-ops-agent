# 多租户智能运维平台（AIOps）

> 让任何业务系统**注册进来就能用**：健康探测 → AI 诊断（读图/读文件/查数据）→ 修复提案 → 人工审批 → 执行 → 回查。
> 已在**三个真实系统**（algp / svom / hxmt）上长期在线运行。

一句话定位：**给业务系统配一个可审计的运维 Agent**——只读取证可自动做，写操作必须人工审批。

---

## 1. 能力总览

| 域 | 能力 |
|---|---|
| **接入** | 多租户（org_id 行级隔离）、系统/服务注册、7 类连接器、出站采集器（平台永不入站访问客户内网）、OpenAPI（Token + scope + IP 白名单） |
| **诊断 Agent** | **23 个预定义只读工具**：健康/日志/PromQL/Redis/Kafka/业务数据集/归档文件问答/服务拓扑/根因图/变更关联/知识库/**MCP 外部工具**/**代码沙箱**/规程（runbook） |
| **上下文工程** | 上下文自动压缩、取证子代理（上下文隔离）、**多专家并行**、工具结果去重、**前缀缓存工程**（实测命中 ~79%） |
| **多模态** | 贴告警/大盘截图提问（控制台 + SVOM 浮窗 + ALGP 全链路），Agent 读图后**必须实时核查**、不盲信截图 |
| **知识/记忆** | pgvector RAG（分块去重 + 混合检索 + 强 rerank）、结构化记忆（置信度 / 有效性回填 / 近重复合并 / 时间衰减） |
| **运维闭环** | 告警对账（按服务抖动抑制）→ 诊断 → 提案 → **审批** → 执行 → **执行后回查**；容器 OOM 主动告警 |
| **主动运维** | 趋势巡检：指标采样 → 最小二乘外推 → **未到阈值就预警**（"按当前增速约 N 小时后触顶"）+ 回落自动解除 |
| **自改进** | 差评 / 诊断失败 → 候选 → **人工采纳**写入知识库（`source=human`，检索优先） |
| **中断与成本** | pydantic-ai **原生取消**（可恢复历史 / 关停收尸 / 心跳判定）、**用量完整性**（`usage_complete`）、**增量账本**、**会话预算**（原子预留） |
| **评测门禁** | 112 条用例 + 4 组消融（baseline/playbook/rag/full）+ CI 门禁（指标回退即失败） |
| **可观测** | 应用日志（诊断耗时/token/缓存命中/成本）、Langfuse trace |

**代码事实**：19 张表 · 21 个迁移 · 102 个 REST 端点 · 229 个后端测试 · 112 条评测用例。

---

## 2. 架构

```
                    ┌──────────────────────── 平台（本仓库 backend/）────────────────────────┐
 浏览器控制台 ──────▶│ FastAPI 控制面                                                                 │
 (frontend/)        │  ├─ 鉴权(JWT) + 多租户行级隔离 + 审计                                            │
                    │  ├─ 诊断 Agent（pydantic-ai 2.55：预定义工具 / 原生取消 / 多模态）                │
                    │  │    └─ 取证子代理 · 专家并行 · MCP 客户端 · 代码沙箱(容器化)                    │
                    │  ├─ 知识/记忆（pgvector RAG + 结构化记忆）                                        │
                    │  ├─ 巡检（健康探测 + 容器 OOM + 趋势预警）                                        │
                    │  └─ 审批工作流（LangGraph + Postgres checkpointer，中断可恢复）                    │
                    └───────┬───────────────────────────────┬──────────────────────────────┘
                            │                               │
             出站采集器（客户内网）                    Postgres / Redis / Kafka / Prometheus
        collector/：上行 HTTP 探活 + 下行 WebSocket 命令
                            │
        外部业务系统（ALGP / SVOM / HXMT）──OpenAPI(出站)──▶ 平台 ──▶ 诊断/告警/日报回传
```

**技术栈**：FastAPI · SQLModel + Alembic · Postgres(pgvector) · Redis · Celery · pydantic-ai 2.55 · LangGraph · Vue 3 + Vite + Ant Design Vue

---

## 3. 快速开始

```bash
# 一次性：配置
cp .env.example .env          # 填 LLM / DB / Redis 等（本地默认已可用）

# 启动整套（后端 + 控制台 + Caddy + Celery + 采集器 + 演示系统 algp/svom/hxmt）
bash scripts/stack-launcher.sh up      # 前台常驻
bash scripts/stack-launcher.sh status  # 看状态
bash scripts/stack-launcher.sh down    # 停止

# 或薄封装
./start_dev.sh
```

| 入口 | 地址 |
|---|---|
| 控制台 | http://localhost |
| 后端 API 文档 | http://127.0.0.1:8000/docs |
| algp / svom / hxmt | :9037 / :9084 / :8096 |

**默认账号**：`admin@test.com` / `admin123`（org_id=1）

> 关键点：`.venv`、`node_modules`、`.dev-stack`（运行态：沙箱 venv / 采集器密钥）都不入库，但**本地需要保留**；`stack-launcher.sh` 会自动准备 `.dev-stack`。

### 本地部署模型（可选）

控制台与 SVOM 浮窗都能选「本地模型」；本地走 OpenAI 兼容端点（默认 Ollama）：

```bash
# 1) 拉一个能胜任工具调用的模型（0.5B 这类小模型答不了诊断，别用）
docker exec ai-ops-agent-ollama-1 ollama pull qwen2.5:7b

# 2) 指向它，然后**重启后端**（配置在导入时读取）
#    .env:  LLM_LOCAL_BASE_URL=http://localhost:11434/v1
#           LLM_LOCAL_MODEL=qwen2.5:7b
```

注意事项：
- **容器内拉取可能因 DNS 失败**（`lookup ...cloudflarestorage.com: no such host`）——ollama 会续传，
  直接重试即可（实测重试一次即成功）；
- **选了 local 就不会静默回退到远程 API**（用户选本地的动机常是数据不出网），本地失败会显式报错；
- 本地模型在无 GPU 的容器里是 CPU 推理，7B 会明显慢于 API，适合"数据不出网"优先于速度的场景。

---

## 4. 仓库结构

```
ai-ops-agent/
├─ backend/            # 控制面（FastAPI）
│  ├─ app/api/         # 102 个 REST 端点（诊断 / 知识库 / 审批 / 巡检 / OpenAPI / 采集器…）
│  ├─ app/agent/       # 诊断 Agent（工具、取证子代理、专家并行、MCP、沙箱）+ 审批工作流
│  ├─ app/services/    # 业务域：diagnostics / knowledge / monitoring / workflows / improvements …
│  ├─ app/models/      # 19 张表（SQLModel）
│  ├─ migrations/      # 21 个 Alembic 迁移
│  └─ tests/           # 229 个测试
├─ frontend/           # 控制台（Vue 3 + Vite + Ant Design Vue）
├─ collector/          # 客户内网采集器（双通道出站）
├─ shared/             # 平台与采集器共享代码（连接器 / 容器状态）
├─ evals/              # 112 条评测用例 + 消融 + CI 门禁
├─ deploy/             # docker-compose、沙箱镜像、演示 MCP server
├─ scripts/            # stack-launcher.sh（一键启停）、eval-gate.sh
├─ docs/               # 设计文档（见 §6）
├─ Caddyfile           # 网关（API 反代 + 控制台静态）
└─ docker-compose.yml  # 本地依赖（MySQL/Redis/Kafka/Prometheus/导出器）
```

---

## 5. 关键设计（值得先读的几个决定）

| 主题 | 决定 | 文档 |
|---|---|---|
| **工具面** | **不**做"Agent 现场生成工具"；只暴露预定义、可审计的只读工具；写操作一律走审批 | 本文件 §1 |
| **中断与成本** | 用 pydantic-ai **官方原生**取消（`CancellationToken` / `RunCancelled` / 可恢复消息历史），并落地 `usage_complete` + 增量账本 + 会话预算 | `docs/诊断中断与成本控制设计.md` |
| **上下文** | 子代理隔离 + 压缩 + **前缀缓存友好**：系统提示分「稳定前缀 + 易变后缀」两段拼装（技能步骤/当前时间/变更/失败/知识库检索一律置尾），保证 provider 的 prefix cache 命中（实测不同问题命中率 25%→70%）；ALGP 业务快照瘦身到 8000 字符 | `docs/诊断中断与成本控制设计.md` |
| **模型选择** | 控制台与 SVOM 浮窗都可选 自动/本地/API/高级（`model_mode` 经 ALGP 透传，平台侧校验非法值回落 auto）；本地模型走 Ollama（`LLM_LOCAL_*`） | 本文件 §3 |
| **外部能力** | 用 **MCP 标准协议**接第三方工具（K8s/云/工单），不为每个工具写代码 | `docs/MCP接入.md` |
| **沙箱** | Agent 生成的代码在**一次性容器**里跑（`--network none` / 限额 / 用完即毁） | `deploy/sandbox/` |
| **知识可信度** | 记忆分层（模型 0.5 / 人工 1.0）、有效性回填、近重复合并；**自改进必须人工采纳** | `docs/归档文件问答能力设计.md` |

---

## 6. 测试与评测

```bash
# 后端单测
cd backend && .venv/bin/python -m pytest tests -q

# 评测门禁（数据集校验 → 脚手架自测 → dry-run 门禁）
bash scripts/eval-gate.sh
bash scripts/eval-gate.sh --full     # 真实评测（需 Docker + LLM 额度）
```

CI：`.github/workflows/eval-gate.yml`（push/PR 自动跑；指标低于阈值即失败）。

---

## 7. 运维速查（排障先看这里）

| 现象 | 处理 |
|---|---|
| 端口占用 / 服务没起来 | `bash scripts/stack-launcher.sh status`；日志在 `.dev-stack/logs/` |
| 改了 `.env` 不生效 | 配置在导入时读取 → **重启后端** |
| 改了 `collector/` 代码 | 需**重启采集器**（常驻进程） |
| 消息中心大批"分析失败" | API 与 worker 的 Redis 指向不一致（本地应为 `6379`，见 `.env.example`） |
| 诊断一直 `running` | 心跳超时后由 reaper 收成终态；`usage_complete`/`resumable` 会如实标注 |
| 预算被拒（429） | 滚动窗口内累计（含保守预留）超限；等窗口滚动或调 `DIAGNOSIS_SESSION_*` |

**设计原则**：宁可**如实说"不知道/拿不到"**，也不把未知当 0（用量、恢复历史、探活结果都遵守这条）。

---

## 8. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/诊断中断与成本控制设计.md` | 中断/恢复/用量完整性/预算（含实测修正与验收矩阵） |
| `docs/归档文件问答能力设计.md` | 归档文件问答（列目录/读文件/取回）能力设计 |
| `docs/MCP接入.md` | 接第三方 MCP 工具（配置 + 安全边界） |
| `docs/外部系统接入开发文档.md` | 外部业务系统如何接入（OpenAPI 用法） |
| `docs/runbook-examples/` | 规程（runbook）样例：Kafka 积压、归档解读 |
| `collector/README.md` | 采集器（客户内网出站双通道） |
| `evals/README.md` | 评测体系与消融说明 |
