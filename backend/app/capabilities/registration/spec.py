"""能力声明。

注册层的一条声明 = 一个能力在平台里的「契约 + 策略」：输入输出 schema、风险等级、
权限要求、超时、重试策略、幂等标记。

清单里三类成员（tool / mcp / skill）共用这一个结构；skill 只用其中一部分字段。

字段与数据库的对应关系见 `app/capabilities/registration/registry.py`：
数据库列 ``parameters`` 对应这里的 ``input_schema``（声明层的叫法更准）。
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

# 成员种类：清单里的三类成员共用这一个结构，字段按 kind 取用
KIND_TOOL = "tool"  # 原子能力：最小可执行单元，能被模型直接调用
KIND_MCP = "mcp"  # 供给通道：一个外部 MCP server 提供的一组能力
KIND_SKILL = "skill"  # 能力组合：原子能力 + "怎么用它们"的知识

# tool 的执行方式（source_type）：决定用哪个适配器执行
SOURCE_BUILTIN = "builtin"  # 走内置适配器，从 locator 指向的工厂函数构造
SOURCE_HTTP = "http"  # 走 HTTP 适配器，method/url/headers/body 模板在 locator 里
SOURCE_MCP = "mcp"  # 走 MCP 适配器，通过 locator 里的 server + tool_name 调用

# 来源：出厂内置 or 用户自建
ORIGIN_BUILTIN = "builtin"  # 出厂源登记，启动时同步进数据库
ORIGIN_USER = "user"  # 用户/运营通过接口创建


@dataclass
class CapabilitySpec:
    """一条能力声明。

    这是**统一能力清单**里的运行时结构：注册层从数据库读出来，执行层、意图层、
    对外接口都只看这一份。它不是数据库表的镜像 —— 数据库里一个 mcp_server 记录
    会被聚合成若干条 spec，一份声明可以被多个入口引用。

    字段按 ``kind`` 分组使用：

    - tool：身份 + 契约 + 策略全部生效；
    - mcp：主要是身份 + 通道信息，契约由 MCP 侧提供；
    - skill：只用身份字段 + 成员/知识字段，**不执行**。
    """

    # ---- 身份：模型看到的、检索用的、清单里展示的 ----
    key: str  # 工具名，模型调用时用的名字，必须是合法标识符
    kind: str = KIND_TOOL  # tool / mcp / skill
    db_id: int = 0  # 数据库主键；出厂源声明还没有落库时为 0
    display_name: str = ""  # 给人看的名字
    description: str = ""  # **给模型看的**说明：检索和选型都靠它
    tags: list[str] = field(default_factory=list)  # 领域标签，检索命中标签权重更高
    permission: str = PermissionLevel.NONE  # 权限要求（治理策略）
    enabled: bool = True  # 停用的能力不进可用集合
    origin: str = ORIGIN_USER  # builtin / user
    version: int = 1  # 声明版本号

    # ---- 仅 tool：怎么执行、契约长什么样 ----
    source_type: str = SOURCE_BUILTIN  # builtin / http / mcp
    # JSON Schema；声明了 properties 就默认拒绝额外字段
    input_schema: dict[str, Any] = field(default_factory=dict)
    # JSON Schema；执行层据此校验返回值（校验时忽略多余字段）
    output_schema: dict[str, Any] | None = None
    # 执行入口：模块:工厂函数 / http 模板 / server + tool_name
    locator: dict[str, Any] = field(default_factory=dict)

    # ---- 治理策略 ----
    risk: str = RiskLevel.READ  # 这能力最坏能造成什么
    timeout_s: int = 30  # 单次调用上限（秒）；执行层还会套一层兜底
    retry: RetryPolicy = field(default_factory=RetryPolicy)  # 重试策略
    annotations: Annotations = field(default_factory=Annotations)  # 工具自述性质，词汇对齐 MCP

    # ---- 仅 skill ----
    members: list[str] = field(default_factory=list)  # 关联的原子能力 key
    knowledge: str = ""  # **核心**：核心做法，挂载时追加到 system prompt
    checklist: list[str] = field(default_factory=list)  # 检查项
    examples: list[str] = field(default_factory=list)  # 正反例
    constraints: str = ""  # 约束与禁区

    @property
    def is_executable(self) -> bool:
        """是否是可直接调用的原子能力。

        skill 本身不执行 —— 它只做检索、挂载、注入提示词；mcp 是通道，
        通道上的工具才是能力。
        """
        return self.kind == KIND_TOOL

    def summary(self) -> str:
        """一句话摘要，给检索排序和前端展示用。

        优先用 ``description``（那是给模型看的、也是检索用的），其次显示名，
        最后才退到 key —— 宁可给一句说明，也不要给一个光秃秃的标识符。
        """
        return self.description or self.display_name or self.key


__all__ = [
    "KIND_MCP",
    "KIND_SKILL",
    "KIND_TOOL",
    "ORIGIN_BUILTIN",
    "ORIGIN_USER",
    "SOURCE_BUILTIN",
    "SOURCE_HTTP",
    "SOURCE_MCP",
    "Annotations",
    "CapabilitySpec",
    "PermissionLevel",
    "RetryPolicy",
    "RiskLevel",
]
