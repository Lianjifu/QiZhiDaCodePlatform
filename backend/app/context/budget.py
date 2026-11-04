"""上下文预算：这次能给多少字符。

## 一个窗口怎么分

```text
窗口（模型的 max_input_tokens）
  ├─ 系统提示词  0.5 × 窗口   ← 提示词、记忆目录、策略说明、上传件清单
  ├─ 对话历史    0.5 × 窗口   ← 压缩阈值按这个算
  └─ 余量        0.15 × 窗口  ← 模型输出、工具 schema、框架追加的提示词
```

**两个份额必须一起看。** 早先这两块各算各的：系统提示词按 0.5×窗口、
压缩阈值按 0.85×窗口 —— 一次请求最多能到 1.35×窗口，厂商直接拒。

所以对话的份额是**减出来的**（窗口 − 系统提示词那份），不是另定一个数。
`compression`（压缩中间件）按同一套比例算阈值。

## 占比为什么默认 0.5

一半给了上下文，一半给了"消耗 token 的其他东西"。这个比例不是最优解 ——
但在没有实测数据之前，**保守**比激进好：超预算的后果是请求被厂商拒绝
（整轮对话失败），而少用一点只是多几轮往返。

## 窗口未知怎么办

窗口拿不到（provider 没填 `context_window`、预设里也没有）时**不猜** ——
用一个保守的固定值。猜大了会在真正窗口用满前就开始压缩（浪费），
猜小了会压缩得太晚、直接撞上厂商报错（更糟）。
"""

from __future__ import annotations

from app.core.config import settings

# 窗口未知时的兜底。跟 deepAgents 的默认触发线（170000 token）同量级，
# 宁可保守也别让请求撞上厂商的报错。
FALLBACK_CONTEXT_WINDOW = 128000

# 系统提示词占窗口的比例。另一半留给输出与工具 schema。
CONTEXT_BUDGET_RATIO = 0.5

# 整个请求（系统提示词 + 对话 + 工具 schema）的上限。
#
# 压缩中间件拿**厂商报回来的 total_tokens**（那是整个请求的大小）跟它比，
# 越线就压缩 —— 这样"真发出去的请求有多大"始终有把尺子。
TOTAL_REQUEST_FRACTION = 0.85


def conversation_budget_ratio(context_budget_ratio: float = CONTEXT_BUDGET_RATIO) -> float:
    """对话历史能占窗口的多少。

    **窗口减去系统提示词那份** —— 不是另定一个数。早先对话按 0.85×窗口算、
    系统提示词另占 0.5×窗口，两份预算互不知情，一次请求能到 1.35×窗口。

    Args:
        context_budget_ratio: 系统提示词占窗口的比例。

    Returns:
        对话的份额（0 到 1 之间）。
    """
    return max(0.0, min(1.0, 1.0 - context_budget_ratio))


def resolve_window(context_window: int) -> int:
    """取本次使用的上下文窗口。

    Args:
        context_window: 已知的窗口（token）；0 或负数表示未知。

    Returns:
        窗口大小（token）。未知时返回保守的兜底值。
    """
    return context_window if context_window > 0 else FALLBACK_CONTEXT_WINDOW


def budget_for_window(
    context_window: int,
    *,
    ratio: float = CONTEXT_BUDGET_RATIO,
    chars_per_token: float = 4.0,
) -> int:
    """按窗口算上下文预算（字符）。

    Args:
        context_window: 窗口（token）。0 = 未知，用保守兜底。
        ratio: 上下文占窗口的比例。
        chars_per_token: 一个 token 折算多少字符。取 4 是中文与英文的折中 ——
            英文大约 4 字符/token，中文大约 1.5。

    Returns:
        预算字符数。
    """
    window = resolve_window(context_window)
    return int(window * ratio * chars_per_token)


def budget_from_settings(context_window: int = 0) -> int:
    """按配置里的比例算预算。

    Args:
        context_window: 窗口（token）。

    Returns:
        预算字符数。
    """
    return budget_for_window(
        context_window,
        ratio=settings.context_budget_ratio,
        chars_per_token=settings.context_chars_per_token,
    )


__all__ = [
    "CONTEXT_BUDGET_RATIO",
    "FALLBACK_CONTEXT_WINDOW",
    "TOTAL_REQUEST_FRACTION",
    "budget_for_window",
    "budget_from_settings",
    "conversation_budget_ratio",
    "resolve_window",
]
