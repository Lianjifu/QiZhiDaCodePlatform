"""Agent 组装：能力清单 → 可执行的 agent。

工具**只从注册层来**（`app.capabilities.registry`），不再保留硬编码的第二条路径。
原因：两条路径并存时，同名能力会被 LangGraph 的 `tools_by_name` 静默覆盖
（后者胜），模型看到的能力集就不可预期了。现在冲突会显式报错。

LangChain v1 用 `create_agent` 一站式创建 agent，它内部是一张 LangGraph 图：
模型思考 → 调工具 → 结果回灌 → 再思考，直到模型不再要求调工具。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agents.llm import get_chat_model
from app.agents.prompts import CODE_AGENT_PROMPT
from app.capabilities.adapter import get_adapter
from app.capabilities.context import ExecutionContext
from app.capabilities.entry import KIND_TOOL, CapabilityEntry
from app.capabilities.orchestration import CapabilityMiddleware

logger = logging.getLogger(__name__)


async def build_tools(entries: list[CapabilityEntry], context: ExecutionContext) -> list[BaseTool]:
    """把清单里的原子能力变成模型可调用的工具。

    单个能力构造失败（配置写错、MCP 连不上）只跳过这一个，不拖垮整个 agent。
    """
    tools: list[BaseTool] = []
    for entry in entries:
        if entry.kind != KIND_TOOL or not entry.enabled:
            continue
        adapter = get_adapter(entry.source_type)
        try:
            tools.append(await adapter.build_tool(entry, context))
        except Exception as exc:
            logger.warning("能力 %s 不可用，已跳过: %s", entry.key, exc)
    return tools


async def build_agent(
    *,
    entries: list[CapabilityEntry],
    context: ExecutionContext,
    checkpointer: Any = None,
    model: BaseChatModel | None = None,
    system_prompt: str | None = None,
    allowed_tool_keys: set[str] | None = None,
) -> Any:
    """组装 agent。

    Args:
        entries: 注册层给出的统一能力清单。
        context: 执行上下文（工作区、沙箱配置等）。
        checkpointer: LangGraph checkpointer，用于多轮对话的上下文持久化。
        model: 覆盖默认模型（测试时注入假模型用）。
        system_prompt: 组装好的系统提示词。
        allowed_tool_keys: 限定暴露给模型的工具（编排层检索后的候选集）；
            为 None 表示全量暴露。
    """
    tools = await build_tools(entries, context)

    # 同名能力必须显式报错，不能让 LangGraph 静默覆盖
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            raise ValueError(f"能力名重复：{tool.name}")
        seen.add(tool.name)

    middleware = []
    if allowed_tool_keys is not None:
        middleware.append(CapabilityMiddleware(entries, allowed_tool_keys))

    return create_agent(
        model=model or get_chat_model(),
        tools=tools,
        system_prompt=system_prompt or CODE_AGENT_PROMPT,
        checkpointer=checkpointer,
        middleware=middleware,
    )


def default_workspace() -> Path:
    """默认工作区（会话级工作区由 Service 覆盖）。"""
    from app.core.config import settings

    return Path(settings.workspace_root).resolve()
