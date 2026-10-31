"""能力层测试：统一清单、出厂证、编排层的检索与参数校验、执行层结果。"""

import pytest
from httpx import AsyncClient

from app.capabilities.entry import KIND_SKILL, KIND_TOOL, CapabilityEntry
from app.capabilities.orchestration import (
    CapabilityMiddleware,
    collect_tool_keys,
    retrieve_skills,
    select_skills,
    validate_arguments,
)
from app.capabilities.prompt import assemble_prompt
from app.capabilities.result import ErrorCode, ExecutionError, ExecutionResult

CAPS = "/api/v1/capabilities"
TOOLS = "/api/v1/tools"
SKILLS = "/api/v1/skills"
MCP = "/api/v1/mcp-servers"


# ---------- 出厂证 ----------


@pytest.mark.asyncio
async def test_builtin_capabilities_are_installed(client: AsyncClient) -> None:
    """内置能力应该已经被出厂源装进数据库，并标记为 builtin。"""
    resp = await client.get(TOOLS, params={"origin": "builtin"})
    assert resp.status_code == 200
    names = {item["name"] for item in resp.json()["data"]["items"]}
    assert {"read_file", "write_file", "list_files", "run_python"} <= names


@pytest.mark.asyncio
async def test_sync_builtins_does_not_overwrite_operator_changes(
    client: AsyncClient, session
) -> None:
    """运营改过的描述不该被启动同步冲掉。"""
    from sqlalchemy import select

    from app.capabilities.registry import CapabilityRegistry
    from app.models.tool import Tool

    listing = await client.get(TOOLS, params={"origin": "builtin"})
    target = next(item for item in listing.json()["data"]["items"] if item["name"] == "read_file")
    await client.put(f"{TOOLS}/{target['id']}", json={"description": "运营改过的说明"})

    # 再同步一次
    await CapabilityRegistry.sync_builtins(session)
    await session.commit()

    row = (await session.execute(select(Tool).where(Tool.name == "read_file"))).scalar_one()
    assert row.description == "运营改过的说明"


@pytest.mark.asyncio
async def test_unified_manifest_lists_all_kinds(client: AsyncClient) -> None:
    """统一清单里同时有 tool / mcp / skill 三类成员。"""
    await client.post(MCP, json={"name": "本地 MCP", "transport": "stdio", "command": "npx"})
    await client.post(SKILLS, json={"name": "读文件技能", "knowledge": "读文件时用"})

    resp = await client.get(CAPS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data["kinds"]) == {"tool", "mcp", "skill"}
    keys = {item["key"] for item in data["items"]}
    assert "read_file" in keys  # 出厂内置能力
    assert "本地 MCP" in keys
    assert "读文件技能" in keys


@pytest.mark.asyncio
async def test_manifest_filters_by_kind_and_tag(client: AsyncClient) -> None:
    only_tools = await client.get(CAPS, params={"kind": "tool"})
    assert all(item["kind"] == "tool" for item in only_tools.json()["data"]["items"])

    by_tag = await client.get(CAPS, params={"tag": "文件"})
    assert by_tag.json()["data"]["total"] >= 1


@pytest.mark.asyncio
async def test_get_capability_by_key(client: AsyncClient) -> None:
    resp = await client.get(f"{CAPS}/read_file")
    assert resp.status_code == 200
    assert resp.json()["data"]["kind"] == "tool"
    assert resp.json()["data"]["origin"] == "builtin"


@pytest.mark.asyncio
async def test_get_missing_capability_returns_404(client: AsyncClient) -> None:
    resp = await client.get(f"{CAPS}/根本没有这个能力")
    assert resp.status_code == 404


# ---------- 执行层 ----------


@pytest.mark.asyncio
async def test_invoke_builtin_capability(client: AsyncClient) -> None:
    """直接调一次内置能力，写进去再读出来。"""
    written = await client.post(
        f"{CAPS}/write_file/invoke", json={"path": "invoked.txt", "content": "hello"}
    )
    assert written.status_code == 200
    assert written.json()["data"]["ok"] is True

    read = await client.post(f"{CAPS}/read_file/invoke", json={"path": "invoked.txt"})
    assert read.json()["data"]["ok"] is True
    assert "hello" in read.json()["data"]["content"]


@pytest.mark.asyncio
async def test_invoke_missing_capability_returns_404(client: AsyncClient) -> None:
    resp = await client.post(f"{CAPS}/没有这个能力/invoke", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoke_records_execution_log(client: AsyncClient, session) -> None:
    await client.post(
        f"{CAPS}/write_file/invoke",
        json={"path": "logged.txt", "content": "x"},
        params={"thread_id": "thread-log"},
    )
    # 轨迹落在 execution_log 表里
    from sqlalchemy import select

    from app.models.execution_log import ExecutionLog

    rows = (
        (await session.execute(select(ExecutionLog).where(ExecutionLog.thread_id == "thread-log")))
        .scalars()
        .all()
    )
    assert rows
    assert rows[0].capability_key == "write_file"
    assert rows[0].ok is True


# ---------- 编排层 ----------


def _skill(name: str, tags: list[str] | None = None, knowledge: str = "") -> CapabilityEntry:
    return CapabilityEntry(
        key=name,
        kind=KIND_SKILL,
        db_id=abs(hash(name)) % 1000,
        display_name=name,
        description=knowledge or name,
        tags=tags or [],
        knowledge=knowledge,
    )


def _tool(key: str, origin: str = "user") -> CapabilityEntry:
    return CapabilityEntry(key=key, kind=KIND_TOOL, origin=origin)


def test_retrieve_skills_matches_tags():
    entries = [
        _skill("代码审查", ["代码", "质量"]),
        _skill("部署上线", ["部署"]),
        _skill("写测试", ["测试"]),
    ]
    hits = retrieve_skills(entries, "帮我审查一下代码质量")
    assert [entry.key for entry in hits] == ["代码审查"]


def test_retrieve_skills_returns_empty_when_no_match():
    entries = [_skill("部署上线", ["部署"])]
    assert retrieve_skills(entries, "帮我写个爬虫") == []


def test_select_skills_prefers_explicit_ids():
    entries = [_skill("A", ["x"]), _skill("B", ["y"])]
    chosen = select_skills(entries, "随便什么", skill_ids=[entries[1].db_id])
    assert [entry.key for entry in chosen] == ["B"]


def test_collect_tool_keys_includes_builtin_capabilities():
    skill = _skill("带工具的技能")
    skill.members = ["custom_tool"]
    entries = [skill, _tool("custom_tool"), _tool("read_file", origin="builtin")]
    keys = collect_tool_keys([skill], entries)
    assert "custom_tool" in keys
    assert "read_file" in keys  # 出厂内置能力始终保留


def test_validate_arguments_reports_missing_required():
    entry = _tool("read_file")
    entry.parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    error = validate_arguments(entry, {})
    assert error is not None
    assert error.code == ErrorCode.INVALID_ARGUMENTS
    assert error.retryable is True


def test_validate_arguments_reports_wrong_type():
    entry = _tool("read_file")
    entry.parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    error = validate_arguments(entry, {"path": 123})
    assert error is not None
    assert error.code == ErrorCode.INVALID_ARGUMENTS


def test_validate_arguments_passes_ok():
    entry = _tool("read_file")
    entry.parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    assert validate_arguments(entry, {"path": "a.txt"}) is None


@pytest.mark.asyncio
async def test_middleware_narrows_tools_to_allowed_set():
    """模型只应看到被允许的能力。"""
    from langchain.agents.middleware.types import ModelRequest

    from tests.fake_chat_model import FakeChatModel

    allowed = _tool("read_file")
    other = _tool("danger_tool")
    middleware = CapabilityMiddleware([allowed, other], {"read_file"})

    class _Tool:
        def __init__(self, name):
            self.name = name

    request = ModelRequest(
        model=FakeChatModel(),
        messages=[],
        tools=[_Tool("read_file"), _Tool("danger_tool")],
    )
    captured = {}

    async def handler(req):
        captured["tools"] = [t.name for t in req.tools]
        return "ok"

    await middleware.awrap_model_call(request, handler)
    assert captured["tools"] == ["read_file"]


# ---------- 提示词组装 ----------


def test_assemble_prompt_includes_skill_knowledge():
    skill = _skill("代码审查", ["代码"], knowledge="逐行检查潜在问题。")
    prompt = assemble_prompt("基础提示词", [skill], runtime_notes=["工作区：/tmp/x"])
    assert "基础提示词" in prompt
    assert "逐行检查潜在问题。" in prompt
    assert "工作区：/tmp/x" in prompt


def test_assemble_prompt_without_skills():
    assert assemble_prompt("基础提示词") == "基础提示词"


# ---------- 执行结果 ----------


def test_execution_result_text_shows_error_and_hint():
    result = ExecutionResult.failed(ExecutionError.of(ErrorCode.INVALID_ARGUMENTS, "缺少 path"))
    text = result.to_text()
    assert "INVALID_ARGUMENTS" in text
    assert "缺少 path" in text
    assert "重试" in text


def test_execution_result_text_truncates_long_output():
    result = ExecutionResult(ok=True, content="x" * 30000)
    assert len(result.to_text()) < 30000
    assert "已截断" in result.to_text()
