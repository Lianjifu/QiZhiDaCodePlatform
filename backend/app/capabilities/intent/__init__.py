"""意图层：把模型的一次 tool call 变成一条可执行、可审计、可拦截的意图。

**模型提议，意图层收敛。** 模型决定调哪个能力，意图层负责把它翻译成
`CapabilityIntent` —— 绑定声明、解析参数、附上策略上下文。
"""

from app.capabilities.intent.intent import CapabilityIntent, compute_idempotency_key
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.intent.planner import (
    collect_tool_keys,
    retrieve_skills,
    score_entry,
    select_skills,
    tokenize,
)
from app.capabilities.intent.resolver import find_spec, resolve

__all__ = [
    "CapabilityIntent",
    "CapabilityMiddleware",
    "collect_tool_keys",
    "compute_idempotency_key",
    "find_spec",
    "resolve",
    "retrieve_skills",
    "score_entry",
    "select_skills",
    "tokenize",
]
