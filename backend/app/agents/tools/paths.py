"""工作区路径解析：防止 agent 用 `../` 或绝对路径越出沙箱。

这是 code agent 最容易被诱导利用的地方 —— 模型生成的路径如果不做约束，
一个 `../../etc/passwd` 就能读到工作区外的文件。因此所有涉及路径的工具
都必须经过这里。
"""

from pathlib import Path, PurePosixPath


class WorkspaceError(ValueError):
    """路径越界或非法。"""


def resolve_in_workspace(root: Path, relative: str) -> Path:
    """把相对路径解析到工作区内，越界则抛 `WorkspaceError`。

    - 绝对路径直接拒绝：静默改写成工作区内的同名路径会让模型读写到意料之外的文件；
    - `..` 在 resolve() 阶段被消解，最终仍落在工作区外时报错；
    - 符号链接会被 resolve() 跟随，指到工作区外同样报错。
    """
    if not relative or relative.strip() == "":
        raise WorkspaceError("路径不能为空")
    if PurePosixPath(relative).is_absolute() or Path(relative).is_absolute():
        raise WorkspaceError(f"只允许相对路径，请不要使用绝对路径: {relative}")

    base = root.resolve()
    candidate = (base / relative).resolve()
    if candidate != base and base not in candidate.parents:
        raise WorkspaceError(f"路径超出工作区范围: {relative}")
    return candidate
