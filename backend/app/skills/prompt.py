"""技能提示词拼装。"""

from __future__ import annotations

from collections.abc import Iterable


def build_skill_prompt(skills: Iterable) -> str:
    """把多个技能的提示词片段拼成一段附加提示词。

    技能本身的 prompt 是"这个技能该怎么用"，这里只负责组织，不做内容加工。
    """
    parts: list[str] = []
    for skill in skills:
        prompt = (getattr(skill, "prompt", "") or "").strip()
        if not prompt:
            continue
        name = getattr(skill, "name", "技能")
        parts.append(f"【{name}】\n{prompt}")
    if not parts:
        return ""
    return "本次任务可以启用以下技能：\n\n" + "\n\n".join(parts)
