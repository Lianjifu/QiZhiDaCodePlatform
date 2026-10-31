"""意图层的规划部分：任务上下文 → 候选技能 → 收敛出本次允许的能力集。

只做检索与收敛，不决定"调哪个能力"——那是模型的事。
"""

from __future__ import annotations

import re

from app.capabilities.registration.spec import KIND_SKILL, KIND_TOOL, CapabilitySpec

_CJK = re.compile(r"[一-鿿]")
_SPLIT = re.compile(r"[\s,，。；;、:：!！?？()（）\[\]【】/\\]+")


def tokenize(text: str) -> list[str]:
    """粗粒度分词：空白切分 + 中文二元组。

    不追求分词质量，只求"用户说的词"能命中"能力描述里的词"。
    """
    lowered = (text or "").lower()
    tokens = [token for token in _SPLIT.split(lowered) if token]
    extra: list[str] = []
    for token in tokens:
        if len(token) > 1 and _CJK.search(token):
            extra.extend(token[i : i + 2] for i in range(len(token) - 1))
    return tokens + extra


def score_entry(spec: CapabilitySpec, tokens: list[str]) -> float:
    """给一条能力打相关性分。"""
    if not tokens:
        return 0.0
    haystack = " ".join(
        [spec.key, spec.display_name, spec.description, " ".join(spec.tags)]
    ).lower()
    score = 0.0
    for token in tokens:
        if len(token) < 2:
            continue
        if token in haystack:
            score += 1.0
        for tag in spec.tags:
            if token in tag.lower():
                score += 1.5
    return score


def retrieve_skills(
    specs: list[CapabilitySpec],
    query: str,
    top_k: int = 5,
) -> list[CapabilitySpec]:
    """按用户输入检索相关技能。

    命中的技能会被挂载；一个都没命中时返回空列表 —— 让上层决定是"不挂技能"
    还是"退回全量能力"。
    """
    tokens = tokenize(query)
    scored = [
        (score_entry(spec, tokens), spec)
        for spec in specs
        if spec.kind == KIND_SKILL and spec.enabled
    ]
    scored = [(score, spec) for score, spec in scored if score > 0]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [spec for _score, spec in scored[:top_k]]


def collect_tool_keys(
    skills: list[CapabilitySpec],
    specs: list[CapabilitySpec],
    include_builtin: bool = True,
) -> set[str]:
    """技能 → 它们承载的原子能力 key。

    ``include_builtin`` 为真时，出厂内置能力（读文件、写文件、执行代码这类
    工作区基本操作）始终保留 —— 否则命中一个技能反而把 agent 的基本能力削掉了。
    """
    by_key = {spec.key: spec for spec in specs if spec.kind == KIND_TOOL}
    keys: set[str] = set()
    for skill in skills:
        for member in skill.members:
            if member in by_key:
                keys.add(member)
    if include_builtin:
        for spec in specs:
            if spec.kind == KIND_TOOL and spec.origin == "builtin" and spec.enabled:
                keys.add(spec.key)
    return keys


def select_skills(
    specs: list[CapabilitySpec],
    query: str,
    skill_ids: list[int] | None = None,
    top_k: int = 5,
) -> list[CapabilitySpec]:
    """决定本次会话挂载哪些技能。

    显式指定（skill_ids）优先；没指定就按用户输入检索。
    """
    if skill_ids:
        wanted = set(skill_ids)
        return [
            spec
            for spec in specs
            if spec.kind == KIND_SKILL and spec.enabled and spec.db_id in wanted
        ]
    return retrieve_skills(specs, query, top_k=top_k)


__all__ = [
    "collect_tool_keys",
    "retrieve_skills",
    "score_entry",
    "select_skills",
    "tokenize",
]
