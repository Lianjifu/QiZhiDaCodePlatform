"""内置能力的出厂源。

**这是一个白名单，不是任意入口点。** builtin 类型的工具只允许引用这里登记过的
`模块:工厂函数` —— 否则任何能调工具管理接口的人都能让平台 import 并执行任意
Python 代码（等价于远程代码执行）。

这里登记的规格会在启动时**同步进数据库**（见 `app.capabilities.registry`），
之后数据库就是唯一事实来源：运营可以改描述、调参数、单独禁用某个内置能力，
而代码里的这份清单降级为"出厂源"。

约定：每个工厂函数签名为 `factory(context: ExecutionContext) -> Callable`，
`context` 由运行时注入，不会出现在给模型的 tool schema 里。

工具形态参考 Claude Code 的内置工具：文件读写改查、命令执行、联网抓取与搜索。
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


_PATH_PARAM = {"type": "string", "description": "相对当前目录的路径"}


BUILTIN_SPECS: dict[str, BuiltinSpec] = {
    spec.key: spec
    for spec in (
        # ---------- 文件 ----------
        BuiltinSpec(
            key="read_file",
            source="app.capabilities.builtin.fs:make_read_file",
            display_name="读取文件",
            description="读取工作区内的文本文件内容，带行号。path 是相对于当前目录的路径。",
            tags=("文件", "只读"),
            parameters={
                "type": "object",
                "properties": {
                    "path": _PATH_PARAM,
                    "offset": {"type": "integer", "description": "起始行号（从 0 开始）"},
                    "limit": {"type": "integer", "description": "读取多少行，0 表示到文件末尾"},
                },
                "required": ["path"],
            },
        ),
        BuiltinSpec(
            key="write_file",
            source="app.capabilities.builtin.fs:make_write_file",
            display_name="写入文件",
            description="把内容写入工作区内的文件（覆盖写）。path 是相对于当前目录的路径。",
            tags=("文件", "写入"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {
                    "path": _PATH_PARAM,
                    "content": {"type": "string", "description": "要写入的文本内容"},
                },
                "required": ["path", "content"],
            },
        ),
        BuiltinSpec(
            key="edit_file",
            source="app.capabilities.builtin.fs:make_edit_file",
            display_name="编辑文件",
            description=(
                "把文件里的 old_string 精确替换成 new_string。"
                "old_string 必须唯一，否则会报错并列出所有匹配位置。"
            ),
            tags=("文件", "编辑"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {
                    "path": _PATH_PARAM,
                    "old_string": {"type": "string", "description": "要被替换的原文"},
                    "new_string": {"type": "string", "description": "替换后的新内容"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "是否替换全部匹配（默认只替换一处）",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        ),
        BuiltinSpec(
            key="list_files",
            source="app.capabilities.builtin.fs:make_list_files",
            display_name="列出文件",
            description="列出工作区内某个目录下的文件和子目录。path 默认为当前目录。",
            tags=("文件", "只读"),
            parameters={
                "type": "object",
                "properties": {"path": _PATH_PARAM},
                "required": [],
            },
        ),
        BuiltinSpec(
            key="move_file",
            source="app.capabilities.builtin.fs:make_move_file",
            display_name="移动文件",
            description=(
                "移动或重命名文件/目录。source 与 destination 都相对于当前目录；"
                "destination 是已存在的目录时移动到该目录下，否则就是目标路径。"
            ),
            tags=("文件", "移动"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "源路径"},
                    "destination": {"type": "string", "description": "目标路径"},
                },
                "required": ["source", "destination"],
            },
        ),
        BuiltinSpec(
            key="copy_file",
            source="app.capabilities.builtin.fs:make_copy_file",
            display_name="复制文件",
            description=(
                "复制文件或目录。source 与 destination 都相对于当前目录；"
                "destination 是已存在的目录时复制到该目录下，否则就是目标路径。"
            ),
            tags=("文件", "复制"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "源路径"},
                    "destination": {"type": "string", "description": "目标路径"},
                },
                "required": ["source", "destination"],
            },
        ),
        BuiltinSpec(
            key="make_dir",
            source="app.capabilities.builtin.fs:make_make_dir",
            display_name="创建目录",
            description="创建目录（含多级父目录），目录已存在也算成功。path 相对于当前目录。",
            tags=("文件", "目录"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {"path": _PATH_PARAM},
                "required": ["path"],
            },
        ),
        BuiltinSpec(
            key="change_directory",
            source="app.capabilities.builtin.fs:make_change_directory",
            display_name="切换目录",
            description=(
                "切换当前工作目录，之后所有相对路径都相对于新目录解析。"
                'path 相对于当前目录："." 是当前目录，"~" 是工作区根目录，'
                '".." 往上一级。'
            ),
            tags=("文件", "目录"),
            parameters={
                "type": "object",
                "properties": {"path": _PATH_PARAM},
                "required": ["path"],
            },
        ),
        BuiltinSpec(
            key="glob",
            source="app.capabilities.builtin.fs:make_glob",
            display_name="按模式找文件",
            description="按 glob 模式查找文件，返回相对工作区根目录的路径列表。例如 **/*.py。",
            tags=("文件", "查找"),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
                    "path": {"type": "string", "description": "在哪个目录下找，默认工作区根目录"},
                },
                "required": ["pattern"],
            },
        ),
        BuiltinSpec(
            key="grep",
            source="app.capabilities.builtin.fs:make_grep",
            display_name="搜索文件内容",
            description="在工作区里按正则搜索文件内容，返回 文件:行号:内容 的形式。",
            tags=("文件", "搜索"),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "在哪个目录下搜，默认工作区根目录"},
                    "glob_pattern": {
                        "type": "string",
                        "description": "限定搜索哪些文件，例如 *.py",
                    },
                },
                "required": ["pattern"],
            },
        ),
        # ---------- 执行 ----------
        BuiltinSpec(
            key="run_python",
            source="app.capabilities.builtin.sandbox:make_run_python",
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
        BuiltinSpec(
            key="run_bash",
            source="app.capabilities.builtin.sandbox:make_run_bash",
            display_name="执行命令",
            description="在工作区的沙箱里执行一条 shell 命令，返回退出码、标准输出和标准错误。",
            tags=("命令", "执行"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
                "required": ["command"],
            },
        ),
        # ---------- 网络 ----------
        BuiltinSpec(
            key="web_fetch",
            source="app.capabilities.builtin.web:make_web_fetch",
            display_name="抓取网页",
            description="抓取一个 http/https 链接的内容并转成纯文本返回。",
            tags=("网络", "抓取"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "要抓取的链接"}},
                "required": ["url"],
            },
        ),
        BuiltinSpec(
            key="web_search",
            source="app.capabilities.builtin.web:make_web_search",
            display_name="联网搜索",
            description="在互联网上搜索，返回标题、链接与摘要。需要配置搜索服务商的 API Key。",
            tags=("网络", "搜索"),
            permission="high",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回多少条，默认 5"},
                },
                "required": ["query"],
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
