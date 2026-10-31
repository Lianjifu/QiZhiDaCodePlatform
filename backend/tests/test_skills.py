"""技能模块测试：MCP 服务器 / 工具 / 技能的增删改查与联动。"""

import pytest
from httpx import AsyncClient

TOOLS = "/api/v1/tools"
MCP = "/api/v1/mcp-servers"
SKILLS = "/api/v1/skills"

BUILTIN_SOURCE = "app.skills.builtin.fs:make_read_file"


def _tool_payload(**overrides):
    payload = {
        "name": "read_workspace_file",
        "display_name": "读取文件",
        "description": "读取工作区文件",
        "type": "builtin",
        "source": BUILTIN_SOURCE,
    }
    payload.update(overrides)
    return payload


def _server_payload(**overrides):
    payload = {
        "name": "本地 MCP",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    }
    payload.update(overrides)
    return payload


# ---------- 内置工具白名单 ----------


@pytest.mark.asyncio
async def test_list_builtins(client: AsyncClient) -> None:
    resp = await client.get(f"{TOOLS}/builtins")
    assert resp.status_code == 200
    keys = {item["key"] for item in resp.json()["data"]}
    assert {"read_file", "write_file", "list_files", "run_python"} <= keys


@pytest.mark.asyncio
async def test_builtin_source_outside_whitelist_is_rejected(client: AsyncClient) -> None:
    """白名单外的入口点必须被拒 —— 否则等于允许执行任意 Python 代码。"""
    resp = await client.post(TOOLS, json=_tool_payload(source="os:system"))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_tool_name_must_be_identifier_like(client: AsyncClient) -> None:
    resp = await client.post(TOOLS, json=_tool_payload(name="1 bad name"))
    assert resp.status_code == 422


# ---------- 工具 CRUD ----------


@pytest.mark.asyncio
async def test_create_and_list_tools(client: AsyncClient) -> None:
    created = await client.post(TOOLS, json=_tool_payload())
    assert created.status_code == 201
    assert created.json()["data"]["type"] == "builtin"

    listing = await client.get(TOOLS)
    assert listing.status_code == 200
    assert listing.json()["data"]["meta"]["total"] == 1


@pytest.mark.asyncio
async def test_duplicate_tool_name_conflicts(client: AsyncClient) -> None:
    await client.post(TOOLS, json=_tool_payload())
    resp = await client.post(TOOLS, json=_tool_payload())
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_http_tool_requires_url_and_parameters(client: AsyncClient) -> None:
    no_url = await client.post(
        TOOLS,
        json={
            "name": "http_no_url",
            "type": "http",
            "config": {"method": "GET"},
            "parameters": {"type": "object", "properties": {}},
        },
    )
    assert no_url.status_code == 422

    no_params = await client.post(
        TOOLS,
        json={
            "name": "http_no_params",
            "type": "http",
            "config": {"method": "GET", "url": "https://example.com"},
        },
    )
    assert no_params.status_code == 422


@pytest.mark.asyncio
async def test_mcp_tool_requires_existing_server(client: AsyncClient) -> None:
    resp = await client.post(
        TOOLS,
        json={
            "name": "mcp_tool",
            "type": "mcp",
            "source": "不存在的服务器",
            "config": {"tool_name": "read_file"},
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_and_delete_tool(client: AsyncClient) -> None:
    created = await client.post(TOOLS, json=_tool_payload())
    tool_id = created.json()["data"]["id"]

    updated = await client.put(f"{TOOLS}/{tool_id}", json={"description": "改过的说明"})
    assert updated.status_code == 200
    assert updated.json()["data"]["description"] == "改过的说明"

    assert (await client.delete(f"{TOOLS}/{tool_id}")).status_code == 204
    assert (await client.get(f"{TOOLS}/{tool_id}")).status_code == 404


@pytest.mark.asyncio
async def test_test_builtin_tool_reports_ok(client: AsyncClient) -> None:
    """内置工具应该能被成功构造出来（上下文齐全时）。"""
    created = await client.post(TOOLS, json=_tool_payload())
    tool_id = created.json()["data"]["id"]
    resp = await client.post(f"{TOOLS}/{tool_id}/test")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert "path" in data["message"]


# ---------- MCP 服务器 CRUD ----------


@pytest.mark.asyncio
async def test_create_mcp_server(client: AsyncClient) -> None:
    resp = await client.post(MCP, json=_server_payload())
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["transport"] == "stdio"
    assert data["command"] == "npx"


@pytest.mark.asyncio
async def test_stdio_server_requires_command(client: AsyncClient) -> None:
    resp = await client.post(MCP, json=_server_payload(command=None))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_http_mcp_server_requires_url(client: AsyncClient) -> None:
    resp = await client.post(MCP, json=_server_payload(transport="sse", url=None, command=None))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_mcp_server_name_conflicts(client: AsyncClient) -> None:
    await client.post(MCP, json=_server_payload())
    resp = await client.post(MCP, json=_server_payload())
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cannot_delete_mcp_server_referenced_by_tool(client: AsyncClient) -> None:
    server = await client.post(MCP, json=_server_payload())
    server_name = server.json()["data"]["name"]
    await client.post(
        TOOLS,
        json={
            "name": "mcp_read",
            "type": "mcp",
            "source": server_name,
            "config": {"tool_name": "read_file"},
        },
    )
    resp = await client.delete(f"{MCP}/{server.json()['data']['id']}")
    assert resp.status_code == 409
    assert "引用" in resp.json()["message"]


# ---------- 技能 CRUD ----------


@pytest.mark.asyncio
async def test_create_skill_with_tools(client: AsyncClient) -> None:
    tool = await client.post(TOOLS, json=_tool_payload())
    tool_id = tool.json()["data"]["id"]

    resp = await client.post(
        SKILLS,
        json={
            "name": "代码审查",
            "description": "审查代码",
            "prompt": "逐行检查潜在问题。",
            "tool_ids": [tool_id],
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["tool_ids"] == [tool_id]
    assert data["tool_names"] == ["read_workspace_file"]


@pytest.mark.asyncio
async def test_skill_with_unknown_tool_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(SKILLS, json={"name": "坏技能", "tool_ids": [9999]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_skill_name_conflicts(client: AsyncClient) -> None:
    await client.post(SKILLS, json={"name": "重复技能"})
    resp = await client.post(SKILLS, json={"name": "重复技能"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_skill_replaces_tools(client: AsyncClient) -> None:
    tool_a = await client.post(TOOLS, json=_tool_payload(name="tool_a"))
    tool_b = await client.post(TOOLS, json=_tool_payload(name="tool_b"))
    skill = await client.post(
        SKILLS, json={"name": "可更新技能", "tool_ids": [tool_a.json()["data"]["id"]]}
    )
    skill_id = skill.json()["data"]["id"]

    resp = await client.put(
        f"{SKILLS}/{skill_id}", json={"tool_ids": [tool_b.json()["data"]["id"]]}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["tool_ids"] == [tool_b.json()["data"]["id"]]


@pytest.mark.asyncio
async def test_deleting_tool_clears_skill_reference(client: AsyncClient) -> None:
    tool = await client.post(TOOLS, json=_tool_payload())
    tool_id = tool.json()["data"]["id"]
    skill = await client.post(SKILLS, json={"name": "引用工具的技能", "tool_ids": [tool_id]})
    skill_id = skill.json()["data"]["id"]

    await client.delete(f"{TOOLS}/{tool_id}")

    resp = await client.get(f"{SKILLS}/{skill_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["tool_ids"] == []


@pytest.mark.asyncio
async def test_delete_skill(client: AsyncClient) -> None:
    skill = await client.post(SKILLS, json={"name": "待删除技能"})
    skill_id = skill.json()["data"]["id"]
    assert (await client.delete(f"{SKILLS}/{skill_id}")).status_code == 204
    assert (await client.get(f"{SKILLS}/{skill_id}")).status_code == 404


# ---------- 与 agent 联动 ----------


@pytest.mark.asyncio
async def test_agent_can_use_skill_tools(app, client: AsyncClient, session, monkeypatch) -> None:
    """技能里的工具应该真的挂到 agent 上，并被模型看见。"""
    from app.agents.runner import AgentRunner
    from app.api.deps import get_agent_service
    from app.services.agent import AgentService
    from tests.fake_chat_model import FakeChatModel

    tool = await client.post(TOOLS, json=_tool_payload())
    tool_id = tool.json()["data"]["id"]
    skill = await client.post(
        SKILLS, json={"name": "读文件技能", "prompt": "需要读文件时用它。", "tool_ids": [tool_id]}
    )
    skill_id = skill.json()["data"]["id"]

    seen: dict = {}

    class InspectingModel(FakeChatModel):
        def bind_tools(self, tools, **kwargs):
            seen["tools"] = [t.name for t in tools]
            return self

    # 技能要从库里读，所以这里得把 session 一起传进去
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=InspectingModel(reply="好的"))
    )
    resp = await client.post(
        "/api/v1/agent/chat",
        json={"message": "读一下文件", "thread_id": "sk1", "skill_ids": [skill_id]},
    )
    assert resp.status_code == 200
    assert "read_workspace_file" in seen["tools"]
