"""模型接入配置（供应商 CRUD + API Key 加密）测试。

重点验证两件事：
1. API Key 明文**绝不出现在**响应里，库里存的也是密文；
2. 默认供应商全局唯一。
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.models.llm_provider import LLMProvider

API = "/api/v1/llm-providers"

SECRET = "sk-test-1234567890abcdef"


def _payload(**overrides):
    payload = {
        "name": "deepseek-生产",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": SECRET,
        "default_model": "deepseek-chat",
        "available_models": ["deepseek-chat", "deepseek-reasoner"],
        "temperature": 0.2,
        "max_tokens": 2048,
        "is_default": True,
    }
    payload.update(overrides)
    return payload


# ---------- 加密基础 ----------


def test_encrypt_decrypt_roundtrip():
    cipher = encrypt_secret(SECRET)
    assert cipher != SECRET
    assert decrypt_secret(cipher) == SECRET


def test_mask_secret_keeps_only_head_and_tail():
    masked = mask_secret(SECRET)
    assert SECRET not in masked
    assert masked.startswith("sk-")
    assert masked.endswith("cdef")


def test_mask_secret_hides_short_values():
    assert mask_secret("abc") == "***"


# ---------- CRUD ----------


@pytest.mark.asyncio
async def test_create_provider_masks_key(client: AsyncClient) -> None:
    resp = await client.post(API, json=_payload())
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["name"] == "deepseek-生产"
    assert data["provider"] == "deepseek"
    assert data["has_api_key"] is True
    assert data["api_key_masked"] != SECRET
    assert SECRET not in resp.text


@pytest.mark.asyncio
async def test_stored_key_is_encrypted(client: AsyncClient, session) -> None:
    resp = await client.post(API, json=_payload())
    provider_id = resp.json()["data"]["id"]

    row = (
        await session.execute(select(LLMProvider).where(LLMProvider.id == provider_id))
    ).scalar_one()
    assert row.api_key_encrypted != SECRET
    assert SECRET not in row.api_key_encrypted
    assert decrypt_secret(row.api_key_encrypted) == SECRET


@pytest.mark.asyncio
async def test_duplicate_name_conflicts(client: AsyncClient) -> None:
    await client.post(API, json=_payload())
    resp = await client.post(API, json=_payload())
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_list_providers_does_not_leak_key(client: AsyncClient) -> None:
    await client.post(API, json=_payload())
    resp = await client.get(API)
    assert resp.status_code == 200
    assert SECRET not in resp.text
    assert resp.json()["data"]["items"][0]["has_api_key"] is True


@pytest.mark.asyncio
async def test_get_provider_detail(client: AsyncClient) -> None:
    created = await client.post(API, json=_payload())
    provider_id = created.json()["data"]["id"]
    resp = await client.get(f"{API}/{provider_id}")
    assert resp.status_code == 200
    assert SECRET not in resp.text


@pytest.mark.asyncio
async def test_get_missing_provider_returns_404(client: AsyncClient) -> None:
    resp = await client.get(f"{API}/9999")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_update_provider_reencrypts_key(client: AsyncClient, session) -> None:
    created = await client.post(API, json=_payload())
    provider_id = created.json()["data"]["id"]
    old_cipher = (
        (await session.execute(select(LLMProvider).where(LLMProvider.id == provider_id)))
        .scalar_one()
        .api_key_encrypted
    )

    resp = await client.put(
        f"{API}/{provider_id}",
        json={"api_key": "sk-new-999988887777", "default_model": "deepseek-reasoner"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["default_model"] == "deepseek-reasoner"

    row = (
        await session.execute(select(LLMProvider).where(LLMProvider.id == provider_id))
    ).scalar_one()
    assert row.api_key_encrypted != old_cipher
    assert decrypt_secret(row.api_key_encrypted) == "sk-new-999988887777"


@pytest.mark.asyncio
async def test_update_without_api_key_keeps_existing(client: AsyncClient, session) -> None:
    created = await client.post(API, json=_payload())
    provider_id = created.json()["data"]["id"]
    before = (
        (await session.execute(select(LLMProvider).where(LLMProvider.id == provider_id)))
        .scalar_one()
        .api_key_encrypted
    )

    resp = await client.put(f"{API}/{provider_id}", json={"description": "改个描述"})
    assert resp.status_code == 200

    after = (
        (await session.execute(select(LLMProvider).where(LLMProvider.id == provider_id)))
        .scalar_one()
        .api_key_encrypted
    )
    assert after == before


@pytest.mark.asyncio
async def test_delete_provider(client: AsyncClient) -> None:
    created = await client.post(API, json=_payload())
    provider_id = created.json()["data"]["id"]
    resp = await client.delete(f"{API}/{provider_id}")
    assert resp.status_code == 204
    assert (await client.get(f"{API}/{provider_id}")).status_code == 404


@pytest.mark.asyncio
async def test_unknown_provider_key_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(API, json=_payload(provider="not-a-real-provider"))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_api_key_is_rejected(client: AsyncClient) -> None:
    payload = _payload()
    payload.pop("api_key")
    resp = await client.post(API, json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_default_provider_is_unique(client: AsyncClient) -> None:
    await client.post(API, json=_payload(name="第一个", is_default=True))
    await client.post(API, json=_payload(name="第二个", is_default=True))

    listing = await client.get(API)
    items = listing.json()["data"]["items"]
    defaults = [item for item in items if item["is_default"]]
    assert len(defaults) == 1


@pytest.mark.asyncio
async def test_presets_endpoint_lists_supported_providers(client: AsyncClient) -> None:
    resp = await client.get(f"{API}/presets")
    assert resp.status_code == 200
    keys = {item["key"] for item in resp.json()["data"]}
    assert {"openai", "anthropic", "deepseek", "moonshot", "qwen", "ollama"} <= keys


@pytest.mark.asyncio
async def test_provider_without_default_model_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(API, json=_payload(default_model=""))
    assert resp.status_code == 422
