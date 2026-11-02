"""流式接口的事件流与会话历史。

前端要画"工具调用折叠块"和"审批卡片"，全靠这里的事件流 —— 所以这几条
是契约，坏了前端就画不出来：

- **每帧都有 type** —— 帧的种类决定前端怎么渲染；
- **tool_call / tool_result 能对上** —— 靠 tool_call_id 配对；
- **文本是增量** —— 一段一段来的，不是最后一次性给全；
- **收尾有 done** —— 带本轮状态（跑完了还是停在审批上）。
"""

from __future__ import annotations

import json

import pytest

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_service
from app.core.workspace import resolve_workspace
from app.services.agent import AgentService
from tests.test_agent_capabilities import RecordingModel

API = "/api/v1/agent"


def _override(app, model: RecordingModel, session) -> None:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )


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


@pytest.mark.asyncio
async def test_stream_emits_typed_events(app, client, session) -> None:
    """一轮对话应该吐出文本、工具调用、工具结果、收尾四种事件。"""
    thread_id = "t-stream-events"
    workspace = resolve_workspace(thread_id)
    (workspace / "a.txt").write_text("hello", encoding="utf-8")

    model = RecordingModel(
        tool_name="read_file",
        tool_args={"file_path": "a.txt"},
        reply="读完了",
    )
    _override(app, model, session)

    resp = await client.post(
        API + "/stream", json={"message": "读一下 a.txt", "thread_id": thread_id}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = _frames(resp.text)
    types = [f["type"] for f in frames]
    assert "tool_call" in types, f"没有工具调用事件：{types}"
    assert "tool_result" in types, f"没有工具结果事件：{types}"
    assert "text" in types, f"没有文本事件：{types}"
    # 最后一帧是 SSE 的结束标记 [DONE]，它前面那帧才是 done 事件
    assert types[-1] == "[DONE]", f"最后一帧应该是 [DONE]：{types}"
    assert types[-2] == "done", f"倒数第二帧应该是 done：{types}"
    assert frames[-2]["status"] == "completed"


@pytest.mark.asyncio
async def test_stream_tool_call_and_result_are_paired(app, client, session) -> None:
    """tool_call 与 tool_result 要能靠 tool_call_id 配上。

    前端靠这个 id 把"调用"和"结果"填进同一个折叠块 —— 对不上就会出现
    一个永远转圈的块。
    """
    thread_id = "t-stream-pair"
    workspace = resolve_workspace(thread_id)
    (workspace / "a.txt").write_text("hello", encoding="utf-8")

    model = RecordingModel(tool_name="read_file", tool_args={"file_path": "a.txt"}, reply="好了")
    _override(app, model, session)

    resp = await client.post(
        API + "/stream", json={"message": "读一下 a.txt", "thread_id": thread_id}
    )
    frames = _frames(resp.text)

    calls = [f["tool_call"] for f in frames if f["type"] == "tool_call"]
    results = [f["tool_result"] for f in frames if f["type"] == "tool_result"]
    assert calls, "没有 tool_call"
    assert results, "没有 tool_result"
    call_ids = {c["tool_call_id"] for c in calls}
    result_ids = {r["tool_call_id"] for r in results}
    assert call_ids == result_ids, f"配不上：{call_ids} vs {result_ids}"
    assert calls[0]["name"] == "read_file"


@pytest.mark.asyncio
async def test_stream_text_is_incremental(app, client, session) -> None:
    """文本是**增量**来的 —— 前端靠这个做打字机。

    如果一次性给全，前端要么没有打字机效果，要么得自己猜怎么切。
    """
    thread_id = "t-stream-text"
    model = RecordingModel(reply="这是最终回复")
    _override(app, model, session)

    resp = await client.post(API + "/stream", json={"message": "说话", "thread_id": thread_id})
    frames = _frames(resp.text)

    texts = [f["text"] for f in frames if f["type"] == "text"]
    assert texts, "没有文本帧"
    assert "".join(texts) == "这是最终回复"


@pytest.mark.asyncio
async def test_stream_ends_with_done_marker(app, client, session) -> None:
    """流尾是 [DONE] —— 前端靠它收连接。"""
    thread_id = "t-stream-done"
    model = RecordingModel(reply="好")
    _override(app, model, session)

    resp = await client.post(API + "/stream", json={"message": "说话", "thread_id": thread_id})
    frames = _frames(resp.text)
    assert frames[-1]["type"] == "[DONE]"


@pytest.mark.asyncio
async def test_history_returns_messages(app, client, session) -> None:
    """会话历史要从 checkpoint 里读回来 —— 刷新页面后前端靠它还原对话。"""
    thread_id = "t-history"
    model = RecordingModel(reply="我说完了")
    _override(app, model, session)

    chat = await client.post(API + "/chat", json={"message": "你好", "thread_id": thread_id})
    assert chat.status_code == 200

    resp = await client.get(f"{API}/{thread_id}/history")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["thread_id"] == thread_id
    roles = [m["role"] for m in data["messages"]]
    assert "user" in roles, f"历史里没有用户消息：{roles}"
    assert "assistant" in roles, f"历史里没有助手消息：{roles}"
    user_message = next(m for m in data["messages"] if m["role"] == "user")
    assert user_message["content"] == "你好"


@pytest.mark.asyncio
async def test_history_of_unknown_thread_is_empty(app, client, session) -> None:
    """没聊过的会话读历史是空列表，不是错误。"""
    model = RecordingModel(reply="好")
    _override(app, model, session)

    resp = await client.get(f"{API}/t-never-talked/history")
    assert resp.status_code == 200
    assert resp.json()["data"]["messages"] == []


@pytest.mark.asyncio
async def test_stream_bad_provider_gives_error_frame(app, client, session) -> None:
    """请求里的供应商配置不存在 —— 要推 error 帧，不是把连接掐了。

    SSE 的 HTTP 状态码在开始推数据时就定了（200），改不了。异常直接冒出去
    的话，连接断掉，**既没有 error 帧也没有 done 帧** —— 前端界面上只剩一个
    空的助手气泡，用户完全不知道出了什么事。
    """
    thread_id = "t-stream-bad-provider"
    model = RecordingModel(reply="好")
    _override(app, model, session)

    resp = await client.post(
        API + "/stream",
        json={"message": "说话", "thread_id": thread_id, "provider_id": 99999},
    )
    assert resp.status_code == 200

    frames = _frames(resp.text)
    types = [f["type"] for f in frames]
    assert "error" in types, f"没有错误帧：{types}"
    assert types[-1] == "[DONE]", f"最后一帧应该是 [DONE]：{types}"
    error_frame = next(f for f in frames if f["type"] == "error")
    assert error_frame["error"], "错误帧里没有原因"
