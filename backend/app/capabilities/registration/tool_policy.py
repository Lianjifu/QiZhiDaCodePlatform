"""工具名 → 治理策略的映射表。

deepAgents 接管文件工具之后，那部分能力不再由我们声明（没有 ``CapabilitySpec``），
但**治理策略还得有地方放**：哪些工具要人工审批、风险多高、能不能重试。

这张表就是"能力声明"在新架构下的替代品 —— 从"每个能力一份声明"变成
"每个工具一行"。它是三样东西的单一来源：

- ``interrupt_on`` 集合（哪些工具要人批）；
- 风险基线（写进调用记录，参数级规则只能上调它）；
- 幂等标记（决定能不能自动重试）。

我们的工具（web_* / run_python / MCP）不在表里 —— 它们有自己的 ``CapabilitySpec``，
策略从声明来。两边合起来才是完整的工具集。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.capabilities.registration.policy import (
    PermissionLevel,
    RiskLevel,
    needs_approval,
    normalize_mode,
)

# 只读的工具 —— ``plan``（只读）模式下只有它们暴露给模型
READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset({"ls", "read_file", "glob", "grep"})

# deepAgents 的文件工具名（由 FilesystemMiddleware 注册）
FS_TOOL_NAMES: frozenset[str] = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)

# **已由 deepAgents 接管**的出厂能力：不再暴露成工具，免得同名冲突。
#
# - 文件类：deepAgents 的 fs 工具覆盖同样的操作，且是它们的原生实现；
# - ``run_bash``：deepAgents 的 ``execute`` 就是它（命令进同一个 docker 沙箱）。
#
# 用户自建的同名能力不受影响 —— 只有出厂内置的这批被接管。
SUPERSEDED_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "list_files",
        "move_file",
        "copy_file",
        "make_dir",
        "change_directory",
        "run_bash",
    }
)


@dataclass(frozen=True)
class ToolPolicy:
    """一个工具的治理策略。"""

    risk: str = RiskLevel.READ  # 风险基线（审批按它归的档位判）
    permission: str = PermissionLevel.NONE  # 权限要求
    idempotent: bool = True  # 重复执行结果相同（重试的前提）


# 文件工具的策略：审批按**风险档位**判（低=只读/写入、中=执行命令、高=破坏性）。
#
# 出厂文件的 permission 一律 none —— 档位说了算，声明不再重复表达一遍。
TOOL_POLICIES: dict[str, ToolPolicy] = {
    "ls": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    "read_file": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    "glob": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    "grep": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    # 写类：**低档**，自动放行（改错了能改回来）
    #
    # **不幂等** —— 同样的参数写第二次，结果不一样：
    # 第一次写进去的内容会被第二次覆盖，而 ``edit_file`` 的第二次调用
    # 会因为"文件已变"报错。声明成幂等的话，执行层会拿上一次的**旧结果**
    # 直接返回，把那个错误吞掉 —— agent 以为改成功了，其实文件没动。
    "write_file": ToolPolicy(
        risk=RiskLevel.WRITE,
        permission=PermissionLevel.NONE,
        idempotent=False,
    ),
    "edit_file": ToolPolicy(
        risk=RiskLevel.WRITE,
        permission=PermissionLevel.NONE,
        idempotent=False,
    ),
    # 删除：**高档**，永远要人批，不管信任模式。
    # 写文件能靠再写一遍补救，删掉的回不来 —— 不可重试（删了就是删了）。
    "delete": ToolPolicy(
        risk=RiskLevel.DESTRUCTIVE,
        permission=PermissionLevel.NONE,
        idempotent=False,
    ),
    # 执行命令：**中档**，strict / balanced 要人批，permissive 放行。
    # 不幂等（超时只说明"不知道执行完没有"，重跑可能把副作用做两遍）
    "execute": ToolPolicy(
        risk=RiskLevel.EXECUTE,
        permission=PermissionLevel.NONE,
        idempotent=False,
    ),
}


def policy_for_tool(name: str) -> ToolPolicy:
    """取某个工具的策略。

    Args:
        name: 工具名。

    Returns:
        工具策略；不在表里就返回最宽松的默认值（只读、自动放行、幂等）。
    """
    return TOOL_POLICIES.get(name, ToolPolicy())


def approval_tool_names(trust_mode: str) -> set[str]:
    """deepAgents 的文件工具里，哪些要人工审批。

    Args:
        trust_mode: 会话信任模式（strict / balanced / permissive）。

    Returns:
        工具名集合。

    审批按**风险档位**判：低档（只读、写入）自动放行，中档（执行命令）与
    高档（删除）要人批。信任模式只能**放宽中档**，放宽不了高档 ——
    删除是不可逆的，permissive 也不放过。
    """
    mode = normalize_mode(trust_mode)
    return {
        name
        for name, policy in TOOL_POLICIES.items()
        if needs_approval(policy.permission, policy.risk, mode)
    }


def approval_required_tools(
    specs: list[Any],
    trust_mode: str,
    allowed_keys: set[str] | None = None,
) -> set[str]:
    """本次会话里，哪些工具要人工审批 —— **审批规则的唯一出口**。

    文件工具按策略表算，我们自己的工具按声明算，但两边用**同一个判定函数**
    （``policy.needs_approval``）。

    三个地方必须用这一份，否则迟早对不上：

    - ``builder.py`` 算出 deepAgents 的 ``interrupt_on``（谁要挂起等人批）；
    - ``middleware.py`` 告诉模型"哪些工具会要人批"；
    - 执行层在直接调用的路径上决定要不要挂起。

    审批按**风险档位**判：低档（只读、写入）自动放行，中档（执行命令、联网）
    与高档（破坏性）要人批 —— 声明里的 ``permission=confirm`` 只是"运营额外
    要求人确认"，``admin`` 是"管理员放行"。

    Args:
        specs: 统一能力清单（我们的工具用它）。
        trust_mode: 会话信任模式。
        allowed_keys: 本次允许的能力集；``None`` 表示不限制。

    Returns:
        工具名集合。
    """
    mode = (trust_mode or "balanced").lower()
    names = set(approval_tool_names(mode))
    for spec in specs:
        if spec.kind != "tool" or not spec.enabled:
            continue
        if allowed_keys is not None and spec.key not in allowed_keys:
            continue
        if spec.key in names:
            continue
        if needs_approval(spec.permission, spec.risk, mode):
            names.add(spec.key)
    return names


__all__ = [
    "FS_TOOL_NAMES",
    "READ_ONLY_TOOL_NAMES",
    "SUPERSEDED_BUILTIN_TOOLS",
    "TOOL_POLICIES",
    "ToolPolicy",
    "approval_required_tools",
    "approval_tool_names",
    "policy_for_tool",
]
