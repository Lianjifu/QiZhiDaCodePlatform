"""把数据库里的工具定义变成 LangChain 可调用的工具。

三类来源在这里汇合：
- ``builtin``：白名单里的内置函数，用闭包注入运行时上下文（工作区等）；
- ``http``：外部 HTTP 接口，模型给的参数进 query 或 body；
- ``mcp``：某个 MCP server 上的工具，复用 MCP 适配器取回来的实现。

这一层不碰数据库 —— 需要的数据（MCP 服务器配置等）由调用方传进来。
"""

from __future__ import annotations

import importlib
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.models.mcp_server import MCPServer
from app.models.tool import Tool
from app.skills.builtin import get_builtin_by_source
from app.skills.mcp_client import MCPManager

_MAX_HTTP_BODY = 10000


class ToolBuildError(RuntimeError):
    """工具无法被构造出来。"""


def _import_factory(source: str):
    """按 "模块:函数" 导入工厂函数。"""
    module_name, _, attr = source.partition(":")
    if not module_name or not attr:
        raise ToolBuildError(f"非法的工具来源: {source}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ToolBuildError(f"无法导入模块 {module_name}: {exc}") from exc
    factory = getattr(module, attr, None)
    if factory is None:
        raise ToolBuildError(f"模块 {module_name} 里没有 {attr}")
    return factory


class ToolExecutor:
    """按数据库定义构造可执行工具。"""

    def __init__(self, context: dict[str, Any], mcp_manager: MCPManager | None = None) -> None:
        self.context = context
        self.mcp = mcp_manager or MCPManager()

    async def build(self, tool_row: Tool, servers: dict[str, MCPServer] | None = None) -> BaseTool:
        """构造一个工具。`servers` 是 name → MCPServer 的映射，mcp 类型必填。"""
        if tool_row.type == "builtin":
            return self._build_builtin(tool_row)
        if tool_row.type == "http":
            return self._build_http(tool_row)
        if tool_row.type == "mcp":
            return await self._build_mcp(tool_row, servers or {})
        raise ToolBuildError(f"不支持的工具类型: {tool_row.type}")

    async def build_many(
        self, tool_rows: list[Tool], servers: dict[str, MCPServer] | None = None
    ) -> list[BaseTool]:
        """批量构造；单个工具构造失败不拖垮整批，直接跳过并记录。"""
        built: list[BaseTool] = []
        for row in tool_rows:
            try:
                built.append(await self.build(row, servers))
            except Exception:
                # 工具不可用时不能让整个 agent 起不来
                continue
        return built

    # ---------- builtin ----------

    def _build_builtin(self, row: Tool) -> BaseTool:
        source = row.source or ""
        spec = get_builtin_by_source(source)
        if spec is None:
            # 白名单之外一律拒绝：否则等于允许加载任意 Python 代码
            raise ToolBuildError(f"内置工具 {source} 不在白名单内")
        factory = _import_factory(source)
        func = factory(self.context)
        if _is_async(func):
            return StructuredTool.from_function(
                coroutine=func,
                name=row.name,
                description=row.description or spec.description,
            )
        return StructuredTool.from_function(
            func=func,
            name=row.name,
            description=row.description or spec.description,
        )

    # ---------- http ----------

    def _build_http(self, row: Tool) -> BaseTool:
        config = row.config or {}
        url = config.get("url") or ""
        if not url:
            raise ToolBuildError(f"HTTP 工具 {row.name} 缺少 url")
        method = str(config.get("method") or "GET").upper()
        headers = dict(config.get("headers") or {})
        body_template = config.get("body")
        timeout = float(config.get("timeout") or 30)

        def call(**kwargs: Any) -> str:
            import httpx

            try:
                final_url = url.format(**kwargs) if "{" in url else url
            except (KeyError, IndexError) as exc:
                return f"URL 模板参数不完整: {exc}"
            try:
                with httpx.Client(timeout=timeout) as client:
                    if method in ("GET", "HEAD", "DELETE"):
                        response = client.request(
                            method, final_url, headers=headers, params=kwargs or None
                        )
                    else:
                        payload = _render_body(body_template, kwargs)
                        response = client.request(method, final_url, headers=headers, json=payload)
            except Exception as exc:
                return f"HTTP 调用失败: {exc}"
            text = response.text
            if len(text) > _MAX_HTTP_BODY:
                text = text[:_MAX_HTTP_BODY] + f"... (已截断，共 {len(response.text)} 字符)"
            return f"HTTP {response.status_code}\n{text}"

        return StructuredTool.from_function(
            func=call,
            name=row.name,
            description=row.description,
            args_schema=row.parameters or None,
        )

    # ---------- mcp ----------

    async def _build_mcp(self, row: Tool, servers: dict[str, MCPServer]) -> BaseTool:
        server = servers.get(row.source or "")
        if server is None:
            raise ToolBuildError(f"MCP 服务器 {row.source} 不存在或未启用")
        tool_name = (row.config or {}).get("tool_name") or ""
        found = await self.mcp.find_tool(server, tool_name)
        if found is None:
            raise ToolBuildError(f"MCP 服务器 {server.name} 上没有工具 {tool_name}")
        # 复用 MCP 取回来的实现，但用平台登记的名字与描述
        return StructuredTool(
            name=row.name,
            description=row.description or found.description,
            args_schema=found.args_schema,
            func=found.func,
            coroutine=found.coroutine,
        )


def _render_body(body_template: Any, kwargs: dict[str, Any]) -> Any:
    """渲染请求体模板：字符串值里的 {占位符} 用模型给的参数替换。"""
    if body_template is None:
        return kwargs
    if isinstance(body_template, dict):
        rendered = {}
        for key, value in body_template.items():
            if isinstance(value, str):
                try:
                    rendered[key] = value.format(**kwargs)
                except (KeyError, IndexError):
                    rendered[key] = value
            else:
                rendered[key] = value
        return rendered
    return body_template


def _is_async(func: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)
