"""能力治理策略：风险等级、权限要求、重试策略、工具注解。

这些是**声明**，不是运行时判定：注册层把"这个能力有多危险、要不要人批"写清楚，
执行层按声明 + 参数级规则做最终判定。

风险与权限是**两个正交维度**：

- ``risk`` —— 这能力最坏能造成什么（读 / 写 / 执行 / 联网 / 破坏）；
- ``permission`` —— 谁来放行（自动 / 需确认 / 需管理员）。

早先把两者压成一个 ``permission: low|high``，结果"高风险但可自动放行"和
"低风险但要人批"都表达不出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RiskLevel(StrEnum):
    """风险等级：能力最坏能造成什么。

    这是**描述能力本身**的维度，与 ``PermissionLevel`` 正交。取值由低到高：
    读 → 写 → 执行命令 → 联网 → 破坏。
    """

    READ = "read"  # 只读，不改任何状态
    WRITE = "write"  # 改状态（写文件、建目录、改配置）
    EXECUTE = "execute"  # 执行任意代码或命令
    NETWORK = "network"  # 访问外部网络
    DESTRUCTIVE = "destructive"  # 不可逆的破坏性操作（删库、写裸设备、SSRF）


class PermissionLevel(StrEnum):
    """权限要求：谁能放行这次调用。

    这是**描述运行时策略**的维度 —— 同是 ``destructive`` 风险，
    可以自动放行也可以要管理员批；同是 ``read`` 风险，也可以每次都要人确认。
    """

    NONE = "none"  # 自动放行
    CONFIRM = "confirm"  # 需要人确认（信任模式可以放宽这一档）
    ADMIN = "admin"  # 需要管理员（信任模式**放宽不了**这一档）


# 风险由低到高。参数级规则只能把风险**上调**，不能下调。
_RISK_ORDER: dict[str, int] = {
    RiskLevel.READ: 0,
    RiskLevel.WRITE: 1,
    RiskLevel.EXECUTE: 2,
    RiskLevel.NETWORK: 3,
    RiskLevel.DESTRUCTIVE: 4,
}


def risk_rank(risk: str) -> int:
    """风险的排序值。未知值按最低处理。

    未知值归零是刻意的：脏数据（手填了一个不存在的等级）不该让能力被当成
    最高风险卡住 —— 宁可少拦一次，也不要让整条链路因为一个错字瘫掉。
    参数级规则仍然可以把它往上抬。
    """
    return _RISK_ORDER.get(str(risk), 0)


def max_risk(*risks: str) -> str:
    """取最高的风险等级。

    参数级规则**只能上调**风险：调用方拿到基线风险后，用它跟规则命中的风险
    取最大值，而不是覆盖。没有参数时返回最低的 ``read``。
    """
    if not risks:
        return RiskLevel.READ
    return max(risks, key=risk_rank)


# 审批决策
APPROVAL_APPROVED = "approved"
APPROVAL_PENDING = "pending"


def approval_decision(
    permission: str,
    risk: str,
    trust_mode: str,
    *,
    require_admin: bool = False,
) -> str:
    """这次调用要不要人批。返回 ``approved`` / ``pending``。

    **单一事实来源**：执行层拿它做审批判定，意图层拿它提前告诉模型
    "哪些能力会要人批"。两处各写一套规则，迟早会对不上 ——
    模型被告知"直接放行"，实际却被拦下，就是这么来的。

    Args:
        permission: 能力声明的权限要求。
        risk: 本次调用的风险等级（可以是参数级规则上调后的值）。
        trust_mode: 会话信任模式（strict / balanced / permissive）。
        require_admin: 参数级规则是否要求管理员放行。

    Returns:
        ``approved``（直接放行）或 ``pending``（挂起等人工审批）。

    判定顺序是有意的：先看声明的硬性要求（``admin`` 谁也放宽不了），
    再看风险，最后才让信任模式插手 —— 信任模式只能**放宽** ``confirm``，
    不能放宽 ``admin``，也不能放过 ``destructive``。
    """
    permission = permission or PermissionLevel.NONE
    if permission == PermissionLevel.ADMIN:
        return APPROVAL_PENDING
    if permission != PermissionLevel.CONFIRM:
        return APPROVAL_APPROVED
    # 风险被顶到破坏性 —— 一律要人批
    if require_admin or risk == RiskLevel.DESTRUCTIVE:
        return APPROVAL_PENDING

    mode = (trust_mode or "balanced").lower()
    if mode == "strict":
        return APPROVAL_PENDING
    if mode == "permissive":
        return APPROVAL_APPROVED
    # balanced：read/write 自动放行；执行命令 / 联网抓取要确认
    if risk in (RiskLevel.EXECUTE, RiskLevel.NETWORK):
        return APPROVAL_PENDING
    return APPROVAL_APPROVED


@dataclass(frozen=True)
class RetryPolicy:
    """重试策略。

    声明的"可以重试"只是一半：真正是否重试，要同时满足三个条件：

    1. 运行时错误自己的 ``retryable`` 为真（声明允许 ≠ 运行时值得）；
    2. 错误码在 ``retry_on`` 白名单里；
    3. **能力是幂等的**（``require_idempotent`` 为真时）—— 不幂等的操作重试
       可能把副作用做两遍。``run_bash`` 超时就是典型：超时只说明"不知道执行完
       没有"，重跑可能重复执行。
    """

    max_attempts: int = 1  # 含首次调用；1 = 不重试
    backoff_s: float = 0.0  # 退避基数；指数退避时按 2^(n-1) 增长
    retry_on: tuple[str, ...] = ()  # ErrorCode 白名单：只有这些错误码才值得重试
    exponential: bool = True  # 是否指数退避
    max_backoff_s: float = 30.0  # 单次退避上限，防止指数增长把线程卡死
    require_idempotent: bool = True  # 只对幂等能力自动重试

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetryPolicy:
        """从数据库/接口的原始字典构造策略。

        容错规则：缺字段用默认值，脏值尽量兜住而不是抛错 —— 声明读不出来
        不该让整个能力清单加载失败。``max_attempts`` 至少是 1（一次都不试
        等于这个能力永远调不通）。

        Args:
            data: 原始字典，可能为空、可能字段缺失。

        Returns:
            构造好的 ``RetryPolicy``。
        """
        data = data or {}
        # retry_on 允许写成单个字符串，也写成数组 —— 两种都要能读
        raw_on = data.get("retry_on") or ()
        if isinstance(raw_on, str):
            raw_on = (raw_on,)
        return cls(
            max_attempts=max(1, int(data.get("max_attempts") or 1)),
            backoff_s=float(data.get("backoff_s") or 0.0),
            retry_on=tuple(str(item) for item in raw_on),
            exponential=bool(data.get("exponential", True)),
            max_backoff_s=float(data.get("max_backoff_s") or 30.0),
            require_idempotent=bool(data.get("require_idempotent", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        """转回字典（写库 / 出接口用）。"""
        return {
            "max_attempts": self.max_attempts,
            "backoff_s": self.backoff_s,
            "retry_on": list(self.retry_on),
            "exponential": self.exponential,
            "max_backoff_s": self.max_backoff_s,
            "require_idempotent": self.require_idempotent,
        }

    def backoff_for(self, attempt: int) -> float:
        """第 attempt 次失败后该等多久。

        ``attempt`` 从 1 开始（第一次失败后等一次）。指数退避：base * 2^(n-1)，
        并封顶在 ``max_backoff_s``。
        """
        if self.backoff_s <= 0:
            return 0.0
        if not self.exponential:
            return min(self.backoff_s, self.max_backoff_s)
        delay = self.backoff_s * (2 ** max(0, attempt - 1))
        return min(delay, self.max_backoff_s)


@dataclass(frozen=True)
class Annotations:
    """工具自述的性质，词汇对齐 MCP tool annotations。

    与 risk 的关系：annotations 描述"工具本来是什么样"，risk 描述"平台判定这次
    调用有多危险"。注册时按 annotations 推导 risk 的**下界**，平台可以上调，
    不允许下调。
    """

    read_only: bool = False  # 只读不改状态
    destructive: bool = False  # 可能造成破坏性改动
    idempotent: bool = False  # 重复执行结果相同（重试的前提）
    open_world: bool = False  # 会访问平台控制不了的外部系统

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Annotations:
        """从数据库/接口的原始字典构造注解。

        Args:
            data: 原始字典，可能为空或字段缺失。

        Returns:
            构造好的 ``Annotations``。
        """
        data = data or {}
        return cls(
            read_only=bool(data.get("read_only", False)),
            destructive=bool(data.get("destructive", False)),
            idempotent=bool(data.get("idempotent", False)),
            open_world=bool(data.get("open_world", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        """转回字典（写库 / 出接口用）。"""
        return {
            "read_only": self.read_only,
            "destructive": self.destructive,
            "idempotent": self.idempotent,
            "open_world": self.open_world,
        }

    @classmethod
    def from_mcp(cls, annotations: Any) -> Annotations:
        """从 MCP server 侧的 tool annotations 继承，不用人工填。

        MCP 的提示字段名是驼峰（``readOnlyHint`` 等），和平台内部的蛇形命名
        不同，所以逐个映射。拿不到提示时返回全 False 的空注解 ——
        宁可是"没信息"，也不要瞎猜一个。

        Args:
            annotations: MCP 侧的 annotations 对象，可能为 ``None``。

        Returns:
            映射后的 ``Annotations``。
        """
        if annotations is None:
            return cls()
        return cls(
            read_only=bool(getattr(annotations, "readOnlyHint", False)),
            destructive=bool(getattr(annotations, "destructiveHint", False)),
            idempotent=bool(getattr(annotations, "idempotentHint", False)),
            open_world=bool(getattr(annotations, "openWorldHint", False)),
        )

    def derived_risk(self) -> str:
        """注解推出的风险参考值（仅供参考，不是判定依据）。

        注解描述的是"工具本来是什么样"，不是"这次调用有多危险" ——
        真要判定，看 ``risk`` 声明 + 参数级规则。

        推导口径：只读 → read；会访问外部世界 → network；否则按写处理。
        注意是"参考值"：执行层只在**声明明显低估**时才用它上调，
        不会拿它下调声明的风险。
        """
        if self.read_only:
            return RiskLevel.READ
        if self.open_world:
            return RiskLevel.NETWORK
        return RiskLevel.WRITE


__all__ = [
    "APPROVAL_APPROVED",
    "APPROVAL_PENDING",
    "Annotations",
    "PermissionLevel",
    "RetryPolicy",
    "RiskLevel",
    "approval_decision",
    "max_risk",
    "risk_rank",
]
