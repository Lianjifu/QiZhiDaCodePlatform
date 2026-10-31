"""示例资源接口的端到端测试。"""

import pytest
from httpx import AsyncClient

API = "/api/v1/examples"


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_and_get(client: AsyncClient) -> None:
    resp = await client.post(API, json={"name": "demo", "description": "示例"})
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["name"] == "demo"

    resp = await client.get(f"{API}/{data['id']}")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == data["id"]


@pytest.mark.asyncio
async def test_create_duplicate_conflict(client: AsyncClient) -> None:
    await client.post(API, json={"name": "dup"})
    resp = await client.post(API, json={"name": "dup"})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_get_not_found(client: AsyncClient) -> None:
    resp = await client.get(f"{API}/999")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_list_and_delete(client: AsyncClient) -> None:
    await client.post(API, json={"name": "a"})
    await client.post(API, json={"name": "b"})

    resp = await client.get(API, params={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["meta"]["total"] == 2
    assert len(body["items"]) == 2

    first_id = body["items"][0]["id"]
    resp = await client.delete(f"{API}/{first_id}")
    assert resp.status_code == 204

    resp = await client.get(API)
    assert resp.json()["data"]["meta"]["total"] == 1
