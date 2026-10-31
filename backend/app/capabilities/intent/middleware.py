"""意图层挂在 LangChain 上的两个挂钩。

- **模型调用前**：按用户输入检索出相关技能，把工具收敛到候选集 ——
  工具全量暴露会导致 prompt 爆 + 模型选错；同时把本次的策略上下文（信任模式）
  告诉模型。
- **工具调用前**：把这次 tool call 翻译成 `CapabilityIntent`，交给执行层走
  流水线；不再直接落到工具函数上。

第二条是这套改造的关键：早先这里直接 `handler(request)`，工具函数被 LangChain
直接调用，**绕开了执行层** —— 于是没有日志、没有超时、没有重试、没有策略检查。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.intent.resolver import find_spec, resolve
from app.capabilities.registration.spec import CapabilitySpec

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注
    from app.capabilities.execution.context import ExecutionContext


class CapabilityMiddleware(AgentMiddleware):
    """意图层的两个挂钩。"""

    def __init__(
        self,
        specs: list[CapabilitySpec],
        allowed_tool_keys: set[str] | None = None,
        context: ExecutionContext | None = None,
        thread_id: str = "",
        trust_mode: str = "balanced",
    ) -> None:
        self.specs = specs
        self.allowed_tool_keys = allowed_tool_keys
        self.context = context
        self.thread_id = thread_id
        self.trust_mode = trust_mode

    async def awrap_model_call(self, request: ModelRequest, handler):
        overrides: dict[str, Any] = {}
        if self.allowed_tool_keys is not None:
            kept = [
                tool
                for tool in request.tools
                if getattr(tool, "name", None) in self.allowed_tool_keys
            ]
            if kept:
                # 一个都没匹配上就别收窄，保留原样，避免把 agent 关死
                overrides["tools"] = kept

        note = self._policy_note()
        if note:
            base = request.system_message.text if request.system_message else ""
            overrides["system_message"] = SystemMessage(content=f"{base}\n\n{note}".strip())

        if not overrides:
            return await handler(request)
        return await handler(request.override(**overrides))

    async def awrap_tool_call(self, request, handler):
        tool_call = request.tool_call or {}
        name = tool_call.get("name")
        spec = find_spec(self.specs, str(name or ""))
        if spec is None or self.context is None:
            # 不在清单里的能力（理论上不会有）交给原路径
            return await handler(request)

        try:
            intent = resolve(
                tool_call,
                self.specs,
                thread_id=self.thread_id,
                workspace=self.context.workspace,
                trust_mode=self.trust_mode,
                allowed_keys=self.allowed_tool_keys,
            )
            # 延迟导入：意图层是执行层的上游，模块级互相 import 会成环
            from app.capabilities.execution.pipeline import execute

            result = await execute(intent, self.context)
        except CapabilityError as exc:
            return ToolMessage(
                content=exc.to_text(),
                tool_call_id=str(tool_call.get("id") or ""),
                status="error",
            )

        return ToolMessage(
            content=self._render(result),
            tool_call_id=str(tool_call.get("id") or ""),
            status="error" if not result.ok else "success",
        )

    def _policy_note(self) -> str:
        """把本次的策略上下文告诉模型。"""
        notes = [f"当前信任模式：{self.trust_mode}。"]
        if self.trust_mode == "balanced":
            notes.append("读写类操作会直接执行；执行命令、联网抓取需要人工审批后才能继续。")
        elif self.trust_mode == "strict":
            notes.append("所有需要确认的操作都会挂起，等待人工审批。")
        elif self.trust_mode == "permissive":
            notes.append("只有破坏性操作需要人工审批。")
        return "\n".join(notes)

    @staticmethod
    def _render(result) -> str:
        """渲染给模型看的文本；待审批的调用要说清楚下一步该做什么。"""
        error = result.error
        if error is not None and error.code == ErrorCode.APPROVAL_REQUIRED:
            return (
                f"[{error.code}] {error.message}\n"
                f"（调用记录 #{result.invocation_id}，人工批准后会自动执行；"
                "请停止后续操作并告知用户需要确认）"
            )
        return result.to_text()


__all__ = ["CapabilityMiddleware"]
