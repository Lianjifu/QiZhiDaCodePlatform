"""执行适配器：把不同来源的能力统一成「构造工具 + 执行」两件事。

- ``builtin``：平台内置函数（白名单里的工厂），闭包注入执行上下文；
- ``http``：外部 REST 接口；
- ``mcp``：MCP server 上提供的工具。

编排层只看到统一的 ``build_tool`` / ``invoke``，不关心来源。
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.capabilities.context import ExecutionContext
from app.capabilities.entry import SOURCE_BUILTIN, SOURCE_HTTP, SOURCE_MCP, CapabilityEntry
from app.capabilities.result import ErrorCode, ExecutionError, ExecutionResult

_MAX_BODY = 10000


class ExecutionAdapter(ABC):
    """执行适配器基类。"""

    source_type: str = ""

    @abstractmethod
    async def build_tool(self, entry: CapabilityEntry, context: ExecutionContext) -> BaseTool:
        """把能力登记条目变成模型能调用的工具。"""

    @abstractmethod
    async def invoke(
        self,
        entry: CapabilityEntry,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """执行一次能力调用。"""


class BuiltinAdapter(ExecutionAdapter):
    """内置能力：调用白名单里的工厂函数。"""

    source_type = SOURCE_BUILTIN

    async def build_tool(self, entry: CapabilityEntry, context: ExecutionContext) -> BaseTool:
        func = self._resolve(entry, context)
        description = entry.description or (func.__doc__ or "").strip()
        if inspect.iscoroutinefunction(func):
            return StructuredTool.from_function(
                coroutine=func, name=entry.key, description=description
            )
        return StructuredTool.from_function(func=func, name=entry.key, description=description)

    async def invoke(
        self,
        entry: CapabilityEntry,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        started = time.perf_counter()
        try:
            func = self._resolve(entry, context)
            if inspect.iscoroutinefunction(func):
                raw = await func(**arguments)
            else:
                raw = await asyncio.to_thread(func, **arguments)
        except Exception as exc:
            return ExecutionResult.failed(
                ExecutionError.of(ErrorCode.EXECUTION_ERROR, str(exc) or exc.__class__.__name__),
                _elapsed(started),
            )
        return ExecutionResult(ok=True, content=str(raw), duration_ms=_elapsed(started))

    def _resolve(self, entry: CapabilityEntry, context: ExecutionContext):
        source = entry.locator.get("source") or ""
        factory = _import_factory(source)
        return factory(context)


class HttpAdapter(ExecutionAdapter):
    """外部 HTTP 接口。"""

    source_type = SOURCE_HTTP

    async def build_tool(self, entry: CapabilityEntry, context: ExecutionContext) -> BaseTool:
        locator = entry.locator or {}
        url = locator.get("url") or ""
        method = str(locator.get("method") or "GET").upper()
        headers = dict(locator.get("headers") or {})
        body_template = locator.get("body")
        timeout = float(locator.get("timeout") or 30)

        async def call(**kwargs: Any) -> str:
            result = await self._request(url, method, headers, body_template, timeout, kwargs)
            return result.to_text()

        return StructuredTool.from_function(
            func=call,
            name=entry.key,
            description=entry.description,
            args_schema=entry.parameters or None,
        )

    async def invoke(
        self,
        entry: CapabilityEntry,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        started = time.perf_counter()
        locator = entry.locator or {}
        return await self._request(
            locator.get("url") or "",
            str(locator.get("method") or "GET").upper(),
            dict(locator.get("headers") or {}),
            locator.get("body"),
            float(locator.get("timeout") or 30),
            arguments,
            started,
        )

    async def _request(
        self,
        url: str,
        method: str,
        headers: dict[str, Any],
        body_template: Any,
        timeout: float,
        arguments: dict[str, Any],
        started: float | None = None,
    ) -> ExecutionResult:
        import httpx

        started = started or time.perf_counter()
        try:
            final_url = url.format(**arguments) if "{" in url else url
        except (KeyError, IndexError) as exc:
            return ExecutionResult.failed(
                ExecutionError.of(ErrorCode.INVALID_ARGUMENTS, f"URL 模板参数不完整: {exc}"),
                _elapsed(started),
            )

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
            return ExecutionResult.failed(
                ExecutionError.of(ErrorCode.NETWORK_ERROR, f"HTTP 请求超时: {exc}"),
                _elapsed(started),
            )
        except Exception as exc:
            return ExecutionResult.failed(
                ExecutionError.of(ErrorCode.NETWORK_ERROR, f"HTTP 调用失败: {exc}"),
                _elapsed(started),
            )

        text = response.text
        if len(text) > _MAX_BODY:
            text = text[:_MAX_BODY] + f"... (已截断，共 {len(response.text)} 字符)"
        if response.status_code >= 400:
            return ExecutionResult.failed(
                ExecutionError.of(
                    ErrorCode.NETWORK_ERROR,
                    f"HTTP {response.status_code}",
                    status=response.status_code,
                ),
                _elapsed(started),
            )
        return ExecutionResult(
            ok=True,
            content=f"HTTP {response.status_code}\n{text}",
            stdout=text,
            duration_ms=_elapsed(started),
        )


class McpAdapter(ExecutionAdapter):
    """MCP server 上提供的能力。"""

    source_type = SOURCE_MCP

    async def build_tool(self, entry: CapabilityEntry, context: ExecutionContext) -> BaseTool:
        found = await self._find(entry, context)
        if found is None:
            raise ValueError(f"MCP 能力不可用: {entry.key}")
        return StructuredTool(
            name=entry.key,
            description=entry.description or found.description,
            args_schema=found.args_schema,
            func=found.func,
            coroutine=found.coroutine,
        )

    async def invoke(
        self,
        entry: CapabilityEntry,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        from app.capabilities.mcp_client import MCPError

        started = time.perf_counter()
        try:
            found = await self._find(entry, context)
            if found is None:
                return ExecutionResult.failed(
                    ExecutionError.of(
                        ErrorCode.CAPABILITY_NOT_FOUND, f"MCP 能力不可用: {entry.key}"
                    ),
                    _elapsed(started),
                )
            raw = await found.ainvoke(arguments)
        except MCPError as exc:
            return ExecutionResult.failed(
                ExecutionError.of(ErrorCode.NETWORK_ERROR, str(exc)), _elapsed(started)
            )
        except Exception as exc:
            return ExecutionResult.failed(
                ExecutionError.of(ErrorCode.EXECUTION_ERROR, str(exc) or exc.__class__.__name__),
                _elapsed(started),
            )
        content = raw if isinstance(raw, str) else str(raw)
        return ExecutionResult(
            ok=True, content=content, stdout=content, duration_ms=_elapsed(started)
        )

    async def _find(self, entry: CapabilityEntry, context: ExecutionContext):
        from app.capabilities.mcp_client import MCPManager

        locator = entry.locator or {}
        server = context.mcp_servers.get(locator.get("server") or "")
        if server is None:
            return None
        return await MCPManager().find_tool(server, locator.get("tool_name") or "")


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


def _import_factory(source: str):
    """按 "模块:函数" 导入工厂函数。"""
    module_name, _, attr = source.partition(":")
    if not module_name or not attr:
        raise ValueError(f"非法的能力来源: {source}")
    module = importlib.import_module(module_name)
    factory = getattr(module, attr, None)
    if factory is None:
        raise ValueError(f"模块 {module_name} 里没有 {attr}")
    return factory


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


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
