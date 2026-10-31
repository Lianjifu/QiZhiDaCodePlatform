"""能力层接口：统一能力清单。

能力层有三类成员（tool / mcp / skill），对前端和编排层来说就是一份清单。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Response, status

from app.api.deps import get_capability_service
from app.schemas.capability import (
    CapabilityInvokeOut,
    CapabilityListOut,
    CapabilityOut,
    CapabilitySpecOut,
)
from app.schemas.common import ResponseModel
from app.services.capability import CapabilityService

router = APIRouter(prefix="/capabilities", tags=["能力"])

ServiceDep = Annotated[CapabilityService, Depends(get_capability_service)]


@router.get("", response_model=ResponseModel[CapabilityListOut], summary="统一能力清单")
async def list_capabilities(
    service: ServiceDep,
    kind: Annotated[str | None, Query(description="按种类过滤：tool/mcp/skill")] = None,
    tag: Annotated[str | None, Query(description="按标签过滤")] = None,
    enabled: Annotated[bool | None, Query(description="按启用状态过滤")] = None,
) -> ResponseModel[CapabilityListOut]:
    result = await service.list_capabilities(kind=kind, tag=tag, enabled=enabled)
    return ResponseModel[CapabilityListOut](data=result)


@router.get("/{key}", response_model=ResponseModel[CapabilityOut], summary="查询能力详情")
async def get_capability(
    key: str,
    service: ServiceDep,
) -> ResponseModel[CapabilityOut]:
    entry = await service.get_capability(key)
    return ResponseModel[CapabilityOut](data=entry)


@router.get(
    "/{key}/spec",
    response_model=ResponseModel[CapabilitySpecOut],
    summary="查询能力声明（契约与策略）",
)
async def get_capability_spec(
    key: str,
    service: ServiceDep,
) -> ResponseModel[CapabilitySpecOut]:
    """能力的完整声明：输入输出 schema、风险等级、权限要求、超时、重试、幂等标记。"""
    spec = await service.get_spec(key)
    return ResponseModel[CapabilitySpecOut](data=spec)


@router.post(
    "/{key}/invoke",
    response_model=ResponseModel[CapabilityInvokeOut],
    summary="直接调用一次能力（调试用）",
)
async def invoke_capability(
    key: str,
    service: ServiceDep,
    response: Response,
    arguments: Annotated[dict[str, Any] | None, Body()] = None,
    thread_id: Annotated[str | None, Query(description="关联的会话 ID")] = None,
) -> ResponseModel[CapabilityInvokeOut]:
    """真跑一次能力并返回结构化结果（失败也返回 200，用 ok 字段表达成败）。

    需要审批的调用返回 **202**：能力还没执行，等人工批准后再由审批接口执行。
    """
    result = await service.invoke(key, arguments or {}, thread_id=thread_id)
    if result.status == "pending":
        response.status_code = status.HTTP_202_ACCEPTED
    return ResponseModel[CapabilityInvokeOut](data=result)
