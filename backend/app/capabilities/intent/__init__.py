"""意图层：把模型的一次 tool call 变成一条可执行、可审计、可拦截的意图。

**模型提议，意图层收敛。** 模型决定调哪个能力，意图层负责把它翻译成
`CapabilityIntent` —— 绑定声明、附上策略上下文。

这一层**不产生任何副作用**：它只翻译和校验，真正的执行在执行层。

治理（工具收敛、参数级风险挂起、留痕）落在中间件上，见
``app.capabilities.intent.middleware``。
"""

from app.capabilities.intent.intent import CapabilityIntent, compute_idempotency_key
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.intent.resolver import find_spec, resolve

__all__ = [
    "CapabilityIntent",
    "CapabilityMiddleware",
    "compute_idempotency_key",
    "find_spec",
    "resolve",
]
