# 系统架构文档

## 服务清单

| 服务 | 连接方式 | 端口 | 说明 |
|---|---|---|---|
| MySQL | TCP | 3306 | 主数据库，存储运维数据 |
| Redis | TCP | 6379 | 缓存层，Celery broker |
| Kafka | TCP | 9092 | 消息队列，消费者组 ops-consumer-group |
| Prometheus | HTTP | 9090 | 指标采集，15s 间隔 |
| NodeExporter | HTTP | 9100 | 主机指标暴露（CPU/内存/磁盘） |
| Platform-API | HTTP | 8000 | 控制面 FastAPI |

## 重要运维经验

- Kafka 出现 lag 增长时，首先检查消费者组 ops-consumer-group 是否在线
- Redis 内存接近峰值属于正常（LRU 策略会自动淘汰）
- MySQL 连接数超过 100 时需要排查连接泄漏
- NodeExporter 9100 必须 up 才能采集到主机 CPU/内存指标

## 常见故障处理

### Kafka 消费者积压
1. 确认消费者进程是否在运行
2. 查看消费者日志是否有连接错误
3. 检查 Kafka 容器状态（ai-ops-agent-kafka-1）

### MySQL 连接数异常
1. 查看当前连接数：mysql_global_status_threads_connected
2. 检查是否有慢查询堆积
3. 必要时重启连接池
