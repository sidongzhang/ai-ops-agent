# LLM Agent 离线评测报告

- 生成时间：2026-09-23T10:54:59+00:00
- 运行模式：真实 LLM 调用
- 数据集：`/Users/zhangsidong/ai-ops-agent/evals/datasets/seed_cases.jsonl`（112 条用例）
- 故障注入状态：journal 中仍标记为已注入的用例: 无
- 描述符目录：`/Users/zhangsidong/ai-ops-agent/evals/fixtures/descriptors`
- 知识库目录：`/Users/zhangsidong/ai-ops-agent/evals/knowledge/docs`（4 篇：container-oom-and-log-storm-runbook.md, kafka-lag-runbook.md, mysql-connections-slow-sql-runbook.md, redis-memory-runbook.md）
- RAG 检索模式：auto（允许 embedding API）
- 模型覆盖：`(默认路由)`（model_mode=auto）
- 并发度：1；单用例超时：300.0s
- 消融组：full
- 主要指标组：**full**（下方「总体指标」「分故障类型」均指该组）

## 总体指标（full）

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 任务成功率% | 99.1 | 命中 required_tools(≥1) 且答案含 ≥1 个 evidence_keys 且未出现 forbidden_conclusion 的用例占比 |
| RCA 准确率(0-1) | 0.914 | Σ(每个 evidence_key 命中权重)/len(evidence_keys)，全命中=1、部分命中=0.5、未命中=0 |
| 工具选择 F1 | 0.446 | 以 required_tools 为正类，对实际调用工具名集合算 P/R/F1 后宏平均 |
| 工具调用成功率% | 100 | tool_call 中 status==success 的占比（近似：只反映工具函数是否报错） |
| 证据覆盖率% | 100 | 至少调用过 1 个工具才下结论的用例占比（对齐 diagnosis_evidence_rate_pct） |
| 平均工具调用数 | 9.81 | 平均每次诊断的 tool_call 数 |
| 平均进度事件数 | 19.62 | on_progress 回调收到的进度事件数均值（tool_start/tool_end 各计一次） |
| 冗余调用率% | 0.4 | (tool_call 总数 - 去重后的 tool+参数 组合数)/总数 |
| 幻觉率% | 0 | 答案命中 forbidden_conclusion 的用例占比 |
| 平均耗时ms | 6502 | DiagnosisRun.duration_ms 的平均值 |
| 平均 tokens | 12831.8 | DiagnosisRun.total_tokens 的平均值 |

## 分故障类型指标（full）

| 故障类型 | 用例数 | 任务成功率% | RCA 准确率(0-1) | 工具选择 F1 | 证据覆盖率% | 幻觉率% | 平均工具调用数 | 冗余调用率% | 平均耗时ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| service_unreachable（服务不可达） | 14 | 100 | 0.964 | 0.468 | 100 | 0 | 8.29 | 0.5 | 6137.1 |
| redis_memory（Redis 内存打满） | 19 | 100 | 0.982 | 0.365 | 100 | 0 | 8.79 | 0 | 5575.3 |
| kafka_lag（Kafka 消费堆积） | 19 | 100 | 0.833 | 0.49 | 100 | 0 | 12.21 | 0.6 | 8865.7 |
| mysql_connections（MySQL 连接打满） | 15 | 100 | 0.978 | 0.435 | 100 | 0 | 12.27 | 1.3 | 6867.5 |
| container_oom（容器 OOMKill） | 15 | 100 | 0.844 | 0.355 | 100 | 0 | 6 | 0 | 4246.1 |
| slow_sql（慢 SQL） | 15 | 93.3 | 0.911 | 0.509 | 100 | 0 | 13 | 0 | 7396.9 |
| log_error_storm（日志错误风暴） | 15 | 100 | 0.889 | 0.515 | 100 | 0 | 7.67 | 0 | 6018 |

## 失败用例（full 组，按需展开）

| 用例 | 消融组 | 故障类型 | 失败原因 |
| --- | --- | --- | --- |
| slow-sql-v060 | full | slow_sql | 答案无任何证据词 |

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
