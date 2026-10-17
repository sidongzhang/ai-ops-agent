# 部署与运维可靠性 体检

## 现状设计

本仓库同时维护两套互不相同的运行拓扑，但只有其中一套真正在跑。被监控依赖栈用根 `docker-compose.yml` 描述（MySQL/Redis/Kafka/三组 exporter/Prometheus/Ollama），由 `scripts/stack-launcher.sh` 通过 `docker compose start|up -d --no-recreate` 拉起；平台自身则由同一脚本以**宿主机裸进程**方式启动（`backend/.venv/bin/uvicorn`、两个 Celery worker、beat、`npm run dev`、Caddy），PID 与日志落在 `.dev-stack/{pids,logs}`，靠 `lsof` 按端口清理。`deploy/docker-compose.yml` 描述的是另一套完整的容器化平台栈（PG+Redis+api+worker+diagnosis-worker+beat+Nginx web），但脚本里只用到它的 `platform-db` 一个服务（`stack-launcher.sh:203`），其余五个服务目前没有任何自动化入口。对外入口是 Caddy 监听 80 端口做 API 前缀分流与 SPA 静态托管，再由 launchd 常驻的 cloudflared 隧道把 `aiops/webhook` 等域名映射回本机 localhost，因此"生产"流量实际落在开发者本机笔记本上。应用侧的可恢复性设计投入很深：`backend/app/main.py:46-82` 在 uvicorn 之前链式接管 SIGTERM 同步取消在跑诊断，`services/diagnostics/shutdown.py` 做有界宽限与终态补写，`reaper.py` 用"心跳新鲜则绝不误收"的判据在启动与每 30s 补收尸。

## 做得好的地方

- **依赖顺序用真实的健康语义表达，而不是 `depends_on` 裸顺序**：`deploy/docker-compose.yml:42-46`（api 等 PG+Redis healthy）、`:67-69`（worker 等 api healthy）、`:87-89`（beat 等 api healthy）、根 `docker-compose.yml:83-85, 94-96`（两个 exporter 等被采集对象 healthy）。迁移先于 worker 启动由 `deploy/docker-compose.yml:41` 的 `alembic upgrade head &&` 保证，避免了 worker 读到旧 schema。
- **迁移前备份的实现细节是教科书级的**：`scripts/prepare-local-stack.py:78-89` 用 `O_EXCL` + `0600` 建文件、目录 `0700`、`pg_dump -Fc` 直写 fd、写完校验非空、异常时删掉半成品；`:76` 还把自动化备份限制在 `localhost + controlplane` 之外一律拒绝执行，避免误备份/误判远端库。`:41-53` 的前端产物构建用 staging 目录 + `rename` 原子替换 + 失败回滚，规避了"构建到一半把线上静态资源改坏"。
- **优雅关停刻意绕开了 uvicorn 的关停顺序陷阱**：`backend/app/main.py:46-82` 明确注释了"uvicorn 会先等 BackgroundTask 跑完再关 lifespan"，因此抢在 uvicorn 之前链式挂信号处理器同步取消；`shutdown.py:1-13, 66-88` 对"取消令牌只表示请求、不等于已停止"的语义边界处理得很克制，宁可标 `failed/resumable=false` 也不谎称可恢复。
- **reaper 的失联判据避免了误收活跃任务**：`reaper.py:56-74` 区分"pending 队列由 durable reconciler 负责"、"started 但无心跳退回更短的创建时间窗"、"心跳新鲜一律跳过"，并且 `:83-93` 对未知消耗采取保守结算（用预留量兜底）而不是按免费退款——这是多租户计费场景下正确且少见的取舍。
- **代码沙箱在"能用容器时"确实做了硬隔离**：`services/diagnostics/sandbox.py:61-72` 一次性容器 + `--network none`（读不到平台密钥、连不上内网库）+ `--memory 1g --cpus 1 --pids-limit 256`，`:139-140` 在 `finally` 里额外 `docker rm -f` 补偿"超时只杀掉 docker 客户端、容器会残留"的问题；非容器路径 `:30-43` 也上了 `RLIMIT_CPU/AS/FSIZE/NPROC`，而且 `:10-11` 明确写了"这不是安全沙箱"。
- **密钥不被烤进镜像**：`backend/Dockerfile:8-9` 只 `COPY backend shared`，根 `.env` 不入镜像；`.gitignore:2` 忽略 `.env`；`deploy/docker-compose.yml:36-37` 对 `JWT_SECRET/ENCRYPTION_KEY` 用 `${VAR:?...}` 强制外部注入，缺失直接拒绝启动而不是落到 `config.py:51` 的 `dev-secret-change-me-in-prod`。
- **前端静态资源的缓存策略与"HTML 指向已删除 chunk"这对经典矛盾被显式解决**：`Caddyfile:28-31` 带 hash 资源 `immutable` 长缓存、`:39-44` SPA 入口 `no-store`；`scripts/build-frontend-prod.sh:12-25` 还额外校验所有懒加载 chunk 真实存在后才允许 reload。

## 发现

### [critical] 业务与平台数据没有任何备份机制，单盘故障即全量丢失

- **证据**：`docker-compose.yml:16-17` 业务数据落在命名卷 `real_mysql_data:/var/lib/mysql`；`deploy/docker-compose.yml:10-11` 平台控制面落在 `platform_pg_data:/var/lib/postgresql/data`；诊断附件落在本机目录（`backend/app/core/config.py:177-179`，实际为 `.dev-stack/artifacts`，实测 92K）。全仓库唯一的备份代码是 `scripts/prepare-local-stack.py:79-89`，它的触发条件是 `:75` `revision != head`，即**只有 alembic 版本号变化的那一次启动**才 dump 一次，且 `:79` 把文件写回同一个 `.dev-stack/backups`（与源库同盘、同机、同用户）。`ls ~/Library/LaunchAgents/` 与 `crontab -l` 均无任何定时备份任务；两份 compose 中也不存在 `alertmanager` 或备份 sidecar（`grep -c alertmanager` 均为 0）。没有任何 restore 脚本或恢复演练记录。
- **风险**：  
  - 平台 PG 里的 `diagnosis_reports`/`diagnosis_job`/`diagnosis_usage_ledger`/`diagnosis_budget_reservation`/`audit_logs`/`knowledge_memories` 支撑多租户计费与审计；volume 一旦损坏（colima VM 磁盘写坏、`docker volume rm`、磁盘写满后半写）不可恢复，且计费账本缺失无法用重放补齐。  
  - 业务 MySQL 卷里是"被监控系统"的真实业务数据（`stack-launcher.sh:278` 的 algp 库直连该实例），损坏即客户数据丢失。  
  - 迁移前备份本身也在同一块盘上：磁盘故障时备份与源库一起没，等于没有备份。  
  - `prepare-local-stack.py:76` 还会把自动化备份限制为本地库，所以一旦 `DATABASE_URL` 指向远端，连这一次性的弱备份都直接 `RuntimeError` 跳过。
- **业界做法**：  
  - 平台库用 `pgBackRest`/`WAL-G` 做 PITR（base backup + WAL 归档到 S3/OSS），RPO 可到秒级；至少也要 `pg_dump -Fc` 定时 + 异地保存。  
  - MySQL 侧用 `mysqldump --single-transaction --source-data=2` 或 XtraBackup 定时全量 + binlog 增量。  
  - 用 K8s CronJob / systemd timer / launchd 承载 schedule，配 3-2-1 保留策略与**定期 restore 演练**（备份未验证等于没有备份）。  
  - Docker 卷改为绑定到独立数据盘/网络存储，卷与备份不同故障域。
- **可采纳性**：moderate。定时全量 dump + 异地副本 + restore 演练属于脚本级工作，不需要架构重构；难点在于"异地"需要一个对象存储凭据与保留策略治理，成本真实但不失控。建议第一步先做"每日 dump + 校验 + 异机/对象存储"，PITR 留到真正上生产时再补。
- **建议**：新增 `scripts/backup-stack.sh`（`pg_dump -Fc --no-owner` + `mysqldump --single-transaction`，产物 `gzip` 后 `aws s3 cp`/`rclone` 到对象存储，附 `sha256sum` 与 `zcat | pg_restore --list` 自检），用 launchd plist（参考已有 `scripts/com.aiops.cloudflared.plist` 的写法）每日触发；在 `prepare-local-stack.py:78` 的备份目录外再落一份到异机；补 `scripts/restore-stack.sh` 并在 README 写清 RPO/RTO。预期收益：把"磁盘坏 = 业务与计费数据永久丢失"降级为可恢复的小时级 RPO。

### [high] 容器化部署下代码沙箱静默降级为"在 API 进程内执行 agent 生成的代码"，且沙箱镜像无任何构建入口

- **证据**：`services/diagnostics/sandbox.py:63-65` 的判据是 `if not image or not shutil.which("docker"): return None`，`:126` 随即退化成 `argv = docker_argv or [settings.sandbox_python or sys.executable, "-I", script_path]`。而 `backend/Dockerfile:1-14` 的基础镜像是 `python:3.12-slim`，全文件没有安装 docker CLI（`grep -n docker backend/Dockerfile` 无输出），镜像内也没有 `.env`，于是 `config.py:182` 的 `sandbox_python` 取默认空值 → 落到 `sys.executable`，即 **API 容器自己的解释器**。触发点是 agent 工具 `run_python_analysis`（`backend/app/agent/diagnostics/tools.py:501`，风险等级在 `agent/harness/catalog.py:50` 被标为 `sandbox`）。另一方面 `deploy/sandbox/Dockerfile:2` 的注释写着"构建：`docker build -t aiops-sandbox:latest deploy/sandbox`"，但全仓库搜不到任何脚本、compose service 或 CI 步骤执行它（`grep -rn "aiops-sandbox|deploy/sandbox"` 排除 `.venv/node_modules` 后零命中），`deploy/docker-compose.yml` 里也没有对应 service。
- **风险**：  
  - 安全面：一旦走容器化拓扑，被监控系统的日志/指标（可被提示注入污染，`ARCHITECTURE.md:307` 自己承认只做了"低档"外部数据护栏）能够诱导 agent 生成代码，而该代码将以 API 容器身份、**无文件系统与网络隔离**地执行——容器环境里直接可读 `DATABASE_URL`(含密码)、`JWT_SECRET`、`ENCRYPTION_KEY`、`DEEPSEEK_API_KEY`、`FEISHU_APP_SECRET`（`deploy/docker-compose.yml:33-40`），并可直连 `platform-db:5432`。这等于把"多租户只读诊断"变成"任意租户可读平台库与密钥"。  
  - 可用性面：宿主机路径下的隔离只在 `docker` 存在时生效；反向的失效模式是镜像缺失——`docker run aiops-sandbox:latest` 会去 Docker Hub 拉 `library/aiops-sandbox:latest`（不存在）而报错，于是 `run_python_analysis` 在全新环境里 100% 失败，而且失败信息是一句 `Unable to find image`，排查者很难想到"镜像没人构建"。
- **业界做法**：沙箱化执行 agent 代码应独立成服务（如 `nsjail`/`gVisor`/Firecracker microVM，或 E2B/Daytona 这类托管沙箱），默认 `--read-only` + `--user 65534` + `--cap-drop=ALL` + `--security-opt=no-new-privileges` + 默认 seccomp，网络默认 deny，且**能力探测失败时 fail-closed**（拒绝执行）而不是 fail-open（降级为进程内）。镜像构建与推送必须进 CI，并在部署前做一次"沙箱可用性" preflight。
- **可采纳性**：moderate。把 `_docker_argv()` 的降级改成 fail-closed、并给 backend 镜像加 `docker-cli`（或把沙箱拆成独立 service）是一两天的改动；真正的 gVisor/microVM 属于架构级投入，可后置。但"fail-closed + 镜像进构建流水线 + 容器安全选项"这部分成本很低、收益极高，应立刻做。
- **建议**：① 把 `sandbox.py:63-65` 的降级改为：若配置了 `sandbox_container_image` 但 docker/镜像不可用则返回明确错误并拒绝执行（`ok=False, stderr="sandbox unavailable"`），仅在**显式**设置 `SANDBOX_ALLOW_INPROCESS=1` 时才允许进程内执行，避免生产静默降级；② 在 `deploy/docker-compose.yml` 增加 `sandbox-image` 构建服务或用 `make build-sandbox` 纳入启动前 preflight，并在 `prepare-local-stack.py` 里校验 `docker image inspect aiops-sandbox:latest`；③ 给 `sandbox.py:66-72` 的 `docker run` 补 `--user 65534:65534 --read-only --tmpfs /tmp --cap-drop=ALL --security-opt=no-new-privileges`。

### [high] 全栈没有任何自愈能力：容器无 `restart` 策略、宿主进程无监管、且整套"生产"跑在一台笔记本上

- **证据**：`deploy/docker-compose.yml` 全文（104 行）**没有一处 `restart:`**，即 api/worker/diagnosis-worker/beat/web 全部是 Docker 默认的 `no`；根 `docker-compose.yml` 里唯一的 `restart: unless-stopped` 在 `:139`，属于 `profiles: ["llm"]` 的 ollama——默认 profile 下根本不启动（`stack-launcher.sh:191` 还会主动 `docker stop` 它）。宿主侧同样没有监管：`stack-launcher.sh:532-544` 的 `while true` 循环只做 `kill -0` 检测并打印 `⚠ $nm 已退出`，注释与实现都表明它**只报告不重启**；唯一具备 `KeepAlive` 的常驻服务是 cloudflared（`scripts/com.aiops.cloudflared.plist:19-20`）。同时，被对外服务的平台只有一份：单机 PG（`deploy/docker-compose.yml:2`）、单实例 Redis（`:18`）、单实例 API/worker/beat，隧道把 `aiops.tiancaizhaozhao.dpdns.org → localhost:80`、`webhook...→ localhost:8000`（`~/.cloudflared/config.yml:11-14`）映射回这台机器。
- **风险**：任一常见事件都足以造成**需要人工介入才恢复**的停机：宿主重启/Docker daemon 重启（`no` 策略下容器不会回来，`stack-launcher` 也不会自动跑）、macOS 睡眠或合盖、colima VM 内存不足被 OOM（`docker-compose.yml:11` 的注释显示这在 2026-09-22 已真实发生过一次）、单一 worker 进程崩溃（脚本只打印告警，诊断队列就此停摆）。对通过隧道访问的飞书 webhook 与外部用户而言，表现为长时间 5xx/超时，且没有任何自动恢复路径——这与"生产运维平台"的可用性预期严重不符。
- **业界做法**：容器级自愈的最低配是给所有长驻服务加 `restart: unless-stopped` 或 `always`（K8s 用 `Deployment` + liveness/readiness probe 自动重建）；宿主进程用 systemd `Restart=always` / launchd `KeepAlive` 托管而不是 `screen`+`nohup`；有状态组件（PG/Redis/Kafka）至少做流复制或托管服务；整套服务应跑在专门的服务器/云主机上，而不是把开发者机器当生产节点。
- **可采纳性**：easy（自愈部分）/ hard（真正的 HA）。给两份 compose 逐个 service 补 `restart`、把 uvicorn/celery 从 `screen` 迁移到 launchd/systemd 是一小时级别的改动；而消除单机单点需要换部署目标（云主机 + 托管 PG/Redis），属于架构级决定，成本真实，需先明确这个平台是否真的要承担生产流量。
- **建议**：① 在 `deploy/docker-compose.yml` 的 7 个 service 与根 `docker-compose.yml` 的 mysql/redis/kafka/prometheus/exporters 上统一补 `restart: unless-stopped`；② 把 `stack-launcher.sh:532-544` 从"只报告"升级为"带退避重启 + 重启次数上限 + 超限告警"，或直接用 `scripts/com.aiops.cloudflared.plist` 的模板为 backend/worker/beat 各写一个 launchd plist 用 `KeepAlive` 托管；③ 明确写进 README：本机栈是演示环境，生产需迁移到独立主机 + 托管有状态服务。

### [high] 系统级 API 令牌以明文硬编码并已提交进 git 历史

- **证据**：`scripts/stack-launcher.sh:282` 在启动 algp 后端时直接内联了平台签发的系统令牌：`AIOPS_TOKEN='sys_********（已脱敏）'`；该文件被 git 跟踪（`git ls-files scripts/` 包含 `scripts/stack-launcher.sh`，`git log -1 -- scripts/stack-launcher.sh` 显示提交 `3610209`）。对比之下，同一脚本对采集器密钥做了正确的外置处理：`:251-255` 从 `.dev-stack/collector-key-algp.txt` 读取（该目录在 `.gitignore:20` 中被忽略），说明团队知道正确做法，只是这一处漏了。
- **风险**：  
  - 该令牌是"系统级"凭据（`AIOPS_SYSTEM_CODE='algp'`），持有者可以按 system 身份调用平台 API，绕过用户级鉴权与租户隔离。  
  - 令牌已进入 git 历史，即使现在删除，任何克隆过仓库的人（含未来的协作者、CI 缓存、`.git` 备份、GitHub 上的 fork）仍可永久取得；轮换前一直有效。  
  - 平台 API 同时通过隧道公网可达（`~/.cloudflared/config.yml:13-14`），泄露凭据 + 公网入口 = 可直接利用，不需要内网位置。
- **业界做法**：凭据一律走 secret manager（Vault / AWS Secrets Manager / SOPS+age / GitHub Actions secrets），本地开发用 `.env`（已 gitignore）或 macOS Keychain 注入；CI 上启用 `gitleaks`/`trufflehog` 的 push 保护阻断明文密钥；令牌设计为短 TTL + 可撤销 + 轮换，并绑定 scope 与来源约束。
- **可采纳性**：easy（移除硬编码与轮换）/ moderate（防止复发）。把令牌挪进 `.dev-stack/algp.token`（该文件已存在且为 `0600`，`stack-launcher.sh:251` 已有同款读取模式）并轮换是分钟级改动；补 pre-commit/CI 密钥扫描与历史清理（`git filter-repo`）稍麻烦，但必须做——因为历史里的旧值不会因为你新提交一次修复而失效。
- **建议**：① 立即在平台侧吊销/轮换该 `sys_` 令牌；② 把 `stack-launcher.sh:282` 改为从 `.dev-stack/algp.token` 读取（缺失则跳过并 `warn`），模式照抄 `:251-255`；③ 在 `scripts/` 增加 `gitleaks detect --no-git` 的 preflight 并在 CI 挂 `gitleaks` 扫描；④ 评估是否需要用 `git filter-repo` 清理历史（若仓库曾被外部分发则必须）。

### [high] 容器化拓扑下优雅关停链路被 PID 1 与缺失的 `stop_grace_period` 整体绕过，普通队列任务静默丢失

- **证据**：`deploy/docker-compose.yml:41` 的启动命令是 `command: sh -c "alembic upgrade head && uvicorn app.main:app ..."`——因为含 `&&`，`sh` 无法 `exec` 优化，PID 1 是 `sh` 而非 uvicorn，Docker 发的 SIGTERM 到不了 uvicorn。这直接废掉了团队精心实现的关停机制：`backend/app/main.py:46-82` 的信号处理器、`services/diagnostics/shutdown.py:23-63` 的有界宽限与终态补写都不会被触发（只在宿主直跑拓扑里有效）。同时两份 compose 全文没有任何 `stop_grace_period`（`grep -rn stop_grace_period` 零命中），Docker 默认宽限 10s 后 SIGKILL；而单次诊断的上限是 `config.py:152` 的 `diagnosis_max_run_seconds: int = 900`，`:153` 的 `diagnosis_shutdown_grace_seconds` 还是 15s——即便信号能到达，10s 的容器宽限也不够。此外 `backend/app/workers/celery.py:7-35` 未设置 `task_acks_late`（默认 False），worker 被 SIGKILL 时已预取并 ACK 的任务就此消失。
- **风险**：  
  - 诊断路径：进程被 SIGKILL 后报告永远停在 `running`，只能靠启动时的 `reap_stale_diagnoses(minutes=1)`（`main.py:94`）收成 `failed` + `resumable=false`——数据不会被写坏（这点设计得对），但用户的诊断成果与已消耗的 token 双双作废，且计费只能按保守预留结算。  
  - 普通 `celery` 队列路径：`health_check_all_systems`（`celery.py:14-19`，15s 一次）、`daily_health_reports`（`:21-24`）、通知重试等任务在 `acks_late=False` + SIGKILL 下**静默丢失**，没有任何补偿——巡检缺口不会被发现，也没有死信队列。  
  - 部署/发布：每次 `docker compose up -d` 或重启都可能落在任务执行中途，问题在规模化后会变成常态而非偶发。
- **业界做法**：容器里用 `exec` 形态的入口（`CMD ["uvicorn", ...]` 或 `tini`/`dumb-init` 做 init 转发信号），Compose 显式设 `stop_grace_period` 且大于应用自身的 graceful 时间；Celery 常规做法是 `task_acks_late=True` + `task_reject_on_worker_lost=True` + `worker_prefetch_multiplier=1` + 为长任务设 `soft_time_limit`（本项目已正确设了 `:33` 的 prefetch=1）；有状态迁移用 `depends_on: service_completed_successfully` 的独立 migrate service 而不是 `&&` 串联。
- **可采纳性**：easy。改动面很小且互不耦合：compose 拆出 migrate service（或改用 `bash -c` + `exec`）、加 `stop_grace_period: 60s`、celery 配置加三行。真正需要论证的只是"宽限期设多长"这一个参数。
- **建议**：① `deploy/docker-compose.yml` 把 `:41` 改为 `command: ["bash","-c","alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 20"]`（`exec` 让 uvicorn 成为 PID 1）；② 给 api/worker/diagnosis-worker/beat 加 `stop_grace_period: 60s`，且 `diagnosis_shutdown_grace_seconds` 与容器宽限对齐；③ `backend/app/workers/celery.py` 增加 `task_acks_late=True`、`task_reject_on_worker_lost=True`，并给定时任务加 `soft_time_limit`；④ 对巡检类任务补一个"漏跑检测"（用 `metric_samples` 的时间戳断档反推）作为兜底。

### [high] 容器化前端 Nginx 的 API 前缀白名单缺 `/incidents` 与 `/workflows`，核心页面会拿到 SPA 的 HTML 而不是 JSON

- **证据**：`deploy/frontend.nginx.conf:6` 的正则是 `^/(auth|analytics|audit|collector|collectors|systems|messages|openapi|feishu|healthz|ws)(/|$)`，其中**没有** `incidents` 与 `workflows`；未匹配的请求落到 `:17-19` 的 `location / { try_files $uri $uri/ /index.html; }`，即返回 SPA 的 `index.html`。而前端在这些完全合法的路径上发请求：`frontend/src/pages/MessagesPage.vue:404-406` 在同一批 `Promise.all` 里同时请求 `/incidents`、`/incidents/summary`、`/workflows/pending`；`frontend/src/pages/ApprovalsPage.vue:94,100` 请求 `/workflows/pending` 与 `/workflows`；`frontend/src/pages/MonitoringCenterPage.vue:62` 请求 `/incidents/summary`。后端路由确实存在：`backend/app/api/incidents.py:21` 与 `:24` 定义 `/incidents/summary`，`backend/app/api/workflow.py:16` 定义 `/workflows`。对照组是开发栈的 `Caddyfile:16-17`——它的 `@api` 匹配器**同时包含** `path /workflows*` 与 `path /incidents*`，所以宿主直跑拓扑正常、容器化拓扑坏。另需注意，`MessagesPage.vue:469,488` 还会 `POST /incidents/{id}/{action}`，而 Nginx 对静态文件不接受 POST，会直接返回 405。
- **风险**：容器化部署（README:27 明确宣称 `deploy/docker-compose.yml` 管理 api/worker/beat/Web）下，控制台首页/消息中心/审批页会静默拿到 `text/html`：axios 解析出的 `data` 是字符串，随后的 `.map`/`.length` 抛错，页面表现为白屏或数据空缺，而 HTTP 状态是 **200**，不产生任何错误日志、也不会被任何健康检查或监控发现。这属于典型的"静默错内容"，比 404 更难排查；同时也说明容器化拓扑从未被真正验证过——这正好解释了为什么它还带着这个缺陷。
- **业界做法**：单一入口下的 API 分流不应维护手工前缀白名单（每加一个 router 就多一个漏配点），而应用 `/api` 统一前缀（`proxy_pass http://platform-api:8000`）或由网关/Ingress 按服务发现路由；Nginx 配置应进 CI 做 `nginx -t` + 冒烟测试（真实请求每个 API 前缀断言 `content-type: application/json`），或在部署后跑一次 API 契约测试（本仓库已有 `scripts/export-ops-contract.py` 生成 OpenAPI，天然可以据此遍历路径断言可路由）。
- **可采纳性**：easy。最小修复是往正则里补两个词；结构性修复是统一 `/api` 前缀（涉及前端 `baseURL`、`Caddyfile`、`nginx` 与后端路由挂载点，属于 moderate 但收益更持久）。
- **建议**：① 立即在 `deploy/frontend.nginx.conf:6` 的正则中加入 `incidents`、`workflows`、`internal`；② 更推荐把后端路由统一挂到 `/api` 前缀（`backend/app/main.py:148-149` 加 `prefix="/api"`），前端 `frontend/src/api/index.js:4-8` 的 `baseURL` 设 `/api`，然后两端反代都退化成一条 `location /api/`；③ 在 `scripts/build-frontend-prod.sh` 之后加一个针对所有 OpenAPI 路径的容器化冒烟测试，防止同类漏配复发。

### [medium] `/healthz` 恒返回 ok 却被当作 readiness 门禁，worker/beat/web 完全没有健康检查

- **证据**：`backend/app/main.py:152-154` 的实现是不带任何依赖探测的 `return {"status": "ok"}`。它被用作 `deploy/docker-compose.yml:48` 的 `platform-api` 容器健康检查，并进一步成为 `:67-69`（platform-worker）、`:87-89`（platform-beat）、`:99-101`（platform-web）的 `depends_on: condition: service_healthy` 前置条件；同一个路径还被 Caddy 当作 API 路由（`Caddyfile:17`）与 `scripts/install-cloudflared-launchd.sh:32` 用作公网健康探测。反观 `platform-worker`/`platform-diagnosis-worker`/`platform-beat`/`platform-web` 自身，在 `deploy/docker-compose.yml:53-101` 内没有任何 `healthcheck` 块。
- **风险**：进程活着但依赖已坏时（PG 连接池耗尽、Redis broker 不可达、`init_db()` 之后的连接被防火墙切断），`/healthz` 依然 200，于是：① Docker 不会重启真正不可用的 api 容器；② worker/beat/web 会在这个"假健康"之上被放行启动，把"依赖顺序保护"变成形式；③ 外部探活（cloudflared 安装脚本、任何基于该路径的监测）持续报告健康，故障对运维不可见，只表现为用户侧 5xx。缺失 worker/beat 的健康检查还意味着"worker 卡死但进程存活"这种最常见的队列故障无法被编排层识别。
- **业界做法**：区分 liveness 与 readiness——`/livez` 只证明事件循环未死锁，`/readyz` 主动探测所有关键下游（DB 执行 `SELECT 1`、Redis `PING`、broker 连接）并返回 503；K8s 用 readinessProbe 摘流量、livenessProbe 触发重建，Compose 里对应 `healthcheck` 用 `curl -f /readyz` 并给 worker 配 `celery inspect ping` 之类的活性检查；`depends_on: service_healthy` 应指向真正表达"可用"的探针。
- **可采纳性**：easy。新增一个带依赖探测的 `/readyz`、把 compose 里所有 `test:` 指向它、给 worker/beat 补 `healthcheck`（`celery -A app.workers.celery.celery inspect ping -d celery@$HOSTNAME`），都是局部改动，不改架构。
- **建议**：在 `backend/app/main.py` 增加 `/readyz`（DB `SELECT 1` + Redis `PING`，任一失败返回 503，探测结果带 `Cache-Control: no-store`），保留 `/healthz` 作为纯 liveness；把 `deploy/docker-compose.yml:48`、`:66`（worker）等处的 `test:` 改为 `/readyz`，并给 worker/diagnosis-worker/beat 各加 `healthcheck`；同步更新 `scripts/install-cloudflared-launchd.sh:32` 用 `/readyz` 做公网验收。

### [medium] 平台 Redis 既无持久化也无数据卷，重启即丢未消费任务，且 broker 与 result backend 共用同一实例

- **证据**：`deploy/docker-compose.yml:18-26` 的 `platform-redis` 只有 `ports: 6380:6379` 与一个 healthcheck，**既没有 `volumes:` 也没有 `command: --appendonly yes`**（对比根 `docker-compose.yml:28-29` 至少挂了 `real_redis_data`）；而 `:34-35` 把 `CELERY_BROKER_URL` 与 `CELERY_RESULT_BACKEND` 都指向这唯一的实例（仅 `/0` 与 `/1` 分库）。`backend/app/workers/celery.py:34-35` 只设了连接超时（2s），没有设置任何持久化或重投策略，`task_acks_late` 保持默认 False。
- **风险**：Redis 容器一旦重启/被 OOM Kill/`docker compose up -d` 重建（无卷意味着容器重建 = 数据全清），队列中所有尚未被 ACK 的任务直接消失：`health_check_all_systems` 的巡检缺口、`daily_health_reports` 的漏发、通知重试丢失，而且因为预取即刻 ACK，Celery 自己也不知道丢过什么，没有任何重投或死信兜底。诊断路径因为另有"DB outbox + `dispatch_pending` 每 2s 对账"（`main.py:97-105`）而基本免疫——这正是设计上做得好的地方，但也反衬出普通队列完全没有这层保护。同时 broker 与 result backend 共用单实例，Redis 抖动会同时影响投递与结果读取。
- **业界做法**：Celery broker 用 `--appendonly yes` + `appendfsync everysec` 并挂持久卷，或直接换 RabbitMQ/Redis Sentinel；broker 与 result backend 在容量/可用性敏感时分离部署；关键任务开启 `task_acks_late` + `task_reject_on_worker_lost` 并配死信队列（RabbitMQ DLX 或应用级重试表），保证"至少一次"。
- **可采纳性**：easy。给 `platform-redis` 补 `command: ["redis-server","--appendonly","yes","--appendfsync","everysec"]` 与命名卷是一行到三行的改动；把 result backend 拆到独立实例是配置级改动；ack 语义已在上一节建议中覆盖。
- **建议**：在 `deploy/docker-compose.yml:18-26` 补 `command` 与 `volumes: platform_redis_data:/data`，并在 `volumes:` 段声明该卷；把 `CELERY_RESULT_BACKEND` 指向独立实例或改为 `rpc://`（结果不被消费时无需落 Redis）；对 `health_check_all_systems` 这类幂等巡检任务补"漏跑检测"以避免依赖 broker 可靠性。

### [medium] 可观测性只有基础设施一层：平台自身无指标、无告警规则、无日志轮转，且日志每次启动即被清空

- **证据**：三条独立证据。① **无平台指标**：全仓库（排除 `.venv`）搜不到 `prometheus_client`，`backend/app/api/metrics.py:10-25` 是"查询被监控系统的指标"而非暴露自身指标，后端没有任何 `/metrics` 端点；`prometheus/prometheus.yml:8-31` 的四个 scrape job 全部是基础设施 exporter（prometheus / node-exporter / kafka-exporter / mysqld-exporter），没有对 `platform-api`/worker 的抓取。② **无告警**：`docker-compose.yml:98-114` 只有 prometheus，两份 compose 里 `grep -c alertmanager` 均为 0，仓库里没有任何 `rules/*.yml` 或 `alerting:` 段——Prometheus 抓了数据但没有任何人去判断异常，唯一的异常感知来自应用内 `HeartbeatCheckScheduler`（`prometheus/prometheus.yml:4-7` 的注释）。③ **日志无轮转且启动即截断**：`scripts/stack-launcher.sh:127` 每次 `start_service` 都执行 `: > "$log"` 把上一次的日志清零；`do_logs()`（`:554-561`）只是 `tail -f`，没有任何轮转/保留逻辑；实测 `.dev-stack/logs` 已累积 224MB（无上限），而两份 compose 都未配置 `logging` 驱动（`grep -n "logging|max-size"` 零命中），Docker 默认 `json-file` 同样无大小上限。
- **风险**：  
  - 故障发现完全依赖用户报障：API 5xx 率、诊断失败率/token 消耗、Celery 队列积压、DB 连接池耗尽这些最关键的信号既不被采集也不被告警；Prometheus 部署了却不产生任何行动，属于"有监控无观测"。  
  - 排障能力被自己销毁：进程异常退出后重启，日志从 0 开始，崩溃前的栈与上下文永久丢失——而 `:127` 的截断恰好发生在最需要历史日志的"重启恢复"场景。  
  - 磁盘会被打满：224MB 且无上限的宿主日志，加上无上限的容器 `json-file`，最终会以"磁盘写满 → 数据库写入失败/容器 ExitCode 137"的形式爆掉，且爆之前没有任何预警。
- **业界做法**：应用暴露 `/metrics`（`prometheus_client` 或 `prometheus-fastapi-instrumentator`）并纳入 scrape；告警走 Alertmanager + 分级路由（P0 电话/P1 飞书），配 SLO 与错误预算；日志用 `logrotate`/`loki`/`fluent-bit` 采集，容器 `logging: driver: json-file, options: {max-size: 10m, max-file: "5"}`，本地文件按天滚动保留 N 天并异地归档；关停时不要截断而在文件名里带启动时间戳。
- **可采纳性**：moderate。补 `/metrics`、Alertmanager 与 `max-size` 都是标准动作，但要让新指标真正有人看、告警阈值调准，需要先定义 SLO 与值班路径，这部分是组织成本而非技术改造。日志轮转本身是 easy，建议先做。
- **建议**：① `backend/app/main.py` 挂 `prometheus-fastapi-instrumentator` 暴露 `/metrics`（含诊断成功/失败计数、诊断耗时直方图、Celery 队列深度、DB 连接池使用率）；② `prometheus/prometheus.yml` 增加 `job_name: platform-api`，根 `docker-compose.yml` 增加 Alertmanager service 与 `prometheus/rules/*.yml`（先落 5 条：API 5xx 率、诊断失败率、队列积压、磁盘 >85%、容器 restart 次数）；③ `scripts/stack-launcher.sh:127` 改为 `$LOG_DIR/$name-$(date +%Y%m%d-%H%M%S).log` 并挂 `logrotate`（保留 7 天），两份 compose 补 `logging.options.max-size/max-file`。

### [medium] Nginx 默认 1MB 请求体上限与 API 声明的 2MB 日志上传冲突，且 API 侧对上传大小没有任何校验

- **证据**：`backend/app/api/openapi.py:414` 与 `:443` 的两个上传端点参数声明为 `file: UploadFile = File(..., description="log.json 或文本日志，最大 2 MB")`，实现（`:424` `raw = await file.read()`）把整个文件一次性读进内存，路径上**没有任何大小判断**。容器侧的 `deploy/frontend.nginx.conf` 全文没有 `client_max_body_size`，即 nginx 默认 `1m`；同一路径的用户入口若走 `Caddyfile`（Caddy 默认无请求体大小限制）则不受影响。
- **风险**：① 合法流量被拒：1–2MB 的日志文件经容器化入口上传会得到 nginx 的 `413 Request Entity Too Large`，而 API 文档和调用方（`openapi.py:414` 的描述）都承诺支持 2MB——这是规格与实现不一致导致的确定性功能缺陷，且只在容器化拓扑出现，又一处开发栈与生产栈的行为分歧。② 无上限读入内存：任何能绕过 nginx 直连 API 的调用方（内网、隧道上的 `webhook.*` 域名直指 8000 端口，见 `~/.cloudflared/config.yml:13-14`，绕过了 Caddy 与 Nginx）都可以上传任意大小的文件，`await file.read()` 会把全部内容读进 API 进程内存——若干个并发大文件即可把单进程 API 打爆（`deploy/docker-compose.yml` 里也没有任何内存限制兜底）。这与代码库自身"输入必须有界"的风格（如 `sandbox.py:24` 的 `MAX_OUTPUT_BYTES`、`:26` 的 `MAX_FILE_BYTES`）形成明显反差。
- **业界做法**：入口网关（Nginx `client_max_body_size` / Ingress `proxy-body-size`）与应用层**双重**限制，应用层按流式方式校验并截断（FastAPI 用 `request.stream()` 边读边计数，超限立即 413，或读前检查 `Content-Length` + 分块计数兜底）；上传接口统一走 `/api` 前缀并限制来源，避免直连绕过网关。
- **可采纳性**：easy。Nginx 加一行 `client_max_body_size 4m;`，API 加一个大小校验辅助函数即可，不涉及结构变化。
- **建议**：① `deploy/frontend.nginx.conf` 的 server 段加 `client_max_body_size 4m;`（留出 multipart 边界余量）；② 在 `backend/app/api/openapi.py:424` 之前按流式读取并累计字节数，超过 2MB 立即返回 413 且不再继续读，同时把上限提到 `config.py` 作为可配置项；③ 关闭隧道上直指 8000 端口的 `webhook.*` 入口或让它也经过统一网关，消除"绕过反代"的旁路。

### [medium] 中间件使用明文弱口令且端口全绑 0.0.0.0，macOS 宿主上等于对局域网开放数据库与消息队列

- **证据**：根 `docker-compose.yml:7-10` 硬编码 `MYSQL_ROOT_PASSWORD: rootpass`、`MYSQL_PASSWORD: opspass`；`:15` 把 `3306:3306` 直接发布到宿主所有接口；`:27` 的 Redis `6379:6379` **无密码**（`:24-34` 无 `--requirepass`）；`:39` 的 Kafka `9092:29092` 同样是明文 PLAINTEXT 且发布到所有接口（`:49` 的 `KAFKA_LISTENER_SECURITY_PROTOCOL_MAP` 全为 PLAINTEXT）；`:131` 的 Ollama `11434:11434` 同理。`deploy/docker-compose.yml:4-5, 20-21` 的 PG/Redis 同样绑定 `0.0.0.0`（口令 `ops/opspass`，无 TLS）。`scripts/stack-launcher.sh:146-155` 还额外以 `-p 3307:3306`、`-p 5672:5672 -p 15672:15672` 起了 algp 的 mysql（root/root）与 rabbitmq（`neu/123456`，管理台 15672 暴露）。
- **风险**：这些端口绑定在宿主机的所有接口上（Colima/Docker Desktop 的默认端口转发行为），因此同一局域网/同一咖啡馆 Wi-Fi 内的任何主机都可以用 `root/rootpass` 直连到含有真实业务数据的 MySQL、无密码直连 Redis（可写 Celery 队列、可篡改缓存）、以及 RabbitMQ 管理台。这类"开发机带着生产数据接入不可信网络"的场景在运维平台里尤其危险：被监控系统的只读库凭据也在 `descriptors` 里（`ARCHITECTURE.md:60-65`），且 15672 管理台用弱口令即可拿到全部队列内容。宿主也没有 `restart` 策略，一旦这些容器被入侵后重启，`docker-compose.yml:11` 的注释表明曾有 OOM 事件，攻击者可借助不稳定状态掩盖痕迹。
- **业界做法**：默认不发布端口，只在同一 compose 网络内用 service 名互相访问（需要宿主访问时绑 `127.0.0.1:3306:3306`）；口令从 `.env`/secret manager 注入而非写进 compose，且开发环境也用随机值（`openssl rand -base64 24`）；Redis 启用 `requirepass`，Kafka 至少 SASL/PLAIN 或干脆只在内网监听；RabbitMQ 关闭 guest/弱口令并限制管理台来源。
- **可采纳性**：easy。把 `3306:3306` 改成 `127.0.0.1:3306:3306` 之类的回环绑定、口令外置到 `.env`（`.gitignore:2` 已忽略）都是低风险改动；要论证的只是"是否需要从局域网其它设备访问这些中间件"——从代码看不需要（所有调用方都在本机或同一 compose 网络内）。
- **建议**：① 两份 compose 中把所有需要宿主访问的端口映射改为 `127.0.0.1:<port>:<port>`，不需要宿主访问的（platform-redis、exporter 等）直接删掉 `ports:`；② `MYSQL_ROOT_PASSWORD`/`MYSQL_PASSWORD`/`POSTGRES_PASSWORD` 改为 `${MYSQL_ROOT_PASSWORD:?...}` 形式并写入 `.env`，Redis 补 `--requirepass ${REDIS_PASSWORD:?}`；③ `stack-launcher.sh:146-155` 的 algp mysql/rabbitmq 也做同样处理（回环绑定 + 口令来自 `.dev-stack/` 下的 600 权限文件），并关闭或限制 15672 管理台的暴露。

---

## 结论摘要

| # | 严重度 | 结论 | 可采纳性 |
|---|---|---|---|
| 1 | critical | 业务/平台数据无任何备份机制，单盘故障即全量丢失 | moderate |
| 2 | high | 容器化部署下沙箱静默降级为进程内执行 agent 代码，且沙箱镜像无构建入口 | moderate |
| 3 | high | 全栈无自愈：容器无 restart、宿主进程无监管、单机单点对外服务 | easy（自愈）/ hard（HA） |
| 4 | high | 系统级 AIOPS_TOKEN 明文硬编码并已提交进 git 历史 | easy |
| 5 | high | 容器化拓扑下优雅关停被 PID 1 与缺失的 stop_grace_period 绕过，任务静默丢失 | easy |
| 6 | high | Nginx 反代前缀缺 /incidents 与 /workflows，核心页面返回 SPA HTML | easy |
| 7 | medium | /healthz 恒 ok 却作为 readiness 门禁，worker/beat/web 无健康检查 | easy |
| 8 | medium | 平台 Redis 无持久化无卷，broker 与 result backend 共用单实例 | easy |
| 9 | medium | 可观测性只有基础设施层：无平台指标、无告警规则、日志无轮转且启动即截断 | moderate |
| 10 | medium | Nginx 1MB 默认请求体上限与 API 声明的 2MB 上传冲突，API 侧无大小校验 | easy |
| 11 | medium | 中间件明文弱口令且端口全绑 0.0.0.0 | easy |

**贯穿性问题**：`deploy/docker-compose.yml` 描述的容器化平台栈（api/worker/diagnosis-worker/beat/web）**没有任何自动化入口会启动它**——`scripts/stack-launcher.sh:203` 只用到其中的 `platform-db`，其余五个服务是"文档里存在的部署方式、实际上从未被验证"。这直接导致了发现 #6（反代前缀漏配）和 #5（PID 1 破坏优雅关停）能长期潜伏。建议要么把 `deploy/docker-compose.yml` 补全为可运行的一键栈并纳入冒烟测试，要么明确标注为未维护的参考实现并从 README:27 的承诺中移除，避免运维人员按文档部署却得到一个静默错误的系统。

---

## 对抗性复核

- **业务与平台数据没有任何备份机制，单盘故障即全量丢失** → confirmed（修正严重度：critical）
  核实：`scripts/prepare-local-stack.py:75` 只在 `revision != head` 时进入备份分支，`:79` 写入 `.dev-stack/backups`（与源库同盘同机同用户），且该目录当前**不存在**（`ls .dev-stack/backups` 报 no such file），即从未产出过备份；全仓 grep `pg_dump|mysqldump|xtrabackup|WAL-G|pgBackRest` 除该文件零命中，无 restore 脚本；`crontab -l` → "no crontab"；`~/Library/LaunchAgents` 只有 cloudflared/feishu-bot，均非备份；`tmutil destinationinfo` → **"No destinations configured"**，连 Time Machine 兜底也没有。critical 成立，无需下调。
  但报告有一处证据链错误：`stack-launcher.sh:278` 指向 `jdbc:mysql://127.0.0.1:3307/algp`（algp-mysql 容器，见 `:147` 的 `-p 3307:3306`），**不是**根 compose 的 3306 实例；根 compose 的 `opsdb`（`docker-compose.yml:8`）在本仓无 init/schema、无任何写入方（只有 mysqld-exporter 读它），称其为"被监控系统的真实业务数据"缺乏依据。真正无备份且含真实数据的是 `algp_mysql_data`（3307）。这不削弱结论，反而说明未受保护的库更多。

- **容器化部署下代码沙箱静默降级为在 API 进程内执行 agent 生成的代码，且沙箱镜像无任何构建入口** → overstated（修正严重度：medium）
  机制核实无误：`sandbox.py:63-65` 仅以"镜像配置非空 + `shutil.which("docker")`"为判据，`:126` 回落 `settings.sandbox_python or sys.executable`；`backend/Dockerfile:1-14` 基于 `python:3.12-slim`（无 docker CLI），`deploy/docker-compose.yml:33-40` 未传任何 `SANDBOX_*`，且 Dockerfile 只 `COPY backend shared`（`:8-9`）故 `.env` 不进镜像 → 容器内 `sandbox_container_image=""` → 在 API 容器里执行 agent 代码，属实。全仓 grep `aiops-sandbox` 只有 `deploy/sandbox/Dockerfile:2` 注释与 `.env:56`，确无构建入口；`:66-72` 也不做 `docker image inspect`，属实。
  但定性与放大有问题：① 不是"进程内"，而是同容器内的**子进程**（`sys.executable -I`），仍套 `_preexec` 的 `RLIMIT_CPU/AS/FSIZE/NPROC`（`sandbox.py:134`）、白名单清空环境（`:116-124`）与用完即毁临时目录；② 维护者在 `sandbox.py:10-11` 与 `ARCHITECTURE.md:325-329` 明确写"这不是安全沙箱""安全模型建立在只读语义+白名单+审计上，而不是进程隔离"——进程隔离从来不是该平台的既定安全边界，报告把它当作被静默移除的防线，属于把"有意取舍"重判为缺陷；③ `run_python_analysis` 另有基于**用户原始问题**的意图闸门（`tools.py:515` `_wants(intent_text, _RUN_HINTS)`），被污染的日志本身不足以放行；④ 实际在跑的宿主拓扑用的是专用 venv（`.env:53 SANDBOX_PYTHON=…/.dev-stack/sandbox-venv/bin/python`，该 venv 存在，120M，含 numpy/matplotlib），不是 API 解释器；⑤ 受影响的容器拓扑从未被启动（`stack-launcher.sh:203` 只起 `platform-db`）。
  未被夸大的部分是可用性面，且比报告写得更广：`_docker_argv` 只要 docker 二进制在 PATH 就选容器路径，不校验镜像/daemon，故宿主上 colima 未启动或镜像缺失时 `run_python_analysis` 直接失败而**不会**退回 venv。综合记 medium。

- **全栈没有任何自愈能力：容器无 `restart` 策略、宿主进程无监管、整套生产跑在一台笔记本上** → confirmed（修正严重度：high）
  核实：`grep -c "restart:" deploy/docker-compose.yml` = **0**（api/worker/diagnosis-worker/beat/web 全是默认 `no`）；根 compose 唯一 `restart: unless-stopped` 在 `docker-compose.yml:139` 的 ollama（`profiles: ["llm"]`，`stack-launcher.sh:191` 还会主动 stop）；`stack-launcher.sh:531-544` 的 `while true` 只 `kill -0` + 打印 `⚠ $nm 已退出`，确实不重启；`~/.cloudflared/config.yml:11-14` 把 aiops/webhook 域名指回本机。核心成立，high 维持。
  但标题的绝对化用词不成立，应改："全栈没有任何自愈能力"被证伪——`stack-launcher.sh:145,151` 给 algp-mysql/algp-rabbitmq 加了 `--restart unless-stopped`，`docker-compose.yml:139` 的 ollama 也有；`~/Library/LaunchAgents/com.aiops.feishu-bot.plist:16-17` 同样有 `KeepAlive`（虽然它指向的 `feishu_bot/server.py` 在本仓已不存在，属失效旧 agent）。因此准确的表述是"平台自身五个服务与宿主 backend/worker/beat 无任何自愈"，而非"全栈"。

- **系统级 API 令牌以明文硬编码并已提交进 git 历史** → confirmed（修正严重度：high）
  核实：`scripts/stack-launcher.sh:282` 明文内联 `AIOPS_TOKEN='sys_********（已脱敏）'`；`git show HEAD:scripts/stack-launcher.sh` 第 280 行同一值；`git log -S` 显示引入自提交 `94a5943`；`git branch -r --contains 94a5943` → `origin/feat/multi-tenant-aiops-platform`，`git grep` 在该**远端分支**同名文件同位置命中，即密钥已推到 GitHub（`git remote -v` → `git@github.com:sidongzhang/ai-ops-agent.git`），比报告"已进 git 历史"的程度更高。`security.py:135 generate_system_token()` 生成 `sys_` 前缀，`openapi.py:52-79` 用它做系统级鉴权（`X-System-Code` + scope + 可选 `allowed_ips`），确非用户 JWT。`.dev-stack/algp.token`（0600，被 `.gitignore:20` 忽略）里是同一值，说明团队已知正确做法。无夸大。

- **容器化拓扑下优雅关停链路被 PID 1 与缺失的 stop_grace_period 整体绕过，普通队列任务静默丢失** → overstated（修正严重度：medium）
  机制核实无误：`deploy/docker-compose.yml:41` 是 `sh -c "alembic upgrade head && uvicorn …"`，PID 1 为 sh；两份 compose `grep -c stop_grace_period` 均为 0；`celery.py` 未设全局 `acks_late`。
  但"普通队列任务静默丢失"忽略了三点既有缓解：① 诊断队列本身是 `backend/app/workers/diagnoses.py:5` 的 `acks_late=True, reject_on_worker_lost=True`，worker 被杀会重投，报告正文只把它算成"停 running"却漏了这条；② 被点名的普通队列任务 `health_check_all_systems` 由 `celery.py:14-19` **每 15s** 触发且幂等，丢一次下一个 tick 即覆盖，只有 `daily_health_reports` 会造成一天缺口；③ 实际在跑的宿主拓扑不走这条路径——`stack-launcher.sh:128` 用 `exec` 启动 uvicorn/celery，`:457-465` 以 SIGTERM（`kill_tree`/`pkill`）关停，Celery 走 warm shutdown，信号能到达。被绕过的只是从未启动的容器拓扑（`stack-launcher.sh:203` 仅起 `platform-db`）。故降为 medium。

- **容器化前端 Nginx 的 API 前缀白名单缺 /incidents 与 /workflows，核心页面拿到 SPA HTML 而非 JSON** → overstated（修正严重度：medium）
  缺陷本身属实：`deploy/frontend.nginx.conf:6` 的白名单确无 `incidents|workflows`，`:17-19` 落到 SPA，返回 **200 + text/html**；前端确实在这些路径发请求（`MessagesPage.vue:404-406`、`ApprovalsPage.vue:94,100`、`MonitoringCenterPage.vue:62`、`App.vue:73`），`frontend/src/api/index.js:4-7` 的 `baseURL` 为空串即同源相对路径；POST `/incidents/{id}/{action}` 打到静态 location 会 405。
  但两处必须修正：① 引用错误——`backend/app/api/workflow.py:16` 是 `router = APIRouter(prefix="/systems")`，`/workflows`、`/workflows/pending`、`/workflows/actions` 实际定义在 `backend/app/api/workflow_pending.py:15,20,31`，`workflow.py` 只有 `/systems/{system_id}/workflow`；② 严重度被夸大：该容器拓扑没有任何启动入口（`stack-launcher.sh:203` 只起 `deploy/docker-compose.yml` 的 `platform-db`，全仓 `grep deploy/docker-compose` 仅此一处），而线上入口 `Caddyfile:11,16` 同时包含 `/workflows*` 与 `/incidents*`，当前用户可见影响为 0，属潜在缺陷。若真采纳容器栈则上线前必修，但不该按 high 计。
