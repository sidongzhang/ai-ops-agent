# Runbook：Redis 内存打满 / 写入被拒 / 缓存命中率下降

适用系统：eval-docker-local（本地演示栈）。适用告警关键词：redis OOM、缓存命中率下降、登录态丢失、evicted_keys 增长。

## 1. 现象

写入偶发报错、`OOM command not allowed when used memory > 'maxmemory'`、缓存命中率骤降、session 大量丢失被迫重新登录。
注意：这些现象**不是**网络抖动，也**不是**客户端连接池耗尽。连接池耗尽的报错形态是 `PoolExhaustedException`，与 `maxmemory` 无关。排查时先看 Redis 自身的 `INFO memory`，不要先怀疑网络。

## 2. 排查步骤

1. `check_service('Redis')` 确认 6379 端口可达（端口不可达属于「服务不可达」故障，不适用本 runbook）。
2. `run_redis_command('INFO memory')`：重点看 `used_memory_human`、`maxmemory_human`、`maxmemory_policy`、`mem_fragmentation_ratio`。
3. `run_redis_command('INFO stats')`：重点看 `evicted_keys`、`rejected_writes`（或 `expired_keys`）。`evicted_keys` 单调增长说明内存已打满并在淘汰 key。
4. `run_redis_command('INFO clients')`：确认 `connected_clients` 未打满 `maxclients`，用于排除连接数问题。
5. `run_redis_command('SLOWLOG GET 10')`：确认慢命令是否集中在 `KEYS`、`HGETALL` 大 key 上。
6. `read_logs('API', 50)`：确认应用侧报错原文是 `OOM command not allowed`，而不是连接超时。

## 3. 判定阈值

`used_memory` 超过 `maxmemory` 的 90% 即视为内存打满风险；`evicted_keys > 0` 且持续增长即判定「key 被淘汰导致缓存失效」。
`rejected_writes > 0` 说明写入已被硬拒绝（淘汰策略为 `noeviction`）。

## 4. 处置

1. 临时：`CONFIG SET maxmemory 0`（取消上限）或 `CONFIG SET maxmemory 512mb` 并设置 `maxmemory-policy allkeys-lru`。
2. 定位大 key：`run_redis_command('OBJECT ENCODING <key>')`、`STRLEN/LLEN/SCARD` 组合确认 key 体积。
3. 长期：按业务拆分缓存前缀，给 session 与业务缓存分配不同的 Redis 实例或不同的 DB。
4. 补充监控：对 `evicted_keys` 增量与 `used_memory / maxmemory` 比值配置告警。

## 5. 回滚与验证

`redis-cli CONFIG SET maxmemory 0` 后重跑写入，确认 `evicted_keys` 不再增长、`rejected_writes` 不再增长，缓存命中率回升。
