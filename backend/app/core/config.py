"""控制面配置（pydantic-settings）。复用仓库根目录的 .env（含 DEEPSEEK_API_KEY 等）。"""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # Agent 模型（默认 DeepSeek，OpenAI 兼容）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    agent_model: str = "deepseek-chat"
    # 高级模型：含 P0/崩溃/数据丢失等关键词时自动升档（也走 OpenAI 兼容接口，可指向任意厂商）
    advanced_agent_model: str = "deepseek-reasoner"
    advanced_agent_base_url: str = ""    # 空则复用 deepseek_base_url
    advanced_agent_api_key: str = ""     # 空则复用 deepseek_api_key

    # Embedding API（RAG 知识库用，OpenAI 兼容）
    # 空则复用 deepseek_api_key / deepseek_base_url
    embedding_model: str = "text-embedding-v2"
    embedding_api_key: str = ""
    embedding_base_url: str = ""

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
