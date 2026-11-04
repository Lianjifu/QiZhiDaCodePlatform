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


class ParallelDeleteModel(BaseChatModel):
    """第一次返回**两个并行**的 ``delete``，之后回文本。

    用 ``delete`` 而不是 ``execute``：删除在本地跑，测试不依赖 docker；
    而且删文件是**破坏性（高档）** —— 一定挂起等人批，
    逐条批完能直接看**哪个文件真被删了**。
    """

    reply: str = "都删完了。"
    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "parallel-delete"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ParallelDeleteModel:
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
                    "name": "delete",
                    "args": {"file_path": "a.txt"},
                    "id": "call_1",
                    "type": "tool_call",
                },
                {
                    "name": "delete",
                    "args": {"file_path": "b.txt"},
                    "id": "call_2",
                    "type": "tool_call",
                },
            ]
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=calls))]
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
        # auto（默认）：按风险档位 —— 低档自动放行，中高档要人批
        ("none", "read", "auto", "approved"),
        ("none", "write", "auto", "approved"),
        ("none", "execute", "auto", "pending"),
        ("none", "network", "auto", "pending"),
        ("none", "destructive", "auto", "pending"),
        # manual：只读不问，其余都要人批
        ("none", "read", "manual", "approved"),
        ("none", "write", "manual", "pending"),
        ("none", "execute", "manual", "pending"),
        # plan（只读）：只有只读放行
        ("none", "read", "plan", "approved"),
        ("none", "write", "plan", "pending"),
        # bypass：全放行
        ("none", "execute", "bypass", "approved"),
        ("none", "destructive", "bypass", "approved"),
        # 显式声明"要人确认"：运营额外要求
        ("confirm", "read", "auto", "pending"),
        ("confirm", "write", "auto", "pending"),
        # 管理员放行：bypass 是"全放行"，也一起放（用户明确选的）
        ("admin", "read", "bypass", "approved"),
        # 老名字仍然认（配置里的老值不用改）
        ("none", "write", "balanced", "approved"),
        ("none", "write", "strict", "pending"),
        ("none", "execute", "permissive", "approved"),
    ],
)
def test_approval_decision(permission: str, risk: str, mode: str, expected: str) -> None:
    assert approval_decision(permission, risk, mode) == expected


def test_accept_edits_只拦破坏性() -> None:
    """接受编辑模式：文件编辑与命令都自动过，只有破坏性要人批。"""
    from app.capabilities.registration.policy import approval_decision as decide

    assert decide("none", "write", "accept_edits") == "approved"
    assert decide("none", "execute", "accept_edits") == "approved"
    assert decide("none", "network", "accept_edits") == "approved"
    # 不可逆的仍然要人批
    assert decide("none", "destructive", "accept_edits") == "pending"


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

    auto = approval_tool_names("auto")
    assert "execute" in auto  # 中档
    assert "delete" in auto  # 高档
    assert "write_file" not in auto  # 低档：改错了能改回来
    assert "read_file" not in auto

    # 手动模式：写类也要人批
    manual = approval_tool_names("manual")
    assert "write_file" in manual
    assert "edit_file" in manual
    assert "read_file" not in manual

    # 接受编辑：文件编辑与命令都自动，只有删除这种破坏性的要人批
    accept = approval_tool_names("accept_edits")
    assert "write_file" not in accept
    assert "edit_file" not in accept
    assert "execute" not in accept
    assert "delete" in accept

    # 绕过权限：什么都不问
    bypass = approval_tool_names("bypass")
    assert not bypass


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


def test_policy_note_respects_permission_mode() -> None:
    """权限模式决定哪些工具要人批 —— 提示词里的口径必须和实际拦截的一致。"""
    auto = CapabilityMiddleware([], trust_mode="auto")._policy_note()
    assert "execute" in auto
    assert "write_file" not in auto

    manual = CapabilityMiddleware([], trust_mode="manual")._policy_note()
    assert "execute" in manual
    assert "write_file" in manual  # 手动模式：写类也要人批

    bypass = CapabilityMiddleware([], trust_mode="bypass")._policy_note()
    assert "execute" not in bypass
    assert "delete" not in bypass  # 绕过权限：什么都不问


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
    """平时不用人批的工具，碰到破坏性命令时要单独拎出来问一次。

    ``accept_edits`` 下 ``execute`` 不用人批（中档自动放行）——
    但一条 ``rm -rf`` 是破坏性的，要单独问。
    """
    monkeypatch.setattr(settings, "capability_trust_mode", "accept_edits")

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
    # accept_edits 下 execute 不用人批 —— 这一轮是参数级规则拦下来的
    assert data["pending"][0]["kind"] == "param_risk"
    assert data["pending"][0]["risk"] == "destructive"


@pytest.mark.asyncio
async def test_bypass_mode_does_not_interrupt_on_param_risk(
    app, client, session, monkeypatch
) -> None:
    """绕过权限模式的承诺是「不会有人工审批」—— 参数级规则也不能挂起。

    判定仍然要跑（结果落进调用记录），只是不再拿它去拦人。
    """
    monkeypatch.setattr(settings, "capability_trust_mode", "bypass")
    monkeypatch.setattr(settings, "capability_allow_bypass", True)

    thread_id = "t-bypass-param"
    model = RecordingModel(
        tool_name="execute",
        tool_args={"command": "rm -rf /tmp/qzd-canary"},
        reply="好",
    )
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "删个目录", "thread_id": thread_id})
    data = resp.json()["data"]
    assert data["status"] == "completed", "绕过权限模式不该挂起等人批"
    assert not data.get("pending")


# ---------- 阶段 0：参数级风险规则的回归测试 ----------


def test_参数级规则覆盖源码参数() -> None:
    """``run_python`` 的参数叫 ``code`` —— 破坏性内容不能从它眼皮底下溜过去。

    只按 ``command`` 匹配的话，一段 ``shutil.rmtree("/")`` 完全逃逸。
    """
    from app.capabilities.execution.risk import assess_call

    destructive = assess_call("run_python", {"code": "import shutil; shutil.rmtree('/')"})
    assert destructive.risk == "destructive"
    assert destructive.require_admin
    assert destructive.threat

    # 正常的代码不该被当成危险
    benign = assess_call("run_python", {"code": "print(sum(range(10)))"})
    assert not benign.threat
    assert benign.risk != "destructive"


def test_参数级规则覆盖命令参数() -> None:
    """shell 命令里的破坏性特征照样要命中。"""
    from app.capabilities.execution.risk import assess_call

    hit = assess_call("execute", {"command": "rm -rf /tmp/x"})
    assert hit.risk == "destructive"
    assert hit.require_admin

    # 内网地址 = SSRF 面
    ssrf = assess_call("web_fetch", {"url": "http://169.254.169.254/latest/meta-data/"})
    assert ssrf.risk == "destructive"
    assert ssrf.require_admin


def test_审批卡片按参数算风险而不是工具基线() -> None:
    """一条 ``rm -rf`` 走的也是 ``execute`` 工具 —— 卡片不能报「中档 · 执行命令」。

    报错了人就会以为只是跑个命令，实际是在删东西。
    """
    from app.agents.runner import _pending_entries

    payload = {
        "action_requests": [
            {"name": "execute", "args": {"command": "rm -rf /tmp/x"}},
            {"name": "execute", "args": {"command": "ls -la"}},
        ]
    }
    entries = _pending_entries(payload)
    assert entries[0]["risk"] == "destructive", "破坏性命令要报高档"
    assert entries[0]["reasons"], "要说明为什么危险"
    # 普通命令还是中档
    assert entries[1]["risk"] == "execute"


def test_auto模式可以显式设置() -> None:
    """``auto`` 是五个模式之一 —— 不能被当成「恢复默认」。

    全局配置是 bypass 时，用户选「自动（按风险档位）」必须真的拿到 auto。
    """
    from app.agents.permissions import resolve_permission_mode, set_permission_mode

    thread_id = "t-explicit-auto"
    try:
        assert set_permission_mode(thread_id, "auto") == "auto"
        assert resolve_permission_mode(thread_id) == "auto"
        # 恢复默认要走 default
        assert set_permission_mode(thread_id, "default") in (
            "auto",
            "manual",
            "accept_edits",
            "plan",
            "bypass",
        )
    finally:
        from app.agents.permissions import clear_permission_mode

        clear_permission_mode(thread_id)


def test_直接调能力走会话级权限模式() -> None:
    """直接调接口的路径也要认会话的模式 —— 与 agent 路径同一份口径。"""
    from app.agents.permissions import (
        clear_permission_mode,
        resolve_permission_mode,
        set_permission_mode,
    )

    thread_id = "t-invoke-mode"
    try:
        set_permission_mode(thread_id, "manual")
        assert resolve_permission_mode(thread_id) == "manual"
    finally:
        clear_permission_mode(thread_id)


# ---------- 逐条审批 ----------


@pytest.mark.asyncio
async def test_逐条审批可以只批其中一条(app, client, session) -> None:
    """一轮里并行挂起好几个调用时，可以**逐条**批 —— 不必全批或全拒。

    HITL 的决定是**按条**给的：``decisions[i]`` 对应待批清单的第 i 条。
    这里批第一条、拒第二条 —— **只有第一个文件真被删掉**。
    """
    thread_id = "t-partial-approve"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "a.txt").write_text("A", encoding="utf-8")
    (workspace / "b.txt").write_text("B", encoding="utf-8")

    model = ParallelDeleteModel(reply="处理完了")
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "删两个文件", "thread_id": thread_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "pending_approval"
    assert len(data["pending"]) == 2, data["pending"]
    # 清单的顺序要跟决定一一对应
    assert [item["arguments"]["file_path"] for item in data["pending"]] == [
        "a.txt",
        "b.txt",
    ]

    # 第一条批准、第二条拒绝
    approve = await client.post(
        f"{API}/{thread_id}/approve", json={"decisions": ["approve", "reject"]}
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["data"]["status"] == "completed"

    # **只删了被批准的那个** —— 这才是逐条审批的意义
    assert not (workspace / "a.txt").exists(), "被批准的那条应该执行了"
    assert (workspace / "b.txt").exists(), "被拒绝的那条不该执行"


@pytest.mark.asyncio
async def test_逐条审批条数对不上会报错(app, client, session) -> None:
    """决定数与挂起数对不上时要明确报错 —— 静默截断会批错东西。"""
    thread_id = "t-bad-decisions"
    model = ParallelToolModel(reply="跑完了")
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "跑两条命令", "thread_id": thread_id})
    assert resp.status_code == 200, resp.text

    approve = await client.post(
        f"{API}/{thread_id}/approve", json={"decisions": ["approve"]}
    )
    assert approve.status_code >= 400, approve.text


# ---------- 删除会话 ----------


@pytest.mark.asyncio
async def test_删除会话清掉工作区与记忆(app, client, session) -> None:
    """删会话要把痕迹抹干净：工作区目录没了，接口也不再报 404 之外的错。

    checkpoint / 权限文件 / 绑定 / 短期记忆 / 工作区目录 —— 一次清完。
    """
    thread_id = "t-delete-session"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "产物.txt").write_text("东西", encoding="utf-8")

    resp = await client.delete(f"{API}/{thread_id}/session")
    assert resp.status_code == 200, resp.text
    removed = resp.json()["data"]
    assert removed["workspace"] is True, removed
    assert not workspace.exists(), "工作区目录应该被删掉"

    # 再删一次不该炸（目录已经没了）
    again = await client.delete(f"{API}/{thread_id}/session")
    assert again.status_code == 200, again.text
