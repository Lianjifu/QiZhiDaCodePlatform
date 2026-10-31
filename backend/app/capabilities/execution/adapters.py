"""执行适配器：把不同来源的能力统一成「执行」一件事。

- ``builtin``：平台内置函数（白名单里的工厂）；
- ``http``：外部 REST 接口；
- ``mcp``：MCP server 上提供的工具。

执行层只看到统一的 ``invoke``，不关心来源。**超时、重试、幂等、留痕都在流水线
里做**，适配器只负责"把这一次调用跑完"。
"""

from __future__ import annotations

import importlib
import time
from abc import ABC, abstractmethod
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.result import ExecutionResult
from app.capabilities.registration.spec import (
    SOURCE_BUILTIN,
    SOURCE_HTTP,
    SOURCE_MCP,
    CapabilitySpec,
)


class ExecutionAdapter(ABC):
    """执行适配器基类。"""

    source_type: str = ""

    @abstractmethod
    async def invoke(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """执行一次能力调用。失败抛 `CapabilityError`。"""


class BuiltinAdapter(ExecutionAdapter):
    """内置能力：调用白名单里的工厂函数。"""

    source_type = SOURCE_BUILTIN

    async def invoke(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        started = time.perf_counter()
        func = import_factory(spec.locator.get("source") or "")
        try:
            raw = func(context)
            if _is_async(raw):
                data = await raw(**arguments)
            else:
                data = await _to_thread(raw, **arguments)
        except CapabilityError:
            raise
        except Exception as exc:
            raise CapabilityError(
                str(exc) or exc.__class__.__name__, code=ErrorCode.EXECUTION_ERROR
            ) from exc
        elapsed = int((time.perf_counter() - started) * 1000)
        return _to_result(data, elapsed)


class HttpAdapter(ExecutionAdapter):
    """外部 HTTP 接口。"""

    source_type = SOURCE_HTTP

    async def invoke(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        started = time.perf_counter()
        locator = spec.locator or {}
        method = str(locator.get("method") or "GET").upper()
        url = locator.get("url") or ""
        headers = dict(locator.get("headers") or {})
        body_template = locator.get("body")
        timeout = float(locator.get("timeout") or spec.timeout_s or 30)

        import httpx

        try:
            final_url = url.format(**arguments) if "{" in url else url
        except (KeyError, IndexError) as exc:
            raise CapabilityError(
                f"URL 模板参数不完整: {exc}", code=ErrorCode.INVALID_ARGUMENTS
            ) from exc

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method in ("GET", "HEAD", "DELETE"):
                    response = await client.request(
                        method, final_url, headers=headers, params=arguments or None
                    )
                else:
                    response = await client.request(
                        method,
                        final_url,
                        headers=headers,
                        json=_render_body(body_template, arguments),
                    )
        except httpx.TimeoutException as exc:
            raise CapabilityError(
                f"HTTP 请求超时: {exc}", code=ErrorCode.TIMEOUT, retryable=True
            ) from exc
        except Exception as exc:
            raise CapabilityError(
                f"HTTP 调用失败: {exc}", code=ErrorCode.NETWORK_ERROR, retryable=True
            ) from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        text = response.text
        if response.status_code >= 400:
            raise CapabilityError(
                f"HTTP {response.status_code}",
                code=ErrorCode.HTTP_ERROR,
                retryable=response.status_code >= 500,
                status=response.status_code,
            )
        return _to_result(
            {
                "content": f"HTTP {response.status_code}\n{text}",
                "status": response.status_code,
                "body": text,
            },
            elapsed,
        )


class McpAdapter(ExecutionAdapter):
    """MCP server 上提供的能力。"""

    source_type = SOURCE_MCP

    async def invoke(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        from app.capabilities.mcp_client import MCPError, MCPManager

        started = time.perf_counter()
        locator = spec.locator or {}
        server = context.mcp_servers.get(locator.get("server") or "")
        if server is None:
            raise CapabilityError(
                f"MCP 能力不可用: {spec.key}", code=ErrorCode.CAPABILITY_NOT_FOUND
            )
        try:
            found = await MCPManager().find_tool(server, locator.get("tool_name") or "")
        except MCPError as exc:
            raise CapabilityError(str(exc), code=ErrorCode.NETWORK_ERROR, retryable=True) from exc
        if found is None:
            raise CapabilityError(
                f"MCP 能力不可用: {spec.key}", code=ErrorCode.CAPABILITY_NOT_FOUND
            )
        try:
            raw = await found.ainvoke(arguments)
        except Exception as exc:
            raise CapabilityError(
                str(exc) or exc.__class__.__name__, code=ErrorCode.EXECUTION_ERROR
            ) from exc
        elapsed = int((time.perf_counter() - started) * 1000)
        content = raw if isinstance(raw, str) else str(raw)
        return _to_result({"content": content}, elapsed)


_ADAPTERS: dict[str, ExecutionAdapter] = {
    SOURCE_BUILTIN: BuiltinAdapter(),
    SOURCE_HTTP: HttpAdapter(),
    SOURCE_MCP: McpAdapter(),
}


def get_adapter(source_type: str) -> ExecutionAdapter:
    """按执行方式取适配器。"""
    adapter = _ADAPTERS.get(source_type)
    if adapter is None:
        raise ValueError(f"不支持的执行方式: {source_type}")
    return adapter


# ---------- 工具函数 ----------


def import_factory(source: str):
    """按 "模块:函数" 导入工厂函数。"""
    module_name, _, attr = source.partition(":")
    if not module_name or not attr:
        raise ValueError(f"非法的能力来源: {source}")
    module = importlib.import_module(module_name)
    factory = getattr(module, attr, None)
    if factory is None:
        raise ValueError(f"模块 {module_name} 里没有 {attr}")
    return factory


def _is_async(func: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)


async def _to_thread(func, **kwargs):
    """同步实现放到线程池里跑，别占着事件循环。"""
    import asyncio

    return await asyncio.to_thread(func, **kwargs)


def _to_result(data: Any, elapsed: int) -> ExecutionResult:
    """把能力的返回值整理成统一结果。

    约定：能力返回 dict，其中 ``content`` 是给模型看的文本，其余是结构化数据。
    """
    if isinstance(data, dict):
        payload = dict(data)
        content = str(payload.get("content") or "")
        return ExecutionResult(
            ok=True,
            content=content,
            data=payload,
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
            exit_code=payload.get("exit_code"),
            duration_ms=elapsed,
        )
    text = "" if data is None else str(data)
    return ExecutionResult(ok=True, content=text, data={"content": text}, duration_ms=elapsed)


def _render_body(body_template: Any, arguments: dict[str, Any]) -> Any:
    """渲染请求体模板：字符串值里的 {占位符} 用模型给的参数替换。"""
    if body_template is None:
        return arguments
    if isinstance(body_template, dict):
        rendered = {}
        for key, value in body_template.items():
            if isinstance(value, str):
                try:
                    rendered[key] = value.format(**arguments)
                except (KeyError, IndexError):
                    rendered[key] = value
            else:
                rendered[key] = value
        return rendered
    return body_template


__all__ = [
    "BuiltinAdapter",
    "ExecutionAdapter",
    "HttpAdapter",
    "McpAdapter",
    "get_adapter",
    "import_factory",
]
