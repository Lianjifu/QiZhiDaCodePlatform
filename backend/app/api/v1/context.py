"""上下文预览接口。

拼装结果看不见就没法排查 —— 这个接口返回"这次会给模型看什么"，以及
**哪些块被丢了、哪些被截断了**。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.agents.prompts import CODE_AGENT_PROMPT
from app.api.deps import get_db
from app.context.service import ContextRequest, build_context_service
from app.core.config import settings
from app.db.session import AsyncSession
from app.schemas.common import ResponseModel
from app.schemas.context import ContextBlockOut, ContextPreviewOut

router = APIRouter(prefix="/context", tags=["上下文"])


@router.get(
    "/preview",
    response_model=ResponseModel[ContextPreviewOut],
    summary="预览这次会给模型看的上下文",
)
async def preview_context(
    session: Annotated[AsyncSession, Depends(get_db)],
    query: Annotated[str, Query(description="用这句话召回记忆目录")] = "",
    thread_id: Annotated[str, Query(description="会话 ID")] = "",
    workspace: Annotated[str | None, Query(description="工作区路径")] = None,
) -> ResponseModel[ContextPreviewOut]:
    """预览一次上下文装配的结果。

    不跑模型、不改任何状态 —— 只看拼装。
    """
    memory_catalog = await _memory_catalog(session, thread_id, query)
    policy_note = await _policy_note(session)
    uploads_note = _uploads_note(thread_id)
    reflection_note = await _reflection_note(session, thread_id)
    service = build_context_service()
    result = service.assemble(
        ContextRequest(
            base_prompt=CODE_AGENT_PROMPT,
            workspace=Path(workspace) if workspace else None,
            memory_catalog=memory_catalog,
            reflection_note=reflection_note,
            policy_note=policy_note,
            uploads_note=uploads_note,
            context_window=settings.llm_context_window,
        )
    )
    return ResponseModel[ContextPreviewOut](
        data=ContextPreviewOut(
            text=result.text,
            budget_chars=result.budget_chars,
            used_chars=result.used_chars,
            included=[_block_out(block) for block in result.blocks],
            dropped=[_block_out(block) for block in result.dropped],
            truncated=[_block_out(block) for block in result.truncated],
            warnings=result.warnings,
        )
    )


async def _memory_catalog(session: AsyncSession, thread_id: str, query: str) -> str:
    """召回记忆目录（预览用）。失败不阻塞预览。"""
    if not query.strip():
        return ""
    from app.memory.long_term_store import LongTermMemoryStore
    from app.memory.service import MemoryService

    try:
        long_term = LongTermMemoryStore(session) if settings.memory_collection_name else None
        service = MemoryService(
            session=session,
            thread_id=thread_id,
            long_term_store=long_term,
        )
        return (await service.recall_catalog(query)).render()
    except Exception:  # pragma: no cover - 预览不该因为召回失败而报错
        return ""


async def _reflection_note(session: AsyncSession, thread_id: str) -> str:
    """反思记录（预览用）。失败不阻塞预览。"""
    from app.agents.reflection import build_reflection_block

    try:
        return await build_reflection_block(session, thread_id)
    except Exception:  # pragma: no cover - 预览不该因为反思查表失败而失败
        return ""


def _uploads_note(thread_id: str) -> str:
    """上传件清单（预览用）。

    会话工作区里用户传过的材料 —— agent 靠它知道有哪些文件可读。
    """
    if not thread_id:
        return ""
    from app.core.workspace import resolve_workspace
    from app.files.attachments import build_uploads_note

    try:
        workspace = resolve_workspace(thread_id)
    except Exception:  # pragma: no cover - thread_id 不合法时预览不该失败
        return ""
    return build_uploads_note(workspace, max_entries=settings.uploads_block_max_entries)


async def _policy_note(session: AsyncSession) -> str:
    """策略说明（预览用）。

    要能力清单才知道"哪些工具会要人批" —— 所以得查一次注册层。
    取不到就返回空串：预览不该因为能力清单出问题而失败。
    """
    from app.capabilities.intent.middleware import build_policy_note
    from app.capabilities.registration.registry import CapabilityRegistry

    try:
        specs = await CapabilityRegistry.load(session)
    except Exception:  # pragma: no cover - 预览不该因为注册层出错而失败
        return ""
    return build_policy_note(specs, settings.capability_trust_mode)


def _block_out(block: Any) -> ContextBlockOut:
    """上下文块 → 响应模型。"""
    return ContextBlockOut(
        key=block.key,
        title=block.title,
        priority=block.priority,
        chars=len(block.content),
        truncated=bool(block.notes),
    )


__all__ = ["router"]
