# LLM Agent 离线评测脚手架（P0-1）

面向本项目诊断 Agent（`backend/app/agent/diagnostics/`）的**可复现离线评测**：固定数据集 + 固定消融组 + 可注入故障 + 自动打分报告。
目标不是「跑一次看答案」，而是**同一批用例、同一套指标，能反复比出模型/提示词/检索策略的差异**，并产出可直接粘进文档的表格。

---

## 0. TL;DR

```bash
cd /Users/zhangsidong/ai-ops-agent

# 1) 不碰 Docker、不调 LLM：校验数据集
backend/.venv/bin/python evals/run_eval.py --validate

# 2) 不碰 Docker、不调 LLM：mock Agent 跑通全链路并产出报告
backend/.venv/bin/python evals/run_eval.py --dry-run

# 3) 自测（38 条用例，不依赖 Docker / 不调 LLM）
backend/.venv/bin/python -m pytest evals/ -q

# 4) 真跑（需要 Docker 起被监控栈 + 消耗 LLM 额度）
docker compose up -d
backend/.venv/bin/python evals/run_eval.py --run --ablation all
```

> ⚠️ `--dry-run` 的数字来自内置 mock Agent，是**合成数据**，只证明「数据集→消融→工具轨迹→打分→报告」这条链路是通的，**不能当作模型能力**。真实能力必须看 `--run`。

---

## 1. 评测目标

1. **任务成功率**：模型能否选对工具、拿到证据、给出不含错误归因的结论。
2. **工具调用质量**：工具选择（P/R/F1）、入参执行成功率、冗余调用率、被环境降级时的表现。
3. **检索与剧本增益**：`baseline / playbook / rag / full` 四组消融，量化 YAML 剧本与 RAG 知识库各自贡献多少。
4. **抗误导能力**：20/25 条用例带「看似合理的错误归因」诱饵（如「是不是网络抖动？」「是不是磁盘满了？」），统计幻觉率。
5. **可复现**：数据集、fixture、知识库、故障注入脚本全部入库，任何人可重跑并对比报告。

**非目标**：不评测平台其它模块（告警、工单、多租户权限等）；不替代线上 `analytics.get_efficiency_analytics()`。

---

## 2. 目录结构

```
evals/
├── README.md                     # 本文件
├── run_eval.py                   # CLI 主入口（argparse）
├── dataset.py                    # 数据集 schema / 加载 / 校验 / 筛选
├── metrics.py                    # 指标纯函数（可单测，无第三方依赖）
├── reporting.py                  # summary.json / summary.md / trajectories.jsonl
├── harness.py                    # sys.path 引导、消融参数、remote_command 适配器、dry-run mock
├── faults.py                     # 故障注入驱动（docker_exec/stop/start、http、manual）
├── conftest.py                   # pytest sys.path 引导
├── test_eval_harness.py          # 自测（不依赖 Docker、不调 LLM）
├── datasets/
│   └── seed_cases.jsonl          # 25 条种子用例，7 类故障
├── fixtures/
│   ├── descriptors/*.json        # 7 份系统描述符（每类故障一份）
│   └── logs/*.log                # 供离线读日志的样例日志
├── knowledge/docs/eval-docker-local/*.md   # 评测知识库（RAG 消融用）
└── results/<timestamp>/          # 产物（已 gitignore）
    ├── summary.json
    ├── summary.md
    └── trajectories.jsonl
```

故障类型（7 类，每类 ≥3 条）：`service_unreachable`、`redis_memory`、`kafka_lag`、`mysql_connections`、`container_oom`、`slow_sql`、`log_error_storm`。

---

## 3. 怎么跑

### 3.1 无 Docker 的冒烟模式（推荐日常/CI 用）

| 命令 | 作用 | 是否调 LLM | 是否需要 Docker |
| --- | --- | --- | --- |
| `--validate` | 校验 schema、id 唯一、每类 ≥3 条、诱饵 ≥5 条、工具名合法 | 否 | 否 |
| `--list` | 列出用例、故障分布、当前仍处于「已注入」状态的用例 | 否 | 否 |
| `--dry-run` | mock Agent 跑完 25 条 × 消融组，产出三件套报告 | 否 | 否 |
| `--dry-run --ablation all` | 四组消融对比表 | 否 | 否 |

`--dry-run` 做了两件刻意的「离线保证」：
1. RAG 强制走**关键词回退**（把 `store._embed` 打桩为 `None`），不访问 embedding API；
2. 不 import `app.agent.diagnostics.runner`，因此不会构造 pydantic-ai 客户端。

### 3.2 真实评测（`--run`）

```bash
docker compose up -d                                   # 需要 mysql/redis/kafka/prometheus
backend/.venv/bin/python evals/run_eval.py --inject-all        # 注入故障
backend/.venv/bin/python evals/run_eval.py --run --ablation all # 真跑
backend/.venv/bin/python evals/run_eval.py --teardown-all      # 恢复
```

Docker 不可用时 `--inject*` 会打印：

```
❌ Docker 不可用。请先启动本地容器运行时：
  colima start          # 或启动 Docker Desktop
  docker compose up -d  # 在仓库根目录拉起 mysql/redis/kafka/prometheus 等
...
```

并以退出码 2 结束，**不打印 traceback**；`--validate/--dry-run` 不受影响。

### 3.3 常用参数

| 参数 | 说明 |
| --- | --- |
| `--fault-type T`（可重复） | 只跑某类故障 |
| `--case-id ID`（可重复） | 只跑某条用例 |
| `--limit N` | 只跑前 N 条 |
| `--model NAME` | 覆盖模型名（会同时把 `model_mode` 切到 `api`，原因见 §10.3） |
| `--model-mode {auto,default,api,local,advanced}` | 模型路由模式，默认 `auto` |
| `--concurrency N` | 并发用例数，默认 1（>1 为实验性，见 §11） |
| `--timeout S` | 单用例超时秒数，默认 300 |
| `--out-dir DIR` | 报告目录，默认 `evals/results/<UTC timestamp>/` |
| `--docs-root DIR` | 知识库 docs 根目录，默认 `evals/knowledge/docs` |
| `--descriptors-dir DIR` | descriptor fixture 目录 |
| `--rag-mode {keyword,auto}` | `keyword`=强制离线关键词检索；`auto`=允许 embedding API（`--run` 默认） |
| `--journal PATH` | 故障注入 journal，默认 `evals/results/injections.jsonl` |
| `-v/--verbose` | 打印每条用例的进度与答案首行 |

---

## 4. 指标定义与计算公式

所有 `*_rate` 都是**百分比（0-100，1 位小数）**，与 `backend/app/services/analytics.py` 的 `_rate()` 保持一致；
`rca_accuracy`、`tool_selection_*` 是 **0-1 比例（3 位小数）**。

| 指标 | 公式 / 定义 |
| --- | --- |
| `task_success_rate` | `命中 required_tools(≥1) 且 答案含 ≥1 个 evidence_keys 且 未出现 forbidden_conclusion` 的用例占比 |
| `rca_accuracy` | `Σ weight(key) / len(evidence_keys)`；`weight`：全命中=1.0，部分命中=0.5，未命中=0 |
| `tool_selection_f1` | 以 `required_tools` 为正类、实际调用的工具名集合为预测：`TP=|交集|`，`P=TP/|实际|`，`R=TP/|必需|`，`F1=2PR/(P+R)`，再对用例宏平均 |
| `tool_param_validity` | `status == "success"` 的 tool_call 占比（**近似**：只反映工具函数是否抛异常，不反映命令是否真的成功） |
| `evidence_coverage_rate` | 至少调用过 1 个工具才下结论的用例占比（对应线上 `diagnosis_evidence_rate_pct`） |
| `avg_tool_calls` | 平均每次诊断的 tool_call 数 |
| `avg_progress_events` | `on_progress` 回调收到的进度事件数均值（`tool_start`/`tool_end` 各计一次，用于核对步数与工具耗时） |
| `redundant_call_rate` | `(tool_call 总数 - 去重后的 (tool, 规范化入参) 组合数) / 总数` |
| `hallucination_rate` | 答案命中 `forbidden_conclusion` 的用例占比 |
| `tool_degraded_rate` | 工具输出含降级标记（`离线评测不支持`、`查询失败`、`未找到 Kafka 容器` 等）的调用占比——**离线评测专用**，用来暴露「环境补不上导致工具退化」 |
| `avg_duration_ms` / `avg_total_tokens` | 直接取 `DiagnosisRun.duration_ms` / `.total_tokens` 的均值 |

`evidence_key` 命中规则（`metrics.key_match_weight`）：
1. 归一化（小写，`_ - / . : , = {} () []` 与空白统一成分隔符）后**子串命中** → 1.0；
   所以 `used_memory`、`used memory`、`used-memory` 等价，`type=ALL` 与 `type: ALL` 等价；
2. 对**多词 key**（如 `evicted_keys`）若 ≥50% 的 token 出现在答案里 → 0.5；
3. 单 token key 只给 0/1，避免子串误判。

报告结构：`总体（primary 组）` → `分故障类型（primary 组）` → `消融对比（多组时）` → `失败用例明细` → `指标口径`。
`summary.json` 里同时给出 `by_fault_type` 与 `ablations.<group>.{overall,by_fault_type}`，便于二次分析。

与线上口径的映射：
* `evidence_coverage_rate` ↔ `analytics.diagnosis_evidence_rate_pct`（同为「带证据下结论」的比例）；
* `task_success_rate` 是 `diagnosis_success_rate_pct` 的**离线严格版**：线上只统计诊断是否完成，离线额外要求工具命中 + 证据词命中 + 无幻觉，因此数值会明显低于线上；
* 线上 `operation_closure_rate_pct`（操作闭环）本脚手架**未覆盖**——它依赖 `ActionWorkflow` 落库，属于「诊断后执行」链路，不在本次 P0-1 范围内。

---

## 5. 消融实验设计

只通过**传参**控制，不改任何业务代码：

| 组 | `skill_steps` | `knowledge_context` | 语义 |
| --- | --- | --- | --- |
| `baseline` | `""` | `""` | 裸 Agent（只有 system prompt + 工具） |
| `playbook` | `match_skill(question)["steps"]` | `""` | 只加 YAML 剧本 |
| `rag` | `""` | `get_relevant_context(question, system_id)` | 只加知识库检索 |
| `full` | 剧本 | 知识库 | 线上默认行为 |
| `all` | — | — | 依次跑上面四组，报告给对比表 |

关键实现细节：**必须传 `""` 而不是 `None`**。`runner.py` 的 system prompt 里写的是
`skill_steps = get_skill_steps(question) if ctx.deps.skill_steps is None else ctx.deps.skill_steps`，
只有 `None` 才会自动挂剧本，所以空串才是真正的「裸 Agent」。

> ⚠️ `--dry-run --ablation all` 里四组数字会**完全相同**，这是预期行为：mock Agent 不读剧本/知识库，
> 所以消融参数不会改变合成结果。dry-run 只能验证「参数确实按组传下去了」（见 `trajectories.jsonl` 里的
> `params.skill_name` / `params.knowledge_context_chars`），**不能**用来比较剧本或 RAG 的增益——那必须 `--run`。

---

## 6. 数据集 schema 与如何扩展

`evals/datasets/seed_cases.jsonl`：每行一个 JSON 对象。

```jsonc
{
  "id": "redis-memory-001",              // 唯一
  "fault_type": "redis_memory",          // 7 类之一
  "difficulty": "easy",                  // easy | medium | hard
  "system_id": "eval-docker-local",      // 知识库目录名 / descriptor.id
  "question": "Redis 写入偶尔报错，帮我看看是不是内存问题",
  "ground_truth": {
    "root_cause": "Redis 实例 maxmemory 被设为 8MB，写入触发 OOM/淘汰策略",
    "evidence_keys": ["maxmemory", "used_memory", "evicted_keys"],
    "required_tools": ["run_redis_command"],       // 至少命中其中之一
    "forbidden_conclusion": ["网络抖动导致", "客户端连接池耗尽"]  // 出现即幻觉
  },
  "inject": { "action": "docker_exec", "target": "ai-ops-agent-redis-1", "command": "redis-cli CONFIG SET maxmemory 8mb" },
  "setup": ["灌入超过 8MB 数据"],
  "teardown": ["redis-cli CONFIG SET maxmemory 0"]
}
```

约定：
* `question` 要像真实用户，**不能把答案写进去**；`forbidden_conclusion` 用来放「同事说的那个错误方向」。
* `required_tools` 必须取自 `dataset.TOOL_NAMES`（10 个真实工具名），`--validate` 会校验。
* `inject.action ∈ {docker_exec, docker_stop, docker_start, http, manual}`。
* `inject.command` 是**机器可执行**的命令；`setup` 是**人工可读**说明（只打印）；`teardown` 是 `docker_exec`/`docker_start` 用例的**机器可执行回滚命令**。

### 扩展一个新故障类型（例如 `disk_full`）

1. 在 `dataset.FAULT_TYPES` / `FAULT_TYPES_LABELS` 加常量；
2. 新增 `evals/fixtures/descriptors/disk_full.json`（对齐 `system_to_descriptor()` 产物：`id/name/local/infra/services`，每个 service 至少 `name/connector/config`）；
3. 在 `seed_cases.jsonl` 追加 ≥3 条用例（`--validate` 强制每类 ≥3）；
4. 如需 RAG 增益，在 `evals/knowledge/docs/eval-docker-local/` 加一篇 runbook；
5. 跑 `--validate` + `--dry-run`，确认分布与报告正常。

不需要改 `metrics.py` / `reporting.py`：指标按 `fault_type` 自动分组。
如需在 mock 里反映新故障的服务名/指标名，补 `harness._mock_service_name` / `_mock_promql`（仅影响 dry-run）。

---

## 7. 故障注入（`evals/faults.py`）

* 五种 action：`docker_exec`（容器内 `sh -c`）、`docker_stop`、`docker_start`、`http`（`<METHOD> [json body]`）、`manual`（打印人工步骤）。
* 每次注入/回滚都追加一条记录到 `evals/results/injections.jsonl`，`--list` 会显示「当前仍标记为已注入」的用例；`--teardown` 从用例定义推导回滚（`docker_stop → docker_start`、`docker_start → 恢复配置 + docker restart`、`docker_exec → 执行 teardown 命令列表`），因此**可重复执行**。
* Docker 不可用时抛 `DockerUnavailable`，CLI 捕获后打印提示 + 退出码 2，无 traceback。`faults.docker_available()` 以 `docker info` 成功为准（不只是 `which docker`）。
* `inject-all` 会先做一次 preflight，避免做到一半才失败。

---

## 8. 本地 `remote_command` 适配器能做什么

`harness.make_remote_command(descriptor)` 产出符合 `AgentDeps.remote_command: Callable[[str, dict], dict]` 契约的函数，
语义对齐 `collector/ws_client.py::_handle_command`，但执行体换成本机能力（`shared/connectors/`）：

| 命令 | 本机实现 | 离线（无 Docker）行为 |
| --- | --- | --- |
| `health_check` | `get_connector(svc, descriptor).health()`，tcp/http/prometheus 真实探活 | ✅ 可用（Docker 相关服务返回容器状态失败原因） |
| `fetch_logs` / `search_logs` | 连接器读本地日志文件 | ✅ 对 `local/kind=process` 的 fixture 日志可用；Docker/ssh/k8s 返回 `ok=False` |
| `query_prometheus` | httpx 直连 fixture 的 Prometheus `/api/v1/query` | ✅ 可用（Prometheus 没起时返回 `ok=False`，Agent 需自行降级） |
| `run_redis_command` | 裸 socket 发 RESP，只读命令 + 子命令白名单 | ✅ 可用（Redis 没起时返回 `ok=False`） |
| `run_kafka_command` | `docker exec <container> /opt/kafka/bin/kafka-*.sh` | ❌ 返回 `{"ok": false, "result": "离线评测不支持：..."}` |

设计原则：**补不上的能力明确返回 `ok=False` 并说明原因，让 Agent 自己降级**——降级本身也是评测对象（`tool_degraded_rate`）。

---

## 9. 与现有代码对接 & 踩坑记录

1. **`sys.path` 必须自己处理**：仓库根没有 `pyproject.toml`，`pytest evals/` 不会读到 `backend/pyproject.toml` 的 `pythonpath = [".."]`。`evals/conftest.py` 与 `harness.bootstrap()` 会插入 `backend/` 与 `evals/`。
2. **`NO_PROXY` 里有 `[::1]` 会让整个 harness 起不来**：`backend/app/core/config.py` 会把 `::1` 追加进 `NO_PROXY`，而外层 shell 常导出 `NO_PROXY=...,[::1]`；httpx 的 `URLPattern` 解析 `[::1]` 会抛 `httpx.InvalidURL: Invalid port: ':1]'`。由于 `backend/app/agent/diagnostics/__init__.py` 在 import 时就会 `from .runner import diagnose` → 构造 pydantic-ai 客户端，这个异常会让**任何**模式崩溃。`harness.sanitize_proxy_env()` 只在评测进程里把 `[::1]` 归一成 `::1`（与 config.py 自己写的写法一致），不改业务代码，报告 meta 会记录本次修正。
3. **`--model` 会被 `model_mode="auto"` 吞掉**：`endpoint_for_choice("auto")` 直接返回 `None`，`model_name` 不生效。因此 `--model X` 时 harness 自动改用 `model_mode="api"`（`model_mode_effective` 会写进轨迹）。
4. **`system_id` 参数不是 RAG 的落点**：`diagnose_with_details(system_id=...)` 只用于日志/tracing，RAG 用的是 `descriptor["id"]`。所以评测传 `system_id=0`，把 `descriptor["id"]` 固定成 `eval-docker-local` 来命中 `evals/knowledge/docs/eval-docker-local/`。
5. **`store.DOCS_ROOT` 用 monkeypatch 重定向**（`harness.patch_knowledge_docs_root`），同时清空 `store._cache`、按需把 `store._embed` 打桩；没有改 `store.py` 的常量。
6. **fixture 里的相对路径按仓库根解析**：`shared/connectors/local.py` 的 `_abspath()` 基准是 `shared/`（`dirname(dirname(local.py))`），所以 `prepare_descriptor()` 会先把 `log_file/pid_file/log_dir` 规范成绝对路径，否则日志会被解析到 `shared/evals/...`。
7. **`skill_router` 同分时按文件名顺序取胜**：`match_skill` 只在 `score > best_score` 时替换，`log_investigation` 排在 `redis_analysis` 之前；问题里同时命中两类触发词时（例如「Redis 报错」）会命中 `log_investigation`。这是现有实现的行为，不是本次改动，但会影响 `playbook` 组的增益，属于可优化点。

---

## 10. 已知限制 / 未完成项（诚实清单）

1. **`--run` 未在本机实测**：Docker/Colima 未运行，且按任务要求不消耗 LLM 额度。真实链路由 `test_real_agent_loop_runs_offline_with_test_model` 用 pydantic-ai `TestModel` 离线跑通（真实 `diagnose_with_details` + 全部 10 个工具），但**线上模型的真实分数尚未产生**，README 不含任何真实模型数字。
2. **`tool_param_validity` 只是近似**：`tool_call.status` 反映工具函数是否抛异常，不反映远程命令是否成功。远程失败被计入 `tool_degraded_rate`（启发式字符串匹配）而非参数不合法。
3. **`rca_accuracy` 是词面命中，不是语义判定**：靠 `evidence_keys` 子串/分词匹配，模型换一种说法（同义改写、只给数值不给指标名）会被判未命中；没有 LLM-as-judge（避免评测本身消耗额度、也避免引入不稳定性）。
4. **`container_oom` 的 3 条用例只能人工注入**（`action: manual`）：5 种 action 里没有改容器内存限制的能力（需要 `docker update --memory` 或改 compose 重建），OOMKill 又强依赖宿主机内存。`faults.py` 已实现 `docker_start` 并在 `svc-unreachable-004` 上使用，`http` 在 `svc-unreachable-005`（`POST /-/quit` 关闭 Prometheus）上使用。
5. **离线时 RAG 关键词回退对中文很弱**：`store._keyword_hits` 用 `[\w一-鿿]+` 切词，中文长句不切分，导致 `--dry-run` 的 `rag` 组检索到的上下文很短（几十~几百字符）。真实 `--run` 走 embedding 语义检索（`.env` 已配 `DEEPSEEK_API_KEY`）不受影响。若要在无网环境做有意义的 RAG 消融，需要引入中文分词或 eval 侧 bigram 检索器（未实现）。
6. **`--concurrency > 1` 是实验性**：`diagnose_agent` 是模块级共享 Agent 实例，`run_sync` 的线程安全性未验证；默认 1 以保证结果可复现。
7. **`--timeout` 不能真正杀掉超时用例**：Python 线程无法强制中断，超时只是把该用例记为 `error` 并继续，后台线程可能仍在跑（pydantic-ai 请求会在自身超时后结束）。
8. **数据集体量小**：25 条用例只够做「方向性对比」，不足以给出统计显著性；每类 3-5 条，`rca_accuracy` 这类均值对单条波动敏感。
9. **`--inject-all` 的 Kafka 注入命令依赖容器内 `/opt/kafka/bin/` 路径**（`apache/kafka:3.7.0` 成立），换 Kafka 镜像需要同步改用例。
10. **未接入的指标**：线上 `operation_closure_rate_pct`（操作闭环）、`notification_success_rate_pct`、`auto_completion_rate_pct` 没有离线对应物；`--run` 也不写库、不产生 `AuditLog`，所以离线报告与线上大盘只能按 §4 的口径映射对比，不能直接拼接。
11. **知识库写了 4 篇 runbook**（任务建议 2~3 篇）：为了让 7 类故障在 `rag` 组里都能检索到东西，刻意多写了一篇（容器 OOM + 日志风暴合并成一篇）。这是有意偏离，不是遗漏。
