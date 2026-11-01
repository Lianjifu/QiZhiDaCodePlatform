"""agent 调用能力的补强：审批结果回流、执行摘要、审批提示、能力集收敛。"""

from __future__ import annotations

from pathlib import Path
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

    用 ``write_file`` 而不是 ``execute``：写文件在本地跑，测试不依赖 docker；
    strict 模式下写类操作要人批。
    """
    monkeypatch.setattr(settings, "capability_trust_mode", "strict")

    thread_id = "t-approve"
    model = RecordingModel(
        tool_name="write_file",
        tool_args={"file_path": "out.txt", "content": "写点东西"},
        reply="写完了",
    )
    _override(app, model, session)

    resp = await client.post(API + "/chat", json={"message": "写个文件", "thread_id": thread_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # strict 模式下写类操作要人批 → 这一轮挂起了
    assert data["status"] == "pending_approval"
    assert data["pending"], "挂起时应该给出待审批条目"
    assert data["pending"][0]["tool"] == "write_file"

    # 人工批准 → 恢复图
    approve = await client.post(f"{API}/{thread_id}/approve")
    assert approve.status_code == 200, approve.text
    approved = approve.json()["data"]
    assert approved["status"] == "completed"
    assert "写完了" in approved["content"]
    # 工具真的执行了：文件落盘
    assert approved["tool_calls"], "恢复后应该有工具调用"
    assert approved["tool_calls"][0]["name"] == "write_file"
    workspace = Path(settings.workspace_root).resolve() / thread_id
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "写点东西"


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
    workspace = Path(settings.workspace_root).resolve() / thread_id
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
        ("none", "read", "balanced", "approved"),
        ("none", "execute", "balanced", "approved"),
        ("confirm", "read", "balanced", "approved"),
        ("confirm", "write", "balanced", "approved"),
        ("confirm", "execute", "balanced", "pending"),
        ("confirm", "network", "balanced", "pending"),
        ("confirm", "execute", "strict", "pending"),
        ("confirm", "execute", "permissive", "approved"),
        ("admin", "read", "permissive", "pending"),
        ("admin", "read", "strict", "pending"),
    ],
)
def test_approval_decision(permission: str, risk: str, mode: str, expected: str) -> None:
    assert approval_decision(permission, risk, mode) == expected


def test_policy_note_lists_capabilities_needing_approval() -> None:
    """模型要提前知道哪些能力会要人批 —— 不知道就会反复触发审批、反复中断。"""
    specs = [
        _spec("read_file", permission="none", risk="read", origin="builtin"),
        _spec("execute", permission="confirm", risk="execute", origin="builtin"),
    ]
    middleware = CapabilityMiddleware(specs, trust_mode="balanced")
    note = middleware._policy_note()
    assert "execute" in note
    assert "read_file" not in note
    assert "人工批准" in note


def test_policy_note_respects_trust_mode() -> None:
    """信任模式决定哪些工具要人批 —— 提示词里的口径必须和实际拦截的一致。"""
    # 文件工具的审批按映射表 + 信任模式算：permissive 只拦破坏性，strict 全拦。
    permissive = CapabilityMiddleware([], trust_mode="permissive")._policy_note()
    assert "execute" not in permissive
    strict = CapabilityMiddleware([], trust_mode="strict")._policy_note()
    assert "execute" in strict
    assert "write_file" in strict


def test_policy_note_counts_exposed_capabilities() -> None:
    """工具数量 = 我们自己的能力 + deepAgents 的文件工具 —— 提示词里两个数字要一致。"""
    specs = [
        _spec("web_fetch", origin="user"),
        _spec("web_search", origin="user"),
    ]
    note = CapabilityMiddleware(specs, trust_mode="balanced")._policy_note()
    assert f"本次可用工具 {len(FS_TOOL_NAMES) + 2} 个" in note
