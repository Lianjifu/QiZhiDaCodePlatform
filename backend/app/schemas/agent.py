"""编码助手的请求/响应模型。"""

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    """一次对话请求。"""

    message: str = Field(min_length=1, max_length=20000, description="用户输入")
    thread_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
        max_length=64,
        description="会话 ID，多轮对话靠它串联；不传则新建会话",
    )


class ToolCallOut(BaseModel):
    """一次工具调用记录。"""

    name: str = Field(description="工具名")
    args: dict[str, Any] = Field(default_factory=dict, description="调用参数")


class AgentChatOut(BaseModel):
    """一轮对话的结果。"""

    thread_id: str = Field(description="会话 ID")
    content: str = Field(default="", description="最终回复文本")
    steps: int = Field(default=0, description="工具调用次数")
    tool_calls: list[ToolCallOut] = Field(default_factory=list, description="工具调用明细")


class AgentStreamChunk(BaseModel):
    """流式接口的单帧内容。"""

    text: str = Field(default="", description="文本增量")
