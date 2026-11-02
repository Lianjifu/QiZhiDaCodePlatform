"""并行工具调用。

**模型一次回复里可以带多个工具调用**（LangGraph 的 ToolNode 会并发执行它们）。
工具是并行的，但数据库会话不是 —— 两个调用同时 flush 同一个 AsyncSession 会炸：

    sqlalchemy.exc.InvalidRequestError: Session is already flushing

这一炸整轮对话就没了 —— 前端看到的是流断在半路。所以数据库那一段必须串行，
工具本身的执行（沙箱里跑命令那几十秒）不能串行。

回归测试：一次回复里带两个工具调用，跑完之后要拿到两个结果 + 一帧 done，
**不能有 error 帧**。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_service
from app.core.workspace import resolve_workspace
from app.services.agent import AgentService

API = "/api/v1/agent"


class ParallelModel(BaseChatModel):
    """一次回复里带两个工具调用 —— 逼着框架并发跑它们。"""

    reply: str = "都跑完了"
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "parallel"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ParallelModel:
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            tool_calls = [
                {
                    "name": "ls",
                    "args": {"path": "/"},
                    "id": "call_a",
                    "type": "tool_call",
                },
                {
                    "name": "read_file",
                    "args": {"file_path": "/a.txt"},
                    "id": "call_b",
                    "type": "tool_call",
                },
            ]
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=tool_calls))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


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
async def test_parallel_tool_calls_do_not_break_the_session(app, client, session) -> None:
    """两个工具并行跑 —— 数据库那段要串行，不然整轮对话炸掉。"""
    thread_id = "t-parallel-tools"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "a.txt").write_text("hello", encoding="utf-8")

    model = ParallelModel()
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )

    resp = await client.post(API + "/stream", json={"message": "看看", "thread_id": thread_id})
    assert resp.status_code == 200

    frames = _frames(resp.text)
    types = [f["type"] for f in frames]

    assert "error" not in types, f"并行调用把这一轮跑炸了：{types}"
    results = [f for f in frames if f["type"] == "tool_result"]
    assert len(results) == 2, f"两个工具的结果都该回来：{types}"
    assert types[-1] == "[DONE]"
    assert types[-2] == "done", f"最后一帧该是 done：{types}"
