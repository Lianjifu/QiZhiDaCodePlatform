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

### 两个阈值，不是一个

框架只有**一个**阈值，可它要同时跟两种度量比：

- ``token_counter(messages)`` —— **只算对话**；
- ``usage_metadata["total_tokens"]`` —— **整个请求**（含系统提示词与工具
  schema），厂商报回来的真实大小。

拿同一个阈值去比两者，要么提前压缩（把系统提示词的额度也算进对话，
上下文白白少一半），要么放任请求超窗被厂商拒。

所以这里拆成两个：

- 对话超过**对话的份额**（窗口 − 系统提示词那份，见 ``budget.py``）就压缩；
- 整个请求超过**请求上限**（0.85 窗口）也压缩。

**系统提示词占的额度要从压缩阈值里扣掉** —— 两份预算互不知情是最容易
踩的坑：一次请求 1.35 倍窗口，厂商直接拒。
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.summarization import (
    SummarizationMiddleware as DeepAgentsSummarizationMiddleware,
)
from langchain_core.language_models import BaseChatModel

from app.context.budget import (
    TOTAL_REQUEST_FRACTION,
    conversation_budget_ratio,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

# 必须与框架的默认中间件同名 —— deepAgents 按 name 做替换。
# 名字对不上会变成"多挂一个"，两份摘要中间件同时跑。
MIDDLEWARE_NAME = "SummarizationMiddleware"

# 保留的消息条数下限。一轮工具调用就有好几条消息，
# 保留太少 agent 会忘记自己刚做过什么。
MIN_KEPT_MESSAGES = 20

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


class PlatformSummarizationMiddleware(DeepAgentsSummarizationMiddleware):
    """平台的压缩中间件。

    **继承 deepAgents 的那份，不是 LangChain 的那份** —— 这不是风格选择。

    两者的差别正好是压缩最要紧的两件事：

    - **被压缩的历史**：LangChain 版**直接丢**（用 ``RemoveMessage`` 重写
      state）；deepAgents 版先**外置成文件**（``conversation_history/{id}.md``），
      摘要里带上路径，agent 能用 ``read_file`` 回去翻。
    - **改 state 的方式**：LangChain 版在 ``before_model`` 里改写；
      deepAgents 版在 ``wrap_model_call`` 里记一个私有事件，
      ``state["messages"]`` **原样不动**。

    早先这里继承的是 LangChain 版，而 deepAgents 的那份构造函数把 ``backend``
    当**必填关键字参数** —— 我们传进去的 ``backend=`` 被 LangChain 的
    ``**deprecated_kwargs`` **静默吞掉**（那个 ``__init__`` 只认
    ``max_tokens_before_summary`` / ``messages_to_keep``）。实测：

    ```text
    deepAgents 实例._backend             = <PlatformBackend ...>
    ours._backend                        = MISSING（丢了）
    ours._history_path_prefix            = MISSING（丢了）
    ```

    后果是**压缩之后原始对话彻底没了** —— 既没有外置文件，checkpoint 里的
    消息也被重写掉了。长会话里 agent 会"忘记"早期做过什么，且**无法回查**。

    继承 deepAgents 版把这三样能力都拿回来：历史外置、state 不被改写、
    大工具参数预裁剪（``_truncate_args``）、以及超窗后的降级重试
    （``_check_reduction``）。
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
            backend: 文件后端。**必填** —— 给了才会把被压缩的历史外置成
                文件，不给的话历史直接不可恢复。
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
            # 兜底触发条件（框架那套）：对话的份额 —— 不是 0.85。
            trigger = ("fraction", conversation_budget_ratio(settings.context_budget_ratio))
            keep = ("fraction", KEEP_FRACTION)

        super().__init__(
            model=model,
            # **backend 是 deepAgents 版的必填参数** —— 少了它就没有历史外置。
            backend=backend,
            summarizer=summarizer,
            trigger=kwargs.pop("trigger", trigger),
            keep=kwargs.pop("keep", keep),
            # **摘要要涵盖全部被压缩的历史** —— deepAgents 版默认只送最后
            # 4000 token 进摘要（``trim_tokens_to_summarize``），更早的那部分
            # **既不进摘要也不被保留**，直接消失（实测：120 条消息里索引
            # 0~67 的内容就这么没了）。设成 None = 全量送进摘要。
            trim_tokens_to_summarize=kwargs.pop("trim_tokens_to_summarize", None),
            **kwargs,
        )
        self._keep_messages = keep_messages
        self._window = window
        self._fix_artifacts_root()

    # ---------- 阈值的只读视图 ----------
    #
    # deepAgents 版把 ``trigger`` / ``keep`` 放在内部的 LangChain 助手上，
    # 自己身上没有这两个属性。但它们是**平台定的**（不是框架默认的），
    # 测试与排障都要看得见 —— 所以在这里透出来。

    @property
    def trigger(self) -> Any:
        """触发压缩的条件。"""
        return self._lc_helper.trigger

    @property
    def keep(self) -> Any:
        """压缩时保留多少历史。"""
        return self._lc_helper.keep

    @property
    def trim_tokens_to_summarize(self) -> int | None:
        """送进摘要模型的历史上限（token）；``None`` = 全量送。"""
        return self._lc_helper.trim_tokens_to_summarize

    def _fix_artifacts_root(self) -> None:
        """把历史外置的落点指回**产物目录**（工作区外面）。

        deepAgents 算 ``artifacts_root`` 用的是 ``isinstance(backend, CompositeBackend)``
        —— 我们传的 ``PlatformBackend`` **是** CompositeBackend 的子类，所以
        框架自己算出来的就是 ``/.artifacts/``。

        这段是**兜底**：万一框架换了判定方式（比如改成 duck typing），落点会
        悄悄退化成 ``"/"`` —— 而 ``"/"`` 落在**用户的工作区**里，也就是被
        压缩掉的对话历史（含用户说过的话、agent 读到的代码）会明晃晃地落进
        用户的仓库，可能被 ``git commit`` 一起提交上去。

        所以这里显式把它指回 ``PlatformBackend.artifacts_root``（那是
        ``/.artifacts/``，挂在工作区外面）。
        """
        backend = getattr(self, "_backend", None)
        root = getattr(backend, "artifacts_root", "") or ""
        if not root or root == "/":
            return
        root = root.rstrip("/")
        self._history_path_prefix = f"{root}/conversation_history"
        self._large_tool_results_prefix = f"{root}/large_tool_results"
        self._media_prefix = f"{self._history_path_prefix}/media"

    def _should_summarize(self, messages: list[Any], total_tokens: int) -> bool:
        """两条度量各用各的阈值。

        框架只有**一个**阈值，可它要同时跟两种度量比：

        - ``token_counter(messages)`` —— **只算对话**；
        - ``usage_metadata["total_tokens"]`` —— **整个请求**（含系统提示词与
          工具 schema）。

        同一个阈值比两者，要么提前压缩（把系统提示词的额度也算进对话），
        要么放任请求超窗被厂商拒。

        这里拆成两个：

        - 对话超过**对话的份额**（窗口 − 系统提示词那份）就压缩；
        - 整个请求超过**请求上限**也压缩。
        """
        limit = self._get_profile_limits()
        if limit is None:
            # 窗口未知 —— 退回框架那套绝对值
            return super()._should_summarize(messages, total_tokens)
        conversation_limit = int(
            limit * conversation_budget_ratio(settings.context_budget_ratio)
        )
        if total_tokens >= conversation_limit:
            return True
        total_limit = int(limit * TOTAL_REQUEST_FRACTION)
        # **走 ``_lc_helper``** —— deepAgents 版把这条判断放在它的 LangChain
        # 助手上，自己身上没有这个方法（直接调会 AttributeError）。
        return self._lc_helper._should_summarize_based_on_reported_tokens(
            messages, float(total_limit)
        )


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
    "PlatformSummarizationMiddleware",
    "build_compaction_middleware",
]
