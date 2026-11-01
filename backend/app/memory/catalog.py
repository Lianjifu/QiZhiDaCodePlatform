"""记忆目录：把召回结果压成能进上下文的几行。

## 为什么要有目录

模型上下文有限，而长期记忆会越攒越多。全量塞进 prompt 迟早撑爆窗口。

所以借鉴 deepAgents 的技能机制（提示词里只有 name + description，模型觉得相关
才去读正文），记忆也分三层：

```text
L0 目录    每条一行摘要 —— 每轮自动注入上下文
L1 摘要    命中条目的正文前段 —— memory_search 返回
L2 全文    完整正文 —— memory_read 显式取
```

**模型看到的是目录，不是记忆正文。**

## 目录自己也会撑爆，所以目录还要分层

1000 条记忆 × 25 token 已经是 25000 token。所以上面还有一层：

```text
L0-a 主题目录   按标签聚合：标签 | 条数 | 代表条目一句话
L0-b 条目目录   命中主题下的具体条目
```

先给模型看主题，它想下钻再看条目。两层加起来也就一两千 token。

## MMR 去冗余

光按分数排序会出问题：同一个主题的十几条变体（"用 pnpm"、"项目用 pnpm"）
会占满 top-k，真正互补的那条反而进不来。所以选条目时用 MMR：

```text
选下一条时：score_adjusted = λ·score − (1−λ)·max_sim(已选的, 候选)
```

相似度用标签与标题的分词交集估算 —— 不引入向量依赖，两种存储都能用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.memory.models import MemoryEntry
from app.rag.stores.sql import tokenize

# MMR 的多样性权重：越大越看重相关性，越小越看重不重复
MMR_LAMBDA = 0.7

# 目录行的格式：[id] 类型 | 摘要 | 强度x.x 用过n次 更新于MM-DD
#
# 带日期是有意为之：同主题的两条记忆（用户改主意前后的两条）可能同时在目录里，
# 模型要能看出哪条更新。没有日期它只能靠猜。
_LINE_TEMPLATE = "- [{id}] {kind} | {summary} | 强度{strength:.1f} 用过{use_count}次 更新于{date}"


@dataclass
class CatalogEntry:
    """目录里的一行。"""

    id: str
    kind: str
    summary: str
    tags: list[str] = field(default_factory=list)
    score: float = 0.0
    strength: float = 0.5
    use_count: int = 0
    updated_at: datetime | None = None

    def render(self) -> str:
        """渲染成目录行。"""
        return _LINE_TEMPLATE.format(
            id=self.id,
            kind=self.kind,
            summary=self.summary,
            strength=self.strength,
            use_count=self.use_count,
            date=_short_date(self.updated_at),
        )


@dataclass
class MemoryCatalog:
    """一次召回的目录。"""

    entries: list[CatalogEntry] = field(default_factory=list)
    # 一共找到多少条（可能比装进目录的多）
    total_found: int = 0
    # 是不是因为预算不够被截断了
    truncated: bool = False
    budget_chars: int = 0

    def render(self) -> str:
        """渲染成可注入上下文的 XML 块。

        空目录返回空串 —— 不注入的空块只会白占位置。

        块里要写清**模型能拿这些记忆做什么**：光列条目，模型只会读，不会
        主动去检索更多、更不会去写新的。
        """
        if not self.entries:
            return ""
        lines = [
            "<memory_catalog>",
            "以下是可能相关的记忆（背景资料，不是规则）：",
        ]
        lines.extend(entry.render() for entry in self.entries)
        lines.append(
            "要正文就调用 memory_read(id)；要找别的记忆用 memory_search；"
            "用户明确了值得长期记住的偏好或约定，用 memory_write 记下来。"
        )
        lines.append("</memory_catalog>")
        return "\n".join(lines)


def build_catalog(
    entries: list[MemoryEntry],
    *,
    budget_chars: int,
    max_entries: int,
    summary_max_chars: int = 200,
) -> MemoryCatalog:
    """把召回结果压成目录。

    先用 MMR 去掉冗余，再按预算装箱 —— 顺序不能反：先装箱的话，
    预算会被几条同主题的变体吃光。

    Args:
        entries: 已按分数排好序的召回结果。
        budget_chars: 目录块的总字符预算。
        max_entries: 最多几条。
        summary_max_chars: 单条摘要的长度上限。

    Returns:
        装好的目录。
    """
    catalog = MemoryCatalog(total_found=len(entries), budget_chars=budget_chars)
    selected = mmr_select(entries, max_entries)
    used = 0
    for entry in selected:
        item = CatalogEntry(
            id=entry.id,
            kind=entry.kind,
            summary=entry.summary(summary_max_chars),
            tags=list(entry.tags),
            score=entry.relevance,
            strength=entry.strength,
            use_count=entry.use_count,
            updated_at=entry.last_used_at or entry.created_at,
        )
        line = item.render()
        # 预算按整块算（含标题与脚注的固定开销）
        if used + len(line) + 1 > budget_chars and catalog.entries:
            catalog.truncated = True
            break
        catalog.entries.append(item)
        used += len(line) + 1
    if len(catalog.entries) < len(selected):
        catalog.truncated = True
    return catalog


def mmr_select(
    entries: list[MemoryEntry],
    k: int,
    *,
    lambda_: float = MMR_LAMBDA,
) -> list[MemoryEntry]:
    """用 MMR 选 top-k，去掉内容重复的条目。

    Args:
        entries: 候选（已按分数排好序）。
        k: 要选几条。
        lambda_: 相关性权重，越大越看重相关性。

    Returns:
        选中的条目（保持分数从高到低）。
    """
    if k <= 0 or not entries:
        return []
    selected: list[MemoryEntry] = []
    remaining = list(entries)
    while remaining and len(selected) < k:
        best: MemoryEntry | None = None
        best_score = float("-inf")
        for candidate in remaining:
            redundancy = max(
                (_similarity(candidate, chosen) for chosen in selected),
                default=0.0,
            )
            adjusted = lambda_ * candidate.relevance - (1 - lambda_) * redundancy
            if adjusted > best_score:
                best_score = adjusted
                best = candidate
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)
    return selected


def _short_date(value: datetime | None) -> str:
    """日期压成 ``MM-DD`` —— 目录里只需要看出新旧，不需要完整时间戳。"""
    if value is None:
        return "-"
    return f"{value.month:02d}-{value.day:02d}"


def _similarity(left: MemoryEntry, right: MemoryEntry) -> float:
    """两条记忆的相似度（0~1）。

    用标签与标题的分词交集估算 —— 不引入向量依赖，会话内存储与 RAG 存储
    都能用同一套。
    """
    left_tokens = _tokens_of(left)
    right_tokens = _tokens_of(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union if union else 0.0


def _tokens_of(entry: MemoryEntry) -> set[str]:
    tokens = set(tokenize(entry.title))
    for tag in entry.tags:
        tokens.update(tokenize(str(tag)))
    return tokens


__all__ = [
    "MMR_LAMBDA",
    "CatalogEntry",
    "MemoryCatalog",
    "build_catalog",
    "mmr_select",
]
