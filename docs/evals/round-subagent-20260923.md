# 取证子代理评测报告（DeepAgents 式上下文隔离，2026-09-23）

> 对照基线：112 条（baseline-112-20260922.md）。改动：主代理新增 investigate 委派工具，
> 取证子代理独立上下文（8 工具子集+极简提示），子代理工具调用冒泡进主轨迹。

## 结果（full 组，112 条）

| 指标 | 基线 | 子代理 | 判定 |
|---|---|---|---|
| tokens | 15084 | **12613（-16.4%）** | ✅ 达成 ≤13k 目标 |
| 成功率 | 97.3% | **98.2%** | ✅ 超基线 |
| RCA | 0.918 | 0.892 | ⚠️ -0.026（约 3 条量级） |
| 幻觉率 | 0% | 0% | ✅ |
| 耗时 | 4.9s | **12.7s** | ⚠️ +2.6 倍（子代理串行 LLM 轮次） |

**结论**：子代理隔离达成 tokens 目标且成功率超基线，架构定稿。
**遗留**：① 延迟翻倍——子代理内工具批量并行化/简单问题跳过委派可缓解；
② RCA 微降 0.026——子代理摘要可能丢失部分证据措辞，可让子代理按 required 证据词强化引用。



- 生成时间：2026-09-23T09:02:09+00:00
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
| 任务成功率% | 98.2 | 命中 required_tools(≥1) 且答案含 ≥1 个 evidence_keys 且未出现 forbidden_conclusion 的用例占比 |
| RCA 准确率(0-1) | 0.892 | Σ(每个 evidence_key 命中权重)/len(evidence_keys)，全命中=1、部分命中=0.5、未命中=0 |
| 工具选择 F1 | 0.427 | 以 required_tools 为正类，对实际调用工具名集合算 P/R/F1 后宏平均 |
| 工具调用成功率% | 100 | tool_call 中 status==success 的占比（近似：只反映工具函数是否报错） |
| 证据覆盖率% | 100 | 至少调用过 1 个工具才下结论的用例占比（对齐 diagnosis_evidence_rate_pct） |
| 平均工具调用数 | 11.64 | 平均每次诊断的 tool_call 数 |
| 平均进度事件数 | 23.29 | on_progress 回调收到的进度事件数均值（tool_start/tool_end 各计一次） |
| 冗余调用率% | 0.7 | (tool_call 总数 - 去重后的 tool+参数 组合数)/总数 |
| 幻觉率% | 0 | 答案命中 forbidden_conclusion 的用例占比 |
| 平均耗时ms | 12713.7 | DiagnosisRun.duration_ms 的平均值 |
| 平均 tokens | 12613.7 | DiagnosisRun.total_tokens 的平均值 |

## 分故障类型指标（full）

| 故障类型 | 用例数 | 任务成功率% | RCA 准确率(0-1) | 工具选择 F1 | 证据覆盖率% | 幻觉率% | 平均工具调用数 | 冗余调用率% | 平均耗时ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| service_unreachable（服务不可达） | 14 | 92.9 | 0.839 | 0.448 | 100 | 0 | 12.5 | 1.6 | 20552.6 |
| redis_memory（Redis 内存打满） | 19 | 100 | 0.974 | 0.366 | 100 | 0 | 10.53 | 0.4 | 7493.9 |
| kafka_lag（Kafka 消费堆积） | 19 | 100 | 0.728 | 0.472 | 100 | 0 | 12.26 | 0.6 | 10338.8 |
| mysql_connections（MySQL 连接打满） | 15 | 100 | 0.967 | 0.412 | 100 | 0 | 14 | 1 | 7754.8 |
| container_oom（容器 OOMKill） | 15 | 93.3 | 0.844 | 0.324 | 100 | 0 | 6 | 0 | 11526.7 |
| slow_sql（慢 SQL） | 15 | 100 | 1 | 0.468 | 100 | 0 | 16.07 | 0.8 | 22973.7 |
| log_error_storm（日志错误风暴） | 15 | 100 | 0.911 | 0.509 | 100 | 0 | 10.33 | 1 | 10903.5 |

## 失败用例（full 组，按需展开）

| 用例 | 消融组 | 故障类型 | 失败原因 |
| --- | --- | --- | --- |
| svc-unreachable-prom-v007 | full | service_unreachable | 答案无任何证据词 |
| container-oom-v067 | full | container_oom | 未命中必需工具（缺 check_service, read_logs) |

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


## 追加优化（合并委派，2026-09-23）

**改动**：多服务体检从 3 次串行委派合并为 1 次委派（子代理一轮内批量并行取证）。

| 指标 | 子代理初版 | 合并委派版 |
|---|---|---|
| 平均耗时 | 12.7s | **6.5s（-49%）** |
| 成功率 | 98.2% | **99.1%（历史新高）** |
| RCA | 0.892 | **0.914** |
| 幻觉 | 0% | 0% |

延迟取舍基本消除：比原始直调基线（4.9s）仅 +1.6s，但换来主上下文隔离与 tokens -16%。
关键认知：耗时大头是 LLM 往返轮次（deepseek 已自动批量并行、pydantic-ai 默认并行执行工具），
减少**串行委派次数**是唯一有效杠杆。
