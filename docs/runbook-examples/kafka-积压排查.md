---
name: Kafka 积压排查
description: 用户询问 Kafka 是否有消息积压、消费延迟、消费组 lag，或实时链路是否阻塞时使用。
---

# Kafka 积压排查规程

## 步骤

1. **看消费组 lag**：调用 `query_business_dataset(dataset="kafka_metrics"...)` 或平台指标；
   若已有 Prometheus，可用 `query_prometheus` 查 `kafka_consumergroup_lag_sum`（按 group/topic）。
   - 关键：**总 Lag > 0 才算积压**；Lag=0 说明消费跟得上。
2. **确认 broker 可达**：查 Kafka 服务健康（端口可达、无 ERROR）。
3. **定位积压来源**：按消费组排序取 Lag 最高者，核对：
   - 消费者实例是否在线；
   - 消费线程是否阻塞（下游 DB/接口变慢）；
   - 是否需要临时扩容消费者。
4. **区分"没消费"与"没数据"**：若 Lag=0 且无消费速率，可能是**没有新数据**，而非链路阻塞——要说明清楚。

## 注意
- 不要把"取不到指标"当成"无积压"；指标不可用要如实说明。
- 结论里给出**具体数值**（总 Lag、Top 消费组），并注明时间范围。
