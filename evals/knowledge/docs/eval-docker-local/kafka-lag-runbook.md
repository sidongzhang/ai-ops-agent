# Runbook：Kafka 消费堆积（lag）排查

适用系统：eval-docker-local。适用告警关键词：消息积压、lag、消费延迟、rebalance、topic 消费不掉。
本系统 Kafka 指标来自 kafka-exporter，经 Prometheus 采集。正确指标名：`kafka_consumergroup_lag`、`kafka_consumergroup_lag_sum`、`kafka_consumergroup_current_offset`、`kafka_topic_partitions`、`kafka_brokers`。不要使用 JMX 格式（`kafka_server_*`）。

## 1. 现象

消息延迟告警、业务数据长时间未更新、消费端日志频繁 rebalance。**消息一般没有丢失**，只是堆积在 topic 里，回答时要明确这一点。

## 2. 排查步骤

1. `check_service('Kafka')`：确认 9092 端口可达。端口不可达说明是 broker 本身的问题。
2. `query_prometheus('kafka_brokers')`：确认 broker 数量，排除「Kafka 挂了」。
3. `query_prometheus('kafka_consumergroup_lag')`：按消费组、topic、partition 看积压分布。
4. `query_prometheus('kafka_consumergroup_lag_sum')`：看总积压量的趋势。
5. `run_kafka_command('consumer-groups --describe --all-groups')`：拿到 `CURRENT-OFFSET`、`LOG-END-OFFSET`、`LAG` 与成员数。
6. `run_kafka_command('topics --list')`：确认 topic 是否存在、分区数是否合理。
7. `read_logs('ConsumerWorker', 50)`：检查 `rebalance`、`max.poll.interval.ms exceeded`、`offset commit skipped` 等日志。

## 3. 判定阈值

单分区 `kafka_consumergroup_lag` 持续增长超过 10000，或总积压 `kafka_consumergroup_lag_sum` 10 分钟内翻倍，判定为消费能力不足。
消费组无成员（`CONSUMER-ID` 为空）说明消费进程未运行或已崩溃。
频繁 rebalance（5 分钟内 ≥3 次）说明消费者处理超时或实例抖动。

## 4. 处置

1. 消费进程缺失：先恢复消费进程（属于「服务不可达」，不要用重启 broker 掩盖）。
2. 处理能力不足：水平扩容消费者实例数（不超过分区数），或增大批处理并行度。
3. `max.poll.interval.ms` 超时：下调 `max.poll.records`，让单次 poll 处理时间小于该阈值。
4. 分区倾斜：检查 key 设计，必要时调整分区数并对热点 key 打散。
5. 不要删除或重置 offset 作为第一手段——会丢消息。

## 5. 回滚与验证

批量灌入测试数据造成的堆积，验证完成后删除测试 topic：`run_kafka_command('topics --delete --topic <topic>')`（写操作被工具安全策略拒绝时，人工在容器内执行）。
验证口径：消费组 `LAG` 持续回落至 0 并稳定。
