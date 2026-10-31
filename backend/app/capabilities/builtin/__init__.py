"""内置能力的出厂源。

**这是一个白名单，不是任意入口点。** builtin 类型的工具只允许引用这里登记过的
`模块:工厂函数` —— 否则任何能调工具管理接口的人都能让平台 import 并执行任意
Python 代码（等价于远程代码执行）。

这里登记的规格会在启动时**同步进数据库**（见 `app.capabilities.registry`），
之后数据库就是唯一事实来源：运营可以改描述、调参数、单独禁用某个内置能力，
而代码里的这份清单降级为"出厂源"。

约定：每个工厂函数签名为 `factory(context: ExecutionContext) -> Callable`，
`context` 由运行时注入，不会出现在给模型的 tool schema 里。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BuiltinSpec:
    """内置能力的出厂规格。"""

    key: str
    source: str  # "模块:工厂函数"
    display_name: str
    description: str
    tags: tuple[str, ...] = ()
    permission: str = "low"
    parameters: dict = field(default_factory=dict)


BUILTIN_SPECS: dict[str, BuiltinSpec] = {
    spec.key: spec
    for spec in (
        BuiltinSpec(
            key="read_file",
            source="app.capabilities.builtin.fs:make_read_file",
            display_name="读取文件",
            description="读取工作区内的文本文件内容。path 是相对于工作区根目录的路径。",
            tags=("文件", "只读"),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对工作区根目录的路径"}},
                "required": ["path"],
            },
        ),
        BuiltinSpec(
            key="write_file",
            source="app.capabilities.builtin.fs:make_write_file",
            display_name="写入文件",
            description="把内容写入工作区内的文件（覆盖写）。path 是相对于工作区根目录的路径。",
            tags=("文件", "写入"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根目录的路径"},
                    "content": {"type": "string", "description": "要写入的文本内容"},
                },
                "required": ["path", "content"],
            },
        ),
        BuiltinSpec(
            key="list_files",
            source="app.capabilities.builtin.fs:make_list_files",
            display_name="列出文件",
            description="列出工作区内某个目录下的文件和子目录。path 默认为工作区根目录。",
            tags=("文件", "只读"),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对工作区根目录的目录"}},
                "required": [],
            },
        ),
        BuiltinSpec(
            key="run_python",
            source="app.capabilities.builtin.code:make_run_python",
            display_name="执行 Python",
            description="在工作区的沙箱里执行一段 Python 代码，返回退出码、标准输出和标准错误。",
            tags=("代码", "执行"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}},
                "required": ["code"],
            },
        ),
    )
}

# source → key 的反查，用于校验用户传入的 source 是否在白名单里
_SOURCE_INDEX: dict[str, str] = {spec.source: key for key, spec in BUILTIN_SPECS.items()}


def list_builtin_specs() -> list[BuiltinSpec]:
    return list(BUILTIN_SPECS.values())


def get_builtin_by_source(source: str) -> BuiltinSpec | None:
    key = _SOURCE_INDEX.get(source)
    return BUILTIN_SPECS.get(key) if key else None


def get_builtin_by_key(key: str) -> BuiltinSpec | None:
    return BUILTIN_SPECS.get(key)
