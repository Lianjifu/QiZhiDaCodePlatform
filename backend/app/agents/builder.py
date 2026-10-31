"""Agent 组装：模型 + 工具 + 提示词 → 可执行的图。

LangChain v1 用 `create_agent` 一站式创建 agent，它内部就是一张 LangGraph 图：
模型思考 → 调用工具 → 把结果回灌 → 再思考，直到模型不再要求调工具。
"""

from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from app.agents.llm import get_chat_model
from app.agents.prompts import CODE_AGENT_PROMPT
from app.agents.tools import build_tools


def build_agent(
    workspace: Path,
    checkpointer: Any = None,
    model: BaseChatModel | None = None,
) -> Any:
    """组装一个绑定到指定工作区的 agent。

    Args:
        workspace: 本次会话的工作区目录，agent 的文件与代码操作都被限制在其中。
        checkpointer: LangGraph checkpointer，用于多轮对话的上下文持久化。
        model: 覆盖默认模型（测试时注入假模型用）。
    """
    return create_agent(
        model=model or get_chat_model(),
        tools=build_tools(workspace),
        system_prompt=CODE_AGENT_PROMPT,
        checkpointer=checkpointer,
    )
