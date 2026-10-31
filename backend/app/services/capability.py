"""能力层服务：统一能力清单的对外出口。

编排层和前端都从这里拿能力，不再各自去拼 tool / mcp / skill 三张表。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.adapter import get_adapter
from app.capabilities.context import ExecutionContext
from app.capabilities.entry import KIND_SKILL, KIND_TOOL, SOURCE_MCP, CapabilityEntry
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registry import CapabilityRegistry
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.execution_log import ExecutionLog
from app.repositories.mcp_server import MCPServerRepository
from app.schemas.capability import (
    CapabilityInvokeOut,
    CapabilityListOut,
    CapabilityOut,
    SkillPreviewOut,
)


class CapabilityService:
    """统一能力清单。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self) -> list[CapabilityEntry]:
        return await CapabilityRegistry.load(self.session)

    async def list_capabilities(
        self,
        *,
        kind: str | None = None,
        tag: str | None = None,
        enabled: bool | None = None,
    ) -> CapabilityListOut:
        entries = await self.load()
        if kind:
            entries = [entry for entry in entries if entry.kind == kind]
        if tag:
            entries = [entry for entry in entries if tag in entry.tags]
        if enabled is not None:
            entries = [entry for entry in entries if entry.enabled is enabled]
        return CapabilityListOut(
            items=[_to_out(entry) for entry in entries],
            total=len(entries),
            kinds=sorted({entry.kind for entry in entries}),
        )

    async def get_capability(self, key: str) -> CapabilityOut:
        entry = await CapabilityRegistry.get(self.session, key)
        if entry is None:
            raise NotFoundError(f"能力 {key} 不存在")
        return _to_out(entry)

    async def invoke(
        self,
        key: str,
        arguments: dict[str, Any] | None = None,
        thread_id: str | None = None,
        workspace: str | Path | None = None,
    ) -> CapabilityInvokeOut:
        """直接调一次能力（调试用）。"""
        entry = await CapabilityRegistry.get(self.session, key)
        if entry is None:
            raise NotFoundError(f"能力 {key} 不存在")
        if entry.kind != KIND_TOOL:
            raise ValidationError(f"{key} 不是可直接调用的原子能力")
        context = await self._build_context(workspace, [entry])
        result = await get_adapter(entry.source_type).invoke(entry, arguments or {}, context)
        await self._record(thread_id, entry, arguments or {}, result)
        return CapabilityInvokeOut(
            ok=result.ok,
            key=entry.key,
            content=result.content,
            error_code=result.error.code if result.error else None,
            error_message=result.error.message if result.error else None,
            retryable=result.error.retryable if result.error else False,
            duration_ms=result.duration_ms,
        )

    async def preview_skill(self, skill_id: int) -> SkillPreviewOut:
        """预览一个技能挂上去会带来什么。"""
        entries = await self.load()
        skill = next(
            (entry for entry in entries if entry.kind == KIND_SKILL and entry.db_id == skill_id),
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
        self, workspace: str | Path | None, entries: list[CapabilityEntry]
    ) -> ExecutionContext:
        """执行上下文：带上要用到的 MCP 供给通道。"""
        mcp_servers = {}
        repo = MCPServerRepository(self.session)
        for entry in entries:
            if entry.kind != KIND_TOOL or entry.source_type != SOURCE_MCP:
                continue
            name = entry.locator.get("server") or ""
            server = await repo.get_by_name(name)
            if server is not None and server.enabled:
                mcp_servers[server.name] = server
        return ExecutionContext.build(workspace or settings.workspace_root, mcp_servers)

    async def _record(
        self,
        thread_id: str | None,
        entry: CapabilityEntry,
        arguments: dict[str, Any],
        result,
    ) -> None:
        """写一条执行轨迹。"""
        self.session.add(
            ExecutionLog(
                thread_id=thread_id,
                capability_key=entry.key,
                arguments=_summarize(arguments),
                ok=result.ok,
                error_code=result.error.code if result.error else None,
                error_message=result.error.message if result.error else None,
                duration_ms=result.duration_ms,
            )
        )
        await self.session.commit()


def _to_out(entry: CapabilityEntry) -> CapabilityOut:
    return CapabilityOut(
        key=entry.key,
        kind=entry.kind,
        db_id=entry.db_id,
        display_name=entry.display_name,
        description=entry.description,
        tags=list(entry.tags),
        permission=entry.permission,
        enabled=entry.enabled,
        origin=entry.origin,
        version=entry.version,
        source_type=entry.source_type,
        parameters=dict(entry.parameters),
        locator=dict(entry.locator),
        members=list(entry.members),
        knowledge=entry.knowledge,
        checklist=list(entry.checklist),
        examples=list(entry.examples),
        constraints=entry.constraints,
    )


def _summarize(arguments: dict[str, Any]) -> dict[str, Any]:
    """参数摘要：长文本截断，避免轨迹表被大段代码撑爆。"""
    summarized: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and len(value) > 500:
            summarized[key] = value[:500] + f"... (共 {len(value)} 字符)"
        else:
            summarized[key] = value
    return summarized


__all__ = ["CapabilityService"]
