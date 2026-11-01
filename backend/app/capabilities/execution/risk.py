"""参数级风险评估。

声明里的 ``risk`` 是**基线**；参数级规则可以把它**上调**，不能下调。

"跑一条 shell 命令"是 execute 级，但命令内容是 ``rm -rf /`` 时，它就该按破坏性
操作对待；"抓一个网页"是 network 级，但 URL 指向 ``169.254.169.254`` 时，它就是
一次 SSRF 尝试。

规则是**纯函数**：``(声明, 参数) -> 评估结果``，便于单测，也便于让模型解释
"这次为什么要审批"。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.capabilities.registration.policy import RiskLevel, max_risk
from app.capabilities.registration.spec import CapabilitySpec

# 破坏性命令特征
_DESTRUCTIVE_COMMANDS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", "递归/强制删除"),
    (r"\bdd\s+if=", "直接写设备"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bchmod\s+-R\s+777\b", "递归放开权限"),
    (r"\bchown\s+-R\b", "递归改属主"),
    (r"\bcurl\b[^|]*\|\s*(ba)?sh\b", "下载并直接执行脚本"),
    (r"\bwget\b[^|]*\|\s*(ba)?sh\b", "下载并直接执行脚本"),
    (r">\s*/dev/(sd|nvme)", "写裸设备"),
)

# 内部网段
_INTERNAL_NETWORKS: tuple[str, ...] = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)


@dataclass
class RiskAssessment:
    """一次调用的风险评估结果。"""

    risk: str = RiskLevel.READ  # 最终风险等级（只能比基线高）
    reasons: list[str] = field(default_factory=list)  # 上调原因（说给模型/人听）
    # 需要管理员才能放行（参数级规则可以把风险顶到 destructive）
    require_admin: bool = False

    @property
    def escalated(self) -> bool:
        """风险是否被参数级规则上调过。"""
        return bool(self.reasons)


def assess(spec: CapabilitySpec, arguments: dict[str, Any] | None = None) -> RiskAssessment:
    """按声明 + 参数做风险评估。

    Args:
        spec: 能力声明（基线风险 + 注解）。
        arguments: 本次调用的参数（规则按参数匹配）。

    Returns:
        评估结果：最终风险等级、上调原因、是否要管理员放行。

    规则是纯函数 —— 同样的输入永远给同样的输出，便于单测，也便于向模型解释
    "这次为什么要审批"。
    """
    arguments = arguments or {}
    risk = spec.risk or RiskLevel.READ
    reasons: list[str] = []
    require_admin = False

    # 声明的注解只在"声明明显低估"时上调风险。注解描述的是**工具本来是什么样**
    # （MCP 语义），不是"这次调用有多危险" —— 直接拿它当风险基线的话，
    # 每个 run_bash 都会变成 destructive，永远要管理员批。
    if spec.annotations.open_world and risk == RiskLevel.READ:
        risk = RiskLevel.NETWORK
        reasons.append("能力声明为开放世界（会访问外部网络），风险等级上调")
    if spec.annotations.destructive and risk == RiskLevel.READ:
        risk = RiskLevel.WRITE
        reasons.append("能力声明可能造成破坏性改动，风险等级上调")

    command = arguments.get("command")
    if isinstance(command, str):
        # 命令内容里的破坏性特征 —— 命中就把风险顶到 destructive 并要管理员放行
        for pattern, label in _DESTRUCTIVE_COMMANDS:
            if re.search(pattern, command):
                risk = max_risk(risk, RiskLevel.DESTRUCTIVE)
                reasons.append(f"命令包含{label}")
                require_admin = True

    url = arguments.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        if _is_internal_url(url):
            risk = max_risk(risk, RiskLevel.DESTRUCTIVE)
            reasons.append("URL 指向内网地址（可能是 SSRF）")
            require_admin = True

    return RiskAssessment(risk=risk, reasons=reasons, require_admin=require_admin)


def _is_internal_url(url: str) -> bool:
    """URL 的主机是不是内网地址。

    域名（不是 IP）查不出来 —— 这时返回 False。真要防住 DNS 重绑定这类手法，
    得在解析之后再判一次 IP；那是另一层的事，不在这里做。
    """
    host = urlparse(url).hostname
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 不是 IP 字面量 → 域名，判不了
        return False
    return any(address in ipaddress.ip_network(cidr) for cidr in _INTERNAL_NETWORKS)


__all__ = ["RiskAssessment", "assess"]
