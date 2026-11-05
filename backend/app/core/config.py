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
    app_description: str = "企智搭·代码平台后端服务"
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
    # 上下文窗口（token）。0 = 未知。
    #
    # 这个值决定对话历史什么时候被压缩、按什么比例保留 —— deepAgents 的摘要
    # 阈值按 `model.profile["max_input_tokens"]` 算，而 OpenAI 兼容端点挂上去的
    # 模型对象没有 profile。不设的话框架只能用固定的"170000 token 触发、
    # 保留 6 条消息"，对大窗口的模型来说太激进。
    llm_context_window: int = 0
    # 这个模型能不能看图。
    #
    # 走 .env 配置（没建数据库供应商配置）时，视觉能力只能在这里声明 ——
    # 对话里上传的图片是直接内联进消息发给模型的，模型不认就会报错。
    # 走数据库供应商配置时，用那条配置上的 vision_enabled 列。
    llm_vision_enabled: bool = False
    # agent 一轮里最多走多少个「模型 ↔ 工具」往返（**轮**，不是 token）。
    #
    # **这个值是怎么变成 LangGraph 的 recursion_limit 的**：一轮往返 = 两个
    # superstep（调一次模型 + 执行一次工具），再加收尾那一次模型调用。
    # 所以 ``recursion_limit ≈ 轮数 × 2 + 余量``（余量给审批恢复与收尾）。
    #
    # **为什么要有这个上限**：deepAgents 建图时把 ``recursion_limit`` 绑成了
    # 9999 —— 那等于**没有上限**。一个陷在循环里的 agent 能一口气跑几千轮，
    # token 和钱都是真的烧掉了。平台自己的置信度机制只管"反复填错参数"，
    # 管不到"反复做无用功但每次都成功"的循环。
    agent_max_steps: int = 60
    # 代码沙箱模式：docker（默认，有隔离）| local（开发用，无隔离）
    #
    # 默认 docker —— 平台执行的是 LLM 生成的代码，隔离是安全底线，不是可选项。
    # local 模式没有任何隔离（能读写本机任意文件、能联网），只给不装 docker 的
    # 本地开发用，而且要**显式**打开。
    sandbox_type: str = "docker"
    sandbox_image: str = "python:3.13-slim"
    sandbox_timeout: int = 60
    # 沙箱容器空闲多久回收（秒）
    #
    # 容器按会话**复用**，所以 agent 装的环境、设的变量、cd 的目录都留着 ——
    # 每次执行都新起容器的话这些全丢。
    #
    # 平台里没有"会话结束"这个事件（没人调 adelete_thread），所以只能靠空闲
    # 超时回收：这段时间没人用就把容器删掉。
    sandbox_idle_timeout: int = 1800
    # 同时存活的沙箱容器上限。超了就把最久没用的那个回收掉。
    sandbox_max_containers: int = 32

    # Agent 工作区
    workspace_root: str = "./workspace"
    agent_max_file_bytes: int = 200000
    agent_docker_mount: str = "/workspace"
    agent_checkpointer_db: str = "./agent_checkpoints.db"

    # ---- 工作目录（环境）----
    #
    # 允许作为 agent 工作目录的**根目录**，逗号分隔的绝对路径。
    # 只有这些根目录下的目录/仓库能被绑成环境。
    #
    # **默认空 = 一个都不许绑** —— 平台没有鉴权，白名单是唯一的闸门：
    # 留空意味着不配置就完全不能用这个功能，而不是"默认可用"。
    repo_roots: str = ""
    # 派生 worktree 的落点（绝对路径）。留空则用 ``<工作区根>/worktrees``。
    #
    # **为什么单独配**：worktree 是要挂进沙箱容器的目录，而容器运行时
    # （Colima 只共享 ``$HOME``、Docker Desktop 默认共享 ``/Users``）
    # 只能挂载被共享的路径。放在工作区根下面，默认就落在共享区里。
    #
    # 落点按仓库名分组：``<worktree_root>/<仓库名>/<分支>``
    worktree_root: str = ""

    # ---- 文件 ----
    # 单个上传文件的上限（字节）
    max_upload_bytes: int = 50 * 1024 * 1024
    # 单个文件下载的上限（字节）。agent 生成的产物可能很大，别让一次下载把内存吃光。
    max_download_bytes: int = 50 * 1024 * 1024
    # 能内联进对话、让模型直接"看见"的图片大小上限（字节）。
    # 超过这个尺寸就只给路径 —— base64 内联进消息会让 checkpoint 明显膨胀。
    max_inline_image_bytes: int = 5 * 1024 * 1024
    # 一轮对话最多带几个附件
    max_attachments_per_turn: int = 10
    # 上下文里「上传件」块最多列几个文件（按修改时间倒序取最近的）
    uploads_block_max_entries: int = 20

    # 联网搜索（web_search 能力用）：tavily | serper | bing
    web_search_provider: str = ""
    web_search_api_key: str = ""

    # ---- 能力治理 ----
    # 权限模式（默认值；会话级可以覆盖 —— 见 app/agents/permissions.py）：
    #   auto（按风险档位）| manual（每次改动都问）| accept_edits（文件编辑自动）
    #   | plan（只读）| bypass（全放行）
    # 老名字还认：strict→manual、balanced→auto、permissive→bypass
    capability_trust_mode: str = "auto"
    # **能不能用 bypass**（跳过人工确认，含破坏性操作）。
    #
    # 默认**关**。平台没有鉴权，而会话级权限模式接口是能改的 ——
    # 没有这道总闸，那条接口本身就是一条绕过链：知道 thread_id 就能把会话
    # 提到 bypass，之后删文件、跑破坏性命令都不再要人批。
    #
    # 关着的时候，选 bypass 会被**明确拒绝**（不静默忽略 —— 静默忽略会让人
    # 以为已经切过去了）。全局配置写成 bypass 也一样不生效（退回 auto）。
    capability_allow_bypass: bool = False
    # 能力没声明超时时的兜底（秒）
    capability_default_timeout: int = 30
    # 审批请求超时自动拒绝（秒）
    capability_approval_timeout: int = 300
    # 单会话并发执行上限
    capability_max_concurrency: int = 4
    # 等并发名额的最长时间（秒）—— 超了按"执行层繁忙"失败，可重试
    capability_concurrency_wait_seconds: float = 30.0
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

    # ---- 上下文模块 ----    # 上下文占模型窗口的比例。另一半留给模型输出、工具 schema 和
    # deepAgents 自己追加的提示词 —— 那部分我们控制不了，只能留地方。
    context_budget_ratio: float = 0.5
    # 一个 token 折算多少字符。英文约 4，中文约 1.5，取折中。
    context_chars_per_token: float = 4.0
    # 上下文预算的兜底字符数（窗口未知时用）
    context_fallback_budget_chars: int = 16000
    # 技能清单里最多列几个技能（0 = 不限制）。正文本来就按需读，
    # 这里限的是每轮都进提示词的那份 name + description 清单。
    context_max_skills_in_prompt: int = 20

    # ---- 记忆模块 ----
    # 长期记忆用的 RAG 集合名；留空 = 只支持会话内记忆
    #
    # **平台不自动建这个集合** —— RAG 集合的向量化配置（模型 + 维度）是建集合时
    # 锁死的，建错了只能重建。平台替用户建一个没配 embedding 的集合，等于把
    # 用户锁死在"纯关键词检索"上。要用长期记忆，先在 RAG 界面建好集合、选好
    # embedding 模型，再把集合名配到这里。
    memory_collection_name: str = ""
    # 是否每轮自动把记忆目录注入上下文
    memory_catalog_enabled: bool = True
    # 目录块的字符预算（每轮都付这笔钱，所以要小）
    memory_catalog_budget_chars: int = 1200
    # 目录最多几条
    memory_catalog_max_entries: int = 40
    # 单条摘要的长度上限
    memory_summary_max_chars: int = 200
    # 时间衰减半衰期（天）。只影响排序微调，不影响召回资格
    memory_decay_half_life_days: int = 30

    # ---------- 自主进化（复盘） ----------
    #
    # 复盘是**免审批**的：agent 自己改自己的记忆、技能与提示词规则。
    # 唯一的机械闸是「范围守卫」（app/agents/autonomy.py）——
    # 想改审批 / 权限 / 沙箱那一层的建议一律不应用。
    #
    # 这个总闸是给「不想让它自己改」的人留的。
    autonomy_enabled: bool = True
    # 同一个会话两次复盘的最小间隔（秒）。
    # 防的是"一次连着失败十次就复盘十次"—— 那是同一件事，一次就够。
    autonomy_cooldown_seconds: int = 300
    # 只看这么长时间内的失败（秒）。
    # 更早的失败多半是老账，翻出来只会让复盘跑题。
    autonomy_lookback_seconds: int = 3600
    # 复盘用哪个模型。留空 = 用主模型（犯错的同一个模型来复盘）。
    # 想省钱就填一个便宜模型名。
    autonomy_model: str = ""


@lru_cache
def get_settings() -> Settings:
    """带缓存的设置单例，避免重复解析环境变量。"""
    return Settings()


settings = get_settings()
