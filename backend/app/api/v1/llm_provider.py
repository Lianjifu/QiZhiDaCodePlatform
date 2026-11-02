"""大模型供应商配置接口。

对外只暴露脱敏后的 API Key（`api_key_masked`）和"是否已配置"标记，
明文既不出现在响应体，也不会出现在日志里。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.agents.providers import list_presets
from app.api.deps import get_llm_provider_service
from app.models.llm_provider import LLMProvider
from app.schemas.common import ResponseModel
from app.schemas.llm_provider import (
    LLMProviderCreate,
    LLMProviderListOut,
    LLMProviderOut,
    LLMProviderUpdate,
    ProviderPresetOut,
    ProviderTestOut,
)
from app.services.llm_provider import LLMProviderService

router = APIRouter(prefix="/llm-providers", tags=["模型接入"])

ServiceDep = Annotated[LLMProviderService, Depends(get_llm_provider_service)]


def _to_out(provider: LLMProvider) -> LLMProviderOut:
    """ORM → 响应模型。API Key 只以脱敏形式出现。"""
    return LLMProviderOut(
        id=provider.id,
        name=provider.name,
        provider=provider.provider,
        base_url=provider.base_url,
        default_model=provider.default_model,
        available_models=list(provider.available_models or []),
        embedding_model=provider.embedding_model,
        embedding_dim=provider.embedding_dim,
        temperature=provider.temperature,
        max_tokens=provider.max_tokens,
        context_window=provider.context_window,
        vision_enabled=provider.vision_enabled,
        timeout=provider.timeout,
        is_default=provider.is_default,
        is_active=provider.is_active,
        description=provider.description,
        api_key_masked=provider.api_key_masked,
        has_api_key=bool(provider.api_key_encrypted),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get(
    "/presets",
    response_model=ResponseModel[list[ProviderPresetOut]],
    summary="支持的供应商预设列表",
)
async def presets() -> ResponseModel[list[ProviderPresetOut]]:
    """前端下拉框用：列出所有支持的供应商及其默认地址。"""
    items = [
        ProviderPresetOut(
            key=preset.key,
            display_name=preset.display_name,
            langchain_provider=preset.langchain_provider,
            default_base_url=preset.default_base_url,
            api_key_env=preset.api_key_env,
            api_key_required=preset.api_key_required,
            openai_compatible=preset.openai_compatible,
            docs_url=preset.docs_url,
            context_window=preset.context_window,
            vision_enabled=preset.vision_enabled,
        )
        for preset in list_presets()
    ]
    return ResponseModel[list[ProviderPresetOut]](data=items)


@router.get("", response_model=ResponseModel[LLMProviderListOut], summary="分页查询供应商配置")
async def list_providers(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
    provider: Annotated[str | None, Query(description="按供应商标识过滤")] = None,
    is_active: Annotated[bool | None, Query(description="按启用状态过滤")] = None,
) -> ResponseModel[LLMProviderListOut]:
    result = await service.list_providers(
        page=page, page_size=page_size, provider=provider, is_active=is_active
    )
    listing = LLMProviderListOut(
        items=[_to_out(item) for item in result.items],
        meta=result.meta,
    )
    return ResponseModel[LLMProviderListOut](data=listing)


@router.get("/{provider_id}", response_model=ResponseModel[LLMProviderOut], summary="查询详情")
async def get_provider(
    provider_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[LLMProviderOut]:
    provider = await service.get_provider(provider_id)
    return ResponseModel[LLMProviderOut](data=_to_out(provider))


@router.post(
    "",
    response_model=ResponseModel[LLMProviderOut],
    status_code=status.HTTP_201_CREATED,
    summary="新增供应商配置",
)
async def create_provider(
    payload: LLMProviderCreate,
    service: ServiceDep,
) -> ResponseModel[LLMProviderOut]:
    provider = await service.create_provider(payload)
    return ResponseModel[LLMProviderOut](message="创建成功", data=_to_out(provider))


@router.put(
    "/{provider_id}",
    response_model=ResponseModel[LLMProviderOut],
    summary="更新供应商配置",
)
async def update_provider(
    provider_id: Annotated[int, Path(ge=1)],
    payload: LLMProviderUpdate,
    service: ServiceDep,
) -> ResponseModel[LLMProviderOut]:
    provider = await service.update_provider(provider_id, payload)
    return ResponseModel[LLMProviderOut](message="更新成功", data=_to_out(provider))


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除供应商配置")
async def delete_provider(
    provider_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> Response:
    await service.delete_provider(provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{provider_id}/test",
    response_model=ResponseModel[ProviderTestOut],
    summary="测试连通性",
)
async def test_provider(
    provider_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[ProviderTestOut]:
    """真实发一次最小请求，验证 URL / API Key / 模型名是否可用。"""
    result = await service.test_provider(provider_id)
    return ResponseModel[ProviderTestOut](data=result)
