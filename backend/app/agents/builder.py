"""Agent 组装：能力清单 → 可执行的 agent。

工具**只从注册层来**（`app.capabilities.registration.registry`），不再保留硬编码
的第二条路径。原因：两条路径并存时，同名能力会被 LangGraph 的 `tools_by_name`
静默覆盖（后者胜），模型看到的能力集就不可预期了。现在冲突会显式报错。

LangChain v1 用 `create_agent` 一站式创建 agent，它内部是一张 LangGraph 图：
模型思考 → 调工具 → 结果回灌 → 再思考，直到模型不再要求调工具。

工具的 coroutine 走**执行层流水线**（`app.capabilities.execution.tooling`），
不直接执行能力 —— 这样 agent 内部的调用和直接调 API 走的是同一条路。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.tooling import build_tools
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.registration.spec import CapabilitySpec

logger = logging.getLogger(__name__)


async def build_agent(
    *,
    specs: list[CapabilitySpec],
    context: ExecutionContext,
    checkpointer: Any = None,
    model: BaseChatModel | None = None,
    system_prompt: str | None = None,
    allowed_tool_keys: set[str] | None = None,
    thread_id: str = "",
    trust_mode: str = "balanced",
) -> Any:
    """组装 agent。

    Args:
        specs: 注册层给出的统一能力清单。
        context: 执行上下文（工作区、沙箱配置、数据库会话等）。
        checkpointer: LangGraph checkpointer，用于多轮对话的上下文持久化。
        model: 覆盖默认模型（测试时注入假模型用）。
        system_prompt: 组装好的系统提示词。
        allowed_tool_keys: 限定暴露给模型的工具（意图层检索后的候选集）；
            为 None 表示全量暴露。
        thread_id: 会话 ID，调用记录按它归集。
        trust_mode: 会话信任模式，决定哪些能力需要人工审批。
    """
    tools = await build_tools(specs, context, allowed_tool_keys)

    # 同名能力必须显式报错，不能让 LangGraph 静默覆盖
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            raise ValueError(f"能力名重复：{tool.name}")
        seen.add(tool.name)

    middleware = [
        CapabilityMiddleware(
            specs,
            allowed_tool_keys=allowed_tool_keys,
            context=context,
            thread_id=thread_id,
            trust_mode=trust_mode,
        )
    ]

    return create_agent(
        model=model or _default_model(),
        tools=tools,
        system_prompt=system_prompt or _default_prompt(),
        checkpointer=checkpointer,
        middleware=middleware,
    )


def _default_model() -> BaseChatModel:
    from app.agents.llm import get_chat_model

    return get_chat_model()


def _default_prompt() -> str:
    from app.agents.prompts import CODE_AGENT_PROMPT

    return CODE_AGENT_PROMPT


def default_workspace() -> Path:
    """默认工作区（会话级工作区由 Service 覆盖）。"""
    from app.core.config import settings

    return Path(settings.workspace_root).resolve()
