"""长期记忆一键初始化测试。

**"配了集合名"不等于"能用"** —— 名字来自环境变量，集合是数据库里的行。
这几条测的就是这两者怎么对得上，以及对不上的时候会发生什么。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.memory.setup import (
    REASON_COLLECTION_MISSING,
    REASON_NOT_CONFIGURED,
    LongTermSetup,
)
from app.models.llm_provider import LLMProvider
from app.rag.models import RagCollection


@pytest.fixture(autouse=True)
def _collection_name(monkeypatch):
    """集合名固定下来 —— 开发者本机的 .env 不该影响测试。"""
    monkeypatch.setattr(settings, "memory_collection_name", "agent-memory")


# ---------- 状态 ----------


@pytest.mark.asyncio
async def test_状态_集合不存在时给出原因(session: AsyncSession):
    status = await LongTermSetup(session).status()

    assert status.ready is False
    assert status.reason == REASON_COLLECTION_MISSING
    assert status.collection_name == "agent-memory"
    assert status.hint


@pytest.mark.asyncio
async def test_状态_没配集合名时给出原因(session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "memory_collection_name", "")

    status = await LongTermSetup(session).status()

    assert status.ready is False
    assert status.reason == REASON_NOT_CONFIGURED


@pytest.mark.asyncio
async def test_状态_集合在了就是就绪(session: AsyncSession):
    session.add(RagCollection(name="agent-memory", description="长期记忆"))
    await session.commit()

    status = await LongTermSetup(session).status()

    assert status.ready is True
    assert status.reason == ""
    assert status.collection_name == "agent-memory"
    assert status.collection_id is not None


# ---------- 初始化 ----------


@pytest.mark.asyncio
async def test_初始化_建出集合(session: AsyncSession):
    setup = LongTermSetup(session)

    status, created = await setup.initialize()

    assert created is True
    assert status.ready is True
    assert status.collection_name == "agent-memory"
    assert status.collection_id is not None
    # 没有可用的 embedding 模型 → 纯关键词集合，但能用
    assert status.keyword_only is True
    assert status.hint


@pytest.mark.asyncio
async def test_初始化_是幂等的(session: AsyncSession):
    setup = LongTermSetup(session)
    first, created_first = await setup.initialize()
    second, created_second = await setup.initialize()

    assert created_first is True
    assert created_second is False
    # 重建会把里面的记忆一起没掉，所以绝不能悄悄重建
    assert first.collection_id == second.collection_id


@pytest.mark.asyncio
async def test_初始化_没配集合名时报错(session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "memory_collection_name", "")

    with pytest.raises(ValidationError) as excinfo:
        await LongTermSetup(session).initialize()

    assert "MEMORY_COLLECTION_NAME" in str(excinfo.value)


@pytest.mark.asyncio
async def test_初始化_自动用供应商配的embedding模型(session: AsyncSession, monkeypatch):
    """默认供应商配了 embedding 模型就用它 —— 不用人再填一遍。"""
    session.add(
        LLMProvider(
            name="默认供应商",
            provider="openai",
            base_url="https://example.invalid/v1",
            api_key_encrypted="",
            api_key_masked="****",
            default_model="gpt-4o-mini",
            available_models=["gpt-4o-mini"],
            embedding_model="text-embedding-3-small",
            embedding_dim=1536,
            is_default=True,
            is_active=True,
        )
    )
    await session.commit()

    async def _fake_probe(provider):
        return 1536

    monkeypatch.setattr("app.rag.service.probe_dimension", _fake_probe)

    status, created = await LongTermSetup(session).initialize()

    assert created is True
    assert status.embedding_model == "text-embedding-3-small"
    assert status.embedding_dim == 1536
    assert status.keyword_only is False
    assert status.provider_name == "默认供应商"


@pytest.mark.asyncio
async def test_初始化_embedding模型不可用时直接报错(session: AsyncSession, monkeypatch):
    """模型名写错、密钥不对、端点不通 —— 建的时候就该报出来。"""
    session.add(
        LLMProvider(
            name="默认供应商",
            provider="openai",
            base_url="https://example.invalid/v1",
            api_key_encrypted="",
            api_key_masked="****",
            default_model="gpt-4o-mini",
            available_models=["gpt-4o-mini"],
            embedding_model="text-embedding-3-small",
            embedding_dim=1536,
            is_default=True,
            is_active=True,
        )
    )
    await session.commit()

    async def _boom(provider):
        from app.core.exceptions import AppError

        raise AppError("向量化端点不通")

    monkeypatch.setattr("app.rag.service.probe_dimension", _boom)

    with pytest.raises(ValidationError):
        await LongTermSetup(session).initialize()


# ---------- 接口 ----------


@pytest.mark.asyncio
async def test_接口_状态与初始化(client):
    status = await client.get("/api/v1/memory/long-term")
    assert status.status_code == 200
    body = status.json()["data"]
    assert body["ready"] is False
    assert body["reason"] == REASON_COLLECTION_MISSING

    created = await client.post("/api/v1/memory/long-term", json={})
    assert created.status_code == 200
    assert created.json()["data"]["ready"] is True

    again = await client.get("/api/v1/memory/long-term")
    assert again.json()["data"]["ready"] is True


@pytest.mark.asyncio
async def test_接口_查长期记忆时集合不在也不报错(client):
    """集合不在时管理接口看到空列表 + 一句提示，而不是 500。"""
    response = await client.get("/api/v1/memory", params={"scope": "long_term"})

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []
    assert "初始化" in response.json()["message"]


# ---------- 集合不在时的降级 ----------


@pytest.mark.asyncio
async def test_写长期记忆时集合不在就改记会话内(tmp_path, session: AsyncSession):
    """模型写长期失败之后往往不重试，而是自己找个别的办法记 ——
    所以宁可降级成会话内，也别让它报错。"""
    from app.capabilities.execution.context import ExecutionContext
    from app.capabilities.registration.builtin.memory import make_memory_write

    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    context.thread_id = "thread-1"

    result = await make_memory_write(context)(
        content="这条该记在长期里，但集合还没建好",
        scope="long_term",
    )

    assert result["scope"] == "session"
    assert "已改记在会话内" in result["content"]


@pytest.mark.asyncio
async def test_记忆目录在集合不在时只有会话内的(tmp_path, session: AsyncSession):
    """召回目录不能因为长期集合不在就整个失败。"""
    from app.agents.runner import AgentRunner
    from app.capabilities.execution.context import ExecutionContext

    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    context.thread_id = "thread-1"

    runner = AgentRunner.__new__(AgentRunner)  # 只借它的方法，不跑整个编排

    block = await runner._memory_block("关键词", context)
    assert isinstance(block, str)
