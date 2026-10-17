# 工程质量 体检

审计对象：`/Users/zhangsidong/ai-ops-agent`（HEAD = `14bc7ff`，分支 `feat/multi-tenant-aiops-platform`，与 `origin` 同步）
审计口径：仅统计自有代码；已排除 `.dev-stack/`（含 `sandbox-venv`）、`node_modules/`、`backend/.venv/`、`dist*/`。

## 现状设计

这是一个单仓多组件的运维 Agent 平台：`backend/`（FastAPI + SQLModel + alembic + Celery，27k 行 Python，210 个模块）、`frontend/`（Vue3 + Vite）、`collector/` + `shared/`（部署到客户内网的出站采集器）、`agent-runtime/`（Pi 只读试点）、`deploy/` 与根 `docker-compose.yml` 承载两种不同的本地/平台拓扑。依赖锁定是"双轨"的：开发侧由 `scripts/prepare-local-stack.py` 驱动 `uv sync --frozen`（`backend/uv.lock`，120 包）与 `npm ci`（`frontend/package-lock.json`，lockfileVersion 3），而生产侧镜像走 `backend/Dockerfile` 的 `pip install`，不读锁文件。数据库演进同样是双轨：Postgres 启动时自动 `alembic upgrade head`（28 个 revision、单 head），而默认的 SQLite 兜底路径用 `SQLModel.metadata.create_all()` 加一份手写的列补丁表 `_SQLITE_REQUIRED_COLUMNS`。代码里没有任何静态检查配置——无 ruff、无 mypy、无 ESLint、无 pre-commit，异常处理靠 `except Exception` 加 `# noqa: BLE001` 注释自我约束，但没有任何工具会读这些注释。工程纪律的另一面是提交质量：134 个提交中 128 个符合 Conventional Commits，正文普遍写清根因、改动与实测数字。目前工作区与 HEAD 严重脱节：169 项未提交变更，测试、评测与 CI 已被删除，而新的 harness/runtime 核心仅在文件系统上存在。

## 做得好的地方

- **最高频变动的 AI 依赖是真精确锁定，不是"看起来锁了"**：`backend/pyproject.toml:16` 唯一一条 `pydantic-ai-slim[openai]==2.55.0`；其余 21 条都是 `>=` 下限。这是有意的取舍，提交 `7c35fa4`（"升级 pydantic-ai 2.0.0 → 2.55.0（原生取消/历史继续能力）"）说明这个包一旦漂移会破坏取消/续跑语义，所以只有它值得钉死。
- **开发路径的锁定是真实生效的，不是摆设**：`scripts/prepare-local-stack.py:25` `uv sync --directory backend --frozen`、`:31` `npm ci --ignore-scripts --no-fund`、`frontend/Dockerfile:5` `npm ci`。实测 `cd backend && uv lock --check` → `Resolved 120 packages`，退出码 0，工作区的 `pyproject.toml` 与 `uv.lock` 当前是一致的。
- **alembic 迁移链健康，模型与迁移无缺口**：28 个 revision、单一 head（`a1b2c3d4e5f`）、单一 root（`238f4c603dd8`）、无断链、无"文件名与 revision id 不符"。用 AST/正则比对 `app/models/*.py` 的 24 张 `__tablename__` 与 `migrations/versions/*` 的 26 处 `create_table`：**没有任何模型表缺迁移**（多出的 `knowledge_chunks`/`knowledge_index_meta` 由 `f7a8b9c0d1e2` 建表，属于手工 SQL/扩展表）。
- **启动期迁移的日志陷阱被实测修掉并留了注释**：`backend/app/core/database.py:90-100` 显式设置 `cfg.attributes["configure_logger"] = False`，因为 alembic 的 `fileConfig` 默认 `disable_existing_loggers=True` 会把 uvicorn 与 `app.*` 全部静默禁用——这类"上线后日志消失"的问题通常要靠几天排查，这里以注释形式固化了结论。
- **错误处理的基本纪律在线**：210 个自有 Python 文件里 **裸 `except:` 为 0 处**（`grep -rnE 'except\s*:'` 无匹配），`except BaseException` 仅 4 处；API 层把领域异常映射为语义状态码而非一律 500，例如 `backend/app/api/health.py:25-26`（`LookupError` → 404）、`:137-140`（`RuntimeError` → 503）。
- **不可信内容进 `v-html` 前做了净化**：`frontend/src/features/diagnostics/useDiagnosisChat.js:426` `DOMPurify.sanitize(markedParser.parse(text || ''))`，配 `DiagnosisPanel.vue:287,301` 的渲染路径。模型输出属不可信输入，这里选择"渲染但净化"而不是"直接 v-html"，是合理且有意识的取舍。
- **历史文档诚实且有量化**：`ARCHITECTURE.md:529-556` 有一张"工程取舍"表（如"审计写失败则不执行工具""显式选模型不允许静默回退"）和 9 条自评改进点，含"技能路由的注释与代码不一致（`KEYWORD_STRONG_SCORE` 定义了但代码从未使用）"这类自曝缺陷。同时 `backend/app` 内 `print()` 0 处、全仓 `TODO/FIXME/HACK` 0 处、`.env` 未被跟踪（`git ls-files .env` 无匹配）并有维护中的 `.env.example`。

## 发现

### [high] 自有测试与 CI 门禁已全部移除，且 pytest 依赖被一并从锁定环境删掉

- **证据**：
  - 磁盘上自有测试文件数 = **0**：`find . -name 'test_*.py' -o -name 'conftest.py'`（排除 `node_modules`/`.dev-stack`/`.venv`）无任何结果。
  - `git status --short` 显示 **85 项删除**，其中测试/评测相关占 40 项以上：`backend/tests/` 下 38 项（36 个 `test_*.py`，含 `test_system_services.py` 2367 行，另加 `.gitkeep`/`__init__.py`）、`evals/` 整个评测脚手架（含 `gate.py`/`harness.py`/`thresholds.json`/2 个测试文件）、`scripts/eval-gate.sh`、`.github/workflows/eval-gate.yml`。
  - HEAD 上这些文件仍在：`backend/tests/*.py` **5674 行** + `evals/*.py` 3407 行，两者合计 **322 个 test 函数**；最后一次提交 `14bc7ff` 的正文写着"后端 276 全绿；评测 46 全绿；冒烟门禁通过（指标无回归）"。
  - 依赖侧同步被拆：`git diff backend/pyproject.toml` 删除了 `default-groups = ["dev"]`、`[dependency-groups] dev = ["pytest>=9.1.1"]` 与 `[tool.pytest.ini_options] pythonpath = [".."]`；`backend/uv.lock` 因此 `51 deletions`，`grep -c pytest backend/uv.lock` → `0`。
  - 后果可复现：`backend/.venv/bin/python -m pytest --version` → `No module named pytest`（该 venv 正是 `prepare-local-stack.py:25` 用 `--frozen` 同步出来的）。
  - 覆盖面本来也偏：即使在 HEAD，测试对 `app.agent.harness` 的引用数为 **0**、`app.agent.runtime` 为 **0**——而这两个包共 1865 行，是 `ARCHITECTURE.md` §4.2/4.3 描述的"统一执行边界"和"双引擎运行时"，即当前架构的核心器官。
  - `README.md:3` 把这件事写成有意为之："本仓库保留当前应用、配置、依赖锁文件与运行入口。测试、评测、历史设计文档和一次性验收脚本已移出。"
- **风险**：平台此后所有改动都无回归网。触发条件是"下一次改提示词/工具/网关"，而历史提交恰恰证明这类改动需要回归网——`51dad11` 的标题是"升级 pydantic-ai + 修 3 个被静默吞掉的启动期 bug"。影响面是整个 backend（27k 行）与 harness/runtime 这两个刚抽出来、从未被测过的核心包；且因为 `pytest` 已不在锁里，即使有人手写测试也跑不起来，退化是自我强化的。
- **业界做法**：CI 里以 `uv sync --frozen` 装齐 dev 组后跑 `pytest`，把该 job 设为 PR 的 required status check（GitHub branch protection）；用 `pytest --cov` + `--cov-fail-under` 设覆盖率下限；Agent 平台尤其要对"工具契约/证据链/取消续跑"这类不变量做契约测试（本仓已有的 `test_cancel_bus.py`、`test_evidence_guard.py` 正是此类）。
- **可采纳性**：**moderate**。恢复测试本体是 `git checkout HEAD -- backend/tests evals scripts/eval-gate.sh .github/workflows/eval-gate.yml` 一行的事，dev 组回到 `pyproject.toml` 后 `uv lock` 即可；但 HEAD 的测试是针对"harness 之前"的架构写的（runner 现在经 `harness/gateway.py` 与 `runtime/factory.py`），恢复后必然要修一批，且 harness/runtime 的测试得从零写。成本真实，收益也真实（这是唯一能阻止静默回归的机制）。
- **建议**：①`git checkout HEAD -- backend/tests evals scripts/eval-gate.sh .github/workflows/eval-gate.yml`；②在 `backend/pyproject.toml` 恢复 `[dependency-groups] dev = ["pytest>=9.1.1"]`、`default-groups = ["dev"]`、`[tool.pytest.ini_options]`，重跑 `uv lock` 并确认 `uv lock --check` 通过；③逐个修恢复后失败的用例，先跑通再逐步补 `backend/tests/test_harness_*.py`（网关审计失败即拒绝、预算熔断、委派降级）与 `test_runtime_*.py`（引擎选择/兼容性拒绝/历史续跑）；④CI 恢复后开启 branch protection，让门禁不可绕过。

### [high] 生产镜像构建完全绕过 uv.lock，锁定只在开发机上成立

- **证据**：
  - `backend/Dockerfile:10` `RUN pip install --no-cache-dir /opt/aiops/backend`——镜像里没有 `uv`，也不消费 `backend/uv.lock`，直接在构建时按 `pyproject.toml` 的 `>=` 约束向 PyPI 求解。
  - `deploy/docker-compose.yml:31`、`:56`、`:79` 三个服务（api / worker / beat）全部 `dockerfile: backend/Dockerfile`，即平台的所有运行组件都走这条路径。
  - 约束是 `backend/pyproject.toml:6-30`：22 条里 21 条为开区间下限。锁文件里已经解出的版本本身就说明跨度：`langfuse>=3.0` → **4.12.0**、`langgraph>=0.2` → **1.2.6**、`mcp>=2.0` → 2.3.0、`fastapi>=0.115` → 0.138.1（连带 `starlette` 1.3.1）。这些全是跨大版本。
  - 客户侧更松：`collector/requirements.txt:1` 是裸 `requests`（无任何版本），`:2-5` 是 `>=` 下限；`collector/Dockerfile:7-8` 直接 `pip install -r`。`collector/README.md` 说明该采集器"装在客户自己网络内"，引导客户 `pip install requests websockets`。
  - 根 `docker-compose.yml:66,79,88,99,117` 使用 `prom/node-exporter:latest`、`danielqsj/kafka-exporter:latest`、`prom/mysqld-exporter:latest`、`prom/prometheus:latest`、`ollama/ollama:latest` 五个 `:latest`。
- **风险**：`README.md` 声称"保留…依赖锁文件"、`prepare-local-stack.py:25` 也用 `--frozen`，于是"已锁定"成为共识；但真正部署的产物不受锁保护。条件：明天重新 `docker build` 就会得到与今天不同（可能跨大版本）的依赖树——`langfuse 3.x→4.x`、`langgraph 0.2→1.x` 这种升级在本仓历史里都伴随过行为变化（`51dad11` 即因升级 3 个启动期 bug）。影响面是全平台部署 + 每一个客户现场采集器；后果是不可复现、无法二分定位、无法为已知良好的版本构建补丁，同时供应链面完全敞开（任何被投毒的上游新版本会直接进生产镜像）。
- **业界做法**：容器内用锁安装——`COPY backend/uv.lock backend/pyproject.toml` 后 `uv sync --frozen --no-dev --no-install-project`（多阶段构建，`uv` 只出现在 builder 阶段）；或从锁导出 `uv export --frozen --no-dev --format requirements-txt` 再 `pip install --require-hashes -r`。客户侧交付物用同样的导出 + 哈希，并把采集器版本写进上报握手，便于"哪个版本的采集器行为异常"可查。基础设施镜像固定 tag/digest，不用 `:latest`。
- **可采纳性**：**easy**。锁定资产已经存在且已验证一致（`uv lock --check` 通过），`--frozen` 也已经是本地路径的既有用法，改动集中在 `backend/Dockerfile`（10 行）与 `collector/`（新增一个导出的 requirements 文件）。
- **建议**：①把 `backend/Dockerfile` 改成 `uv sync --frozen --no-dev --no-install-project`，`CMD` 走 `.venv/bin/uvicorn`；②`collector/requirements.txt` 换成带版本（理想是哈希）的锁定清单，并在 CI 里校验它与 `uv.lock` 同步；③`docker-compose.yml` 的 `:latest` 改为固定 tag（必要时加 digest）；④加一条 CI 校验：`uv lock --check` + `uv export --frozen` 与提交的 requirements 无 diff。

### [high] 审计写入失败被静默吞掉，与 ARCHITECTURE.md 明示的硬规则相反

- **证据**：
  - `backend/app/services/diagnostics/service.py:302-308`：在**已成功查询客户只读数据库并把行返回给模型**之后，审计写入被整体包在 `try` 里，异常分支是 `except Exception:  # noqa: BLE001` + `pass`——没有日志、没有计数、没有降级记录：
    ```
    with Session(engine) as audit_session:
        record_audit_event(..., event_type="data_analysis.dataset_queried", ...)
    except Exception:  # noqa: BLE001
        pass
    return f"{header} 共 {len(rows)} 行（最多展示 40 行）：\n{payload}{total_line}"
    ```
  - 同一文件的 `:307` 与 `:1138` 是两处"整段函数体只有 `pass`"的处理器；AST 扫描 `backend/app` 得到**共 16 处**这样的处理器（`service.py:307,1138`、`stream.py:74`、`monitoring/metrics.py:213,325,369,388`、`agent/workflows/runner.py:87`、`main.py:81`、`sandbox.py:42,82`、`answer_stream.py:116,143` 等），其中只有部分带 `# noqa` 说明，全部不产生任何日志或指标。
  - 与之对立的规则写在 `ARCHITECTURE.md:531`：「审计写失败则不执行工具 | 宁可失败，也不产生"无审计的执行"」。
  - 该处理器本身注释说明为什么用独立 Session（`:289` "工具会被并行调用，复用诊断主 Session 会污染事务"）——即这条路径天然运行在高并发、易发生锁等待/连接池耗尽的条件下，不是理论例外。
  - 全局风格：`backend/`+`shared/`+`collector/` 共 193 处 `except Exception`（约每 140 行一次），105 处 `# noqa`（其中 84 处 `noqa: BLE001`），而仓库内**没有任何 ruff 配置**，这些抑制注释不会被执行。
- **风险**：`data_analysis.dataset_queried` 是"Agent 直接 SQL 读了客户业务库哪张表、取了多少行、SQL 是什么"的唯一留痕。触发条件：审计用的独立 Session 在并发工具调用下拿不到锁、连接超时或事务冲突——此时用户看到的是一次成功的数据查询，审计表里什么都没有，且没有日志、没有 counter、没有告警，事后无法发现，也无法向客户证明"读了多少数据"。影响面是多租户平台最敏感的取证/合规能力，属于"静默失效"这种最糟的失败模式。同一模式还使 `:1138` 的降级路径（Kafka 兜底失败）失去可观测性。
- **业界做法**：审计与业务同库同事务（transactional outbox / append-only ledger），失败即 fail-closed 并向上抛；若出于可用性必须 fail-open，则用本地 WAL 落盘重放 + Prometheus counter（如 `audit_write_failures_total{event_type=...}`）+ 告警规则，并在架构文档里显式声明这条取舍。SaaS 多租户审计通常还要求租户隔离的审计流与保留策略，静默丢行会直接导致 SOC2/ISO 27001 类审计不通过。
- **可采纳性**：**moderate**。"审计失败即拒绝执行"会把当前必然成功的只读查询变成可能失败的操作，是产品可用性取舍，需要确认；且 `dataset_queried` 在工具热路径上，改成同事务写需要把审计 Session 与查询 Session 的关系重新设计。最小可用改动（日志 + counter + 告警）成本很低，完整闭环（outbox）成本中等。
- **建议**：①立即给 `service.py:307` 与其余 15 处"整段 pass"补 `log.warning(..., exc_info=True)` 与模块级 counter（或至少 `metrics` 里加一个失败计数）；②把 `dataset_queried` 的审计改为 fail-closed，或在 `ARCHITECTURE.md` 里把规则改为"审计失败则记录到本地 WAL 并继续"，消除文档与代码的矛盾；③配置 ruff 的 `BLE` 规则集，让 `# noqa: BLE001` 从装饰变成真正的决策记录（见下一条）。

### [medium] 版本控制基线不可信：核心架构只存在于未提交的工作区，默认分支也不是真实代码

- **证据**：
  - `git status --short` 共 **169 项**；`git diff --shortstat` = `136 files changed, 2201 insertions(+), 13072 deletions(-)`。
  - 未跟踪的新代码 **26 个 .py**，含 `backend/app/agent/harness/`（17 个模块）与 `backend/app/agent/runtime/`（9 个模块）共 **1865 行**、`api/diagnosis_chats.py`、`api/internal_agent_tools.py`、`models/{chats,diagnosis_jobs,runtime_events,submissions}.py`、`services/diagnostics/{answer_stream,chats,jobs,stream,submissions,termination,verification}.py`、`workers/diagnoses.py`、6 个 alembic 迁移（`a1b2c3d4e5f`…`f0a1b2c3d4e5`）、`scripts/{prepare-local-stack,pi-runtime,export-ops-contract}.py`；`ARCHITECTURE.md`（575 行）也是 `??`。`agent-runtime/` 整个目录未被跟踪（`git ls-files agent-runtime` 无输出）。
  - 分支 `feat/multi-tenant-aiops-platform` 比 `master` 领先 **122 个提交**、落后 0（`master` 是其祖先），两者相差 **420 个文件**；`master` 停在 2026-06-28，树里还是 `agent/`、`business/`、`feishu_bot/`、`设计思路.md` 的旧形态。全仓无 PR 流程痕迹（无 `.github/PULL_REQUEST_TEMPLATE.md`、无 `CONTRIBUTING.md`），唯一 CI 文件已删除。
- **风险**：`origin/feat/multi-tenant-aiops-platform` 与 HEAD 同步，意味着**工作区里这批东西在远端不存在**——harness/runtime 这套当前架构的核心、6 个新迁移、整个测试与 CI 的删除动作，都只有一份副本在地盘上；磁盘故障即等于 1865 行核心架构与迁移历史永久丢失（这是本报告里唯一符合"数据丢失且无缓解"的路径）。次生影响：任何人 `git clone` 默认分支拿到的是 3.5 个月前的另一个项目；若照 `README.md` 的判断恢复 CI，CI 跑的是 HEAD 的旧世界，而不是实际运行的新代码，门禁结论与生产态不一致。
- **业界做法**：主干开发 + 短生命周期分支 + PR 评审（trunk-based / GitHub Flow）；大型重构用 strangler 模式分批小提交，让每个中间态都能构建和回滚；在制品用 feature flag 或 `git worktree` 隔离而不是留在未提交状态；默认分支始终等于可部署版本。
- **可采纳性**：**easy**（`git add -A` 后按语义拆 3–5 个提交即可，无需重构）。真正的成本是拆提交前要先决定测试/CI 是否恢复——建议先恢复（见第一条），再提交，避免把"删除测试"固化成一个独立提交。
- **建议**：①按语义分提交：`chore(repo): 移除测试/评测/一次性脚本并同步 README`、`feat(harness): 统一执行边界（catalog/gateway/budget/evidence）`、`feat(runtime): 双引擎运行时与历史续跑`、`feat(diagnose): 作业/会话/流式与迁移`、`docs: 架构文档入库`；②把 `ARCHITECTURE.md` 与 `agent-runtime/` 一并入库；③把 `feat/multi-tenant-aiops-platform` 合入 `master`（或把 `master` 重命名为 `legacy-2026-06` 并把特性分支设为默认），让默认分支等于可部署版本。

### [medium] SQLite 兜底 schema 是第二套手写迁移，且已与 alembic 漂移

- **证据**：
  - `backend/app/core/database.py:15-54` 维护 `_SQLITE_REQUIRED_COLUMNS`（手写的"表→新列→DDL 字符串"表），`:57-72` 的 `_ensure_sqlite_schema()` 逐列发 `ALTER TABLE ... ADD COLUMN`。
  - `:75-83` `init_db()` 按方言分叉：SQLite 走 `SQLModel.metadata.create_all(engine)` + `_ensure_sqlite_schema()`，**不跑 alembic**；Postgres 走 `:90-100` `_run_alembic_upgrade()`。而 `backend/app/core/config.py:48` 的 `database_url` 默认值就是 `sqlite:///.../dev.db`。
  - 漂移可验证：用脚本比对"迁移中 `add_column` 的列"与"补丁表里声明的列"，`diagnosis_reports` 缺少两列——
    - `migrations/versions/e6f7a8b9c0d1_add_diagnosis_business_context.py:18-21` 增加 `business_context`（模型：`backend/app/models/diagnostics.py:36`）；
    - `migrations/versions/d5e6f7a8b9c0_add_external_diagnosis_request.py:19-22` 增加 `external_request_id`（模型：`backend/app/models/diagnostics.py:20`）。
    这两列在 `_SQLITE_REQUIRED_COLUMNS` 中均不存在。
  - `.gitignore` 明确排除 `*.db`，README 也提到"本地历史备份（已迁移的 SQLite 库），不入库"——即受影响的库通常不在版本控制里，无法在 CI 里复现。
- **风险**：触发条件是"已有 dev.db 的机器升级到当前代码"。`create_all` 只补缺失的**表**，不会给已有表加列，于是任何读取 `diagnosis_reports` 的路径都会在运行期抛 `OperationalError: no such column: diagnosis_reports.business_context`——平台在本地/演示/POC 形态下直接不可用，且报错点远离根因（模型定义处）。影响面：所有沿用旧 SQLite 库的开发机与演示环境；且这套 shim 每加一个模型字段都要人工同步两处，漏一处就是同一个故障，属于会持续复发的结构性缺陷而非一次性疏漏。
- **业界做法**：单一迁移真源——所有方言（含 SQLite）都走 alembic，用 `op.batch_alter_table` 处理 SQLite 的 ALTER 限制；测试中用"空库 `alembic upgrade head`"与 `SQLModel.metadata` 做 `alembic check`（autogenerate 无 diff）断言，把漂移变成红灯而不是运行期异常。Django/SQLAlchemy 生态的通行做法是禁止在生产代码路径里手写 ALTER。
- **可采纳性**：**moderate**。需要把本地 SQLite 切到 alembic、把现有 SQLite 库通过 `alembic stamp` 纳管，并处理 `create_all` 时代遗留的库；改动集中在一个文件与启动路径，但涉及现有开发机的数据。
- **建议**：①统一走 `_run_alembic_upgrade()`，删除 `_SQLITE_REQUIRED_COLUMNS`/`_ensure_sqlite_schema`（SQLite 需要的 ALTER 用 `batch_alter_table` 重写受影响迁移）；②新增 CI 步骤 `alembic upgrade head`（空 SQLite 与空 Postgres 各一次）+ `alembic check`，让"模型改了但没写迁移"在 PR 阶段失败；③过渡期至少在 `_SQLITE_REQUIRED_COLUMNS` 补齐 `business_context`、`external_request_id` 并加注释指向两个迁移，避免当前可直接触发的崩溃。

### [medium] 无 ruff/mypy/ESLint 配置：105 处 noqa 是死配置，类型注解从不被校验

- **证据**：
  - 全仓不存在任何静态检查配置：无 `[tool.ruff]`、`[tool.mypy]`、`ruff.toml`、`mypy.ini`、`setup.cfg`、`.flake8`、`.pre-commit-config.yaml`（逐项 find/grep 均为空）；`grep -rni mypy` 在全仓（除 lock）**0 命中**；`backend/.venv` 里没有 `ruff`/`mypy`（`site-packages` 下仅有 `81d243bd2c585b0f4821__mypyc...so` 这类历史残留），即 mypy 曾在本机跑过但从未进入项目配置与依赖。
  - 但代码里到处是给 linter 看的抑制注释：`# noqa` 共 **105** 处，其中 `noqa: BLE001`（盲捕异常）**84** 处，另有 `F401`/`E402`/`E712`。没有任何工具会读它们——包括本报告 high 条目里那 16 处"整段 pass"。
  - 注解覆盖率（AST 统计 `backend/app` 210 个文件、950 个函数）：带返回注解 **619（65%）**，参数全注解 **642（67%）**；`type: ignore` **0** 处。注解事实上只作文档，`dict`/`Any` 泛化类型与运行期不一致不会被发现。
  - 前端同样零检查：`frontend/package.json:6-10` 只有 `dev`/`build`/`preview` 三个脚本，无 lint、无 test；无 `eslint`/`prettier`/`tsconfig`/`vitest` 配置文件；`frontend/src` 下 **0 个 .ts**、22 个 `.js`、33 个 `.vue`，即全部无类型。
- **风险**：`# noqa` 的存在说明作者清楚 BLE001 是风险（"我知道这里在吞异常"），但没人执行时它既不能阻止新增盲捕，也不能阻止有人在没有 `noqa` 的地方继续加——现状正是如此（16 处整段 `pass`，只有一部分带注释）。类型注解随重构失真是静默的：本仓刚把 `runner.py` 拆成 `runner.py` + `harness/*`（17 个模块），这类改造最容易留下过时注解，而 65% 的注解覆盖率又足以让人误以为"类型是受保障的"。前端 33 个组件零静态检查，`v-html`/状态管理这类易错点完全靠人眼。
- **业界做法**：ruff 一次配置覆盖 `E,F,I,UP,B,S,BLE,ASYNC` 等规则集（含 `BLE001` 与 `G` 日志规则），`ruff format` 替代 black；mypy 以 `disallow_untyped_defs` 分目录渐进（先从 `app/agent/harness`、`app/core` 起步）并要求 CI 通过；前端 `eslint` + `vue-tsc --noEmit`（Vue 组件允许 JS，但模板类型检查仍可用）；pre-commit 钩子把这几件事下沉到提交前，避免 CI 排队反馈。
- **可采纳性**：**easy**（新增配置 + 一次性清理存量告警，ruff 的 `--add-noqa` 可批量收敛）；真正 hard 的是 mypy 全量 strict——建议只对核心包开启，其余目录用 `ignore_errors` 白名单逐步推进，不要一次性上严格模式。
- **建议**：①`backend/pyproject.toml` 增加 `[tool.ruff]`（`select = ["E","F","I","UP","B","S","BLE","G"]`、`line-length` 与现有风格一致）与 `[tool.ruff.lint.per-file-ignores]`，先只跑 `ruff check app shared ../collector` 并修完存量；②加 `[tool.mypy]`，`files = ["app/core","app/agent/harness","app/agent/runtime"]` 起步；③`frontend/package.json` 加 `lint`/`typecheck` 脚本与 `eslint` + `@vitejs/plugin-vue` 配套配置；④三者接进 CI 与 pre-commit；⑤把这次审计里那些"整段 pass"的 `# noqa: BLE001` 换成带原因的行内注释，让抑制变成可评审的决策。

### [medium] 依赖约束全为开区间下限、零 CVE 扫描机制、基础设施镜像用 latest

- **证据**：
  - `backend/pyproject.toml:6-30`：22 条依赖里 21 条是 `>=` 下限且**无上界**（`langfuse>=3.0`、`langgraph>=0.2`、`mcp>=2.0`、`fastapi>=0.115`、`celery[redis]>=5.3`…），唯一的精确锁是 `:16` 的 `pydantic-ai-slim[openai]==2.55.0`。锁文件已解出的版本说明下界有多松：`langfuse 4.12.0`、`langgraph 1.2.6`、`starlette 1.3.1`、`redis 6.4.0`、`pgvector 0.5.1`。
  - 全仓无任何漏洞/更新扫描机制：`grep -rni 'pip-audit|npm audit|safety|dependabot|trivy|grype'` 0 命中；无 `.github/`（Dependabot 配置的载体已随 CI 一起删除）；无 `Makefile`/`justfile` 等入口可挂扫描。
  - `collector/requirements.txt:1` 裸 `requests`（无版本），`:2-5` 全为下限；`collector/README.md` 引导客户 `pip install requests websockets`。该文件是交付到**客户内网**的产物。
  - 根 `docker-compose.yml:66,79,88,99,117` 五个 `:latest` 镜像（prometheus、node-exporter、kafka-exporter、mysqld-exporter、ollama）。
  - 已锁定的版本本身不陈旧（`python-jose 3.5.0`、`cryptography 49.0.0`、`python-multipart 0.0.32`、`dompurify 3.4.17`、`axios 1.18.1` 均高于各自历史 CVE 的修复版本），所以问题不是"版本旧"，而是"没有任何机制在下一轮继续保证它不旧"。
- **风险**：约束层完全不设防，安全性 100% 依赖"每个人都记得提交锁文件"这一纪律；而本仓另一条 finding 已证明生产镜像会绕过锁——两者叠加后，一条上游被投毒或回归的 release 可以零审查进入生产。触发条件不需要攻击者：`langfuse` 3→4 或 `langgraph` 0.2→1.x 这种大版本跳变在本仓已发生过（并伴随 `51dad11` 的 3 个启动期 bug），任何一次重建镜像都可能重现。影响面覆盖全部容器组件与客户现场采集器；由于无 SBOM、无扫描记录，事后也无法回答"我们当时装的是哪个版本、有没有已知 CVE"。
- **业界做法**：给高变动依赖写上界（`langfuse>=3.0,<5`）表达兼容承诺，同时保留锁作为精确真源；Dependabot/Renovate 每周开 PR 并附 CI 结果，把升级变成可回滚的小步；CI 跑 `pip-audit`/`uv pip audit` 与 `npm audit --audit-level=high` 作为门禁；发布镜像生成 SBOM（`syft`）并与 `docker scout`/`trivy` 扫描结果留存；基础设施镜像固定 digest。
- **可采纳性**：**easy**。加约束、加扫描、固定 tag 都是配置级改动，无架构改动；唯一成本是恢复 CI 通道（与第一条同源）。
- **建议**：①给 `langfuse`、`langgraph`、`mcp`、`celery`、`fastapi` 补上界并注释兼容范围；②恢复 CI 后加 job：`uv lock --check`、`pip-audit -r`（或 `uv pip audit`）、`npm audit --audit-level=high`，并开启 Dependabot（`pip`/`npm`/`docker` 三个 ecosystem）；③`collector/requirements.txt` 改为锁定（含哈希）版本，客户交付说明同步更新；④根 `docker-compose.yml` 的五个 `:latest` 改为固定 tag/digest，并在 CI 里对 `deploy/` 与根 compose 做镜像 tag 校验。

### [medium] 巨型编排函数把"不可测"固化进了架构核心

- **证据**：
  - `backend/app/services/diagnostics/service.py:915-1291`：`diagnose_system()` **377 行**，函数体内联 5 个闭包（`_load_runbook`、`_topology_query`、`_changes_query`、`_live_failing`、`_failure_query`）与十多个内联 provider；同文件 `:670-841` 的 `complete_diagnosis_report()` 172 行。
  - `backend/app/agent/diagnostics/runner.py:84-360`：`diagnose_with_details()` **277 行**。
  - 该文件总量 1315 行，`grep -c` 显示其中 16 处 `except Exception` 为全仓最多。项目自己的文档承认这点：`ARCHITECTURE.md:549`（§6.5）"`services/diagnostics/service.py` 1315 行，`diagnose_system()` 单函数内联了十几个闭包…可拆为独立 provider 模块"，`:550`（§6.6）指出 `runner.py` 的降级链深嵌套、建议抽成显式 `FallbackPolicy`。
- **风险**：377 行的单函数无法被单元测试有效覆盖——任何用例都得驱动整条链路（工具、DB、模型），这正是"没有 harness/runtime 测试"的结构性原因；也因此每次改动都只能靠端到端手工验收（历史提交里"真机验收""实测"字样频繁出现，成本已经显性化）。触发条件：任何需要修改诊断编排的改动（新增工具类别、调整降级顺序、改预算口径），改动风险与回归面无法局部化。
- **业界做法**：把编排拆成显式步骤对象/管道（`ContextBuilder` → `ToolPlanner` → `Executor` → `Synthesizer` → `Verifier`），每步输入输出可断言；降级策略抽成显式 policy 对象（本仓已有 `runtime/contracts.py` 与 `harness/` 骨架，是这个方向但尚未覆盖 service 层）；长函数重构用 characterization test（先固定当前行为再动刀）。
- **可采纳性**：**hard**。这是本次审计里改动风险最高的一条：在没有测试的情况下重写 377 行编排等同于盲改，必须先落地第一条（测试恢复 + harness 测试），再以"先加 characterization test、后抽 provider 模块"的顺序推进。成本高、收益是长期的，因此不建议在本次整改中优先做。
- **建议**：①**排在测试恢复之后**；②先用 characterization test 锁住 `diagnose_system()` 当前的可观测行为（工具调用序列、终态写入、审计事件、预算记账）；③按 `ARCHITECTURE.md:549` 的建议把 5 个闭包与各 provider 抽成 `services/diagnostics/providers/*.py`，每抽一个跑一次测试；④`runner.py` 的降级链抽成 `FallbackPolicy`（输入 `model_mode` + 失败原因，输出下一候选或终态），使"显式选模型不得静默回退"这条既有规则有单一实现点与对应测试。

### [low] 日志风格混用且全部为非结构化文本

- **证据**：`backend/app` 内 f-string 日志 **45 处**（如 `backend/app/services/monitoring/metrics.py:350` `log.warning(f"[metrics] Kafka 采集失败: {exc}")`、`backend/app/agent/diagnostics/tracing.py:21`、`backend/app/agent/workflows/runner.py:78`），`%`-style **79 处**，两种写法在同一文件里混用。日志配置只有 `backend/app/main.py:19-41` 一处 `StreamHandler` + `_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"`：纯文本、无结构字段、无 trace/request id；`backend/app/agent/diagnostics/mcp_server.py:39` 又独立 `basicConfig` 了一套格式。
- **风险**：f-string 在日志级别被过滤时仍会构造字符串，本仓诊断热路径（`runner.py`、`service.py` 各数百行日志调用）上属于无谓开销；无结构字段意味着无法按 `org_id`/`report_id`/`run_id` 在日志系统里过滤聚合——而本平台恰好是每租户每报告都要追溯的场景，`main.py:27-29` 的注释也说明"诊断耗时、token、缓存命中、成本"都靠日志输出，即关键业务可观测性全押在这套文本日志上。
- **业界做法**：`structlog` 或 `logging` + JSON formatter，用 `contextvars` 绑定 `trace_id`/`org_id`/`report_id` 自动注入每条日志；用 ruff 的 `G`（flake8-logging-format）规则禁止 f-string 日志，把风格统一交给工具。
- **可采纳性**：**easy**（加 ruff `G` 规则 + 一次性替换 45 处；formatter 换成 JSON 也不动业务代码）/ **moderate**（真正做 trace 上下文贯穿需要改造请求与后台任务的边界）。
- **建议**：①ruff 启用 `G` 规则并修完 45 处 f-string 日志；②`main.py:_setup_app_logging` 增加可选 JSON formatter（由 `LOG_FORMAT=json` 开关控制，本地仍用可读格式）；③在诊断请求入口与 Celery 任务入口用 `contextvars` 绑定 `org_id`/`report_id`，让 trace 字段自动进入所有日志。

### [low] 文档资产齐备但缺许可证与贡献指南，唯一的架构文档还没入库

- **证据**：`README.md`（37 行，`:3` 声明测试/评测已移出）与 `collector/README.md` 质量尚可；`ARCHITECTURE.md` 575 行、含 §5 取舍表与 §6 自评改进点，但 `git status --short ARCHITECTURE.md` → `?? ARCHITECTURE.md`（未跟踪）；仓库根无 `LICENSE`、`CONTRIBUTING.md`、`CHANGELOG`、无 `docs/` 目录（find 仅命中 `.dev-stack/sandbox-venv` 下第三方许可证）；`README.md` 没有"如何运行测试"的章节（因为测试已移出）。
- **风险**：①交接/评审场景下，唯一成体系的架构文档（575 行，`ARCHITECTURE.md`）只存在于本机，随工作区丢失；②无 LICENSE 会在开源或客户安全评审/采购流程里直接卡住（`collector/` 是要交付到客户内网的组件，客户法务通常会要许可证声明）；③无 CONTRIBUTING 且 README 不提测试，新人无法自证改动正确，质量退化缺少制度入口。
- **业界做法**：架构决策用 ADR（`docs/adr/NNNN-*.md`）入库并随代码评审；`LICENSE` 明确授权范围（闭源产品则标注 proprietary 并说明第三方许可证清单）；`CONTRIBUTING.md` 给出"提交前必须跑的命令"（`uv sync --frozen`、`ruff check`、`pytest`），并把这些命令做成 `Makefile`/`justfile` 目标降低摩擦。
- **可采纳性**：**easy**。
- **建议**：①`git add ARCHITECTURE.md`（并入第一条的提交拆分）；②补 `LICENSE`（或 proprietary 声明）与第三方依赖许可证清单；③补 `CONTRIBUTING.md` + `Makefile`（`make lint`/`make test`/`make lock`），并把 `ARCHITECTURE.md:543-556`（§6）的 9 条自评改进点转成可跟踪的 issue，避免它们只停留在文档里。

## 对抗性复核

- **自有测试与 CI 门禁已全部移除，且 pytest 依赖被一并从锁定环境删掉** → confirmed（修正严重度：high）
  逐项复算均成立，未找到缓解。①磁盘上确无任何测试：对 `test_*.py`/`*_test.py`/`tests.py`/`conftest.py`/`*.test.js`/`*.spec.js`/`__tests__/` 全部 `find` 命中 0（已排除 `node_modules`/`.dev-stack`/`.venv`）；`git status --short` 170 项、其中 85 项删除，`backend/tests` 38 项、`evals` 15 项 + fixtures、`.github/workflows`、`scripts/eval-gate.sh` 均在列。②HEAD 侧数字与报告完全一致：`backend/tests/*.py` 合计 **5674 行**、`evals/*.py` **3407 行**、`^\s*(async )?def test_` **322 个**。③依赖侧同样确凿：`git diff backend/pyproject.toml` 删掉 `default-groups`/`[dependency-groups] dev`/`[tool.pytest.ini_options]`，`grep -c pytest backend/uv.lock` = **0**（51 deletions），`backend/.venv/bin/python -m pytest` → `No module named pytest`；我另外试了系统 `python3` 与 `.dev-stack/sandbox-venv/bin/python`，同样没有 pytest，即"手写测试也跑不起来"这条没有逃生口。④门禁没有替代品：`.github/` 已不存在，全仓无 `.gitlab-ci.yml`/`Jenkinsfile`/`.circleci`/`azure-pipelines.yml`/`Makefile`/`justfile`/`.pre-commit-config.yaml`，`frontend/package.json:6-10` 只有 dev/build/preview；而 HEAD 的 `.github/workflows/eval-gate.yml` 是真实门禁（`uv sync --directory backend --frozen` + `bash scripts/eval-gate.sh`），确认被整体删除。⑤报告"覆盖面本来也偏"的精确论断经复核成立：对 `app.agent.harness`、`app.agent.runtime` 这两个**全限定**模块名在 HEAD 的 `backend/tests` + `evals` 里 `git grep` 命中 **0**（`harness`/`runtime` 的 34/8 次命中全部来自 `evals/harness.py` 一类同名子串，不是这两个包）。唯一可算缓解的是测试仍在 HEAD 内、`git checkout` 可恢复，但报告已写明并把可采纳性定为 moderate，不改变 severity；`README.md:3` 的"已移出"只证明意图，意图不构成回归网。（口径小误差：`git status` 实为 170 项而非 169，不影响结论。）
- **生产镜像构建完全绕过 uv.lock，锁定只在开发机上成立** → confirmed（修正严重度：high）
  证据全部落到代码，且没有"其实锁了"的隐藏路径。①`backend/Dockerfile:10` 就是 `RUN pip install --no-cache-dir /opt/aiops/backend`，全文无 `uv`、无 constraints、无 `--require-hashes`；`:8` 的 `COPY backend` 虽把 `uv.lock` 带进镜像，但无人消费（`backend/` 下只有 `Dockerfile`/`pyproject.toml`/`uv.lock`，无 requirements/constraints 文件）。②`deploy/docker-compose.yml:29-31`、`:54-56`、`:77-79` 三个服务（platform-api/worker/beat）均为 `context: ..` + `dockerfile: backend/Dockerfile`，且 `:36-37` 的 `JWT_SECRET`/`ENCRYPTION_KEY` 标着 "before production deployment"，确认这就是生产拓扑，"平台所有运行组件都走这条路径"成立。③约束与已锁版本的落差不虚：`backend/pyproject.toml` 共 **23** 条依赖、**22** 条 `>=`、正好 **1** 条 `==`（`pydantic-ai-slim[openai]==2.55.0`）；`backend/uv.lock` 实解出 `langfuse 4.12.0`、`langgraph 1.2.6`、`mcp 2.3.0`、`fastapi 0.138.1`、`starlette 1.3.1`，逐条与报告一致。④客户侧与基础设施侧同样不受锁保护：`collector/requirements.txt:1` 裸 `requests`、`:2-5` 全为下限，`collector/Dockerfile:7-8` 直接 `pip install -r`；根 `docker-compose.yml:66,79,88,99,117` 五个 `:latest` 确认存在。⑤我特意找"这是有意取舍"的文档依据：README 与 ARCHITECTURE 中关于镜像/锁的表述只有 `README.md:3`"保留…依赖锁文件"，没有任何地方声明生产构建故意不锁，故属未记录漂移而非取舍。仅有的口径误差是报告写"22 条里 21 条"（实为 23/22），不影响严重度。
- **审计写入失败被静默吞掉，与 ARCHITECTURE.md 明示的硬规则相反** → overstated（修正严重度：medium）
  "吞掉"的事实成立，但标题的"与硬规则相反"与风险段的"审计表里什么都没有/唯一留痕"两处关键论断不成立，严重度被高估。①事实部分我逐字复核无误：`backend/app/services/diagnostics/service.py:293-306` 在查询已成功、`rows` 已拿到之后才写 `data_analysis.dataset_queried`，被丢弃的正是 `output={"rows": len(rows), "sql": result["sql"]}`；`:307-308` 是 `except Exception:  # noqa: BLE001` + `pass`；`backend/app/services/audit.py:9-40` 的 `record_audit_event` 内部无日志、无重试、无计数，失败确实无声。我用 AST 独立扫描 `backend/app` 复算出"整段函数体只有 `pass`"的处理器正好 **16 处**，行号与报告清单逐一吻合（`service.py:307,1138`、`stream.py:74`、`metrics.py:213,325,369,388`、`workflows/runner.py:87`、`main.py:81`、`sandbox.py:40,42,82`、`answer_stream.py:116,143`、`runner.py:401`、`claims.py:65`）。②但"与 ARCHITECTURE.md 明示的硬规则相反"是误读：`ARCHITECTURE.md:206-207` 把该规则明确限定为 `tool_audit_sink` 写失败则抛 `ToolAuditError`、不执行工具，§5 取舍表 `:531-534` 只是复述；而该契约在 `backend/app/agent/harness/gateway.py:105-112`（执行前先落 `started`，失败即 `raise ToolAuditError('工具审计写入失败，未执行工具')`）与 `:160-169`（结果记录失败即抛，注释写明 no silent success）**确实被实现且 fail-closed**。被吞掉的是第二个、补充性的业务事件，不是准入审计，硬规则并未被违反。③"唯一留痕"不成立：所有工具都经 `backend/app/agent/diagnostics/tool_adapter.py:28` → `ToolGateway.invoke`；`query_business_dataset`（`tools.py:436-456`，已在 `harness/catalog.py:13` 登记）全仓只有 `service.py:1067` 一处接线，而它在 `diagnose_system()`（`:915` 起）内与 `:1086` 的 `tool_audit_sink=_tool_audit_recorder(...)` 是**同一次调用**（`business_dataset_query=` 全仓仅此一处赋值）。`_tool_audit_recorder`（`service.py:1294-1311`）把 tool 名、arguments（含 dataset/date_from/date_to/filters）、call_id、scope、status 落成 `agent.tool` 审计行，其写入失败会使整轮以 `ToolAuditError` 中止（`runner.py:162,215,263,344` 把它列为 control-flow）。连 Pi 引擎路径也没漏：`pi_runtime.py:99` 的字段白名单只排除 `descriptor/scope/gateway_records/control_flow_errors`，`tool_audit_sink` 被保留传递。故"用户看到成功查询、审计表里什么都没有"在本路径上不可能发生。④顺带削弱了"该路径天然高并发易失败"的论证：`_tool_audit_recorder` 用的是同一套"每条记录独立 Session"模式，同样会撞锁，但撞锁时表现为可见的 `ToolAuditError` 中止，而非静默成功。结论：真实且值得修的是"`dataset_queried` 的行数与 SQL 明细会无声丢失、无日志无 counter"（`:1138` 等其余 15 处同理），但把它说成违反已实现文档规则、并导致多租户取证链彻底空白，超出证据，故由 high 下调为 medium。（口径小误差：`except Exception` 实测 189 处而非 193，`noqa` 实测 92 处而非 105；`noqa: BLE001` 84 处与报告一致。）
