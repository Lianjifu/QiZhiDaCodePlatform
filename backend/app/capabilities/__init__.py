"""能力层：tool / mcp / skill 的统一体系。

三层结构：

- **注册层** ``registration/``：能力的声明、编目、安装 —— 统一能力清单的唯一入口；
- **意图层** ``intent/``：把模型的一次 tool call 变成一条可执行、可审计的意图；
- **执行层** ``execution/``：受控执行、策略判定、结果验证与留痕。

Agent 层不直接碰能力，只通过注册表拿清单（见 ``app.agents.builder``）。

这个包只做最小导出：各层之间有依赖方向（注册层 ← 意图层 ← 执行层），
在包级别把三层都拉进来容易成环。要用哪层就 import 哪层。
"""

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.result import ExecutionResult
from app.capabilities.intent.intent import CapabilityIntent
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registration.registry import CapabilityRegistry
from app.capabilities.registration.spec import CapabilitySpec

__all__ = [
    "CapabilityError",
    "CapabilityIntent",
    "CapabilityMiddleware",
    "CapabilityRegistry",
    "CapabilitySpec",
    "ErrorCode",
    "ExecutionContext",
    "ExecutionResult",
    "assemble_prompt",
]
