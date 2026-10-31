"""Agent 执行器：把一次用户输入跑成一轮完整的"思考 + 调工具"循环。

这一层只做编排，不碰数据库、不做鉴权 —— 那是 Service 层的事。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agents.builder import build_agent


@dataclass
class AgentRunResult:
    """一轮 agent 执行的结果。"""

    thread_id: str
    content: str = ""
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class AgentRunner:
    """执行 agent。可注入 checkpointer 与 model（测试时用假模型）。"""

    def __init__(self, checkpointer: Any = None, model: Any = None) -> None:
        self.checkpointer = checkpointer
        self.model = model

    async def run(self, message: str, thread_id: str, workspace: Path) -> AgentRunResult:
        """跑完一轮，返回最终回复与过程中的工具调用。"""
        agent = build_agent(workspace, checkpointer=self.checkpointer, model=self.model)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        messages = result.get("messages", [])
        # 用了 checkpointer 后，state 里带着历史消息；只统计本轮（最后一条用户输入之后）的结果
        start = 0
        for index, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                start = index + 1
        turn_messages = messages[start:] or messages

        tool_calls: list[dict[str, Any]] = []
        for msg in turn_messages:
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                for call in msg.tool_calls:
                    tool_calls.append({"name": call.get("name"), "args": call.get("args", {})})
        final = turn_messages[-1] if turn_messages else None
        content = final.content if isinstance(final, AIMessage) else ""
        return AgentRunResult(
            thread_id=thread_id,
            content=content if isinstance(content, str) else str(content),
            steps=len(tool_calls),
            tool_calls=tool_calls,
        )

    async def stream(self, message: str, thread_id: str, workspace: Path) -> AsyncIterator[str]:
        """流式产出模型文本增量，供 SSE 推送。"""
        agent = build_agent(workspace, checkpointer=self.checkpointer, model=self.model)
        async for chunk, _meta in agent.astream(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="messages",
        ):
            if isinstance(chunk, AIMessageChunk):
                text = chunk.content
                if isinstance(text, str) and text:
                    yield text
