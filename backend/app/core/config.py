"""控制面配置（pydantic-settings）。复用仓库根目录的 .env（含 DEEPSEEK_API_KEY 等）。"""
import os

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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(_REPO_ROOT, '.env'),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # 数据层：dev 用 SQLite，生产改成 postgresql+psycopg://... 即可
    database_url: str = f"sqlite:///{os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'dev.db')}"

    # 鉴权
    jwt_secret: str = "dev-secret-change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # Agent 模型（OpenAI 兼容）。推荐新配置：LLM_MODE=api/local。
    llm_mode: str = "api"  # api: 远程 API；local: 本地部署（Ollama/vLLM/LM Studio 等）
    llm_api_base_url: str = ""   # 空则兼容复用 deepseek_base_url
    llm_api_key: str = ""        # 空则兼容复用 deepseek_api_key
    llm_api_model: str = ""      # 空则兼容复用 agent_model
    llm_local_base_url: str = "http://localhost:11434/v1"
    llm_local_api_key: str = "ollama"
    llm_local_model: str = "qwen2.5:0.5b"
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

    # 单次诊断的资源上限，防止 agent 陷入循环无限烧 token。
    # 每多一轮 loop 都要重发整段上下文，所以这三项同时也是成本闸门。
    diagnosis_request_limit: int = 12        # 模型请求轮数
    diagnosis_tool_calls_limit: int = 24     # 工具调用次数
    diagnosis_token_limit: int = 100000      # 累计 token（含每轮重复计入的上下文）

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

    # Langfuse 可观测性（留空则跳过追踪）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # 飞书机器人（Webhook 接收 + 主动推送共用同一个自建应用）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_alert_chat_id: str = ""   # 默认告警群，无 chat_id 时回退到此

    # 凭据字段加密（Fernet AES-128）；空字符串 = dev 模式跳过加密
    encryption_key: str = ""

    # Celery 定时巡检
    celery_broker_url: str = "redis://localhost:6380/0"
    celery_result_backend: str = "redis://localhost:6380/1"
    health_check_interval: int = 60        # 巡检周期（秒）
    alert_cooldown_seconds: int = 3600     # 同一系统两次告警最小间隔（秒）

    repo_root: str = _REPO_ROOT


settings = Settings()
