# 故障修复经验库


## [2026-07-10 03:09] kafka什么状态

**摘要**: 好的，信息已收集完毕。下面是 **Kafka 状态报告**。  ---  ## 📋 Kafka 状态报告  ### 1️⃣ Broker 状态 > ⚠️ 无法通过 Prometheus 获取 broker 数量（Prometheus 当前 502 不可用），但通过 `run_kafka_command` 确认 Kafka 进程可正常响应命令，说明 **Kafka 服务本身是存活的**。  ### 2️⃣ Topic 列表 | Topic | 说明 | |---|---| | `__consumer_offsets` | 内部 offsets 主题（正常） | | **`sensor-data`...


## [2026-07-10 03:11] 重启Prometheus

**摘要**: 知识库中没有 Prometheus 重启相关的特定操作手册。不过根据状态信息，我来给你做一下整体的分析和说明。  ---  ## 📋 问题报告  ### 发现的问题  | 服务 | 状态 | 说明 | |------|------|------| | **Prometheus** (prometheus.prod:9090) | ❌ **502 Bad Gateway** | HTTP 502，服务不可用 | | **API网关** (api.example.com) | ❌ **连接被拒绝** | 无法连接 | | **用户服务** (user.example.com) | ❌ **连接被拒...

