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
    """执行适配器基类。

    适配器只做一件事：**把这一次调用跑完**。超时、重试、幂等、留痕都在流水线里，
    适配器不操心 —— 它抛错，流水线决定怎么分层、要不要重试。
    """

    source_type: str = ""

    @abstractmethod
    async def invoke(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """执行一次能力调用。

        Args:
            spec: 能力声明。
            arguments: 调用参数。
            context: 执行上下文。

        Returns:
            统一格式的执行结果。

        Raises:
            CapabilityError: 执行失败（错误码决定这一类失败怎么处理）。
        """


class BuiltinAdapter(ExecutionAdapter):
    """内置能力：调用白名单里的工厂函数。"""

    source_type = SOURCE_BUILTIN

    async def invoke(
        self,
        spec: CapabilitySpec,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """构造并调用内置能力。

        同步实现丢到线程池里跑 —— 文件读写这类操作是阻塞的，直接在事件循环里
        调用会把整个服务卡住。
        """
        started = time.perf_counter()
        func = import_factory(spec.locator.get("source") or "")
        try:
            raw = func(context)
            if _is_async(raw):
                data = await raw(**arguments)
            else:
                data = await _to_thread(raw, **arguments)
        except CapabilityError:
            # 能力自己抛的错已经带好错误码了，原样往上抛
            raise
        except Exception as exc:
            # 其它异常统一包成 EXECUTION_ERROR：模型不需要知道 Python 的内部细节
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
        """调用外部 HTTP 接口。

        GET / HEAD / DELETE 的参数进 query string，其余方法的参数进 JSON body
        —— 跟大多数 REST 接口的约定一致。

        Raises:
            CapabilityError: URL 模板参数不完整（``INVALID_ARGUMENTS``）、
                超时（``TIMEOUT``）、网络失败（``NETWORK_ERROR``）、
                HTTP 4xx/5xx（``HTTP_ERROR``；5xx 可重试，4xx 重试没用）。
        """
        started = time.perf_counter()
        locator = spec.locator or {}
        method = str(locator.get("method") or "GET").upper()
        url = locator.get("url") or ""
        headers = dict(locator.get("headers") or {})
        body_template = locator.get("body")
        timeout = float(locator.get("timeout") or spec.timeout_s or 30)

        import httpx

        try:
            # URL 里的 {占位符} 用模型给的参数替换
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
            # 5xx 是服务端的毛病，重试可能有用；4xx 是请求本身的问题，重试没用
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
        """调用 MCP server 上的工具。

        Raises:
            CapabilityError: server 不可用或工具找不到（``CAPABILITY_NOT_FOUND``）、
                连接失败（``NETWORK_ERROR``）、调用出错（``EXECUTION_ERROR``）。
        """
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


# 三种执行方式各一个适配器实例（适配器本身无状态，可以共用）
_ADAPTERS: dict[str, ExecutionAdapter] = {
    SOURCE_BUILTIN: BuiltinAdapter(),
    SOURCE_HTTP: HttpAdapter(),
    SOURCE_MCP: McpAdapter(),
}


def get_adapter(source_type: str) -> ExecutionAdapter:
    """按执行方式取适配器。

    Raises:
        ValueError: 执行方式不认识 —— 说明声明里写了不支持的 ``source_type``。
    """
    adapter = _ADAPTERS.get(source_type)
    if adapter is None:
        raise ValueError(f"不支持的执行方式: {source_type}")
    return adapter


# ---------- 工具函数 ----------


def import_factory(source: str):
    """按 "模块:函数" 导入工厂函数。

    Raises:
        ValueError: 格式不对，或模块里没有这个属性。

    注意：这个函数**只做导入**，不做白名单校验 —— 白名单在注册层
    （``registration/builtin``）里，schema 校验层会挡住越界的 source。
    """
    module_name, _, attr = source.partition(":")
    if not module_name or not attr:
        raise ValueError(f"非法的能力来源: {source}")
    module = importlib.import_module(module_name)
    factory = getattr(module, attr, None)
    if factory is None:
        raise ValueError(f"模块 {module_name} 里没有 {attr}")
    return factory


def _is_async(func: Any) -> bool:
    """是不是 async 函数。"""
    import inspect

    return inspect.iscoroutinefunction(func)


async def _to_thread(func, **kwargs):
    """同步实现放到线程池里跑，别占着事件循环。"""
    import asyncio

    return await asyncio.to_thread(func, **kwargs)


def _to_result(data: Any, elapsed: int) -> ExecutionResult:
    """把能力的返回值整理成统一结果。

    约定：能力返回 dict，其中 ``content`` 是给模型看的文本，其余是结构化数据。
    返回的不是 dict 时（老能力），把值本身当文本。
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
    """渲染请求体模板：字符串值里的 {占位符} 用模型给的参数替换。

    替换失败（占位符对不上）就**原样保留**那个字符串 —— 总比整次请求失败好，
    服务端大概率会给出更具体的报错。
    """
    if body_template is None:
        # 没写 body 模板 → 整个参数当 body
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
