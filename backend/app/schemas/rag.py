"""RAG 的请求/响应模型。

三个概念对应三张表：集合（collection）、文档（document）、分块（chunk）。
接口层只暴露集合、文档和检索结果 —— 分块是内部实现，不单独开放。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta

SearchMode = Literal["hybrid", "vector", "keyword"]
DocumentFormat = Literal["auto", "text", "markdown", "code"]


class RagCollectionCreate(BaseModel):
    """建集合。"""

    name: str = Field(min_length=1, max_length=64, description="集合名称，唯一")
    description: str = Field(default="", description="用途说明")
    scope: Literal["private", "shared"] = Field(default="private", description="可见范围")
    embedding_model: str = Field(
        default="",
        max_length=128,
        description="向量化模型名；留空表示只用关键词检索",
    )
    provider_id: int | None = Field(default=None, description="用哪条模型接入配置发向量化请求")
    chunk_size: int = Field(default=1000, ge=100, le=8000, description="分块大小（字符）")
    chunk_overlap: int = Field(default=150, ge=0, le=2000, description="分块重叠（字符）")
    meta: dict[str, Any] = Field(default_factory=dict, description="自定义元信息")


class RagCollectionOut(BaseModel):
    """集合详情。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    scope: str
    embedding_model: str
    embedding_dim: int
    provider_id: int | None
    chunk_size: int
    chunk_overlap: int
    doc_count: int
    chunk_count: int
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RagDocumentCreate(BaseModel):
    """入库一篇文档。"""

    title: str = Field(default="", max_length=255, description="标题，留空取正文第一行")
    content: str = Field(min_length=1, description="正文")
    source: str = Field(default="", max_length=512, description="来源：文件路径或 URL")
    format: DocumentFormat = Field(default="auto", description="正文格式；auto 表示自动判断")
    meta: dict[str, Any] = Field(default_factory=dict, description="自定义元信息")


class RagDocumentOut(BaseModel):
    """文档详情。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    collection_id: int
    title: str
    source: str
    format: str
    content_hash: str
    chunk_count: int
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RagDocumentListOut(BaseModel):
    """文档列表。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[RagDocumentOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


class RagIngestOut(BaseModel):
    """入库结果。"""

    document: RagDocumentOut
    chunk_count: int = Field(description="本次生成的分块数")
    skipped: bool = Field(default=False, description="内容重复，已跳过重新入库")
    keyword_only: bool = Field(default=False, description="集合未启用向量化，只建了关键词索引")


class RagSearchRequest(BaseModel):
    """检索请求。"""

    query: str = Field(min_length=1, description="检索词")
    collection_id: int = Field(description="在哪个集合里找")
    top_k: int = Field(default=8, ge=1, le=50, description="返回条数")
    mode: SearchMode = Field(
        default="hybrid", description="hybrid（向量+关键词）/ vector / keyword"
    )
    min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="最低分，低于它丢掉")
    filters: dict[str, Any] = Field(
        default_factory=dict, description="按分块 meta 过滤，键值都要相等"
    )


class RagSearchHit(BaseModel):
    """一条命中。"""

    chunk_id: int
    document_id: int
    seq: int
    title: str = ""
    source: str = ""
    content: str
    score: float = Field(description="融合后的总分，0-1")
    vector_score: float | None = Field(default=None, description="向量一路的分数")
    keyword_score: float | None = Field(default=None, description="关键词一路的分数")
    rank: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)


class RagSearchOut(BaseModel):
    """检索结果。"""

    hits: list[RagSearchHit] = Field(default_factory=list)
    mode: str = "hybrid"
    took_ms: int = 0
    total: int = 0
    degraded: str = Field(default="", description="降级说明，例如退化为纯关键词检索")


__all__ = [
    "RagCollectionCreate",
    "RagCollectionOut",
    "RagDocumentCreate",
    "RagDocumentListOut",
    "RagDocumentOut",
    "RagIngestOut",
    "RagSearchHit",
    "RagSearchOut",
    "RagSearchRequest",
]
