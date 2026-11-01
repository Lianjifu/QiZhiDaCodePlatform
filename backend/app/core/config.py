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
    # 开发期自动建表；正式用 Alembic 迁移时置 False（否则两者会打架）
    db_auto_create_tables: bool = True

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
    # 代码沙箱模式：docker（默认，有隔离）| local（开发用，无隔离）
    #
    # 默认 docker —— 平台执行的是 LLM 生成的代码，隔离是安全底线，不是可选项。
    # local 模式没有任何隔离（能读写本机任意文件、能联网），只给不装 docker 的
    # 本地开发用，而且要**显式**打开。
    sandbox_type: str = "docker"
    sandbox_image: str = "python:3.13-slim"
    sandbox_timeout: int = 60

    # Agent 工作区
    workspace_root: str = "./workspace"
    agent_max_file_bytes: int = 200000
    agent_docker_mount: str = "/workspace"
    agent_checkpointer_db: str = "./agent_checkpoints.db"

    # 联网搜索（web_search 能力用）：tavily | serper | bing
    web_search_provider: str = ""
    web_search_api_key: str = ""

    # ---- 能力治理 ----
    # 信任模式：strict（按声明逐条审批）| balanced（读写放行、执行/联网需确认）
    #           | permissive（只有破坏性操作需确认）
    capability_trust_mode: str = "balanced"
    # 能力没声明超时时的兜底（秒）
    capability_default_timeout: int = 30
    # 审批请求超时自动拒绝（秒）
    capability_approval_timeout: int = 300
    # 单会话并发执行上限
    capability_max_concurrency: int = 4
    # 能力没声明重试策略时的兜底
    capability_retry_max_attempts: int = 1
    # 额外的脱敏正则（逗号分隔）
    capability_redact_patterns: str = ""
    # 内部域名后缀（SSRF 判定用，逗号分隔）
    capability_internal_domains: str = "localhost,.internal,.local"
    # 熔断：按能力统计滑动窗口内的错误率，坏了就快速失败
    capability_breaker_window: int = 60
    capability_breaker_min_samples: int = 5
    capability_breaker_error_rate: float = 0.5
    capability_breaker_cooldown: int = 30
    # 慢调用也算"劣化"的阈值（毫秒）
    capability_breaker_latency_ms: int = 5000
    # 任务置信度：低于阈值就阻塞转人工
    capability_confidence_block_threshold: float = 40.0
    # 同一能力连续多少次格式类错误就阻塞
    capability_max_format_retries: int = 3

    # API Key 加密主密钥（Fernet），用 scripts/gen_encryption_key.py 生成
    api_key_encryption_key: str = ""
    # Azure OpenAI 专用
    azure_openai_api_version: str = "2024-10-21"

    # ---- RAG ----
    # 分块参数（字符数，不是 token 数）
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 150
    # 单篇文档上限（字符）
    rag_max_document_chars: int = 200000
    # 检索默认返回条数
    rag_top_k: int = 8
    # 融合权重：向量一路、关键词一路
    rag_vector_weight: float = 1.0
    rag_keyword_weight: float = 1.0
    # 向量化环境变量兜底（正式配置走「模型接入配置」表，密钥才有加密存储）
    rag_embedding_model: str = ""
    rag_embedding_base_url: str = ""
    rag_embedding_api_key: str = ""
    rag_embedding_dim: int = 0
    rag_embedding_timeout: int = 60
    rag_embedding_batch_size: int = 32


@lru_cache
def get_settings() -> Settings:
    """带缓存的设置单例，避免重复解析环境变量。"""
    return Settings()


settings = get_settings()
