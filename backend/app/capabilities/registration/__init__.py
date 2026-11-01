"""注册层：能力的声明、编目、安装。

产出**统一能力清单** —— 数据库是唯一事实来源，代码里的出厂源只负责首次安装。

这一层做三件事：

1. **声明**（``spec.py`` / ``policy.py``）：一个能力在平台里的契约与策略；
2. **编目**（``registry.py``）：把出厂源、数据库里的 tool / mcp_server / skill
   聚合成一份清单；
3. **校验**（``validator.py``）：按声明的 JSON Schema 卡住不合规的参数。

编排层和前端都只从这份清单取能力，不再各自去拼三张表。
"""

from app.capabilities.registration.policy import (
    Annotations,
    PermissionLevel,
    RetryPolicy,
    RiskLevel,
    max_risk,
    risk_rank,
)
from app.capabilities.registration.registry import CapabilityRegistry
from app.capabilities.registration.spec import (
    KIND_MCP,
    KIND_SKILL,
    KIND_TOOL,
    ORIGIN_BUILTIN,
    ORIGIN_USER,
    SOURCE_BUILTIN,
    SOURCE_HTTP,
    SOURCE_MCP,
    CapabilitySpec,
)
from app.capabilities.registration.validator import (
    SchemaValidationError,
    validate_arguments,
    validate_output,
)

__all__ = [
    "KIND_MCP",
    "KIND_SKILL",
    "KIND_TOOL",
    "ORIGIN_BUILTIN",
    "ORIGIN_USER",
    "SOURCE_BUILTIN",
    "SOURCE_HTTP",
    "SOURCE_MCP",
    "Annotations",
    "CapabilityRegistry",
    "CapabilitySpec",
    "PermissionLevel",
    "RetryPolicy",
    "RiskLevel",
    "SchemaValidationError",
    "max_risk",
    "risk_rank",
    "validate_arguments",
    "validate_output",
]
