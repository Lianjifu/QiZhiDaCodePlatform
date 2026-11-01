"""内置能力的出厂源。

**这是一个白名单，不是任意入口点。** builtin 类型的工具只允许引用这里登记过的
`模块:工厂函数` —— 否则任何能调工具管理接口的人都能让平台 import 并执行任意
Python 代码（等价于远程代码执行）。

这里登记的规格会在启动时**同步进数据库**（见 `app/capabilities/registration.registry`），
之后数据库就是唯一事实来源：运营可以改描述、调参数、单独禁用某个内置能力，
而代码里的这份清单降级为"出厂源"。

约定：每个工厂函数签名为 `factory(context: ExecutionContext) -> Callable`，
`context` 由运行时注入，不会出现在给模型的 tool schema 里。

工厂函数返回**结构化结果**（dict），其中 `content` 字段是给模型看的文本，
其余字段是给程序看的。执行层按 `output_schema` 校验，并原样保留 `content`，
所以模型看到的输出和以前一样。

**文件与命令类工具不在这里** —— 它们由 deepAgents 的文件系统中间件提供
（``ls`` / ``read_file`` / ``write_file`` / ``edit_file`` / ``delete`` / ``glob`` /
``grep`` / ``execute``），backend 指向我们的工作区与 docker 沙箱。
这里只留 deepAgents 没有等价物的那几个。
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
    """内置能力的出厂规格。

    冻结的（frozen）—— 出厂规格是常量，不该被谁在运行时改掉。
    运营想改某个内置能力的描述或风险等级，改数据库去；启动同步时只**补空字段**，
    不会用这份常量去覆盖。

    字段含义见 ``app/capabilities/registration/spec.py`` 的 ``CapabilitySpec``
    （两者字段一一对应，只是这个不落库）。
    """

    key: str  # 工具名，模型调用时用的名字
    source: str  # "模块:工厂函数" —— 必须登记在下面的白名单里
    display_name: str  # 给人看的名字
    description: str  # 给模型看的说明：检索和选型都靠它
    tags: tuple[str, ...] = ()  # 领域标签，检索命中标签权重更高
    permission: str = PermissionLevel.NONE  # 权限要求
    risk: str = RiskLevel.READ  # 风险等级
    timeout_s: int = 30  # 单次调用上限（秒）
    retry: RetryPolicy = field(default_factory=RetryPolicy)  # 重试策略
    annotations: Annotations = field(default_factory=Annotations)  # 工具自述性质
    input_schema: dict[str, Any] = field(default_factory=dict)  # 入参契约
    output_schema: dict[str, Any] | None = None  # 出参契约


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """构造一个对象类型的 JSON Schema。

    固定 ``additionalProperties: False`` —— 声明了什么就只收什么，
    模型多传一个参数不该让能力实现直接 TypeError。
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


# 执行类：可能破坏，也可能访问外部世界
_EXEC = Annotations(destructive=True, open_world=True)
# 联网类是只读 GET：同样的请求重发一次结果不变，可以安全重试
_OPEN = Annotations(read_only=True, idempotent=True, open_world=True)

# 重试策略：只对"外部因素导致、且重试安全"的错误重试
_RETRY_TIMEOUT = RetryPolicy(max_attempts=2, backoff_s=0.5, retry_on=("TIMEOUT",))
_RETRY_NETWORK = RetryPolicy(max_attempts=3, backoff_s=1.0, retry_on=("NETWORK_ERROR",))


BUILTIN_SPECS: dict[str, BuiltinSpec] = {
    spec.key: spec
    for spec in (
        # ---------- 执行 ----------
        # run_python 的约定：
        #
        # - 权限 confirm、风险 execute：``balanced`` 信任模式下每次都要人工确认；
        # - 一律进沙箱（docker 模式：断网、限资源、只挂载工作区）；
        # - 超时 60 秒，执行层还会再套一层兜底超时；
        # - 声明了超时重试，但**实际不会重试** —— 这个能力不幂等，
        #   超时只说明"不知道执行完没有"，重跑可能把副作用做两遍。
        #   执行层会把这类超时降级成不可重试，并在错误信息里写明
        #   "不要重复执行同样的命令"。
        BuiltinSpec(
            key="run_python",
            source="app.capabilities.registration.builtin.sandbox:make_run_python",
            display_name="执行 Python",
            description="在工作区的沙箱里执行一段 Python 代码，返回退出码和输出。",
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
                    "output": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "duration_ms": {"type": "integer"},
                },
                ["content", "exit_code"],
            ),
        ),
        # ---------- 网络 ----------
        # web_fetch / web_search 的共同约定：
        #
        # - 权限 confirm、风险 network：联网在 balanced 模式下要人工确认；
        # - 都是只读 GET，同一个请求重发结果不变 → 幂等，网络错误可安全重试；
        # - web_fetch 的 URL 会再过一遍参数级规则：指向内网地址就顶成
        #   destructive（SSRF 尝试），要管理员放行（见 execution/risk.py）。
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
    """列出全部出厂规格（启动时同步进数据库用）。"""
    return list(BUILTIN_SPECS.values())


def get_builtin_by_source(source: str) -> BuiltinSpec | None:
    """按 ``模块:工厂函数`` 反查出厂规格。

    这是白名单校验的入口：不在这里登记的 source 一律拒绝 ——
    允许任意 ``模块:函数`` 等于允许任何人让平台 import 并执行任意 Python 代码。
    """
    key = _SOURCE_INDEX.get(source)
    return BUILTIN_SPECS.get(key) if key else None


def get_builtin_by_key(key: str) -> BuiltinSpec | None:
    """按 key 取出厂规格（没有就返回 None）。"""
    return BUILTIN_SPECS.get(key)
