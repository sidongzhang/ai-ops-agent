# LLM Agent 离线评测报告

> ⚠️ **本报告由 `--dry-run` 的 mock Agent 生成，数字是合成数据，不能当作模型真实能力。** 它只证明评测链路（数据集→消融→打分→报告）是通的。

- 生成时间：2026-10-17T04:30:57+00:00
- 运行模式：dry-run（mock Agent，合成数据）
- 数据集：`/Users/zhangsidong/ai-ops-agent/evals/datasets/seed_cases.jsonl`（114 条用例）
- 故障注入状态：journal 中仍标记为已注入的用例: 无
- 描述符目录：`/Users/zhangsidong/ai-ops-agent/evals/fixtures/descriptors`
- 知识库目录：`/Users/zhangsidong/ai-ops-agent/evals/knowledge/docs`（4 篇：container-oom-and-log-storm-runbook.md, kafka-lag-runbook.md, mysql-connections-slow-sql-runbook.md, redis-memory-runbook.md）
- RAG 检索模式：keyword（强制离线，确定性）
- 模型覆盖：`(默认路由)`（model_mode=auto）
- 并发度：1；单用例超时：300.0s
- 消融组：full
- 主要指标组：**full**（下方「总体指标」「分故障类型」均指该组）

## 总体指标（full）

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 任务成功率% | 88.6 | 命中 required_tools(≥1) 且答案含 ≥1 个 evidence_keys 且未出现 forbidden_conclusion 的用例占比 |
| RCA 准确率(0-1) | 0.92 | Σ(每个 evidence_key 命中权重)/len(evidence_keys)，全命中=1、部分命中=0.5、未命中=0 |
| 工具选择 F1 | 0.81 | 以 required_tools 为正类，对实际调用工具名集合算 P/R/F1 后宏平均 |
| 工具调用成功率% | 98.2 | tool_call 中 status==success 的占比（近似：只反映工具函数是否报错） |
| 证据覆盖率% | 98.2 | 至少调用过 1 个工具才下结论的用例占比（对齐 diagnosis_evidence_rate_pct） |
| 平均工具调用数 | 1.91 | 平均每次诊断的 tool_call 数 |
| 平均进度事件数 | 3.82 | on_progress 回调收到的进度事件数均值（tool_start/tool_end 各计一次） |
| 冗余调用率% | 32.9 | (tool_call 总数 - 去重后的 tool+参数 组合数)/总数 |
| 幻觉率% | 11.4 | 答案命中 forbidden_conclusion 的用例占比 |
| 平均耗时ms | 749.6 | DiagnosisRun.duration_ms 的平均值 |
| 平均 tokens | 244.9 | DiagnosisRun.total_tokens 的平均值 |

## 分故障类型指标（full）

| 故障类型 | 用例数 | 任务成功率% | RCA 准确率(0-1) | 工具选择 F1 | 证据覆盖率% | 幻觉率% | 平均工具调用数 | 冗余调用率% | 平均耗时ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| service_unreachable（服务不可达） | 14 | 92.9 | 0.929 | 0.786 | 100 | 7.1 | 2 | 32.1 | 812.1 |
| redis_memory（Redis 内存打满） | 20 | 90 | 0.933 | 0.965 | 95 | 10 | 1.6 | 32.5 | 669 |
| kafka_lag（Kafka 消费堆积） | 20 | 90 | 0.9 | 0.772 | 95 | 10 | 1.9 | 32.5 | 760.7 |
| mysql_connections（MySQL 连接打满） | 15 | 86.7 | 0.9 | 0.778 | 100 | 13.3 | 2 | 33.3 | 760.1 |
| container_oom（容器 OOMKill） | 15 | 86.7 | 0.889 | 0.778 | 100 | 13.3 | 2 | 33.3 | 764.9 |
| slow_sql（慢 SQL） | 15 | 86.7 | 1 | 0.778 | 100 | 13.3 | 2 | 33.3 | 780 |
| log_error_storm（日志错误风暴） | 15 | 86.7 | 0.889 | 0.778 | 100 | 13.3 | 2 | 33.3 | 727.8 |

## 失败用例（full 组，按需展开）

| 用例 | 消融组 | 故障类型 | 失败原因 |
| --- | --- | --- | --- |
| redis-memory-003 | full | redis_memory | 命中错误归因: 恶意刷接口攻击 |
| kafka-lag-003 | full | kafka_lag | 命中错误归因: 网络分区导致 broker 失联 |
| mysql-connections-003 | full | mysql_connections | 命中错误归因: 慢 SQL 拖垮数据库 |
| container-oom-003 | full | container_oom | 命中错误归因: 磁盘写满导致 |
| slow-sql-003 | full | slow_sql | 命中错误归因: 网络抖动导致 |
| log-error-storm-003 | full | log_error_storm | 命中错误归因: DDoS 攻击 |
| svc-unreachable-redis-v006 | full | service_unreachable | 命中错误归因: Redis 内存打满导致 |
| redis-memory-v015 | full | redis_memory | 命中错误归因: 网络抖动导致 |
| kafka-lag-v036 | full | kafka_lag | 命中错误归因: Kafka broker 磁盘写满 |
| mysql-connections-v045 | full | mysql_connections | 命中错误归因: 慢 SQL 占用连接 |
| slow-sql-v057 | full | slow_sql | 命中错误归因: 连接数打满导致排队 |
| container-oom-v069 | full | container_oom | 命中错误归因: Kafka 消费积压导致 |
| log-error-storm-v087 | full | log_error_storm | 命中错误归因: 磁盘快满导致 IO 阻塞 |

## 指标口径

- `task_success_rate`：命中 required_tools(≥1) 且答案含 ≥1 个 evidence_keys 且未出现 forbidden_conclusion 的用例占比
- `rca_accuracy`：Σ(每个 evidence_key 命中权重)/len(evidence_keys)，全命中=1、部分命中=0.5、未命中=0
- `tool_selection_f1`：以 required_tools 为正类，对实际调用工具名集合算 P/R/F1 后宏平均
- `tool_param_validity`：tool_call 中 status==success 的占比（近似：只反映工具函数是否报错）
- `evidence_coverage_rate`：至少调用过 1 个工具才下结论的用例占比（对齐 diagnosis_evidence_rate_pct）
- `avg_tool_calls`：平均每次诊断的 tool_call 数
- `avg_progress_events`：on_progress 回调收到的进度事件数均值（tool_start/tool_end 各计一次）
- `redundant_call_rate`：(tool_call 总数 - 去重后的 tool+参数 组合数)/总数
- `hallucination_rate`：答案命中 forbidden_conclusion 的用例占比
- `avg_duration_ms`：DiagnosisRun.duration_ms 的平均值
- `avg_total_tokens`：DiagnosisRun.total_tokens 的平均值
- `tool_degraded_rate`：工具输出含降级标记（如「离线评测不支持」「查询失败」）的调用占比

线上对齐：`evidence_coverage_rate` ↔ `analytics.diagnosis_evidence_rate_pct`；`task_success_rate` 是 analytics `diagnosis_success_rate_pct` 的离线严格版（线上只统计诊断是否完成，离线额外要求工具命中 + 证据词命中 + 无幻觉）。

产物：`summary.json`（机器可读）、`summary.md`（本文件）、`trajectories.jsonl`（每条用例完整 tool_calls 轨迹）。
