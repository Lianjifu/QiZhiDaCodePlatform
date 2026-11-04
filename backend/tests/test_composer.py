"""输入框的三项能力：斜杠命令、@ 提及（工作区目录树）、清空会话。

还有一条容易坏的契约：**流式执行失败时必须推一帧 ``error``**。
SSE 一旦开始就不能改 HTTP 状态码了，错误只能走帧 —— 少了这一帧，前端就永远
停在「思考中…」。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatResult

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_service
from app.core.workspace import resolve_workspace
from app.services.agent import AgentService
from tests.test_agent_capabilities import RecordingModel

API = "/api/v1/agent"


class BrokenModel(BaseChatModel):
    """一调用就炸 —— 用来测流式失败时的错误帧。"""

    @property
    def _llm_type(self) -> str:
        return "broken"

    def bind_tools(self, tools: Any, **kwargs: Any) -> BrokenModel:
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("模型炸了")


def _override(app, model, session) -> None:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )


def _frames(text: str) -> list[dict]:
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
async def test_commands_endpoint_returns_catalog(app, client, session) -> None:
    """斜杠命令清单要能从后端拿到，而且两类命令都有。"""
    resp = await client.get(API + "/commands")
    assert resp.status_code == 200
    commands = resp.json()["data"]
    assert commands, "命令清单是空的"

    names = {item["name"] for item in commands}
    assert {"help", "model", "files"} <= names, f"少了命令：{names}"

    kinds = {item["kind"] for item in commands}
    assert "prompt" in kinds and "action" in kinds, f"两类命令都得有：{kinds}"

    # prompt 类必须带提示词正文，否则选中之后没东西可填
    for item in commands:
        if item["kind"] == "prompt":
            assert item["template"], f"{item['name']} 没有提示词正文"
        else:
            assert item["action"], f"{item['name']} 没有动作名"


@pytest.mark.asyncio
async def test_tree_endpoint_includes_directories(app, client, session) -> None:
    """@ 提及面板要能选目录 —— 所以列表里得有目录条目。"""
    thread_id = "t-tree"
    workspace = resolve_workspace(thread_id)
    (workspace / "sub").mkdir(parents=True, exist_ok=True)
    (workspace / "sub" / "a.txt").write_text("hello", encoding="utf-8")
    (workspace / "b.txt").write_text("world", encoding="utf-8")

    resp = await client.get(f"/api/v1/files/tree?thread_id={thread_id}")
    assert resp.status_code == 200
    files = resp.json()["data"]["files"]
    paths = {item["path"] for item in files}

    assert "/sub" in paths, f"目录没列出来：{paths}"
    assert "/sub/a.txt" in paths, f"子目录里的文件没列出来：{paths}"
    assert "/b.txt" in paths, f"根目录的文件没列出来：{paths}"

    directories = [item for item in files if item["is_dir"]]
    assert directories, "没有目录条目"
    assert all(item["size"] == 0 for item in directories)


@pytest.mark.asyncio
async def test_clear_endpoint_empties_history(app, client, session) -> None:
    """/clear 要真的把对话记录清掉（工作区文件留着）。"""
    thread_id = "t-clear"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "keep.txt").write_text("留着", encoding="utf-8")

    model = RecordingModel(reply="我说完了")
    _override(app, model, session)
    await client.post(API + "/chat", json={"message": "你好", "thread_id": thread_id})

    resp = await client.delete(f"{API}/{thread_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cleared"

    history = await client.get(f"{API}/{thread_id}/history")
    assert history.json()["data"]["messages"] == []
    # 文件不该被删 —— 清的是对话，不是工作区
    assert (workspace / "keep.txt").exists()


@pytest.mark.asyncio
async def test_stream_failure_yields_error_frame(app, client, session) -> None:
    """流式执行炸了要推一帧 error，再补一帧 done。

    少了 error 帧，前端会一直停在「思考中…」—— 因为它只认得 done 收尾。
    """
    thread_id = "t-stream-error"
    _override(app, BrokenModel(), session)

    resp = await client.post(API + "/stream", json={"message": "说话", "thread_id": thread_id})
    assert resp.status_code == 200

    frames = _frames(resp.text)
    types = [f["type"] for f in frames]
    assert "error" in types, f"没有错误帧：{types}"
    assert types[-1] == "[DONE]", f"最后一帧应该是 [DONE]：{types}"

    error_frame = next(f for f in frames if f["type"] == "error")
    assert error_frame["error"], "错误帧里没有原因"
    # 每帧都得是 AgentStreamChunk —— 端点会调 model_dump_json()
    assert "tool_call" in error_frame and "status" in error_frame
