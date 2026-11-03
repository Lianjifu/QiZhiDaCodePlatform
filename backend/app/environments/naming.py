"""分支名与路径片段的命名规则。

分支名会变成**目录名**（worktree 的落点），也会变成 git 引用名 —— 两边都
有规矩，所以在这里统一处理一次。

- **分支名**：交给 git 自己校验（``git check-ref-format --branch``），
  它比任何正则都准；
- **目录片段（slug）**：我们自己拼，要保证拼出来的路径是安全的 ——
  只留字母数字与 ``-_.``，其余换成 ``-``。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.exceptions import ValidationError

# 目录片段里允许的字符：字母数字（含中文）、下划线、点、短横线。
# 路径分隔符、空格与各种标点一律换成短横 —— 片段会变成目录名。
_SAFE = re.compile(r"[^\w.-]+", re.UNICODE)


def slugify(text: str, *, max_length: int = 48) -> str:
    """把任意文本变成安全的路径片段。

    Args:
        text: 原始文本（分支名、任务描述……）。
        max_length: 片段最长多少字符。

    Returns:
        安全的片段；实在挑不出字符时返回 ``"task"``。
    """
    cleaned = _SAFE.sub("-", (text or "").strip()).strip("-._")
    if not cleaned:
        return "task"
    return cleaned[:max_length].strip("-._") or "task"


def validate_branch(branch: str) -> str:
    """校验分支名是否合法（交给 git 判定）。

    Args:
        branch: 分支名。

    Returns:
        去掉首尾空白后的分支名。

    Raises:
        ValidationError: 分支名非法。
    """
    from app.git.service import GitService

    name = (branch or "").strip()
    if not name:
        raise ValidationError("分支名不能为空")
    if not GitService(Path.cwd(), timeout=10).is_valid_branch(name):
        raise ValidationError(f"分支名不合法：{name}")
    return name


__all__ = ["slugify", "validate_branch"]
