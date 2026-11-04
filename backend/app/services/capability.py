"""能力层服务：统一能力清单的对外出口。

编排层和前端都从这里拿能力，不再各自去拼 tool / mcp / skill 三张表。

执行走**执行层流水线**（`app.capabilities.execution.pipeline`），与 agent 内部
的调用同一条路径 —— 所以这里不需要自己写日志、留痕。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.permissions import resolve_permission_mode
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.pipeline import execute
from app.capabilities.intent.resolver import resolve
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registration.registry import CapabilityRegistry
from app.capabilities.registration.spec import (
    KIND_SKILL,
    KIND_TOOL,
    SOURCE_MCP,
    CapabilitySpec,
)
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.workspace import resolve_workspace
from app.repositories.mcp_server import MCPServerRepository
from app.schemas.capability import (
    CapabilityInvokeOut,
    CapabilityListOut,
    CapabilityOut,
    CapabilitySpecOut,
    SkillPreviewOut,
)

# 说明：调用记录由执行层统一写（`app.capabilities.execution.pipeline`），
# 这里不再单独记日志 —— 早先只有"直接调用"这条路会记，agent 内部调用漏了。


class CapabilityService:
    """统一能力清单。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self) -> list[CapabilitySpec]:
        return await CapabilityRegistry.load(self.session)

    async def list_capabilities(
        self,
        *,
        kind: str | None = None,
        tag: str | None = None,
        enabled: bool | None = None,
    ) -> CapabilityListOut:
        specs = await self.load()
        if kind:
            specs = [spec for spec in specs if spec.kind == kind]
        if tag:
            specs = [spec for spec in specs if tag in spec.tags]
        if enabled is not None:
            specs = [spec for spec in specs if spec.enabled == enabled]
        return CapabilityListOut(
            items=[_to_out(spec) for spec in specs],
            total=len(specs),
            kinds=sorted({spec.kind for spec in specs}),
        )

    async def get_capability(self, key: str) -> CapabilityOut:
        spec = await CapabilityRegistry.get(self.session, key)
        if spec is None:
            raise NotFoundError(f"能力 {key} 不存在")
        return _to_out(spec)

    async def get_spec(self, key: str) -> CapabilitySpecOut:
        """能力的完整声明：契约、风险、权限、策略。"""
        spec = await CapabilityRegistry.get(self.session, key)
        if spec is None:
            raise NotFoundError(f"能力 {key} 不存在")
        return _to_spec_out(spec)

    async def invoke(
        self,
        key: str,
        arguments: dict[str, Any] | None = None,
        thread_id: str | None = None,
        workspace: str | Path | None = None,
    ) -> CapabilityInvokeOut:
        """直接调一次能力（调试用）。"""
        spec = await CapabilityRegistry.get(self.session, key)
        if spec is None:
            raise NotFoundError(f"能力 {key} 不存在")
        if spec.kind != KIND_TOOL:
            raise ValidationError(f"{key} 不是可直接调用的原子能力")

        specs = await self.load()
        context = await self._build_context(workspace, [spec], thread_id)
        context.thread_id = thread_id or ""
        # 参数校验交给执行层（权威校验）：失败会带着 SCHEMA_INVALID 落进调用记录，
        # 而不是在这里抛出去变成 500
        intent = resolve(
            {"name": key, "args": arguments or {}},
            specs,
            thread_id=thread_id or "",
            workspace=context.workspace,
            # **会话级权限模式** —— 与 agent 路径同一份口径。
            # 读全局配置的话，用户在会话里选了「手动」，直接调接口却还是全局那档。
            trust_mode=resolve_permission_mode(thread_id or ""),
            validate=False,
        )
        result = await execute(intent, context)
        await self.session.commit()
        return CapabilityInvokeOut(
            ok=result.ok,
            key=key,
            content=result.content,
            error_code=result.error.code.value if result.error else None,
            error_message=result.error.message if result.error else None,
            retryable=result.error.retryable if result.error else False,
            duration_ms=result.duration_ms,
            invocation_id=result.invocation_id,
            status="pending" if _is_pending(result) else ("succeeded" if result.ok else "failed"),
            confidence=result.confidence,
            blocked=result.blocked,
        )

    async def preview_skill(self, skill_id: int) -> SkillPreviewOut:
        """预览一个技能挂上去会带来什么。"""
        specs = await self.load()
        skill = next(
            (spec for spec in specs if spec.kind == KIND_SKILL and spec.db_id == skill_id),
            None,
        )
        if skill is None:
            raise NotFoundError(f"技能 {skill_id} 不存在")
        prompt = assemble_prompt("", [skill])
        return SkillPreviewOut(
            skill=skill.key,
            knowledge=skill.knowledge,
            checklist=list(skill.checklist),
            constraints=skill.constraints,
            members=list(skill.members),
            prompt=prompt,
        )

    async def _build_context(
        self,
        workspace: str | Path | None,
        specs: list[CapabilitySpec],
        thread_id: str | None = None,
    ) -> ExecutionContext:
        """执行上下文：带上要用到的 MCP 供给通道。"""
        mcp_servers = {}
        repo = MCPServerRepository(self.session)
        for spec in specs:
            if spec.kind != KIND_TOOL or spec.source_type != SOURCE_MCP:
                continue
            name = spec.locator.get("server") or ""
            server = await repo.get_by_name(name)
            if server is not None and server.enabled:
                mcp_servers[server.name] = server
        # 落点：**会话的工作区**，不是平台工作区根目录。
        # 根目录是所有会话目录的父目录 —— 在那儿执行命令，
        # 一条 `rm -rf` 就能删掉**别的会话**的工作区。
        if workspace:
            target = Path(workspace)
        elif thread_id:
            target = resolve_workspace(thread_id)
        else:
            target = Path(settings.workspace_root)
        context = ExecutionContext.build(target, mcp_servers)
        context.session = self.session
        return context


def _is_pending(result) -> bool:
    return result.error is not None and result.error.code.value == "APPROVAL_REQUIRED"


def _to_out(spec: CapabilitySpec) -> CapabilityOut:
    return CapabilityOut(
        key=spec.key,
        kind=spec.kind,
        db_id=spec.db_id,
        display_name=spec.display_name,
        description=spec.description,
        tags=list(spec.tags),
        permission=spec.permission,
        enabled=spec.enabled,
        origin=spec.origin,
        version=spec.version,
        source_type=spec.source_type,
        parameters=dict(spec.input_schema),
        output_schema=dict(spec.output_schema) if spec.output_schema else None,
        risk=spec.risk,
        timeout_s=spec.timeout_s,
        retry_policy=spec.retry.to_dict(),
        annotations=spec.annotations.to_dict(),
        locator=dict(spec.locator),
        members=list(spec.members),
        knowledge=spec.knowledge,
        checklist=list(spec.checklist),
        examples=list(spec.examples),
        constraints=spec.constraints,
    )


def _to_spec_out(spec: CapabilitySpec) -> CapabilitySpecOut:
    return CapabilitySpecOut(
        key=spec.key,
        kind=spec.kind,
        display_name=spec.display_name,
        description=spec.description,
        enabled=spec.enabled,
        source_type=spec.source_type,
        input_schema=dict(spec.input_schema),
        output_schema=dict(spec.output_schema) if spec.output_schema else None,
        risk=spec.risk,
        permission=spec.permission,
        timeout_s=spec.timeout_s,
        retry_policy=spec.retry.to_dict(),
        annotations=spec.annotations.to_dict(),
        trust_mode=settings.capability_trust_mode,
    )


__all__ = ["CapabilityService"]
