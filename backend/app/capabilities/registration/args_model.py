"""把声明的 JSON Schema 转成给模型看的参数模型。

执行层的工具函数是 ``async def call(**kwargs)``，从函数签名推不出参数 schema
（只会得到一个空的 object）。所以要用注册层声明的 ``input_schema`` 显式构造
pydantic 模型，LangChain 再由它生成 tool schema。

这样模型看到的参数说明、约束（枚举、范围、长度）与平台校验用的是**同一份声明**，
不会出现"提示词里说只能填 1-20，校验却不管"的情况。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

_TYPE_MAP: dict[str, Any] = {
    # JSON Schema 的基本类型 → Python 类型
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list[Any],
    "object": dict[str, Any],
}


def _python_type(spec: dict[str, Any]) -> Any:
    """JSON Schema 的类型 → Python 类型。

    优先看 ``enum``：有枚举值就用 ``Literal``，这样 pydantic 和 LangChain
    都能直接把可选值告诉模型 —— 比"类型是 string，但合法值有哪些"更管用。

    类型是列表（联合类型）时，取第一个非 ``null`` 的那个：声明里的
    ``["string", "null"]`` 在这里就是"一个可选的字符串"。
    """
    if "enum" in spec and isinstance(spec["enum"], list) and spec["enum"]:
        values = tuple(spec["enum"])
        return Literal[values]  # type: ignore[valid-type]
    declared = spec.get("type")
    if isinstance(declared, list):
        declared = next((item for item in declared if item != "null"), None)
    # 认不出的类型一律退到 Any：宁可少约束，也不要把模型挡在门外
    return _TYPE_MAP.get(str(declared), Any)


def _constraints(spec: dict[str, Any]) -> dict[str, Any]:
    """把 JSON Schema 的约束翻译成 pydantic Field 的参数。

    只翻译 pydantic 原生支持的那几项（范围、长度、正则）。翻译不了的
    （比如跨字段的依赖）就丢掉 —— 权威校验仍然在执行层按原 schema 做，
    这里只是给模型看的说明。
    """
    kwargs: dict[str, Any] = {}
    if spec.get("description"):
        # description 是给模型看的说明，原样带过去
        kwargs["description"] = spec["description"]
    if isinstance(spec.get("minimum"), (int, float)):
        kwargs["ge"] = spec["minimum"]
    if isinstance(spec.get("maximum"), (int, float)):
        kwargs["le"] = spec["maximum"]
    if isinstance(spec.get("minLength"), int):
        kwargs["min_length"] = spec["minLength"]
    if isinstance(spec.get("maxLength"), int):
        kwargs["max_length"] = spec["maxLength"]
    if isinstance(spec.get("pattern"), str):
        kwargs["pattern"] = spec["pattern"]
    return kwargs


def schema_to_model(
    schema: dict[str, Any] | None, name: str = "CapabilityInput"
) -> type[BaseModel]:
    """按 JSON Schema 构造 pydantic 模型。

    schema 为空时返回一个允许任意字段的模型；声明了 ``additionalProperties: false``
    的 schema 会拒绝多余字段 —— 否则模型多传一个参数，能力实现就会收到没见过的
    关键字参数直接 TypeError。

    Args:
        schema: 声明的 ``input_schema``，可以为空。
        name: 模型名（LangChain 生成 tool schema 时会带上）。

    Returns:
        构造好的 pydantic 模型类。

    注意：pydantic 的 ``extra`` 配置只能在建模型时通过 ``__config__`` 传 ——
    事后改 ``model_config`` 字典对已经建好的模型不生效。
    """
    if not schema:
        # 没有声明 → 不给模型任何约束；给一张空参数表是很糟的，它不知道该传什么
        return create_model(name, __config__=ConfigDict(extra="allow"))

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    # 与 validator.normalize_schema 同一套口径：声明了字段就拒绝多余参数
    allow_extra = schema.get("additionalProperties") is True

    fields: dict[str, Any] = {}
    for key, spec in properties.items():
        if not isinstance(spec, dict):
            spec = {}
        python_type = _python_type(spec)
        constraints = _constraints(spec)
        annotation = Annotated[python_type, Field(**constraints)] if constraints else python_type
        if key in required:
            # 必填：不给默认值，模型必须传
            fields[key] = (annotation, ...)
        else:
            # 选填：类型放宽成"可空"，默认值 None
            fields[key] = (annotation | None if python_type is not Any else Any, None)

    # extra 必须通过 __config__ 传：事后改 model_config 字典对已建好的模型不生效
    config = ConfigDict(extra="allow" if allow_extra else "forbid")
    return create_model(name, __config__=config, **fields)


__all__ = ["schema_to_model"]
