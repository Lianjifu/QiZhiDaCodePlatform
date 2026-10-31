"""提示词组装器。

不再是简单的字符串拼接 —— 系统提示词由几块拼出来：

    基础提示词（全局常量）
      + 选中技能的知识（knowledge / checklist / examples / constraints）
      + 运行时上下文（工作区、可用能力、约束）

技能那一块才是重点：工具告诉模型"能做什么"，知识告诉它"该怎么做"。
"""

from __future__ import annotations

from app.capabilities.registration.spec import CapabilitySpec


def assemble_prompt(
    base_prompt: str,
    skills: list[CapabilitySpec] | None = None,
    runtime_notes: list[str] | None = None,
) -> str:
    """组装最终的系统提示词。"""
    parts: list[str] = [base_prompt.strip()] if base_prompt else []

    skill_blocks = [block for block in (_skill_block(skill) for skill in skills or []) if block]
    if skill_blocks:
        parts.append("本次任务可用的技能：\n\n" + "\n\n".join(skill_blocks))

    if runtime_notes:
        parts.append("运行环境：\n" + "\n".join(f"- {note}" for note in runtime_notes))

    return "\n\n".join(part for part in parts if part)


def _skill_block(skill: CapabilitySpec) -> str:
    """一个技能的知识块。"""
    title = skill.display_name or skill.key
    lines: list[str] = [f"【{title}】"]
    if skill.knowledge:
        lines.append(skill.knowledge.strip())
    if skill.checklist:
        lines.append("执行检查项：\n" + "\n".join(f"- {item}" for item in skill.checklist))
    if skill.constraints:
        lines.append(f"约束：{skill.constraints.strip()}")
    if skill.examples:
        lines.append("示例：\n" + "\n".join(f"- {example}" for example in skill.examples))
    if not skill.knowledge and not skill.checklist and not skill.constraints and not skill.examples:
        return ""
    return "\n".join(lines)
