"""记忆模块：关于用户与项目的经验。

两层结构，不设第三层：

| 层 | 范围 | 存储 |
| --- | --- | --- |
| 短期 | 会话内（随 thread 存活） | ``memory_item`` 表 |
| 长期 | 跨会话 | RAG 的专用集合 |

**记忆 ≠ 语料**：普通文档走 RAG 的集合与检索接口，不归这个模块管。

模块地图：

| 模块 | 管什么 |
| --- | --- |
| `models.py` | 领域结构 ``MemoryEntry``、scope / kind 常量、评分口径 |
| `store.py` | ``MemoryStore`` 协议 —— 两种存储的统一接口 |
| `session_store.py` | 短期记忆的存储（``memory_item`` 表，关键词检索） |
| `long_term_store.py` | 长期记忆的存储（RAG 集合，混合检索） |
| `catalog.py` | 目录组装、预算装箱、MMR 去冗余 |
| `service.py` | 统一入口：按 scope 分发、打分、写入校验 |

设计细节见 ``docs/plans/memory-module.md``。
"""

from app.memory.models import (
    KIND_CONVENTION,
    KIND_DECISION,
    KIND_FACT,
    KIND_NOTE,
    KIND_PREFERENCE,
    KIND_PROFILE,
    MEMORY_KINDS,
    SCOPE_LONG_TERM,
    SCOPE_SESSION,
    SOURCE_MODEL,
    SOURCE_USER,
    MemoryEntry,
)
from app.memory.service import MemoryService
from app.memory.store import MemoryStore

__all__ = [
    "KIND_CONVENTION",
    "KIND_DECISION",
    "KIND_FACT",
    "KIND_NOTE",
    "KIND_PREFERENCE",
    "KIND_PROFILE",
    "MEMORY_KINDS",
    "SCOPE_LONG_TERM",
    "SCOPE_SESSION",
    "SOURCE_MODEL",
    "SOURCE_USER",
    "MemoryEntry",
    "MemoryService",
    "MemoryStore",
]
