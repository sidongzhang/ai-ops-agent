# Runbook：容器 OOMKilled 与 日志错误风暴

适用系统：eval-docker-local。适用告警关键词：容器反复重启、进程消失、Killed、exit 137、日志刷屏、磁盘要满了。

## 1. 容器 OOMKill 排查

现象：容器或进程莫名重启，`Killed` 字样，退出码 `137`（= 128 + SIGKILL 9）。
排查步骤：
1. `check_service('Kafka')` 或对应容器服务，确认容器状态与重启次数。
2. `read_logs('Kafka', 100)` 获取容器日志（需要 Docker 可用）。
3. 观察 `docker inspect` 中的 `State.OOMKilled=true`、`State.ExitCode=137`；这是判定 OOMKill 的决定性证据。
4. `query_prometheus('up')` 检查抓取目标是否随重启出现断点。

判定阈值：`OOMKilled=true` 或退出码 137 出现 ≥1 次即判定内存超限被杀。

处置：
1. 核对容器内存上限（compose `deploy.resources.limits.memory` 或 `docker run --memory`）与 JVM `-Xmx`；本仓库 Kafka 演示环境默认 `KAFKA_HEAP_OPTS=-Xmx256m -Xms128m`，若改成 `-Xmx1g`，在 Docker Desktop 约 2GB 内存下会被 OOMKill。
2. 堆内存设置为限额的 50%~70%，预留 page cache 与堆外内存。
3. **不要**把 OOMKill 归因为宿主机磁盘写满——两者报错形态完全不同（磁盘问题报 `No space left on device`）。

## 2. 日志错误风暴排查

现象：同一异常在短时间内被重复打印数千次，日志文件快速增长。
排查步骤：
1. `read_logs('API', 50)` 读取最近日志。
2. `search_logs('API', 'ERROR', 200)` 统计异常类型，确认是否**同一个异常**在重复（重复度是判定关键）。
3. 检查应用连接池配置（`maximumPoolSize`）与下游依赖的连接上限是否匹配。
4. 检查重试逻辑是否带指数退避；无退避重试会把一次失败放大成日志风暴。

判定阈值：同一异常在 1 分钟内出现 > 100 次，且日志量增长与请求量不成比例（放大系数 > 10）。

处置：
1. 调大连接池或降低并发，先止损。
2. 给重试加指数退避与最大重试次数；对同一异常做日志限流（采样/去重）。
3. 加日志轮转（`maxsize`/`maxbackup`）防止磁盘被写满。
4. 若日志里出现大量来自**不同来源 IP** 的相同请求，才需要考虑外部攻击；否则优先怀疑内部重试放大，不要先下 DDoS 结论。
