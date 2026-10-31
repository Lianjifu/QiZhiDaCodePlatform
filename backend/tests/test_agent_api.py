"""agent 接口测试：注入假模型，验证 HTTP 链路（不触网、不需要 api_key）。"""

import pytest
from httpx import AsyncClient

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_service
from app.services.agent import AgentService
from tests.fake_chat_model import FakeChatModel

API = "/api/v1/agent"


def _override(app, reply: str = "已收到") -> None:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        runner=AgentRunner(model=FakeChatModel(reply=reply))
    )


@pytest.mark.asyncio
async def test_chat_returns_reply(app, client: AsyncClient) -> None:
    _override(app, "已收到：测试")
    resp = await client.post(API + "/chat", json={"message": "hello", "thread_id": "t1"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["content"] == "已收到：测试"
    assert data["thread_id"] == "t1"
    assert data["steps"] == 0


@pytest.mark.asyncio
async def test_chat_generates_thread_id(app, client: AsyncClient) -> None:
    _override(app)
    resp = await client.post(API + "/chat", json={"message": "hello"})
    assert resp.status_code == 200
    assert resp.json()["data"]["thread_id"]


@pytest.mark.asyncio
async def test_chat_rejects_empty_message(app, client: AsyncClient) -> None:
    _override(app)
    resp = await client.post(API + "/chat", json={"message": ""})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_stream_returns_sse(app, client: AsyncClient) -> None:
    _override(app, "流式内容")
    resp = await client.post(API + "/stream", json={"message": "hello", "thread_id": "t2"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "流式内容" in resp.text
    assert "[DONE]" in resp.text
