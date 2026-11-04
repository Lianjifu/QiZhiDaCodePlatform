"""记忆存储的协议。

两种实现：

- ``SessionMemoryStore`` → ``memory_item`` 表（会话内）；
- ``LongTermMemoryStore`` → RAG 的专用集合（跨会话）。

上层 ``MemoryService`` 只认这个协议，换存储不动调用方 —— 与 RAG 层既有的
做法一致。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.memory.models import MemoryEntry


@runtime_checkable
class MemoryStore(Protocol):
    """一个记忆存储。"""

    scope: str

    async def add(self, entry: MemoryEntry) -> str:
        """写入一条记忆，返回它的 id。"""
        ...

    async def get(self, item_id: str) -> MemoryEntry | None:
        """取一条记忆。"""
        ...

    async def search(self, query: str, top_k: int, **kwargs) -> list[MemoryEntry]:
        """按查询词召回。

        返回的条目要带 ``relevance``（0~1 的相关度）。**召回资格不受权重影响**
        —— 只要没被删除的记忆都应该在候选池里，权重只参与排序。
        """
        ...

    async def list_all(self, limit: int = 100) -> list[MemoryEntry]:
        """列出全部（管理接口用）。"""
        ...

    async def find_by_hash(self, digest: str) -> MemoryEntry | None:
        """按内容哈希找一条（查重用）。

        **必须走数据库索引** —— 不能"拉全量再在 Python 里比对"。
        长期记忆有几百条时，全量拉取既慢又会被存储层的返回上限截断
        （RAG 的 ``list_documents`` 单页最多 100 条），
        查重与冲突检测会**双双失效**：重复内容写进去，
        跟旧记忆矛盾的内容也拦不住。
        """
        ...

    async def touch(self, item_id: str) -> None:
        """命中即重新激活：刷新使用次数与最后召回时间。"""
        ...

    async def supersede(self, item_id: str) -> bool:
        """标记一条记忆已被取代（不删除，只是不再参与召回）。"""
        ...

    async def forget(self, item_id: str) -> bool:
        """删除一条记忆。返回是否真的删了。"""
        ...


__all__ = ["MemoryStore"]
