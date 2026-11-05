"""记忆条目：两种存储共用的领域结构，以及配套的评分口径。

## 为什么有两种 scope

| scope | 范围 | 存储 |
| --- | --- | --- |
| ``session`` | 会话内 | ``memory_item`` 表 |
| ``long_term`` | 跨会话 | RAG 的专用集合 |

两种存储的检索方式、更新方式都不同（表 vs 集合），但**对上层是同一种东西** ——
所以定义一个领域结构 ``MemoryEntry``，两种 store 各自映射到它。

## 评分口径

排序分三段：

```text
score = relevance × time_factor × usage_factor
```

- ``relevance`` —— 检索相关度，**主导项**（向量 + 关键词融合分）；
- ``time_factor`` —— 时间衰减，**限制在 [0.7, 1.0]**。做成断崖式衰减
  （``0.5^(age/7)``）会误伤"很久没用但依然有效"的记忆，所以只做微调；
- ``usage_factor`` —— 被召回过的次数，用 10 次封顶。

**降权只影响排序，不影响召回资格**：只要没被显式删除，记忆永远在候选池里。
"很久没用"不等于"已过时"。

## 命中即重新激活

召回一条记忆就刷新 ``last_used_at`` 与 ``use_count`` —— 衰减从**最后一次被
召回**重新算，不是从创建时间算。这跟人类记忆的规律一致：记忆强度由"回忆次数"
决定，不由"距上次多久"决定。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

# ---- scope ----
SCOPE_SESSION = "session"  # 会话内
SCOPE_LONG_TERM = "long_term"  # 跨会话
SCOPE_ALL = "all"  # 两个都搜（检索时的"全都找"）

# ---- kind ----
KIND_PREFERENCE = "preference"  # 用户的稳定偏好
KIND_DECISION = "decision"  # 已定的做法与理由
KIND_CONVENTION = "convention"  # 项目约定
KIND_FACT = "fact"  # 外部世界的事实
KIND_PROFILE = "profile"  # 关于用户本人
KIND_NOTE = "note"  # 其它

MEMORY_KINDS = (
    KIND_PREFERENCE,
    KIND_DECISION,
    KIND_CONVENTION,
    KIND_FACT,
    KIND_PROFILE,
    KIND_NOTE,
)

# ---- 来源 ----
SOURCE_USER = "user"  # 用户明确说的
SOURCE_MODEL = "model-inferred"  # 模型推断的

# 时间衰减的下限：再旧也只降到 0.7，不让时间把记忆踢出视野
TIME_FACTOR_FLOOR = 0.7
# 使用次数项的下限：没用过也只降到 0.85
USAGE_FACTOR_FLOOR = 0.85
# 使用次数的封顶值（用满 10 次就不再加分）
USAGE_SATURATION = 10
# 来源权重：用户明确说的比模型推断的更权威
#
# **用户说的（source=user）= 1.0，模型推断的（model-inferred）= 0.9**。
#
# 为什么模型推断的不能跟用户说的平起平坐：模型推断的东西**可能猜错**，
# 而用户明确表达的偏好与约定不会。用户说"用 pnpm"是事实；模型从代码里
# 看出"好像用 pnpm"是推断 —— 两者混为一谈时，召回会把推断当承诺。
#
# 只降 10% 而不是更多：降太多就变成"事实上的淘汰"，违背"降权 ≠ 淘汰"；
# 10% 足以在同等相关度时把用户说的排在前面。
SOURCE_WEIGHTS: dict[str, float] = {
    SOURCE_USER: 1.0,
    SOURCE_MODEL: 0.9,
}
# 未知来源按模型推断算（保守的那一侧 —— 宁可低估权威性）
SOURCE_DEFAULT_WEIGHT = SOURCE_WEIGHTS[SOURCE_MODEL]


def source_weight(source: str) -> float:
    """来源权重 —— 参与召回排序。

    Args:
        source: 来源标记（``user`` / ``model-inferred``）。

    Returns:
        权重系数。未知来源按模型推断算。
    """
    return SOURCE_WEIGHTS.get(str(source or ""), SOURCE_DEFAULT_WEIGHT)


@dataclass
class MemoryEntry:
    """一条记忆。

    ``id`` 带存储前缀（``s:12`` / ``l:34``）—— 召回、读取、删除都得知道去哪
    个存储里找，前缀是最省事的办法。
    """

    id: str
    scope: str
    title: str = ""
    content: str = ""
    kind: str = KIND_NOTE
    tags: list[str] = field(default_factory=list)
    strength: float = 0.5
    use_count: int = 0
    source: str = SOURCE_USER
    source_thread_id: str = ""
    # 已被取代（还在，只是不再参与召回）
    superseded: bool = False
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    # 检索相关度（只有召回结果带这个值）
    relevance: float = 0.0

    def summary(self, max_chars: int = 200) -> str:
        """一句话摘要 —— 目录行就取它。

        优先用 ``title``；没有标题就截正文。
        """
        text = (self.title or self.content or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "…"

    def to_dict(self) -> dict:
        """转成字典（给模型看 / 出接口用）。"""
        return {
            "id": self.id,
            "scope": self.scope,
            "title": self.title,
            "content": self.content,
            "kind": self.kind,
            "tags": list(self.tags),
            "strength": round(self.strength, 3),
            "use_count": self.use_count,
            "source": self.source,
            "source_thread_id": self.source_thread_id,
            "superseded": self.superseded,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "relevance": round(self.relevance, 4),
        }


def content_hash(text: str) -> str:
    """内容哈希：用来判重。

    归一化掉空白与大小写 —— "用 pnpm" 和 "用  pnpm " 应该是同一条。
    """
    normalized = " ".join((text or "").split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def ensure_utc(value: datetime | None) -> datetime | None:
    """把 naive datetime 当成 UTC 补上时区。

    SQLite 不存时区（``DateTime(timezone=True)`` 在 SQLite 上照样存 naive），
    从库里读出来的时间戳是 naive 的，而 ``datetime.now(UTC)`` 是 aware 的 ——
    两者相减会直接抛 TypeError。凡是从库里读的时间都要过这里。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def time_factor(
    last_used_at: datetime | None,
    half_life_days: int,
    now: datetime | None = None,
) -> float:
    """时间衰减系数，限制在 ``[0.7, 1.0]``。

    从**最后一次被召回**算起，没被召回过就按创建时间算。

    Args:
        last_used_at: 最后一次被召回（或创建）的时间。
        half_life_days: 半衰期（天）。
        now: 当前时间。

    Returns:
        系数，越近越接近 1。
    """
    last_used_at = ensure_utc(last_used_at)
    if not last_used_at:
        return 1.0
    reference = ensure_utc(now) or datetime.now(UTC)
    age_days = max(0.0, (reference - last_used_at).total_seconds() / 86400)
    if half_life_days <= 0:
        return 1.0
    decayed = 0.5 ** (age_days / half_life_days)
    return max(TIME_FACTOR_FLOOR, min(1.0, decayed))


def usage_factor(use_count: int) -> float:
    """使用次数系数，归一化到 0~1（用满 10 次封顶）。

    这个**原始值**不是直接乘上去的 —— 直接乘的话，一条从没用过的记忆会被
    砍掉一半，"相关性主导"就成了空话。真正参与打分的是加了地板的版本
    （见 :func:`effective_score`）。
    """
    if use_count <= 0:
        return 0.0
    return min(1.0, math.log1p(use_count) / math.log1p(USAGE_SATURATION))


def effective_score(
    relevance: float,
    use_count: int,
    last_used_at: datetime | None,
    half_life_days: int,
    now: datetime | None = None,
    source: str = SOURCE_MODEL,
) -> float:
    """综合排序分。

    相关度主导，时间与使用次数只做微调 —— 这样"很久没用但依然相关"的记忆
    不会被一条"刚用过但不太相关"的记忆挤下去。

    三项微调都有地板：时间最低 0.7、使用次数最低 0.85、来源最低 0.9，
    合计最多打三折。再多就会把"排序靠后"变成"事实上的消失"，
    那就违背了"降权不等于淘汰"。

    Args:
        relevance: 相关度（召回时的原始分）。
        use_count: 被召回过多少次。
        last_used_at: 最后一次被召回的时间。
        half_life_days: 时间衰减的半衰期（天）。
        now: 当前时间（测试可注入）。
        source: 来源标记 —— 用户说的比模型推断的权威一点。

    Returns:
        综合分。
    """
    usage_term = USAGE_FACTOR_FLOOR + (1 - USAGE_FACTOR_FLOOR) * usage_factor(use_count)
    return (
        max(0.0, relevance)
        * time_factor(last_used_at, half_life_days, now)
        * usage_term
        * source_weight(source)
    )


__all__ = [
    "KIND_CONVENTION",
    "KIND_DECISION",
    "KIND_FACT",
    "KIND_NOTE",
    "KIND_PREFERENCE",
    "KIND_PROFILE",
    "MEMORY_KINDS",
    "SCOPE_ALL",
    "SCOPE_LONG_TERM",
    "SCOPE_SESSION",
    "SOURCE_MODEL",
    "SOURCE_USER",
    "SOURCE_DEFAULT_WEIGHT",
    "SOURCE_WEIGHTS",
    "source_weight",
    "TIME_FACTOR_FLOOR",
    "MemoryEntry",
    "content_hash",
    "effective_score",
    "ensure_utc",
    "time_factor",
    "usage_factor",
]
