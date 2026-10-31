"""应用配置。

所有配置来自环境变量或同目录的 .env 文件，字段统一小写，便于 `settings.xxx` 直接访问。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    app_name: str = "QiZhiDa Code Platform API"
    app_version: str = "0.1.0"
    app_description: str = "启智达代码平台后端服务"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./app.db"
    database_sync_url: str = "sqlite:///./app.db"
    db_echo: bool = False

    # CORS：环境变量必须是 JSON 数组，例如 CORS_ORIGINS=["http://localhost:3000"]
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    # 日志
    log_level: str = "INFO"

    # LLM / Agent（LangChain）
    llm_provider: str = "openai"  # openai | anthropic | ollama
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = ""  # OpenAI 兼容网关：DeepSeek / 通义 / Kimi / vLLM 等
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    agent_max_steps: int = 20
    sandbox_type: str = "local"  # local | docker
    sandbox_image: str = "python:3.13-slim"
    sandbox_timeout: int = 60

    # Agent 工作区
    workspace_root: str = "./workspace"
    agent_max_file_bytes: int = 200000
    agent_docker_mount: str = "/workspace"
    agent_checkpointer_db: str = "./agent_checkpoints.db"

    # API Key 加密主密钥（Fernet），用 scripts/gen_encryption_key.py 生成
    api_key_encryption_key: str = ""
    # Azure OpenAI 专用
    azure_openai_api_version: str = "2024-10-21"


@lru_cache
def get_settings() -> Settings:
    """带缓存的设置单例，避免重复解析环境变量。"""
    return Settings()


settings = get_settings()
