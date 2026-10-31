"""Agent 执行器：把一次用户输入跑成一轮完整的"思考 + 调工具"循环。

这一层只做编排，不碰数据库、不做鉴权 —— 那是 Service 层的事。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agents.builder import build_agent
from app.agents.prompts import CODE_AGENT_PROMPT
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.intent.planner import collect_tool_keys, select_skills
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registration.spec import CapabilitySpec
from app.core.config import settings


@dataclass
class AgentRunResult:
    """一轮 agent 执行的结果。"""

    thread_id: str
    content: str = ""
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


class AgentRunner:
    """执行 agent。可注入 checkpointer 与 model（测试时用假模型）。"""

    def __init__(self, checkpointer: Any = None, model: Any = None) -> None:
        self.checkpointer = checkpointer
        self.model = model

    async def run(
        self,
        message: str,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> AgentRunResult:
        """跑完一轮，返回最终回复与过程中的工具调用。"""
        agent, skills = await self._build(
            message,
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
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
            skills=[skill.key for skill in skills],
        )

    async def stream(
        self,
        message: str,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> AsyncIterator[str]:
        """流式产出模型文本增量，供 SSE 推送。"""
        agent, _skills = await self._build(
            message,
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
        async for chunk, _meta in agent.astream(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="messages",
        ):
            if isinstance(chunk, AIMessageChunk):
                text = chunk.content
                if isinstance(text, str) and text:
                    yield text

    async def _build(
        self,
        message: str,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None,
        context: ExecutionContext | None,
        skill_ids: list[int] | None,
    ):
        """组装 agent 与选中的技能。"""
        specs = specs or []
        context = context or ExecutionContext.build(workspace)
        context.thread_id = context.thread_id or thread_id

        skills = select_skills(specs, message, skill_ids)
        allowed = collect_tool_keys(skills, specs) if skills else None
        system_prompt = assemble_prompt(
            CODE_AGENT_PROMPT,
            skills,
            runtime_notes=[f"工作区：{workspace}"],
        )
        agent = await build_agent(
            specs=specs,
            context=context,
            checkpointer=self.checkpointer,
            model=self.model,
            system_prompt=system_prompt,
            allowed_tool_keys=allowed,
            thread_id=thread_id,
            trust_mode=settings.capability_trust_mode,
        )
        return agent, skills
