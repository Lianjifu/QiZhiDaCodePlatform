"""RAG 语料工具测试：rag_search / rag_read。

这两个能力是给 agent 用的，所以测的是**模型看到的东西**：
命中给不给标题、给不给来源、集合名写错了怎么办。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.errors import CapabilityError
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.builtin.rag import make_rag_read, make_rag_search
from app.core.config import settings
from app.rag.service import RagService
from app.schemas.rag import RagCollectionCreate, RagDocumentCreate


@pytest.fixture
def ctx(tmp_path, session: AsyncSession) -> ExecutionContext:
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    return context


async def _seed(session: AsyncSession, name: str, content: str, title: str = "数据库配置"):
    """建一个集合并塞一篇文档。"""
    service = RagService(session)
    collection = await service.create_collection(RagCollectionCreate(name=name))
    await service.ingest_document(
        collection.id, RagDocumentCreate(content=content, title=title, source="docs/db.md")
    )
    return collection


# ---------- 检索 ----------


@pytest.mark.asyncio
async def test_rag_search_命中并给出标题与来源(ctx):
    await _seed(ctx.session, "平台文档", "数据库连接通过 DATABASE_URL 配置，默认是 postgres。")
    tool = make_rag_search(ctx)

    result = await tool(query="DATABASE_URL")

    assert result["items"]
    first = result["items"][0]
    assert first["title"] == "数据库配置"
    assert first["source"] == "docs/db.md"
    assert first["collection"] == "平台文档"
    assert "DATABASE_URL" in first["snippet"]
    # 给模型看的那段也要有标题
    assert "数据库配置" in result["content"]


@pytest.mark.asyncio
async def test_rag_search_指定集合只搜那一个(ctx):
    await _seed(ctx.session, "甲", "关键词 甲甲甲")
    await _seed(ctx.session, "乙", "关键词 乙乙乙")
    tool = make_rag_search(ctx)

    result = await tool(query="甲甲甲", collection="甲")

    assert result["collections"] == ["甲"]
    assert {item["collection"] for item in result["items"]} == {"甲"}


@pytest.mark.asyncio
async def test_rag_search_不指定集合就扫全部(ctx):
    await _seed(ctx.session, "甲", "关键词 甲甲甲")
    await _seed(ctx.session, "乙", "关键词 乙乙乙")
    tool = make_rag_search(ctx)

    result = await tool(query="甲甲甲")

    assert set(result["collections"]) == {"甲", "乙"}


@pytest.mark.asyncio
async def test_rag_search_扫全部时排除记忆集合(ctx, monkeypatch):
    """记忆集合有自己的召回语义（被取代的不召回），扫全部时不能把它混进来。"""
    monkeypatch.setattr(settings, "memory_collection_name", "agent-memory")
    await _seed(ctx.session, "agent-memory", "记忆内容 关键词 甲甲甲")
    await _seed(ctx.session, "平台文档", "语料内容 关键词 甲甲甲")
    tool = make_rag_search(ctx)

    result = await tool(query="甲甲甲")

    assert "agent-memory" not in result["collections"]
    assert {item["collection"] for item in result["items"]} == {"平台文档"}


@pytest.mark.asyncio
async def test_rag_search_点名记忆集合时仍然能搜(ctx, monkeypatch):
    monkeypatch.setattr(settings, "memory_collection_name", "agent-memory")
    await _seed(ctx.session, "agent-memory", "记忆内容 关键词 甲甲甲")
    tool = make_rag_search(ctx)

    result = await tool(query="甲甲甲", collection="agent-memory")

    assert result["collections"] == ["agent-memory"]
    assert result["items"]


@pytest.mark.asyncio
async def test_rag_search_集合名写错时报错并列出可用集合(ctx):
    await _seed(ctx.session, "平台文档", "一些内容")
    tool = make_rag_search(ctx)

    with pytest.raises(CapabilityError) as excinfo:
        await tool(query="关键词", collection="不存在的集合")

    assert "平台文档" in str(excinfo.value)


@pytest.mark.asyncio
async def test_rag_search_检索词为空报错(ctx):
    tool = make_rag_search(ctx)
    with pytest.raises(CapabilityError):
        await tool(query="   ")


@pytest.mark.asyncio
async def test_rag_search_top_k_越界报错(ctx):
    tool = make_rag_search(ctx)
    with pytest.raises(CapabilityError):
        await tool(query="关键词", top_k=0)
    with pytest.raises(CapabilityError):
        await tool(query="关键词", top_k=999)


@pytest.mark.asyncio
async def test_rag_search_没有集合时给一句话(ctx):
    tool = make_rag_search(ctx)
    result = await tool(query="关键词")
    assert result["items"] == []
    assert "还没有" in result["content"]


@pytest.mark.asyncio
async def test_rag_search_没命中时给一句话(ctx):
    await _seed(ctx.session, "平台文档", "一些内容")
    tool = make_rag_search(ctx)

    result = await tool(query="压根不存在的词")

    assert result["items"] == []
    assert "没找到" in result["content"]


@pytest.mark.asyncio
async def test_rag_search_纯关键词集合不算降级(ctx):
    """集合压根没配 embedding 是用户的配置选择，不是故障 ——
    报成"降级"会让人以为哪里坏了。"""
    await _seed(ctx.session, "平台文档", "数据库连接通过 DATABASE_URL 配置。")
    tool = make_rag_search(ctx)

    result = await tool(query="DATABASE_URL")

    assert "退化为关键词检索" not in result["content"]


# ---------- 读全文 ----------


@pytest.mark.asyncio
async def test_rag_read_取全文(ctx):
    collection = await _seed(ctx.session, "平台文档", "完整的一段正文，含 DATABASE_URL。")
    search_tool = make_rag_search(ctx)
    read_tool = make_rag_read(ctx)

    found = await search_tool(query="DATABASE_URL")
    document_id = found["items"][0]["document_id"]

    result = await read_tool(document_id=document_id)

    assert "完整的一段正文" in result["content"]
    assert result["collection"] == "平台文档"
    assert result["title"] == "数据库配置"
    assert result["document_id"] == document_id
    assert collection.id


@pytest.mark.asyncio
async def test_rag_read_文档不存在报错(ctx):
    tool = make_rag_read(ctx)
    with pytest.raises(CapabilityError):
        await tool(document_id=9999)


@pytest.mark.asyncio
async def test_rag_read_文档号不是整数报错(ctx):
    tool = make_rag_read(ctx)
    with pytest.raises(CapabilityError):
        await tool(document_id="不是数字")


# ---------- 注册 ----------


def test_两个能力都注册了():
    from app.capabilities.registration.builtin import BUILTIN_SPECS

    assert "rag_search" in BUILTIN_SPECS
    assert "rag_read" in BUILTIN_SPECS
    # 只读 → 不触发审批
    assert BUILTIN_SPECS["rag_search"].risk.value == "read"
    assert BUILTIN_SPECS["rag_read"].risk.value == "read"
