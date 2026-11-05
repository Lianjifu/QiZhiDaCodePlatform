"""记忆管理的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta


class MemoryOut(BaseModel):
    """一条记忆。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="记忆 id，带存储前缀（s: / l:）")
    scope: str = Field(description="session（会话内）/ long_term（跨会话）")
    title: str = Field(default="", description="一句话摘要")
    content: str = Field(default="", description="正文")
    kind: str = Field(default="note", description="记忆类型")
    tags: list[str] = Field(default_factory=list, description="检索标签")
    strength: float = Field(default=0.5, description="重要性 0~1")
    use_count: int = Field(default=0, description="被召回过几次")
    source: str = Field(default="user", description="user / model-inferred")
    superseded: bool = Field(default=False, description="是否已被更新的记忆取代（不再参与召回）")
    source_thread_id: str = Field(default="", description="从哪个会话学到的")
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    relevance: float = Field(default=0.0, description="检索相关度（列表里一般为 0）")


class MemoryListOut(BaseModel):
    """列表响应模型。"""

    items: list[MemoryOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


class MemoryCatalogOut(BaseModel):
    """记忆目录（预览用）。"""

    content: str = Field(default="", description="渲染好的目录块")
    total_found: int = Field(default=0, description="一共找到多少条")
    truncated: bool = Field(default=False, description="是不是因为预算被截断了")
    entries: list[MemoryOut] = Field(default_factory=list)


class LongTermInitRequest(BaseModel):
    """初始化长期记忆集合。"""

    embedding_model: str = Field(
        default="", max_length=128, description="向量化模型名；留空 = 自动找（默认供应商优先）"
    )
    provider_id: int | None = Field(
        default=None, description="用哪条模型接入配置发向量化请求；留空 = 跟着模型名一起找"
    )


class LongTermStatusOut(BaseModel):
    """长期记忆的状态。"""

    model_config = ConfigDict(from_attributes=True)

    ready: bool = Field(default=False, description="能不能用")
    collection_name: str = Field(default="", description="集合名")
    collection_id: int | None = Field(default=None, description="集合 id")
    embedding_model: str = Field(default="", description="集合上的向量化模型；空 = 只有关键词索引")
    embedding_dim: int = Field(default=0, description="向量维度")
    keyword_only: bool = Field(default=False, description="是不是只有关键词索引")
    doc_count: int = Field(default=0, description="集合里已有多少篇文档（= 多少条记忆）")
    reason: str = Field(
        default="",
        description="没就绪的原因：not_configured（没配集合名）/ collection_missing（集合不存在）",
    )
    hint: str = Field(default="", description="给人看的下一步")
    provider_name: str = Field(default="", description="初始化时用的供应商配置名")
