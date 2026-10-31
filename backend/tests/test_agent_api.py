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


@pytest.mark.asyncio
async def test_chat_returns_clean_error_when_model_fails(app, client: AsyncClient) -> None:
    """模型调用失败时，要返回结构化错误，而不是裸的 500 traceback。"""

    class BrokenModel(FakeChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("Missing credentials. Please pass an `api_key`")

    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        runner=AgentRunner(model=BrokenModel())
    )
    resp = await client.post(API + "/chat", json={"message": "hi", "thread_id": "t3"})
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "AGENT_ERROR"
    assert "LLM_API_KEY" in body["message"]


# ---------- 与模型接入配置联动 ----------


@pytest.mark.asyncio
async def test_chat_uses_provider_from_db(app, client: AsyncClient, monkeypatch) -> None:
    """指定 provider_id 时，应按数据库里的供应商配置来构造模型。"""
    created = await client.post(
        "/api/v1/llm-providers",
        json={
            "name": "联动测试",
            "provider": "deepseek",
            "api_key": "sk-unit-test-key",
            "default_model": "deepseek-chat",
        },
    )
    provider_id = created.json()["data"]["id"]

    seen: dict = {}

    def fake_build(provider):
        seen["id"] = provider.id
        seen["model"] = provider.default_model
        return FakeChatModel(reply="来自数据库供应商")

    monkeypatch.setattr("app.services.agent.build_model_for_provider", fake_build)

    resp = await client.post(
        API + "/chat", json={"message": "hi", "thread_id": "tp1", "provider_id": provider_id}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["content"] == "来自数据库供应商"
    assert seen == {"id": provider_id, "model": "deepseek-chat"}


@pytest.mark.asyncio
async def test_chat_with_unknown_provider_returns_404(app, client: AsyncClient) -> None:
    resp = await client.post(
        API + "/chat", json={"message": "hi", "thread_id": "tp2", "provider_id": 9999}
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NOT_FOUND"
