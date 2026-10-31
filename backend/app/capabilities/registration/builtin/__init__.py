"""内置能力的出厂源。

**这是一个白名单，不是任意入口点。** builtin 类型的工具只允许引用这里登记过的
`模块:工厂函数` —— 否则任何能调工具管理接口的人都能让平台 import 并执行任意
Python 代码（等价于远程代码执行）。

这里登记的规格会在启动时**同步进数据库**（见 `app.capabilities.registration.registry`），
之后数据库就是唯一事实来源：运营可以改描述、调参数、单独禁用某个内置能力，
而代码里的这份清单降级为"出厂源"。

约定：每个工厂函数签名为 `factory(context: ExecutionContext) -> Callable`，
`context` 由运行时注入，不会出现在给模型的 tool schema 里。

工厂函数返回**结构化结果**（dict），其中 `content` 字段是给模型看的文本，
其余字段是给程序看的。执行层按 `output_schema` 校验，并原样保留 `content`，
所以模型看到的输出和以前一样。

工具形态参考 Claude Code 的内置工具：文件读写改查移拷、执行命令、联网抓取与搜索。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.capabilities.registration.policy import (
    Annotations,
    PermissionLevel,
    RetryPolicy,
    RiskLevel,
)


@dataclass(frozen=True)
class BuiltinSpec:
    """内置能力的出厂规格。"""

    key: str
    source: str  # "模块:工厂函数"
    display_name: str
    description: str
    tags: tuple[str, ...] = ()
    permission: str = PermissionLevel.NONE
    risk: str = RiskLevel.READ
    timeout_s: int = 30
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    annotations: Annotations = field(default_factory=Annotations)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None


_PATH = {"type": "string", "description": "相对当前目录的路径"}
_SOURCE = {"type": "string", "description": "源路径，相对当前目录"}
_DEST = {"type": "string", "description": "目标路径，相对当前目录"}


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_READ_ONLY = Annotations(read_only=True, idempotent=True)
_WRITE = Annotations(idempotent=True)
_MUTATING = Annotations()
_EXEC = Annotations(destructive=True, open_world=True)
# 联网类是只读 GET：同样的请求重发一次结果不变，可以安全重试
_OPEN = Annotations(read_only=True, idempotent=True, open_world=True)

# 重试策略：只对"外部因素导致、且重试安全"的错误重试
_RETRY_TIMEOUT = RetryPolicy(max_attempts=2, backoff_s=0.5, retry_on=("TIMEOUT",))
_RETRY_NETWORK = RetryPolicy(max_attempts=3, backoff_s=1.0, retry_on=("NETWORK_ERROR",))


BUILTIN_SPECS: dict[str, BuiltinSpec] = {
    spec.key: spec
    for spec in (
        # ---------- 文件 ----------
        BuiltinSpec(
            key="read_file",
            source="app.capabilities.registration.builtin.fs:make_read_file",
            display_name="读取文件",
            description="读取工作区内的文本文件内容，带行号。path 是相对于当前目录的路径。",
            tags=("文件", "只读"),
            risk=RiskLevel.READ,
            timeout_s=10,
            annotations=_READ_ONLY,
            input_schema=_obj(
                {
                    "path": _PATH,
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "起始行号（从 0 开始）",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "读取多少行，0 表示到文件末尾",
                    },
                },
                ["path"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "line_count": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                },
                ["content", "line_count"],
            ),
        ),
        BuiltinSpec(
            key="write_file",
            source="app.capabilities.registration.builtin.fs:make_write_file",
            display_name="写入文件",
            description="把内容写入工作区内的文件（覆盖写）。path 是相对于当前目录的路径。",
            tags=("文件", "写入"),
            permission=PermissionLevel.NONE,
            risk=RiskLevel.WRITE,
            timeout_s=10,
            annotations=_WRITE,
            input_schema=_obj(
                {
                    "path": _PATH,
                    "content": {"type": "string", "description": "要写入的文本内容"},
                },
                ["path", "content"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "path": {"type": "string"},
                    "bytes": {"type": "integer"},
                    "created": {"type": "boolean"},
                },
                ["content", "path", "bytes"],
            ),
        ),
        BuiltinSpec(
            key="edit_file",
            source="app.capabilities.registration.builtin.fs:make_edit_file",
            display_name="编辑文件",
            description=(
                "把文件里的 old_string 精确替换成 new_string。"
                "old_string 必须唯一，否则会报错并列出所有匹配位置。"
            ),
            tags=("文件", "编辑"),
            risk=RiskLevel.WRITE,
            timeout_s=10,
            annotations=_WRITE,
            input_schema=_obj(
                {
                    "path": _PATH,
                    "old_string": {"type": "string", "description": "要被替换的原文"},
                    "new_string": {"type": "string", "description": "替换后的新内容"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "是否替换全部匹配（默认只替换一处）",
                    },
                },
                ["path", "old_string", "new_string"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "path": {"type": "string"},
                    "replaced_count": {"type": "integer"},
                },
                ["content", "path"],
            ),
        ),
        BuiltinSpec(
            key="move_file",
            source="app.capabilities.registration.builtin.fs:make_move_file",
            display_name="移动文件",
            description=(
                "移动或重命名文件/目录。source 与 destination 都相对于当前目录；"
                "destination 是已存在的目录时移动到该目录下，否则就是目标路径。"
            ),
            tags=("文件", "移动"),
            risk=RiskLevel.WRITE,
            timeout_s=10,
            # 不幂等：第一次成功后源已不存在，重试必然失败
            annotations=_MUTATING,
            input_schema=_obj(
                {"source": _SOURCE, "destination": _DEST},
                ["source", "destination"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                ["content", "source", "destination"],
            ),
        ),
        BuiltinSpec(
            key="copy_file",
            source="app.capabilities.registration.builtin.fs:make_copy_file",
            display_name="复制文件",
            description=(
                "复制文件或目录。source 与 destination 都相对于当前目录；"
                "destination 是已存在的目录时复制到该目录下，否则就是目标路径。"
            ),
            tags=("文件", "复制"),
            risk=RiskLevel.WRITE,
            timeout_s=10,
            annotations=_WRITE,
            input_schema=_obj(
                {"source": _SOURCE, "destination": _DEST},
                ["source", "destination"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                ["content", "source", "destination"],
            ),
        ),
        BuiltinSpec(
            key="make_dir",
            source="app.capabilities.registration.builtin.fs:make_make_dir",
            display_name="创建目录",
            description="创建目录（含多级父目录），目录已存在也算成功。path 相对于当前目录。",
            tags=("文件", "目录"),
            risk=RiskLevel.WRITE,
            timeout_s=10,
            annotations=_WRITE,
            input_schema=_obj({"path": _PATH}, ["path"]),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "path": {"type": "string"},
                    "created": {"type": "boolean"},
                },
                ["content", "path"],
            ),
        ),
        BuiltinSpec(
            key="change_directory",
            source="app.capabilities.registration.builtin.fs:make_change_directory",
            display_name="切换目录",
            description=(
                "切换当前工作目录，之后所有相对路径都相对于新目录解析。"
                'path 相对于当前目录："." 是当前目录，"~" 是工作区根目录，'
                '".." 往上一级。'
            ),
            tags=("文件", "目录"),
            risk=RiskLevel.READ,
            timeout_s=5,
            annotations=_READ_ONLY,
            input_schema=_obj({"path": _PATH}, ["path"]),
            output_schema=_obj(
                {"content": {"type": "string"}, "cwd": {"type": "string"}},
                ["content", "cwd"],
            ),
        ),
        BuiltinSpec(
            key="list_files",
            source="app.capabilities.registration.builtin.fs:make_list_files",
            display_name="列出文件",
            description="列出工作区内某个目录下的文件和子目录。path 默认为当前目录。",
            tags=("文件", "只读"),
            risk=RiskLevel.READ,
            timeout_s=10,
            annotations=_READ_ONLY,
            input_schema=_obj({"path": _PATH}),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "is_dir": {"type": "boolean"},
                                "size": {"type": "integer"},
                            },
                        },
                    },
                },
                ["content", "entries"],
            ),
        ),
        BuiltinSpec(
            key="glob",
            source="app.capabilities.registration.builtin.fs:make_glob",
            display_name="按模式找文件",
            description="按 glob 模式查找文件，返回相对工作区根目录的路径列表。例如 **/*.py。",
            tags=("文件", "查找"),
            risk=RiskLevel.READ,
            timeout_s=15,
            annotations=_READ_ONLY,
            input_schema=_obj(
                {
                    "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
                    "path": {"type": "string", "description": "在哪个目录下找，默认当前目录"},
                },
                ["pattern"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "matches": {"type": "array", "items": {"type": "string"}},
                },
                ["content", "matches"],
            ),
        ),
        BuiltinSpec(
            key="grep",
            source="app.capabilities.registration.builtin.fs:make_grep",
            display_name="搜索文件内容",
            description="在工作区里按正则搜索文件内容，返回 文件:行号:内容 的形式。",
            tags=("文件", "搜索"),
            risk=RiskLevel.READ,
            timeout_s=20,
            annotations=_READ_ONLY,
            input_schema=_obj(
                {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "在哪个目录下搜，默认当前目录"},
                    "glob_pattern": {
                        "type": "string",
                        "description": "限定搜索哪些文件，例如 *.py",
                    },
                },
                ["pattern"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                                "text": {"type": "string"},
                            },
                        },
                    },
                },
                ["content", "matches"],
            ),
        ),
        # ---------- 执行 ----------
        BuiltinSpec(
            key="run_python",
            source="app.capabilities.registration.builtin.sandbox:make_run_python",
            display_name="执行 Python",
            description="在工作区的沙箱里执行一段 Python 代码，返回退出码、标准输出和标准错误。",
            tags=("代码", "执行"),
            permission=PermissionLevel.CONFIRM,
            risk=RiskLevel.EXECUTE,
            timeout_s=60,
            retry=_RETRY_TIMEOUT,
            annotations=_EXEC,
            input_schema=_obj(
                {"code": {"type": "string", "description": "要执行的 Python 代码"}},
                ["code"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "duration_ms": {"type": "integer"},
                },
                ["content", "stdout", "stderr", "exit_code"],
            ),
        ),
        BuiltinSpec(
            key="run_bash",
            source="app.capabilities.registration.builtin.sandbox:make_run_bash",
            display_name="执行命令",
            description="在工作区的沙箱里执行一条 shell 命令，返回退出码、标准输出和标准错误。",
            tags=("命令", "执行"),
            permission=PermissionLevel.CONFIRM,
            risk=RiskLevel.EXECUTE,
            timeout_s=60,
            retry=_RETRY_TIMEOUT,
            annotations=_EXEC,
            input_schema=_obj(
                {"command": {"type": "string", "description": "要执行的 shell 命令"}},
                ["command"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "duration_ms": {"type": "integer"},
                },
                ["content", "stdout", "stderr", "exit_code"],
            ),
        ),
        # ---------- 网络 ----------
        BuiltinSpec(
            key="web_fetch",
            source="app.capabilities.registration.builtin.web:make_web_fetch",
            display_name="抓取网页",
            description="抓取一个 http/https 链接的内容并转成纯文本返回。",
            tags=("网络", "抓取"),
            permission=PermissionLevel.CONFIRM,
            risk=RiskLevel.NETWORK,
            timeout_s=20,
            retry=_RETRY_NETWORK,
            annotations=_OPEN,
            input_schema=_obj(
                {"url": {"type": "string", "description": "要抓取的链接"}},
                ["url"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "url": {"type": "string"},
                    "status": {"type": "integer"},
                    "bytes": {"type": "integer"},
                },
                ["content", "url", "status"],
            ),
        ),
        BuiltinSpec(
            key="web_search",
            source="app.capabilities.registration.builtin.web:make_web_search",
            display_name="联网搜索",
            description="在互联网上搜索，返回标题、链接与摘要。需要配置搜索服务商的 API Key。",
            tags=("网络", "搜索"),
            permission=PermissionLevel.CONFIRM,
            risk=RiskLevel.NETWORK,
            timeout_s=20,
            retry=_RETRY_NETWORK,
            annotations=_OPEN,
            input_schema=_obj(
                {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "返回多少条，默认 5",
                    },
                },
                ["query"],
            ),
            output_schema=_obj(
                {
                    "content": {"type": "string"},
                    "query": {"type": "string"},
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "snippet": {"type": "string"},
                            },
                        },
                    },
                },
                ["content", "query", "results"],
            ),
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
