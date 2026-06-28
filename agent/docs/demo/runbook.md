# 故障修复经验库


## [2026-06-26 11:02] 数据已从684增长到685，Kafka新产生的1条消息已被consumer消费（

**症状**: Python 业务服务全部停止，导致数据流中断
**根因**: MySQL短暂宕机导致consumer异常退出，进而引发producer和frontend连锁停止
**修复步骤**: 重启producer、consumer和frontend服务
**验证**: 所有服务正常运行，Kafka消息消费正常，Lag=0
**关键词**: 服务离线，连锁故障，MySQL宕机，进程重启，Kafka消费确认

## [2026-06-25 18:36] 数据正常流动，Producer已成功发送第1条数据，Consumer正在消费（L

**症状**: Python业务服务（producer、consumer、frontend）全部停止，Docker基础设施正常运行。

**根因**: Kafka容器短暂宕机导致producer连续连接失败异常退出，consumer和frontend因依赖链断裂相继停止，且无自动重启机制。

**修复步骤**: 网络连通性诊断 → 日志分析确认根因 → 依次重启producer、consumer、frontend服务。

**验证**: Producer成功发送数据，Consumer正在消费（Lag=1），系统完全恢复。

**关键词**: Kafka宕机, Python服务停止, 依赖链断裂, 服务重启, 数据未丢失

## [2026-06-24 14:59] 所有验证通过！数据已从 54 条增长到 55 条（新数据已成功生产并消费），Ka

**症状**: 系统巡检发现 Python 业务服务（producer、consumer、frontend）全部停止。
**根因**: Kafka 容器曾停止运行，导致业务服务因连接失败而退出，Kafka 自动恢复后业务服务未自动拉起。
**修复步骤**: 手动重启 producer、consumer、frontend 服务。
**验证**: 所有服务运行正常，新数据成功生产并消费，Kafka 无积压，Frontend 返回 200。
**关键词**: kafka故障, 业务服务停止, 自动恢复, 服务重启, 数据积压

## [2026-06-24 14:52] 所有数据正常，无异常读数。现在汇总输出最终报告。

**症状**: 系统定时巡检发现 producer 服务已停止，Kafka 容器未运行  
**根因**: Kafka Docker 容器意外停止，导致 producer 因连接超时触发 KafkaTimeoutError 后异常退出  
**修复步骤**: 重启 Kafka 容器并验证端口可达，重启 producer 服务  
**验证**: 所有服务运行中，数据流持续增长，无异常读数  
**关键词**: producer 停止, Kafka 容器故障, KafkaTimeoutError, ECONNREFUSED, Docker 重启, 服务恢复

## [2026-06-24 14:03] 好的，producer 第 7/10 次，consumer 第 5/10 次重试

**症状**: 系统定时巡检发现异常，多个服务持续重试连接Kafka并即将退出，数据生产中断

**根因**: Kafka Docker容器在2026-06-24 05:50:09正常关闭后未自动重启，导致Producer和Consumer无法连接

**修复步骤**: 启动Kafka Docker容器，确认端口9092恢复可用；重启Producer和Consumer服务

**验证**: Kafka服务正常运行（端口9092可达），Producer和Consumer运行状态恢复正常，数据流持续更新

**关键词**: Kafka异常, Docker容器关闭, 服务重试, 数据生产中断, 端口不可达

## [2026-06-24 13:55] 现在让我给出完整的诊断报告。

**症状**: Python producer服务停止，Kafka容器异常关闭，导致无新传感器数据生成约8小时  
**根因**: Kafka Docker容器已停止运行（日志显示正常关闭），导致producer服务无法连接端口9092，重试10次后退出  
**修复步骤**: 启动Kafka容器 → 验证端口9092可达 → 启动producer服务  
**验证**: 确认producer、consumer、frontend及所有Docker基础设施服务运行正常，数据流恢复  
**关键词**: Kafka容器停止, producer服务退出, 端口9092不可达, 数据断流, 系统巡检

## [2026-06-24 13:46] 所有服务全部恢复正常。以下是完整的 **问题报告**：

**症状**: 系统定时巡检发现 producer 服务已停止，进程未运行。
**根因**: Producer 进程在发送第5条数据后意外退出，日志中未发现异常堆栈，属于进程异常退出。
**修复步骤**: 检查 MySQL 连接与数据状态，重启 producer 服务。
**验证**: 所有服务全部恢复正常，数据库数据持续增长。
**关键词**: producer 服务停止, 进程异常退出, 服务重启, 系统巡检

## [2026-06-18 21:11] 所有服务运行正常。下面是完整的**问题报告**。

**症状**: 系统巡检发现 consumer 服务进程意外退出，导致 Kafka 消息积压  
**根因**: consumer 进程静默退出，未留下 ERROR 或异常堆栈日志  
**修复步骤**: 检查 MySQL 端口与数据状态，排查 consumer 日志，检查 Kafka 积压，重启 consumer 服务  
**验证**: consumer 重启成功（新 PID: 67358），所有服务恢复正常  
**关键词**: consumer 已停止, 进程意外退出, Kafka 消息积压, 服务重启, 定时巡检

## [2026-06-18 17:04] ---

**症状**: consumer服务已停止，导致Kafka消息积压40条  
**根因**: consumer进程意外退出，未发现异常日志  
**修复步骤**: 重启consumer服务  
**验证**: consumer成功重启(PID 11169)，自动消费积压消息，Lag降至0，所有服务恢复正常  
**关键词**: consumer停止，进程异常退出，Kafka消息积压，服务重启
<!-- 由 AI Ops Agent 自动归档，最新在前 -->
<!-- 格式：症状 / 根因 / 修复步骤 / 验证 / 关键词 -->
