"""编排层：把用户意图变成一串工具调用意图。

两个挂钩，都挂在 LangChain v1 的 middleware 上：

- **模型调用前**：按用户输入检索出相关技能，把工具收敛到候选集 ——
  工具全量暴露会导致 prompt 爆 + 模型选错；
- **工具调用前**：用能力登记的 schema 校验参数 ——
  校验失败就在进执行层之前拦下来，让模型自己改，而不是等执行层报错再回灌一轮。
"""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage

from app.capabilities.entry import KIND_SKILL, KIND_TOOL, CapabilityEntry
from app.capabilities.result import ErrorCode, ExecutionError

_CJK = re.compile(r"[一-鿿]")
_SPLIT = re.compile(r"[\s,，。；;、:：!！?？()（）\[\]【】/\\]+")


def tokenize(text: str) -> list[str]:
    """粗粒度分词：空白切分 + 中文二元组。

    不追求分词质量，只求"用户说的词"能命中"能力描述里的词"。
    """
    lowered = (text or "").lower()
    tokens = [token for token in _SPLIT.split(lowered) if token]
    extra: list[str] = []
    for token in tokens:
        if len(token) > 1 and _CJK.search(token):
            extra.extend(token[i : i + 2] for i in range(len(token) - 1))
    return tokens + extra


def score_entry(entry: CapabilityEntry, tokens: list[str]) -> float:
    """给一条能力打相关性分。"""
    if not tokens:
        return 0.0
    haystack = " ".join(
        [entry.key, entry.display_name, entry.description, " ".join(entry.tags)]
    ).lower()
    score = 0.0
    for token in tokens:
        if len(token) < 2:
            continue
        if token in haystack:
            score += 1.0
        for tag in entry.tags:
            if token in tag.lower():
                score += 1.5
    return score


def retrieve_skills(
    entries: list[CapabilityEntry],
    query: str,
    top_k: int = 5,
) -> list[CapabilityEntry]:
    """按用户输入检索相关技能。

    命中的技能会被挂载；一个都没命中时返回空列表 —— 让上层决定是"不挂技能"
    还是"退回全量能力"。
    """
    tokens = tokenize(query)
    scored = [
        (score_entry(entry, tokens), entry)
        for entry in entries
        if entry.kind == KIND_SKILL and entry.enabled
    ]
    scored = [(score, entry) for score, entry in scored if score > 0]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _score, entry in scored[:top_k]]


def validate_arguments(entry: CapabilityEntry, arguments: dict[str, Any]) -> ExecutionError | None:
    """按登记的 schema 校验参数。返回 None 表示通过。"""
    schema = entry.parameters or {}
    if not schema:
        return None
    arguments = arguments or {}

    required = schema.get("required") or []
    missing = [
        name
        for name in required
        if name not in arguments or arguments[name] is None or arguments[name] == ""
    ]
    if missing:
        return ExecutionError.of(
            ErrorCode.INVALID_ARGUMENTS,
            f"调用 {entry.key} 缺少必填参数：{', '.join(missing)}",
            missing=missing,
        )

    properties = schema.get("properties") or {}
    for name, value in arguments.items():
        spec = properties.get(name)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if not _type_matches(expected, value):
            return ExecutionError.of(
                ErrorCode.INVALID_ARGUMENTS,
                f"调用 {entry.key} 的参数 {name} 类型不对，期望 {expected}",
                argument=name,
                expected=expected,
            )
    return None


def _type_matches(expected: str | None, value: Any) -> bool:
    if not expected or value is None:
        return True
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


class CapabilityMiddleware(AgentMiddleware):
    """编排层的两个挂钩。

    - ``awrap_model_call``：把暴露给模型的工具收敛到候选集；
    - ``awrap_tool_call``：执行前校验参数，失败就以可重试的错误回灌给模型。
    """

    def __init__(
        self,
        entries: list[CapabilityEntry],
        allowed_tool_keys: set[str] | None = None,
    ) -> None:
        self.entries = entries
        self.allowed_tool_keys = allowed_tool_keys

    async def awrap_model_call(self, request: ModelRequest, handler):
        if self.allowed_tool_keys is None:
            return await handler(request)
        kept = [
            tool for tool in request.tools if getattr(tool, "name", None) in self.allowed_tool_keys
        ]
        if not kept:
            # 一个都没匹配上就别收窄，保留原样，避免把 agent 关死
            return await handler(request)
        return await handler(request.override(tools=kept))

    async def awrap_tool_call(self, request, handler):
        name = (request.tool_call or {}).get("name")
        entry = self._entry_for(name)
        if entry is not None:
            error = validate_arguments(entry, (request.tool_call or {}).get("args") or {})
            if error is not None:
                return ToolMessage(
                    content=f"[{error.code}] {error.message}（可以修正参数后重试）",
                    tool_call_id=(request.tool_call or {}).get("id", ""),
                    status="error",
                )
        return await handler(request)

    def _entry_for(self, name: str | None) -> CapabilityEntry | None:
        if not name:
            return None
        for entry in self.entries:
            if entry.kind == KIND_TOOL and entry.key == name:
                return entry
        return None


def collect_tool_keys(
    skills: list[CapabilityEntry],
    entries: list[CapabilityEntry],
    include_builtin: bool = True,
) -> set[str]:
    """技能 → 它们承载的原子能力 key。

    ``include_builtin`` 为真时，出厂内置能力（读文件、写文件、执行代码这类
    工作区基本操作）始终保留 —— 否则命中一个技能反而把 agent 的基本能力削掉了。
    """
    by_key = {entry.key: entry for entry in entries if entry.kind == KIND_TOOL}
    keys: set[str] = set()
    for skill in skills:
        for member in skill.members:
            if member in by_key:
                keys.add(member)
    if include_builtin:
        for entry in entries:
            if entry.kind == KIND_TOOL and entry.origin == "builtin" and entry.enabled:
                keys.add(entry.key)
    return keys


def select_skills(
    entries: list[CapabilityEntry],
    query: str,
    skill_ids: list[int] | None = None,
    top_k: int = 5,
) -> list[CapabilityEntry]:
    """决定本次会话挂载哪些技能。

    显式指定（skill_ids）优先；没指定就按用户输入检索。
    """
    if skill_ids:
        wanted = set(skill_ids)
        return [
            entry
            for entry in entries
            if entry.kind == KIND_SKILL and entry.enabled and entry.db_id in wanted
        ]
    return retrieve_skills(entries, query, top_k=top_k)
