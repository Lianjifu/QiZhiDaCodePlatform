"""能力层测试：统一清单、出厂证、意图层的检索与参数校验、执行层流水线与治理。"""

import pytest
from httpx import AsyncClient

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.pipeline import execute
from app.capabilities.execution.redaction import redact, redact_text
from app.capabilities.execution.result import ExecutionResult
from app.capabilities.execution.risk import assess
from app.capabilities.intent.intent import CapabilityIntent, compute_idempotency_key
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.intent.planner import collect_tool_keys, retrieve_skills, select_skills
from app.capabilities.intent.resolver import resolve
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registration.args_model import schema_to_model
from app.capabilities.registration.spec import KIND_SKILL, KIND_TOOL, CapabilitySpec
from app.capabilities.registration.validator import (
    SchemaValidationError,
    validate_arguments,
    validate_output,
)

CAPS = "/api/v1/capabilities"
TOOLS = "/api/v1/tools"
SKILLS = "/api/v1/skills"
MCP = "/api/v1/mcp-servers"
INVOCATIONS = "/api/v1/invocations"


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

    from app.capabilities.registration.registry import CapabilityRegistry
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
async def test_builtin_specs_carry_governance_declarations(client: AsyncClient) -> None:
    """内置能力出厂时就要带齐声明：风险、权限、超时、重试、注解。"""
    resp = await client.get(f"{CAPS}/run_bash/spec")
    assert resp.status_code == 200
    spec = resp.json()["data"]
    assert spec["risk"] == "execute"
    assert spec["permission"] == "confirm"
    assert spec["timeout_s"] == 60
    assert spec["retry_policy"]["max_attempts"] == 2
    assert spec["annotations"]["destructive"] is True
    assert spec["output_schema"]["properties"]["exit_code"]["type"] == "integer"


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
async def test_invoke_records_invocation(client: AsyncClient, session) -> None:
    """执行层统一留痕：调用记录落在 invocation 表里。"""
    await client.post(
        f"{CAPS}/write_file/invoke",
        json={"path": "logged.txt", "content": "x"},
        params={"thread_id": "thread-log"},
    )
    from sqlalchemy import select

    from app.models.invocation import Invocation

    rows = (
        (await session.execute(select(Invocation).where(Invocation.thread_id == "thread-log")))
        .scalars()
        .all()
    )
    assert rows
    assert rows[0].capability_key == "write_file"
    assert rows[0].status == "succeeded"
    # 各阶段结论也记下来了
    assert {stage["stage"] for stage in rows[0].stages} >= {"schema", "permission", "risk"}


@pytest.mark.asyncio
async def test_invoke_rejects_arguments_violating_schema(client: AsyncClient) -> None:
    """入参不符合声明的 schema 就该被挡下，而不是把脏数据带进执行。"""
    resp = await client.post(f"{CAPS}/write_file/invoke", json={"path": "x.txt"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] == "SCHEMA_INVALID"
    assert data["retryable"] is True


# ---------- 意图层 ----------


def _skill(name: str, tags: list[str] | None = None, knowledge: str = "") -> CapabilitySpec:
    return CapabilitySpec(
        key=name,
        kind=KIND_SKILL,
        db_id=abs(hash(name)) % 1000,
        display_name=name,
        description=knowledge or name,
        tags=tags or [],
        knowledge=knowledge,
    )


def _tool(key: str, origin: str = "user") -> CapabilitySpec:
    return CapabilitySpec(key=key, kind=KIND_TOOL, origin=origin)


def test_retrieve_skills_matches_tags():
    specs = [
        _skill("代码审查", ["代码", "质量"]),
        _skill("部署上线", ["部署"]),
        _skill("写测试", ["测试"]),
    ]
    hits = retrieve_skills(specs, "帮我审查一下代码质量")
    assert [spec.key for spec in hits] == ["代码审查"]


def test_retrieve_skills_returns_empty_when_no_match():
    specs = [_skill("部署上线", ["部署"])]
    assert retrieve_skills(specs, "帮我写个爬虫") == []


def test_select_skills_prefers_explicit_ids():
    specs = [_skill("A", ["x"]), _skill("B", ["y"])]
    chosen = select_skills(specs, "随便什么", skill_ids=[specs[1].db_id])
    assert [spec.key for spec in chosen] == ["B"]


def test_collect_tool_keys_includes_builtin_capabilities():
    skill = _skill("带工具的技能")
    skill.members = ["custom_tool"]
    specs = [skill, _tool("custom_tool"), _tool("read_file", origin="builtin")]
    keys = collect_tool_keys([skill], specs)
    assert "custom_tool" in keys
    assert "read_file" in keys  # 出厂内置能力始终保留


def test_validate_arguments_reports_missing_required():
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    with pytest.raises(SchemaValidationError) as exc:
        validate_arguments(schema, {})
    assert "path" in str(exc.value)


def test_validate_arguments_reports_wrong_type():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    with pytest.raises(SchemaValidationError):
        validate_arguments(schema, {"path": 123})


def test_validate_arguments_enforces_enum_and_range():
    """真正的 JSON Schema 校验：enum / minimum / additionalProperties 都管。"""
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["a", "b"]},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "additionalProperties": False,
    }
    validate_arguments(schema, {"mode": "a", "count": 3})
    with pytest.raises(SchemaValidationError):
        validate_arguments(schema, {"mode": "c"})
    with pytest.raises(SchemaValidationError):
        validate_arguments(schema, {"count": 99})
    with pytest.raises(SchemaValidationError):
        validate_arguments(schema, {"mode": "a", "extra": 1})


def test_validate_arguments_passes_ok():
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    assert validate_arguments(schema, {"path": "a.txt"}) is None


def test_validate_arguments_rejects_extra_fields_by_default():
    """声明了字段就默认拒绝多余参数。

    不统一这条规则的话，校验放行、能力实现却收到没见过的关键字参数直接炸。
    """
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    with pytest.raises(SchemaValidationError):
        validate_arguments(schema, {"path": "a.txt", "extra": 1})


def test_validate_output_ignores_extra_fields():
    """结果里常带簿记字段（状态码、退出码），契约只该管声明过的字段。"""
    schema = {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
        "additionalProperties": False,
    }
    validate_output(schema, {"content": "x", "status": 200})


def test_intent_drops_none_arguments():
    """没填的可选参数不应该以 None 传进能力实现。"""
    spec = _tool("list_files")
    intent = CapabilityIntent(
        capability_key="list_files",
        arguments={"path": None},
        spec=spec,
    )
    assert intent.arguments == {}


def test_resolver_builds_intent_with_idempotency_key():
    spec = _tool("write_file")
    spec.annotations = type(spec.annotations)(idempotent=True)
    intent = resolve(
        {"name": "write_file", "args": {"path": "a.txt", "content": "x"}},
        [spec],
        thread_id="t1",
    )
    assert intent.capability_key == "write_file"
    assert intent.idempotency_key is not None
    assert intent.fingerprint() == intent.fingerprint()  # 稳定


def test_resolver_rejects_unknown_capability():
    with pytest.raises(CapabilityError) as exc:
        resolve({"name": "nope", "args": {}}, [_tool("read_file")])
    assert exc.value.code == ErrorCode.CAPABILITY_NOT_FOUND


def test_resolver_rejects_capability_outside_allowed_set():
    with pytest.raises(CapabilityError) as exc:
        resolve(
            {"name": "read_file", "args": {}},
            [_tool("read_file")],
            allowed_keys={"write_file"},
        )
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


def test_idempotency_key_only_for_idempotent_capabilities():
    spec = _tool("move_file")
    assert compute_idempotency_key(spec, {"source": "a"}, "t") is None


# ---------- 风险评估 ----------


def test_risk_assessment_keeps_declared_risk():
    spec = _tool("read_file")
    spec.risk = "read"
    assert assess(spec, {"path": "a.txt"}).risk == "read"


def test_risk_assessment_escalates_destructive_command():
    spec = _tool("run_bash")
    spec.risk = "execute"
    result = assess(spec, {"command": "rm -rf /"})
    assert result.risk == "destructive"
    assert result.require_admin is True
    assert result.reasons


def test_risk_assessment_escalates_internal_url():
    spec = _tool("web_fetch")
    spec.risk = "network"
    result = assess(spec, {"url": "http://169.254.169.254/latest/meta-data/"})
    assert result.risk == "destructive"
    assert result.require_admin is True


def test_risk_assessment_leaves_public_url_alone():
    spec = _tool("web_fetch")
    spec.risk = "network"
    assert assess(spec, {"url": "https://example.com"}).risk == "network"


# ---------- 脱敏 ----------


def test_redact_masks_api_keys():
    text = "token=sk-abcdefghijklmnopqrstuvwxyz012345"
    assert "sk-abcdef" not in redact_text(text)


def test_redact_recurses_into_structures():
    payload = {
        "a": ["sk-abcdefghijklmnopqrstuvwxyz012345"],
        "b": {"c": "ghp_abcdefghijklmnopqrstuv"},
    }
    cleaned = redact(payload)
    assert "sk-abcdef" not in str(cleaned)
    assert "ghp_abcdef" not in str(cleaned)


# ---------- 执行流水线 ----------


@pytest.mark.asyncio
async def test_pipeline_records_stages(session, tmp_path):
    """七阶段的结果都要落进调用记录，便于定位卡在哪一关。"""
    from sqlalchemy import select

    from app.capabilities.registration.registry import _tool_spec
    from app.models.tool import Tool

    spec = _tool_spec(
        (await session.execute(select(Tool).where(Tool.name == "write_file"))).scalar_one()
    )
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve({"name": "write_file", "args": {"path": "a.txt", "content": "x"}}, [spec])
    result = await execute(intent, context)
    await session.commit()

    assert result.ok is True
    assert {stage["stage"] for stage in result.stages} == {
        "schema",
        "permission",
        "risk",
        "approval",
        "invoke",
        "verify",
    }


@pytest.mark.asyncio
async def test_pipeline_blocks_on_invalid_arguments(session, tmp_path):
    from sqlalchemy import select

    from app.capabilities.registration.registry import _tool_spec
    from app.models.tool import Tool

    spec = _tool_spec(
        (await session.execute(select(Tool).where(Tool.name == "write_file"))).scalar_one()
    )
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve({"name": "write_file", "args": {"path": "a.txt"}}, [spec], validate=False)
    result = await execute(intent, context)

    assert result.ok is False
    assert result.error.code == ErrorCode.SCHEMA_INVALID
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_pipeline_requires_approval_for_execute_in_balanced_mode(session, tmp_path):
    from sqlalchemy import select

    from app.capabilities.registration.registry import _tool_spec
    from app.models.tool import Tool

    spec = _tool_spec(
        (await session.execute(select(Tool).where(Tool.name == "run_bash"))).scalar_one()
    )
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve(
        {"name": "run_bash", "args": {"command": "echo hi"}}, [spec], trust_mode="balanced"
    )
    result = await execute(intent, context)

    assert result.ok is False
    assert result.error.code == ErrorCode.APPROVAL_REQUIRED
    assert result.invocation_id is not None


@pytest.mark.asyncio
async def test_pipeline_runs_in_permissive_mode(session, tmp_path):
    from sqlalchemy import select

    from app.capabilities.registration.registry import _tool_spec
    from app.models.tool import Tool

    spec = _tool_spec(
        (await session.execute(select(Tool).where(Tool.name == "run_bash"))).scalar_one()
    )
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve(
        {"name": "run_bash", "args": {"command": "echo hi"}}, [spec], trust_mode="permissive"
    )
    result = await execute(intent, context)

    assert result.ok is True
    assert "hi" in result.content


@pytest.mark.asyncio
async def test_pipeline_dedupes_idempotent_calls(session, tmp_path):
    """同键的重复调用不重复执行。"""
    from sqlalchemy import select

    from app.capabilities.registration.registry import _tool_spec
    from app.models.tool import Tool

    spec = _tool_spec(
        (await session.execute(select(Tool).where(Tool.name == "write_file"))).scalar_one()
    )
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    context.thread_id = "idem-thread"
    intent = resolve(
        {"name": "write_file", "args": {"path": "a.txt", "content": "x"}},
        [spec],
        thread_id="idem-thread",
    )
    first = await execute(intent, context)
    await session.commit()
    second = await execute(intent, context)
    await session.commit()

    assert first.ok and second.ok
    assert first.content == second.content


# ---------- 审批 ----------


@pytest.mark.asyncio
async def test_approval_flow(client: AsyncClient) -> None:
    """待审批 → 批准 → 执行。"""
    pending = await client.post(
        f"{CAPS}/run_bash/invoke",
        json={"command": "echo approved"},
        params={"thread_id": "approve-thread"},
    )
    # 需要审批的调用返回 202：还没执行
    assert pending.status_code == 202
    data = pending.json()["data"]
    assert data["status"] == "pending"
    assert data["error_code"] == "APPROVAL_REQUIRED"
    invocation_id = data["invocation_id"]
    assert invocation_id

    listing = await client.get(INVOCATIONS, params={"status": "pending"})
    assert listing.status_code == 200
    assert listing.json()["data"]["pending"] >= 1

    approved = await client.post(f"{INVOCATIONS}/{invocation_id}/approve")
    assert approved.status_code == 200
    result = approved.json()["data"]
    assert result["ok"] is True
    assert "approved" in result["content"]


@pytest.mark.asyncio
async def test_deny_flow(client: AsyncClient) -> None:
    pending = await client.post(
        f"{CAPS}/run_bash/invoke", json={"command": "echo nope"}, params={"thread_id": "deny"}
    )
    invocation_id = pending.json()["data"]["invocation_id"]
    denied = await client.post(f"{INVOCATIONS}/{invocation_id}/deny", json={"reason": "不放心"})
    assert denied.status_code == 200
    assert denied.json()["data"]["status"] == "denied"


@pytest.mark.asyncio
async def test_cannot_approve_twice(client: AsyncClient) -> None:
    pending = await client.post(
        f"{CAPS}/run_bash/invoke", json={"command": "echo x"}, params={"thread_id": "twice"}
    )
    invocation_id = pending.json()["data"]["invocation_id"]
    await client.post(f"{INVOCATIONS}/{invocation_id}/deny")
    again = await client.post(f"{INVOCATIONS}/{invocation_id}/approve")
    assert again.status_code == 422


# ---------- middleware ----------


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


# ---------- 参数模型 ----------


def test_schema_to_model_marks_required_fields():
    model = schema_to_model(
        {
            "type": "object",
            "properties": {"path": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["path"],
        }
    )
    assert "path" in model.model_fields
    assert model.model_fields["path"].is_required()
    assert not model.model_fields["count"].is_required()


# ---------- 执行结果 ----------


def test_execution_result_text_shows_error_and_hint():
    result = ExecutionResult.failed(CapabilityError.of(ErrorCode.INVALID_ARGUMENTS, "缺少 path"))
    text = result.to_text()
    assert "INVALID_ARGUMENTS" in text
    assert "缺少 path" in text
    assert "重试" in text


def test_execution_result_text_truncates_long_output():
    result = ExecutionResult(ok=True, content="x" * 30000)
    assert len(result.to_text()) < 30000
    assert "已截断" in result.to_text()
