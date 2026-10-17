# 安全与权限 体检

## 现状设计

认证分成三条互不复用的链路：人用 OAuth2 password flow 换 JWT（`backend/app/core/security.py:112`、`backend/app/api/auth.py:35`），租户身份在每次请求时从数据库 `User.org_id` 现读而不是采信 token 里的 `org` 声明（`backend/app/core/deps.py:21`）；采集器用 `X-Collector-Key` 明文头，服务端只存 sha256 后按哈希查表（`backend/app/core/deps.py:38`、`backend/app/services/collectors/service.py:24`）；外部业务系统用平台签发的 `sys_` token，支持 `scopes` / `allowed_ips` / `expires_at` / `status` 四重约束并绑定 `X-System-Code`（`backend/app/api/openapi.py:56`）。授权是两层：路由层 `require_operator` 区分「只读账号」与「可改运维配置」的角色（`deps.py:31`），Agent 工具层则由 `ToolGateway` 做身份/范围/工具许可/策略四级校验，并强制写审计（`backend/app/agent/harness/gateway.py:77`）。凭据字段靠字段名白名单自动加密（Fernet）与响应脱敏（`security.py:42`、`security.py:83`），Redis/Kafka 只读策略被收敛到唯一实现 `shared/ops_policy.py`，供 Agent 工具、MCP 服务、采集器三处共用。Agent 的权限模型是「运行范围 + 每 run 临时能力令牌」：内部工具端点 `/internal/agent-tools/runs/{run_id}/calls` 不进 OpenAPI 文档，凭 240 秒过期的 `secrets.token_urlsafe(32)` 绑定到具体 run（`harness/bridge.py:22`、`api/internal_agent_tools.py:12`）。发给模型的上下文做了字段白名单投影与外部数据护栏，凭据与 infra 图不进提示词（`harness/context_data.py:61`）。代码执行走一次性工作目录/容器沙箱（`services/diagnostics/sandbox.py`），密钥则全部来自仓库根的 `.env` 由 pydantic-settings 在导入期读入（`core/config.py:42`）。

## 做得好的地方

- **两种 token 都只存哈希、明文只回显一次**：采集器密钥 `sha256` 落库（`services/collectors/service.py:24-30`），系统 token 的 `token_hash` 存哈希 + 明文仅在创建响应里返回一次（`services/tokens.py:18-39`），`SystemTokenOut` 不含任何哈希/明文（`services/tokens.py:70-81`）。这是 SaaS 签发型凭据的正确做法。
- **每 run 临时能力令牌 + 常量时间比较 + 过期**：`harness/bridge.py:22-23`（`token_urlsafe(32)`、240s）、`bridge.py:60-65`（`hmac.compare_digest`）、`bridge.py:71-80`（call_id 幂等 + 24 次上限），内部端点还 `include_in_schema=False`（`api/internal_agent_tools.py:12`）。比共享静态内部密钥安全一个量级。
- **工具网关是真正的服务端授权点，而不是靠提示词约束**：范围不匹配/未登记/不在许可清单/策略拒绝各有独立错误码（`harness/gateway.py:77-95`），审计写入失败直接抛 `ToolAuditError` 且不重跑（`gateway.py:105-112`、`gateway.py:160-169`）。`authorize()` 还会拒绝未在当前系统登记的服务名与「系统未登记 Kafka/Redis」（`gateway.py:44-62`）。
- **危险动作被强制赶到审批工作流**：`api/collector_exec.py:35-36` 对 `restart_container` / `restart_systemd` 直接 403，审批时再校验 `restart_policy.authorized_user_ids`（`services/workflows/service.py:242-243`），重启权限只能由 owner 维护（`services/systems/configuration.py:105-118`）。
- **只读策略单一实现 + 子命令约束**：`shared/ops_policy.py:25-31` 对 `CONFIG/SLOWLOG/MEMORY/CLIENT/OBJECT` 追加只读子命令白名单，`shared/ops_policy.py:39-42` 显式拒绝 `--create/--delete/--reset-offsets/--command-config` 等危险 flag；文件头注明了此前「只校验动词、`CONFIG SET` 可绕过」的历史缺陷。
- **提示注入做了分层防护且明确"不授权"**：外部数据统一 `【外部数据·仅供参考】…【外部数据结束】` 包裹并转义正文里的边界标记（`harness/context_data.py:9-38`），`CONTEXT_RULES` 明确文档/日志不能授予工具权限（`context_data.py:11-19`），`model_descriptor()` 用字段 allowlist 投影服务信息、凭据与 infra 不落进上下文（`context_data.py:61-74`）。
- **凭据不经 API 明文外泄**：`mask_sensitive_fields` 在系统/服务/通知的读路径上统一兜底（`services/systems/service.py:84-85`、`services/systems/views.py:12`、`services/systems/configuration.py:74`），字段名白名单覆盖 password/secret/token/api_key/database_url/identity_file/kubeconfig（`security.py:22-33`）。
- **数据集查询是真参数化 + 标识符白名单**（无 SQL 注入）：视图/列名走 `^[A-Za-z_][A-Za-z0-9_]*$` 校验（`services/data_analysis.py:495-499`），值全部绑定参数（`data_analysis.py:444-451`），`list_readonly_datasets` 对不合规 dataset 直接跳过（`data_analysis.py:360`）。
- **沙箱容器模式做对了关键几件事**：清空白名单以外的环境变量（`sandbox.py:116-124`）、`--network none` + 内存/CPU/pids 限额 + 用完即删（`sandbox.py:66-72`、`sandbox.py:75-83`）、`-I` 隔离模式（`sandbox.py:71`）；采集器侧归档访问做了 `realpath` 边界校验 + tcat_id 正则清洗 + 大小上限（`collector/ws_client.py:300-313`、`ws_client.py:365-417`）。
- **采集器安装包下载要求重新出示明文密钥**，不是仅凭 JWT 就能把密钥打包带走（`services/collectors/service.py:127-128`），脚本里的 key/URL 也做了 `shlex.quote`（`service.py:151-157`）。
- **生产 compose 对密钥 fail-fast**：`deploy/docker-compose.yml:36-37` 用 `${JWT_SECRET:?set JWT_SECRET before production deployment}` / `${ENCRYPTION_KEY:?...}` 强制必填——方向完全正确（问题在于实际在跑的不是这条路径，见发现 1/5）。

## 发现

### [critical] 运行实例用的是源码里硬编码的 JWT 密钥，任何人可伪造任意用户令牌完成跨租户接管

- **证据**：`backend/app/core/config.py:51` `jwt_secret: str = "dev-secret-change-me-in-prod"`；签名/校验直接用它（`core/security.py:112-122`）；而仓库根 `.env`（`core/config.py:42` 的配置源）通篇没有 `JWT_SECRET`。实测当前进程内取值：
  - `settings.jwt_secret == 'dev-secret-change-me-in-prod'`
  - 用该常量自签的 token 被 `decode_access_token()` 接受：`{'sub': '1', 'org': 1, 'exp': ...}`
  - 启动脚本也未注入：`scripts/stack-launcher.sh:241` 的 `red_env` 只导出 CELERY/PYTHONUNBUFFERED，第 246 行以它启动 uvicorn（`:201` 那处 `JWT_SECRET=dev-placeholder` 只用于跑 alembic 的子进程）。
- **风险**：`get_current_user`（`core/deps.py:18-24`）只验签名与 `exp`，随后用 `sub` 去数据库取用户、以 `user.org_id` 作为租户边界。`User.id` 是自增主键，从 1 开始枚举即可。攻击者用公开在源码里的密钥签一个 `{"sub":"1","exp":...}` 就是该平台的 owner：可读取全部诊断报告与日志原文、拿到系统/服务的（脱敏前的）配置能力、下发采集器命令、创建系统 token。**这是无缓解的生产级越权**，而且因为 `PUBLIC_BASE_URL=https://webhook.tiancaizhaozhao.dpdns.org`（`.env:47`）说明实例已挂在公网隧道上（`Caddyfile:18-22` 把 `/auth/*`、`/systems*` 等全部反代到 8000）。
- **业界做法**：密钥由 KMS/Secret Manager 注入（AWS Secrets Manager、Vault KV、阿里云 KMS），进程不持有静态默认值；配置层做「非 dev 环境禁止默认值」的 fail-fast 校验（pydantic-settings 用 `secrets_dir` + 启动断言，或 `ENVIRONMENT != 'dev' and jwt_secret == DEFAULT: raise`）；JWT 加 `kid` 支持轮换、加 `jti` 支持吊销，短 TTL + refresh token；CI 里用 gitleaks/trufflehog 拦默认密钥。
- **可采纳性**：easy。改动面很小——生产环境注入一个强随机 `JWT_SECRET`（`deploy/docker-compose.yml:36` 已经强制），并在 `config.py` 增加「非 dev 且等于默认值就启动失败」的校验 + 启动日志告警。唯一代价是历史 token 全部失效（用户重登一次）。
- **建议**：1) 立刻生成 ≥32 字节随机串写入部署密钥管理并重启；2) `core/config.py` 增加 `model_validator`，当 `jwt_secret` 仍是默认值且 `log_level`/`env` 非 dev 时直接 `raise`；3) `create_access_token` 增加 `iat`/`jti`，`decode` 时校验 `typ`；4) 把 `stack-launcher.sh` 也改成从 `.env` 读 `JWT_SECRET` 并注入（否则本地开发仍在裸奔，且容易再一次把本地配置带到线上）。

### [critical] `.env` 里的真实密钥已经进了 git 历史，且同一把 key 至今未轮换

- **证据**：
  - `git show b77713f --stat` → `Initial commit` 提交了 `.env`（21 行），内容含 `DEEPSEEK_API_KEY=sk-8********（已脱敏）`、`FEISHU_APP_SECRET=1tNH********（已脱敏）`、`FEISHU_VERIFICATION_TOKEN=g5AO********（已脱敏）`。
  - 后续 commit `1f53781 "Add .gitignore, remove .env (secrets)"` 只是把文件从 HEAD 移除（`.gitignore:2` 现在忽略 `.env`），blob 仍在历史里，`git log --all --diff-filter=A --name-only` 依然能列出 `.env`。
  - **工作区 `.env:2` 就是同一把 DeepSeek key**，`:19-20` 是同一组飞书密钥——说明泄漏后没有做轮换。文件权限还是 `-rw-r--r--`（全员可读）。
- **风险**：任何拿到过仓库的人（曾 clone/镜像/fork、CI 缓存、离职同学的本地副本、未来的公开仓库）都持有可用的 LLM API key 与飞书自建应用密钥。DeepSeek key 可被用来烧掉账户余额（无额度护栏，平台自身也没有 per-key 花费告警）；`FEISHU_APP_SECRET` 可换 tenant_access_token，进而以机器人身份读写该应用的群消息、上传文件（`services/notifications/feishu.py` 正是用这对 app_id/secret 拉 token）；`FEISHU_VERIFICATION_TOKEN` 一旦泄漏，配合发现 3 的无签名校验，伪造 webhook 事件的门槛归零。
- **业界做法**：立刻轮换（视为已泄漏）优先于清历史；用 `git filter-repo --path .env --invert-paths` 或 BFG 重写历史并强推，通知全员重新 clone；GitHub/GitLab secret scanning + push protection + pre-commit gitleaks；密钥一律进 KMS/Secrets Manager，`.env` 只留 `.env.example` 占位（项目已有 `.env.example`，写得比 `.env` 规范）；API key 按服务拆权限并设额度上限与用量告警。
- **可采纳性**：moderate。清历史本身是机械操作，但会重写所有 commit hash、需要全组重新 clone，且若仓库曾被 fork/镜像则无法彻底清除——所以必须与密钥轮换绑定做，轮换本身还要改 `.env`、飞书后台、以及重新验证机器人（有停机窗口和联调成本）。
- **建议**：1) 今天轮换 DeepSeek key 与飞书 app secret/verification token，并给 LLM key 设额度上限；2) `git filter-repo` 清历史 + 在远端开启 secret scanning/push protection；3) 本地 `.env` 收敛为 600，并在 `.env.example` 里加注释禁止把真实值提交；4) 给 `services/notifications/feishu.py` 的主动推送加每日发送量上限，降低密钥泄漏后的滥用面。

### [high] 飞书 webhook 事件路径完全不校验签名/verification token，可被任意伪造触发一次完整 AI 诊断

- **证据**：
  - 路由本身无任何鉴权依赖：`backend/app/api/feishu_webhook.py:8-11` 直接 `await request.json()` 后交给业务层；路由枚举确认 `POST /feishu/webhook` 依赖列表为空；`Caddyfile:19` 的 `/feishu*` 将其暴露在公网隧道上。
  - `services/notifications/webhook.py` 里**唯一**的 token 校验在 URL challenge 分支：`webhook.py:79-86`，而且条件是 `if settings.feishu_verification_token and token and token != settings.feishu_verification_token` —— body 里不带 `token` 字段就直接放行。
  - 事件分支（`webhook.py:88-118`）只按 `header.event_type` 分发，不校验 `token`、不校验 `X-Lark-Signature`、也不处理 Encrypt Key 密文（全仓 grep `signature`/`Encrypt-Key` 无任何验签实现）。
  - `header.app_id` 缺失时回退平台默认应用（`webhook.py:110`），`resolve_app_secret` 找不到匹配也会回退 `settings.feishu_app_secret`（`webhook.py:51-59`）——攻击者不需要知道 app_id，构造最小 body 即可命中已注册系统。
  - 进入 `handle_message` 后，攻击者提供的 `text` 会被当作**用户原始问题**送入诊断（`webhook.py:66-72` → `diagnose(descriptor, text)`），同时它也是行动意图闸门的判定来源（`agent/diagnostics/tools.py:492`、`tools.py:515` 读的 `intent_text or question`）。
- **风险**：`POST /feishu/webhook` 带 `{"header":{"event_type":"im.message.receive_v1","event_id":"x"},"event":{"message":{"message_type":"text","content":"{\"text\":\"...\"}","chat_id":"oc_...","message_id":"om_x"}}}` 就能：1) 用平台的 LLM key 起一次完整诊断，反复调用即无上限烧钱（`diagnosis_session_token_budget` 只约束单次会话，`config.py:157`）；2) 用「把文件发给我」这类文本满足 `_SEND_HINTS`（`tools.py:81`），把归档文件取回成免登录下载链接（`api/diagnose.py:397`）并发送到 body 指定的 `chat_id`——若攻击者能把自己拉进该机器人的可见会话，就是一次外带；3) 污染诊断历史、长期记忆与日报（`services/monitoring/daily_report.py:51-79` 把采集器上报的 `detail` 文本原样拼进 LLM 提示，未经 `external_text` 包裹）。
- **业界做法**：用飞书/Lark 官方 SDK 的事件分发器，同时校验 verification token、`X-Lark-Signature`（`sha256(timestamp + nonce + encrypt_key + body)`）与 Encrypt Key 的 AES 解密；加时间戳窗口与 nonce 去重（现值 `already_seen` 是进程内 `deque(maxlen=200)`，`webhook.py:14-26`，重启即失效）；入口限流 + 事件来源 IP 白名单；把「谁触发了诊断」作为显式主体写审计。
- **可采纳性**：moderate。需要引入 Encrypt Key 配置（新增一个密钥项）、实现签名与解密（官方 SDK 最简单，但要评估依赖）并兼容明文/密文两种推送模式，还要加回归测试；改动集中在 `feishu_webhook.py` + `webhook.py`，不影响 Agent 主链路。
- **建议**：在 `webhook.py:process_webhook_payload` 最前面做强制验签：无 `X-Lark-Signature`/token 不匹配直接 401，绝不进入事件分发；把 `if ... and token and ...` 的短路写法改为「未配置 token 就拒绝」；`event_id` 去重落到 DB（Redis/PG）以保证重启后仍幂等；给该端点加 IP/速率限制。

### [high] 采集器密钥经 URL query 传输并明文落进访问日志，日志读权限等于采集器全权

- **证据**：
  - 服务端把它当 query 参数收：`backend/app/api/websocket.py:26` `key: str = Query(..., description="X-Collector-Key（采集器密钥明文）")`，随后按 sha256 查表（`websocket.py:30-32`）。
  - 客户端把它拼进 URL：`collector/ws_client.py:425` `url = f"{ws_url}?key={collector_key}"`。
  - 实测泄漏（现网日志里能直接读到可用密钥，出现多次）：
    ```
    .dev-stack/logs/backend.log:28:INFO:  127.0.0.1:60386 - "WebSocket /ws/collector?key=tWqkKnJ39Hw2UyLHLLEZJPR_EmGpmOKVOaiwNZ7wD3Y" [accepted]
    ```
    `.dev-stack/logs/` 下另有 `aiops-backend.log`、`collector-*.log` 同样命中 `key=`；`cloudflared.log` 里还能读到**另外两把**不同的采集器密钥（`grep -o "ws/collector?key=..." .dev-stack/logs/cloudflared.log` → `MIk1********（已脱敏）`、`jjD7********（已脱敏）_Qhaq-s`），说明这不是单点偶发，而是每个采集器都在泄漏。
- **风险**：访问日志通常被轮转、聚拢到 ELK/Loki、被更宽的人群（值班、SRE、第三方日志 SaaS）读取，且这类密钥**没有 TTL、没有轮换机制**（`Collector` 模型只有 `token_hash`，`models/collectors.py:12-21`，删除采集器才能吊销）。拿到这条日志的人即拿到该采集器在当前客户内网的全部能力：`run_readonly_query`（任意 SELECT，见发现 6）、`fetch_logs`/`search_logs`（读任一日志）、`archive_list/read/fetch`（读并以 base64 回传归档文件）、`run_redis_command`/`run_kafka_command`，以及连接后作为「合法采集器」持续接收平台下发的新命令。链路上 `Caddyfile:18` 的 `/ws*` 与 Cloudflare 隧道也会记录/转发同样的 URL。
- **业界做法**：握手阶段用 `Authorization` 头（`websockets.connect(..., extra_headers=...)`）或 `Sec-WebSocket-Protocol` 子协议承载凭据；若必须用 URL，则改为一次性、60 秒过期、与 collector_id 绑定的 ticket（换取方式是带 key 头的 POST）；访问日志对 query 做脱敏（uvicorn 无法按字段脱敏，需在前置代理或自建 access log 中间件里处理）；密钥支持轮换与吊销（`rotated_at`/`revoked_at`），并对同一密钥的异地使用告警。
- **可采纳性**：moderate。服务端改成 header/ticket 只是几十行，但**已部署在客户现场的采集器（`collector/run.py`、运维手工分发的 `collector-*.zip`）需要同步升级**，必须做双通道兼容 + 版本协商窗口；日志脱敏可以立刻做（低成本先止血）。
- **建议**：1) 先加 access log 脱敏中间件 + 清理现有日志文件，降低当前暴露面；2) 服务端同时接受 `Authorization: Bearer <key>` 与 `Sec-WebSocket-Protocol`，query 方式标记为 deprecated 并打告警；3) 采集器侧改为 extra_headers，随下一版安装包下发；4) 给采集器密钥加轮换/吊销字段与控制台「重新生成」入口。

### [high] `ENCRYPTION_KEY` 未配置使凭据加密整体变成 no-op，SSH/数据库/通知凭据明文落库

- **证据**：
  - `core/config.py:135-136` `encryption_key: str = ""`，注释即「空字符串 = dev 模式跳过加密」；`core/security.py:37-39` `_get_fernet()` 在 key 为空时返回 `None`；`security.py:58` 的加密条件 `elif f and _is_sensitive(k) and v ...` 因此恒为假，`encrypt_sensitive_fields` 原样返回明文。
  - 实测运行实例：`settings.encryption_key == ''`；仓库根 `.env` 里没有 `ENCRYPTION_KEY`（`grep -n ENCRYPTION .env` 无结果）。
  - 明文落库的写点（这些路径全部经过上面的 no-op 加密）：系统 infra 含只读业务库连接串 `services/systems/service.py:119`、`data_analysis.py:486`；服务 config 含 SSH 口令/identity_file/DB 串 `services/systems/lifecycle.py:56,97`、`systems/service.py:154`；通知配置含飞书 app_secret/webhook_url `services/systems/configuration.py:50,70`。
  - 反向对照：`deploy/docker-compose.yml:37` 用 `${ENCRYPTION_KEY:?...}` 强制必填——但当前实例由 `scripts/stack-launcher.sh:246` 启动（`red_env` 不含该变量），走的是不强制的那条路。
- **风险**：平台的业务价值就是替客户保管内网凭据（MySQL 账号口令、SSH key 路径、飞书 app_secret、redis/kafka 连接），现在这些以明文存在 `systems.infra` / `services.config` 的 JSON 列里。任一环节泄漏——DB 备份被拷走、给审计/BI 开的只读 DB 账号、pg_dump 落到共享盘、云厂商磁盘快照、`logs/` 之外的一次误导出——就直接等于拿到多个客户的生产内网横向移动凭据。而且因为「dev 模式」是静默降级（没有任何启动告警或界面提示），运维很可能以为已加密。
- **业界做法**：envelope encryption——DEK 由 KMS（AWS KMS / 阿里云 KMS / Vault Transit）托管，密文带 key id 前缀以支持轮换；至少要求 `ENCRYPTION_KEY` 在生产必填并在启动时 fail-fast；DB 侧叠加列级/TDE 与最小权限账号；对「密文列是否真的加密」加一致性巡检（启动时抽样检查 `enc:` 前缀覆盖率）。
- **可采纳性**：moderate。设置密钥本身是配置动作，但存量数据要迁移（读出明文→加密写回，且必须先用备份验证），密钥丢失会永久读不出旧配置，需要一次性停机/备份窗口 + 密钥托管方案；另外 `mask_sensitive_fields` 的读路径要确认不会把 `***` 回写覆盖真值（`configuration.py:48-50` 是先解密再整体重加密，风险点在 `update_notify`/`update_monitoring_config` 的合并逻辑是否可能吃到掩码值）。
- **建议**：1) 生成 Fernet key 进密钥管理并配置 `ENCRYPTION_KEY`；2) 写一次性迁移脚本把存量 `infra`/`config`/`notify` 中的敏感字段改成 `enc:` 前缀；3) `config.py` 加启动校验：非 dev 且 `encryption_key` 为空时 WARN（或直接 raise），并在健康检查里暴露 `credentials_encrypted: true/false`；4) 给密钥加 key id 与轮换流程。

### [high] `/systems/{id}/collector/exec` 不要求 operator 角色却可下发任意 SQL，采集器侧只靠关键字黑名单

- **证据**：
  - 端点只挂 `get_current_org_id`，没有 `require_operator`：`backend/app/api/collector_exec.py:28-38`；同文件只硬编码拦了 `restart_container` / `restart_systemd`（`:35-36`）。作为对比，连「服务探活测试」这种只读动作都要求 operator（`api/systems.py:166-172`），`services/tokens.py` 的 token 创建也要求 operator（`api/tokens.py:19`）。
  - 允许的命令集合包含自由 SQL 与中间件命令：`services/collectors/exec.py:14-17`（`run_readonly_query` / `run_redis_command` / `run_kafka_command` / `fetch_logs` / `search_logs` / `archive_*`），`body.args` 是无结构 `dict`（`schemas/collectors.py:42-45`），直接透传给采集器（`exec.py:53`）。
  - 采集器侧的全部「只读」保障是关键字黑名单：`collector/ws_client.py:123-126`
    ```python
    if not sql.lower().startswith("select") or ";" in sql.rstrip(";") or _DANGEROUS_SQL.search(sql):
        return {"ok": False, "result": "安全限制：只允许单条 SELECT 查询"}
    ```
    而 `_DANGEROUS_SQL`（`ws_client.py:31-34`）只覆盖 `insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|vacuum|attach|detach|copy`——**没有** `pg_read_file` / `lo_import` / `pg_ls_dir` / MySQL `INTO OUTFILE` / `LOAD_FILE`。`engine.connect()` 用的是注册进来的完整连接串（通常是业务账号，有写权限），只做了 `connection.rollback()`（`ws_client.py:141`），没有只读事务、也没有只读副本/只读账号约束。平台侧同一策略也是黑名单（`services/data_analysis.py:22-25`）。
  - 该路径的审计只有一行不含 SQL 与操作者：`services/collectors/exec.py:54` `log.info(f"[exec] system={system_id} cmd={body.cmd} ok=...")`。
- **风险**：组织内任何非 operator 账号（只读同事、外包、被钓鱼的账号）只要持有效 JWT，就能用 `run_readonly_query` 在**客户内网**的业务库上执行任意单条 SELECT——`SELECT * FROM users`、`SELECT * FROM <支付/订单表>` 全量拖库，脱敏逻辑（`_serialize_rows` / `mask_sensitive_fields`）在这条路径上不生效（它只属于 `data_analysis` 的数据集通道）。若该 DB 账号具备 FILE 权限，`SELECT ... INTO OUTFILE` / `LOAD_FILE()` 还能读写服务器文件。命令白名单里的 `run_redis_command`/`run_kafka_command` 虽受 `shared/ops_policy.py` 约束（做得好的地方已记），但 `fetch_logs`/`archive_fetch` 同样是「本不该给只读账号」的数据出口。此外基于 JWT 的越权（发现 1）会把这条路的入口直接开放给任何人。
- **业界做法**：命令级 RBAC——每个 `cmd` 映射一个 scope（`ops:read` / `sql:read` / `restart`），端点声明所需 scope，角色到 scope 的映射可配；自由 SQL 一律取消，只保留预定义数据集/视图白名单（平台里 `query_readonly_dataset` 已经是正确实现，`services/data_analysis.py:440-453`，应统一到这里）；下游强制只读：PostgreSQL `SET TRANSACTION READ ONLY` / `default_transaction_read_only=on`，MySQL 用只读账号或只读副本；SQL 语法用 AST/`sqlglot` 解析而不是正则黑名单；每次调用写审计（actor、cmd、SQL 哈希、行数）。
- **可采纳性**：moderate。要让 `collector/exec` 收敛成 scope 化的能力，需要给角色/权限模型加一层（当前只有 `role ∈ {owner, operator}`，`deps.py:31-35`），并且下掉自由 SQL 会影响前端调试入口与 Agent 的降级路径（`_remote_query_executor`，`services/data_analysis.py:457-482`），需要同步改造调用方与回归用例。
- **建议**：1) 立刻给该端点加 `require_operator` 作为止血（一行）；2) 把 `run_readonly_query` 从用户可直连的 exec 白名单里移除，只允许平台内部由数据集通道生成 SQL；3) 采集器侧把黑名单换成 `sqlglot` AST 校验 + `READ ONLY` 事务 + 强制 `LIMIT`，并拒绝 `pg_read_file`/`INTO OUTFILE`/`LOAD_FILE`/多语句；4) 该路径补审计事件（沿用 `record_audit_event`）。

### [medium] 外部 MCP server 子进程继承平台全量环境变量，第三方工具可直接拿走平台密钥

- **证据**：`backend/app/agent/diagnostics/mcp.py:128-134`
  ```python
  return StdioServerParameters(
      command=server.command,
      args=list(server.args),
      env={**os.environ, **server.env},
  )
  ```
  `command`/`args` 来自 `MCP_SERVERS` 配置，`{python}` 会被替换成平台解释器（`mcp.py:45`）、`{repo}` 换成仓库根，子进程以平台进程的同一用户身份运行。当前 `.env:59` 已配置一个示例外部 server（`deploy/mcp/demo_k8s_server.py`）——这个位置的语义就是「第三方」，而 MCP 协议本身也允许 server 自行发起任意动作。对照 `services/diagnostics/sandbox.py:116-124`：同一代码库在跑模型生成的代码时明确只透传 `PATH/HOME/LANG/...` 白名单。
- **风险**：任何被配置进来的 MCP server（自研脚本、供应商 SDK、被供应链投毒的包）以及它 `args` 指向的任何可执行文件，都可以读到平台进程环境里的 `DEEPSEEK_API_KEY`、`FEISHU_APP_SECRET`、`DATABASE_URL`（含口令）、`ENCRYPTION_KEY`（一旦按发现 5 设上，就等于把解密主密钥一起给出）、以及 `JWT_SECRET` —— 即一次性拿到平台全部信任根。这从「MCP 只提供只读工具」的假设上直接逃逸。
- **业界做法**：子进程最小环境（只透传白名单变量 + 显式 `server.env`）；密钥不落进程环境，改用短时凭据/文件描述符（`systemd` 的 `LoadCredential`、Vault agent sidecar、KMS 调用）；MCP server 容器化并加 `--read-only`、`--cap-drop=ALL`、网络策略与只读挂载；对第三方 server 做允许清单与完整性校验（哈希/签名）；把 MCP 调用纳入与原生工具同级的审计与配额。
- **可采纳性**：easy。`_params` 改成显式白名单（例如只带 `PATH/HOME/LANG/TZ/PYTHONPATH` 再合并 `server.env`）是一处几行的改动，风险低；容器化可以后续再做。
- **建议**：改 `mcp.py:_params` 为白名单 env；在文档与配置校验里要求 `server.env` 显式声明所需变量；给 MCP 子进程加 `RLIMIT`/超时（已有 `timeout`）并考虑后续容器化。

### [medium] 系统 token 的 IP 白名单基于可伪造的 `request.client.host`，形同虚设

- **证据**：
  - 判定逻辑：`backend/app/api/openapi.py:75-78`
    ```python
    if token.allowed_ips:
        client_ip = request.client.host if request.client else ""
        if client_ip not in token.allowed_ips:
            raise HTTPException(403, "来源 IP 不在允许范围内")
    ```
  - 应用侧没有任何可信代理配置：`backend/app/main.py:140-146` 只加了 CORS；启动命令既没 `--no-proxy-headers` 也没 `--forwarded-allow-ips`（`scripts/stack-launcher.sh:246`、`deploy/docker-compose.yml:41`）。uvicorn 0.49 默认 `proxy_headers=True`、`trusted_hosts="127.0.0.1"`，而反代正来自 127.0.0.1（`Caddyfile:1-22`）。
  - 实测 uvicorn 的取值实现（`uvicorn/middleware/proxy_headers.py` 的 `_TrustedHosts.get_trusted_client_address`）：**从右往左取第一个不受信主机**。Caddy 反代会把自己看到的对端（127.0.0.1，受信）追加到 `X-Forwarded-For` 末尾，于是被跳过，攻击者自填在左侧的值就成为 `request.client.host`。
- **风险**：`allowed_ips` 是外部系统 token 唯一的地理/网络约束，可被 `X-Forwarded-For: <任意IP>` 一行绕过——token 一旦泄漏（日志、客户代码库里的硬编码、`X-System-Code`+token 的组合被截图）就完全不受来源限制。同时在同一条链路上，任何基于 IP 的审计/风控（如果有）都会记录伪造地址。
- **业界做法**：明确可信代理链——uvicorn 显式设 `--forwarded-allow-ips` 指向 Caddy 的地址（或 `--no-proxy-headers` 并在 Caddy 层注入唯一可信头）；由入口侧（Cloudflare + Caddy）**剥离**客户端自带的 `X-Forwarded-For`，只使用 `CF-Connecting-IP` 或 Caddy 自己写入的头；应用只读这一个可信头，并把它抽成一个统一的 `client_ip(request)` helper；IP 白名单作为纵深防御而非唯一控制（配合 mTLS/签名请求）。
- **可采纳性**：moderate。需要先确定真实拓扑（cloudflared → Caddy → uvicorn）与各跳的头处理行为，再在 Caddy 配置 `trusted_proxies`/`header_up`、给 uvicorn 设 `--forwarded-allow-ips`，并统一替换应用内取 IP 的位置；改动分散在部署配置与应用代码两侧，且改错会直接影响审计与限流。
- **建议**：Caddy `reverse_proxy` 增加 `trusted_proxies` 并显式 `header_up X-Forwarded-For {http.request.remote.host}`（丢弃外部值）；uvicorn 启动参数固定 `--forwarded-allow-ips=127.0.0.1`（显式化而非依赖默认）+ 只信任一跳；`openapi.py` 改读统一的 `client_ip()`；给 token 增加「首次使用 IP 记录 + 变更告警」。

### [medium] 代码沙箱在没有 docker CLI 时静默降级为宿主进程执行，模型生成的代码可读平台 `.env`

- **证据**：
  - 降级路径：`services/diagnostics/sandbox.py:61-72` 的 `_docker_argv()` 只在 `shutil.which("docker")` 为真时返回容器命令；`sandbox.py:126` `argv = docker_argv or [settings.sandbox_python or sys.executable, "-I", script_path]` —— 容器不可用即退回**本机 python 直接执行**。资源限制只在非容器分支生效（`sandbox.py:134`），而文档自己写明「这不是安全沙箱（无法阻断本机网络/文件读取）」（`sandbox.py:10-11`）。
  - 触发闸门是关键词而非权限：`agent/diagnostics/tools.py:515` 只要求用户问题里出现「分析/统计/计算/生成/报告/图表/csv/…」（`tools.py:82`），而用户问题在飞书路径上可被外部构造（见发现 3）。
  - 容器分支也缺常见加固：`sandbox.py:66-72` 只有 `--network none --memory 1g --cpus 1 --pids-limit 256`，没有 `--read-only`、`--cap-drop=ALL`、`--security-opt no-new-privileges`、非 root `--user`；工作目录落在仓库内 `$HOME`（`sandbox.py:86-93`）。
  - 平台自身容器化部署时大概率没有 docker CLI：`deploy/docker-compose.yml:20-41` 的 `platform-api` 未挂载 docker socket 也未安装 docker CLI，而 `SANDBOX_CONTAINER_IMAGE`/`SANDBOX_PYTHON` 是配置驱动的（`core/config.py:181-184`）。
- **风险**：在无 docker CLI 的部署形态下，`run_python_analysis` 生成的脚本以平台进程用户身份在宿主/应用容器内执行，可以 `open('/Users/.../ai-ops-agent/.env')` 读走 `DEEPSEEK_API_KEY`/`FEISHU_APP_SECRET`/`DATABASE_URL`，可以读 `config.py`、直接连 Postgres、或把结果写进产出文件再通过免登录下载链接（`api/diagnose.py:397`）取回——提示注入（日志/知识库/归档文件内容）到平台密钥外泄形成完整链条，而现有防护只是提示词级别的 `_untrusted` 包裹（`harness/context_data.py:27-38`）。
- **业界做法**：沙箱不可用时应 **fail closed**（拒绝执行 + 明确报错），而不是降级到宿主；统一用 gVisor/Firecracker/nsjail 或强制容器执行；容器参数补齐 `--read-only --tmpfs /tmp --cap-drop=ALL --security-opt no-new-privileges --user 65534`，并加 seccomp/AppArmor 与只读工作目录；产出文件先做类型/内容校验再挂下载链接；把「是否真在隔离环境执行」作为运行元数据写进审计与报告。
- **可采纳性**：moderate。改成 fail-closed 需要保证部署侧一定提供沙箱镜像（CI 里预构建 `aiops-sandbox:latest`、把镜像名做成必填配置），否则功能直接不可用，属于「可用性 vs 安全性」的产品取舍；容器加固参数是低成本的顺带改进。
- **建议**：1) `sandbox.py:126` 的降级分支改为返回明确错误（或仅在显式 `SANDBOX_ALLOW_HOST_EXECUTION=true` 的 dev 开关下允许），并在审核报告中标注；2) 容器命令补 `--read-only --tmpfs /tmp --cap-drop=ALL --security-opt no-new-privileges --user 65534`；3) 产出文件落盘后做魔数/大小校验；4) 在 `deploy/docker-compose.yml` 给 platform-api 提供可达的沙箱 runner（独立服务）而不是依赖宿主 docker。

### [low] 登录无速率限制与锁定、注册完全开放、密码无强度要求，JWT 也没有吊销机制

- **证据**：
  - `api/auth.py:35-40` 的登录只有「查用户 + 校验密码」，无失败计数、无退避、无验证码；全仓 grep 无 `slowapi`/`Limiter`/`rate_limit` 依赖或实现。
  - `api/auth.py:15-32` 的注册对任何人开放：只要邮箱未被占用就新建 Org 并授予 `role="owner"`（`auth.py:26-27`），没有邮箱验证、邀请或审批；`schemas/auth.py:5-8` 的 `password: str` 无长度/复杂度约束。
  - `core/config.py:53` `access_token_expire_minutes = 60 * 24`，token 里没有 `jti`，服务端没有 denylist/refresh（`core/security.py:112-122`）：改密码、降权、封禁都不会使已签发的 token 立即失效（`deps.py:18-24` 只按 `sub` 查库，用户被删除才会失效）。
  - `api/auth.py:48-54` `/auth/users` 允许组织内任意成员枚举同组织全部邮箱（`UserOut` 含 email/role）。
- **风险**：开放注册 + 无限次密码尝试，使「拿到一个平台账号」的门槛很低；配合发现 1 的伪造 token 或发现 6 的越权 SQL，一个自助注册的 owner 就能进入多租户平台并注册指向内网地址的系统（`health_url`/`webhook_url` 无任何地址校验，`schemas/systems.py:60-63` 只校验 `http(s)://` 前缀），从而获得对平台可达内网的盲 SSRF 探测与 webhook 外发能力。token 不可吊销则意味着凭据泄漏后的止损窗口是 24 小时。
- **业界做法**：注册走邀请码/域名白名单/SSO（OIDC/SAML），至少加邮箱验证；登录限流用 Redis 令牌桶做账号+IP 双维度并指数退避、失败锁定与告警；密码策略按 NIST SP 800-63B（最小长度 + 弱口令字典，而非强制复杂度）；对高权限账号启用 MFA；JWT 短 TTL（15-30 分钟）+ refresh token + `jti` 黑名单或 token 版本号（`user.token_version`），改密码/降权时自增。
- **可采纳性**：easy（限流 + 密码校验 + `token_version` 加起来不到百行，中间件与 Redis 都已在栈里）；但「注册是否需要邀请/SSO」是产品定位决策，若平台要做对外 SaaS 则不能简单关闭注册，需要单独排期。
- **建议**：1) 给 `/auth/login`、`/auth/register`、`/feishu/webhook` 加基于 Redis 的限流中间件；2) `RegisterRequest` 加 `min_length=8` + 弱口令检查；3) `User` 加 `token_version` 并放进 JWT，改密/降权时自增；4) 生产部署建议关闭自助注册或改为邀请制；5) 给注册的系统 URL 加内网地址策略（拒绝 `169.254.0.0/16`、`127.0.0.0/8`、RFC1918，除非显式允许）。

### [low] SSH 连接器采用 trust-on-first-use，且远端命令由注册配置拼接进 shell

- **证据**：`shared/connectors/ssh.py:19-20`
  ```python
  cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
         '-o', 'StrictHostKeyChecking=accept-new']
  ```
  没有任何 `known_hosts` 固定或指纹校验；健康检查/日志/重启都把注册配置直接拼进远端命令串（`ssh.py:45` `cfg['health_cmd']`、`ssh.py:60-61` `tail -n {lines} {path}`、`ssh.py:68` `grep -i '{kw}'`、`ssh.py:84` `sudo -n systemctl restart {safe_unit}`）。`search_logs` 的 `kw` 来自 Agent/模型，仅靠 `keyword.replace("'", '')` 防注入（`ssh.py:66`）。
- **风险**：首次连接（或 known_hosts 被清空/容器重建后）可被中间人劫持，攻击者取得主机密钥后即可解密后续「只读」流量（日志内容常含业务数据/凭据片段）并冒充被监控主机应答；`health_cmd`/`log_path` 由 operator 注册，写入等价于在被监控主机上执行任意远端命令（这是产品能力，但缺少任何语法/模板约束，误配或越权注册即远端 RCE）。`replace("'", '')` 这类"过滤"在换行/反引号等场景下的行为依赖远端 shell 细节，属于脆弱的实现。
- **业界做法**：显式固定主机密钥——注册服务时提供 fingerprint 字段或由 `ssh-keyscan` 后人工确认写入 `known_hosts`，连接用 `StrictHostKeyChecking=yes` + `UserKnownHostsFile`；用 paramiko/asyncssh 显式校验 host key；远端命令避免自由字符串，改成受限命令模板（`tail`/`grep` 的参数用 `shlex.quote` 统一转义或走 `exec_command` 的参数化接口），把允许的命令做成枚举而不是可编辑的 `health_cmd` 字符串。
- **可采纳性**：moderate。需要为每个被监控主机预置 known_hosts 或引入指纹录入的运维流程与控制台字段（跨团队配合），远端命令模板化还要改描述符 schema 与 UI，短期只能先加指纹校验开关。
- **建议**：1) 给服务描述符加 `host_key_fingerprint`，配置了就走 `StrictHostKeyChecking=yes` + 临时 known_hosts；未配置时在 UI 显式提示风险；2) 把 `health_cmd` 收窄为「命令枚举 + 参数」，禁止自由字符串；3) 统一用一个 `quote_remote_arg()` 处理所有拼进远端命令的值，替换现在的 `replace("'", '')`。

## 对抗性复核

- **运行实例用的是源码里硬编码的 JWT 密钥，任何人可伪造任意用户令牌完成跨租户接管** → confirmed（修正严重度：critical）
  复核：`backend/app/core/config.py:51` 默认值 `dev-secret-change-me-in-prod` 属实，`backend/app/core/security.py:115,120` 的签名/校验直接取 `settings.jwt_secret`；配置源是仓库根 `.env`（`config.py:42`），`.env` 全文 59 行确无 `JWT_SECRET`；全仓（含 scripts/deploy）只有 `deploy/docker-compose.yml:36/61/84` 注入该变量，而 `scripts/stack-launcher.sh:241` 的 `red_env` 只导出 CELERY_*/PYTHONUNBUFFERED，`:246` 正是用它启动 uvicorn。用 `backend/.venv` 实跑：`settings.jwt_secret == 'dev-secret-change-me-in-prod'`，且该常量自签 token 能过 `decode_access_token()`，`backend/app/core/deps.py:18-24` 只验签名+exp 再按 `sub` 查库。**报告反而低估了暴露面**：`~/.cloudflared/config.yml` 把 `webhook.tiancaizhaozhao.dpdns.org` 直接映射到 `http://localhost:8000`（不走 `Caddyfile` 的路径白名单），而 uvicorn 以 `--host 0.0.0.0 --port 8000` 启动，等于整个 API 面（含 `/systems`、`/collectors`、`/openapi`）在公网可直接命中。唯一缓解是 compose 的 `${JWT_SECRET:?}` fail-fast，但当前启动路径不经过它；`stack-launcher.sh:201` 的 `JWT_SECRET=dev-placeholder` 只作用于 alembic 子进程，不构成缓解。未发现任何启动期对默认密钥的校验。裁决：confirmed，critical。

- **.env 里的真实密钥已经进了 git 历史，且同一把 key 至今未轮换** → confirmed（修正严重度：critical）
  复核：`git show b77713f --stat` 确认 Initial commit 提交了 `.env`（21 行）；`1f53781 Add .gitignore, remove .env (secrets)` 只把它从 HEAD 移除（`git ls-files | grep '^\.env$'` = 0，`.gitignore:2` 已忽略）。逐字段比对历史 blob 与当前工作区：`DEEPSEEK_API_KEY`（`.env:2`）、`FEISHU_APP_SECRET`（`.env:19`）、`FEISHU_VERIFICATION_TOKEN`（`.env:20`）**三者均 IDENTICAL**，"未轮换"属实；`.env` 权限确为 `-rw-r--r--`。**比报告更严重**：`git ls-remote origin` 加 `git merge-base --is-ancestor` 证明 b77713f 是 `origin/master` 与 `origin/feat/multi-tenant-aiops-platform` 的共同祖先，且 `https://api.github.com/repos/sidongzhang/ai-ops-agent` 返回 `"private": false` —— 仓库**当前就是公开的**，含真实密钥的历史已在 GitHub 上，而不是报告假设的"未来的公开仓库"；报告把它列为"未来风险"是低估。裁决：confirmed，critical。

- **飞书 webhook 事件路径完全不校验签名/verification token，可被任意伪造触发一次完整 AI 诊断** → confirmed（修正严重度：high）
  复核：`backend/app/api/feishu_webhook.py:8-11` 无任何 `Depends`；`backend/app/services/notifications/webhook.py:79-86` 的 token 校验只在 URL challenge 分支，且写成 `if settings.feishu_verification_token and token and token != ...`（body 不带 `token` 即放行）；`webhook.py:88-118` 事件分支只按 `header.event_type` 分发。全仓 grep `X-Lark-Signature|Encrypt-Key|encrypt_key|verify_signature` 零命中；`main.py:140-146` 只挂 CORSMiddleware，无全局鉴权。报告未提到的两处"看似缓解"已逐一证伪：(1) `webhook.py:110` `header.get("app_id", settings.feishu_app_id)` 让省略 app_id 的攻击者命中默认应用，无需知道 app_id；(2) `handle_message` 最先调用的 `feishu.add_reaction`（`services/notifications/feishu.py:181-193`）失败时只记 debug 并返回 ""，不抛异常，伪造的 `message_id` 不会阻断其后的 `diagnose(descriptor, text)`。报告的预算判断也不夸大：`services/diagnostics/usage.py:272,330` 的 `reserve_budget/settle_budget` 只在 `services/diagnostics/service.py:421,508` 的 API/Celery 路径被调用，webhook 的同步 `diagnose()` 根本不进预算账本（比报告说的"只约束单次会话"更弱），单次上限只有 `diagnosis_request_limit=12`、`tool_calls_limit=24`、`diagnosis_max_run_seconds=900`，对循环伪造无总量约束。裁决：confirmed，high。

- **采集器密钥经 URL query 传输并明文落进访问日志，日志读权限等于采集器全权** → confirmed（修正严重度：high）
  复核：`backend/app/api/websocket.py:26` `key: str = Query(...)` 与 `collector/ws_client.py:425` `url = f"{ws_url}?key={collector_key}"` 属实。日志实测：`.dev-stack/logs/` 下命中 `ws/collector?key=` 的文件是 3 个（`backend.log` 209 次、`aiops-backend.log` 101 次、`cloudflared.log` 6 次），去重后是**4 把不同密钥**（`tWqk…`/`MIk1…`/`jjD7…`/`q1Ze…`）——报告说"另外两把"少算一把；但报告称 `collector-*.log` 也命中，实测未命中，属小的事实偏差。`backend/app/models/collectors.py:12-21` 确实只有 `token_hash`，无 `rotated_at/revoked_at/expires_at`，只能靠删除采集器吊销。**"等于采集器全权"不但成立而且偏保守**：同一把 key 还能过 `core/deps.py:38-47` 的头鉴权打 `api/collector_gateway.py:17-24` 的 `GET /collector/config`，其返回经 `services/collectors/service.py:65-75` 到 `services/descriptors/builder.py:50-57,35` 的 `decrypt_sensitive_fields` 解密，等于把该系统的 `infra` 与每个 `service.config`（含连接串口令/SSH 配置）整包交出。唯一减弱项：`.dev-stack/` 已在 `.gitignore:20` 忽略，日志未进仓库。裁决：confirmed，high。

- **ENCRYPTION_KEY 未配置使凭据加密整体变成 no-op，SSH/数据库/通知凭据明文落库** → confirmed（修正严重度：high）
  复核：`core/config.py:136` `encryption_key: str = ""`；`core/security.py:37-39` 空 key 返回 `None`，`security.py:58` 的加密条件因 `f` 为假而短路。在 `backend/.venv` 内实跑：`settings.encryption_key == ''`，且 `encrypt_sensitive_fields({'database_url':'mysql://root:pw@10.0.0.5:3306/prod','password':'s3cr3t'})` 返回**逐字节相同的明文 dict**。配置源 `.env` 全文无 `ENCRYPTION_KEY`（仅 `.env.example:29` 留空占位），`stack-launcher.sh:241` 的 red_env 也不含它。写点逐个核对：`services/systems/service.py:119`(infra)、`service.py:154`(service config)、`services/systems/configuration.py:50`(monitoring)、`:70`(notify)、`services/systems/lifecycle.py:56`、`:97`(service config) 全部包了 `encrypt_sensitive_fields`，即以上路径全部退化为明文写库，报告未漏也未多算。报告称"静默降级、无启动告警"核实为真：全仓无任何针对 `encryption_key`/`jwt_secret` 的启动校验或 warn。`deploy/docker-compose.yml:37` 的 `${ENCRYPTION_KEY:?...}` 只覆盖 compose 启动路径。裁决：confirmed，high。

- **/systems/{id}/collector/exec 不要求 operator 角色却可下发任意 SQL，采集器侧只靠关键字黑名单** → confirmed（修正严重度：high）
  复核：`backend/app/api/collector_exec.py:28-38` 只挂 `get_current_org_id`（= 任意有效 JWT），只硬编码拦 `restart_container/restart_systemd`，与 `api/systems.py:166-172` 连只读探活都要 `require_operator` 的不一致属实。`services/collectors/exec.py:14-17` 白名单含 `run_readonly_query`，`schemas/collectors.py:42-45` 的 `args` 是无结构 dict 直通采集器（`exec.py:53`）。`collector/ws_client.py:123-126` 的守卫确实只有"以 select 开头 + 无内嵌分号 + 正则黑名单"，`:31-34` 的 `_DANGEROUS_SQL` 不含 `pg_read_file/lo_import/pg_ls_dir/INTO OUTFILE/LOAD_FILE`；`:135-141` 只 `engine.connect()` + `connection.rollback()`，无 `SET TRANSACTION READ ONLY`/`default_transaction_read_only`，也无 statement timeout，因此 `SELECT … INTO OUTFILE`（服务端落文件）、`SELECT pg_read_file(...)`/`lo_import(...)`、`SELECT pg_terminate_backend(...)`/`pg_sleep(...)` 全部放行，"任意 SQL"的定性成立。审计缺口也属实：`exec.py:54` 只记 system/cmd/ok，无 actor、无 SQL。**需要修正报告的一个论据**：报告把风险建立在"组织内任何非 operator 账号"上，但全仓 role 赋值只有 `api/auth.py:27` 的 `role="owner"`，不存在邀请/改角色端点（grep `role=`/`viewer`/`operator` 仅命中 `deps.py:33` 的判定与 auth.py:27），即当前平台**造不出非 operator 账号**——"加 require_operator 止血（一行）"在今天不改变任何可达能力，跨租户也被 `services/systems/access.py:10-14` 的 `require_system` 挡住。扣掉这一环后，端点的严重度仍由"自由 SQL + 仅黑名单 + 无只读事务 + 无审计"支撑（持任一有效 JWT 即可从客户内网业务库拖数据、并用 INTO OUTFILE 在服务端落文件），维持 high，但报告的风险叙述应把"非 operator 账号"标注为"当前不可实例化、属纵深防御缺口"。裁决：confirmed，high。
