"""子代理的治理：子代理的工具调用也要经过同一套风险判定与留痕。

deepAgents 自带的通用子代理**不继承**我们自定义的中间件 —— 它的中间件是按
默认槽位继承的。所以我们自带一份子代理规格，显式带上 ``CapabilityMiddleware``。

这个测试守住的就是这件事：子代理干的活同样要留痕。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.capabilities.execution.context import ExecutionContext
from app.core.config import settings


class SequencedModel(BaseChatModel):
    """按脚本顺序发工具调用，之后回文本。

    父 agent 和子代理共用同一个模型实例，所以调用次序是全局的：
    第 1 次调用派子代理，第 2 次调用（子代理里的）读文件，第 3 次回文本。
    """

    steps: list[tuple[str, dict[str, Any]]] = []
    calls: int = 0
    reply: str = "做完了。"

    @property
    def _llm_type(self) -> str:
        return "sequenced"

    def bind_tools(self, tools: Any, **kwargs: Any) -> SequencedModel:
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        index = self.calls
        self.calls += 1
        if index < len(self.steps):
            name, args = self.steps[index]
            call = {
                "name": name,
                "args": dict(args),
                "id": f"call_{index}",
                "type": "tool_call",
            }
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


@pytest.mark.asyncio
async def test_subagent_tool_calls_are_recorded(session) -> None:
    """子代理调的工具也要写进调用记录 —— 否则子代理就是治理的盲区。"""
    from sqlalchemy import select

    from app.agents.builder import build_agent
    from app.models.invocation import Invocation

    thread_id = "t-sub"
    workspace = Path(settings.workspace_root).resolve() / thread_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "a.txt").write_text("hello", encoding="utf-8")

    context = ExecutionContext.build(workspace)
    context.session = session
    context.thread_id = thread_id

    model = SequencedModel(
        steps=[
            ("task", {"description": "读一下 a.txt", "subagent_type": "general"}),
            ("read_file", {"file_path": "a.txt"}),
        ]
    )
    agent = await build_agent(
        specs=[],
        context=context,
        model=model,
        system_prompt="你是测试助手。",
        thread_id=thread_id,
        trust_mode="balanced",
    )
    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "读一下 a.txt"}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    await session.commit()

    records = (
        (await session.execute(select(Invocation).where(Invocation.thread_id == thread_id)))
        .scalars()
        .all()
    )
    keys = {record.capability_key for record in records}
    assert "read_file" in keys, f"子代理的工具调用没有留痕：{keys}"
