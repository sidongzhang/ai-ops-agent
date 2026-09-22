# Runbook：MySQL 连接数打满 与 慢 SQL（全表扫描）

适用系统：eval-docker-local。适用告警关键词：500 增多、Too many connections、1040、数据库卡死、凌晨跑批慢、CPU 高。

## 1. 两类问题必须分开定性

**连接数打满**：`Threads_connected` 顶到 `max_connections`，新连接被拒绝并返回 `ERROR 1040 (HY000): Too many connections`。特征是 **3306 端口 ping 得通**（TCP 探活成功），但业务新建连接失败。不要判定为网络抖动。
**慢 SQL**：单条查询缺少可用索引导致全表扫描（`EXPLAIN` 的 `type=ALL`、`possible_keys=NULL`、`key=NULL`），特征是数据库 CPU/慢查询计数升高，但连接数正常。

## 2. 连接数排查步骤

1. `check_service('MySQL')`：确认 3306 端口可达（可达但业务连不上 = 连接数问题）。
2. `query_prometheus('mysql_global_status_threads_connected')`：连接数趋势。
3. `query_prometheus('mysql_global_variables_max_connections')`：当前上限（被调小是最常见的人为原因）。
4. `search_logs('API', '1040', 200)`：确认报错原文为 `1040 Too many connections`。
5. 判定阈值：`Threads_connected / max_connections >= 0.9` 且出现 1040 报错。

## 3. 连接数处置

1. 临时放宽：`SET GLOBAL max_connections=151`（并写回配置，避免重启后失效）。
2. 定位泄漏：检查应用连接池 `maximumPoolSize` 之和是否超过 `max_connections`；开启连接池泄漏检测。
3. 长事务/空闲事务占用：查 `information_schema.processlist` 中 `Sleep` 时间过长的会话。
4. 不要在未确认前重启数据库——会把连接风暴放大。

## 4. 慢 SQL 排查步骤

1. `search_logs('API', '慢查询', 200)` 或检查 `slow_query_log`（`long_query_time=1`）。
2. 对可疑 SQL 执行 `EXPLAIN`，确认 `type=ALL` 且 `key=NULL` 即全表扫描。
3. `query_prometheus('mysql_global_status_slow_queries')` 确认慢查询计数增长。
4. 判定阈值：单条查询 > 1s 且 `EXPLAIN type=ALL`。

## 5. 慢 SQL 处置

1. 为 WHERE/GROUP BY 列补索引：`ALTER TABLE ... ADD INDEX idx_user_created (user_id, created_at)`。
2. 避免 `SELECT *`，限制返回行数与时间范围。
3. 跑批类统计改到低峰期或独立只读实例，必要时预聚合。
4. 补慢查询数量与 `p99` 告警。

## 6. 回滚与验证

评测注入的验证表用完即删：`DROP TABLE IF EXISTS eval_orders_big`（同理 `eval_report_big`、`eval_batch_big`），并 `SET GLOBAL slow_query_log=OFF`。
验证口径：`Threads_connected` 回落到上限的 70% 以下；慢查询计数不再增长。
