"""agent 调用能力的补强：审批结果回流、执行摘要、审批提示、能力集收敛。"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_service
from app.capabilities.execution.result import execution_artifact
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.registration.policy import approval_decision
from app.capabilities.registration.spec import CapabilitySpec
from app.capabilities.registration.tool_policy import FS_TOOL_NAMES
from app.core.config import settings
from app.core.workspace import resolve_workspace
from app.services.agent import AgentService

API = "/api/v1/agent"


class RecordingModel(BaseChatModel):
    """记录每次收到的消息。

    第一次返回一个 tool_call，之后回文本 —— 模拟"模型思考 → 调工具 → 看结果"。
    """

    tool_name: str = ""
    tool_args: dict[str, Any] = {}
    reply: str = "已完成。"
    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "recording"

    def bind_tools(self, tools: Any, **kwargs: Any) -> RecordingModel:
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        if self.tool_name and len(self.seen) == 1:
            call = {
                "name": self.tool_name,
                "args": dict(self.tool_args),
                "id": "call_1",
                "type": "tool_call",
            }
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


class ParallelToolModel(BaseChatModel):
    """第一次返回**两个并行**的 tool_call，之后回文本。

    模拟"模型一轮里并行发好几个工具调用" —— 它们会一起挂起等人批。
    """

    reply: str = "已完成。"
    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "parallel"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ParallelToolModel:
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        if len(self.seen) == 1:
            calls = [
                {
                    "name": "execute",
                    "args": {"command": "echo first"},
                    "id": "call_1",
                    "type": "tool_call",
                },
                {
                    "name": "execute",
                    "args": {"command": "echo second"},
                    "id": "call_2",
                    "type": "tool_call",
                },
            ]
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=calls))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


def _override(app, model: RecordingModel, session) -> None:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )


def _spec(
    key: str,
    *,
    kind: str = "tool",
    permission: str = "none",
    risk: str = "read",
    origin: str = "user",
) -> CapabilitySpec:
    return CapabilitySpec(
        key=key,
        kind=kind,
        permission=permission,
        risk=risk,
        origin=origin,
        enabled=True,
    )


# ---------- 审批结果回流 ----------


@pytest.mark.asyncio
async def test_approval_round_trip_pushes_result_back(app, client, session, monkeypatch) -> None:
    """端到端：agent 触发审批 → 人工批准 → 图恢复 → 工具真的执行了。

    新契约：审批走 LangGraph 中断。批准后**图恢复、agent 自己接着往下跑**，
    结果直接回到它的上下文 —— 不再需要"下一轮把结果补给它"那套回流机制。

    用 ``delete`` 而不是 ``execute``：删除在本地跑，测试不依赖 docker；
    删文件是**破坏性（高档）**—— 不可逆，永远要人批，跟信任模式无关。
    """
    thread_id = "t-approve"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "out.txt").write_text("写点东西", encoding="utf-8")

    model = RecordingModel(
        tool_name="delete",
        tool_args={"file_path": "out.txt"},
        reply="删完了",
    )
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "删个文件", "thread_id": thread_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 破坏性操作要人批 → 这一轮挂起了
    assert data["status"] == "pending_approval"
    assert data["pending"], "挂起时应该给出待审批条目"
    assert data["pending"][0]["tool"] == "delete"

    # 人工批准 → 恢复图
    approve = await client.post(f"{API}/{thread_id}/approve")
    assert approve.status_code == 200, approve.text
    approved = approve.json()["data"]
    assert approved["status"] == "completed"
    assert "删完了" in approved["content"]
    # 工具真的执行了：文件没了
    assert approved["tool_calls"], "恢复后应该有工具调用"
    assert approved["tool_calls"][0]["name"] == "delete"
    assert not (workspace / "out.txt").exists()


@pytest.mark.asyncio
async def test_parallel_calls_are_approved_together(app, client, session) -> None:
    """并行发好几个工具调用时，它们会**一起**挂起 —— 批准要一次全批。

    HITL 校验的是"决定数 = 挂起的调用数"：只给一个决定会报
    "Number of human decisions (1) does not match number of hanging tool
    calls (2)"，整轮直接失败。所以待批清单要**按条**列出来，恢复时也要
    **按条**给决定。
    """
    thread_id = "t-parallel-approve"
    model = ParallelToolModel(reply="两条都跑完了")
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "跑两条命令", "thread_id": thread_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "pending_approval"
    # 两个调用都挂起了 —— 清单里要有两条
    assert len(data["pending"]) == 2, data["pending"]
    assert {item["tool"] for item in data["pending"]} == {"execute"}

    approve = await client.post(f"{API}/{thread_id}/approve")
    assert approve.status_code == 200, approve.text
    approved = approve.json()["data"]
    assert approved["status"] == "completed"
    assert "两条都跑完了" in approved["content"]


@pytest.mark.asyncio
async def test_rejected_call_tells_agent(app, client, session) -> None:
    """被拒绝的调用也要告诉 agent —— 它得知道这条路走不通，别再试。"""
    thread_id = "t-deny-new"
    model = RecordingModel(tool_name="execute", tool_args={"command": "rm -rf /"}, reply="好的")
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "删一下", "thread_id": thread_id})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "pending_approval"

    reject = await client.post(f"{API}/{thread_id}/reject")
    assert reject.status_code == 200, reject.text
    # 拒绝后图恢复，工具不执行
    assert reject.json()["data"]["status"] == "completed"


# ---------- 执行摘要 ----------


@pytest.mark.asyncio
async def test_tool_call_carries_execution_summary(app, client, session) -> None:
    """调用方要知道这次调用成功了还是失败了，不能只知道模型动了手。"""
    thread_id = "t-tool"
    # 与 AgentService.resolve_workspace 同一套路径规则（含 resolve）
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "a.txt").write_text("hello", encoding="utf-8")

    # read_file 是 deepAgents 的文件工具（本地执行），参数名是 file_path
    model = RecordingModel(tool_name="read_file", tool_args={"file_path": "a.txt"}, reply="读完了")
    _override(app, model, session)

    payload = {"message": "读一下 a.txt", "thread_id": thread_id}
    resp = await client.post(API + "/chat", json=payload)
    assert resp.status_code == 200
    calls = resp.json()["data"]["tool_calls"]
    assert len(calls) == 1
    call = calls[0]
    assert call["name"] == "read_file"
    assert call["ok"] is True
    assert call["status"] == "succeeded"
    assert call["invocation_id"]
    assert call["duration_ms"] >= 0


def test_execution_artifact_marks_pending_approval() -> None:
    from app.capabilities.errors import CapabilityError, ErrorCode
    from app.capabilities.execution.result import ExecutionResult

    pending = ExecutionResult.failed(
        CapabilityError("需要审批", code=ErrorCode.APPROVAL_REQUIRED), duration_ms=5
    )
    artifact = execution_artifact(pending)
    assert artifact["status"] == "pending"
    assert artifact["ok"] is False
    assert artifact["error_code"] == "APPROVAL_REQUIRED"


# ---------- 审批规则与提示 ----------


@pytest.mark.parametrize(
    ("permission", "risk", "mode", "expected"),
    [
        # 低档（只读、写入）：自动放行 —— 改错了能改回来
        ("none", "read", "balanced", "approved"),
        ("none", "read", "strict", "approved"),
        ("none", "write", "balanced", "approved"),
        ("none", "write", "strict", "approved"),
        # 中档（执行命令、联网）：strict / balanced 要人批，permissive 放行
        ("none", "execute", "balanced", "pending"),
        ("none", "network", "balanced", "pending"),
        ("none", "execute", "strict", "pending"),
        ("none", "execute", "permissive", "approved"),
        # 高档（破坏性）：永远要人批，permissive 也不放过
        ("none", "destructive", "balanced", "pending"),
        ("none", "destructive", "permissive", "pending"),
        # 显式声明"要人确认"：运营额外要求，permissive 才放宽
        ("confirm", "read", "balanced", "pending"),
        ("confirm", "write", "balanced", "pending"),
        ("confirm", "read", "permissive", "approved"),
        # 管理员放行：谁也放宽不了
        ("admin", "read", "permissive", "pending"),
        ("admin", "read", "strict", "pending"),
    ],
)
def test_approval_decision(permission: str, risk: str, mode: str, expected: str) -> None:
    assert approval_decision(permission, risk, mode) == expected


def test_低档不需要审批_中高档才需要() -> None:
    """用户定的口径：只有中高档才需要人工审批。"""
    from app.capabilities.registration.policy import risk_tier

    assert risk_tier("read") == "low"
    assert risk_tier("write") == "low"
    assert risk_tier("execute") == "medium"
    assert risk_tier("network") == "medium"
    assert risk_tier("destructive") == "high"


def test_文件工具的审批按风险档位() -> None:
    """deepAgents 的文件工具：低档自动放行，中档（执行命令）与高档（删除）要人批。"""
    from app.capabilities.registration.tool_policy import approval_tool_names

    balanced = approval_tool_names("balanced")
    assert "execute" in balanced  # 中档
    assert "delete" in balanced  # 高档
    assert "write_file" not in balanced  # 低档：改错了能改回来
    assert "read_file" not in balanced

    permissive = approval_tool_names("permissive")
    assert "execute" not in permissive  # 中档被放宽
    assert "delete" in permissive  # 高档：不可逆，谁也不放过


def test_出厂能力的风险档位() -> None:
    """不可逆的 git 操作归高档，派子 agent 归中档。"""
    from app.capabilities.registration.builtin import list_builtin_specs

    specs = {spec.key: spec for spec in list_builtin_specs()}
    assert specs["git_worktree_remove"].risk == "destructive"
    assert specs["git_merge"].risk == "destructive"
    assert specs["delegate_worker"].risk == "execute"
    assert specs["git_commit"].risk == "write"
    # 出厂能力的权限要求一律 none —— 档位说了算
    assert all(spec.permission == "none" for spec in specs.values())


def test_policy_note_lists_capabilities_needing_approval() -> None:
    """模型要提前知道哪些能力会要人批 —— 不知道就会反复触发审批、反复中断。"""
    specs = [
        _spec("read_file", permission="none", risk="read", origin="builtin"),
        _spec("execute", permission="none", risk="execute", origin="builtin"),
    ]
    middleware = CapabilityMiddleware(specs, trust_mode="balanced")
    note = middleware._policy_note()
    assert "execute" in note
    assert "read_file" not in note
    assert "人工批准" in note


def test_policy_note_respects_trust_mode() -> None:
    """信任模式决定哪些工具要人批 —— 提示词里的口径必须和实际拦截的一致。"""
    # 文件工具的审批按映射表 + 信任模式算：permissive 只拦破坏性，strict 连中档都拦。
    permissive = CapabilityMiddleware([], trust_mode="permissive")._policy_note()
    assert "execute" not in permissive
    assert "delete" in permissive  # 破坏性：permissive 也不放过
    strict = CapabilityMiddleware([], trust_mode="strict")._policy_note()
    assert "execute" in strict
    # 低档的写文件在 strict 下也自动放行 —— 只有中高档才要审批
    assert "write_file" not in strict


def test_policy_note_counts_exposed_capabilities() -> None:
    """工具数量 = 我们自己的能力 + deepAgents 的文件工具 —— 提示词里两个数字要一致。"""
    specs = [
        _spec("web_fetch", origin="user"),
        _spec("web_search", origin="user"),
    ]
    note = CapabilityMiddleware(specs, trust_mode="balanced")._policy_note()
    assert f"本次可用工具 {len(FS_TOOL_NAMES) + 2} 个" in note


@pytest.mark.asyncio
async def test_approved_own_tool_actually_runs(app, client, session) -> None:
    """批准之后，我们自己的工具要**真的跑起来**。

    agent 路径的审批走 deepAgents 的 HITL；执行层自己的审批阶段不能把它再拦一遍
    —— 两处各判一次，批准了等于没批准。
    """
    thread_id = "t-own-tool"
    model = RecordingModel(
        tool_name="run_python",
        tool_args={"code": "print('ok')"},
        reply="跑完了",
    )
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "跑一下", "thread_id": thread_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # run_python 是 confirm/execute，balanced 下要人批
    assert data["status"] == "pending_approval"
    assert data["pending"][0]["tool"] == "run_python"

    approve = await client.post(f"{API}/{thread_id}/approve")
    assert approve.status_code == 200, approve.text
    approved = approve.json()["data"]
    assert approved["status"] == "completed"
    calls = approved["tool_calls"]
    assert calls, "批准后应该有工具调用"
    assert calls[0]["name"] == "run_python"
    assert calls[0].get("ok") is True, f"批准后工具没有真正执行：{calls[0]}"


@pytest.mark.asyncio
async def test_one_call_one_approval(app, client, session) -> None:
    """一次调用只让人批一次。

    声明级（deepAgents HITL）与参数级（我们的 interrupt）是**两道闸，不是两轮审批**：
    声明级放行过的调用，参数级不再重复问 —— 否则同一条命令要人批两遍。
    """
    thread_id = "t-one-approval"
    model = RecordingModel(
        tool_name="execute",
        tool_args={"command": "echo hi"},
        reply="跑完了",
    )
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "跑个命令", "thread_id": thread_id})
    data = resp.json()["data"]
    assert data["status"] == "pending_approval"
    # balanced 模式下 execute 走声明级审批
    assert data["pending"][0]["kind"] == "declared"

    approve = await client.post(f"{API}/{thread_id}/approve")
    assert approve.status_code == 200, approve.text
    approved = approve.json()["data"]
    # 批准之后不该再挂起（没有第二轮审批）
    assert approved["status"] == "completed"
    assert approved["tool_calls"], "批准后工具应该真的执行了"
    assert approved["tool_calls"][0].get("ok") is True


@pytest.mark.asyncio
async def test_param_risk_still_gates_tools_that_need_no_approval(
    app, client, session, monkeypatch
) -> None:
    """平时不用人批的工具，碰到破坏性命令时要单独拎出来问一次。"""
    monkeypatch.setattr(settings, "capability_trust_mode", "permissive")

    thread_id = "t-param-only"
    model = RecordingModel(
        tool_name="execute",
        tool_args={"command": "rm -rf /tmp/qzd-canary"},
        reply="好",
    )
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "删个目录", "thread_id": thread_id})
    data = resp.json()["data"]
    assert data["status"] == "pending_approval"
    # permissive 下 execute 不用人批 —— 这一轮是参数级规则拦下来的
    assert data["pending"][0]["kind"] == "param_risk"
    assert data["pending"][0]["risk"] == "destructive"
