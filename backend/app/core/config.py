"""控制面配置（pydantic-settings）。复用仓库根目录的 .env（含 DEEPSEEK_API_KEY 等）。"""
import os
import secrets
import warnings

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 本机服务（Prometheus、各被监控服务探活、采集器）绝不能走 HTTP 代理。
# 开发机上常设的 HTTP_PROXY 会把 127.0.0.1 的请求也转发出去，代理一旦不可用，
# 所有本地探活都会变成 502，进而把假故障写进 AI 诊断结论。
_LOCAL_NO_PROXY = "127.0.0.1,localhost,::1,0.0.0.0"


def _normalize_no_proxy(value: str) -> str:
    """清洗继承来的代理绕过列表，返回规范化后的逗号串。

    常见的代理工具/IDE 会把 IPv6 回环写成带方括号的 `[::1]`。httpx 解析该列表时
    按 `host:port` 切分，遇到 `[::1]` 会抛 `httpx.InvalidURL: Invalid port: ':1]'`，
    而 httpx 客户端在模块导入期就会构造，导致整个应用无法启动（不只是本地探活失效）。
    这里统一去掉方括号，与 _LOCAL_NO_PROXY 的写法保持一致。
    """
    parts = []
    for raw in (value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if item.startswith("[") and item.endswith("]"):
            item = item[1:-1]
        parts.append(item)
    return ",".join(parts)


for _var in ("NO_PROXY", "no_proxy"):
    _current = _normalize_no_proxy(os.environ.get(_var, ""))
    _merged = ",".join(part for part in (_current, _LOCAL_NO_PROXY) if part)
    os.environ[_var] = _merged

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 历史上用过的开发默认值。它们已经进入公开 git 历史，必须视为已泄露：
# 生产环境出现这些值等同于"任何人都能签发平台 owner 的 token"。
_LEGACY_INSECURE_JWT_SECRETS = {
    "dev-secret-change-me-in-prod",
    "change-me-in-production",
}
_MIN_JWT_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(_REPO_ROOT, '.env'),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # 运行环境：dev / production。production 下安全配置缺失时拒绝启动（fail-fast）。
    environment: str = "dev"

    # 数据层：dev 用 SQLite，生产改成 postgresql+psycopg://... 即可
    database_url: str = f"sqlite:///{os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'dev.db')}"

    # 鉴权：不提供默认值。开发环境缺失时生成本进程随机密钥（重启即失效），
    # 生产环境缺失或仍是历史默认值时拒绝启动。
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    # 登录失败限流（按账号，滑动窗口）。多 worker 时额度按 worker 数放大。
    login_max_failures: int = 5
    login_window_seconds: int = 300
    # CORS 允许来源（逗号分隔）。留空 = *（且不携带凭证）；填了才启用 credentials。
    cors_origins: str = ""
    # 受信任的反向代理（逗号分隔，支持 CIDR）。只有来自这些地址的请求才采信 X-Forwarded-For。
    trusted_proxies: str = "127.0.0.1,::1"

    # Agent 模型（OpenAI 兼容）。推荐新配置：LLM_MODE=api/local。
    pi_price_profile: str = ""
    pi_runtime_enabled: bool = False
    pi_runtime_url: str = "http://127.0.0.1:8810"
    pi_service_secret: str = ""

    llm_mode: str = "api"  # api: 远程 API；local: 本地部署（Ollama/vLLM/LM Studio 等）
    llm_api_base_url: str = ""   # 空则兼容复用 deepseek_base_url
    llm_api_key: str = ""        # 空则兼容复用 deepseek_api_key
    llm_api_model: str = ""      # 空则兼容复用 agent_model
    llm_local_base_url: str = "http://localhost:11434/v1"
    llm_local_api_key: str = "ollama"
    llm_local_model: str = "qwen2.5:0.5b"
    # 本地模型（CPU 推理）偶发"卡死"：给读超时，卡住时变成可读失败而不是无限挂起。
    # 注意：本地模型 prefill 我们的长系统提示（~9k tokens）在纯 CPU 上可能就要几分钟，
    # 所以这里默认给 300s；更慢的机器用 LLM_LOCAL_TIMEOUT_SECONDS 调大。
    llm_local_timeout_seconds: float = 300.0
    # 高级模型可单独指定；未指定则复用当前 LLM_MODE 对应配置。
    advanced_llm_mode: str = ""
    advanced_llm_api_base_url: str = ""
    advanced_llm_api_key: str = ""
    advanced_llm_api_model: str = ""
    advanced_llm_local_base_url: str = ""
    advanced_llm_local_api_key: str = ""
    advanced_llm_local_model: str = ""

    # 旧配置保留兼容：默认 DeepSeek，OpenAI 兼容
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    agent_model: str = "deepseek-chat"
    # 高级模型：含 P0/崩溃/数据丢失等关键词时自动升档（也走 OpenAI 兼容接口，可指向任意厂商）
    advanced_agent_model: str = "deepseek-reasoner"

    # 是否把每次诊断的摘要自动追加到系统知识库 runbook。
    # 默认关闭：模型输出的数字可能出错，一旦写进知识库就会被后续诊断当作"权威依据"引用，
    # 形成自我投毒的闭环。知识库应由人工维护。
    diagnosis_auto_runbook: bool = False
    # 结构化长期记忆（带来源/置信度/有效性闭环，污染风险受控）：诊断/工作流完成后自动沉淀
    diagnosis_memory_enabled: bool = True

    # 单次诊断的资源上限，防止 agent 陷入循环无限烧 token。
    # 每多一轮 loop 都要重发整段上下文，所以这三项同时也是成本闸门。
    diagnosis_request_limit: int = 12        # 模型请求轮数
    diagnosis_tool_calls_limit: int = 24     # 工具调用次数
    diagnosis_token_limit: int = 100000      # 累计 token（含每轮重复计入的上下文）
    diagnosis_stop_wait_seconds: float = Field(default=20.0, ge=0, le=120)

    # 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str = "INFO"
    advanced_agent_base_url: str = ""    # 空则复用 deepseek_base_url
    advanced_agent_api_key: str = ""     # 空则复用 deepseek_api_key

    # Embedding API（RAG 知识库用，OpenAI 兼容）
    # 空则复用 deepseek_api_key / deepseek_base_url
    embedding_model: str = "text-embedding-v2"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    # 向量维度：必须与 embedding_model 输出维度一致（nomic-embed-text=768，
    # text-embedding-v2/OpenAI text-embedding-3-small=1536）。修改后需重建知识库索引。
    embedding_dim: int = 1536

    # 检索重排（strong rerank）：api=用 rerank API（快，需配 base_url）；llm=用大模型重排（较慢）；
    # auto=有 API 用 API、否则 LLM；off=关闭。默认 api（未配置 API 时自动跳过，不引入延迟）。
    # auto：有 rerank 端点用 API，否则**回退到聊天模型重排**（每次检索多 3~5s，默认不用）；
    # api：只有配置了 RERANK_BASE_URL 才重排；llm：显式用聊天模型重排；off：关闭。
    rerank_mode: str = "api"
    rerank_base_url: str = ""       # 通用 rerank 接口（Cohere/Jina/SiliconFlow 风格）
    rerank_model: str = ""
    rerank_api_key: str = ""
    rerank_timeout_seconds: float = 8.0

    # Langfuse 可观测性（留空则跳过追踪）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # 飞书机器人（Webhook 接收 + 主动推送共用同一个自建应用）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # 事件订阅「Verification Token」；配置后所有 webhook 必须携带一致 token，否则 403。
    feishu_verification_token: str = ""
    # 事件订阅「Encrypt Key」；配置后按飞书规范 AES-256-CBC 解密整包。
    feishu_encrypt_key: str = ""
    feishu_alert_chat_id: str = ""   # 默认告警群，无 chat_id 时回退到此

    # 凭据字段加密（Fernet AES-128）；空字符串 = dev 模式跳过加密
    encryption_key: str = ""

    # 审计日志保留天数（<=0 关闭清理）。清理在启动时与每 24h 执行一次。
    audit_retention_days: int = 180
    # 平台自身 /metrics（Prometheus 抓取）。留空 = 匿名可读（仅建议内网）；
    # 设置后要求 Authorization: Bearer <token>。
    metrics_token: str = ""

    # Celery 定时巡检
    celery_broker_url: str = "redis://localhost:6380/0"
    celery_result_backend: str = "redis://localhost:6380/1"
    health_check_interval: int = 60        # 巡检周期（秒）
    alert_cooldown_seconds: int = 3600     # 同一系统两次告警最小间隔（秒）

    # 趋势巡检（早期预警）：采样指标 → 线性外推 → 未到阈值就提醒
    trend_patrol_enabled: bool = True
    trend_patrol_interval: int = 900       # 趋势采样周期（秒）

    # 诊断中断（停止）
    diagnosis_cancel_channel: str = "aiops:diagnosis:cancel"
    diagnosis_cancel_poll_seconds: float = 2.0    # DB 兜底巡检周期（Redis 丢消息也能取消）
    diagnosis_cancel_poll_jitter: float = 0.3
    diagnosis_max_run_seconds: int = 900          # 单次运行上限（超时交 reaper）
    diagnosis_shutdown_grace_seconds: float = 15.0  # 关停等待落库的宽限（uvicorn graceful 需 ≥）
    diagnosis_stale_heartbeat_seconds: int = 60   # 心跳过期判据（运行方每轮巡检续期，约 2s）
    diagnosis_stale_running_minutes: int = Field(default=5, ge=1)  # 尚未注册心跳的任务启动宽限
    # 预算：未知用量不得按免费处理 → 用保守预留结算
    diagnosis_session_token_budget: int = 300000
    diagnosis_session_cost_budget_usd: float = 1.0
    diagnosis_session_window_seconds: int = 600   # 滚动窗口（非按天归零）
    diagnosis_budget_reserve_tokens: int = 30000  # 提交时原子预留，覆盖并发
    diagnosis_budget_reserve_cost_usd: float = 0.1
    # 证据时效（秒）：resume 时据此判断"哪些证据该重新取证"
    evidence_stale_ttl: dict = {
        "health": 180,
        "metrics": 300,
        "business": 900,
        "static": 3600,
    }
    trend_horizon_hours: float = 48.0      # 预测提前量：按当前增速预计多久触顶才提醒
    trend_retention_days: int = 7          # 指标样本保留天数
    trend_warn_ratio: float = 0.8          # 当前值达到阈值的该比例即预警（容量逼近）
    mysql_max_connections: int = 151       # MySQL max_connections（用于连接数预警）
    kafka_lag_warn: int = 5000             # Kafka 积压预警阈值（条）

    repo_root: str = _REPO_ROOT

    # 归档附件回传：下载链接对外基址；附件落盘目录（空=repo_root/.dev-stack/artifacts）
    public_base_url: str = "http://localhost"
    diagnosis_artifact_dir: str = ""
    # 附件下载 token 的有效期（小时）。能力 URL 必须有边界，过期后 404。
    artifact_ttl_hours: int = 168

    # 代码沙箱用的解释器（空=平台自身 python）。指到装了 numpy/matplotlib 的独立 venv 即可出图。
    sandbox_python: str = ""
    # 代码沙箱容器镜像（非空则优先在一次性容器里执行：--network none + 资源限额 + 用完即毁）
    sandbox_container_image: str = ""
    # 外部 MCP server 白名单（JSON 数组）。示例：
    # [{"name":"k8s","command":"{python}","args":["deploy/mcp/demo_k8s_server.py"],"description":"K8s 只读查询"}]
    mcp_servers: str = ""

    # 证据约束（防幻觉）：含具体数字但无任何取证工具调用时，在回答后附"未取证"提示
    diagnosis_evidence_guard: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"prod", "production"}

    @model_validator(mode="after")
    def _validate_security_settings(self) -> "Settings":
        """安全配置 fail-fast：宁可起不来，也不带着可预测密钥对外提供服务。"""
        # JWT：生产必须显式配置强随机值；开发缺失时生成随机密钥，绝不沿用历史默认值。
        if not self.jwt_secret or self.jwt_secret in _LEGACY_INSECURE_JWT_SECRETS:
            if self.is_production:
                raise ValueError(
                    "JWT_SECRET 未配置或仍为历史默认值（已泄露）。"
                    "生产环境必须设置为 ≥32 字符的强随机串，例如："
                    "python -c \"import secrets;print(secrets.token_urlsafe(48))\""
                )
            self.jwt_secret = secrets.token_urlsafe(48)
            warnings.warn(
                "JWT_SECRET 未配置，已生成本次进程内随机密钥（重启后登录态失效）。"
                "请在 .env 中设置固定的 JWT_SECRET。",
                RuntimeWarning,
                stacklevel=2,
            )
        elif self.is_production and len(self.jwt_secret) < _MIN_JWT_SECRET_LEN:
            raise ValueError(
                f"JWT_SECRET 长度不足 {_MIN_JWT_SECRET_LEN} 字符，生产环境拒绝启动。"
            )

        # 凭据加密：生产缺密钥等于把 SSH/DB/通知凭据明文落库，直接拒绝启动。
        if self.encryption_key:
            try:
                Fernet(self.encryption_key.encode())
            except Exception as exc:  # noqa: BLE001 - 统一转成可读的启动错误
                raise ValueError(
                    "ENCRYPTION_KEY 不是合法的 Fernet 密钥。"
                    "生成方式：python -c \"from cryptography.fernet import Fernet;"
                    "print(Fernet.generate_key().decode())\""
                ) from exc
        elif self.is_production:
            raise ValueError(
                "ENCRYPTION_KEY 未配置：连接器凭据会以明文落库，生产环境拒绝启动。"
            )
        return self


settings = Settings()
