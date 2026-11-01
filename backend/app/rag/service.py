"""RAG 业务服务：集合 / 文档 / 检索。

RAG 层是**独立的一层**，不依赖能力层、也不依赖记忆模块 —— 记忆模块只是它
的一个消费者。这样 RAG 可以先单独用起来（接口直接调、自己灌语料），
记忆模块接入时不用反过来改 RAG。

三件事的边界很清楚：

- **集合**决定"在哪儿找"和"怎么算相似度"（向量化配置锁死在集合上）；
- **文档**是原文，按内容哈希去重；
- **分块**是检索单元，入库时生成并向量化。

一条硬规则：**换了向量化模型就得重建集合**。不同模型的向量不在同一个空间里，
混着比出来的相似度是噪声。所以集合上记死模型名和维度，入库时校验维度，
不一致直接报错。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.providers import resolve_preset
from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.exceptions import AppError, ConflictError, NotFoundError, ValidationError
from app.models.llm_provider import LLMProvider
from app.rag.chunking import chunk_document, detect_format
from app.rag.embedding import (
    EmbeddingProvider,
    EmbeddingTarget,
    NullEmbeddingProvider,
    build_provider,
    ensure_dimensions,
    normalize,
    probe_dimension,
    target_from_settings,
)
from app.rag.models import RagChunk, RagCollection, RagDocument
from app.rag.retriever import Retriever, SearchRequest, SearchResult
from app.rag.stores.sql import SqlVectorStore
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """一次入库的结果。"""

    document: RagDocument
    chunk_count: int
    # 内容没变，已存在，跳过重新分块
    skipped: bool = False
    # 没有配置向量化模型，本次只做了关键词索引
    keyword_only: bool = False


class RagService:
    """RAG 的业务入口。"""

    def __init__(self, session: AsyncSession, store: VectorStore | None = None) -> None:
        self.session = session
        self.store = store or SqlVectorStore(session)

    # ---------- 集合 ----------

    async def create_collection(self, payload: Any) -> RagCollection:
        """建集合。

        配了向量化模型的话，建的时候就探一次维度 —— 模型名写错、密钥不对、
        端点不通，都在这一步暴露出来，而不是入库之后才发现。
        """
        name = (payload.name or "").strip()
        if not name:
            raise ValidationError("集合名称不能为空")
        if await self.get_collection_by_name(name) is not None:
            raise ConflictError(f"集合 {name} 已存在")

        model = (getattr(payload, "embedding_model", "") or "").strip()
        provider_id = getattr(payload, "provider_id", None)
        if provider_id is not None:
            await self._get_provider(provider_id)

        dim = 0
        if model:
            target = await self._build_target(model, provider_id)
            if target is None:
                raise ValidationError(
                    f"配置了向量化模型 {model}，但找不到可用的模型接入配置。"
                    "请先在「模型接入配置」里补一条，或去掉 embedding_model 只用关键词检索"
                )
            try:
                dim = await probe_dimension(build_provider(target))
            except AppError as exc:
                raise ValidationError(f"向量化模型不可用：{exc.message}") from exc

        collection = RagCollection(
            name=name,
            description=getattr(payload, "description", "") or "",
            scope=getattr(payload, "scope", "private") or "private",
            embedding_model=model,
            embedding_dim=dim,
            provider_id=provider_id,
            chunk_size=int(getattr(payload, "chunk_size", 0) or settings.rag_chunk_size),
            chunk_overlap=int(getattr(payload, "chunk_overlap", 0) or settings.rag_chunk_overlap),
            meta=dict(getattr(payload, "meta", None) or {}),
        )
        self.session.add(collection)
        try:
            await self.session.commit()
        except IntegrityError as exc:  # 并发建同名集合
            await self.session.rollback()
            raise ConflictError(f"集合 {name} 已存在") from exc
        await self.session.refresh(collection)
        return collection

    async def list_collections(self) -> list[RagCollection]:
        stmt = select(RagCollection).order_by(RagCollection.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_collection(self, collection_id: int) -> RagCollection:
        collection = await self.session.get(RagCollection, collection_id)
        if collection is None:
            raise NotFoundError(f"集合 {collection_id} 不存在")
        return collection

    async def get_collection_by_name(self, name: str) -> RagCollection | None:
        stmt = select(RagCollection).where(RagCollection.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_collection(self, collection_id: int) -> None:
        """删集合，连同文档和分块一起删。"""
        collection = await self.get_collection(collection_id)
        await self.store.delete_collection(collection_id)
        stmt = delete(RagDocument).where(RagDocument.collection_id == collection_id)
        await self.session.execute(stmt)
        await self.session.delete(collection)
        await self.session.commit()

    # ---------- 文档 ----------

    async def ingest_document(self, collection_id: int, payload: Any) -> IngestResult:
        """入库一篇文档：分块 → 向量化 → 落库。"""
        collection = await self.get_collection(collection_id)

        content = getattr(payload, "content", "") or ""
        if not content.strip():
            raise ValidationError("文档内容不能为空")
        limit = settings.rag_max_document_chars
        if len(content) > limit:
            raise ValidationError(f"文档太长（{len(content)} 字），上限 {limit} 字，请切分后再入库")

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = await self._find_by_hash(collection_id, content_hash)
        if existing is not None:
            # 内容一样，不重复切、不重复向量化
            return IngestResult(
                document=existing,
                chunk_count=existing.chunk_count,
                skipped=True,
                keyword_only=not collection.embedding_model,
            )

        source = (getattr(payload, "source", "") or "").strip()
        requested_format = (getattr(payload, "format", "auto") or "auto").strip()
        # auto 在这里定下来：存的是"这份文档到底是什么格式"，不是"用户说了什么"
        resolved_format = (
            detect_format(content, filename=source)
            if requested_format == "auto"
            else requested_format
        )

        chunks = chunk_document(
            content,
            fmt=resolved_format,
            max_chars=collection.chunk_size,
            overlap=collection.chunk_overlap,
            filename=source,
        )
        if not chunks:
            raise ValidationError("文档切分后没有内容")

        vectors: list[list[float]] = []
        if collection.embedding_model:
            target = await self._build_target(
                collection.embedding_model, collection.provider_id, collection.embedding_dim
            )
            if target is None:
                raise ValidationError(
                    f"集合 {collection.name} 配置了向量化模型 {collection.embedding_model}，"
                    "但找不到可用的模型接入配置，无法入库"
                )
            vectors = await build_provider(target).embed([c.content for c in chunks])
            ensure_dimensions(vectors, collection.embedding_dim)

        document = RagDocument(
            collection_id=collection_id,
            title=(getattr(payload, "title", "") or "").strip() or _default_title(content),
            source=source,
            format=resolved_format,
            content=content,
            content_hash=content_hash,
            chunk_count=len(chunks),
            meta=dict(getattr(payload, "meta", None) or {}),
        )
        self.session.add(document)
        await self.session.flush()

        rows: list[RagChunk] = []
        for index, chunk in enumerate(chunks):
            vector = vectors[index] if index < len(vectors) else []
            rows.append(
                RagChunk(
                    collection_id=collection_id,
                    document_id=document.id,
                    seq=chunk.seq,
                    content=chunk.content,
                    embedding=normalize(list(vector)) if vector else None,
                    dim=len(vector),
                    token_count=chunk.token_count,
                    meta=dict(chunk.meta),
                )
            )
        await self.store.add_chunks(rows)

        collection.doc_count += 1
        collection.chunk_count += len(rows)
        await self.session.commit()
        await self.session.refresh(document)

        return IngestResult(
            document=document,
            chunk_count=len(rows),
            skipped=False,
            keyword_only=not collection.embedding_model,
        )

    async def list_documents(
        self, collection_id: int, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[RagDocument], int]:
        await self.get_collection(collection_id)
        page = max(1, page)
        page_size = min(max(1, page_size), 100)

        count_stmt = (
            select(func.count())
            .select_from(RagDocument)
            .where(RagDocument.collection_id == collection_id)
        )
        total = int((await self.session.execute(count_stmt)).scalar_one())

        stmt = (
            select(RagDocument)
            .where(RagDocument.collection_id == collection_id)
            .order_by(RagDocument.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_document(self, document_id: int) -> RagDocument:
        document = await self.session.get(RagDocument, document_id)
        if document is None:
            raise NotFoundError(f"文档 {document_id} 不存在")
        return document

    async def delete_document(self, document_id: int) -> None:
        """删文档，连同分块一起删，并回退集合计数。"""
        document = await self.get_document(document_id)
        removed = await self.store.delete_document(document_id)
        collection = await self.get_collection(document.collection_id)
        collection.doc_count = max(0, collection.doc_count - 1)
        collection.chunk_count = max(0, collection.chunk_count - removed)
        await self.session.delete(document)
        await self.session.commit()

    # ---------- 检索 ----------

    async def search(self, payload: Any) -> SearchResult:
        """检索。

        集合没启用向量化 → 纯关键词。启用了但供应商配不出来 → 也降级成关键词，
        并在 ``degraded`` 里说明原因（不因为配置缺失就让检索整个不可用）。
        """
        collection = await self.get_collection(int(payload.collection_id))
        embedder = await self._embedder_for(collection)

        retriever = Retriever(
            self.store,
            embedder,
            vector_weight=settings.rag_vector_weight,
            keyword_weight=settings.rag_keyword_weight,
        )
        request = SearchRequest(
            query=payload.query,
            collection_id=collection.id,
            top_k=int(payload.top_k or settings.rag_top_k),
            mode=getattr(payload, "mode", "hybrid") or "hybrid",
            min_score=float(getattr(payload, "min_score", 0.0) or 0.0),
            filters=dict(getattr(payload, "filters", None) or {}),
        )
        result = await retriever.search(request)
        await self._attach_documents(result)
        return result

    async def _attach_documents(self, result: SearchResult) -> None:
        """把文档标题和来源回填到命中结果上。"""
        ids = {hit.chunk.document_id for hit in result.hits if hit.chunk.document_id}
        if not ids:
            return
        stmt = select(RagDocument).where(RagDocument.id.in_(ids))
        documents = {doc.id: doc for doc in (await self.session.execute(stmt)).scalars().all()}
        for hit in result.hits:
            document = documents.get(hit.chunk.document_id)
            if document is not None:
                hit.title = document.title
                hit.source = document.source

    # ---------- 向量化配置 ----------

    async def _embedder_for(self, collection: RagCollection) -> EmbeddingProvider:
        """给集合配一个向量化器。

        配不出来就返回"不向量化"——检索退化成关键词，而不是报错。
        """
        if not collection.embedding_model:
            return NullEmbeddingProvider()
        target = await self._build_target(
            collection.embedding_model, collection.provider_id, collection.embedding_dim
        )
        if target is None:
            logger.warning(
                "集合 %s 配置了向量化模型 %s，但找不到可用的模型接入配置，退化为关键词检索",
                collection.name,
                collection.embedding_model,
            )
            return NullEmbeddingProvider()
        return build_provider(target)

    async def _build_target(
        self, model: str, provider_id: int | None, dim: int = 0
    ) -> EmbeddingTarget | None:
        """组装向量化目标：模型名 + 端点 + 密钥。"""
        row: LLMProvider | None = None
        if provider_id is not None:
            row = await self._get_provider(provider_id)
        if row is None:
            row = await self._default_provider()

        if row is not None:
            preset = resolve_preset(row.provider)
            return EmbeddingTarget(
                model=model,
                base_url=row.base_url or preset.default_base_url or "",
                api_key=_safe_decrypt(row.api_key_encrypted),
                provider=row.provider,
                dim=dim,
                timeout=row.timeout or settings.rag_embedding_timeout,
                batch_size=settings.rag_embedding_batch_size,
            )

        # 没有数据库配置就退到环境变量（本地开发）
        env = target_from_settings()
        if env is None:
            return None
        env.model = model
        env.dim = dim
        return env

    async def _get_provider(self, provider_id: int) -> LLMProvider:
        row = await self.session.get(LLMProvider, provider_id)
        if row is None:
            raise NotFoundError(f"模型接入配置 {provider_id} 不存在")
        return row

    async def _default_provider(self) -> LLMProvider | None:
        stmt = (
            select(LLMProvider)
            .where(LLMProvider.is_default.is_(True), LLMProvider.is_active.is_(True))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_by_hash(self, collection_id: int, content_hash: str) -> RagDocument | None:
        stmt = select(RagDocument).where(
            RagDocument.collection_id == collection_id,
            RagDocument.content_hash == content_hash,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


def _safe_decrypt(cipher: str | None) -> str:
    """解密失败不致命 —— 本地端点本来就不要密钥。"""
    try:
        return decrypt_secret(cipher) or ""
    except AppError:
        return ""


def _default_title(content: str) -> str:
    """没给标题就取第一行，截断一下。"""
    first = content.strip().splitlines()[0] if content.strip() else "未命名文档"
    return first[:60]


__all__ = ["IngestResult", "RagService"]
