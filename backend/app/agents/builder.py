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
    extra_tools: list | None = None,
    extra_prompt: str = "",
) -> Any:
    """组装一个绑定到指定工作区的 agent。

    Args:
        workspace: 本次会话的工作区目录，agent 的文件与代码操作都被限制在其中。
        checkpointer: LangGraph checkpointer，用于多轮对话的上下文持久化。
        model: 覆盖默认模型（测试时注入假模型用）。
        extra_tools: 技能带来的额外工具（来自数据库里的技能定义）。
        extra_prompt: 技能带来的附加提示词。
    """
    tools = [*build_tools(workspace), *(extra_tools or [])]
    system_prompt = CODE_AGENT_PROMPT
    if extra_prompt:
        system_prompt = f"{system_prompt}\n\n{extra_prompt}"

    return create_agent(
        model=model or get_chat_model(),
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
