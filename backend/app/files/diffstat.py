"""改动量统计：这次写入/编辑改了多少行。

前端工具块上那个 ``+12 −3`` 就是它算出来的。

## 为什么自己算

deepAgents 的 ``write_file`` / ``edit_file`` 都不返回 diff —— 成功时只说
"Updated file xxx"。要显示改动量，就得**执行前后各读一次文件**，中间差多少行。

## 怎么算

按行做 diff（``difflib``），统计新增行与删除行：

- 新增一段 → 算新增；
- 删掉一段 → 算删除；
- 改掉一段 → 算「删旧 + 增新」（跟 git 的行数统计一个口径）。

**不是字符数、不是 token 数** —— 用户看到的是"我改了十几行"，不是"我写了
三千个字符"。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class DiffStat:
    """一次改动的行数统计。"""

    added: int = 0
    removed: int = 0

    @property
    def changed(self) -> bool:
        """有没有实际改动。"""
        return self.added > 0 or self.removed > 0


def line_diff(before: str, after: str) -> DiffStat:
    """两个文本之间的行级差异。

    Args:
        before: 改动前的文本。
        after: 改动后的文本。

    Returns:
        新增行数与删除行数。
    """
    if before == after:
        return DiffStat()

    before_lines = before.splitlines()
    after_lines = after.splitlines()

    added = 0
    removed = 0
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return DiffStat(added=added, removed=removed)


__all__ = ["DiffStat", "line_diff"]
