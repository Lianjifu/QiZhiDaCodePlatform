"""能力层测试：统一清单、出厂证、技能导出、执行层流水线与治理。"""

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
from app.capabilities.intent.resolver import resolve
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registration.args_model import schema_to_model
from app.capabilities.registration.policy import Annotations, RetryPolicy
from app.capabilities.registration.spec import KIND_SKILL, KIND_TOOL, CapabilitySpec
from app.capabilities.registration.validator import (
    SchemaValidationError,
    validate_arguments,
    validate_output,
)
from app.capabilities.skills_export import export_skills, render_skill_md

CAPS = "/api/v1/capabilities"
TOOLS = "/api/v1/tools"
SKILLS = "/api/v1/skills"
MCP = "/api/v1/mcp-servers"
INVOCATIONS = "/api/v1/invocations"


# ---------- 测试用的假能力 ----------

# 假能力的入参 / 出参契约。行为简单，但走的是和真实能力完全一样的执行路径。
_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}
_WRITE_OUTPUT = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "path": {"type": "string"},
        "bytes": {"type": "integer"},
        "created": {"type": "boolean"},
    },
    "required": ["content", "path", "bytes"],
}
_EXEC_SCHEMA = {
    "type": "object",
    "properties": {"command": {"type": "string"}},
    "required": ["command"],
    "additionalProperties": False,
}
_EXEC_OUTPUT = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "output": {"type": "string"},
        "exit_code": {"type": "integer"},
    },
    "required": ["content", "exit_code"],
}


async def _install_tool(
    session,
    *,
    name: str,
    source: str,
    permission: str = "none",
    risk: str = "read",
    parameters: dict | None = None,
    output_schema: dict | None = None,
    annotations: dict | None = None,
    timeout_s: int = 10,
    retry_policy: dict | None = None,
):
    """往 tool 表里装一条测试能力。

    直接写库、绕过 API —— API 会校验 builtin 的 source 白名单，而测试要用的
    是 ``tests.fake_capability`` 里的假能力。``origin`` 记成 user，免得被
    "下线已被 deepAgents 接管的能力"那套逻辑误伤。
    """
    from app.models.tool import Tool

    tool = Tool(
        name=name,
        display_name=name,
        description="测试用能力",
        type="builtin",
        source=source,
        config={},
        parameters=parameters if parameters is not None else {},
        enabled=True,
        origin="user",
        tags=["测试"],
        permission=permission,
        risk=risk,
        output_schema=output_schema,
        timeout_s=timeout_s,
        retry_policy=retry_policy
        or {"max_attempts": 1, "backoff_s": 0, "retry_on": [], "exponential": True},
        annotations=annotations
        or {"read_only": True, "destructive": False, "idempotent": True, "open_world": False},
        version=1,
    )
    session.add(tool)
    await session.commit()
    return tool


async def _install_write_tool(session, name: str = "test_write") -> None:
    """装一个"写文件"式的测试能力：权限 none，自动放行。"""
    await _install_tool(
        session,
        name=name,
        source="tests.fake_capability:make_test_write",
        permission="none",
        risk="write",
        parameters=_WRITE_SCHEMA,
        output_schema=_WRITE_OUTPUT,
        annotations={
            "read_only": False,
            "destructive": False,
            "idempotent": True,
            "open_world": False,
        },
    )


async def _install_read_tool(session, name: str = "test_read") -> None:
    """装一个"读文件"式的测试能力：文件不存在时失败。"""
    await _install_tool(
        session,
        name=name,
        source="tests.fake_capability:make_test_read",
        permission="none",
        risk="read",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "path": {"type": "string"},
                "line_count": {"type": "integer"},
            },
            "required": ["content", "path"],
        },
    )


async def _install_exec_tool(session, name: str = "test_exec") -> None:
    """装一个"执行命令"式的测试能力：权限 confirm，要人批。"""
    await _install_tool(
        session,
        name=name,
        source="tests.fake_capability:make_test_exec",
        permission="confirm",
        risk="execute",
        parameters=_EXEC_SCHEMA,
        output_schema=_EXEC_OUTPUT,
        timeout_s=30,
        annotations={
            "read_only": False,
            "destructive": True,
            "idempotent": False,
            "open_world": True,
        },
    )


async def _spec_of(session, name: str):
    """从库里取出某个能力的声明。"""
    from sqlalchemy import select

    from app.capabilities.registration.registry import _tool_spec
    from app.models.tool import Tool

    row = (await session.execute(select(Tool).where(Tool.name == name))).scalar_one()
    return _tool_spec(row)


# ---------- 出厂证 ----------


@pytest.mark.asyncio
async def test_builtin_capabilities_are_installed(client: AsyncClient) -> None:
    """内置能力应该已经被出厂源装进数据库，并标记为 builtin。

    文件类能力不在这里 —— 它们由 deepAgents 的文件系统中间件提供。
    """
    resp = await client.get(TOOLS, params={"origin": "builtin"})
    assert resp.status_code == 200
    names = {item["name"] for item in resp.json()["data"]["items"]}
    assert {"run_python", "web_fetch", "web_search"} <= names
    # 文件类能力不该再作为出厂内置能力出现
    assert not ({"read_file", "write_file", "list_files", "run_bash"} & names)


@pytest.mark.asyncio
async def test_sync_builtins_does_not_overwrite_operator_changes(
    client: AsyncClient, session
) -> None:
    """运营改过的描述不该被启动同步冲掉。"""
    from sqlalchemy import select

    from app.capabilities.registration.registry import CapabilityRegistry
    from app.models.tool import Tool

    listing = await client.get(TOOLS, params={"origin": "builtin"})
    target = next(item for item in listing.json()["data"]["items"] if item["name"] == "run_python")
    await client.put(f"{TOOLS}/{target['id']}", json={"description": "运营改过的说明"})

    # 再同步一次
    await CapabilityRegistry.sync_builtins(session)
    await session.commit()

    row = (await session.execute(select(Tool).where(Tool.name == "run_python"))).scalar_one()
    assert row.description == "运营改过的说明"


@pytest.mark.asyncio
async def test_superseded_builtin_capabilities_are_retired(client: AsyncClient, session) -> None:
    """被 deepAgents 接管的出厂能力要被下线，不能留在清单里当工具。"""
    from sqlalchemy import select

    from app.capabilities.registration.registry import CapabilityRegistry
    from app.models.tool import Tool

    # 造一条"以前装过、现在没人提供"的出厂能力
    session.add(
        Tool(
            name="read_file",
            display_name="读取文件",
            description="老的能力",
            type="builtin",
            source="app.capabilities.registration.builtin.fs:make_read_file",
            config={},
            parameters={},
            enabled=True,
            origin="builtin",
            tags=["文件"],
            permission="none",
            risk="read",
            timeout_s=10,
            retry_policy={},
            annotations={},
            version=1,
        )
    )
    await session.commit()

    await CapabilityRegistry.sync_builtins(session)
    await session.commit()

    row = (await session.execute(select(Tool).where(Tool.name == "read_file"))).scalar_one()
    assert row.enabled is False
    assert not row.source


@pytest.mark.asyncio
async def test_builtin_specs_carry_governance_declarations(client: AsyncClient) -> None:
    """内置能力出厂时就要带齐声明：风险、权限、超时、重试、注解。"""
    resp = await client.get(f"{CAPS}/run_python/spec")
    assert resp.status_code == 200
    spec = resp.json()["data"]
    assert spec["risk"] == "execute"
    # 审批按风险档位判，出厂能力的权限要求一律 none
    assert spec["permission"] == "none"
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
    assert "run_python" in keys  # 出厂内置能力
    assert "本地 MCP" in keys
    assert "读文件技能" in keys


@pytest.mark.asyncio
async def test_manifest_filters_by_kind_and_tag(client: AsyncClient) -> None:
    only_tools = await client.get(CAPS, params={"kind": "tool"})
    assert all(item["kind"] == "tool" for item in only_tools.json()["data"]["items"])

    by_tag = await client.get(CAPS, params={"tag": "网络"})
    assert by_tag.json()["data"]["total"] >= 1


@pytest.mark.asyncio
async def test_get_capability_by_key(client: AsyncClient) -> None:
    resp = await client.get(f"{CAPS}/run_python")
    assert resp.status_code == 200
    assert resp.json()["data"]["kind"] == "tool"
    assert resp.json()["data"]["origin"] == "builtin"


@pytest.mark.asyncio
async def test_get_missing_capability_returns_404(client: AsyncClient) -> None:
    resp = await client.get(f"{CAPS}/根本没有这个能力")
    assert resp.status_code == 404


# ---------- 执行层 ----------


@pytest.mark.asyncio
async def test_invoke_builtin_capability(client: AsyncClient, session) -> None:
    """直接调一次能力，写进去再读出来。"""
    await _install_write_tool(session, "test_write")
    written = await client.post(
        f"{CAPS}/test_write/invoke", json={"path": "invoked.txt", "content": "hello"}
    )
    assert written.status_code == 200
    assert written.json()["data"]["ok"] is True
    assert "hello" not in written.json()["data"]["content"]  # 只回显状态，不回显内容


@pytest.mark.asyncio
async def test_invoke_missing_capability_returns_404(client: AsyncClient) -> None:
    resp = await client.post(f"{CAPS}/没有这个能力/invoke", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoke_records_invocation(client: AsyncClient, session) -> None:
    """执行层统一留痕：调用记录落在 invocation 表里。"""
    await _install_write_tool(session, "test_write")
    await client.post(
        f"{CAPS}/test_write/invoke",
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
    assert rows[0].capability_key == "test_write"
    assert rows[0].status == "succeeded"
    # 各阶段结论也记下来了
    assert {stage["stage"] for stage in rows[0].stages} >= {"schema", "permission", "risk"}


@pytest.mark.asyncio
async def test_invoke_rejects_arguments_violating_schema(client: AsyncClient, session) -> None:
    """入参不符合声明的 schema 就该被挡下，而不是把脏数据带进执行。"""
    await _install_write_tool(session, "test_write")
    resp = await client.post(f"{CAPS}/test_write/invoke", json={"path": "x.txt"})
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


def test_export_skills_writes_skill_md(tmp_path):
    """技能要导出成 deepAgents 认的目录结构：每个技能一个目录 + SKILL.md。"""
    specs = [
        _skill("code-review", ["代码"], knowledge="逐行检查潜在问题。"),
        _skill("deploy", ["部署"]),
    ]
    sources = export_skills(specs, tmp_path / "skills")

    assert sources == ["/skills/"]
    skill_dir = tmp_path / "skills" / "code-review"
    assert (skill_dir / "SKILL.md").exists()
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: code-review" in text
    assert "逐行检查潜在问题。" in text


def test_export_skills_keeps_chinese_name_in_description(tmp_path):
    """中文技能名转不出合法目录名 —— 退化成哈希名，但中文名要留在描述里。

    否则模型看到的是一串看不懂的目录名，认不出这个技能是干什么的。
    """
    specs = [_skill("代码审查", ["代码"], knowledge="逐行检查。")]
    export_skills(specs, tmp_path / "skills")

    dirs = [p for p in (tmp_path / "skills").iterdir() if p.is_dir()]
    assert len(dirs) == 1
    text = (dirs[0] / "SKILL.md").read_text(encoding="utf-8")
    assert "代码审查" in text
    assert dirs[0].name.startswith("skill-")


def test_export_skills_respects_explicit_ids(tmp_path):
    """显式指定了技能 id 就只导出那几个。"""
    specs = [_skill("A", ["x"]), _skill("B", ["y"])]
    specs[0].db_id = 1
    specs[1].db_id = 2
    export_skills(specs, tmp_path / "skills", skill_ids=[2])
    assert (tmp_path / "skills" / "B" / "SKILL.md").exists()
    assert not (tmp_path / "skills" / "A").exists()


def test_export_skills_clears_stale_directory(tmp_path):
    """上一次导出的技能不该留在目录里 —— 模型会看到已经不存在的技能。"""
    skills_dir = tmp_path / "skills"
    export_skills([_skill("old-skill", ["x"])], skills_dir)
    assert (skills_dir / "old-skill").exists()

    export_skills([], skills_dir)
    assert not skills_dir.exists()


def test_render_skill_md_puts_knowledge_before_constraints():
    skill = _skill("写测试", ["测试"], knowledge="先写用例。")
    skill.checklist = ["用例覆盖边界"]
    skill.constraints = "不要写脆弱断言"
    text = render_skill_md(skill, "write-test")
    assert text.index("先写用例。") < text.index("不要写脆弱断言")


def test_slugify_rejects_illegal_characters():
    """技能目录名必须合法：小写 ASCII 字母 / 数字 / 单个连字符。"""
    from app.capabilities.skills_export import _slugify

    assert _slugify("My Skill!!") == "my-skill"
    assert _slugify("--a--b--") == "a-b"


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
    await _install_write_tool(session, "test_write")
    spec = await _spec_of(session, "test_write")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve({"name": "test_write", "args": {"path": "a.txt", "content": "x"}}, [spec])
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
    await _install_write_tool(session, "test_write")
    spec = await _spec_of(session, "test_write")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve({"name": "test_write", "args": {"path": "a.txt"}}, [spec], validate=False)
    result = await execute(intent, context)

    assert result.ok is False
    assert result.error.code == ErrorCode.SCHEMA_INVALID
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_pipeline_requires_approval_for_execute_in_balanced_mode(session, tmp_path):
    await _install_exec_tool(session, "test_exec")
    spec = await _spec_of(session, "test_exec")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve(
        {"name": "test_exec", "args": {"command": "echo hi"}}, [spec], trust_mode="balanced"
    )
    result = await execute(intent, context)

    assert result.ok is False
    assert result.error.code == ErrorCode.APPROVAL_REQUIRED
    assert result.invocation_id is not None


@pytest.mark.asyncio
async def test_pipeline_runs_in_permissive_mode(session, tmp_path):
    await _install_exec_tool(session, "test_exec")
    spec = await _spec_of(session, "test_exec")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = resolve(
        {"name": "test_exec", "args": {"command": "echo hi"}}, [spec], trust_mode="permissive"
    )
    result = await execute(intent, context)

    assert result.ok is True
    assert "hi" in result.content


@pytest.mark.asyncio
async def test_pipeline_dedupes_idempotent_calls(session, tmp_path):
    """同键的重复调用不重复执行。"""
    await _install_write_tool(session, "test_write")
    spec = await _spec_of(session, "test_write")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    context.thread_id = "idem-thread"
    intent = resolve(
        {"name": "test_write", "args": {"path": "a.txt", "content": "x"}},
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
async def test_approval_flow(client: AsyncClient, session) -> None:
    """待审批 → 批准 → 执行。"""
    await _install_exec_tool(session, "test_exec")
    pending = await client.post(
        f"{CAPS}/test_exec/invoke",
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
async def test_deny_flow(client: AsyncClient, session) -> None:
    await _install_exec_tool(session, "test_exec")
    pending = await client.post(
        f"{CAPS}/test_exec/invoke", json={"command": "echo nope"}, params={"thread_id": "deny"}
    )
    invocation_id = pending.json()["data"]["invocation_id"]
    denied = await client.post(f"{INVOCATIONS}/{invocation_id}/deny", json={"reason": "不放心"})
    assert denied.status_code == 200
    assert denied.json()["data"]["status"] == "denied"


@pytest.mark.asyncio
async def test_cannot_approve_twice(client: AsyncClient, session) -> None:
    await _install_exec_tool(session, "test_exec")
    pending = await client.post(
        f"{CAPS}/test_exec/invoke", json={"command": "echo x"}, params={"thread_id": "twice"}
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


# ---------- 重试策略 ----------


def _retry_spec(*, idempotent: bool, retry_on=("TIMEOUT",), attempts: int = 3):
    spec = _tool("run_bash")
    spec.risk = "execute"
    spec.retry = RetryPolicy(max_attempts=attempts, retry_on=tuple(retry_on))
    spec.annotations = Annotations(idempotent=idempotent)
    return spec


def test_retry_requires_idempotency():
    """不幂等的操作不自动重试 —— 重跑可能把副作用做两遍。"""
    from app.capabilities.execution.pipeline import _can_retry

    timeout = CapabilityError.of(ErrorCode.TIMEOUT, "超时")
    idempotent = _retry_spec(idempotent=True)
    assert _can_retry(idempotent, timeout, attempt=1, attempts=3) is True

    not_idempotent = _retry_spec(idempotent=False)
    assert _can_retry(not_idempotent, timeout, attempt=1, attempts=3) is False


def test_retry_requires_error_to_be_retryable():
    from app.capabilities.execution.pipeline import _can_retry

    spec = _retry_spec(idempotent=True)
    fatal = CapabilityError.of(ErrorCode.EXECUTION_ERROR, "代码写错了")
    assert _can_retry(spec, fatal, attempt=1, attempts=3) is False


def test_retry_respects_error_code_allowlist():
    from app.capabilities.execution.pipeline import _can_retry

    spec = _retry_spec(idempotent=True, retry_on=("NETWORK_ERROR",))
    timeout = CapabilityError.of(ErrorCode.TIMEOUT, "超时")
    assert _can_retry(spec, timeout, attempt=1, attempts=3) is False


def test_retry_respects_attempt_budget():
    from app.capabilities.execution.pipeline import _can_retry

    spec = _retry_spec(idempotent=True, attempts=2)
    timeout = CapabilityError.of(ErrorCode.TIMEOUT, "超时")
    assert _can_retry(spec, timeout, attempt=1, attempts=2) is True
    assert _can_retry(spec, timeout, attempt=2, attempts=2) is False


def test_backoff_is_exponential_with_cap():
    policy = RetryPolicy(max_attempts=5, backoff_s=1.0, exponential=True, max_backoff_s=4.0)
    assert policy.backoff_for(1) == 1.0
    assert policy.backoff_for(2) == 2.0
    assert policy.backoff_for(3) == 4.0
    assert policy.backoff_for(4) == 4.0  # 封顶


def test_backoff_can_be_fixed():
    policy = RetryPolicy(max_attempts=5, backoff_s=1.0, exponential=False)
    assert policy.backoff_for(1) == 1.0
    assert policy.backoff_for(3) == 1.0


def test_non_idempotent_capability_refuses_model_retry_on_timeout():
    """超时的能力不幂等时，不许模型重试 —— 重跑可能把副作用做两遍。"""
    from app.capabilities.execution.pipeline import _apply_idempotency

    spec = _retry_spec(idempotent=False)
    error = CapabilityError.of(ErrorCode.TIMEOUT, "执行超时（>60s），已终止")
    adjusted = _apply_idempotency(spec, error)
    assert adjusted.retryable is False
    assert "不要重复执行" in adjusted.message

    idempotent = _retry_spec(idempotent=True)
    ok_error = _apply_idempotency(idempotent, CapabilityError.of(ErrorCode.TIMEOUT, "超时"))
    assert ok_error.retryable is True


@pytest.mark.asyncio
async def test_timeout_on_non_idempotent_capability_is_not_retryable(tmp_path):
    """端到端：跑一条会超时的命令，错误不该邀请模型重试。"""
    from app.capabilities.execution.pipeline import execute

    context = ExecutionContext.build(workspace=tmp_path)
    context.sandbox_type = "local"
    context.sandbox_timeout = 1  # 沙箱内部超时压到 1s
    spec = CapabilitySpec(
        key="test_exec",
        source_type="builtin",
        locator={"source": "tests.fake_capability:make_test_exec"},
        risk="execute",
        timeout_s=1,
        retry=RetryPolicy(max_attempts=3, backoff_s=0.1, retry_on=("TIMEOUT",)),
        annotations=Annotations(destructive=True, open_world=True),  # 不幂等
    )
    intent = CapabilityIntent(
        capability_key="test_exec",
        arguments={"command": "sleep 5"},
        spec=spec,
        # 这条用例测的是超时与重试，不是审批 —— 审批已经在 agent 路径上过了
        pre_approved=True,
    )
    result = await execute(intent, context)
    assert result.ok is False
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.error.retryable is False
    assert "不要重复执行" in result.error.message


# ---------- 熔断 ----------


def test_breaker_opens_after_repeated_failures():
    from app.capabilities.execution.breaker import CircuitBreaker

    breaker = CircuitBreaker(window_seconds=60, min_samples=4, error_rate=0.5, cooldown_seconds=30)
    for _ in range(4):
        breaker.record("flaky", ok=False, duration_ms=10)
    assert breaker.is_open("flaky") is True


def test_breaker_stays_closed_when_errors_are_few():
    from app.capabilities.execution.breaker import CircuitBreaker

    breaker = CircuitBreaker(window_seconds=60, min_samples=5, error_rate=0.9)
    breaker.record("ok_one", ok=False, duration_ms=10)
    breaker.record("ok_two", ok=True, duration_ms=10)
    assert breaker.is_open("ok_one") is False


def test_breaker_does_not_count_slow_successes_as_failures():
    """**慢不等于失败** —— 慢的成功不该把能力误熔断。

    ``run_python`` 正常跑 6 秒是常事。把它记成失败，五次正常执行就能
    把能力熔断，之后每次调用都被快速失败 —— 比慢更糟。
    """
    from app.capabilities.execution.breaker import CircuitBreaker

    breaker = CircuitBreaker(
        window_seconds=60, min_samples=3, error_rate=0.5, cooldown_seconds=30, latency_ms=100
    )
    for _ in range(3):
        breaker.record("slow", ok=True, duration_ms=500)
    assert breaker.is_open("slow") is False
    # 但慢这件事本身要被记下来 —— 事后查得到
    assert breaker.state("slow")["degraded"] == 3
    # 错误率只算真失败
    assert breaker.state("slow")["error_rate"] == 0.0


def test_breaker_opens_on_real_failures():
    """真失败还是照样熔断 —— 上面的改动不能把熔断器整个废掉。"""
    from app.capabilities.execution.breaker import CircuitBreaker

    breaker = CircuitBreaker(
        window_seconds=60, min_samples=3, error_rate=0.5, cooldown_seconds=30, latency_ms=100
    )
    for _ in range(3):
        breaker.record("broken", ok=False, duration_ms=10)
    assert breaker.is_open("broken") is True


# ---------- 任务状态：置信度 / 阻塞 ----------


@pytest.mark.asyncio
async def test_confidence_drops_on_format_errors(session):
    """格式类错误：扣得轻 —— 模型改改参数就能对。"""
    from app.capabilities.execution.task_state import load_task_state
    from app.models.invocation import Invocation

    for _ in range(2):
        session.add(
            Invocation(
                thread_id="conf-1",
                capability_key="write_file",
                arguments={},
                status="failed",
                error_code="SCHEMA_INVALID",
            )
        )
    await session.commit()
    state = await load_task_state(session, "conf-1", "write_file")
    assert state.confidence < 100
    assert state.confidence >= 80  # 格式类扣得轻
    assert state.blocked is False


@pytest.mark.asyncio
async def test_confidence_drops_faster_on_semantic_errors(session):
    """语义类错误：扣得重 —— 说明模型的判断本身有问题。"""
    from app.capabilities.execution.task_state import load_task_state
    from app.models.invocation import Invocation

    for _ in range(2):
        session.add(
            Invocation(
                thread_id="conf-2",
                capability_key="write_file",
                arguments={},
                status="failed",
                error_code="EXECUTION_ERROR",
            )
        )
    await session.commit()
    state = await load_task_state(session, "conf-2", "write_file")
    assert state.confidence <= 50
    assert state.semantic_errors == 2


@pytest.mark.asyncio
async def test_repeated_format_errors_block_the_task(session):
    """同一能力连续多次参数错误 → 任务阻塞，转人工。"""
    from app.capabilities.execution.task_state import load_task_state
    from app.models.invocation import Invocation

    for _ in range(3):
        session.add(
            Invocation(
                thread_id="conf-3",
                capability_key="write_file",
                arguments={},
                status="failed",
                error_code="SCHEMA_INVALID",
            )
        )
    await session.commit()
    state = await load_task_state(session, "conf-3", "write_file")
    assert state.blocked is True
    assert "连续" in state.reason


@pytest.mark.asyncio
async def test_blocked_task_refuses_further_calls(session, tmp_path):
    """阻塞之后不再执行，直接转人工。"""
    from app.capabilities.execution.pipeline import execute
    from app.models.invocation import Invocation

    await _install_write_tool(session, "test_write")
    for _ in range(3):
        session.add(
            Invocation(
                thread_id="blocked-1",
                capability_key="test_write",
                arguments={},
                status="failed",
                error_code="SCHEMA_INVALID",
            )
        )
    await session.commit()

    spec = await _spec_of(session, "test_write")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    context.thread_id = "blocked-1"
    intent = CapabilityIntent(
        capability_key="test_write",
        arguments={"path": "a.txt", "content": "x"},
        spec=spec,
        thread_id="blocked-1",
    )
    result = await execute(intent, context)
    assert result.ok is False
    assert result.error.code == ErrorCode.TASK_BLOCKED
    assert result.blocked is True


# ---------- 权限类错误转审批 ----------


@pytest.mark.asyncio
async def test_capability_outside_allowed_set_goes_to_approval(session, tmp_path):
    """权限类错误不许模型重试，但可以转人工授权。"""
    from app.capabilities.execution.pipeline import execute

    await _install_write_tool(session, "test_write")
    spec = await _spec_of(session, "test_write")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    intent = CapabilityIntent(
        capability_key="test_write",
        arguments={"path": "a.txt", "content": "x"},
        spec=spec,
        allowed_keys={"别的能力"},
    )
    result = await execute(intent, context)
    assert result.ok is False
    assert result.error.code == ErrorCode.APPROVAL_REQUIRED
    assert result.invocation_id is not None


@pytest.mark.asyncio
async def test_task_blocks_as_soon_as_confidence_crosses_threshold(session, tmp_path):
    """置信度跌破阈值的那一刻就标记阻塞，不等下一次调用。"""
    from app.capabilities.execution.pipeline import execute

    await _install_read_tool(session, "test_read")
    spec = await _spec_of(session, "test_read")
    context = ExecutionContext.build(workspace=tmp_path)
    context.session = session
    context.thread_id = "cross-thread"
    intent = CapabilityIntent(
        capability_key="test_read",
        arguments={"path": "不存在.txt"},
        spec=spec,
        thread_id="cross-thread",
    )
    # 先攒两次语义类错误（100 → 75 → 50）
    for _ in range(2):
        await execute(intent, context)
        await session.commit()

    result = await execute(intent, context)
    await session.commit()
    # 第三次：100-25*3 = 25 < 40 → 立即阻塞
    assert result.confidence == 25.0
    assert result.blocked is True


def test_middleware_tells_model_to_stop_when_blocked():
    """阻塞时给模型的提示要说清楚"停下来"，而不是让它继续撞。

    新架构里这条提示由中间件的 ``_with_blocked_notice`` 追加在工具消息末尾。
    """
    from langchain_core.messages import ToolMessage

    from app.capabilities.intent.middleware import _with_blocked_notice

    message = _with_blocked_notice(ToolMessage(content="文件不存在", tool_call_id="c1"))
    text = str(message.content)
    assert "任务已阻塞" in text
    assert "停止重试" in text


def test_middleware_distinguishes_error_types():
    """不同失败类型给模型不同的指令。

    新架构里失败文本由 ``CapabilityError.to_text()`` 统一渲染：
    可重试的说"修正参数后重试"，不可重试的说"换个思路，不要重复同样的调用"。
    """
    schema_error = CapabilityError.of(ErrorCode.SCHEMA_INVALID, "缺少 content")
    assert "修正参数后重试" in schema_error.to_text()

    permission_error = CapabilityError.of(ErrorCode.PERMISSION_DENIED, "能力已停用")
    assert "不要重复同样的调用" in permission_error.to_text()


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


# ---------- 工具更新：入参 schema 只对 http 类型有意义 ----------


@pytest.mark.asyncio
async def test_update_builtin_tool_without_parameters_is_allowed(client: AsyncClient) -> None:
    """改 builtin 工具的名字，不该被入参 schema 拦下。

    builtin / mcp 不需要入参 schema —— 空字典是正常的。之前更新接口一律校验
    parameters，改个名字都改不了。
    """
    created = await client.post(
        TOOLS,
        json={
            "name": "demo_builtin_tool",
            "type": "builtin",
            "source": "app.capabilities.registration.builtin.sandbox:make_run_python",
            "description": "测试用",
            "parameters": {},
        },
    )
    assert created.status_code == 201
    tool_id = created.json()["data"]["id"]

    updated = await client.put(
        f"{TOOLS}/{tool_id}",
        json={"name": "demo_builtin_tool_2", "parameters": {}},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "demo_builtin_tool_2"


@pytest.mark.asyncio
async def test_update_http_tool_still_requires_parameters_schema(client: AsyncClient) -> None:
    """http 类型还是要入参 schema —— 校验没被放松掉。"""
    created = await client.post(
        TOOLS,
        json={
            "name": "demo_http_tool",
            "type": "http",
            "config": {"url": "https://example.com/api"},
            "parameters": {"type": "object", "properties": {}},
        },
    )
    assert created.status_code == 201
    tool_id = created.json()["data"]["id"]

    bad = await client.put(f"{TOOLS}/{tool_id}", json={"parameters": {}})
    assert bad.status_code == 422
