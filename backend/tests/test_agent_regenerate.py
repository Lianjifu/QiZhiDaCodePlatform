"""重新生成 与 分支会话。

两个能力都作用在 **checkpoint 的历史**上：

- **重新生成**：把最后一条用户消息起（含它）往后全删，再用那条用户消息重跑；
- **分支**：把原会话的消息切到分支点，写进一个新会话的 checkpoint。

都只动 LangGraph 的公开接口（``update_state`` + ``RemoveMessage``），
不碰 checkpoint 表里的 blob。
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_service
from app.services.agent import AgentService
from tests.test_agent_capabilities import RecordingModel

API = "/api/v1/agent"


def _frames(text: str) -> list[dict]:
    """把 SSE 响应体拆成帧。"""
    frames = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        payload = block[len("data:") :].strip()
        if payload == "[DONE]":
            frames.append({"type": "[DONE]"})
            continue
        frames.append(json.loads(payload))
    return frames


def _override(app, model: RecordingModel, session, saver) -> None:
    """注入带 checkpointer 的 runner —— 没它就没有历史可删。"""
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model, checkpointer=saver)
    )


@pytest.fixture
async def saver(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as instance:
        await instance.setup()
        yield instance


@pytest.mark.asyncio
async def test_regenerate_replaces_the_last_turn(app, client, session, saver) -> None:
    """重新生成：最后那轮被换掉，之前的对话不动。"""
    thread_id = "t-regenerate"
    model = RecordingModel(reply="第一版回复")
    _override(app, model, session, saver)

    await client.post(API + "/stream", json={"message": "第一句", "thread_id": thread_id})
    model.reply = "第二版回复"
    await client.post(API + "/stream", json={"message": "第二句", "thread_id": thread_id})

    before = (await client.get(f"{API}/{thread_id}/history")).json()["data"]["messages"]
    assert [m["role"] for m in before] == ["user", "assistant", "user", "assistant"]
    assert before[-1]["content"] == "第二版回复"

    # 换一句回复，重新生成
    model.reply = "重新生成的回复"
    resp = await client.post(f"{API}/{thread_id}/regenerate", json={"thread_id": thread_id})
    assert resp.status_code == 200
    frames = _frames(resp.text)
    assert any(f["type"] == "text" and "重新生成的回复" in f.get("text", "") for f in frames)

    after = (await client.get(f"{API}/{thread_id}/history")).json()["data"]["messages"]
    # 还是四条：用户1、助手1、用户2、助手2（新）
    assert [m["role"] for m in after] == ["user", "assistant", "user", "assistant"]
    assert after[-1]["content"] == "重新生成的回复"
    # 前面那轮没被动过
    assert after[0]["content"] == "第一句"


@pytest.mark.asyncio
async def test_regenerate_on_empty_session_reports_error(app, client, session, saver) -> None:
    """没聊过就没什么可重新生成 —— 得说清楚，别静默。"""
    _override(app, RecordingModel(reply="x"), session, saver)
    resp = await client.post(f"{API}/t-nothing/regenerate", json={"thread_id": "t-nothing"})
    frames = _frames(resp.text)
    errors = [f for f in frames if f["type"] == "error"]
    assert errors, frames
    assert "重新生成" in errors[0]["error"]


@pytest.mark.asyncio
async def test_fork_copies_the_prefix_into_a_new_thread(app, client, session, saver) -> None:
    """分支：从第一条助手消息切出新会话，新会话保留到那条为止。"""
    thread_id = "t-fork-source"
    model = RecordingModel(reply="回复")
    _override(app, model, session, saver)

    await client.post(API + "/stream", json={"message": "第一句", "thread_id": thread_id})
    await client.post(API + "/stream", json={"message": "第二句", "thread_id": thread_id})

    history = (await client.get(f"{API}/{thread_id}/history")).json()["data"]["messages"]
    assert len(history) == 4
    # 从第一条助手消息分出去（保留下标 0 和 1）
    branch_id = history[1]["id"]
    assert branch_id, "历史里要有消息 id，前端才知道从哪分"

    resp = await client.post(f"{API}/{thread_id}/fork", json={"message_id": branch_id})
    assert resp.status_code == 200
    data = resp.json()["data"]
    new_thread = data["thread_id"]
    assert new_thread != thread_id
    assert data["kept_messages"] == 2

    forked = (await client.get(f"{API}/{new_thread}/history")).json()["data"]["messages"]
    assert [m["role"] for m in forked] == ["user", "assistant"]
    assert forked[0]["content"] == "第一句"

    # 原会话没被动过
    original = (await client.get(f"{API}/{thread_id}/history")).json()["data"]["messages"]
    assert len(original) == 4


@pytest.mark.asyncio
async def test_fork_with_unknown_message_returns_404(app, client, session, saver) -> None:
    """消息 id 不存在就说 404，别静默分出个空会话。"""
    _override(app, RecordingModel(reply="x"), session, saver)
    resp = await client.post(f"{API}/t-fork-missing/fork", json={"message_id": "不存在"})
    assert resp.status_code == 404
