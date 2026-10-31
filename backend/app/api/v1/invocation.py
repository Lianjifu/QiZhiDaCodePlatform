"""调用记录接口：查询与审批。

审批流程在这里闭环：

    POST /api/v1/invocations/{id}/approve   批准并执行
    POST /api/v1/invocations/{id}/deny      拒绝
    POST /api/v1/invocations/{id}/cancel    取消
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query

from app.api.deps import get_invocation_service
from app.schemas.capability import (
    CapabilityInvokeOut,
    InvocationListOut,
    InvocationOut,
)
from app.schemas.common import ResponseModel
from app.services.invocation import InvocationService

router = APIRouter(prefix="/invocations", tags=["调用记录"])

ServiceDep = Annotated[InvocationService, Depends(get_invocation_service)]


@router.get("", response_model=ResponseModel[InvocationListOut], summary="调用记录分页")
async def list_invocations(
    service: ServiceDep,
    thread_id: Annotated[str | None, Query(description="按会话过滤")] = None,
    capability_key: Annotated[str | None, Query(description="按能力过滤")] = None,
    status: Annotated[str | None, Query(description="按状态过滤")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ResponseModel[InvocationListOut]:
    result = await service.list_invocations(
        thread_id=thread_id,
        capability_key=capability_key,
        status=status,
        offset=offset,
        limit=limit,
    )
    return ResponseModel[InvocationListOut](data=result)


@router.get("/{invocation_id}", response_model=ResponseModel[InvocationOut], summary="调用详情")
async def get_invocation(
    invocation_id: int,
    service: ServiceDep,
) -> ResponseModel[InvocationOut]:
    record = await service.get_invocation(invocation_id)
    return ResponseModel[InvocationOut](data=record)


@router.post(
    "/{invocation_id}/approve",
    response_model=ResponseModel[CapabilityInvokeOut],
    summary="批准并执行待审批的调用",
)
async def approve_invocation(
    invocation_id: int,
    service: ServiceDep,
) -> ResponseModel[CapabilityInvokeOut]:
    result = await service.approve(invocation_id)
    return ResponseModel[CapabilityInvokeOut](data=result)


@router.post(
    "/{invocation_id}/deny",
    response_model=ResponseModel[InvocationOut],
    summary="拒绝待审批的调用",
)
async def deny_invocation(
    invocation_id: int,
    service: ServiceDep,
    reason: Annotated[str | None, Body(embed=True)] = None,
) -> ResponseModel[InvocationOut]:
    record = await service.deny(invocation_id, reason or "")
    return ResponseModel[InvocationOut](data=record)


@router.post(
    "/{invocation_id}/cancel",
    response_model=ResponseModel[InvocationOut],
    summary="取消待审批的调用",
)
async def cancel_invocation(
    invocation_id: int,
    service: ServiceDep,
) -> ResponseModel[InvocationOut]:
    record = await service.cancel(invocation_id)
    return ResponseModel[InvocationOut](data=record)
