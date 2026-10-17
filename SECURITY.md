# 安全与运维基线

这份文档记录**已修复的安全红线**、**上线前必须完成的检查项**，以及备份/恢复操作。
背景见 [`HEALTH-REPORT.md`](HEALTH-REPORT.md)（全项目体检报告）。

---

## 1. 已在代码层修掉的四件事

| 问题 | 现在的位置 | 行为 |
|---|---|---|
| JWT 使用源码默认密钥 | `backend/app/core/config.py` | 无默认值。`ENVIRONMENT=production` 下缺失/过短/仍是历史默认值 → **拒绝启动**；dev 缺失 → 生成本进程随机密钥（重启即失效）并告警 |
| 飞书 Webhook 零验签 | `backend/app/api/feishu_webhook.py`、`services/notifications/webhook.py` | 校验 `FEISHU_VERIFICATION_TOKEN`（v2 `header.token` 与 v1 顶层 `token` 都支持）；未配置 → 503，token 不符 → 403；配置 `FEISHU_ENCRYPT_KEY` 时按飞书规范 AES-256-CBC 解密 |
| 集成 token 硬编码在启动脚本 | `scripts/stack-launcher.sh` | 改为从 `.env` 的 `ALGP_SYSTEM_TOKEN` 读取；缺失时告警且不注入 |
| 零备份 | `scripts/backup_db.py`、`scripts/restore_db.py`、`scripts/backup.sh` | `pg_dump` 优先；无 `pg_dump` 时逐表导出 JSONL.gz；含 manifest（行数 + sha256）、锁、保留策略与恢复脚本 |

凭据加密（`ENCRYPTION_KEY`）在生产环境缺失同样会**拒绝启动**；dev 留空时给出启动告警（明文落库只允许出现在本机开发）。

---

## 2. 上线前检查清单

```bash
# 1) 生产模式
ENVIRONMENT=production

# 2) JWT 密钥（≥32 字符，强随机）
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3) 凭据加密密钥（Fernet，丢失后已加密数据无法恢复，请单独保管）
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4) 飞书验签（事件订阅页面的 Verification Token）
FEISHU_VERIFICATION_TOKEN=...

# 5) 可选：事件加密（Encrypt Key）
FEISHU_ENCRYPT_KEY=...
```

> 启动自检：缺 2)/3) 时进程会直接报错退出（这是刻意的 fail-fast，不是 bug）。

---

## 3. 必须人工轮换的外部密钥（代码改不了）

`.env` 曾经进入过公开 git 历史，**所有当时存在的密钥都应视为已泄露**：

| 密钥 | 去哪里轮换 |
|---|---|
| `DEEPSEEK_API_KEY` / `LLM_API_KEY` | DeepSeek 控制台 → API Keys（吊销旧的、生成新的） |
| `FEISHU_APP_SECRET` | 飞书开放平台 → 应用凭证（重置 App Secret） |
| `FEISHU_VERIFICATION_TOKEN` | 飞书开放平台 → 事件订阅（重新生成） |
| 平台系统 token（`sys_...`） | 已在本轮轮换：旧 token 已置为 `revoked`，新 token 只存在于 `.env` |

历史提交里的旧值不会因为轮换而消失，但轮换后它们**不再有效**——这是标准处置方式，无需改写 git 历史。

---

## 4. 备份与恢复

```bash
# 备份（默认读取 .env 的 DATABASE_URL，保留最近 14 份）
backend/.venv/bin/python scripts/backup_db.py --keep 14
# 或：scripts/backup.sh

# 查看备份状态（launchd/cron 可用它做告警，>26h 返回非 0）
scripts/backup.sh check

# 恢复（先预览，确认后加 --yes；会清空同名表）
backend/.venv/bin/python scripts/restore_db.py --backup backups/20261017-160623
backend/.venv/bin/python scripts/restore_db.py --backup backups/20261017-160623 --yes
```

产物结构：

```
backups/20261017-160623/
├── manifest.json          # 创建时间、脱敏后的库地址、表行数、每个文件的 sha256
├── dump.pgc               # 有 pg_dump 时生成，可用 pg_restore 恢复
└── tables/*.jsonl.gz      # 否则逐表导出，用 restore_db.py 恢复（按外键依赖排序）
```

定时执行（macOS）：

```xml
<!-- ~/Library/LaunchAgents/com.aiops.backup.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>com.aiops.backup</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>/Users/你/ai-ops-agent/scripts/backup.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><true/>
</dict></plist>
```

> 备份目录含业务数据，已加入 `.gitignore`；请另外同步到异机/对象存储（本脚本只负责本地产物）。

---

## 5. 静默失效已改为显式失败

| 位置 | 以前 | 现在 |
|---|---|---|
| 代码沙箱 | 配置了镜像但 docker 不可用 → 静默在 API 进程内执行模型代码 | 直接拒绝并返回错误；生产未配镜像同样拒绝 |
| 连接器工厂 | 未知 `connector` 类型 → 静默回退 `LocalConnector`（拿 127.0.0.1 探活） | 抛 `ValueError` 并列出可用类型，健康检查显示"检查失败: 未知的连接器类型 …" |
| 数据集查询审计 | 审计写失败 → `except: pass` | `log.exception` + `aiops_audit_write_failures_total` |
| Kafka 兜底 | 兜底失败 → 静默走错误路径 | 记录 warning（含堆栈）后走统一错误路径 |
| 凭据加密 | dev 无密钥时静默明文落库 | 启动时打印告警（生产已直接拒绝启动） |
| 归档附件下载 | token 永久有效、不校验系统 | `ARTIFACT_TTL_HOURS`（默认 168h）+ `system_id` 绑定 + `no-store`；无元数据 fail-closed |
| `collector/exec` | 只校验登录，viewer 也能下发 SQL/Redis/Kafka 命令 | 要求 owner/operator，viewer 返回 403 |

## 6. 平台自身指标

`GET /metrics`（Prometheus 文本格式）暴露控制面自己的失败信号，`prometheus/prometheus.yml`
已加入 `aiops-control-plane` 抓取任务（容器内经 `host.docker.internal:8000`）。

| 指标 | 含义 | 建议告警 |
|---|---|---|
| `aiops_up` | 进程存活 | `up{job="aiops-control-plane"} == 0` |
| `aiops_db_up` | 数据库连通 | `aiops_db_up == 0` |
| `aiops_audit_write_failures_total` | 审计写入失败 | `increase(...[10m]) > 0` |
| `aiops_sandbox_blocked_total` | 沙箱拒绝执行 | `increase(...[10m]) > 0` |
| `aiops_unknown_connector_total` | 未知连接器类型 | `increase(...[10m]) > 0` |

需要鉴权时设置 `METRICS_TOKEN`，抓取端带 `Authorization: Bearer <token>`。

## 7. 还剩的工程项

- 前端缺 ESLint 配置与锁文件（`.gitignore` 忽略了 `package-lock.json`），CI 只跑后端测试与评测冒烟；
- `services/diagnostics/service.py` 仍有 1300+ 行，`FallbackPolicy` 未抽；
- `mcp.py` 每次调用起 stdio 子进程（无连接复用）；
- 备份产物只落本机，需自行同步到异机/对象存储。
