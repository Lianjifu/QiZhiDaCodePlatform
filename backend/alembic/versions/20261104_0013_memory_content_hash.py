"""长期记忆改用记忆层的内容哈希（归一化后）

Revision ID: 0013_memory_content_hash
Revises: 0012_approval_by_risk_tier
Create Date: 2026-11-04

改动：
- ``rag_document`` 里**记忆类**文档的 ``content_hash`` 重新按记忆层的
  口径算一遍（归一化空白与大小写后取 sha256 前 32 位）。

**为什么需要它**：长期记忆的查重与冲突检测原来靠"拉全量再比对"，
但 RAG 的列表接口单页只给 100 条 —— 集合里超过 100 条之后，
重复内容会被写进去，跟旧记忆矛盾的内容也拦不住。

现在查重改走 ``rag_document.content_hash`` 索引 —— 那条索引本来就有，
只是存的值跟记忆层算的不一样（一个是原始 sha256，一个是归一化后的），
两边对不上，索引就用不上。

**只动记忆类文档**（``meta.kind == 'memory'``）：普通 RAG 语料的
``content_hash`` 是 RAG 自己的口径（原始 sha256），改了它们的入库判重就废了。

**旧值回填**：迁移前那些行的 ``content_hash`` 是原始 sha256，
按新口径查不到 —— 所以这里逐行重算一遍。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_memory_content_hash"
down_revision: str | None = "0012_approval_by_risk_tier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _memory_hash(text: str) -> str:
    """记忆层的内容哈希：归一化空白与大小写后取 sha256 前 32 位。

    与 ``app/memory/models.py::content_hash`` 一致 —— 两边必须同口径，
    否则查重索引又对不上了。
    """
    normalized = " ".join((text or "").split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _raw_hash(text: str) -> str:
    """RAG 自己的口径：原始 sha256。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _rewrite(digest_of) -> None:
    """把记忆类文档的 ``content_hash`` 重算一遍。

    ``digest_of`` 决定用哪个口径 —— 升级用记忆层的，降级用 RAG 的。

    **只碰记忆类文档**：普通语料的哈希是 RAG 自己的判重依据，
    改了它，同一份语料会被当成两篇重新入库。
    """
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, content, meta FROM rag_document")
    ).fetchall()
    for row_id, content, meta in rows:
        if not _is_memory(meta):
            continue
        conn.execute(
            sa.text("UPDATE rag_document SET content_hash = :digest WHERE id = :id").bindparams(
                digest=digest_of(content or ""), id=row_id
            )
        )


def _is_memory(meta: object) -> bool:
    """这篇文档是记忆，还是普通 RAG 语料。

    ``meta`` 是 JSON —— 各数据库的 JSON 解析不一样，
    所以这里读出来在 Python 里判，不靠 SQL 的 JSON 函数。
    """
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except ValueError:
            return False
    if not isinstance(meta, dict):
        return False
    return str(meta.get("kind", "")) == "memory"


def upgrade() -> None:
    _rewrite(_memory_hash)


def downgrade() -> None:
    _rewrite(_raw_hash)
