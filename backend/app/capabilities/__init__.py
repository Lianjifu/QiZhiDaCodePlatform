"""能力层：tool / mcp / skill 的统一体系。

三层结构：
- **注册层**（本目录）：能力的声明、编目、安装 —— 统一能力清单的唯一入口；
- **编排层**（``orchestration.py``）：检索技能、收敛工具、校验参数；
- **执行层**（``adapter.py`` / ``result.py``）：受控执行与结构化结果。

Agent 层不直接碰能力，只通过注册表拿清单（见 ``app.agents.builder``）。
"""

from app.capabilities.adapter import ExecutionAdapter, get_adapter
from app.capabilities.builtin import list_builtin_specs
from app.capabilities.context import ExecutionContext
from app.capabilities.entry import CapabilityEntry
from app.capabilities.orchestration import (
    CapabilityMiddleware,
    collect_tool_keys,
    retrieve_skills,
    validate_arguments,
)
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registry import CapabilityRegistry
from app.capabilities.result import ErrorCode, ExecutionError, ExecutionResult

__all__ = [
    "CapabilityEntry",
    "CapabilityMiddleware",
    "CapabilityRegistry",
    "ErrorCode",
    "ExecutionAdapter",
    "ExecutionContext",
    "ExecutionError",
    "ExecutionResult",
    "assemble_prompt",
    "collect_tool_keys",
    "get_adapter",
    "list_builtin_specs",
    "retrieve_skills",
    "validate_arguments",
]
