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


@lru_cache
def get_settings() -> Settings:
    """带缓存的设置单例，避免重复解析环境变量。"""
    return Settings()


settings = get_settings()
