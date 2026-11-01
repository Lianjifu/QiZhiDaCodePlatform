"""技能清单配额：技能多的时候只列前 N 个。

## 为什么需要

deepAgents 的技能中间件会把**全部**技能的 name + description 塞进系统提示词
（正文靠模型自己 ``read_file`` 按需读 —— 那部分是对的）。技能少的时候没问题，
但清单本身没有上限：五十个技能就是几千 token，每轮都付。

## 怎么改

``SkillsMiddleware._format_skills_list`` 是个私有方法，覆盖它就能改清单的
渲染方式。**不重写整个中间件** —— 技能加载、渐进披露、动态工具挂载那些机制
都是框架的，动了容易坏。

被省略的技能会在清单末尾留一行说明，告诉模型还有多少个没列出来、怎么找到
它们 —— 静默截断会让模型以为只有这几个技能。

⚠️ 覆盖私有方法 = 依赖框架实现细节。升级 deepAgents 时这里可能失效 ——
失效的表现是清单又变回"全列"，不是功能坏掉，所以风险可控。
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.skills import SkillsMiddleware

logger = logging.getLogger(__name__)

# 默认最多列多少个技能
DEFAULT_MAX_SKILLS = 20


class QuotaSkillsMiddleware(SkillsMiddleware):
    """带清单配额的技能中间件。

    名字**不能改** —— deepAgents 按 name 替换默认项（跟压缩中间件同一套机制）。
    """

    name = "SkillsMiddleware"

    def __init__(self, *, max_skills: int = DEFAULT_MAX_SKILLS, **kwargs: Any) -> None:
        """构造。

        Args:
            max_skills: 清单里最多列几个技能。0 = 不限制。
            **kwargs: 透传给框架的 ``SkillsMiddleware``。
        """
        super().__init__(**kwargs)
        self._max_skills = max_skills

    def _format_skills_list(self, skills: list[dict[str, Any]]) -> str:
        """渲染技能清单，超出配额就截断并注明还剩多少。

        Args:
            skills: 框架给的技能元数据。

        Returns:
            清单文本。
        """
        full = super()._format_skills_list(skills)
        limit = self._max_skills
        if limit <= 0 or len(skills) <= limit:
            return full

        kept = skills[:limit]
        # 前 limit 个照常渲染，剩下的只留一行说明
        lines = super()._format_skills_list(kept).split("\n")
        omitted = len(skills) - limit
        lines.append(
            f"- 还有 {omitted} 个技能未列出（按名称或描述查找；技能目录可用 ls 查看完整列表）"
        )
        return "\n".join(lines)


__all__ = ["DEFAULT_MAX_SKILLS", "QuotaSkillsMiddleware"]
