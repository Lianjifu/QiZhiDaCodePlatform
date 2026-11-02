"""会话状态：环境、上下文占用、产出、改动量、接进来的东西。

前端那个状态面板（点侧栏底部那根条弹出来的）就靠这个。

## 上下文占用是**估算**

没有精确的 token 数 —— 不同厂商的分词器不一样，我们也不该为了显示一个百分比
去装一个分词器。折算口径跟上下文模块一致：**4 个字符约 1 个 token**
（``app/context/budget.py`` 里的 ``chars_per_token``）。

**这是估算，不是账单。** 面板上要写清楚，别让人拿它当精确值看。

## 各块怎么来的

| 块 | 来源 |
| --- | --- |
| 对话消息 | checkpoint 里的消息正文 |
| 系统提示词 | 上下文模块装配出来的正文（基础提示 + 策略说明 + 上传件清单） |
| 工具定义 | 能力清单里挂着的工具的 schema |
| MCP 工具 | MCP 服务器提供的工具的 schema |
| 技能 | 技能正文（``SKILL.md``） |
| 记忆文件 | 工作区里的 ``AGENTS.md`` |

剩余空间 = 窗口 − 已用。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas.agent import (
    ChangesOut,
    ConnectorOut,
    ContextPartOut,
    ContextUsageOut,
    EnvironmentOut,
    OutputFileOut,
    RunningTaskOut,
    SessionStatusOut,
)

logger = logging.getLogger(__name__)

# 一个 token 折算多少字符 —— 跟上下文模块同一个口径
CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str | None) -> int:
    """把字符数折算成 token 数（估算）。"""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN)


def _percent(value: int, limit: int) -> float:
    if limit <= 0:
        return 0.0
    return round(value / limit * 100, 1)


def _tool_schema_size(tools: list[Any]) -> int:
    """一批工具的 schema 有多大（字符）。

    只量"模型看到的那部分"：名字、描述、参数 schema。工具的实现代码不算 ——
    那不占上下文。
    """
    total = 0
    for tool in tools:
        parts = [str(getattr(tool, "name", "") or "")]
        description = getattr(tool, "description", "") or ""
        parts.append(str(description))
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is not None:
            try:
                parts.append(json.dumps(args_schema, ensure_ascii=False, default=str))
            except (TypeError, ValueError):  # pragma: no cover - schema 不是 JSON 就退化成描述
                parts.append(str(args_schema))
        total += len("\n".join(parts))
    return total


def build_context_usage(
    *,
    limit: int,
    messages_text: str,
    system_prompt: str,
    tools_text: str,
    mcp_text: str,
    skills_text: str,
    memory_text: str,
    mcp_items: list[str] | None = None,
    memory_items: list[str] | None = None,
    buffer_tokens: int = 0,
) -> ContextUsageOut:
    """把各部分的体量拼成上下文占用。

    Args:
        limit: 上下文窗口（token）。
        messages_text: 对话消息正文。
        system_prompt: 系统提示词正文。
        tools_text: 工具定义。
        mcp_text: MCP 工具定义。
        skills_text: 技能正文。
        memory_text: 记忆文件正文。
        mcp_items: 每个 MCP 工具的名字（面板上可展开）。
        memory_items: 每个记忆文件的名字（面板上可展开）。
        buffer_tokens: 压缩缓冲（窗口用到这个比例就触发压缩，那段空间一直空着）。

    Returns:
        上下文占用（含明细与剩余空间）。
    """
    raw = [
        ("messages", "对话消息", estimate_tokens(messages_text), []),
        ("system_prompt", "系统提示词", estimate_tokens(system_prompt), []),
        ("tools", "工具定义", estimate_tokens(tools_text), []),
        ("mcp_tools", "MCP 工具", estimate_tokens(mcp_text), list(mcp_items or [])),
        ("skills", "技能", estimate_tokens(skills_text), []),
        ("memory", "记忆文件", estimate_tokens(memory_text), list(memory_items or [])),
        # 压缩缓冲：窗口用到 85% 就触发压缩 —— 那 15% 得一直空着
        ("autocompact", "压缩缓冲", buffer_tokens, []),
    ]
    used = sum(item[2] for item in raw)
    parts = [
        ContextPartOut(
            key=key,
            label=label,
            tokens=tokens,
            percent=_percent(tokens, limit),
            items=items,
        )
        for key, label, tokens, items in raw
        if tokens > 0
    ]
    return ContextUsageOut(
        limit=limit,
        used=used,
        percent=_percent(used, limit),
        free=max(limit - used, 0),
        parts=parts,
    )


def collect_outputs(workspace: Path, *, limit: int = 50) -> list[OutputFileOut]:
    """工作区里的产出与上传件。

    按修改时间倒序 —— 最近动过的排前面，用户最关心的是刚生成的那个。
    """
    from app.files.service import FileService

    try:
        entries = FileService(workspace).list_files(include_uploads=True)
    except Exception as exc:  # pragma: no cover - 读工作区失败不该让面板整个挂掉
        logger.warning("读工作区文件失败: %s", exc)
        return []
    entries.sort(key=lambda item: item.modified_at, reverse=True)
    return [
        OutputFileOut(
            path=entry.path,
            size=entry.size,
            is_dir=entry.is_dir,
            uploaded=entry.uploaded,
        )
        for entry in entries[:limit]
    ]


def sum_changes(records: list[Any]) -> ChangesOut:
    """把一批调用记录里的改动量加起来。

    改动量是治理中间件执行前后比对文件算出来的，挂在调用记录的 ``result.diff``
    里 —— 这里只负责汇总。
    """
    added = 0
    removed = 0
    for record in records:
        result = getattr(record, "result", None)
        if not isinstance(result, dict):
            continue
        diff = result.get("diff")
        if not isinstance(diff, dict):
            continue
        value = diff.get("added")
        if isinstance(value, int):
            added += value
        value = diff.get("removed")
        if isinstance(value, int):
            removed += value
    return ChangesOut(added=added, removed=removed)


def build_environment(
    *,
    thread_id: str,
    workspace: Path,
    model: str,
    provider: str,
    trust_mode: str,
) -> EnvironmentOut:
    """这次会话跑在什么环境里。"""
    return EnvironmentOut(
        thread_id=thread_id,
        workspace=str(workspace),
        model=model,
        provider=provider,
        trust_mode=trust_mode,
    )


def build_connectors(
    *,
    providers: list[Any],
    mcp_servers: list[Any],
) -> list[ConnectorOut]:
    """接进来的东西：模型供应商 + MCP 服务器。"""
    connectors: list[ConnectorOut] = []
    for provider in providers:
        connectors.append(
            ConnectorOut(
                name=str(getattr(provider, "name", "") or ""),
                kind="provider",
                status="ok" if getattr(provider, "is_active", False) else "disabled",
                detail=str(getattr(provider, "default_model", "") or ""),
            )
        )
    for server in mcp_servers:
        connectors.append(
            ConnectorOut(
                name=str(getattr(server, "name", "") or ""),
                kind="mcp",
                status="ok" if getattr(server, "enabled", False) else "disabled",
                detail=str(getattr(server, "url", "") or ""),
            )
        )
    return connectors


def build_status(
    *,
    thread_id: str,
    environment: EnvironmentOut,
    context: ContextUsageOut,
    outputs: list[OutputFileOut],
    changes: ChangesOut,
    connectors: list[ConnectorOut],
    tasks: list[RunningTaskOut] | None = None,
    finished_tasks: int = 0,
) -> SessionStatusOut:
    """把各部分拼成会话状态。"""
    return SessionStatusOut(
        thread_id=thread_id,
        environment=environment,
        context=context,
        outputs=outputs,
        changes=changes,
        connectors=connectors,
        tasks=list(tasks or []),
        finished_tasks=finished_tasks,
        updated_at=datetime.now(UTC).isoformat(),
    )


__all__ = [
    "CHARS_PER_TOKEN",
    "build_connectors",
    "build_context_usage",
    "build_environment",
    "build_status",
    "collect_outputs",
    "estimate_tokens",
    "sum_changes",
]
