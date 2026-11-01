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

from app.capabilities.registration.policy import PermissionLevel, RiskLevel

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

    risk: str = RiskLevel.READ  # 风险基线
    permission: str = PermissionLevel.NONE  # 权限要求
    idempotent: bool = True  # 重复执行结果相同（重试的前提）
    needs_approval: bool = False  # 要不要人工审批


# 文件工具的策略：只读的自动放行；写类按信任模式决定；删除永远要人批；执行命令要人批
TOOL_POLICIES: dict[str, ToolPolicy] = {
    "ls": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    "read_file": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    "glob": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    "grep": ToolPolicy(risk=RiskLevel.READ, permission=PermissionLevel.NONE, idempotent=True),
    # 写类在 balanced 下自动放行（读写类照旧），strict 下要人批
    "write_file": ToolPolicy(
        risk=RiskLevel.WRITE,
        permission=PermissionLevel.CONFIRM,
        idempotent=True,
    ),
    "edit_file": ToolPolicy(
        risk=RiskLevel.WRITE,
        permission=PermissionLevel.CONFIRM,
        idempotent=True,
    ),
    # 删除：**永远**要人批，不管信任模式。
    # 写文件能靠再写一遍补救，删掉的回不来 —— 不可重试（删了就是删了）。
    "delete": ToolPolicy(
        risk=RiskLevel.WRITE,
        permission=PermissionLevel.CONFIRM,
        idempotent=False,
        needs_approval=True,
    ),
    # 执行命令：不幂等（超时只说明"不知道执行完没有"，重跑可能把副作用做两遍）
    "execute": ToolPolicy(
        risk=RiskLevel.EXECUTE,
        permission=PermissionLevel.CONFIRM,
        idempotent=False,
    ),
}

# 信任模式 → 哪些风险等级以上的工具要人工审批
_TRUST_APPROVAL_RISKS: dict[str, tuple[str, ...]] = {
    # 写类 + 执行类 + 网络类都要批
    "strict": (RiskLevel.WRITE, RiskLevel.EXECUTE, RiskLevel.NETWORK, RiskLevel.DESTRUCTIVE),
    # 执行类 + 网络类要批（读写类自动放行，与改造前一致）
    "balanced": (RiskLevel.EXECUTE, RiskLevel.NETWORK, RiskLevel.DESTRUCTIVE),
    # 只有破坏性要批
    "permissive": (RiskLevel.DESTRUCTIVE,),
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
    """按信任模式算出"哪些工具要人工审批"。

    Args:
        trust_mode: 会话信任模式（strict / balanced / permissive）。

    Returns:
        工具名集合。

    信任模式只能**放宽**审批：permissive 也不会放过 ``destructive``。
    但标了 ``needs_approval`` 的工具（删除）不在放宽范围内 —— 信任模式决定的是
    "按风险多严来卡"，不是"这一类操作不用问人"。
    """
    mode = (trust_mode or "balanced").lower()
    risks = _TRUST_APPROVAL_RISKS.get(mode, _TRUST_APPROVAL_RISKS["balanced"])
    return {
        name
        for name, policy in TOOL_POLICIES.items()
        if policy.needs_approval or policy.risk in risks
    }


__all__ = [
    "FS_TOOL_NAMES",
    "SUPERSEDED_BUILTIN_TOOLS",
    "TOOL_POLICIES",
    "ToolPolicy",
    "approval_tool_names",
    "policy_for_tool",
]
