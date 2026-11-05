"""反思的回归测试。

盯住的是**三条会悄悄塌掉的判断**：

1. **什么算"失败"** —— 记录的状态只是其中一层；退出码非零也是
   （代码 agent 最常见的失败形态，而它在记录里是 ``succeeded``）；
2. **窗口** —— 反思是"最近这一段"，不是"本会话全部历史"；
3. **它真的到了模型眼前** —— 占位符换成了内容，不是留了一行标记。

每条测试都对着一个**具体的失效方式**写的，不是"应该这样"。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.dynamic_context import (
    PLACEHOLDER_REFLECTION,
    PromptHolder,
)
from app.agents.reflection import (
    MAX_INJECTED,
    SCAN_WINDOW,
    FailureNote,
    build_reflection_block,
    is_failure,
    recent_failures,
    render_failures,
)
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.builtin.invocation import (
    make_invocation_recent_failures,
)
from app.context.service import (
    KEY_REFLECTION,
    PRIORITY_REFLECTION,
    ContextRequest,
    build_context_service,
)
from app.models.invocation import (
    STATUS_BLOCKED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    Invocation,
)


def _record(
    *,
    status: str = STATUS_SUCCEEDED,
    key: str = "run_python",
    thread_id: str = "t1",
    error_message: str | None = None,
    error_code: str | None = None,
    result: dict[str, Any] | None = None,
) -> Invocation:
    """造一条调用记录。"""
    return Invocation(
        thread_id=thread_id,
        capability_key=key,
        arguments={},
        payload={},
        status=status,
        risk="read",
        permission="none",
        decision_reason="",
        stages=[],
        result=result or {},
        error_code=error_code,
        error_message=error_message,
    )


# ---------------------------------------------------------------- 什么算失败


def test_状态是失败就算失败():
    """状态这一层：失败 / 被阻塞 / 被拒绝 / 审批过期。"""
    for status in (STATUS_FAILED, STATUS_BLOCKED, STATUS_DENIED, STATUS_EXPIRED):
        assert is_failure(_record(status=status)) is True


def test_退出码非零也算失败():
    """**代码 agent 最常见的失败形态。**

    ``run_python`` 的职责是"跑一段代码并把退出码与输出带回来" —— 代码抛
    异常时这个职责履行了，所以记录是 ``succeeded``。

    但从 agent 的角度那次尝试就是失败了：它想算的东西没算出来。
    只看状态的话，反思块在最需要它的场景里永远是空的。
    """
    record = _record(status=STATUS_SUCCEEDED, result={"exit_code": 1})
    assert is_failure(record) is True


def test_退出码为零不算失败():
    record = _record(status=STATUS_SUCCEEDED, result={"exit_code": 0})
    assert is_failure(record) is False


def test_普通成功不算失败():
    assert is_failure(_record(status=STATUS_SUCCEEDED)) is False


def test_退出码不是整数就不算数():
    """结果里塞了个字符串当退出码 —— 别把它当失败。"""
    record = _record(status=STATUS_SUCCEEDED, result={"exit_code": "1"})
    assert is_failure(record) is False


def test_错误消息从结果正文里取():
    """退出码非零但没有 error_message 时 —— 从正文里找原因。

    ``run_python`` 的正文是"退出码: 1\\nTraceback...\\nValueError: ..."，
    最后一行的原因才是有用的。
    """
    from app.agents.reflection import _failure_message

    record = _record(
        status=STATUS_SUCCEEDED,
        result={
            "exit_code": 1,
            "content": "退出码: 1\nTraceback (most recent call last):\nValueError: 坏了",
        },
    )
    assert "ValueError: 坏了" in _failure_message(record)


def test_状态标记行不算错误原因():
    """``退出码: 1`` 与 ``[Command failed with exit code 1]`` 是结论，不是原因。"""
    from app.agents.reflection import _failure_message

    record = _record(
        status=STATUS_SUCCEEDED,
        result={
            "exit_code": 1,
            "content": "输出: something broke\n[Command failed with exit code 1]",
        },
    )
    message = _failure_message(record)
    assert "something broke" in message
    assert "[Command" not in message


# ---------------------------------------------------------------- 窗口与查询


async def test_只报本会话的失败(session):
    """别人的会话失败跟本会话无关 —— 混进来只会误导。"""
    session.add(_record(status=STATUS_FAILED, thread_id="别人的"))
    session.add(_record(status=STATUS_FAILED, thread_id="我的"))
    await session.commit()

    notes = await recent_failures(session, "我的")
    assert len(notes) == 1


async def test_窗口把陈旧的失败挤出去(session):
    """反思是"最近这一段"，不是"本会话全部历史"。

    后面又跑了几十次调用，早先那次失败就不该再被翻出来 ——
    否则模型会一直盯着一个早就解决掉的问题。
    """
    session.add(_record(status=STATUS_FAILED))
    for _ in range(SCAN_WINDOW + 5):
        session.add(_record(status=STATUS_SUCCEEDED))
    await session.commit()

    notes = await recent_failures(session, "t1")
    assert notes == [], "窗口之外的失败不该被报出来"


async def test_失败按新的在前(session):
    session.add(_record(status=STATUS_FAILED, key="第一个"))
    session.add(_record(status=STATUS_FAILED, key="第二个"))
    await session.commit()

    notes = await recent_failures(session, "t1")
    assert [note.capability_key for note in notes] == ["第二个", "第一个"]


async def test_最多报几条(session):
    for i in range(MAX_INJECTED + 5):
        session.add(_record(status=STATUS_FAILED, key=f"k{i}"))
    await session.commit()

    notes = await recent_failures(session, "t1")
    assert len(notes) == MAX_INJECTED


async def test_退出码非零的调用也会被报出来(session):
    """这是最要紧的一条 —— 记录是 succeeded，但那次尝试确实没成。"""
    session.add(
        _record(
            status=STATUS_SUCCEEDED,
            result={"exit_code": 1, "content": "退出码: 1\nValueError: 坏了"},
        )
    )
    await session.commit()

    notes = await recent_failures(session, "t1")
    assert len(notes) == 1
    assert notes[0].capability_key == "run_python"
    assert notes[0].exit_code == 1
    assert "ValueError: 坏了" in notes[0].error_message


async def test_没有会话就返回空(session):
    """反思是加分项 —— 缺了不该报错。"""
    assert await recent_failures(None, "t1") == []
    assert await recent_failures(session, "") == []


# ---------------------------------------------------------------- 渲染


def test_空列表不渲染():
    """不注入的空块只会白占位置。"""
    assert render_failures([]) == ""


def test_渲染出来带能力名与原因():
    notes = [
        FailureNote(
            capability_key="run_python",
            status=STATUS_FAILED,
            error_code="EXECUTION_ERROR",
            error_message="沙箱起不来",
        )
    ]
    text = render_failures(notes)
    assert "run_python" in text
    assert "沙箱起不来" in text
    # 也要告诉模型能拿这些做什么
    assert "invocation_recent_failures" in text


async def test_反思块查不到时不报错(session):
    """查表失败不该让整轮对话挂掉。"""
    assert await build_reflection_block(session, "没有这个会话") == ""


# ---------------------------------------------------------------- 能力


async def test_能力能查到失败(session, tmp_path):
    session.add(_record(status=STATUS_FAILED, error_message="炸了"))
    await session.commit()

    context = ExecutionContext.build(tmp_path)
    context.session = session
    context.thread_id = "t1"

    tool = make_invocation_recent_failures(context)
    result = await tool()
    assert "炸了" in result["content"]
    assert result["items"][0]["capability_key"] == "run_python"


async def test_能力没有失败时说清楚(session, tmp_path):
    context = ExecutionContext.build(tmp_path)
    context.session = session
    context.thread_id = "t1"

    tool = make_invocation_recent_failures(context)
    result = await tool()
    assert result["items"] == []
    assert "没有失败" in result["content"]


async def test_能力缺会话上下文时报错(tmp_path):
    """没有 thread_id 时，所有直连调用会共用一个空桶 —— 那不是"会话的失败"。"""
    from app.capabilities.errors import CapabilityError

    context = ExecutionContext.build(tmp_path)
    tool = make_invocation_recent_failures(context)
    with pytest.raises(CapabilityError):
        await tool()


async def test_能力的limit有上界(session, tmp_path):
    """limit 越界要明确拒绝，不是静默截断。"""
    from app.capabilities.errors import CapabilityError

    context = ExecutionContext.build(tmp_path)
    context.session = session
    context.thread_id = "t1"

    tool = make_invocation_recent_failures(context)
    with pytest.raises(CapabilityError):
        await tool(limit=999)


# ---------------------------------------------------------------- 进了上下文


def test_反思块进了上下文():
    """优先级比记忆目录高 —— 记忆是背景资料，反思是"你刚摔过"。"""
    service = build_context_service()
    result = service.assemble(
        ContextRequest(
            base_prompt="基准",
            reflection_note="<recent_failures>x</recent_failures>",
        )
    )
    keys = [block.key for block in result.blocks]
    assert KEY_REFLECTION in keys
    assert PRIORITY_REFLECTION < 40


async def test_占位符被换成了内容():
    """图是跨轮复用的 —— 内容每轮换，位置不能变。"""
    from langchain_core.messages import SystemMessage

    from app.agents.dynamic_context import _replace_placeholders

    holder = PromptHolder()
    holder.update(reflection="<recent_failures>跑挂了</recent_failures>")

    message = SystemMessage(content=f"前面\n{PLACEHOLDER_REFLECTION}\n后面")
    replaced = _replace_placeholders(message, holder)
    assert replaced is not None
    assert "跑挂了" in replaced.text
    assert PLACEHOLDER_REFLECTION not in replaced.text


async def test_反思为空时不留空标记():
    """没有失败时不该在提示词里留一段空标记。"""
    from langchain_core.messages import SystemMessage

    from app.agents.dynamic_context import _replace_placeholders

    holder = PromptHolder()
    holder.update(reflection="")

    message = SystemMessage(content=f"前面\n{PLACEHOLDER_REFLECTION}\n后面")
    replaced = _replace_placeholders(message, holder)
    assert replaced is not None
    assert PLACEHOLDER_REFLECTION not in replaced.text
