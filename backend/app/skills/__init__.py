"""技能能力层：把数据库里的技能 / 工具 / MCP 定义变成 agent 能用的东西。

职责边界：
- `app/skills/` 只管"怎么执行"（构造工具、连 MCP、拼提示词）；
- `app/services/` 管"数据从哪来"（CRUD、关联维护、启停校验）；
- `app/api/v1/` 只做参数转换。
"""

from app.skills.builtin import BUILTIN_TOOLS, list_builtin_tools
from app.skills.context import build_runtime_context
from app.skills.executor import ToolBuildError, ToolExecutor
from app.skills.mcp_client import MCPError, MCPManager, build_connection
from app.skills.prompt import build_skill_prompt

__all__ = [
    "BUILTIN_TOOLS",
    "list_builtin_tools",
    "build_runtime_context",
    "ToolExecutor",
    "ToolBuildError",
    "MCPManager",
    "MCPError",
    "build_connection",
    "build_skill_prompt",
]
