"""调用记录接口：查询与审批。

**审批有两个入口，别搞混**：

- agent 在跑，撞上了要人批的调用 →
  ``POST /api/v1/agent/{thread_id}/approve``（恢复 LangGraph 的图）
- 直接调 API（``POST /api/v1/capabilities/{key}/invoke``），能力要人批 →
  ``POST /api/v1/invocations/{id}/approve``（按记录里的参数执行这一次调用）

agent 路径的审批状态在 **LangGraph 的 checkpoint** 里，不在调用记录表里 ——
本文件的接口只服务"直接调用"那条路径。
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
    summary="批准并执行待审批的调用（直接调用路径；agent 路径用 /agent/{thread_id}/approve）",
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
    summary="拒绝待审批的调用（直接调用路径）",
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
    summary="取消待审批的调用（直接调用路径）",
)
async def cancel_invocation(
    invocation_id: int,
    service: ServiceDep,
) -> ResponseModel[InvocationOut]:
    record = await service.cancel(invocation_id)
    return ResponseModel[InvocationOut](data=record)
