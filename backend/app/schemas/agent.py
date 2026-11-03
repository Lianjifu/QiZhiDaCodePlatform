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
    added: int | None = Field(
        default=None, description="这次改动新增了几行（改文件的工具才有）"
    )
    removed: int | None = Field(
        default=None, description="这次改动删掉了几行（改文件的工具才有）"
    )


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
    | ``error`` | ``error`` | 这一轮炸了，把原因显示出来 |

    ``type`` 默认 ``"text"`` —— 老的消费方按 ``{"text": ...}`` 解析仍然能用。
    """

    type: str = Field(
        default="text",
        description=(
            "事件类型：text / tool_call / tool_result / pending_approval / done / error"
        ),
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
    error: str | None = Field(
        default=None,
        description="失败原因（type=error 时有值）",
    )


class SlashCommandOut(BaseModel):
    """一条斜杠命令。

    两类：
    - ``prompt`` —— 把 ``template`` 填进输入框，用户改完再发；
    - ``action`` —— 前端自己执行 ``action`` 里的动作（换模型、开新会话）。
    """

    name: str = Field(description="命令名（不含斜杠）")
    description: str = Field(default="", description="一句话说明")
    kind: str = Field(default="prompt", description="prompt / action")
    template: str = Field(default="", description="prompt 类的提示词正文")
    action: str = Field(default="", description="action 类的动作名")


class AgentHistoryMessage(BaseModel):
    """历史消息里的一条。"""

    id: str | None = Field(default=None, description="消息 id（checkpoint 里的稳定 UUID）")
    role: str = Field(description="user / assistant / tool")
    content: str = Field(default="", description="消息正文")
    name: str | None = Field(default=None, description="工具消息的工具名")
    tool_call_id: str | None = Field(
        default=None, description="工具消息对应的 tool_call id（前端拿它对上工具块）"
    )
    tool_calls: list[ToolCallOut] = Field(default_factory=list, description="助手消息里的工具调用")
    added: int | None = Field(default=None, description="这次改动新增了几行")
    removed: int | None = Field(default=None, description="这次改动删掉了几行")


class AgentHistoryOut(BaseModel):
    """一个会话的历史消息。

    从 checkpoint 的状态里读回来的 —— 消息是**只读的**，前端拿它渲染列表。
    """

    thread_id: str = Field(description="会话 ID")
    messages: list[AgentHistoryMessage] = Field(default_factory=list, description="历史消息")


class AgentRegenerateIn(BaseModel):
    """重新生成的参数。

    **不需要传消息** —— 用的是历史里最后一条用户消息。
    """

    provider_id: int | None = Field(default=None, description="用哪个供应商的模型")
    skill_ids: list[int] = Field(default_factory=list, description="这一轮挂哪些技能")


class AgentForkIn(BaseModel):
    """从某条消息分支出新会话。"""

    message_id: str = Field(
        min_length=1,
        max_length=64,
        description="分支点的消息 id（历史里的 message.id）—— 分支出来的会话保留到这条为止",
    )


class AgentForkOut(BaseModel):
    """分支的结果。"""

    thread_id: str = Field(description="新会话 ID")
    source_thread_id: str = Field(default="", description="从哪个会话分出来的")
    message_id: str = Field(default="", description="分支点的消息 id")
    kept_messages: int = Field(default=0, description="新会话里保留了几条消息")


# ---------- 会话状态 ----------


class ContextPartOut(BaseModel):
    """上下文占用里的一块。"""

    key: str = Field(description="块的标识")
    label: str = Field(description="显示名")
    tokens: int = Field(default=0, description="占用多少 token（估算）")
    percent: float = Field(default=0, description="占整个窗口的百分比")
    items: list[str] = Field(
        default_factory=list,
        description="这一块具体是什么（可展开的明细，如每个 MCP 工具、每个记忆文件）",
    )


class ContextUsageOut(BaseModel):
    """上下文窗口用了多少、都被谁占了。"""

    limit: int = Field(default=0, description="上下文窗口（token）")
    used: int = Field(default=0, description="已占用（token，估算，不含压缩缓冲）")
    percent: float = Field(default=0, description="占用百分比")
    reserved: int = Field(default=0, description="压缩缓冲（预留出来放摘要的，不算占用）")
    free: int = Field(default=0, description="还能用多少（token）")
    parts: list[ContextPartOut] = Field(default_factory=list, description="占用明细")


class EnvironmentOut(BaseModel):
    """这次会话跑在什么环境里。"""

    thread_id: str = Field(default="", description="会话 ID")
    workspace: str = Field(default="", description="工作区路径")
    model: str = Field(default="", description="用的模型")
    provider: str = Field(default="", description="模型来自哪个供应商")
    trust_mode: str = Field(default="", description="信任模式")


class OutputFileOut(BaseModel):
    """会话里产出或上传的一个文件。"""

    path: str = Field(description="虚拟路径")
    size: int = Field(default=0, description="字节数")
    is_dir: bool = Field(default=False, description="是不是目录")
    uploaded: bool = Field(default=False, description="是不是用户上传的")


class ChangesOut(BaseModel):
    """这个会话一共改了多少行。"""

    added: int = Field(default=0, description="新增行数")
    removed: int = Field(default=0, description="删除行数")


class ConnectorOut(BaseModel):
    """一个接进来的外部东西（模型供应商、MCP 服务器）。"""

    name: str = Field(description="名字")
    kind: str = Field(default="", description="provider / mcp")
    status: str = Field(default="", description="ok / disabled / error")
    detail: str = Field(default="", description="补充说明")


class RunningTaskOut(BaseModel):
    """一个正在跑的工具调用。"""

    tool: str = Field(default="", description="工具名")
    detail: str = Field(default="", description="在干什么（命令、路径…）")
    elapsed_ms: int = Field(default=0, description="已经跑了多久（毫秒）")


class WorktreeOut(BaseModel):
    """一个 worktree（独立检出）。"""

    path: str = Field(description="worktree 目录")
    branch: str = Field(default="", description="分支")
    head: str = Field(default="", description="HEAD 的短 sha")
    kind: str = Field(default="worktree", description="repo（主仓库）/ worktree（独立检出）")
    repo: str | None = Field(default=None, description="所属的主仓库")
    dirty: bool = Field(default=False, description="有没有未提交的改动")


class CollaborationOut(BaseModel):
    """协作状态：会话绑在哪个目录、有哪些 worktree 与分支。

    派出去的子 agent 会留下分支 —— 它们**不会自动合回主线**，由人决定。
    """

    bound: bool = Field(default=False, description="有没有绑定工作目录")
    path: str = Field(default="", description="绑定的目录")
    kind: str = Field(default="", description="folder / worktree")
    branch: str = Field(default="", description="绑定的目录当前分支")
    repo: str | None = Field(default=None, description="所属的主仓库")
    worktrees: list[WorktreeOut] = Field(default_factory=list, description="仓库里的 worktree")
    branches: list[str] = Field(default_factory=list, description="仓库里的分支")


class SessionStatusOut(BaseModel):
    """一个会话的当前状态。

    给前端那个状态面板用 —— 环境、上下文占用、产出、改动量、接进来的东西。
    """

    thread_id: str = Field(description="会话 ID")
    environment: EnvironmentOut = Field(default_factory=EnvironmentOut)
    context: ContextUsageOut = Field(default_factory=ContextUsageOut)
    outputs: list[OutputFileOut] = Field(default_factory=list, description="产出与上传件")
    changes: ChangesOut = Field(default_factory=ChangesOut)
    connectors: list[ConnectorOut] = Field(default_factory=list, description="接进来的东西")
    collaboration: CollaborationOut = Field(default_factory=CollaborationOut, description="协作状态")
    tasks: list[RunningTaskOut] = Field(default_factory=list, description="正在跑的工具调用")
    finished_tasks: int = Field(default=0, description="这个会话跑完过多少个工具调用")
    updated_at: str = Field(default="", description="这份状态是什么时候读的")
