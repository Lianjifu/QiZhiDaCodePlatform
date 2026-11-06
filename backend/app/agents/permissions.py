"""会话级权限模式 —— 「这一轮/这个会话怎么决定要不要人批」。

为什么要有会话级：全局只有一个 ``CAPABILITY_TRUST_MODE``，要改就得改配置、重启服务。
可实际需要的是随手切换：读代码时想自动放行，改代码时想逐个确认，只想看方案时想只读。

======================  ====================================================
模式                    行为
======================  ====================================================
``auto``（默认）        按风险档位：低档自动，中档与高档要人批
``manual``              只读不问，其余任何操作都要人批
``accept_edits``        中档自动（执行命令、联网），只有破坏性要人批
``plan``                只读：写类与执行类工具不暴露给模型
``bypass``              跳过人工确认（含破坏性）
======================  ====================================================
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.capabilities.registration.policy import PermissionMode, normalize_mode
from app.core.config import settings
from app.core.workspace import workspace_dir_name

logger = logging.getLogger(__name__)

# 权限文件所在目录（相对工作区根）
PERMISSIONS_DIRNAME = ".qzd/permissions"


def permissions_root() -> Path:
    """权限文件的根目录。"""
    return Path(settings.workspace_root).resolve() / PERMISSIONS_DIRNAME


def permission_path(thread_id: str) -> Path:
    """某个会话的权限文件路径。"""
    return permissions_root() / f"{workspace_dir_name(thread_id)}.json"


def resolve_permission_mode(thread_id: str) -> str:
    """这个会话实际用的权限模式。

    会话有覆盖就用覆盖的，没有就用全局配置。

    Args:
        thread_id: 会话 ID。**空串**表示"没有会话上下文"
            （直接调能力的接口可以不带 thread_id）—— 那就只用全局配置。

    Returns:
        ``auto`` / ``manual`` / ``accept_edits`` / ``plan`` / ``bypass``。
    """
    if not thread_id or not thread_id.strip():
        # 没有会话就没有会话级覆盖 —— 走全局默认。
        # （工作区目录名要求 thread_id 非空，这里不能去查文件。）
        return _global_mode()
    override = _read(thread_id)
    if override:
        # **会话里存的 bypass 也要过总闸** —— 总闸可能是后来才关上的
        # （之前开着，写了一堆 bypass 会话，然后运维把闸关了）。
        if override == PermissionMode.BYPASS and not settings.capability_allow_bypass:
            logger.warning("会话 %s 存的是 bypass，但总闸关着 —— 退回全局默认", thread_id)
            return _global_mode()
        return override
    return _global_mode()


def _global_mode() -> str:
    """全局默认模式。

    **bypass 要过总闸** —— 配置里写了 bypass 也得看
    ``capability_allow_bypass``。没开就退回 auto 并记一笔：静默忽略会让
    运维以为已经放开了。
    """
    mode = normalize_mode(settings.capability_trust_mode)
    if mode == PermissionMode.BYPASS and not settings.capability_allow_bypass:
        logger.warning("全局配置要求 bypass，但 capability_allow_bypass 是关的 —— 退回 auto")
        return PermissionMode.AUTO
    return mode


def set_permission_mode(thread_id: str, mode: str | None) -> str:
    """设置这个会话的权限模式。

    Args:
        thread_id: 会话 ID。
        mode: 模式名；``None`` / ``""`` / ``"default"`` 恢复全局默认。

    Returns:
        设置之后的实际模式。

    Raises:
        ValueError: 给了不支持的取值。

    Note:
        **``"auto"`` 是显式写出来的，不是"恢复默认"。** 它是五个模式之一 ——
        全局配置可能是 ``bypass``，那时候把会话设成 ``auto`` 必须是
        "按风险档位"，而不是"跟随全局"。混为一谈的话，用户选了更严的模式，
        实际拿到的是更宽的。
    """
    text = str(mode or "").strip().lower()
    if text in ("", "default"):
        clear_permission_mode(thread_id)
        return _global_mode()
    if text not in PermissionMode:
        raise ValueError(f"不支持的权限模式：{mode!r}")
    if text == PermissionMode.BYPASS and not settings.capability_allow_bypass:
        # **明确拒绝，不静默忽略** —— 静默忽略的话，界面上显示"已切换到
        # bypass"，实际却还在按 auto 走，用户以为放开了其实没放开。
        raise ValueError(
            "bypass 模式没有开启（配置项 capability_allow_bypass）。"
            "它会跳过人工确认，包括破坏性操作 —— 需要显式打开。"
        )
    path = permission_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return text


def clear_permission_mode(thread_id: str) -> None:
    """清掉这个会话的覆盖（文件不存在就当本来就没有）。"""
    permission_path(thread_id).unlink(missing_ok=True)


def _read(thread_id: str) -> str | None:
    """读这个会话的覆盖值。

    读不出来（文件没了 / 坏了）就当没有 —— 退回全局默认，不让会话挂掉。
    """
    path = permission_path(thread_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("权限文件坏了，忽略: %s", path)
        return None
    if not isinstance(data, dict):
        return None
    mode = str(data.get("mode") or "").strip().lower()
    return mode if mode in PermissionMode else None


__all__ = [
    "PERMISSIONS_DIRNAME",
    "clear_permission_mode",
    "normalize_mode",
    "permission_path",
    "permissions_root",
    "resolve_permission_mode",
    "set_permission_mode",
]
