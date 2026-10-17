# AIOps 运行目录

本仓库保留当前应用、配置、依赖锁文件与运行入口。测试、评测、历史设计文档和一次性验收脚本已移出。

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

## 运行数据与构建

`.dev-stack/`保存PID、日志、令牌、诊断附件、沙箱环境及运行记录；真实系统知识内容在 `backend/app/agent/diagnostics/knowledge/docs/`。这些数据保留。

前端更新使用启动器自动构建，或运行 `scripts/build-frontend-prod.sh`。`npm run preview`使用 `dist-prod`；旧的普通 `dist`已移出。

`Caddyfile`是本机代理配置；`collector.spec`用于手动打包采集器。容器构建文件与依赖锁保留，第三方依赖内部不逐个删减。
