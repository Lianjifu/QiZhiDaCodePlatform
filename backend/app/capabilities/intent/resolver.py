"""意图层的解析：模型的一次 tool call → 一条 `CapabilityIntent`。

顺带做**前置快速校验**：参数不符合声明就当场拦下，给模型一句"缺少必填参数
path，可以修正后重试"，省一轮往返。执行层还会再校验一次（权威校验），
两处共用同一份 schema 和同一个校验器。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.intent.intent import CapabilityIntent, compute_idempotency_key
from app.capabilities.registration.spec import KIND_TOOL, CapabilitySpec
from app.capabilities.registration.validator import (
    SchemaValidationError,
    validate_arguments,
)


def find_spec(specs: list[CapabilitySpec], key: str) -> CapabilitySpec | None:
    """按 key 找一条可执行的声明。

    只认 ``kind == tool`` 的成员 —— mcp 是通道、skill 是组合，都不能被
    模型当工具直接调。
    """
    for spec in specs:
        if spec.kind == KIND_TOOL and spec.key == key:
            return spec
    return None


def clean_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """去掉值为 None 的参数。

    模型可能显式传 ``null``，pydantic 也会把没填的可选参数补成 ``None``。
    两种都表示"没给"，应该让能力实现用自己的默认值，而不是把 None 传进去。
    """
    return {key: value for key, value in (arguments or {}).items() if value is not None}


def resolve(
    tool_call: dict[str, Any],
    specs: list[CapabilitySpec],
    *,
    thread_id: str = "",
    workspace: Path | None = None,
    trust_mode: str = "balanced",
    allowed_keys: set[str] | None = None,
    validate: bool = True,
) -> CapabilityIntent:
    """把一次 tool call 翻译成意图。

    检查顺序是有意的，从便宜到贵：先看能力存不存在 → 停用没有 → 在不在允许集里
    → 最后才做 schema 校验。前三步都是内存里的集合查询，几乎不花钱；
    schema 校验要跑一遍 JSON Schema 校验器。

    Args:
        tool_call: 模型产出的 ``{"name": ..., "args": {...}}``。
        specs: 统一能力清单。
        thread_id: 会话 ID（进幂等键）。
        workspace: 本次执行的工作区。
        trust_mode: 会话信任模式，生成意图时快照下来。
        allowed_keys: 本次允许的能力集；超出范围的调用在这里就被挡住。
        validate: 是否做前置参数校验。

    Returns:
        绑定了声明与策略快照的 ``CapabilityIntent``。

    Raises:
        CapabilityError: 能力不存在（``CAPABILITY_NOT_FOUND``）、已停用或
            不在允许集内（``PERMISSION_DENIED``）、参数不符合声明
            （``SCHEMA_INVALID``，可重试 —— 模型改对参数就能成）。
    """
    name = str(tool_call.get("name") or "")
    spec = find_spec(specs, name)
    if spec is None:
        raise CapabilityError(f"能力 {name} 不存在", code=ErrorCode.CAPABILITY_NOT_FOUND)
    if not spec.enabled:
        raise CapabilityError(f"能力 {name} 已停用", code=ErrorCode.PERMISSION_DENIED)
    if allowed_keys is not None and name not in allowed_keys:
        # 不在本次允许集内 = 这次会话根本没给模型这个工具
        raise CapabilityError(
            f"能力 {name} 不在本次允许的能力集内",
            code=ErrorCode.PERMISSION_DENIED,
        )

    arguments = clean_arguments(tool_call.get("args"))
    if validate:
        try:
            validate_arguments(spec.input_schema, arguments)
        except SchemaValidationError as exc:
            # 标成可重试：这类失败模型改对参数就能成，值得再试一次
            raise CapabilityError(
                f"调用 {name} 的参数不合法：{exc}",
                code=ErrorCode.SCHEMA_INVALID,
                retryable=True,
            ) from exc

    return CapabilityIntent(
        capability_key=name,
        arguments=arguments,
        spec=spec,
        thread_id=thread_id,
        workspace=workspace,
        trust_mode=trust_mode,
        idempotency_key=compute_idempotency_key(spec, arguments, thread_id),
        allowed_keys=set(allowed_keys) if allowed_keys is not None else None,
    )


__all__ = ["clean_arguments", "find_spec", "resolve"]
