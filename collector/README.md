# 采集器 (Collector)

装在**客户自己网络内**的轻量采集器。双通道出站连平台——
平台永远不需要入站访问客户内网，也不保管客户的内网地址/凭据。

```
客户内网  [collector/run.py]
   ├─ 上行（HTTP 轮询）─────出站 HTTPS──> 平台
   │   └ 探活结果每 30s 上报一次
   └─ 下行（WebSocket 持久连接）─出站 WSS──> 平台
       └ 平台可随时下发命令：拉日志 / 搜日志 / 实时探活，采集器立即执行回传
```

## 接入步骤

1. 在控制台为某套系统创建采集器，拿到一次性密钥：
   ```
   POST /systems/{id}/collectors  {"name": "客户A机房采集器"}
   → { "collector_key": "xxxx", ... }
   ```
2. 在客户网络里的一台机器上安装依赖并运行采集器：
   ```bash
   pip install requests websockets   # websockets 用于下行通道（可选）
   
   PLATFORM_URL=https://platform.example.com \
   COLLECTOR_KEY=xxxx \
   COLLECTOR_INTERVAL=30 \
   python collector/run.py
   ```
   也可以在仓库根目录构建 Docker 镜像后运行：
   ```bash
   docker build -f collector/Dockerfile -t aiops-collector:local .
   docker run -d --restart=unless-stopped \
     -e PLATFORM_URL=https://platform.example.com \
     -e COLLECTOR_KEY=xxxx \
     --name aiops-collector aiops-collector:local
   ```
   测试可加 `--once` 只跑一轮（跳过 WebSocket 启动）。

之后控制台对该系统的健康面板会显示「采集器上报」的快照（含上报时间）。

## 下行通道（WebSocket）

采集器启动后自动尝试连接 `wss://platform/ws/collector?key=KEY`，
平台可通过以下 API 向该采集器发送命令（同步等待结果，最长 30s）：

```
POST /systems/{id}/collector/exec
{
  "cmd": "fetch_logs",
  "args": {"service": "web", "lines": 50}
}
```

支持命令：

| cmd | args | 返回 |
|---|---|---|
| `fetch_logs` | `service`, `lines`（默认 50） | 日志文本（string） |
| `search_logs` | `service`, `keyword`, `lines`（默认 200） | 匹配行（string） |
| `health_check` | `service`（空=全部） | 健康状态列表（list） |
| `query_prometheus` | `service`, `query`（PromQL） | 在对方内网查询 Prometheus 即时指标 |
| `query_prometheus_range` | `service`, `query`, `start`, `end`, `step` | 查询 Prometheus 时序范围数据 |
| `run_readonly_query` | `sql`, `params`, `max_rows` | 在对方网络执行单条 SELECT，最多返回 100 行 |
| `run_redis_command` | `service`, `command` | 执行白名单 Redis 只读命令 |
| `run_kafka_command` | `service`, `command` | 执行白名单 Kafka topics/consumer-groups 查询 |
| `restart_systemd` | `service`, `unit` | 仅重启服务注册配置中的 systemd unit（需审批） |
| `restart_container` | `service`, `container` | 仅重启注册配置中完全匹配的容器；必须经过平台审批 |

**如果 `websockets` 包未安装**：下行通道跳过，上行轮询照常工作。
**`COLLECTOR_WS=false`**：手动禁用下行通道。

## 依赖

| 包 | 用途 | 必需 |
|---|---|---|
| `requests` | 上行 HTTP 轮询 | 是 |
| `websockets` | 下行 WS 通道 | 可选（不装则无下行） |
| `connectors/` | 探活能力（仓库根目录） | 是 |

可用 pyinstaller / Docker 打成单文件或镜像分发给客户。
