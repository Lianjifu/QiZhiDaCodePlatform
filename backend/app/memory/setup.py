"""长期记忆的一键初始化。

**为什么需要它**：长期记忆要一个 RAG 集合，而集合的向量化配置（模型 + 维度）
是**建集合时锁死**的 —— 建错了只能重建。所以以前只能让人手工去 RAG 界面建
集合、再把集合名配进环境变量。忘了就等于长期记忆一直没开，而且**没人知道**
（写长期失败会降级成会话内，从外面看着像正常的）。

这里把这件事做成一个动作：按当前供应商的 embedding 配置建好集合，名字取
``MEMORY_COLLECTION_NAME``（默认 ``agent-memory``）。

**没配 embedding 模型就建纯关键词的集合** —— 那不是最优，但能用：关键词
检索对"记事实"这种场景够用，而且集合是空的（还没有记忆），想要向量检索
的话删掉重建没有代价。已经放了记忆之后就不建议改了 —— 重建会把记忆一起
没掉，所以那一步要人自己决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.llm_provider import LLMProvider
from app.rag.models import RagCollection

# 状态里的 reason 取值
REASON_NOT_CONFIGURED = "not_configured"
REASON_COLLECTION_MISSING = "collection_missing"


@dataclass
class LongTermStatus:
    """长期记忆现在是什么状态。"""

    ready: bool = False
    collection_name: str = ""
    collection_id: int | None = None
    embedding_model: str = ""
    embedding_dim: int = 0
    keyword_only: bool = False
    doc_count: int = 0
    # 没就绪的原因（"" = 就绪）
    reason: str = ""
    # 给人的一句可操作的话
    hint: str = ""
    # 初始化时用了哪个供应商配置
    provider_id: int | None = None
    provider_name: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class LongTermSetup:
    """长期记忆集合的建/查。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- 查 ----------

    async def status(self) -> LongTermStatus:
        """长期记忆现在能不能用，为什么不能。"""
        name = settings.memory_collection_name.strip()
        if not name:
            return LongTermStatus(
                reason=REASON_NOT_CONFIGURED,
                hint=(
                    "没有配置 MEMORY_COLLECTION_NAME，平台不知道长期记忆该用哪个集合。"
                    "设上它（或用默认的 agent-memory）再初始化。"
                ),
            )

        collection = await self._collection(name)
        if collection is None:
            return LongTermStatus(
                collection_name=name,
                reason=REASON_COLLECTION_MISSING,
                hint=(
                    f"集合 {name!r} 还不存在。调用一次初始化就会按当前供应商的 "
                    "embedding 配置把它建好。"
                ),
            )
        return self._status_of(collection)

    def _status_of(self, collection: RagCollection) -> LongTermStatus:
        keyword_only = not collection.embedding_model
        hint = ""
        if keyword_only:
            hint = (
                "这个集合只建了关键词索引（没有可用的 embedding 模型）。"
                "召回能用，但语义检索用不上；等配好了 embedding 模型，"
                "空集合可以直接删掉重建。"
            )
        return LongTermStatus(
            ready=True,
            collection_name=collection.name,
            collection_id=collection.id,
            embedding_model=collection.embedding_model,
            embedding_dim=collection.embedding_dim,
            keyword_only=keyword_only,
            doc_count=collection.doc_count,
            hint=hint,
            provider_id=collection.provider_id,
        )

    async def _collection(self, name: str) -> RagCollection | None:
        stmt = select(RagCollection).where(RagCollection.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- 建 ----------

    async def initialize(
        self,
        *,
        embedding_model: str = "",
        provider_id: int | None = None,
    ) -> tuple[LongTermStatus, bool]:
        """把长期记忆集合建好。

        幂等：集合已经在就直接返回现状，不会重建（重建会丢记忆）。

        Args:
            embedding_model: 用哪个向量化模型；不给就自动找（默认供应商优先）。
            provider_id: 用哪条模型接入配置发向量化请求；不给就跟着
                ``embedding_model`` 一起自动找。

        Returns:
            ``(状态, 是不是这次新建的)``。

        Raises:
            ValidationError: 没配集合名，或配置的 embedding 模型不可用
                （模型名写错、密钥不对、端点不通 —— 建的时候就要暴露出来）。
        """
        name = settings.memory_collection_name.strip()
        if not name:
            raise ValidationError(
                "没有配置 MEMORY_COLLECTION_NAME，不知道长期记忆该用哪个集合。"
                "设上它再初始化。"
            )

        existing = await self._collection(name)
        if existing is not None:
            return self._status_of(existing), False

        model, resolved_provider = await self._resolve_embedding(embedding_model, provider_id)
        provider_name = resolved_provider.name if resolved_provider is not None else ""

        from app.rag.service import RagService
        from app.schemas.rag import RagCollectionCreate

        service = RagService(self.session)
        collection = await service.create_collection(
            RagCollectionCreate(
                name=name,
                description="长期记忆（跨会话）",
                scope="private",
                embedding_model=model,
                provider_id=resolved_provider.id if resolved_provider is not None else None,
            )
        )
        status = self._status_of(collection)
        status.provider_name = provider_name
        return status, True

    async def _resolve_embedding(
        self, embedding_model: str, provider_id: int | None
    ) -> tuple[str, LLMProvider | None]:
        """定下用哪个向量化模型。

        优先级：显式指定 → 默认供应商 → 任意一条配了 embedding 的供应商 →
        环境变量兜底 → 都没有就纯关键词。
        """
        model = (embedding_model or "").strip()
        if model:
            row = await self._provider(provider_id)
            return model, row

        stmt = (
            select(LLMProvider)
            .where(LLMProvider.is_active.is_(True))
            .where(LLMProvider.embedding_model != "")
            .order_by(LLMProvider.is_default.desc(), LLMProvider.id)
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row.embedding_model, row

        env_model = settings.rag_embedding_model.strip()
        if env_model:
            return env_model, None

        return "", None

    async def _provider(self, provider_id: int | None) -> LLMProvider | None:
        """取一条供应商配置；不给就取默认那条。"""
        if provider_id is not None:
            return await self.session.get(LLMProvider, provider_id)
        stmt = (
            select(LLMProvider)
            .where(LLMProvider.is_default.is_(True), LLMProvider.is_active.is_(True))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "REASON_COLLECTION_MISSING",
    "REASON_NOT_CONFIGURED",
    "LongTermSetup",
    "LongTermStatus",
]
