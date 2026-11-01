"""结果脱敏。

执行结果里可能带密钥 —— 读一个 ``.env``、跑一条 ``env``、抓一个返回 token 的接口，
都会把凭据带进轨迹表。

**只脱敏落库的部分**：给模型看的文本保持原样（模型需要看到真实内容才能干活），
脱敏的是写进调用记录的那一份。
"""

from __future__ import annotations

import re
from typing import Any

# 常见凭据格式
_DEFAULT_PATTERNS: tuple[str, ...] = (
    r"sk-[A-Za-z0-9_\-]{16,}",  # OpenAI / 兼容厂商
    r"sk-ant-[A-Za-z0-9_\-]{16,}",  # Anthropic
    r"gh[pousr]_[A-Za-z0-9]{16,}",  # GitHub
    r"AKIA[0-9A-Z]{16}",  # AWS Access Key
    r"AIza[0-9A-Za-z_\-]{30,}",  # Google
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*\S+",
)

# 掩码：只标出"这里原来有东西"，不留下任何原文片段
_MASK = "***"

# 预编译一次：脱敏在每次调用留痕时都会跑，编译不该重复做
_compiled: list[re.Pattern[str]] = [re.compile(pattern) for pattern in _DEFAULT_PATTERNS]


def redact_text(text: str, extra_patterns: tuple[str, ...] = ()) -> str:
    """把文本里的凭据替换成掩码。

    Args:
        text: 原始文本。
        extra_patterns: 额外的正则（配置里自定义的），格式写错就跳过。

    Returns:
        脱敏后的文本。
    """
    if not text:
        return text
    result = text
    for pattern in _compiled:
        result = pattern.sub(_MASK, result)
    for pattern in extra_patterns:
        try:
            result = re.sub(pattern, _MASK, result)
        except re.error:  # pragma: no cover - 配置写错
            continue
    return result


def contains_secret(text: str) -> bool:
    """文本里有没有疑似凭据。

    与 :func:`redact_text` 的区别：这里**只检测不掩码**。记忆写入用得上 ——
    把 ``api_key=xxx`` 掩码成 ``api_key=***`` 再存进去，存的是一条没用的记忆，
    不如直接拒绝写入。

    Args:
        text: 待检查的文本。

    Returns:
        命中任一凭据特征返回 ``True``。
    """
    if not text:
        return False
    return any(pattern.search(text) for pattern in _compiled)


def redact(value: Any, extra_patterns: tuple[str, ...] = ()) -> Any:
    """递归脱敏：dict / list / str 逐层处理。

    只认容器和字符串 —— 数字、布尔这些没有凭据可言，原样返回。
    """
    if isinstance(value, str):
        return redact_text(value, extra_patterns)
    if isinstance(value, dict):
        return {key: redact(item, extra_patterns) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, extra_patterns) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, extra_patterns) for item in value)
    return value


__all__ = ["contains_secret", "redact", "redact_text"]
