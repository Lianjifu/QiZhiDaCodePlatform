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
        """渲染给模型看的文本。

        按失败类型分层给不同的指令 —— 模型知道该怎么反应，任务才不会空转：

        - **格式类**（参数没按 schema 填）：让它改参数重试，但有次数上限；
        - **语义类**（参数合法但意图错了）：同样回灌，但会拉低任务置信度；
        - **权限类**：明确不许重试，直接走权限流程；
        - **阻塞/熔断**：让它停下来转人工，别再撞。
        """
        error = result.error
        if error is None:
            return result.to_text()

        if error.code == ErrorCode.APPROVAL_REQUIRED:
            return (
                f"[{error.code}] {error.message}\n"
                f"（调用记录 #{result.invocation_id}，人工批准后会自动执行；"
                "请停止后续操作并告知用户需要确认）"
            )
        if result.blocked:
            # 这次调用本身可能成功了/失败了，但任务已经越过阻塞线 ——
            # 错误码还是原来的（要留着算置信度），但要明确告诉模型该停
            return (
                f"[{error.code}] {error.message}\n"
                "（任务已阻塞：重试次数或任务置信度已到阈值；"
                "请停止重试并告知用户需要人工介入）"
            )
        if error.code == ErrorCode.TASK_BLOCKED:
            return (
                f"[{error.code}] {error.message}\n（任务已阻塞，请停止重试并告知用户需要人工介入）"
            )
        if error.code == ErrorCode.CIRCUIT_OPEN:
            return (
                f"[{error.code}] {error.message}\n"
                "（该能力近期连续失败，已熔断；请换一条路，别再重复调用）"
            )
        if error.code in (ErrorCode.PERMISSION_DENIED, ErrorCode.RISK_BLOCKED):
            return (
                f"[{error.code}] {error.message}\n"
                "（权限/风险判定不通过，不允许重复调用；请停止并告知用户）"
            )
        if error.code == ErrorCode.SCHEMA_INVALID:
            return (
                f"[{error.code}] {error.message}\n"
                "（参数格式不对，请按上面的要求改正后重试；连续多次不对会转人工）"
            )
        if error.code in (ErrorCode.INVALID_ARGUMENTS, ErrorCode.EXECUTION_ERROR):
            confidence = (
                f"，任务置信度降到 {result.confidence:.0f}" if result.confidence is not None else ""
            )
            return (
                f"[{error.code}] {error.message}\n"
                f"（这次调用没成功{confidence}；"
                "请检查参数是否与实际目标一致后重试，不要重复同样的调用）"
            )
        return result.to_text()


__all__ = ["CapabilityMiddleware"]
