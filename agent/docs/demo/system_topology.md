# 系统拓扑文档

## 服务清单

### Python 业务服务（由 start.sh 启动）

| 服务名 | 脚本路径 | 端口 | PID 文件 | 日志文件 |
|--------|----------|------|----------|----------|
| producer | business/producer.py | — | pids/producer.pid | logs/producer.log |
| consumer | business/consumer.py | — | pids/consumer.pid | logs/consumer.log |
| frontend | business/frontend/app.py | 5001 | pids/frontend.pid | logs/frontend.log |

### Docker 基础设施（由 docker compose 管理）

| 服务 | 镜像 | 端口 | 功能 |
|------|------|------|------|
| kafka | bitnami/kafka:3.6 | 9092 | 消息队列（KRaft 模式） |
| mysql | mysql:8.0 | 3306 | 数据库，库名 opsdb |
| redis | redis:7 | 6379 | 缓存 |
| prometheus | prom/prometheus | 9090 | 监控指标采集 |
| node-exporter | prom/node-exporter | 9100 | 主机指标暴露 |

## 数据流

```
Producer (每10秒)
  → Kafka topic: sensor-data (localhost:9092)
    → Consumer
      → MySQL: opsdb.sensor_data (localhost:3306)
        → Frontend http://localhost:5001
```

## MySQL 表结构

```sql
CREATE TABLE sensor_data (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    sensor_id  VARCHAR(50) NOT NULL,
    value      FLOAT NOT NULL,
    unit       VARCHAR(20) DEFAULT 'celsius',
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 常见故障与排查步骤

### Producer 停止
- 症状：数据库条数长时间不增加，Kafka 无新消息
- 排查：check_process producer → read_logs producer
- 修复：restart_service producer
- 验证：check_process producer，再看 query_database "SELECT COUNT(*) FROM sensor_data"

### Consumer 停止
- 症状：Kafka 有消息但数据库不增加
- 排查：check_process consumer → read_logs consumer
- 修复：restart_service consumer

### Frontend 停止
- 症状：http://localhost:5001 无法访问
- 排查：check_process frontend → read_logs frontend
- 修复：restart_service frontend

### MySQL 连接失败
- 症状：consumer 日志出现 "MySQL connection error" 或 "Can't connect"
- 排查：query_database "SELECT 1"
- 修复：docker compose restart mysql（需手动执行）

### Kafka 不可用
- 症状：producer 日志出现 "NoBrokersAvailable" 或 "KafkaTimeoutError"
- 排查：get_metrics "up"
- 修复：docker compose restart kafka（需手动执行）

## Prometheus 常用查询

- `up` — 所有监控目标的在线状态（1=在线，0=离线）
- `node_memory_MemAvailable_bytes` — 可用内存字节数
- `node_cpu_seconds_total` — CPU 使用时间（按 mode 分类）
- `node_filesystem_avail_bytes` — 磁盘可用空间
- `node_load1` — 1分钟平均负载

## 访问地址

- 前端数据面板：http://localhost:5001
- Prometheus 查询：http://localhost:9090
- Node Exporter 原始指标：http://localhost:9100/metrics
