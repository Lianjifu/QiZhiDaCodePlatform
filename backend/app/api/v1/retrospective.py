"""复盘记录接口：给人看"agent 自己改了什么"，以及撤销。

⚠️ **当前没有鉴权**（整个平台都没做，见项目的鉴权决策）。所以这里的接口
**任何人都能调**：能读全部复盘记录，能撤销别人的改进。

**撤销接口会写平台状态**，风险比只读接口高一档 —— 它能让 agent 的提示词
规则失效、技能的知识少一段、记忆少一条。鉴权补上之前，这个接口不要暴露
到公网。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_retrospective_service
from app.core.exceptions import NotFoundError
from app.models.retrospective import (
    CARRIER_MEMORY,
    CARRIER_PROMPT,
    CARRIER_SKILL,
    STATUS_APPLIED,
)
from app.schemas.common import PageMeta, ResponseModel
from app.schemas.retrospective import (
    RetrospectiveListOut,
    RetrospectiveMetricsOut,
    RetrospectiveOut,
    RevertOut,
)
from app.services.retrospective import RetrospectiveService

router = APIRouter(prefix="/retrospectives", tags=["复盘"])

ServiceDep = Annotated[RetrospectiveService, Depends(get_retrospective_service)]


@router.get("", response_model=ResponseModel[RetrospectiveListOut], summary="查询复盘记录")
async def list_retrospectives(
    service: ServiceDep,
    thread_id: Annotated[str, Query(description="会话 id")] = "",
    status: Annotated[str, Query(description="按状态筛")] = "",
    carrier: Annotated[str, Query(description="按载体筛：memory/skill/prompt")] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel[RetrospectiveListOut]:
    """列出复盘记录。

    **新的在前** —— 最近发生了什么最要紧。
    """
    items = await service.repo.list_page(
        thread_id=thread_id or None,
        status=status or None,
        carrier=carrier or None,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    total = await service.repo.count_page(
        thread_id=thread_id or None,
        status=status or None,
        carrier=carrier or None,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return ResponseModel[RetrospectiveListOut](
        data=RetrospectiveListOut(
            items=[RetrospectiveOut.model_validate(item.to_dict()) for item in items],
            meta=PageMeta(total=total, page=page, page_size=page_size, pages=pages),
        )
    )


@router.get(
    "/metrics",
    response_model=ResponseModel[RetrospectiveMetricsOut],
    summary="复盘这条链路的度量",
)
async def retrospective_metrics(
    service: ServiceDep,
    thread_id: Annotated[str, Query(description="会话 id")] = "",
) -> ResponseModel[RetrospectiveMetricsOut]:
    """复盘跑了多少、改了什么、有没有用。

    **复发率**是"改进有没有用"的唯一依据：某次复盘涉及的能力，在它**之前**
    失败过几次、**之后**又失败了几次 —— 后者比前者少，才说明改进起作用了。
    """
    metrics = await service.metrics(thread_id=thread_id or None)
    return ResponseModel[RetrospectiveMetricsOut](data=RetrospectiveMetricsOut(**metrics))


@router.get(
    "/{record_id}",
    response_model=ResponseModel[RetrospectiveOut],
    summary="查询复盘详情",
)
async def get_retrospective(
    record_id: int,
    service: ServiceDep,
) -> ResponseModel[RetrospectiveOut]:
    """取一条复盘记录。"""
    record = await service.repo.get(record_id)
    if record is None:
        raise NotFoundError(f"复盘记录 {record_id} 不存在")
    return ResponseModel[RetrospectiveOut](
        data=RetrospectiveOut.model_validate(record.to_dict())
    )


@router.post(
    "/{record_id}/revert",
    response_model=ResponseModel[RevertOut],
    summary="撤销这次复盘做的改动",
)
async def revert_retrospective(
    record_id: int,
    service: ServiceDep,
) -> ResponseModel[RevertOut]:
    """撤销。

    **免审批的兜底** —— 平台不让人先看一眼再改，那事后就得能撤。
    记忆删掉、技能追加的那段删回去、提示词规则停用。
    """
    reverted, message = await service.revert(record_id)
    return ResponseModel[RevertOut](
        message=message,
        data=RevertOut(id=record_id, reverted=reverted, message=message),
    )


__all__ = [
    "CARRIER_MEMORY",
    "CARRIER_PROMPT",
    "CARRIER_SKILL",
    "STATUS_APPLIED",
    "router",
]
