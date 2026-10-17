# AIOps 运行目录

本仓库包含当前应用、配置、依赖锁文件、运行入口，以及**测试与评测门禁**（`backend/tests`、`evals/`）。
历史设计文档与一次性验收脚本已移出到 `audit/`。

## 本机启动

```bash
./start_dev.sh
./start_dev.sh status
./start_dev.sh down
```

入口统一调用 `scripts/stack-launcher.sh`，桌面启动程序也使用该脚本。启动前运行 `scripts/prepare-local-stack.py`，同步锁定依赖、构建控制台及执行数据库迁移准备。

配置使用仓库根目录 `.env`，变量见 `.env.example`。不要提交真实密钥。后台依赖在 `backend/.venv`，前端依赖在 `frontend/node_modules`，Pi依赖在 `agent-runtime/node_modules`，当前环境保留。

## 运行组成

- `backend/`：FastAPI、Pydantic主引擎、统一harness、业务服务、Celery与数据库迁移。
- `frontend/`：AIOps控制台，当前Caddy静态根目录为 `frontend/dist-prod`。
- `collector/`、`shared/`：采集器与共享连接器；`collector/README.md`是采集器下载包的一部分。
- `agent-runtime/`：Pi只读试点运行服务；启动入口 `backend/.venv/bin/python scripts/pi-runtime.py`，需显式配置并启用。
- `deploy/`：平台容器、Nginx、分析沙箱及当前配置引用的MCP服务。
- `prometheus/`、根 `docker-compose.yml`：本地被监控服务及指标抓取。
- `scripts/`：启动、构建、隧道与本地模型运行准备。

两份Compose用途不同：根文件管理本地被监控依赖，`deploy/docker-compose.yml`管理平台PostgreSQL、Redis、API、worker、beat和Web。

`backend/migrations/`仍属于启动依赖。不要删除迁移历史链、真实系统知识库或调度状态。

## 测试与评测门禁

```bash
# 单元/集成测试（约 328 项）
backend/.venv/bin/python -m pytest backend/tests -q

# 评测数据集校验（不调用模型）
backend/.venv/bin/python evals/run_eval.py --validate

# 冒烟门禁：mock Agent，不花模型费，CI 每次可跑
backend/.venv/bin/python evals/run_eval.py --dry-run --gate --thresholds evals/thresholds.smoke.json

# 真实门禁：真实模型 + 真实故障注入（会 stop/start 容器，产生模型费用）
backend/.venv/bin/python evals/run_eval.py --run --gate --thresholds evals/thresholds.json

# 发布前门禁：再加 LLM 裁判（faithfulness / correctness，额外模型费用）
backend/.venv/bin/python evals/run_eval.py --run --judge --gate --thresholds evals/thresholds.judge.json
```

> ⚠️ `--run` 会按用例**真实注入故障**（stop/start MySQL / Redis / Prometheus 等容器），
> 期间相关端口与依赖会短暂不可用，进程本身不会退出（PID 不变、无 traceback）。
> **不要与现场演示同时跑**；需要时用 `--limit` 分批。

口径说明：`tool_selection_f1` 只作信息指标——`required_tools` 是**最低要求**，
额外的只读取证是期望行为；门禁看「必要工具覆盖率 recall」+「平均调用上限」。

## 安全与备份

上线前的安全清单、密钥轮换与备份/恢复步骤见 [`SECURITY.md`](SECURITY.md)。要点：

- `JWT_SECRET` / `ENCRYPTION_KEY` 无默认值；`ENVIRONMENT=production` 下缺失会**拒绝启动**。
- 飞书 Webhook 必须配置 `FEISHU_VERIFICATION_TOKEN`，否则返回 503（fail-closed）。
- 数据库备份：`scripts/backup.sh`；恢复：`scripts/restore_db.py --backup <目录> --yes`。

```bash
# 立即备份一次（保留最近 14 份）
scripts/backup.sh
# 检查最近备份是否新鲜（>26h 返回非 0，便于告警）
scripts/backup.sh check
# 恢复演练（临时 schema + 行数对账，验证完自动清理）
backend/.venv/bin/python scripts/verify_backup.py
```

平台自身指标：`GET /metrics`（Prometheus 文本格式），已加入 `prometheus/prometheus.yml` 的
`aiops-control-plane` 抓取任务；覆盖 `aiops_db_up`、`aiops_audit_write_failures_total`、
`aiops_sandbox_blocked_total`、`aiops_unknown_connector_total`。详见 `SECURITY.md`。

## 运行数据与构建

`.dev-stack/`保存PID、日志、令牌、诊断附件、沙箱环境及运行记录；真实系统知识内容在 `backend/app/agent/diagnostics/knowledge/docs/`。这些数据保留。

前端更新使用启动器自动构建，或运行 `scripts/build-frontend-prod.sh`。`npm run preview`使用 `dist-prod`；旧的普通 `dist`已移出。

`Caddyfile`是本机代理配置；`collector.spec`用于手动打包采集器。容器构建文件与依赖锁保留，第三方依赖内部不逐个删减。
