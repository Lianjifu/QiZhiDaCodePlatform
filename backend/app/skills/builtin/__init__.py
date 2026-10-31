"""内置工具注册表。

**这是一个白名单，不是任意入口点。** builtin 类型的工具只允许引用这里登记过的
`模块:工厂函数`—— 否则任何能调工具管理接口的人都能让平台 import 并执行任意
Python 代码（等价于远程代码执行）。新增内置工具必须改这个文件。

约定：每个工厂函数签名为 `factory(context: dict) -> Callable`，
`context` 由运行时注入（工作区路径、沙箱配置等），不会出现在给模型的 schema 里。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinToolSpec:
    """内置工具的元信息。"""

    key: str
    source: str  # "模块:工厂函数"
    display_name: str
    description: str


BUILTIN_TOOLS: dict[str, BuiltinToolSpec] = {
    spec.key: spec
    for spec in (
        BuiltinToolSpec(
            key="read_file",
            source="app.skills.builtin.fs:make_read_file",
            display_name="读取文件",
            description="读取工作区内的文本文件内容。path 是相对于工作区根目录的路径。",
        ),
        BuiltinToolSpec(
            key="write_file",
            source="app.skills.builtin.fs:make_write_file",
            display_name="写入文件",
            description="把内容写入工作区内的文件（覆盖写）。path 是相对于工作区根目录的路径。",
        ),
        BuiltinToolSpec(
            key="list_files",
            source="app.skills.builtin.fs:make_list_files",
            display_name="列出文件",
            description="列出工作区内某个目录下的文件和子目录。path 默认为工作区根目录。",
        ),
        BuiltinToolSpec(
            key="run_python",
            source="app.skills.builtin.code:make_run_python",
            display_name="执行 Python",
            description="在工作区的沙箱里执行一段 Python 代码，返回退出码、标准输出和标准错误。",
        ),
    )
}

# source → key 的反查，用于校验用户传入的 source 是否在白名单里
_SOURCE_INDEX: dict[str, str] = {spec.source: key for key, spec in BUILTIN_TOOLS.items()}


def list_builtin_tools() -> list[BuiltinToolSpec]:
    return list(BUILTIN_TOOLS.values())


def get_builtin_by_source(source: str) -> BuiltinToolSpec | None:
    key = _SOURCE_INDEX.get(source)
    return BUILTIN_TOOLS.get(key) if key else None


def get_builtin_by_key(key: str) -> BuiltinToolSpec | None:
    return BUILTIN_TOOLS.get(key)
