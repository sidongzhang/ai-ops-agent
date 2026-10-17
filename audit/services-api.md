# 应用服务与 API 层 体检

## 现状设计

控制面是 FastAPI + SQLModel 的分层结构：`app/api/*` 只做参数校验、鉴权依赖与异常→HTTP 状态映射，业务逻辑落在 `app/services/*`，`app/repositories/*` 承担查询封装。诊断是一条"数据库 outbox + Celery"的异步流水线：`start_diagnosis` 在**同一个事务**里做预算原子预留（`reserve_budget`）、写 `diagnosis_reports(status=running)` 与 `diagnosis_jobs(pending)`，由 API 进程内的 `dispatch_pending` 协程每 2 秒扫表投递到 `diagnoses` 队列，worker 用 CAS 认领（`claim_job`）后跑 agent，再以"账本增量 + 预算结算 + 条件更新终态"收尾。取消是三层的：进程内 `report_id → token` 注册表做快路径、`diagnosis_reports.cancel_requested_at` 做唯一真相、Redis pub/sub 只做加速，另有 poller 兜底轮询；运行态（框架原生消息历史）单独落 `diagnosis_run_state` 供续跑，失联由 reaper 收成 failed 且标记不可续。多租户隔离统一靠 `org_id + system_id` 双条件过滤，外部业务系统走 system token（scope + IP 白名单），采集器走独立的 `X-Collector-Key` 通道并用 WebSocket 反向下发命令。用量记账是本项目最讲究的一块：区分"有 usage"与"用量完整"，未知用量按 `max(实测, 保守预留)` 结算，绝不按免费处理。

## 做得好的地方

- **终态写入用原子条件更新，先到先赢**：`backend/app/services/diagnostics/service.py:646-667` 把 `status='running' AND runner_id=INSTANCE_ID` 放进 `UPDATE ... WHERE`，彻底消除了"读→判断→写"导致成功被关停路径覆盖成 cancelled 的竞态；`termination.mark_execution_unknown`（`backend/app/services/diagnostics/termination.py:7-28`）用同一手法配合 `heartbeat_token` 做乐观并发校验。
- **取消语义的三层职责分离是有意为之的合理取舍**：`backend/app/services/diagnostics/cancel_bus.py:1-14` 明确写了"未命中注册表是正常情况""Redis 丢消息不影响正确性"，`register()` 用 CAS 认领 `runner_id`（`cancel_bus.py:81-97`），重复派发不会替换别的 run 的 token，`start_poller` 是唯一的正确性来源（`cancel_bus.py:311-346`）。
- **"不确定"如实暴露，不伪装成功**：`backend/app/services/diagnostics/usage.py:73-89`（`assess_completeness`）与 `usage.py:184-188`（未知用量按保守预估计入）、`run_state.py:104-134`（读时重新反序列化校验，不信任历史里的 `resumable` 标记）都体现了同一条原则。在多租户计费场景里这种克制比"乐观补零"值钱得多。
- **DB outbox 是正确的方向**：`backend/app/services/diagnostics/jobs.py:221-266` 的 send-after-commit + `publish_after` 租约 + CAS 认领，保证 broker 丢消息不丢任务；`submissions.claim`（`backend/app/services/diagnostics/submissions.py:12-40`）用 `INSERT ... ON CONFLICT DO NOTHING` 把幂等键、预算预留、报告、outbox 锁在同一事务里，是这份代码里最"教科书"的一段。
- **展示流与权威状态解耦**：`backend/app/services/diagnostics/answer_stream.py:1-5` 明确"永不作为证据/历史/执行授权"，`_save` 用 `WHERE sequence <= :seq` 条件写避免旧快照回退（`answer_stream.py:97-103`），Redis 不可用时退化为 DB checkpoint 而非报错。
- **租户与采集器鉴权边界清晰**：`backend/app/core/deps.py:14-47` 把用户 JWT 与 `X-Collector-Key` 分成两条依赖；`backend/app/services/diagnostics/reports.py:126-131`、`backend/app/api/openapi.py:71-73` 等所有入口都同时校验 `org_id` 与 `system_id`，没有发现"只查 id 不查 org"的越权读。

## 发现

### [critical] 飞书 Webhook 无签名校验、无鉴权，任何人都能驱动面向客户内网的特权诊断 Agent 并指定回发会话

- **证据**：`backend/app/api/feishu_webhook.py:7-11` 的 `router = APIRouter(prefix="/feishu")` 与 `process_webhook_payload(body, background)` 没有任何 `Depends` 鉴权；`backend/app/services/notifications/webhook.py:89-95` 的 verification token 校验**只存在于 `challenge` 分支**，且条件是 `settings.feishu_verification_token and token and ...`——`feishu_verification_token` 默认空串（`backend/app/core/config.py:132`），攻击者只要不带 `token` 字段就整条短路；真实事件分支（`webhook.py:97-114`）**完全没有**任何验签。`webhook.py:106-114` 信任请求体里的 `chat_id`（`chat_id = message.get("chat_id", "") or settings.feishu_alert_chat_id`）与 `app_id`，随后 `webhook.py:62-76` 调 `find_systems_by_app_id(...)` 取 `descriptors[0]` 并执行完整诊断 `diagnose(descriptor, text)`，最后 `feishu.send_card(chat_id, answer)`。入口暴露面确认：`Caddyfile:19` 的 `path /feishu*` 直接 `reverse_proxy localhost:8000`，无前置鉴权；`deploy/frontend.nginx.conf:6` 同理。
- **风险**：任何能访问该域名的攻击者，构造 `{"header":{"event_type":"im.message.receive_v1"},"event":{"message":{"message_type":"text","content":"{\"text\":\"...\"}","chat_id":"<我的会话>"}}}` 即可让平台用**租户自己的凭据**跑一次带工具的诊断（工具集含只读业务库查询、日志读取、采集器命令、Python 沙箱），并把结论投递到攻击者指定的 `chat_id`（只要机器人已在该会话内）——这是无鉴权的越权执行 + 客户数据外泄 + LLM 费用消耗（预算上限只限制单窗口金额，不限制"谁在花"）。反馈回路：攻击者用 `text` 做提示词注入即可操纵 Agent 输出。
- **业界做法**：飞书/Lark 事件订阅要求校验 `X-Lark-Signature`（`SHA256(timestamp + nonce + encrypt_key + body)`）或用 `encrypt_key` 做 AES 解密并校验 `X-Lark-Request-Timestamp` 防重放，官方 SDK（`lark-oapi`）自带 `EventDispatcher` 验签；幂等与重放窗口应放 Redis（`SETNX event_id EX 3600`）而不是进程内 `deque`；对外 Webhook 端点通常单独挂在只允许飞书出口 IP 的网关/路径上。
- **可采纳性**：easy。改动面只有 `webhook.py` 的入口校验加一个 `X-Lark-Signature`/`encrypt_key` 实现（或直接引入 `lark-oapi` 的 dispatcher），外加把 `already_seen` 换成 Redis；不需要动诊断链路。
- **建议**：在 `backend/app/api/feishu_webhook.py` 增加一个 `Depends(verify_feishu_signature)`（读 raw body + `X-Lark-Signature`/`X-Lark-Request-Timestamp`，用 `settings.feishu_encrypt_key` 计算 HMAC/解密），校验失败一律 401；`process_webhook_payload` 中删掉"无 token 即放行"的短路并把 `chat_id` 与 `app_id` 绑定到已注册系统的白名单（不允许请求体任意指定 `chat_id`）；`already_seen` 改为 Redis `SETNX`。预期收益：关掉一条从公网直达内部 Agent 与客户数据的无鉴权通道。

### [high] `query_business_dataset` 的相对日期必然抛 `NameError`，而工具文档正好教模型这么传

- **证据**：`backend/app/services/data_analysis.py:394-404` 的 `_parse_date` 在 `today`/`今天`/`今日` 与 `昨天` 两个分支都引用了 `today`：`return today.isoformat()`、`return (today - timedelta(days=1)).isoformat()`；模块内只从 `datetime` 导入了 `date, datetime, timedelta, timezone`（`data_analysis.py:4`），**没有定义 `today`**（AST 校验确认模块级无该名字）。调用链：`backend/app/services/diagnostics/service.py:255-267` 的 `query` → `query_readonly_dataset`（`data_analysis.py:434-439`）→ `_parse_date`；工具侧 `backend/app/agent/diagnostics/tools.py:449` 的 docstring 明确写"查询今天时两个都填今天"。
- **风险**：Agent 按自己的工具说明传 `"今天"` 就 100% 抛 `NameError`，异常被 `service.py:266-267` 吞成一句 `业务数据集查询失败：name 'today' is not defined` 返回给模型——模型拿不到真实数据，在"今天执行了多少次/失败多少"这类量化问题上有很强的动机改口凭快照猜测，产出看起来合理但错误的运维结论（比直接报错更危险）。凡带相对日期的数据集查询全部不可用。
- **业界做法**：日期解析必须有单元测试覆盖（参数化覆盖 `""`/`today`/`今天`/`昨天`/`YYYY-MM-DD`/非法值）；统一用 `datetime.now(tz).date()` 并显式传平台时区（本项目业务侧是 `Asia/Shanghai`，见 `backend/app/workers/celery.py:26`），不要把"平台本地时区"和"UTC"混用。
- **可采纳性**：easy。修一个函数 + 补一个参数化单测即可，无接口变更。
- **建议**：`data_analysis.py:394` 内改为 `today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()`（或复用已有的时区配置），并让 `_parse_date` 对无法识别的值抛 `ValueError` 而非静默；在 `backend/tests/`（当前为空）补 `test_parse_date` 覆盖上表用例。预期收益：恢复一条被文档主推的取证路径，堵住"模型猜数字"的诱因。

### [high] Celery beat 的 15 秒巡检无分布式锁、单事务跨全部租户：重叠执行重复告警，单个系统异常拖垮整批

- **证据**：`backend/app/workers/celery.py:14-19` 用 `"schedule": 15.0` 触发 `app.tasks.health_check_all_systems`，未配置 `options={"expires": ...}`，任务本身（`backend/app/workers/tasks.py:10-13`）也没有任何锁或幂等键。实现 `backend/app/services/monitoring/scheduled.py:52-98`：第 56 行开**一个** `Session(engine)` 覆盖整个租户集合，第 57 行 `select(MonitoredSystem).all()` 全量加载，循环内逐系统探活，`system.last_report_at = utcnow()` 在第 91 行才赋值，`session.commit()` 直到第 98 行才执行一次；`is_due`（`scheduled.py:44-49`）判断依据正是 `system.last_report_at`。循环内异常只 `log`（`scheduled.py:94-96`）不 `rollback`。
- **风险**：(1) 只要一轮耗时 > 15s（跨多租户、逐服务探活，很容易），beat 就会持续堆叠任务副本，而副本在自己的事务里读到的 `last_report_at` 都是旧值 → 判定全部"到期"→ 对同一批系统重复探活、重复 `alert_if_needed`、重复 `run_trend_patrol`，放大 N 倍打向客户内网与被监控系统；(2) 某个系统探活时抛 DB 异常（如唯一约束）会把 Session 置为 `PendingRollbackError`，后续系统的 flush 全部失败，最后第 98 行的 `commit()` 直接抛出，**整批巡检结果全部丢失**且没有外层捕获；(3) 单事务长期持有连接与快照，PG 上还会拦住 vacuum；(4) 没有任何按租户的公平性，大租户的系统数直接决定别人被巡检的延迟。
- **业界做法**：周期性巡检用分布式互斥（Redis `SET NX EX` 租约、`pg_try_advisory_lock`，或 Celery `task_acks_late=True` + `expires` 配合 `celery-redbeat` 的原子锁）；每个租户/系统单独事务提交（"一个系统一个事务"），失败只影响自身并写审计；把探活并行化（`asyncio.gather` + 信号量）而不是串行阻塞；规模再大就用 `SELECT ... FOR UPDATE SKIP LOCKED` 的分片扫描。
- **可采纳性**：moderate。改动集中在 `scheduled.py` 的编排与 `celery.py` 的 beat 配置，但要保证"跳过判定"在并发下正确（需要把 `last_report_at` 的写入提前到每个系统处理完就提交），属于行为级重构而非一行开关。
- **建议**：`celery.py` 给该任务加 `options={"expires": 60}` 并设 `task_acks_late`；`run_scheduled_health_checks` 改为"取一个 Redis 租约 → 每系统一次事务提交 `last_report_at` → 异常 rollback 后继续下一个"；`has_fresh_collector`/`services_of` 的逐系统查询（`scheduled.py:26-41`）合并为一次 `IN` 查询消除 N+1。预期收益：消除重复告警风暴与"一个坏系统拖垮整批"的放大效应。

### [high] OpenAPI 幂等键靠"全表加载 + Python 比对"实现，且没有唯一约束兜底

- **证据**：`backend/app/repositories/messages.py:82-101` 的 `find_message_by_request_id` 先 `session.get` 试数字 ID，否则 `select(SystemMessage).where(system_id==..., source=="openapi")` **不带 limit/all() 全量取回**，再在 Python 里逐条比对 `(message.related or {}).get("request_id")`。它被放在所有写入路径的最前面：`backend/app/services/openapi.py:55`（告警）、`:147`（消息/日报/周报/月报）、`:275`→`:362`（状态更新）、以及 `backend/app/api/openapi.py:207` 和 `:614` 的查询端点。`backend/app/models/messages.py:27` 的 `related` 是普通 JSON 列，没有 `request_id` 的生成列或唯一索引。
- **风险**：(1) 延迟与内存随该系统历史消息数线性增长——一个每天上报几千条告警的系统，每次上报都要把全部行 hydrate 进 ORM 再丢弃，是典型的可预测性劣化点；(2) 读-判断-写在并发下不成立：两个携带相同 `request_id` 的请求同时到达会**都**判定"不存在"并各自插入，重复告警/重复派发处理，而下游的 `submissions.claim`（`submissions.py:26-28`）用的是数据库唯一约束——同一个仓库里正确与错误的两种幂等实现并存。
- **业界做法**：幂等键落到列上并建唯一索引（本项目已有的写法就是模板：`backend/app/services/diagnostics/submissions.py:23-28` 的 `INSERT ... ON CONFLICT DO NOTHING` + 读回既有行）；对 JSON 内的键用 PG 生成列 `((related->>'request_id'))` 建部分唯一索引，或直接把 `external_request_id` 提升为正式列；查询侧用 `WHERE related->>'request_id' = :rid` 走表达式索引，不要 `.all()`。
- **可采纳性**：moderate。需要一次 alembic 迁移 + 存量重复数据清理（现在就可能有重复行），并同步改 4 处调用点；逻辑本身是复制 `submissions.py` 的成熟模式，风险可控。
- **建议**：新增迁移给 `system_messages` 加 `request_id` 生成列与 `UNIQUE(system_id, request_id) WHERE source='openapi'`；把 `find_message_by_request_id` 改为按该列的单行查询，写入侧改成 `ON CONFLICT DO NOTHING` 后读回旧行并记 `openapi.*.duplicate`（审计事件名已经存在，`openapi.py:63`）。预期收益：幂等从"通常有效"变成"数据库保证"，同时消掉一条会随业务量爆掉的查询。

### [medium] 报告列表/对话接口对每条记录做重活：历史反序列化 + 账本全表求和 + 逐条查询（N+1）

- **证据**：`backend/app/services/diagnostics/reports.py:98-112` 的 `_report_out` 每行都执行：`resumable_of(row.id, session=session)`（`backend/app/services/diagnostics/run_state.py:104-134`，会把整段 `messages_json` 反序列化校验一次）、`session.get(DiagnosisRunState, row.id)`、`session_usage(...)`（`backend/app/services/diagnostics/usage.py:239-250`，`select(...).all()` 把该会话的全部账本行取回，在 Python 里 `sum`）。而 `list_diagnosis_reports`（`reports.py:132-138`）就是 `[_report_out(row, session) for row in rows]`，limit 上限 100（`reports.py:125`）；同样的循环复制在 `backend/app/services/diagnostics/chats.py:73-86`（对话历史）。列表接口 `api/diagnose.py:250-260` 与 `backend/app/api/openapi.py:352-366` 直接暴露。
- **风险**：`GET /diagnosis-reports?limit=50` 会触发 50 次"MB 级消息历史 JSON 反序列化 + 全账本扫描 + 逐行 get"，单请求可到秒级、数百 MB 瞬时内存；对话历史页与外部 OpenAPI 轮询（`created_at desc` 排序后立刻 `reverse()`，`reports.py:137`）会周期性重复这个开销。同步 `def` 端点跑在 anyio 线程池（默认 40 槽）里，几个并发列表请求就能把线程池占满，表现为整个控制台无响应。
- **业界做法**：列表接口只读"便宜"的列——把 `resumable` 落成 `diagnosis_reports` 上的物化布尔列（写终态时一并更新，已有 `resumable` 列可复用），把会话用量改成 `SELECT SUM(tokens_delta) ... GROUP BY conversation_id` 的 SQL 聚合或增量维护的计数表；列表与详情分成两套出参（列表不带 `evidence`/`messages_json`）；给列表加 cursor 分页而不是 offset。
- **可采纳性**：moderate。`_report_out` 同时被详情、外链、对话三处复用，拆分需要引入"轻量出参"并同步前端字段，但不需要改表结构（`diagnosis_reports` 已有 `resumable`/`total_tokens`/`cost_usd_estimated`）。
- **建议**：在 `reports.py` 增加 `_report_out_light`（不调 `resumable_of`/`session_usage`，直接用行上的 `resumable` 列与 `total_tokens`），`list_diagnosis_reports` 与 `chats.chat_reports` 改用它；把一个 OR 批量的 `resumable_of`（一次 `WHERE report_id IN (...)` 取状态行）留给详情页。预期收益：列表 p99 从秒级降到数十毫秒，消除线程池被列表请求占满的风险。

### [medium] 请求路径上做同步阻塞聚合：metrics/patrol 串行外呼，无超时预算，可把控制台线程池占满

- **证据**：`backend/app/services/monitoring/metrics.py:80-92` 的 `get_metrics` 串行执行 Prometheus 4 次即时查询（`metrics.py:218-235`，单次 `timeout=6`，`metrics.py:168`）、Redis 裸 socket（`metrics.py:248`，timeout=5）、Kafka/MySQL 连通性探测（`metrics.py:323`/`:367`，各 timeout=3）、以及每个 http 服务的探测（`metrics.py:396-424`，逐个 `httpx.get(..., timeout=5)`）——最坏情况 ≥ 4×6 + 5 + 3 + 3 + 5N 秒；`get_metrics_history` 再加一个 `timeout=10` 的 range 查询（`metrics.py:194`）。它是同步端点 `backend/app/api/metrics.py:14-22`。同类还有 `backend/app/api/systems.py:29-62`（`run_patrol` 在请求内做全量探活 + `check_container_ooms` + `run_trend_patrol`）和 `backend/app/api/systems.py:324-342`（`test_notify` 在请求内发外部 HTTP）。
- **风险**：`GET /systems/{id}/metrics` 与被前端仪表盘定时轮询，一个 20 个 http 服务的系统就可能阻塞一个请求线程上百秒；这些同步 `def` 端点共享 anyio 默认 40 线程池，同时打满后 `/auth/login` 等所有同步接口一起排队——可观测到的"平台整体卡住"往往源于此。且没有任何请求级超时/取消，客户端断开也不会中断底层调用。
- **业界做法**：把这类聚合改成"后台任务定期采样 → 写时序表/缓存 → 接口只读快照"，或至少接口返回 last-known 值并带 `stale_seconds`；确需实时则用 `httpx.AsyncClient`（连接池复用）+ `asyncio.gather` + `asyncio.timeout(...)` 做全局预算；对客户内网的探测统一走采集器（项目已有 `manager.send_command` 通道，`metrics.py:95-147` 也已经这么做了，只是本地直连路径仍串行）。
- **可采纳性**：moderate。改 `metrics.py` 为 async + 并发不难，但"快照 + 缓存"要引入存储与失效策略，涉及前端字段语义（`available` 与新鲜度）。
- **建议**：先把 `get_metrics` 各采集函数改成 async 并发执行并加总预算（如 8s），超时部分返回 `available=false` 并标 `reason`；把 `run_patrol` 的 OOM/趋势巡检挪到 Celery（`check_container_ooms`/`run_trend_patrol` 本就是定时任务的一部分，见 `scheduled.py:81-88`），接口只回 202 + 任务 id。预期收益：控制台接口 p99 有界，线程池不再被监控页面独占。

### [medium] 诊断证据用"读整段 JSON→改→写回"落库，8 线程工具池并发写会丢证据

- **证据**：`backend/app/services/diagnostics/service.py:333-390` 的 `_progress_recorder.record`：每次工具事件新开一个 `Session(engine)`（`:340`），`report = session.get(DiagnosisReport, report_id)`（无 `with_for_update`），`items = [dict(item) for item in (report.evidence or [])]`（`:346`），就地改完后 `report.evidence = items; session.add(report); session.commit()`（`:386-388`）。而工具是在**进程级共享线程池**里并发跑的：`backend/app/agent/harness/execution.py:23` 的 `ThreadPoolExecutor(max_workers=8, thread_name_prefix='diagnosis-tool')`，`ExecutionTracker.submit`（`execution.py:33-43`）直接提交到该池，因此 `tool_start`/`tool_end` 事件天然并发（代码注释自己也承认"工具可能被并行调用"，见 `service.py:214`、`:289`）。
- **风险**：两个线程同时"读→改→写"同一 JSON 列，后提交者覆盖先提交者（无行锁、无版本号），被覆盖的工具调用会从 `evidence` 里消失——终态 `_finalize_report` 会用 `result.evidence` 整体覆写（`service.py:610`）所以最终可能有全量，但**运行中的实时证据面板**（`answer_stream`/SSE 快照的数据源）会随机缺步骤，且异常/关停路径下（终态未写入时）这些丢失就是永久的，审计链不完整。
- **业界做法**：追加型数据不要塞在一个 JSON 列里做读改写——用子表 `diagnosis_report_evidence(report_id, call_id, ...)` + `INSERT ... ON CONFLICT (report_id, call_id) DO UPDATE`（幂等且并发安全），读时聚合；短期也可以在改前 `SELECT ... FOR UPDATE`（项目已有 `row_lock_clause`，`session.py:33-35`）或把进度写收敛到单线程队列。
- **可采纳性**：moderate。子表方案要迁移 + 改 `evidence` 的所有读写点（`reports.py:80-113`、前端出参）；加行锁是一行改动但会与诊断主流程争锁，需要评估。推荐先做"单写者队列"过渡。
- **建议**：短期把 `_progress_recorder` 的写操作投递到一个专用单线程执行器（保证同报告串行）并在 `session.get` 上加 `with_for_update()`；中期迁移到 `diagnosis_report_evidence` 子表。预期收益：实时证据不再随机丢失，终态与审计对得上。

### [medium] 效率分析把整个 org 的消息与审计日志拉进内存做 Python 聚合

- **证据**：`backend/app/services/analytics.py:43-56` 对 `SystemMessage` 与 `AuditLog` 各做一次 `select(...).all()`（仅按 `org_id`、`created_at >= since` 过滤），`analytics.py:104-111` 再全量取一次 `MonitoredSystem`，`analytics.py:139-145` 取全部 `ActionWorkflow`；所有统计（success rate、avg duration、trend、per-system 汇总）都在 Python 里遍历（`analytics.py:57-176`）。`days` 上限 90（`analytics.py:39`）。端点 `backend/app/api/analytics.py:13-25` 是无缓存的同步 `def`。
- **风险**：审计日志是**每次工具调用都写一条**（`service.py:1294-1310` 的 `agent.tool` 事件），一个活跃租户 90 天的 `audit_logs` 很容易到十万～百万行，每行还带 `input`/`output` 两个 JSON（诊断的 output 含整段答案与 tool_calls）。这个接口会：拉取数百 MB 到 API 进程内存 → 有 OOM 风险；同步遍历期间占住一个线程池槽很久；同一 org 的并发展板刷新互相放大。多个租户共用一套 API 进程时是典型的 noisy-neighbor。
- **业界做法**：聚合下推到 SQL（`GROUP BY date_trunc('day', created_at)`、`FILTER (WHERE ...)`、`percentile_cont`），或预聚合成日报表/物化视图（本项目已有 `monitoring/daily_report.py` 的定时任务，天然是 rollup 的落点）；审计这类大表按时间分区 + 冷热分离（只把聚合结果留在 PG，明细进对象存储/ClickHouse）；接口加短 TTL 缓存（同一 org 的看板 1~5 分钟）。
- **可采纳性**：moderate。SQL 化这一份函数（约 180 行 Python 统计）是实打实的工作量，且要注意 SQLite 兼容（项目在无 Docker 时回退 SQLite，`core/database.py:12`），部分函数（`date_trunc`）需要分支。
- **建议**：把 `get_efficiency_analytics` 拆成"每日 rollup 任务（Celery beat 落 `analytics_daily` 表）+ 接口只读 rollup"，先覆盖 `days>=7` 的查询；短期至少给该端点加 `limit` 化的时间分桶与 per-org 缓存。预期收益：看板接口从"秒级 + 高内存"变成常数开销，OOM 风险消失。

### [medium] 归档下载端点忽略租户校验，而它的 token 会随答案进入第三方 LLM 与审计日志

- **证据**：`backend/app/api/diagnose.py:397-408`：

  ```python
  @router.get("/{system_id}/diagnosis-artifacts/{token}")
  def download_diagnosis_artifact(system_id: int, token: str):
      """下载 agent 取回的归档附件（凭不可猜的 token，免登录）。"""
      found = find_artifact(token)      # system_id 完全未参与校验
  ```

  `backend/app/services/diagnostics/artifacts.py:36-42` 的 `find_artifact` 在全局目录 `glob(f"{token}__*")`，不区分 org/system。而 token 是在工具返回里给出的（`service.py:154-157`、`:195-197` 把 `.../diagnosis-artifacts/{token}` 作为工具结果字符串返回），该字符串会进入模型对话、`report.tool_calls`/`answer` 与审计 `output`（`service.py:1226`）。
- **风险**：工具结果整体发给外部模型供应商，等于把"下载凭据"外发给第三方；答案/审计里出现的链接也可能被转发、贴进工单或飞书群。任何人拿到这个 32 位 token 就能下载客户归档文件（可能是日志、任务产物），且端点不看 `system_id`、无鉴权、目录全局共享——一旦 token 泄漏就是跨租户任意文件读取。
- **业界做法**：短期凭据（signed URL）应过期并有归属——下载走 `GET /systems/{id}/diagnosis-artifacts/{token}` 时校验 `token` 绑定的 `org_id/system_id`，或直接签发带 `exp` 的 HMAC 签名 URL（S3 presigned 模式）；凭据不应出现在给模型看的内容里，而应只回给用户界面（工具结果里给"附件已生成，见报告附件区"）；下载响应带 `Content-Disposition` + 一次性/限次使用。
- **可采纳性**：moderate（改动本身很小，但要新增附件归属元数据与迁移、并同步改动工具返回文案与前端入口，所以不能算 easy）。把 `artifacts.save_artifact` 的元数据（org_id/system_id/report_id/expires_at）落一张小表或文件名前缀里，下载时比对即可；工具返回里去掉 URL 是纯文案改动。若坚持"免登录"，至少要做到"不可枚举 + 有归属 + 有 TTL"。
- **建议**：`artifacts.py` 改为 `save_artifact(org_id, system_id, file_name, content)` 并返回带归属的 token（或签名 token），`download_diagnosis_artifact` 增加 `token` 归属校验 + 过期校验；工具返回文本里不再直接暴露下载 URL（改由报告附件接口按 `org_id` 鉴权列出）。预期收益：消掉一条凭据外泄即可跨租户读文件的路径。

### [medium] Celery 队列与重试语义过于粗糙：无全局 acks_late/time limit、固定 10s 重试、大正文进 broker

- **证据**：`backend/app/workers/celery.py:26-35` 只设了 serializer/`worker_prefetch_multiplier=1`/连接超时与 `task_routes`，**没有** `task_acks_late`、`task_reject_on_worker_lost`、`task_time_limit`/`task_soft_time_limit`、`worker_max_tasks_per_child`、`result_expires`；`backend/app/workers/tasks.py:21-26` 与 `:29-51` 用 `raise self.retry(countdown=10)` 固定间隔重试 2 次，且触发条件是"结果 `ok` 为假"（业务性失败也重试，无退避无 jitter）；`backend/app/services/log_analysis.py:50-65` 把日志正文（`MAX_LOG_CHARS = 180_000`，`log_analysis.py:22`）整体作为任务参数 `.delay(...)` 投给 broker，重试时再发一遍。
- **风险**：(1) 默认 early-ack：worker 被 OOM/SIGKILL 时消息已 ack → 日志分析/消息处理任务静默丢失（诊断任务单独配了 `acks_late=True`，见 `backend/app/workers/diagnoses.py:5-6`，说明团队知道该怎么做，只是没推广）；(2) 无时间限制：一个卡住的任务永久占住 prefork 槽位（诊断链路靠 `threading.Timer` 和 reaper 兜底，但 `process_open_message` 这类没有）；(3) 固定 10s 重试对"内容非法/权限错误"这类永久失败只是把错误放大 3 倍，且无 jitter 时批量重试会形成尖峰；(4) 180k 字符的消息在 Redis broker 与 result backend 里各存一份，重试再放大，队列内存可被单租户的上传打爆。
- **业界做法**：`task_acks_late=True` + `task_reject_on_worker_lost=True` + `task_time_limit/task_soft_time_limit`；重试用 `self.retry(exc=..., countdown=2**n, max_retries=..., jitter)` 或 `retry_backoff=True, retry_jitter=True`，并且只对可重试异常重试（区分 `TransientError`/`PermanentError`）；大负载放对象存储/S3 传引用（`payload_uri`），队列只传元数据；`result_expires` 收敛、`ignore_result` 用于无返回值的任务。
- **可采纳性**：easy。都是 `celery.conf` 与装饰器层面的改动，不需要动业务逻辑；唯一需要小心的是开 `acks_late` 后任务必须幂等（`process_open_message`/`process_open_log_analysis` 按 message_id 操作，基本幂等）。
- **建议**：在 `celery.py` 补 `task_acks_late=True`、`task_reject_on_worker_lost=True`、`task_time_limit=900/soft=840`、`worker_max_tasks_per_child=100`、`result_expires=3600`；`tasks.py` 的重试改为带退避 + 只重试可重试异常；`log_analysis.py` 把正文落盘/对象存储后只传引用与 `message_id`。预期收益：worker 崩溃不再静默丢任务，重试不再放大尖峰，broker 内存可控。

### [low] 只读业务数据源没有语句超时：配置里的 `timeout_seconds` 对非 SQLite 完全不生效

- **证据**：`backend/app/services/data_analysis.py:531-535` 的 `_connect_args` 只在 `url.startswith("sqlite")` 时返回 `{"timeout": ...}`，PostgreSQL/MySQL 一律返回 `{}`；查询在 `data_analysis.py:342-348` 用 `create_engine(url, connect_args=_connect_args(...))` 现建现销毁（无连接池、无 `connect_timeout`、无 `statement_timeout`/`options=-c statement_timeout=...`），并且只 `fetchmany(max_rows)` 就 `conn.rollback()`。
- **风险**：客户只读库上一条慢查询（大表 `COUNT(*)`、缺索引的 `ORDER BY`，`data_analysis.py:100-108` 与 `:187-218` 会生成这类 SQL）会把 Agent 的工具调用挂住，链路里唯一的边界是诊断运行的 600s 超时（`backend/app/services/diagnostics/jobs.py:17` 的 `RUN_SECONDS`），期间该会话的预算一直被占、worker 槽位被占；`timeout_seconds` 这个配置项给出的是"已经配置了超时"的错觉。另外每查询一次 `create_engine`+`dispose` 也放弃了连接复用。
- **业界做法**：在连接层强制超时——PG 用 `connect_args={"options": "-c statement_timeout=5000 -c idle_in_transaction_session_timeout=5000"}` 或 `SET LOCAL statement_timeout`；MySQL 用 `read_timeout`/`max_execution_time`；配 `pool_pre_ping` 与按数据源缓存的 engine（只读账号 + 只读副本）。只读通道还应有查询代价预检（`EXPLAIN` 拒绝全表扫描）。
- **可采纳性**：easy。改 `_connect_args` 按方言返回超时参数即可立刻生效；engine 缓存需要一个以 URL 为键的小字典，属于顺手优化。
- **建议**：`_connect_args` 为 PG/MySQL 补 `connect_timeout` 与 `statement_timeout` 等价配置（沿用 `config["timeout_seconds"]`，默认 5s），并在 `run_readonly_query` 里把超时异常转成明确的"查询超时"工具返回；顺带按 URL 缓存 engine。预期收益：慢查询不再拖住诊断 worker，配置语义与实际行为一致。

## 对抗性复核

- **飞书 Webhook 无签名校验、无鉴权，任何人都能驱动面向客户内网的特权诊断 Agent 并指定回发会话** → confirmed（修正严重度：critical）
  逐条核实，证据属实，且我补了两条它没写的加重项。① 入口无鉴权成立：`backend/app/api/feishu_webhook.py:8-11` 无任何 `Depends`；`backend/app/api/__init__.py:50` 确认 router 已挂载；`backend/app/main.py:140` 只加了 `CORSMiddleware`，无全局鉴权中间件。② token 短路成立：`backend/app/services/notifications/webhook.py:91` 的 `and token` 使"不带 token 即放行"，真实事件分支（`:97-117`）全程无校验；全仓 grep `X-Lark`/`encrypt_key`/lark_signature 零命中。③ 公网可达不是推测：`~/.cloudflared/config.yml` 有 `webhook.tiancaizhaozhao.dpdns.org → http://localhost:8000` 的 ingress，**绕过 Caddy**（`Caddyfile:19` 的 `path /feishu*` 只是第二条路径），我 GET 该域名得到 502（隧道在线、源站当时未启动）。④ 攻击者不需要知道任何凭据：`webhook.py:115` 的 `header.get("app_id", settings.feishu_app_id)` 使其**省略 app_id 就落到平台自己的应用**（`.env:18` 已配 `FEISHU_APP_ID=cli_aa…`），`resolve_app_secret`（`:52-59`）从库中取该租户解密后的 app_secret，`find_systems_by_app_id`（`:40-49`）跨全部 org 全表匹配后 `descriptors[0]` 直接进 `diagnose()`（`:74-76`）。⑤ 伪造 message_id 不构成阻断：`feishu.py` 的 `add_reaction` 失败只 `log.debug` 并返回 `""`，不抛异常。⑥ 它漏掉的两点加重定级：descriptor 携带**已解密**的服务凭据（`services/descriptors/builder.py:24-48` 调 `decrypt_sensitive_fields`）；而 `tools.py:492`/`:515` 的"提示注入防护"读的是 `ctx.deps.intent_text or ctx.deps.question`，webhook 路径下 `intent_text=original_question`（`services/diagnostics/service.py:719`）就是攻击者写的 `text`，护栏可被同一段文本自带关键词绕过。
  唯一需要保留的限定（报告自己也括注了）：把答案投到"攻击者自己的会话"要求机器人已在该 chat 内，chat_id 不可枚举。但这不动摇 critical——无鉴权的**特权动作本身**（按租户凭据跑诊断、读客户业务库/日志、经采集器在客户内网执行命令、消耗 LLM 预算）不需要攻击者看到回包即可完成。无可证伪空间。

- **`query_business_dataset` 的相对日期必然抛 `NameError`，而工具文档正好教模型这么传** → confirmed（修正严重度：high）
  用项目 venv 直接跑真函数：`_parse_date("")`/`("today")`/`("今天")`/`("昨天")` 全部 `NameError: name 'today' is not defined`，`"2026-01-02"` 正常返回。`backend/app/services/data_analysis.py:398,400` 引用 `today`，模块级只有 `from datetime import date, datetime, timedelta, timezone`（`:5`），全仓 `grep -rn "\btoday\s*="` 无赋值。文档确实在教这条路：`backend/app/agent/diagnostics/tools.py:449`"查询今天时两个都填今天"；链路 `tools.py:454-456` → `services/diagnostics/service.py:255-266` → `query_readonly_dataset`（`data_analysis.py:407,434-439`），异常被 `service.py:265-266` 吞成 `业务数据集查询失败：name 'today' is not defined` 回给模型。
  两处应纠正报告的小瑕疵（不改结论）：`:434`/`:437` 的 `if date_from:`/`if date_to:` 守卫让**空串根本走不到 `_parse_date`**，所以"必然抛"只在传相对关键字（或非 ISO 文本）时成立，报告列的 `""` 用例在实际调用路径上不可达；`backend/tests` 不是"当前为空"而是**不存在**（`ls` 报 No such file or directory）。`high` 保留：被文档主推的取证路径 100% 失效，且本仓自己就有 `b8047bb feat(guard): 标记"success 但没做任何取证"的报告`，说明团队已把"没干活像干完了"视为需专门设防的失败模式。

- **Celery beat 的 15 秒巡检无分布式锁、单事务跨全部租户：重叠执行重复告警，单个系统异常拖垮整批** → confirmed（修正严重度：high）
  结构性质逐行核实：`backend/app/workers/celery.py:15-18` 的 `schedule: 15.0` 无 `options={"expires":…}`；`workers/tasks.py:10-12` 任务内无锁无幂等键；`services/monitoring/scheduled.py:56-57` 一个 `Session(engine)` + `select(MonitoredSystem).all()`（**确实无 org 过滤、跨全部租户**），`system.last_report_at = utcnow()` 在 `:91`、唯一 `session.commit()` 在 `:98`，`is_due`（`:44-49`）正是读 `last_report_at`，循环内异常（`:94-96`）只 log 不 rollback。
  我补了两个它没做的验证。① "毒化会话拖垮整批"的机制用同版本 SQLAlchemy 实测复现：一次 flush 失败（IntegrityError）后，同 session 内后续 `add/flush` 抛 `PendingRollbackError`、后续 `query()` 抛 `PendingRollbackError`、末尾 `commit()` 同样抛——即"后续系统全部失败 + `:98` commit 抛异常、整批结果丢失"成立，且任务没有外层捕获、`max_retries=2` 却从未调用 `self.retry`。② 并发是配置事实：`scripts/stack-launcher.sh:265` 用 `--concurrency=2`，`deploy/docker-compose.yml:66` 的 platform-worker 未指定并发即 CPU 数；beat 无条件每 15s 投递，单轮 >15s 时同队列两份任务被两个 prefork 进程**同时**执行、各自读到未提交的旧 `last_report_at`。
  但它风险叙述里的"放大 N 倍"应下调（方向不变）：积压副本只有与在跑副本**时间重叠**时才重复；第一份一旦提交，后续排队副本会在 `is_due` 整体跳过，故放大上界≈worker 并发数（2），不是随积压深度增长。且 `settings.alert_cooldown_seconds=3600`（`core/config.py:142`）配 `recently_alerted_services`（`notifications/alerts.py:88-110,430-434`）会压掉**顺序**重复告警，只有并发副本因互相看不到对方未提交的告警才漏过——"重复告警"真实但被限制在 ~2 份，够不上"告警风暴"。真正支撑 high 的是第二条：单个系统的 DB 级异常会让该轮之后所有租户的巡检全部失败且批量结果丢失，表现为"巡检静默停摆直到坏数据被修"。

- **OpenAPI 幂等键靠"全表加载 + Python 比对"实现，且没有唯一约束兜底** → confirmed（修正严重度：high）
  `backend/app/repositories/messages.py:82-101` 核实：先 `session.get` 试数字 ID，否则 `select(SystemMessage).where(system_id==…, source=="openapi")` **无 limit 无分页 `.all()`**，再在 Python 里比 `(message.related or {}).get("request_id")`。调用点与报告一致：`services/openapi.py:55`、`:147`、`:362`，外加它**漏掉的第四处** `services/log_analysis.py:86`（日志分析上传同样走这条幂等查询）。兜底确实不存在：`models/messages.py:27` 的 `related` 是裸 JSON 列，`backend/migrations/versions/` 内全部迁移对 `system_messages` 只建了 org_id/system_id/status/severity/message_type/created_at/incident_id 的**非唯一**索引（`4b6a8c2d91f0_add_system_messages.py:47-52`、`b8c9d0e1f2a3_add_incidents.py:48-51`），没有任何 `request_id` 生成列或唯一约束。写入侧是普通 insert + `session.commit()`（`services/openapi.py:103`，重复处理派发在 `:116-123`），故两个同时到达的同 `request_id` 请求会各自"查无→插入→提交"，幂等承诺在 READ COMMITTED 下不成立；同仓 `services/diagnostics/submissions.py:23-28` 的 `INSERT … ON CONFLICT DO NOTHING` 正是报告所指的对照模板。`api/openapi.py:56-80` 只有 token/scope/IP 白名单，没有锁或二次去重——不存在被忽略的缓解措施。
  只纠一处措辞：标题的"全表加载"不准确，查询被 `system_id + source='openapi'` 限定，是"该系统历史 openapi 消息全量"而非整张表；报告正文（"不带 limit/all() 全量取回"）反而是准确的。`high` 保留：`request_id` 在 schema 里为必填（`schemas/openapi.py:12,27,38,49,63`），幂等恰好在"超时后并发重试"这个它唯一存在的场景下失效，后果是客户可见的重复告警 + 重复 LLM 处理与投递，同时叠加一条随该客户上报量线性增长的全量扫描。
