"""把数据库里的技能导出成 deepAgents 认的 SKILL.md。

deepAgents 的技能是**文件系统上的目录**：一个技能一个目录，里面一份 ``SKILL.md``，
frontmatter 写 ``name`` / ``description``，正文是知识。技能中间件按"渐进披露"
用它们 —— 先只把名字和描述放进提示词，模型觉得相关了才去读正文。

我们的技能存在数据库里，所以每次会话开始前把它们导出成文件，再把那个目录挂进
backend 的 ``/skills/`` 路由。

**导出而不是改数据库结构**：数据库仍然是唯一事实来源，导出的文件是这次会话的
快照。导出目录放在**工作区外面** —— 放进工作区的话，agent 的文件工具会看见并
可能改写这些文件。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from app.capabilities.registration.spec import KIND_SKILL, CapabilitySpec

logger = logging.getLogger(__name__)

# backend 里的技能路由前缀。跟 deepAgents 的技能中间件约定一致：源路径是
# 虚拟路径，backend 负责把它映射到真实目录。
SKILLS_ROUTE = "/skills/"

# frontmatter 里 description 的上限（Agent Skills 规范）
_MAX_DESCRIPTION = 1024


def export_skills(
    specs: list[CapabilitySpec],
    skills_dir: Path,
    skill_ids: list[int] | None = None,
) -> list[str]:
    """把技能导出成 ``SKILL.md`` 文件。

    Args:
        specs: 统一能力清单（只取 ``kind=skill`` 的部分）。
        skills_dir: 导出目录。
        skill_ids: 显式指定的技能 id（数据库主键）。给了就只导出这几个，
            不给就导出全部启用的技能 —— 让模型自己按描述挑。

    Returns:
        交给 deepAgents 的技能源路径列表。没有技能时返回空列表。
    """
    wanted = _wanted(specs, skill_ids)
    if not wanted:
        # 没有技能时把目录清掉：上一次的残留会让模型看到已经不存在的技能
        if skills_dir.exists():
            shutil.rmtree(skills_dir, ignore_errors=True)
        return []

    skills_dir.mkdir(parents=True, exist_ok=True)
    keep: set[str] = set()
    for spec in wanted:
        slug = _slugify(spec.key)
        if not slug:
            logger.warning("技能 %s 的名字转不出合法目录名，已跳过", spec.key)
            continue
        keep.add(slug)
        target = skills_dir / slug
        content = render_skill_md(spec, slug)
        path = target / "SKILL.md"
        # **内容没变就别写** —— 以前每次都把整个目录 rmtree 掉再重写一遍，
        # 那是每个请求都要付的磁盘开销（技能正文还不小）。
        try:
            if path.read_text(encoding="utf-8") == content:
                continue
        except OSError:
            pass  # 文件不存在或读不了 → 下面写
        target.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # **多余的删掉** —— 上一次的残留会让模型看到已经不存在的技能
    # （技能没了、停了，或者这次只导出其中几个）。
    for entry in skills_dir.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)

    return [SKILLS_ROUTE]


def render_skill_md(spec: CapabilitySpec, slug: str) -> str:
    """渲染一份 ``SKILL.md``。

    正文沿用原来的四段结构：知识、检查项、约束、示例 —— 顺序是有意的，
    先说"该怎么做"，再说"做完检查什么"、"不许做什么"、"长什么样"。

    ``description`` 里带上技能的**原名** —— 目录名可能是哈希出来的合法 ASCII，
    模型要靠这个描述认出它。
    """
    title = spec.display_name or spec.key
    description = spec.description or title
    frontmatter_description = f"{title}：{description}" if title else description
    lines = [
        "---",
        f"name: {slug}",
        f"description: {frontmatter_description[:_MAX_DESCRIPTION]}",
        "---",
        "",
    ]

    body: list[str] = []
    if spec.knowledge:
        body.append(spec.knowledge.strip())
    if spec.checklist:
        body.append("## 执行检查项\n\n" + "\n".join(f"- {item}" for item in spec.checklist))
    if spec.constraints:
        body.append(f"## 约束\n\n{spec.constraints.strip()}")
    if spec.examples:
        body.append("## 示例\n\n" + "\n".join(f"- {example}" for example in spec.examples))
    if spec.members:
        body.append("## 承载的能力\n\n" + "\n".join(f"- {member}" for member in spec.members))

    lines.extend(body)
    return "\n".join(lines).rstrip() + "\n"


def _wanted(specs: list[CapabilitySpec], skill_ids: list[int] | None) -> list[CapabilitySpec]:
    """挑出本次要导出的技能。"""
    skills = [spec for spec in specs if spec.kind == KIND_SKILL and spec.enabled]
    if not skill_ids:
        return skills
    wanted_ids = set(skill_ids)
    return [spec for spec in skills if spec.db_id in wanted_ids]


def _slugify(name: str) -> str:
    """把技能名转成合法的目录名。

    Agent Skills 规范要求：小写 ASCII 字母 / 数字 / 单个连字符，不能以连字符
    开头结尾，不能有连续连字符，而且**目录名必须和 frontmatter 里的 name 一致**。

    中文名转不出合法目录名 —— 这时退回到一个稳定的哈希名。技能的中文名不丢：
    它写进 frontmatter 的 ``description``，模型照样认得出来。
    """
    chars: list[str] = []
    for char in (name or "").strip().lower():
        if char == "-":
            chars.append("-")
        elif ("a" <= char <= "z") or ("0" <= char <= "9"):
            chars.append(char)
        else:
            chars.append("-")
    slug = "".join(chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    if not slug:
        digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()
        slug = f"skill-{digest[:8]}"
    return slug


__all__ = ["SKILLS_ROUTE", "export_skills", "render_skill_md"]
