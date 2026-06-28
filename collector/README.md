# 采集器 (Collector)

装在**客户自己网络内**的轻量采集器。出站连平台、本地探测、推回结果——
平台永远不需要入站访问客户内网，也不保管客户的内网地址/凭据。

```
客户内网  [collector/run.py] ──出站 HTTPS──> 平台(controlplane)
   └ 本地用 connectors 探测客户的服务，把健康结果 POST 回平台
```

## 接入步骤

1. 在控制台为某套系统创建采集器，拿到一次性密钥：
   ```
   POST /systems/{id}/collectors  {"name": "客户A机房采集器"}
   → { "collector_key": "xxxx", ... }
   ```
2. 在客户网络里的一台机器上运行采集器：
   ```bash
   PLATFORM_URL=https://platform.example.com \
   COLLECTOR_KEY=xxxx \
   COLLECTOR_INTERVAL=30 \
   python collector/run.py
   ```
   测试可加 `--once` 只跑一轮。

之后控制台对该系统的健康面板会显示「采集器上报」的快照（含上报时间）。

## 依赖

仅需 `requests`，并复用仓库根目录的 `connectors/` 包。可用 pyinstaller / Docker
打成单文件或镜像分发给客户（后续工程化）。

## 当前范围

本版本采集器负责**健康探活上报**。日志按需拉取、远程动作下发（需要平台→采集器
下行指令通道，WebSocket/任务队列）属于后续阶段。
