"""供应商配置服务：CRUD + API Key 加密 + 连通性测试。

事务边界在这里：一个方法 = 一个业务用例 = 一个事务。
"""

from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError

from app.agents.llm import build_model_for_provider
from app.agents.providers import resolve_preset
from app.core.crypto import encrypt_secret, mask_secret
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.llm_provider import LLMProvider
from app.repositories.llm_provider import LLMProviderRepository
from app.schemas.common import PageMeta
from app.schemas.llm_provider import (
    LLMProviderCreate,
    LLMProviderUpdate,
    ProviderTestOut,
)
from app.services.base import BaseService, PageResult


class LLMProviderService(BaseService[LLMProviderRepository]):
    repository = LLMProviderRepository

    # ---------- 查询 ----------

    async def get_provider(self, provider_id: int) -> LLMProvider:
        provider = await self.repo.get(provider_id)
        if provider is None:
            raise NotFoundError(f"供应商配置 {provider_id} 不存在")
        return provider

    async def list_providers(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        provider: str | None = None,
        is_active: bool | None = None,
    ) -> PageResult:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        items = await self.repo.list_all()

        if provider:
            items = [item for item in items if item.provider == provider]
        if is_active is not None:
            items = [item for item in items if item.is_active is is_active]

        total = len(items)
        start = (page - 1) * page_size
        window = items[start : start + page_size]
        pages = (total + page_size - 1) // page_size
        return PageResult(
            items=window,
            meta=PageMeta(total=total, page=page, page_size=page_size, pages=pages),
        )

    async def get_default_provider(self) -> LLMProvider | None:
        return await self.repo.get_default()

    # ---------- 写入 ----------

    async def create_provider(self, payload: LLMProviderCreate) -> LLMProvider:
        if await self.repo.get_by_name(payload.name) is not None:
            raise ConflictError(f"名称 {payload.name} 已存在")

        preset = resolve_preset(payload.provider)
        if preset.api_key_required and not payload.api_key:
            raise ValidationError(f"{preset.display_name} 需要配置 API Key")
        if not payload.default_model:
            raise ValidationError("必须指定默认模型")

        encrypted = encrypt_secret(payload.api_key)
        masked = mask_secret(payload.api_key)
        try:
            provider = await self.repo.create(
                name=payload.name,
                provider=payload.provider,
                base_url=payload.base_url,
                api_key_encrypted=encrypted,
                api_key_masked=masked,
                default_model=payload.default_model,
                available_models=list(payload.available_models),
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
                timeout=payload.timeout,
                is_default=payload.is_default,
                is_active=payload.is_active,
                description=payload.description,
            )
        except IntegrityError as exc:  # 唯一约束兜底，防并发写入绕过上面的检查
            await self.session.rollback()
            raise ConflictError(f"名称 {payload.name} 已存在") from exc

        if provider.is_default:
            await self.repo.clear_default_except(provider.id)
        await self.session.commit()
        return provider

    async def update_provider(self, provider_id: int, payload: LLMProviderUpdate) -> LLMProvider:
        provider = await self.get_provider(provider_id)
        data = payload.model_dump(exclude_unset=True)

        if "name" in data and data["name"] and data["name"] != provider.name:
            if await self.repo.get_by_name(data["name"]) is not None:
                raise ConflictError(f"名称 {data['name']} 已存在")

        if "provider" in data and data["provider"]:
            resolve_preset(data["provider"])

        if "api_key" in data:
            # 传空串表示清空已配置的 key；不传这个字段表示保持原样
            new_key = data.pop("api_key")
            if new_key:
                data["api_key_encrypted"] = encrypt_secret(new_key)
                data["api_key_masked"] = mask_secret(new_key)
            else:
                data["api_key_encrypted"] = None
                data["api_key_masked"] = ""

        if data.get("is_default"):
            await self.repo.clear_default_except(provider.id)

        provider = await self.repo.update(provider, **data)
        await self.session.commit()
        return provider

    async def delete_provider(self, provider_id: int) -> None:
        provider = await self.get_provider(provider_id)
        await self.repo.delete(provider)
        await self.session.commit()

    # ---------- 连通性测试 ----------

    async def test_provider(self, provider_id: int) -> ProviderTestOut:
        """真发一次最小请求，验证 URL / API Key / 模型名是否可用。"""
        provider = await self.get_provider(provider_id)
        started = time.perf_counter()
        try:
            model = build_model_for_provider(provider)
            reply = await model.ainvoke("ping")
            elapsed = int((time.perf_counter() - started) * 1000)
            content = getattr(reply, "content", "")
            if not isinstance(content, str):
                content = str(content)
            return ProviderTestOut(
                ok=True,
                provider_id=provider.id,
                model=provider.default_model,
                latency_ms=elapsed,
                message=content[:200],
            )
        except Exception as exc:  # 连通性测试要把失败原因如实回显
            elapsed = int((time.perf_counter() - started) * 1000)
            return ProviderTestOut(
                ok=False,
                provider_id=provider.id,
                model=provider.default_model,
                latency_ms=elapsed,
                message=str(exc)[:500] or exc.__class__.__name__,
            )
