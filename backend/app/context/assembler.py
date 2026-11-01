"""上下文装配：把各个来源的块按配额装成一份提示词。

## 装箱算法

**先按优先级分档，再逐档装箱。** 不是简单的"按优先级排序后依次装" ——
那样一个长块会把后面的整批挤掉。分档的意义在于：

```text
第一档（priority < 20）   必装 —— 装不下就报错
第二档（20 ≤ priority < 40）尽量装 —— 装不下可截断
第三档（priority ≥ 40）   有余才装 —— 装不下就丢
```

同一档内按 priority 升序，同 priority 按原来的顺序（稳定）。

## 为什么预算按比例而不是绝对值

不同模型的窗口差 10 倍（8k 的本地小模型 vs 1M 的 DeepSeek）。写死字符数
意味着要么在小模型上超预算，要么在大模型上浪费 90%。

所以预算 = ``窗口 × 比例``。剩下那一半留给**输出**（`max_tokens`）、
**工具 schema**（十来个工具的 JSON，几千 token）和 deepAgents 自己追加的
提示词 —— 那些我们控制不了，只能给它留地方。

## 截断与丢弃的区别

装不下时：

- 可截断的块 → 截到剩余预算，**并留下标记**（模型得知道后面被砍了）；
- 只能丢的块 → 整块丢掉；
- 必装的块 → 装不下就**报错**，不静默丢。

静默丢必装块是危险的：策略说明（哪些操作要人批）没了，模型会反复触发审批，
用户看到的是"这 agent 怎么一直要确认"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.context.blocks import (
    OVERFLOW_COMPACT,
    OVERFLOW_TRUNCATE,
    ContextBlock,
)

# 优先级分档的界
# 第一档：必装（基准提示词、运行时环境）
TIER_REQUIRED_MAX = 20
# 第二档：可截断（策略说明、工作区说明）
TIER_TRUNCATABLE_MAX = 40


@dataclass
class AssembledContext:
    """装配结果。

    - ``text`` —— 最终的提示词正文；
    - ``blocks`` —— 装进去的块（按最终顺序）；
    - ``dropped`` —— 被丢掉的块；
    - ``truncated`` —— 被截断的块；
    - ``used_chars`` —— 实际用掉的字符；
    - ``budget_chars`` —— 本次的预算；
    - ``warnings`` —— 需要人知道的异常（必装块装不下、被丢的块很重要…）。
    """

    text: str = ""
    blocks: list[ContextBlock] = field(default_factory=list)
    dropped: list[ContextBlock] = field(default_factory=list)
    truncated: list[ContextBlock] = field(default_factory=list)
    used_chars: int = 0
    budget_chars: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def over_budget(self) -> bool:
        """装出来的东西超预算了吗。

        正常应该永远是 False —— 装配器保证不超。真超了说明有 bug 或者
        有块绕过了预算检查（比如标题行没算进去）。
        """
        return self.used_chars > self.budget_chars


def assemble(
    blocks: list[ContextBlock],
    *,
    budget_chars: int,
    separator: str = "\n\n",
) -> AssembledContext:
    """把块装配成提示词。

    Args:
        blocks: 各个来源交来的块。装配器**不改**入参 —— 返回的是新对象。
        budget_chars: 本次的总预算（字符）。
        separator: 块之间的分隔符。

    Returns:
        装配结果。
    """
    usable = [block for block in blocks if not block.empty]
    result = AssembledContext(budget_chars=budget_chars)
    if not usable:
        return result

    ordered = sorted(enumerate(usable), key=lambda pair: (pair[1].priority, pair[0]))
    remaining = budget_chars
    selected: list[ContextBlock] = []

    for _index, block in ordered:
        if remaining <= 0 and not block.required:
            result.dropped.append(block)
            _note_drop(result, block)
            continue

        cost = _cost(block)
        if cost <= remaining:
            selected.append(block)
            remaining -= cost
            continue

        # 装不下：按溢出策略处理
        if block.required:
            # 必装块装不下 —— 这是配置问题，不是运行时问题，明确报出来
            result.warnings.append(
                f"必装上下文块「{block.key}」超出预算（需要 {cost} 字符，"
                f"只剩 {remaining} 字符），已强制装入 —— 请调大预算或缩短该内容"
            )
            selected.append(block)
            remaining -= cost
            continue

        if block.overflow == OVERFLOW_TRUNCATE:
            truncated = block.truncated(remaining)
            if truncated.content.strip():
                selected.append(truncated)
                result.truncated.append(truncated)
            else:
                result.dropped.append(block)
                _note_drop(result, block)
            remaining = 0
            continue

        if block.overflow == OVERFLOW_COMPACT:
            # 压缩需要额外一轮模型调用，这里只标记 —— 真要压缩时由调用方决定
            result.dropped.append(block)
            result.warnings.append(
                f"上下文块「{block.key}」超出预算且策略为 compact，"
                f"本次未装入（对话历史的压缩由框架的摘要中间件负责）"
            )
            continue

        # 默认：DROP
        result.dropped.append(block)
        _note_drop(result, block)

    # 最终顺序：按 priority 排回去 —— 高优先级的块应该在提示词前面
    selected.sort(key=lambda block: block.priority)
    result.blocks = selected
    result.text = separator.join(block.rendered() for block in selected if block.rendered())
    result.used_chars = len(result.text)
    return result


def _cost(block: ContextBlock) -> int:
    """块的预算成本。

    算**渲染后**的长度（含标题）—— 标题也占位置。
    """
    return len(block.rendered()) + 2  # +2 是块之间的分隔符


def _note_drop(result: AssembledContext, block: ContextBlock) -> None:
    """丢掉一个块时记一笔值得注意的。

    不是每个块丢了都值得警告 —— 记忆目录丢了很正常（下一轮还会召回）。
    但策略说明（哪些操作要人批）丢了就有问题：模型不知道，会反复触发审批。
    """
    if block.key == "policy":
        result.warnings.append(
            "策略说明（哪些工具需要人工审批）被挤出上下文 —— 模型可能反复触发审批，请调大上下文预算"
        )


__all__ = [
    "TIER_REQUIRED_MAX",
    "TIER_TRUNCATABLE_MAX",
    "AssembledContext",
    "assemble",
]
