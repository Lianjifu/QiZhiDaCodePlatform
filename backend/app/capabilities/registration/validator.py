"""按声明的 JSON Schema 校验参数。

注册层声明的 ``input_schema`` 是唯一事实来源：

- **意图层**用它做前置快速校验 —— 不通过就给模型一句"缺少必填参数 path，可以
  修正后重试"，省一轮往返；
- **执行层**用它做权威校验 —— 不通过就拒绝执行。

两处共用本模块，**不写第二套实现**（早先那份手写的 `_type_matches` 只查必填和
基础类型，``enum`` / ``minimum`` / ``pattern`` / ``additionalProperties`` 全不管）。
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

_MAX_MESSAGES = 5


class SchemaValidationError(ValueError):
    """参数不符合声明的 schema。"""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("；".join(messages))


def normalize_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """统一"多余参数"的处理口径。

    JSON Schema 里 ``additionalProperties`` 缺省是**允许**多余的，但我们的声明
    语义是"这份 schema 就是全部入参" —— 声明了字段就默认拒绝多余的。

    不统一的话会出现很难查的错：校验放行了，能力实现却收到没见过的关键字参数
    直接 TypeError。两层（意图层快速校验、执行层权威校验、给模型看的参数模型）
    都用这个函数，规则只有一份。
    """
    if not schema:
        return schema
    if not schema.get("properties"):
        return schema
    if schema.get("additionalProperties") is True:
        return schema
    return {**schema, "additionalProperties": False}


def validate_arguments(
    schema: dict[str, Any] | None,
    arguments: dict[str, Any] | None,
) -> None:
    """按 JSON Schema 校验参数。

    不符合则抛 `SchemaValidationError`；schema 为空表示这个能力没有约束，直接放过。
    """
    schema = normalize_schema(schema)
    if not schema:
        return
    arguments = arguments or {}
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(arguments),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if not errors:
        return

    messages: list[str] = []
    for error in errors[:_MAX_MESSAGES]:
        location = ".".join(str(part) for part in error.absolute_path) or "(根)"
        messages.append(f"{location}: {error.message}")
    if len(errors) > _MAX_MESSAGES:
        messages.append(f"（另有 {len(errors) - _MAX_MESSAGES} 处错误）")
    raise SchemaValidationError(messages)


def validate_output(
    schema: dict[str, Any] | None,
    data: dict[str, Any] | None,
) -> None:
    """按 JSON Schema 校验执行结果。schema 为空表示这个能力没有输出契约。

    这里**不**限制多余字段：结果里常带些簿记字段（HTTP 状态、退出码之类），
    契约只该管"声明过的字段对不对"。
    """
    if not schema:
        return
    validator = Draft202012Validator({**schema, "additionalProperties": True})
    errors = list(validator.iter_errors(data or {}))
    if not errors:
        return
    messages = [
        f"{'.'.join(str(p) for p in e.absolute_path) or '(根)'}: {e.message}" for e in errors
    ]
    raise SchemaValidationError(messages)


__all__ = [
    "SchemaValidationError",
    "normalize_schema",
    "validate_arguments",
    "validate_output",
]
