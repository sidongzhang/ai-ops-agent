"""控制面配置（pydantic-settings）。复用仓库根目录的 .env（含 DEEPSEEK_API_KEY 等）。"""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(_REPO_ROOT, '.env'),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # 数据层：dev 用 SQLite，生产改成 postgresql+psycopg://... 即可
    database_url: str = f"sqlite:///{os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dev.db')}"

    # 鉴权
    jwt_secret: str = "dev-secret-change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # Agent 模型（默认 DeepSeek，OpenAI 兼容；硬核诊断可切 Claude）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    agent_model: str = "deepseek-chat"

    repo_root: str = _REPO_ROOT


settings = Settings()
