"""上下文模块：决定一次模型调用看到什么。

把多来源的内容（基准提示词、运行环境、策略说明、记忆目录）按配额装成一份
有预算的提示词，并在对话历史过长时压缩。

**它不负责**：召回到什么（记忆模块）、怎么执行（能力层）、对话历史存哪
（checkpointer）。它只管**装配与压缩**。

模块地图：

| 模块 | 管什么 |
| --- | --- |
| `blocks.py` | 上下文块的抽象：标题、内容、优先级、溢出策略 |
| `budget.py` | 预算计算（按模型窗口的比例） |
| `assembler.py` | 装箱：按优先级与配额装块 |
| `compaction.py` | 接管 deepAgents 的摘要中间件 |
| `service.py` | 对外入口：收集各来源的块 → 装配成提示词 |
"""

from app.context.assembler import AssembledContext, assemble
from app.context.blocks import (
    OVERFLOW_COMPACT,
    OVERFLOW_DROP,
    OVERFLOW_TRUNCATE,
    ContextBlock,
)
from app.context.budget import budget_for_window, budget_from_settings, resolve_window
from app.context.compaction import (
    PlatformSummarizationMiddleware,
    build_compaction_middleware,
)
from app.context.service import (
    ContextRequest,
    ContextResult,
    ContextService,
    build_context_service,
)

__all__ = [
    "OVERFLOW_COMPACT",
    "OVERFLOW_DROP",
    "OVERFLOW_TRUNCATE",
    "AssembledContext",
    "ContextBlock",
    "ContextRequest",
    "ContextResult",
    "ContextService",
    "PlatformSummarizationMiddleware",
    "assemble",
    "budget_for_window",
    "budget_from_settings",
    "build_compaction_middleware",
    "build_context_service",
    "resolve_window",
]
