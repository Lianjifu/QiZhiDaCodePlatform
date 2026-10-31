"""把能力声明变成模型能调用的工具。

工具的 coroutine **不直接执行能力**，而是构造意图、走执行层流水线。这样无论
调用从哪条路进来（agent 内部、直接调 API），都会经过同一套校验、策略和留痕。

参数的 tool schema 由注册层声明的 ``input_schema`` 生成（`schema_to_model`），
和平台校验用的是同一份声明 —— 模型看到的约束就是真正生效的约束。
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.adapters import import_factory
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.pipeline import execute
from app.capabilities.intent.intent import CapabilityIntent
from app.capabilities.registration.args_model import schema_to_model
from app.capabilities.registration.spec import KIND_TOOL, SOURCE_MCP, CapabilitySpec

logger = logging.getLogger(__name__)


async def build_tools(
    specs: list[CapabilitySpec],
    context: ExecutionContext,
    allowed_keys: set[str] | None = None,
) -> list[BaseTool]:
    """把清单里的原子能力变成模型可调用的工具。

    单个能力构造失败（配置写错、MCP 连不上）只跳过这一个，不拖垮整个 agent。
    """
    tools: list[BaseTool] = []
    for spec in specs:
        if spec.kind != KIND_TOOL or not spec.enabled:
            continue
        if allowed_keys is not None and spec.key not in allowed_keys:
            continue
        try:
            tool = await _build_tool(spec, context)
        except Exception as exc:
            logger.warning("能力 %s 不可用，已跳过: %s", spec.key, exc)
            continue
        if tool is not None:
            tools.append(tool)
    return tools


async def _build_tool(spec: CapabilitySpec, context: ExecutionContext) -> BaseTool | None:
    if spec.source_type == SOURCE_MCP:
        return await _build_mcp_tool(spec, context)
    return _build_generic_tool(spec, context)


def _build_generic_tool(spec: CapabilitySpec, context: ExecutionContext) -> BaseTool:
    """builtin / http 类型的工具：参数 schema 来自注册层声明。"""
    input_schema = spec.input_schema or _derive_input_schema(spec, context)
    args_model = schema_to_model(input_schema, name=f"{spec.key}_input")

    async def call(**kwargs: Any) -> str:
        intent = CapabilityIntent(
            capability_key=spec.key,
            arguments=dict(kwargs),
            spec=spec,
            thread_id=context.thread_id,
            workspace=context.workspace,
        )
        result = await execute(intent, context)
        return result.to_text()

    return StructuredTool(
        name=spec.key,
        description=spec.description,
        args_schema=args_model,
        coroutine=call,
    )


async def _build_mcp_tool(spec: CapabilitySpec, context: ExecutionContext) -> BaseTool | None:
    """MCP 类型的工具：参数 schema 以 MCP server 侧的声明为准。"""
    from app.capabilities.mcp_client import MCPManager

    locator = spec.locator or {}
    server = context.mcp_servers.get(locator.get("server") or "")
    if server is None:
        raise CapabilityError(
            f"MCP 服务器不可用: {locator.get('server')}", code=ErrorCode.CAPABILITY_NOT_FOUND
        )
    found = await MCPManager().find_tool(server, locator.get("tool_name") or "")
    if found is None:
        raise CapabilityError(f"MCP 能力不可用: {spec.key}", code=ErrorCode.CAPABILITY_NOT_FOUND)

    async def call(**kwargs: Any) -> str:
        intent = CapabilityIntent(
            capability_key=spec.key,
            arguments=dict(kwargs),
            spec=spec,
            thread_id=context.thread_id,
            workspace=context.workspace,
        )
        result = await execute(intent, context)
        return result.to_text()

    return StructuredTool(
        name=spec.key,
        description=spec.description or (found.description or spec.key),
        args_schema=found.args_schema,
        coroutine=call,
    )


__all__ = ["build_tools"]


# ---------- 从实现函数签名推断入参 ----------

_JSON_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def _json_type(annotation: Any) -> str:
    """Python 注解 → JSON Schema 类型。

    内置模块开了 `from __future__ import annotations`，注解拿到的是**字符串**，
    所以按名字映射。
    """
    if isinstance(annotation, str):
        name = annotation.split("[")[0].strip().lower()
    else:
        name = getattr(annotation, "__name__", "").lower()
    return _JSON_TYPES.get(name, "string")


def _derive_input_schema(spec: CapabilitySpec, context: ExecutionContext) -> dict[str, Any]:
    """能力没声明入参时，从实现函数的签名推断一份。

    注册层声明的 ``input_schema`` 是权威的；但用户自建的内置能力常常不填，
    这时让模型看到一张**空的**参数表是很糟的（它不知道该传什么）。
    从函数签名兜一份，比空白强。
    """
    source = spec.locator.get("source") or ""
    if not source:
        return {}
    try:
        func = import_factory(source)(context)
        signature = inspect.signature(func)
    except Exception:
        return {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        properties[name] = {"type": _json_type(parameter.annotation)}
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    if not properties:
        return {}
    return {"type": "object", "properties": properties, "required": required}
