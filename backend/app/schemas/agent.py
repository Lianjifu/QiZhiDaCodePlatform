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
    provider_id: int | None = Field(
        default=None,
        ge=1,
        description="指定用哪个模型接入配置；不传则用默认配置，都没有则读 .env",
    )
    skill_ids: list[int] = Field(
        default_factory=list,
        description="要启用的技能 id；技能带来的工具与提示词会被挂载到 agent 上",
    )


class ToolCallOut(BaseModel):
    """一次工具调用记录。

    除了"调了什么"，还带上**执行摘要**：这次调用成功了、失败了，还是卡在
    审批上。没有它的话，调用方只知道模型动了手，不知道结果如何。
    """

    name: str = Field(description="工具名")
    args: dict[str, Any] = Field(default_factory=dict, description="调用参数")
    invocation_id: int | None = Field(default=None, description="对应的调用记录 ID")
    ok: bool | None = Field(default=None, description="这次调用是否成功")
    status: str | None = Field(default=None, description="succeeded / failed / pending（等待审批）")
    error_code: str | None = Field(default=None, description="失败时的错误码")
    duration_ms: int | None = Field(default=None, description="执行耗时（毫秒）")
    blocked: bool | None = Field(default=None, description="任务是否已被标记为阻塞")


class PendingApprovalOut(BaseModel):
    """一条待审批的调用。"""

    kind: str = Field(default="", description="declared（声明级）/ param_risk（参数级）")
    tool: str = Field(default="", description="工具名")
    arguments: dict[str, Any] = Field(default_factory=dict, description="调用参数")
    risk: str = Field(default="", description="评估出的风险等级")
    reasons: list[str] = Field(default_factory=list, description="风险上调原因")
    message: str = Field(default="", description="给人看的说明")


class AgentChatOut(BaseModel):
    """一轮对话的结果。"""

    thread_id: str = Field(description="会话 ID")
    content: str = Field(default="", description="最终回复文本")
    steps: int = Field(default=0, description="工具调用次数")
    tool_calls: list[ToolCallOut] = Field(default_factory=list, description="工具调用明细")
    status: str = Field(
        default="completed",
        description="completed（跑完了）/ pending_approval（挂起等人批）",
    )
    pending: list[PendingApprovalOut] = Field(
        default_factory=list,
        description="待审批的条目；status 为 pending_approval 时有值",
    )


class AgentStreamChunk(BaseModel):
    """流式接口的单帧内容。"""

    text: str = Field(default="", description="文本增量")
