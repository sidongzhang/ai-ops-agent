# AI 智能运维 Demo

一个完整可运行的 AI 运维 Agent，基于 **LLM + Tool Calling + ReAct** 实现自动故障检测与修复，并通过飞书机器人提供对话式运维入口。
### 接入飞书机器人
本地服务通过 Cloudflare Tunnel 做内网穿透，将 localhost:8080 映射到公网 HTTPS 域名，飞书以 Webhook 事件订阅的方式将消息 Push 到该域名，实现了零公网 IP、零服务器成本的机器人接入。

## 架构

```
Producer (每10秒)
  ↓  Kafka (9092)
Consumer
  ↓  MySQL (3306)  ←── AI Agent 可查询
Frontend (http://localhost:5001)

AI Ops Agent
  ├── list_services     检查服务状态
  ├── check_process     验证进程存活
  ├── read_logs         分析日志
  ├── get_metrics       查 Prometheus 指标
  ├── restart_service   重启服务
  └── query_database    查数据库

飞书机器人
  飞书群消息 → Webhook (8080) → AI Agent → 诊断报告（卡片格式）
  公网地址：webhook.tiancaizhaozhao.dpdns.org（Cloudflare Tunnel）
```

---

## 启动流程

系统分为两层：**常驻后台层**（配置好后自动运行）和**业务系统层**（演示时手动启动）。

### 第一层：常驻后台（登录 Mac 后自动启动，无需操作）

以下服务已通过 macOS LaunchAgent 配置开机自启：

| 服务 | 作用 |
|------|------|
| `com.aiops.feishu-bot` | 飞书 webhook 服务，监听端口 8080 |
| `com.aiops.cloudflared` | Cloudflare 命名隧道，将 8080 暴露到公网 |

Mac 登录后飞书机器人自动可用，无需任何操作。

如需手动控制：

```bash
# 重启飞书 bot
launchctl stop com.aiops.feishu-bot && launchctl start com.aiops.feishu-bot

# 重启 Cloudflare 隧道
launchctl stop com.aiops.cloudflared && launchctl start com.aiops.cloudflared

# 查看日志
tail -f ~/ai-ops-agent/logs/feishu_bot.log
tail -f ~/ai-ops-agent/logs/cloudflared.log
```

### 第二层：业务系统（演示前手动启动）

```bash
cd ~/ai-ops-agent

# 1. 启动 Docker（仅首次或重启后需要）
colima start

# 2. 一键启动全部业务服务
./scripts/start.sh
```

`start.sh` 依次执行：
1. `docker compose up -d` — 启动 Kafka、MySQL、Redis、Prometheus
2. 等待 30 秒（服务初始化）
3. 后台启动 `producer.py`（每 10 秒生产传感器数据）
4. 后台启动 `consumer.py`（Kafka → MySQL）
5. 后台启动 `frontend/app.py`（数据面板，端口 5001）

---

## 使用流程

### 场景零：定时自动巡检（无需操作）

Bot 启动后每 **30 分钟**自动执行一次健康检查：

```
定时触发
  → 快速检查所有服务状态
  → 发现异常：调用 Agent 深度诊断
  → 发送「⚠️ 系统异常检测」告警卡片（红色）
  → 点击「🔧 立即修复」
  → Agent 自动修复所有问题
  → 发送「✅ 修复结果报告」卡片（绿色）
```

正常时静默，有问题才通知。配置项（`.env`）：

```bash
HEALTH_CHECK_INTERVAL=1800   # 检查间隔秒数，默认 30 分钟
FEISHU_ALERT_CHAT_ID=oc_xxx  # 告警目标 chat_id，留空用最近对话的 chat_id
```

---

### 场景一：飞书机器人（主要入口）

在飞书中给机器人发消息（单聊或群聊 @机器人），AI Agent 自动分析并以卡片形式回复。

**基础状态查询：**
```
帮我查一下所有服务现在是什么状态
数据库里现在有多少条数据
最近的日志有没有报错
consumer 消费了多少条消息
```

**故障诊断（先注入再问）：**
```bash
./scripts/inject_fault.sh producer   # 先在命令行注入故障
```
```
系统好像有问题，帮我检查一下
数据好像停止增长了，排查一下原因
producer 还在运行吗，帮我确认一下
```

**数据分析：**
```
查询最近10条传感器数据
有没有异常的传感器读数，帮我分析一下
```

**性能监控：**
```
帮我看看主机的 CPU 和内存使用情况
Prometheus 里现在有哪些指标可以看
```

**复合任务（最能体现 Agent 能力，先全挂再问）：**
```bash
./scripts/inject_fault.sh all
```
```
系统完全没响应了，帮我全面排查并修复
```

### 场景一补充：Web 聊天入口（前端内嵌）

打开数据面板后，点击右下角 🤖 按钮可直接在网页内向 AI 运维助手提问，效果与飞书机器人相同，支持 Markdown 渲染和聊天历史（24 小时缓存）。点击「🔍 立即巡检」按钮或告警横幅中的「立即巡检」会自动发起全面巡检请求。

---

### 场景二：演示 Demo（故障注入 → AI 自动修复）

```bash
# 1. 打开数据面板，确认数据在增长
open http://localhost:5001

# 2. 注入故障（模拟 producer 崩溃）
./scripts/inject_fault.sh producer

# 3. 观察面板数据停止增长

# 4. 让 AI Agent 检测并修复（命令行方式）
cd agent && python3 agent.py "系统好像有问题，帮我检查一下"

# 或直接在飞书发消息，效果相同

# 5. 回到面板，确认数据恢复增长
```

Agent 自动执行链路：
1. `list_services` — 发现 producer 已停止
2. `read_logs producer` — 分析停止原因
3. `restart_service producer` — 重启服务
4. `query_database` — 验证数据恢复写入

### 场景三：命令行交互模式

```bash
cd ~/ai-ops-agent/agent
python3 agent.py                        # 进入交互模式
python3 agent.py "查一下所有服务状态"    # 单次问答
```

---

## 故障注入类型

```bash
./scripts/inject_fault.sh --list   # 查看所有类型
```

| 类型 | 命令 | 现象 |
|------|------|------|
| 进程崩溃 | `producer` / `consumer` / `frontend` | 对应服务停止 |
| 全链路中断 | `all` | 三个服务同时挂掉 |
| 基础设施 | `kafka` / `mysql` | Docker 容器停止 |
| 数据表丢失 | `db-table` | sensor_data 表被删除 |
| 脏数据 | `bad-data` | 向 Kafka 注入 20 条乱码消息 |
| CPU 飙高 | `cpu` | 4 线程压满 CPU，持续 2 分钟 |
| 日志洪水 | `log-flood` | 写入 5 万行垃圾日志 |

---

## 日常运维命令

```bash
# 查看所有服务状态
./scripts/status.sh

# 停止业务系统（不影响飞书机器人）
./scripts/stop.sh

# 查看各服务日志
tail -f logs/producer.log
tail -f logs/consumer.log
tail -f logs/feishu_bot.log
```

---

## 常见问题 & 已知坑

### 飞书机器人不回复
1. **检查 bot 是否运行**：`lsof -ti:8080` 有输出则正常
2. **检查隧道是否连通**：`curl -X POST https://webhook.tiancaizhaozhao.dpdns.org/webhook -H "Content-Type: application/json" -d '{"challenge":"test"}'` 应返回 `{"challenge":"test"}`
3. **飞书开发者后台**：事件订阅 URL 需要通过验证（绿色 ✓），且 app 已发布
4. **权限问题**：单聊需要开通「获取用户发给机器人的单聊消息」权限；群聊 @机器人需要「获取群组中用户@当前机器人的消息」权限

### LaunchAgent 报 Operation not permitted
项目必须放在 `~/ai-ops-agent`（主目录），不能放在 `~/Desktop/ai-ops-agent`。macOS 对 Desktop 目录有沙箱限制，LaunchAgent 进程无权访问。

### `pip: command not found`
macOS 只有 `pip3`，`start.sh` 已修正为 `pip3`。

### `python: command not found`
macOS 只有 `python3`，所有脚本已统一使用 `python3`。

### Kafka 镜像拉取失败
`bitnami/kafka` 在部分网络环境无法拉取，已切换为 `apache/kafka:3.7.0`（官方镜像）。

### 飞书回复格式乱（显示 `##`、`**`）
回复使用了飞书卡片（interactive）格式，若显示原始 Markdown 说明是旧版消息。重启 bot 后新消息会自动用卡片渲染。

### 端口 8080 / 5001 被占用
```bash
lsof -ti:8080 | xargs kill -9   # 释放 8080
lsof -ti:5001 | xargs kill -9   # 释放 5001
```

### Docker 连不上（Cannot connect to Docker daemon）
Colima 未启动，运行 `colima start` 后再试。

---

## 文件结构

```
ai-ops-agent/
├── .env                        环境变量（API Key、数据库配置等）
├── docker-compose.yml          Kafka / MySQL / Redis / Prometheus
├── business/
│   ├── producer.py             数据生产者（→ Kafka）
│   ├── consumer.py             数据消费者（Kafka → MySQL，自动跳过脏消息）
│   └── frontend/
│       ├── app.py              Flask 服务（:5001），纯 Python 路由与数据逻辑
│       ├── templates/
│       │   └── index.html      页面 HTML 结构
│       └── static/
│           ├── css/style.css   全局样式
│           └── js/
│               ├── chat.js     AI 聊天浮窗（历史记录 / Markdown 渲染）
│               └── dashboard.js 数据刷新 / 折线图 / 服务状态 / 一键巡检
├── agent/
│   ├── agent.py                ReAct 主循环（DeepSeek API，while True）
│   ├── tools.py                运维工具集
│   ├── knowledge_base.py       关键词 RAG
│   ├── skills/                 场景化 Skill（日志调查等）
│   └── docs/                   系统拓扑 + 故障修复经验库（自动归档）
├── feishu_bot/
│   ├── server.py               飞书 Webhook 服务（Flask :8080）+ /internal/chat
│   └── feishu_client.py        飞书消息发送客户端（支持卡片格式）
├── scripts/
│   ├── start.sh                一键启动业务系统
│   ├── stop.sh                 停止业务系统
│   ├── inject_fault.sh         故障注入（10 种类型）
│   └── status.sh               查看服务状态
└── logs/                       服务日志（运行后生成）
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM | DeepSeek (`deepseek-chat`) via OpenAI 兼容接口 |
| Agent 框架 | 自实现 ReAct 循环 + Tool Calling |
| 消息队列 | Kafka (Apache 3.7, KRaft 模式) |
| 数据库 | MySQL 8.0 |
| 缓存 | Redis 7 |
| 监控 | Prometheus + Node Exporter |
| 知识库 | 关键词 RAG（无向量库依赖） |
| 飞书集成 | Webhook 事件订阅 + 卡片消息 API |
| 公网暴露 | Cloudflare Tunnel（命名隧道，永久域名） |
| 自动启动 | macOS LaunchAgent |



Node Exporter，是 Prometheus 生态里的一个采集器，专门负责暴露宿主机（你的 Mac）的系统指标。

它做的事：挂载 /proc、/sys 等系统目录，把里面的数据转成 Prometheus 能抓取的格式，暴露在 :9100/metrics。

采集的指标包括：
- CPU 使用率、idle 时间
- 内存使用量
- 磁盘 I/O、磁盘剩余空间
- 网络流量（收发字节数）
- 系统负载（load average）

在这个项目里的作用：Agent 的 get_metrics 工具会向 Prometheus 发 PromQL 查询，Prometheus 再去 scrape Node Exporter 的数据。所以当你注入 cpu 故障时，Agent 可以通过 get_metrics 查到 CPU 飙高的指标，从而辅助判断根因。

访问 http://localhost:9100/metrics 可以看到它暴露的所有原始数据。