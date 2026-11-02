"""编码助手的请求/响应模型。"""

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.config import settings


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
    attachments: list[str] = Field(
        default_factory=list,
        max_length=settings.max_attachments_per_turn,
        description=(
            "本轮要带的附件（虚拟路径，如 /uploads/xxx.png）。"
            "图片会内联进消息，模型这一轮就能看见；其它文件只给路径，agent 自己读"
        ),
    )


class ToolCallOut(BaseModel):
    """一次工具调用记录。

    除了"调了什么"，还带上**执行摘要**：这次调用成功了、失败了，还是卡在
    审批上。没有它的话，调用方只知道模型动了手，不知道结果如何。
    """

    name: str = Field(description="工具名")
    args: dict[str, Any] = Field(default_factory=dict, description="调用参数")
    tool_call_id: str | None = Field(
        default=None, description="模型给这次调用分配的 id（前端拿它对上工具结果）"
    )
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


class ToolResultOut(BaseModel):
    """工具执行结果的摘要。

    **只给摘要，不给全文** —— 工具输出可能有几万 token（读了整个仓库），
    原样塞进 SSE 会把前端撑爆。要全文的话去查调用记录
    （``GET /api/v1/invocations/{id}``，那里有 ``result`` 与 ``stages``）。
    """

    tool_call_id: str = Field(default="", description="对应的 tool_call id")
    name: str = Field(default="", description="工具名")
    ok: bool = Field(default=True, description="执行成功与否")
    status: str = Field(default="", description="succeeded / failed / pending")
    error_code: str | None = Field(default=None, description="失败时的错误码")
    duration_ms: int = Field(default=0, description="执行耗时（毫秒）")
    summary: str = Field(default="", description="结果摘要（截断后的正文）")


class AgentStreamChunk(BaseModel):
    """流式接口的单帧内容。

    **一帧一种事件**（``type`` 决定这帧是什么）：

    | type | 载荷 | 前端拿它做什么 |
    | --- | --- | --- |
    | ``text`` | ``text`` | 打字机渲染 |
    | ``tool_call`` | ``tool_call`` | 工具折叠块亮起 |
    | ``tool_result`` | ``tool_result`` | 工具折叠块补成完成态 |
    | ``pending_approval`` | ``pending`` | 弹审批卡片 |
    | ``done`` | ``status`` / ``steps`` | 收尾 |

    ``type`` 默认 ``"text"`` —— 老的消费方按 ``{"text": ...}`` 解析仍然能用。
    """

    type: str = Field(
        default="text",
        description="事件类型：text / tool_call / tool_result / pending_approval / done",
    )
    text: str = Field(default="", description="文本增量（type=text 时有值）")
    tool_call: ToolCallOut | None = Field(
        default=None, description="工具调用（type=tool_call 时有值）"
    )
    tool_result: ToolResultOut | None = Field(
        default=None, description="工具结果摘要（type=tool_result 时有值）"
    )
    pending: list[PendingApprovalOut] = Field(
        default_factory=list, description="待批清单（type=pending_approval 时有值）"
    )
    status: str = Field(default="", description="本轮状态（type=done 时有值）")
    steps: int = Field(default=0, description="本轮工具调用次数（type=done 时有值）")


class AgentHistoryMessage(BaseModel):
    """历史消息里的一条。"""

    role: str = Field(description="user / assistant / tool")
    content: str = Field(default="", description="消息正文")
    name: str | None = Field(default=None, description="工具消息的工具名")
    tool_call_id: str | None = Field(
        default=None, description="工具消息对应的 tool_call id（前端拿它对上工具块）"
    )
    tool_calls: list[ToolCallOut] = Field(default_factory=list, description="助手消息里的工具调用")


class AgentHistoryOut(BaseModel):
    """一个会话的历史消息。

    从 checkpoint 的状态里读回来的 —— 消息是**只读的**，前端拿它渲染列表。
    """

    thread_id: str = Field(description="会话 ID")
    messages: list[AgentHistoryMessage] = Field(default_factory=list, description="历史消息")
