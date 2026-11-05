"""审批恢复链路的回归测试。

**这条链路出过一个很具体的故障**：挂起期间用户把权限模式切到「绕过权限」，
图被重建成没有审批闸的样子 —— checkpoint 里那条挂起的中断就"看不见"了，
恢复变成空转（接口返回 200，工具根本没跑）。界面表现是"审批后无法继续"：
工具块一直挂在"未完成"，agent 不再往下走，也没有任何报错。

这里的每条测试都盯住那个故障的一个面。
"""

from __future__ import annotations

import pytest

from app.agents.builder import _interrupt_on
from app.agents.permissions import set_permission_mode
from app.capabilities.registration.policy import (
    PermissionLevel,
    PermissionMode,
    RiskLevel,
    normalize_mode,
)
from app.capabilities.registration.spec import CapabilitySpec
from app.capabilities.registration.tool_policy import (
    approval_candidate_tools,
    tool_needs_approval,
)
from app.core.exceptions import ConflictError


def _spec(key: str, risk: str, permission: str = PermissionLevel.NONE) -> CapabilitySpec:
    return CapabilitySpec(key=key, kind="tool", risk=risk, permission=permission)


@pytest.fixture
def specs() -> list[CapabilitySpec]:
    """一份有代表性的能力清单。"""
    return [
        _spec("run_python", RiskLevel.EXECUTE),
        _spec("memory_write", RiskLevel.WRITE),
        _spec("memory_search", RiskLevel.READ),
        _spec("web_fetch", RiskLevel.NETWORK),
    ]


# ---------- 闸的结构 ----------


def test_闸的结构不随权限模式变(specs):
    """**核心回归**：闸挂在哪些工具上，不能跟着模式变。

    变了的话，挂起期间改模式就会让那条中断"看不见" —— 恢复变成空转。
    """
    shapes = {
        mode: set(_interrupt_on(specs, mode, None))
        for mode in ("auto", "manual", "accept_edits", "plan", "bypass")
    }
    assert len(shapes) == 5
    # 所有模式的闸必须完全一样
    first = shapes["auto"]
    for mode, names in shapes.items():
        assert names == first, f"{mode} 模式的闸与自动模式不一致"


def test_候选集包含所有可能需要审批的工具(specs):
    names = approval_candidate_tools(specs, None)
    # 执行、写入、联网都要人批（手动模式里写入也要）
    assert {"run_python", "memory_write", "web_fetch"} <= names
    # 只读的工具任何模式下都不用问人
    assert "memory_search" not in names


def test_候选集含文件工具的写与删(specs):
    names = approval_candidate_tools(specs, None)
    assert "write_file" in names
    assert "execute" in names
    assert "delete" in names
    # 只读的文件工具不进候选
    assert "read_file" not in names
    assert "grep" not in names


def test_候选集尊重本次允许的能力集(specs):
    names = approval_candidate_tools(specs, {"run_python"})
    assert "run_python" in names
    assert "web_fetch" not in names


# ---------- 运行时判定 ----------


def test_谓词按当前模式判定(specs):
    """自动模式：执行类要人批；绕过权限：一律不问。"""
    gate = _interrupt_on(specs, PermissionMode.AUTO, None)
    run_python = gate["run_python"]["when"]
    assert run_python(None) is True

    gate_bypass = _interrupt_on(specs, PermissionMode.BYPASS, None)
    assert gate_bypass["run_python"]["when"](None) is False


def test_只读工具任何模式都不用问(specs):
    for mode in ("auto", "manual", "accept_edits", "plan", "bypass"):
        gate = _interrupt_on(specs, mode, None)
        # 只读的工具压根不在闸上
        assert "memory_search" not in gate


def test_谓词在恢复时重新读模式(specs, tmp_path, monkeypatch):
    """**核心回归**：挂起期间改了模式，恢复时的判定要跟着变。

    谓词在中断时和恢复时各跑一次 —— 两次之间模式可能变了。
    改成绕过权限，恢复时谓词返回 False，工具直接跑掉（"绕过权限"就是不问人）。
    """
    thread_id = "t-regression"
    monkeypatch.setattr(
        "app.agents.permissions.permission_path",
        lambda _tid: tmp_path / "perm.json",
    )
    set_permission_mode(thread_id, PermissionMode.AUTO)

    gate = _interrupt_on(specs, PermissionMode.AUTO, None, thread_id=thread_id)
    when = gate["run_python"]["when"]
    assert when(None) is True

    # 用户在挂起期间把模式切成了绕过权限
    set_permission_mode(thread_id, PermissionMode.BYPASS)
    assert when(None) is False


def test_判定口径与策略函数一致(specs):
    """闸上的谓词与 ``needs_approval`` 必须同一个口径 —— 两处各写一套，
    批准过了又被拦一次就是这么来的。"""
    declared = {spec.key: (spec.permission, spec.risk) for spec in specs}
    for mode in ("auto", "manual", "accept_edits", "plan", "bypass"):
        gate = _interrupt_on(specs, mode, None)
        for name, config in gate.items():
            assert config["when"](None) == tool_needs_approval(
                name, normalize_mode(mode), declared
            )


def test_手动模式写入也要人批(specs):
    gate = _interrupt_on(specs, PermissionMode.MANUAL, None)
    assert gate["memory_write"]["when"](None) is True


# ---------- 恢复时没有可批的 ----------


@pytest.mark.asyncio
async def test_恢复时没有待批的会明确报错(monkeypatch):
    """**核心回归**：没有可批的就报冲突，别静默空转。

    静默空转的表现是接口返回 200、什么也没发生 —— 界面看着像"点了没反应"。
    """
    from app.agents.runner import AgentRunner

    runner = AgentRunner.__new__(AgentRunner)
    monkeypatch.setattr(
        AgentRunner,
        "_build",
        lambda self, *a, **k: _async_value(_StubBuilt()),
    )
    monkeypatch.setattr(
        AgentRunner,
        "_last_user_message",
        lambda self, *a, **k: _async_value("任务"),
    )
    # ``_build`` 要能 await —— 上面的桩返回的是一个 await 出来的值
    monkeypatch.setattr(
        AgentRunner,
        "_fill_holder",
        lambda self, *a, **k: _async_value(None),
    )
    monkeypatch.setattr(
        AgentRunner,
        "_interrupts_of",
        lambda self, *a, **k: _async_value([]),
    )

    with pytest.raises(ConflictError) as excinfo:
        await runner.resume("t1", tmp_path_workspace(), decision="approve")
    assert "没有待批准的调用" in str(excinfo.value)


class _StubBuilt:
    """够 ``resume`` 用一用的那点东西。"""

    agent = object()
    skills: list = []
    holder = object()
    context = None


async def _async_value(value):
    return value


def tmp_path_workspace():
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp(prefix="qzd-resume-"))
