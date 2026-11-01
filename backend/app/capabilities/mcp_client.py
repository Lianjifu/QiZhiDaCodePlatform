"""MCP 连接管理。

把数据库里的 MCP 服务器配置翻译成 `langchain-mcp-adapters` 的连接参数，
并负责把 server 上的能力取回来。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from app.models.mcp_server import MCPServer


class MCPError(RuntimeError):
    """MCP 连接或调用失败。"""


def build_connection(server: MCPServer) -> dict[str, Any]:
    """按 transport 生成 MCP 连接配置。

    Args:
        server: 数据库里的 MCP 服务器记录。

    Returns:
        ``MultiServerMCPClient`` 认识的连接参数字典。

    Raises:
        MCPError: 缺 command / url，或 transport 不支持。

    缺参数就明确报错 —— 连接参数不全时静默默认值，会连到一个错的地址上，
    排查起来比直接报错难得多。
    """
    transport = (server.transport or "stdio").lower()
    if transport == "stdio":
        if not server.command:
            raise MCPError(f"MCP 服务器 {server.name} 缺少 command")
        return {
            "transport": "stdio",
            "command": server.command,
            "args": list(server.args or []),
            "env": dict(server.env) if server.env else None,
        }
    if transport in ("sse", "streamable_http"):
        if not server.url:
            raise MCPError(f"MCP 服务器 {server.name} 缺少 url")
        return {
            "transport": transport,
            "url": server.url,
            "headers": dict(server.headers) if server.headers else None,
            "timeout": server.timeout,
        }
    raise MCPError(f"不支持的 MCP transport: {server.transport}")


class MCPManager:
    """MCP 客户端封装。

    `MultiServerMCPClient` 构造时不建立连接，真正建立会话发生在
    `get_tools()` / 调用工具时，所以这里每次都新建 client，避免长期持有
    一个连不上的 server 把整个 agent 拖死。
    """

    async def list_tools(self, server: MCPServer) -> list[BaseTool]:
        """列出某个 MCP server 上的全部能力。

        Raises:
            MCPError: 连接失败（统一包成 MCPError，别把底层异常泄给上层）。
        """
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({server.name: build_connection(server)})
        try:
            return await client.get_tools(server_name=server.name)
        except Exception as exc:
            raise MCPError(f"连接 MCP 服务器 {server.name} 失败: {exc}") from exc

    async def find_tool(self, server: MCPServer, tool_name: str) -> BaseTool | None:
        """按名字找出 server 上的某个能力；找不到返回 ``None``。"""
        if not tool_name:
            return None
        for tool in await self.list_tools(server):
            if tool.name == tool_name:
                return tool
        return None
