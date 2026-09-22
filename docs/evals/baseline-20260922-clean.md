# LLM Agent 真实基线 · 干净方法学版（2026-09-22 第三轮）

> 前置：逐例故障隔离（注入→跑→恢复）+ 真实生效的故障注入（docker_stop）+ 否定语境评分豁免。
> 前两轮（污染环境/缺陷注入）的报告见 baseline-20260922.md，仅作方法学教训留档。

## 三轮对比（成功率 baseline/playbook/rag/full）

| 轮次 | 环境状态 | baseline | playbook | rag | full | 结论 |
|---|---|---|---|---|---|---|
| 第一轮 | 污染（容器串扰） | 96 | 76 | 92 | 92 | "Playbook 负收益"是假象 |
| 第二轮（逐例隔离，注入缺陷仍在） | 96 | 88 | 92 | 88 | 部分负收益 |
| **第三轮（干净）** | **100** | **100** | **96** | **100** | **Playbook 负收益消除** |

## 干净基线核心发现

1. **成功率 100%（baseline/playbook/full）**：故障真实注入 + 评分公正后，Agent 在全部可注入用例上任务成功。
2. **full 组工具选择 F1 四组最高（0.534，精确率 0.388）**：剧本+RAG 对工具选择有真实正贡献。
3. **rag 组 96%**：唯一失败 container-oom-003（RCA 0.833，缺 1/3 证据键，用例为 manual 注入类型）。
4. **下一个优化靶点**：
   - RCA 精度 0.77~0.82（约 1/5 evidence_key 未命中，是质量上限的瓶颈）
   - Tool-F1 ~0.5（精确率低 = 调用工具偏多，收口策略可省步数）
   - full 组比 baseline 多 ~10% tokens（Context 压缩的空间）



- 生成时间：2026-09-22T08:33:16+00:00
- 运行模式：真实 LLM 调用
- 数据集：`/Users/zhangsidong/ai-ops-agent/evals/datasets/seed_cases.jsonl`（25 条用例）
- 故障注入状态：journal 中仍标记为已注入的用例: 无
- 描述符目录：`/Users/zhangsidong/ai-ops-agent/evals/fixtures/descriptors`
- 知识库目录：`/Users/zhangsidong/ai-ops-agent/evals/knowledge/docs`（4 篇：container-oom-and-log-storm-runbook.md, kafka-lag-runbook.md, mysql-connections-slow-sql-runbook.md, redis-memory-runbook.md）
- RAG 检索模式：auto（允许 embedding API）
- 模型覆盖：`(默认路由)`（model_mode=auto）
- 并发度：1；单用例超时：300.0s
- 消融组：baseline, playbook, rag, full
- 主要指标组：**baseline**（下方「总体指标」「分故障类型」均指该组）

## 总体指标（baseline）

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 任务成功率% | 100 | 命中 required_tools(≥1) 且答案含 ≥1 个 evidence_keys 且未出现 forbidden_conclusion 的用例占比 |
| RCA 准确率(0-1) | 0.773 | Σ(每个 evidence_key 命中权重)/len(evidence_keys)，全命中=1、部分命中=0.5、未命中=0 |
| 工具选择 F1 | 0.469 | 以 required_tools 为正类，对实际调用工具名集合算 P/R/F1 后宏平均 |
| 工具调用成功率% | 100 | tool_call 中 status==success 的占比（近似：只反映工具函数是否报错） |
| 证据覆盖率% | 100 | 至少调用过 1 个工具才下结论的用例占比（对齐 diagnosis_evidence_rate_pct） |
| 平均工具调用数 | 6.6 | 平均每次诊断的 tool_call 数 |
| 平均进度事件数 | 13.2 | on_progress 回调收到的进度事件数均值（tool_start/tool_end 各计一次） |
| 冗余调用率% | 0 | (tool_call 总数 - 去重后的 tool+参数 组合数)/总数 |
| 幻觉率% | 0 | 答案命中 forbidden_conclusion 的用例占比 |
| 平均耗时ms | 4998.6 | DiagnosisRun.duration_ms 的平均值 |
| 平均 tokens | 12851.2 | DiagnosisRun.total_tokens 的平均值 |

## 分故障类型指标（baseline）

| 故障类型 | 用例数 | 任务成功率% | RCA 准确率(0-1) | 工具选择 F1 | 证据覆盖率% | 幻觉率% | 平均工具调用数 | 冗余调用率% | 平均耗时ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| service_unreachable（服务不可达） | 5 | 100 | 0.633 | 0.471 | 100 | 0 | 5.4 | 0 | 4723.8 |
| redis_memory（Redis 内存打满） | 4 | 100 | 0.916 | 0.321 | 100 | 0 | 9 | 0 | 5787.2 |
| kafka_lag（Kafka 消费堆积） | 4 | 100 | 0.75 | 0.49 | 100 | 0 | 7.5 | 0 | 5154.8 |
| mysql_connections（MySQL 连接打满） | 3 | 100 | 1 | 0.492 | 100 | 0 | 8.67 | 0 | 5375.3 |
| container_oom（容器 OOMKill） | 3 | 100 | 0.833 | 0.468 | 100 | 0 | 5.67 | 0 | 5016.7 |
| slow_sql（慢 SQL） | 3 | 100 | 0.667 | 0.411 | 100 | 0 | 5.33 | 0 | 3849 |
| log_error_storm（日志错误风暴） | 3 | 100 | 0.667 | 0.667 | 100 | 0 | 4.33 | 0 | 4952 |

## 消融对比

| 消融组 | 用例数 | 任务成功率% | RCA 准确率(0-1) | 工具选择 F1 | 工具选择 P | 工具选择 R | 证据覆盖率% | 幻觉率% | 平均工具调用数 | 冗余调用率% | 平均 tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 25 | 100 | 0.773 | 0.469 | 0.335 | 0.84 | 100 | 0 | 6.6 | 0 | 12851.2 |
| playbook | 25 | 100 | 0.793 | 0.46 | 0.321 | 0.84 | 100 | 0 | 8.32 | 0.2 | 14349.5 |
| rag | 25 | 96 | 0.82 | 0.471 | 0.339 | 0.82 | 100 | 0 | 6.28 | 0 | 11965.2 |
| full | 25 | 100 | 0.78 | 0.534 | 0.388 | 0.9 | 100 | 0 | 7.32 | 0 | 12283.1 |

消融组定义：
- `baseline`：裸 Agent：skill_steps=""、knowledge_context=""、data_catalog=""
- `playbook`：仅 YAML 剧本：skill_steps=match_skill(question).steps
- `rag`：仅知识库：knowledge_context=get_relevant_context(question, system_id)
- `full`：线上默认行为：剧本 + RAG

## 失败用例（baseline 组，按需展开）

无失败用例。

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


---

## 第一循环优化结果（2026-09-22，块 2 完成）

**改动**：①「关键证据」必须引用工具返回原文凭证（错误类名/数值/对象），禁止纯转述；
② evidence_keys 支持 any_of 等价表述组并修正 5 条用例 GT；③ 新工具 check_container_state。

| 组 | 指标 | 基线 → 改造后 |
|---|---|---|
| full | RCA | 0.780 → **0.92**（+0.14，达标） |
| full | 成功率 | 100% → 100% |
| playbook | RCA | 0.793 → **0.90** |
| playbook | 成功率 / 步数 / tokens | 100% / 8.3→7.7 / 14.3k→14.1k（全改善） |

**full 组遗留问题（块 3 目标）**：tokens +3149（+26%）、步数 +1.0、Tool-F1 -0.084。
预研定位：query_prometheus 占调用 28%（52/186 次 vs baseline 19 次），每次返回原始 metrics JSON——
改为关键行格式化 + 截断，并让剧本 PromQL 步骤按需引导，预期 tokens 拉平、步数 -30%。


## 块 3 结果（收口策略，2026-09-22）

**改动**：① 8 个剧本加「按需执行：证据足够即收口」引导；② query_prometheus 返回 20→8 条+标签精简。

| 指标（full 组） | 基线 | 块 2 | 块 3 |
|---|---|---|---|
| RCA | 0.780 | 0.92 | **0.913**（保持） |
| 成功率 | 100% | 100% | **100%** |
| 平均步数 | 7.32 | 8.32 | **7.6**（-0.7） |
| Tool-F1 | 0.534 | 0.45 | **0.484**（回升） |
| tokens | 12.3k | 15.4k | 14.98k（-3%，未达标） |

**遗留**：tokens 仍高于基线 22%。分析显示大头不在单次工具输出（最大 1123 字符/次），
而在多轮对话的上下文累积（每步重发全历史）——根治需跨轮历史压缩，
属于 Context Engineering 的完整立项（P1），超出本轮「小步快跑」范围。
