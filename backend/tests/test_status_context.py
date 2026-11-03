"""上下文占用的估算（状态面板上那个百分比）。

两条容易搞错的规则，单独钉住：

1. **新会话什么都��占** —— 系统提示词和工具定义是每次请求都带上的，可请求
   还没发出去，占用就该是 0；
2. **压缩缓冲不算"已用"** —— 它是预留出来放摘要的那段，一直空着。
"""

from __future__ import annotations

from app.agents.status import build_context_usage

LIMIT = 128_000


def _usage(**overrides):
    kwargs = {
        "limit": LIMIT,
        "messages_text": "",
        "system_prompt": "",
        "tools_text": "",
        "mcp_text": "",
        "skills_text": "",
        "memory_text": "",
        "buffer_tokens": 0,
        "started": True,
    }
    kwargs.update(overrides)
    return build_context_usage(**kwargs)


def test_新会话什么都不占():
    usage = _usage(
        started=False,
        system_prompt="系统提示词" * 100,
        tools_text="工具定义" * 100,
        buffer_tokens=19_200,
    )
    assert usage.used == 0
    assert usage.percent == 0
    assert usage.reserved == 0
    assert usage.free == LIMIT
    assert usage.parts == []


def test_压缩缓冲不计入已用():
    usage = _usage(messages_text="你好" * 100, buffer_tokens=19_200)
    assert usage.reserved == 19_200
    # 已用只包含真的占掉的部分
    assert usage.used == sum(part.tokens for part in usage.parts)
    assert usage.used < 19_200
    # 剩余 = 窗口 − 已用 − 预留
    assert usage.free == LIMIT - usage.used - 19_200


def test_各块都算进去():
    usage = _usage(
        messages_text="a" * 400,
        system_prompt="b" * 400,
        tools_text="c" * 400,
        mcp_text="d" * 400,
        skills_text="e" * 400,
        memory_text="f" * 400,
    )
    keys = {part.key for part in usage.parts}
    assert keys == {"messages", "system_prompt", "tools", "mcp_tools", "skills", "memory"}
    # 每块 100 字符 ≈ 25 token
    assert all(part.tokens == 100 for part in usage.parts)


def test_百分比对得上():
    usage = _usage(messages_text="a" * 4000, buffer_tokens=0)
    assert usage.used == 1000
    assert usage.percent == round(1000 / LIMIT * 100, 1)
    assert usage.free == LIMIT - 1000
