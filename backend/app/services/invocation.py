"""调用记录服务：审批与查询。

审批是**两阶段**调用的后半段：

    调用 → 生成一条 pending 的调用记录 → 返回"等待审批"
                                          ↓
                            人工批准 ─┬─ approved → 按记录里的参数执行
                                     └─ denied   → 不执行

批准后直接由这里执行，不依赖 agent 还活着 —— 所以不需要把 agent 挂起等审批
（那是 P5c 才做的事）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.pipeline import execute
from app.capabilities.execution.redaction import redact
from app.capabilities.intent.intent import CapabilityIntent, compute_idempotency_key
from app.capabilities.registration.registry import CapabilityRegistry
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.invocation import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_DENIED,
    STATUS_PENDING,
    Invocation,
)
from app.repositories.invocation import InvocationRepository
from app.repositories.mcp_server import MCPServerRepository
from app.schemas.capability import (
    CapabilityInvokeOut,
    InvocationListOut,
    InvocationOut,
)


class InvocationService:
    """调用记录的查询与审批。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_invocations(
        self,
        *,
        thread_id: str | None = None,
        capability_key: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> InvocationListOut:
        repo = InvocationRepository(self.session)
        items = await repo.list_page(
            thread_id=thread_id,
            capability_key=capability_key,
            status=status,
            offset=offset,
            limit=limit,
        )
        total = await repo.count_page(
            thread_id=thread_id, capability_key=capability_key, status=status
        )
        pending = await repo.count_page(status=STATUS_PENDING)
        return InvocationListOut(
            items=[_to_out(item) for item in items],
            total=total,
            pending=pending,
        )

    async def get_invocation(self, invocation_id: int) -> InvocationOut:
        record = await self.session.get(Invocation, invocation_id)
        if record is None:
            raise NotFoundError(f"调用记录 {invocation_id} 不存在")
        return _to_out(record)

    async def approve(self, invocation_id: int) -> CapabilityInvokeOut:
        """批准并执行一条待审批的调用。"""
        record = await self.session.get(Invocation, invocation_id)
        if record is None:
            raise NotFoundError(f"调用记录 {invocation_id} 不存在")
        if record.status != STATUS_PENDING:
            raise ValidationError(
                f"调用记录 {invocation_id} 当前状态是 {record.status}，只有待审批的才能批准"
            )

        specs = await CapabilityRegistry.load(self.session)
        spec = next((item for item in specs if item.key == record.capability_key), None)
        if spec is None:
            raise NotFoundError(f"能力 {record.capability_key} 不存在")

        context = await self._build_context(spec, record.thread_id)
        context.thread_id = record.thread_id or ""
        arguments = dict(record.payload or {})
        intent = CapabilityIntent(
            capability_key=spec.key,
            arguments=arguments,
            spec=spec,
            thread_id=record.thread_id or "",
            workspace=context.workspace,
            trust_mode=settings.capability_trust_mode,
            idempotency_key=compute_idempotency_key(spec, arguments, record.thread_id or ""),
            pre_approved=True,
        )
        result = await execute(intent, context)

        # 审批动作本身也留痕：这条记录标记为"已批准"，
        # 真正执行的结果由执行层另写一条调用记录。
        # 但**执行结果要回填到这条记录上** —— 审计时一眼能看到"这条挂起的
        # 调用最后跑成什么样了"。
        record.status = STATUS_APPROVED
        record.decided_at = datetime.now(UTC)
        record.finished_at = datetime.now(UTC)
        record.decision_reason = (record.decision_reason or "") + "；已人工批准"
        record.result = redact(result.to_dict())
        record.error_code = result.error.code.value if result.error else None
        record.error_message = result.error.message if result.error else None
        record.duration_ms = result.duration_ms
        record.stages = list(result.stages or [])
        await self.session.commit()

        return CapabilityInvokeOut(
            ok=result.ok,
            key=spec.key,
            content=result.content,
            error_code=result.error.code.value if result.error else None,
            error_message=result.error.message if result.error else None,
            retryable=result.error.retryable if result.error else False,
            duration_ms=result.duration_ms,
            invocation_id=result.invocation_id,
            status="succeeded" if result.ok else "failed",
        )

    async def deny(self, invocation_id: int, reason: str = "") -> InvocationOut:
        """拒绝一条待审批的调用。"""
        record = await self._session_get_pending(invocation_id)
        record.status = STATUS_DENIED
        record.decision_reason = reason or "人工拒绝"
        record.decided_at = datetime.now(UTC)
        record.finished_at = datetime.now(UTC)
        await self.session.commit()
        return _to_out(record)

    async def cancel(self, invocation_id: int) -> InvocationOut:
        """取消一条待审批的调用。"""
        record = await self._session_get_pending(invocation_id)
        record.status = STATUS_CANCELLED
        record.decision_reason = "人工取消"
        record.decided_at = datetime.now(UTC)
        record.finished_at = datetime.now(UTC)
        await self.session.commit()
        return _to_out(record)

    async def _session_get_pending(self, invocation_id: int) -> Invocation:
        record = await self.session.get(Invocation, invocation_id)
        if record is None:
            raise NotFoundError(f"调用记录 {invocation_id} 不存在")
        if record.status != STATUS_PENDING:
            raise ValidationError(
                f"调用记录 {invocation_id} 当前状态是 {record.status}，只有待审批的才能操作"
            )
        return record

    async def _build_context(self, spec, thread_id: str | None) -> ExecutionContext:
        """执行上下文：带上要用到的 MCP 供给通道。

        工作区按会话还原（和 `AgentService.resolve_workspace` 同一套规则）——
        批准后执行的必须是**原来那个工作区**，否则文件操作会落到别处。
        """
        mcp_servers = {}
        if spec.source_type == "mcp":
            repo = MCPServerRepository(self.session)
            name = spec.locator.get("server") or ""
            server = await repo.get_by_name(name)
            if server is not None and server.enabled:
                mcp_servers[server.name] = server
        context = ExecutionContext.build(self._resolve_workspace(thread_id), mcp_servers)
        context.session = self.session
        return context

    @staticmethod
    def _resolve_workspace(thread_id: str | None) -> Path:
        """会话 → 工作区目录（与 AgentService 保持一致）。"""
        base = Path(settings.workspace_root).resolve()
        if not thread_id:
            return base
        safe = "".join(ch for ch in thread_id if ch.isalnum() or ch in "-_")[:64]
        if not safe:
            return base
        workspace = base / safe
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace


def _to_out(record: Invocation) -> InvocationOut:
    return InvocationOut(
        id=record.id,
        thread_id=record.thread_id,
        capability_key=record.capability_key,
        arguments=dict(record.arguments or {}),
        status=record.status,
        risk=record.risk,
        permission=record.permission,
        decision_reason=record.decision_reason,
        stages=list(record.stages or []),
        result=dict(record.result or {}),
        error_code=record.error_code,
        error_message=record.error_message,
        duration_ms=record.duration_ms,
        created_at=record.created_at.isoformat() if record.created_at else "",
        decided_at=record.decided_at.isoformat() if record.decided_at else None,
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
    )


__all__ = ["InvocationService"]
