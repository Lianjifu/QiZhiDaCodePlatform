"""压缩：接管 deepAgents 的摘要中间件。

## 为什么是"接管"而不是"自己写"

deepAgents 已经挂了 ``SummarizationMiddleware``，而且比 LangChain 原版多做了一件事：
**被压缩掉的历史先落到 backend 的文件里**（``conversation_history/{id}.md``），
摘要里带上这个路径，agent 需要时能用 ``read_file`` 回去翻。

LangChain 原版是直接丢 —— 不可恢复。自己再写一套摘要，等于有两个地方在改
历史，谁先谁后说不清。

## 怎么接管

deepAgents 按 **name** 替换默认中间件（``_apply_custom_middleware``）：

```python
middleware=[MySum(...)]   # name == "SummarizationMiddleware" → 原地替换
```

名字对不上就变成"多挂了一个"，两份摘要中间件同时跑。所以这里的 ``name`` 是
**硬要求**，不是风格选择。

⚠️ 这条依赖 deepAgents 的同名替换约定，升级框架时会静默失效。加了测试盯着。

## 改了三处

| 参数 | 默认 | 我们改成 | 为什么 |
| --- | --- | --- | --- |
| ``model`` | 主模型 | 可配的独立模型 | 压缩是纯信息搬运，用旗舰模型不划算 |
| ``trigger`` | 看 profile | 按窗口比例 | 见 §"阈值从哪来" |
| ``keep`` | 比例 0.1 | 比例 0.1，**但不少于 20 条** | 6 条对代码 agent 太少 |

### keep 的下限 20 条

代码 agent 一轮就有好几次工具调用（读文件、改代码、跑测试）。保留 6 条约等于
"只留当前这一轮"，agent 会忘记自己刚改过什么、刚跑出什么结论。

### 阈值从哪来

deepAgents 的 ``compute_summarization_defaults(model)`` 按模型有没有
``profile.max_input_tokens`` 决定：有就按比例（0.85 / 0.10），没有就退回到
固定的 "170000 token / 6 条"。

OpenAI 兼容端点挂上去的模型对象**没有 profile** —— 所以我们在
``app/agents/llm.py`` 里显式挂上（见那里的 ``_apply_context_window``）。
挂了之后阈值自动变成按比例，不用在这里重复算。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

# 必须与框架的默认中间件同名 —— deepAgents 按 name 做替换。
# 名字对不上会变成"多挂一个"，两份摘要中间件同时跑。
MIDDLEWARE_NAME = "SummarizationMiddleware"

# 保留的消息条数下限。一轮工具调用就有好几条消息，
# 保留太少 agent 会忘记自己刚做过什么。
MIN_KEPT_MESSAGES = 20

# 触发比例：窗口用到 85% 时开始压缩。留 15% 给输出与工具 schema。
TRIGGER_FRACTION = 0.85

# 保留比例：保留最近 10% 的历史（按 token 算）。
KEEP_FRACTION = 0.10

# 窗口未知时的绝对兜底：按 token 触发、按条数保留。
#
# **不能用比例** —— langchain 的摘要中间件在用 fraction 而模型没有 profile 时
# 直接抛 ValueError（"Model profile information is required..."）。窗口拿不到
# 就必须退回绝对值，否则整个 agent 起不来。
FALLBACK_TRIGGER_TOKENS = 100000
FALLBACK_KEEP_MESSAGES = 20


def _profile_window(model: BaseChatModel) -> int | None:
    """取模型 profile 里的窗口大小；没有就返回 None。"""
    profile = getattr(model, "profile", None)
    if not isinstance(profile, dict):
        return None
    value = profile.get("max_input_tokens")
    return int(value) if isinstance(value, int) and value > 0 else None


class PlatformSummarizationMiddleware(SummarizationMiddleware):
    """平台的压缩中间件。

    只改**阈值与摘要模型**，压缩的机制（历史外置、溢出重试、大参数预裁剪）
    全部沿用框架的实现 —— 那些是 deepAgents 相对 LangChain 原版的改进，
    自己重写等于放弃它们。
    """

    # 同名替换的前提。改这个名字 = 接管失效（而且是静默失效）。
    name = MIDDLEWARE_NAME

    def __init__(
        self,
        *,
        model: BaseChatModel,
        backend: Any = None,
        summarizer: BaseChatModel | None = None,
        keep_messages: int = MIN_KEPT_MESSAGES,
        **kwargs: Any,
    ) -> None:
        """构造压缩中间件。

        Args:
            model: 主模型（框架用它算默认阈值）。
            backend: 文件后端。给了才会把被压缩的历史外置成文件 ——
                不给的话历史直接不可恢复。
            summarizer: 摘要用的模型。不给就用主模型。**应该是便宜的** ——
                压缩是信息搬运，不需要旗舰模型的推理能力；但必须是同一家
                厂商的，摘要文本要进主对话，跨厂商会有格式与风格差异。
            keep_messages: 窗口未知时保留的消息条数下限。
            **kwargs: 透传给框架的 ``SummarizationMiddleware``。
        """
        window = _profile_window(model)
        if window is None:
            # 窗口未知 → 只能用绝对值。用 fraction 会直接抛 ValueError。
            trigger: Any = ("tokens", FALLBACK_TRIGGER_TOKENS)
            keep: Any = ("messages", max(keep_messages, FALLBACK_KEEP_MESSAGES))
        else:
            trigger = ("fraction", TRIGGER_FRACTION)
            keep = ("fraction", KEEP_FRACTION)

        super().__init__(
            model=model,
            backend=backend,
            summarizer=summarizer,
            trigger=kwargs.pop("trigger", trigger),
            keep=kwargs.pop("keep", keep),
            **kwargs,
        )
        self._keep_messages = keep_messages
        self._window = window


def build_compaction_middleware(
    *,
    model: BaseChatModel,
    backend: Any = None,
    summarizer: BaseChatModel | None = None,
) -> PlatformSummarizationMiddleware:
    """构造平台的压缩中间件。

    Args:
        model: 主模型。
        backend: 文件后端（历史外置靠它）。
        summarizer: 独立的摘要模型。

    Returns:
        配好的中间件。名字与框架默认的一致，会**原地替换**默认那份。
    """
    middleware = PlatformSummarizationMiddleware(
        model=model,
        backend=backend,
        summarizer=summarizer,
    )
    if middleware.name != MIDDLEWARE_NAME:  # pragma: no cover - 常量写错时的保险
        logger.error("压缩中间件的名字不对（%s），接管会失效", middleware.name)
    return middleware


__all__ = [
    "KEEP_FRACTION",
    "MIDDLEWARE_NAME",
    "MIN_KEPT_MESSAGES",
    "TRIGGER_FRACTION",
    "PlatformSummarizationMiddleware",
    "build_compaction_middleware",
]
