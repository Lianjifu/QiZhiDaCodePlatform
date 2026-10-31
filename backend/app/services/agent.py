"""Agent 服务：平台侧的业务入口，负责工作区分配与 agent 生命周期。

分层职责：
- `app/agents/` 只管怎么跑（模型 + 工具 + 循环）；
- 这里管"给这次会话分配哪个工作区""上下文怎么存""错误怎么翻译成 HTTP"。
"""

from collections.abc import AsyncIterator
from pathlib import Path

from app.agents.checkpointer import open_checkpointer
from app.agents.runner import AgentRunner, AgentRunResult
from app.core.config import settings
from app.core.exceptions import ValidationError


class AgentService:
    """编码 agent 的业务服务。"""

    def __init__(self, runner: AgentRunner | None = None) -> None:
        self._runner = runner

    @property
    def workspace_base(self) -> Path:
        return Path(settings.workspace_root).resolve()

    def resolve_workspace(self, thread_id: str) -> Path:
        """每个会话一个独立工作区，目录名做安全过滤。"""
        safe = "".join(ch for ch in thread_id if ch.isalnum() or ch in "-_")[:64]
        if not safe:
            raise ValidationError("thread_id 不能为空")
        workspace = self.workspace_base / safe
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    async def chat(self, message: str, thread_id: str) -> AgentRunResult:
        """跑一轮对话，返回最终回复。"""
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            runner = self._runner or AgentRunner(checkpointer=checkpointer)
            return await runner.run(message, thread_id, workspace)

    async def stream(self, message: str, thread_id: str) -> AsyncIterator[str]:
        """流式跑一轮对话，逐段产出文本增量。"""
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            runner = self._runner or AgentRunner(checkpointer=checkpointer)
            async for text in runner.stream(message, thread_id, workspace):
                yield text
