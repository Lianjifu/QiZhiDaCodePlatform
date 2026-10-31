"""技能管理接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import get_skill_service
from app.schemas.common import ResponseModel
from app.schemas.skill import SkillCreate, SkillListOut, SkillOut, SkillUpdate
from app.services.skill import SkillService

router = APIRouter(prefix="/skills", tags=["技能"])

ServiceDep = Annotated[SkillService, Depends(get_skill_service)]


async def _to_out(service: SkillService, skill) -> SkillOut:
    """ORM → 响应模型，顺带把关联的工具 id 与名字带出来。"""
    out = SkillOut.model_validate(skill)
    out.tool_ids = await service.list_tool_ids(skill.id)
    out.tool_names = await service.get_tool_names(skill.id)
    return out


@router.get("", response_model=ResponseModel[SkillListOut], summary="分页查询技能")
async def list_skills(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
    enabled: Annotated[bool | None, Query(description="按启用状态过滤")] = None,
) -> ResponseModel[SkillListOut]:
    result = await service.list_skills(page=page, page_size=page_size, enabled=enabled)
    listing = SkillListOut(
        items=[await _to_out(service, item) for item in result.items],
        meta=result.meta,
    )
    return ResponseModel[SkillListOut](data=listing)


@router.post(
    "",
    response_model=ResponseModel[SkillOut],
    status_code=status.HTTP_201_CREATED,
    summary="新增技能",
)
async def create_skill(
    payload: SkillCreate,
    service: ServiceDep,
) -> ResponseModel[SkillOut]:
    skill = await service.create_skill(payload)
    return ResponseModel[SkillOut](message="创建成功", data=await _to_out(service, skill))


@router.get("/{skill_id}", response_model=ResponseModel[SkillOut], summary="查询技能详情")
async def get_skill(
    skill_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[SkillOut]:
    skill = await service.get_skill(skill_id)
    return ResponseModel[SkillOut](data=await _to_out(service, skill))


@router.put("/{skill_id}", response_model=ResponseModel[SkillOut], summary="更新技能")
async def update_skill(
    skill_id: Annotated[int, Path(ge=1)],
    payload: SkillUpdate,
    service: ServiceDep,
) -> ResponseModel[SkillOut]:
    skill = await service.update_skill(skill_id, payload)
    return ResponseModel[SkillOut](message="更新成功", data=await _to_out(service, skill))


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除技能")
async def delete_skill(
    skill_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> Response:
    await service.delete_skill(skill_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
