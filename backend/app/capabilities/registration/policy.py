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
    WRITE = "write"  # 改状态（写文件、建目录、改配置），或把数据发到进程外
    EXECUTE = "execute"  # 执行任意代码或命令
    NETWORK = "network"  # 访问外部网络
    DESTRUCTIVE = "destructive"  # 不可逆的破坏性操作（删库、写裸设备、SSRF）


class RiskTier(StrEnum):
    """风险**档位**：低 / 中 / 高 —— 审批就是按档位判的。

    五个风险等级归成三档：

    - **低**：只读、写入 —— 改错了能改回来，不用打断人；
    - **中**：执行命令、联网 —— 会在进程外产生副作用，要人看一眼；
    - **高**：破坏性 —— 不可逆，永远要人批。

    分档的规矩：**只有中档和高档才需要人工审批**，低档自动放行。
    档位之外还有两个例外口子 —— ``permission=admin``（管理员放行）与
    声明为破坏性的不可逆操作，它们不受信任模式放宽。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# 风险等级 → 档位。
#
# 归并口径：只读与写入是低档（改得回来）；执行命令与联网是中档（会在进程外
# 产生副作用）；破坏性是高档（不可逆）。
_RISK_TIERS: dict[str, str] = {
    RiskLevel.READ: RiskTier.LOW,
    RiskLevel.WRITE: RiskTier.LOW,
    RiskLevel.EXECUTE: RiskTier.MEDIUM,
    RiskLevel.NETWORK: RiskTier.MEDIUM,
    RiskLevel.DESTRUCTIVE: RiskTier.HIGH,
}


def risk_tier(risk: str) -> str:
    """风险等级 → 档位（低 / 中 / 高）。

    未知等级按**低档**处理：脏数据不该让能力被当成高风险卡住 —— 宁可少拦
    一次，也不要让整条链路因为一个错字瘫掉。
    """
    return _RISK_TIERS.get(str(risk), RiskTier.LOW)


class PermissionLevel(StrEnum):
    """权限要求：谁能放行这次调用。

    这是**描述运行时策略**的维度 —— 同是 ``destructive`` 风险，
    可以自动放行也可以要管理员批；同是 ``read`` 风险，也可以每次都要人确认。
    """

    NONE = "none"  # 自动放行
    CONFIRM = "confirm"  # 需要人确认
    ADMIN = "admin"  # 需要管理员（只有"绕过权限"模式会放它）


class PermissionMode(StrEnum):
    """权限模式 —— 这一轮（或这个会话）怎么决定"要不要人批"。

    词汇对齐 Claude Code 的权限模式，语义也是同一套：

    - ``AUTO``（默认）—— **按风险档位**：低档自动放行，中档与高档要人批；
    - ``MANUAL`` —— 任何会改东西的操作都要人批（只读不问）；
    - ``ACCEPT_EDITS`` —— 文件编辑自动批准，执行命令 / 联网 / 破坏性照旧问；
    - ``PLAN`` —— 只读：写类与执行类工具**不暴露给模型**，只能读与规划；
    - ``BYPASS`` —— 全放行（含破坏性）。

    模式是**会话级**的（见 ``app/agents/permissions.py``），全局配置
    ``CAPABILITY_TRUST_MODE`` 只是默认值。
    """

    AUTO = "auto"
    MANUAL = "manual"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    BYPASS = "bypass"


# 老配置值 → 新模式。
#
# 早先只有三档信任模式（strict / balanced / permissive）。保留老名字能用的，
# 但语义按新模式走 —— 其中 ``permissive`` 现在等于 ``bypass``（全放行），
# 比从前宽：从前它还拦着破坏性操作。
_LEGACY_MODES: dict[str, str] = {
    "strict": PermissionMode.MANUAL,
    "balanced": PermissionMode.AUTO,
    "permissive": PermissionMode.BYPASS,
}


def normalize_mode(mode: str) -> str:
    """把任意模式取值归一成权限模式。

    认得的老名字（strict / balanced / permissive）会映射到对应的新模式；
    认不出来的按 ``auto``（按风险档位）—— 宁可多问一次，也不要因为一个错字
    变成"全放行"。
    """
    text = str(mode or "").strip().lower()
    if text in PermissionMode:
        return text
    return _LEGACY_MODES.get(text, PermissionMode.AUTO)


# 数据出口的目标是谁定的 —— 决定这个能力是不是"模型能操控的出口"。
#
# 这两个值合起来才说得清一件事：能力把数据发出去的时候，**地址是不是模型说了算**。
#
# - ``model_controlled``：地址/参数由模型给（抓 URL、搜关键字、跑命令）——
#   这类才是 SSRF 与数据外泄的真正面，风险顶到 ``network``；
# - ``fixed``：地址由平台配好（自己配的向量化端点、内部服务）——
#   模型只能决定"发什么内容"，决定不了"发到哪"，不该按网络风险拦。
EGRESS_TARGET_FIXED = "fixed"
EGRESS_TARGET_MODEL_CONTROLLED = "model_controlled"

# egress_target 的合法取值
EGRESS_TARGETS = (EGRESS_TARGET_FIXED, EGRESS_TARGET_MODEL_CONTROLLED)


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
    always: bool = False,
) -> str:
    """这次调用要不要人批。返回 ``approved`` / ``pending``。

    **单一事实来源**：执行层拿它做审批判定，意图层拿它提前告诉模型
    "哪些能力会要人批"。两处各写一套规则，迟早会对不上 ——
    模型被告知"直接放行"，实际却被拦下，就是这么来的。

    **判定按权限模式走**（``normalize_mode``）：

    - ``bypass`` —— 全放行；
    - ``plan`` —— 只读模式：只有只读的能力放行，其余一律要人批；
    - ``manual`` —— 只读放行，其余（写 / 执行 / 联网 / 破坏）都要人批；
    - ``accept_edits`` —— 中档（执行命令、联网）也自动放行，只有破坏性要人批；
    - ``auto``（默认）—— 按**风险档位**：低档自动放行，中档与高档要人批。

    Args:
        permission: 能力声明的权限要求。
        risk: 本次调用的风险等级（可以是参数级规则上调后的值）。
        trust_mode: 权限模式（老名字 strict / balanced / permissive 也认）。
        require_admin: 参数级规则是否要求管理员放行。
        always: 无论模式都要人批（不可逆的操作）。

    Returns:
        ``approved``（直接放行）或 ``pending``（挂起等人工审批）。
    """
    mode = normalize_mode(trust_mode)
    permission = permission or PermissionLevel.NONE

    # 绕过权限：什么都放行 —— 用户明确选的。
    if mode == PermissionMode.BYPASS:
        return APPROVAL_APPROVED

    if permission == PermissionLevel.ADMIN:
        return APPROVAL_PENDING
    if always or require_admin:
        return APPROVAL_PENDING

    # 只读模式：写类与执行类根本不该走到判定这一步（工具没暴露给模型），
    # 真走到了也不放行。
    if mode == PermissionMode.PLAN:
        return APPROVAL_APPROVED if risk == RiskLevel.READ else APPROVAL_PENDING

    # 手动模式：只读不问，其余都要人批。
    if mode == PermissionMode.MANUAL:
        return APPROVAL_APPROVED if risk == RiskLevel.READ else APPROVAL_PENDING

    # 运营显式要求人确认（能力声明里的 confirm）。
    if permission == PermissionLevel.CONFIRM:
        return APPROVAL_PENDING

    tier = risk_tier(risk)

    # 接受编辑：文件编辑与命令都自动放行，只有破坏性（不可逆）要人批。
    if mode == PermissionMode.ACCEPT_EDITS:
        return APPROVAL_PENDING if tier == RiskTier.HIGH else APPROVAL_APPROVED

    # 默认：按风险档位 —— 低档自动放行，中档与高档要人批。
    if tier in (RiskTier.MEDIUM, RiskTier.HIGH):
        return APPROVAL_PENDING
    return APPROVAL_APPROVED


def needs_approval(
    permission: str,
    risk: str,
    trust_mode: str,
    *,
    require_admin: bool = False,
    always: bool = False,
) -> bool:
    """这次调用要不要人批 —— 审批规则的**唯一实现**。

    文件工具（deepAgents 的）与我们的工具（web_* / run_python / MCP）都用这一
    个函数判定。两边各写一套规则，迟早对不上：模型被告知"直接放行"、实际却被
    拦下，或者更糟 —— 批准过了，执行层又拦一次。

    Args:
        permission: 权限要求（none / confirm / admin）。
        risk: 风险等级（可以是参数级规则上调后的值）。
        trust_mode: 权限模式（老名字 strict / balanced / permissive 也认）。
        require_admin: 参数级规则是否要求管理员放行。
        always: 无论模式都要人批（删除这类不可逆的操作）。

    Returns:
        要人批返回 ``True``。
    """
    if always:
        return True
    return (
        approval_decision(
            permission,
            risk,
            trust_mode,
            require_admin=require_admin,
        )
        == APPROVAL_PENDING
    )


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

    ``egress_target`` 是这里最要紧的一个字段：它把"会联网"拆成了两件事 ——
    **会不会出进程**（``open_world``）与**出口地址是不是模型说了算**
    （``egress_target``）。只有后者才是 SSRF 与数据外泄的真正风险面。
    """

    read_only: bool = False  # 只读不改状态
    destructive: bool = False  # 可能造成破坏性改动
    idempotent: bool = False  # 重复执行结果相同（重试的前提）
    open_world: bool = False  # 会访问进程之外的系统
    # 数据出口的目标是谁定的：fixed（平台配好）/ model_controlled（模型给）
    egress_target: str = EGRESS_TARGET_MODEL_CONTROLLED
    # 会不会把数据发到那个出口
    egress_data: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Annotations:
        """从数据库/接口的原始字典构造注解。

        **新字段缺失时按保守默认处理**：``egress_target`` 缺失 → 记成
        ``model_controlled``（当成模型能操控的出口），``egress_data`` 缺失 → 记成
        True。这样老数据的行为与改造前完全一致，不需要写数据迁移。

        Args:
            data: 原始字典，可能为空或字段缺失。

        Returns:
            构造好的 ``Annotations``。
        """
        data = data or {}
        target = str(data.get("egress_target") or "").strip().lower()
        if target not in EGRESS_TARGETS:
            target = EGRESS_TARGET_MODEL_CONTROLLED
        return cls(
            read_only=bool(data.get("read_only", False)),
            destructive=bool(data.get("destructive", False)),
            idempotent=bool(data.get("idempotent", False)),
            open_world=bool(data.get("open_world", False)),
            egress_target=target,
            egress_data=bool(data.get("egress_data", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        """转回字典（写库 / 出接口用）。"""
        return {
            "read_only": self.read_only,
            "destructive": self.destructive,
            "idempotent": self.idempotent,
            "open_world": self.open_world,
            "egress_target": self.egress_target,
            "egress_data": self.egress_data,
        }

    @classmethod
    def from_mcp(cls, annotations: Any) -> Annotations:
        """从 MCP server 侧的 tool annotations 继承，不用人工填。

        MCP 的提示字段名是驼峰（``readOnlyHint`` 等），和平台内部的蛇形命名
        不同，所以逐个映射。拿不到提示时返回全 False 的空注解 ——
        宁可是"没信息"，也不要瞎猜一个。

        出口维度的推断口径：MCP 工具只要声明了开放世界，地址就是外部给定的
        （模型能决定调哪个 URL、传什么参数）→ 记成 ``model_controlled``；
        不开放世界的，地址由 server 决定 → ``fixed``，只读且不开放的连数据
        都不往外发。

        Args:
            annotations: MCP 侧的 annotations 对象，可能为 ``None``。

        Returns:
            映射后的 ``Annotations``。
        """
        if annotations is None:
            return cls()
        open_world = bool(getattr(annotations, "openWorldHint", False))
        read_only = bool(getattr(annotations, "readOnlyHint", False))
        return cls(
            read_only=read_only,
            destructive=bool(getattr(annotations, "destructiveHint", False)),
            idempotent=bool(getattr(annotations, "idempotentHint", False)),
            open_world=open_world,
            egress_target=(EGRESS_TARGET_MODEL_CONTROLLED if open_world else EGRESS_TARGET_FIXED),
            egress_data=open_world or not read_only,
        )

    def derived_risk(self) -> str:
        """注解推出的风险参考值（仅供参考，不是判定依据）。

        注解描述的是"工具本来是什么样"，不是"这次调用有多危险" ——
        真要判定，看 ``risk`` 声明 + 参数级规则。

        推导口径：只读且不出进程 → read；出口由模型决定 → network；
        出口固定但有数据外发 → write；否则按写处理。
        注意是"参考值"：执行层只在**声明明显低估**时才用它上调，
        不会拿它下调声明的风险。
        """
        if self.open_world and self.egress_target == EGRESS_TARGET_MODEL_CONTROLLED:
            return RiskLevel.NETWORK
        if self.open_world and self.egress_data:
            # 出口固定：有数据出去，但不是模型能操控的地址
            return RiskLevel.WRITE
        if self.read_only:
            return RiskLevel.READ
        return RiskLevel.WRITE


__all__ = [
    "APPROVAL_APPROVED",
    "APPROVAL_PENDING",
    "EGRESS_TARGETS",
    "EGRESS_TARGET_FIXED",
    "EGRESS_TARGET_MODEL_CONTROLLED",
    "Annotations",
    "PermissionLevel",
    "PermissionMode",
    "RetryPolicy",
    "RiskLevel",
    "RiskTier",
    "approval_decision",
    "max_risk",
    "normalize_mode",
    "risk_rank",
    "risk_tier",
]
