"""记忆管理接口。

给"人"用的 —— agent 写错了记忆要能看见、能删。

⚠️ **当前没有鉴权**（整个平台都没做，见项目的鉴权决策）。所以这里的接口
**任何人都能调**：能读别人的会话内记忆（只要知道 thread_id）、能读长期记忆
集合里的全部内容、能往里塞东西。鉴权补上之前，这个接口不要暴露到公网。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_memory_service
from app.core.exceptions import NotFoundError
from app.memory.models import SCOPE_SESSION
from app.memory.service import MemoryService
from app.schemas.common import PageMeta, ResponseModel
from app.schemas.memory import MemoryCatalogOut, MemoryListOut, MemoryOut

router = APIRouter(prefix="/memory", tags=["记忆"])

ServiceDep = Annotated[MemoryService, Depends(get_memory_service)]


@router.get("", response_model=ResponseModel[MemoryListOut], summary="查询记忆")
async def list_memories(
    service: ServiceDep,
    scope: Annotated[
        str, Query(description="session（会话内）/ long_term（跨会话）")
    ] = SCOPE_SESSION,
    thread_id: Annotated[str, Query(description="会话 id（scope=session 时必填）")] = "",
    limit: Annotated[int, Query(ge=1, le=200, description="最多返回多少条")] = 50,
) -> ResponseModel[MemoryListOut]:
    """列出记忆。

    会话内记忆按 ``thread_id`` 过滤 —— 没有它就什么都看不到（不是返回全部）。
    """
    if scope == SCOPE_SESSION and not thread_id:
        return ResponseModel[MemoryListOut](
            message="scope=session 时需要提供 thread_id",
            data=MemoryListOut(items=[], meta=PageMeta(total=0, page=1, page_size=limit)),
        )
    scoped = service.for_thread(thread_id) if thread_id else service
    entries = await scoped.list_all(scope=scope, limit=limit)
    return ResponseModel[MemoryListOut](
        data=MemoryListOut(
            items=[MemoryOut.model_validate(item.to_dict()) for item in entries],
            meta=PageMeta(total=len(entries), page=1, page_size=limit),
        )
    )


@router.get("/catalog", response_model=ResponseModel[MemoryCatalogOut], summary="预览记忆目录")
async def preview_catalog(
    service: ServiceDep,
    query: Annotated[str, Query(description="检索词")] = "",
    thread_id: Annotated[str, Query(description="会话 id")] = "",
) -> ResponseModel[MemoryCatalogOut]:
    """预览这一轮会注入上下文的记忆目录。

    用来调预算、调召回策略 —— 不用真跑一轮对话就能看到目录长什么样。
    """
    scoped = service.for_thread(thread_id) if thread_id else service
    catalog = await scoped.recall_catalog(query)
    return ResponseModel[MemoryCatalogOut](
        data=MemoryCatalogOut(
            content=catalog.render(),
            total_found=catalog.total_found,
            truncated=catalog.truncated,
            entries=[],
        )
    )


@router.get("/{item_id}", response_model=ResponseModel[MemoryOut], summary="查询记忆详情")
async def get_memory(
    item_id: str,
    service: ServiceDep,
) -> ResponseModel[MemoryOut]:
    """取一条记忆的全文。

    **按 id 跨会话找** —— 路径里只有 id，没有会话。
    记忆 id 全局唯一（``s:12`` / ``l:3``），按它就能定位到那一条。
    """
    entry = await service.read_any(item_id)
    if entry is None:
        raise NotFoundError(f"记忆 {item_id} 不存在")
    return ResponseModel[MemoryOut](data=MemoryOut.model_validate(entry.to_dict()))


@router.delete("/{item_id}", summary="删除记忆")
async def delete_memory(
    item_id: str,
    service: ServiceDep,
) -> ResponseModel[dict]:
    """删除一条记忆。

    agent 记错了要能删 —— 这是"写入后用户能一句话纠错"的兜底。
    """
    deleted = await service.forget_any(item_id)
    if not deleted:
        raise NotFoundError(f"记忆 {item_id} 不存在")
    return ResponseModel[dict](message="已删除", data={"id": item_id})


@router.delete("/thread/{thread_id}", summary="删除一个会话的全部短期记忆")
async def delete_thread_memories(
    thread_id: str,
    service: ServiceDep,
) -> ResponseModel[dict]:
    """删掉某个会话的全部短期记忆。

    **会话被删时调用** —— 短期记忆与对话历史同生共死。长期记忆不受影响。
    """
    count = await service.forget_thread(thread_id)
    return ResponseModel[dict](
        message=f"已删除 {count} 条会话内记忆",
        data={"thread_id": thread_id, "deleted": count},
    )
