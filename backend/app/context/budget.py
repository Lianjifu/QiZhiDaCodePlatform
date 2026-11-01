"""上下文预算：这次能给多少字符。

## 预算怎么来的

```text
窗口（模型的 max_input_tokens）
  × 占比（默认 0.5）
  = 上下文预算
```

另一半留给**我们控制不了的部分**：模型输出（`max_tokens`）、工具 schema
（十来个工具的 JSON，几千 token）、deepAgents 自己追加的提示词（文件工具说明、
虚拟路径映射…）。

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

# 上下文占窗口的比例。另一半留给输出与工具 schema。
CONTEXT_BUDGET_RATIO = 0.5


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
    "budget_for_window",
    "budget_from_settings",
    "resolve_window",
]
